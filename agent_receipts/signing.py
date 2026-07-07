"""Ed25519 signing and verification for receipt records."""

from __future__ import annotations

import base64
import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ALGORITHM = "ed25519"
CANONICALIZATION = "canonical-json-v1"
SIGNATURE_FIELD = "signature"
DEFAULT_PRIVATE_KEY_PATH = Path("~/.config/signed-agent-receipts/ed25519_private.pem")


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    status: str
    reason: str
    run_id: str | None = None


def default_private_key_path() -> Path:
    override = os.environ.get("AGENT_RECEIPTS_SIGNING_KEY")
    return Path(override).expanduser() if override else DEFAULT_PRIVATE_KEY_PATH.expanduser()


def sign_record(record: dict[str, Any], *, key_path: str | Path | None = None) -> dict[str, Any]:
    private_key = load_or_create_private_key(Path(key_path).expanduser() if key_path else default_private_key_path())
    public_key = private_key.public_key()
    payload = canonical_record_bytes(record)
    signature = private_key.sign(payload)
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    record[SIGNATURE_FIELD] = {
        "algorithm": ALGORITHM,
        "canonicalization": CANONICALIZATION,
        "public_key": encode_b64(public_bytes),
        "key_id": "sha256:" + public_key_fingerprint(public_bytes),
        "value": encode_b64(signature),
    }
    return record


def verify_record(record: dict[str, Any]) -> VerificationResult:
    run_id = str(record.get("run_id")) if record.get("run_id") is not None else None
    sig = record.get(SIGNATURE_FIELD)
    if not isinstance(sig, dict):
        return VerificationResult(False, "invalid", "missing signature", run_id)
    if sig.get("algorithm") != ALGORITHM:
        return VerificationResult(False, "invalid", "unsupported signature algorithm", run_id)
    if sig.get("canonicalization") != CANONICALIZATION:
        return VerificationResult(False, "invalid", "unsupported canonicalization", run_id)
    try:
        public_bytes = decode_b64(sig.get("public_key"))
        signature = decode_b64(sig.get("value"))
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
    except (TypeError, ValueError):
        return VerificationResult(False, "invalid", "malformed signature metadata", run_id)
    try:
        public_key.verify(signature, canonical_record_bytes(record))
    except InvalidSignature:
        return VerificationResult(False, "tampered", "signature does not match record content", run_id)
    except ValueError:
        return VerificationResult(False, "invalid", "malformed signature value", run_id)
    return VerificationResult(True, "valid", "signature verified", run_id)


def canonical_record_bytes(record: dict[str, Any]) -> bytes:
    unsigned = unsigned_record(record)
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def unsigned_record(record: dict[str, Any]) -> dict[str, Any]:
    unsigned = copy.deepcopy(record)
    unsigned.pop(SIGNATURE_FIELD, None)
    return unsigned


def load_or_create_private_key(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        return load_private_key(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    data = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return private_key


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"signing key is not an Ed25519 private key: {path}")
    return key


def encode_b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode_b64(value: Any) -> bytes:
    if not isinstance(value, str):
        raise TypeError("expected base64 string")
    return base64.b64decode(value.encode("ascii"), validate=True)


def public_key_fingerprint(public_bytes: bytes) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(public_bytes)
    return digest.finalize().hex()
