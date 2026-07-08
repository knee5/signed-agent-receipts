"""Trust anchor: trusted signers and revocations, read from the protected base branch.

The gate reads `.agent-receipts/trusted_signers.yml` ONLY from the base branch
of the PR — never from the PR head. A PR that could introduce its own trusted
key would make the whole scheme decorative. Enforcement of who may change the
base copy belongs to branch protection + CODEOWNERS (documented in
docs/RECEIPTS-GATE.md); this module just refuses to read it from anywhere else
by taking the file CONTENT (already resolved from the base branch) rather than
a path.

Each trusted signer pins the full public key, not just the key_id: signatures
are verified against the pinned key material, so a receipt cannot smuggle in
substitute key bytes alongside a familiar-looking id.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

import yaml

from .receipt import parse_iso
from .signing import SIGNATURE_FIELD

TRUSTED_SIGNERS_PATH = ".agent-receipts/trusted_signers.yml"

# Tolerance for clock disagreement between signer and verifier when checking
# that issued_at is not in the verifier's future.
MAX_CLOCK_SKEW = _dt.timedelta(minutes=5)


class TrustConfigError(ValueError):
    """trusted_signers.yml is malformed. The gate fails CLOSED on this."""


@dataclass(frozen=True)
class TrustedSigner:
    name: str
    key_id: str
    public_key: str
    valid_from: _dt.datetime | None
    valid_until: _dt.datetime | None


@dataclass
class TrustAnchor:
    signers: list[TrustedSigner] = field(default_factory=list)
    revoked: dict[str, str] = field(default_factory=dict)  # key_id -> reason

    def find(self, key_id: str) -> TrustedSigner | None:
        for signer in self.signers:
            if signer.key_id == key_id:
                return signer
        return None


def parse_trusted_signers(text: str) -> TrustAnchor:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TrustConfigError(f"trusted_signers.yml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TrustConfigError("trusted_signers.yml must be a YAML mapping")
    if data.get("version") != 1:
        raise TrustConfigError("trusted_signers.yml 'version' must be 1")

    anchor = TrustAnchor()
    signers = data.get("signers")
    if not isinstance(signers, list):
        raise TrustConfigError("trusted_signers.yml 'signers' must be a list")
    for i, entry in enumerate(signers):
        if not isinstance(entry, dict):
            raise TrustConfigError(f"signers[{i}] must be a mapping")
        for required in ("name", "key_id", "public_key"):
            if not isinstance(entry.get(required), str) or not entry[required]:
                raise TrustConfigError(f"signers[{i}] missing required string field '{required}'")
        anchor.signers.append(
            TrustedSigner(
                name=entry["name"],
                key_id=entry["key_id"],
                public_key=entry["public_key"],
                valid_from=_parse_optional_dt(entry, "valid_from", i),
                valid_until=_parse_optional_dt(entry, "valid_until", i),
            )
        )

    revoked = data.get("revoked_keys", [])
    if not isinstance(revoked, list):
        raise TrustConfigError("trusted_signers.yml 'revoked_keys' must be a list")
    for i, entry in enumerate(revoked):
        if isinstance(entry, str):
            anchor.revoked[entry] = "revoked"
        elif isinstance(entry, dict) and isinstance(entry.get("key_id"), str):
            anchor.revoked[entry["key_id"]] = str(entry.get("reason", "revoked"))
        else:
            raise TrustConfigError(f"revoked_keys[{i}] must be a key_id string or a mapping with 'key_id'")
    return anchor


def _parse_optional_dt(entry: dict[str, Any], key: str, index: int) -> _dt.datetime | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    parsed = parse_iso(str(value))
    if parsed is None:
        raise TrustConfigError(f"signers[{index}].{key} is not an ISO-8601 datetime: {value!r}")
    return parsed


def check_signer(anchor: TrustAnchor, receipt: dict[str, Any], *, now: _dt.datetime | None = None) -> list[str]:
    """Trust checks for a structurally-valid, signature-verified receipt.
    Returns problems; empty means the signer is trusted for this receipt.

    issued_at is self-asserted by the signer. Enforcing validity windows
    against it bounds what a STOLEN-BUT-HONESTLY-TIMESTAMPED key can do; a
    forger who also lies about issued_at defeats the window check. Two checks
    therefore use the VERIFIER's clock and cannot be defeated by a lying
    signer: issued_at must not be in the verifier's future (beyond skew), and
    a key whose trusted window has expired verifies nothing, regardless of
    what the receipt claims about when it was issued. The remaining limit —
    a backdated issued_at inside a still-valid window — is documented in
    SECURITY-MODEL.md; do not present windows as stronger than they are.
    """
    problems: list[str] = []
    signature = receipt.get(SIGNATURE_FIELD) or {}
    key = receipt.get("key") or {}
    key_id = signature.get("key_id")

    if key_id in anchor.revoked:
        problems.append(f"signing key {key_id} is REVOKED: {anchor.revoked[key_id]}")
        return problems

    signer = anchor.find(key_id) if isinstance(key_id, str) else None
    if signer is None:
        problems.append(f"signing key {key_id} is not in trusted_signers.yml on the base branch")
        return problems

    if signature.get("public_key") != signer.public_key:
        problems.append(
            f"public key bytes in receipt do not match the key pinned for signer '{signer.name}'"
        )

    issued_at = parse_iso(str(receipt.get("issued_at", "")))
    if issued_at is None:
        problems.append("issued_at is not a parseable ISO-8601 datetime")
        return problems

    verifier_now = now or _dt.datetime.now(_dt.timezone.utc)
    if issued_at > verifier_now + MAX_CLOCK_SKEW:
        problems.append(
            f"receipt issued_at {receipt['issued_at']} is in the verifier's future "
            f"(now {verifier_now.isoformat()}): forward-dated receipts are rejected"
        )
    if signer.valid_until and verifier_now > signer.valid_until:
        problems.append(
            f"trusted window for '{signer.name}' expired at {signer.valid_until.isoformat()} (verifier clock): "
            "an expired key verifies nothing, regardless of the receipt's claimed issued_at"
        )

    if signer.valid_from and issued_at < signer.valid_from:
        problems.append(f"receipt issued_at {receipt['issued_at']} predates trusted window for '{signer.name}'")
    if signer.valid_until and issued_at > signer.valid_until:
        problems.append(f"receipt issued_at {receipt['issued_at']} is after trusted window for '{signer.name}'")

    claimed_from = parse_iso(str(key.get("valid_from", "")))
    claimed_until = parse_iso(str(key.get("valid_until", "")))
    if claimed_from and issued_at < claimed_from:
        problems.append("receipt issued_at predates the key's own claimed valid_from")
    if claimed_until and issued_at > claimed_until:
        problems.append("receipt issued_at is after the key's own claimed valid_until")

    return problems
