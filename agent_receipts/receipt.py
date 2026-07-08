"""v0.2 receipt: a signed binding of request -> deliverable -> evidence.

A v0.2 receipt is a single JSON document (not a run-ledger JSONL record). The
signed body binds, at minimum:

- ``request.hash``      — SHA-256 of the exact bytes of the task the agent was
                          given. Without this, an agent can solve an easier
                          task than asked and sign a self-consistent receipt.
- ``deliverable``       — the git PR being delivered: repo, base_sha, head_sha,
                          pr_number, and ``diff_hash`` = SHA-256 of the
                          canonical ``git diff base...head`` bytes (see
                          gitdiff.py). Never the mutable PR body.
- ``evidence[]``        — typed by how the RECEIVER can verify each item:
                          re_executable | content_addressed | ci_attested |
                          self_claimed. self_claimed is disclosure, not proof.
- ``key``               — key_id + claimed validity window (the enforced
                          window lives in the verifier's trusted_signers.yml).
- ``purpose``/``audience``/``nonce``/``issued_at`` — scoping + replay dedup.

Reserved, present-but-unwired sockets (schema stability for one rev):
``authorization`` (external authz/intent token ref) and ``signers[]``
(multi-signer / witness co-signatures). ``judgment_calls`` is an optional
disclosure field and is never a gate input.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any

from .signing import SIGNATURE_FIELD, public_key_material, sign_record
from .utils import utc_now

RECEIPT_SCHEMA_VERSION = "agent-receipt.v0.2"
PURPOSE_GITHUB_PR_GATE = "github-pr-gate"

EVIDENCE_METHODS = ("re_executable", "content_addressed", "ci_attested", "self_claimed")

SHA256_PREFIXED_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

DEFAULT_KEY_VALID_DAYS = 365


def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_request_text(text: str) -> str:
    """SHA-256 over the exact UTF-8 bytes of the request. No normalization:
    any canonicalization would create ambiguity about what was hashed. Issuers
    must retain the exact bytes to re-verify."""
    return hash_bytes(text.encode("utf-8"))


def hash_request_file(path: str | Path) -> str:
    return hash_bytes(Path(path).expanduser().read_bytes())


def audience_for_repo(repo: str) -> str:
    return f"github:{repo}"


def key_validity(key_path: str | Path, *, default_days: int = DEFAULT_KEY_VALID_DAYS) -> tuple[str, str]:
    """Claimed validity window for the signing key, persisted in a sidecar
    meta file next to the key. For keys that predate the sidecar, the window
    starts at the key file's mtime. This is the SIGNER'S claim; verifiers
    enforce the window granted in their own trusted_signers.yml."""
    key_path = Path(key_path).expanduser()
    meta_path = key_path.with_name(key_path.name + ".meta.json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta["valid_from"], meta["valid_until"]
    if key_path.exists():
        created = _dt.datetime.fromtimestamp(key_path.stat().st_mtime, _dt.timezone.utc).replace(microsecond=0)
    else:
        created = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    valid_from = created.isoformat()
    valid_until = (created + _dt.timedelta(days=default_days)).isoformat()
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"valid_from": valid_from, "valid_until": valid_until}, indent=2) + "\n",
        encoding="utf-8",
    )
    return valid_from, valid_until


def build_receipt(
    *,
    request_hash: str,
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    diff_hash: str,
    evidence: list[dict[str, Any]] | None = None,
    request_source: str | None = None,
    request_preview: str | None = None,
    purpose: str = PURPOSE_GITHUB_PR_GATE,
    audience: str | None = None,
    judgment_calls: list[str] | None = None,
    run_refs: list[str] | None = None,
    issued_at: str | None = None,
    nonce: str | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build and sign a v0.2 receipt. Raises ValueError if the result would
    not validate — an emitter must never produce a structurally bad receipt."""
    public_key_b64, key_id = public_key_material(key_path)
    from .signing import default_private_key_path  # local import to avoid cycle at module load

    valid_from, valid_until = key_validity(Path(key_path).expanduser() if key_path else default_private_key_path())
    nonce = nonce or secrets.token_hex(16)
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": "rcpt_" + hashlib.sha256(f"{nonce}|{repo}|{pr_number}".encode()).hexdigest()[:16],
        "issued_at": issued_at or utc_now(),
        "purpose": purpose,
        "audience": audience or audience_for_repo(repo),
        "nonce": nonce,
        "request": {
            "hash": request_hash,
            "content_type": "text/plain; charset=utf-8",
            "source": request_source,
            "preview": request_preview,
        },
        "deliverable": {
            "type": "git_pr",
            "repo": repo,
            "pr_number": pr_number,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff_hash": diff_hash,
        },
        "evidence": list(evidence or []),
        "key": {
            "key_id": key_id,
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
        "authorization": None,
        "signers": [],
        "judgment_calls": list(judgment_calls or []),
        "run_refs": list(run_refs or []),
    }
    problems = validate_receipt(receipt, require_signature=False)
    if problems:
        raise ValueError("refusing to sign a structurally invalid receipt: " + "; ".join(problems))
    signed = sign_record(receipt, key_path=key_path)
    if signed[SIGNATURE_FIELD]["key_id"] != key_id:
        raise ValueError("signing key changed between key_id lookup and signing")
    return signed


def validate_evidence_item(item: Any, index: int) -> list[str]:
    problems: list[str] = []
    where = f"evidence[{index}]"
    if not isinstance(item, dict):
        return [f"{where}: not an object"]
    method = item.get("method")
    if method not in EVIDENCE_METHODS:
        return [f"{where}: method must be one of {EVIDENCE_METHODS}, got {method!r}"]
    if method == "re_executable":
        if not isinstance(item.get("cmd"), str) or not item["cmd"].strip():
            problems.append(f"{where}: re_executable requires a non-empty 'cmd'")
        if not isinstance(item.get("expected_exit_code"), int):
            problems.append(f"{where}: re_executable requires integer 'expected_exit_code'")
        cwd = item.get("cwd")
        if cwd is not None and (
            not isinstance(cwd, str) or not cwd or cwd.startswith("/") or ".." in cwd.split("/")
        ):
            problems.append(
                f"{where}: re_executable 'cwd' must be a relative path inside the repo "
                "(no absolute paths, no '..'; the gate additionally confines it to the worktree)"
            )
    elif method == "content_addressed":
        path = item.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            problems.append(f"{where}: content_addressed requires a repo-relative 'path'")
        if not isinstance(item.get("sha256"), str) or not SHA256_PREFIXED_RE.match(item["sha256"]):
            problems.append(f"{where}: content_addressed requires 'sha256' matching sha256:<64 hex>")
    elif method == "ci_attested":
        if item.get("provider") != "github":
            problems.append(f"{where}: ci_attested requires provider 'github' (only provider in v0.2)")
        if not item.get("check_name") and not item.get("check_run_id"):
            problems.append(f"{where}: ci_attested requires 'check_name' or 'check_run_id'")
        sha = item.get("sha")
        if sha is not None and (not isinstance(sha, str) or not GIT_SHA_RE.match(sha)):
            problems.append(f"{where}: ci_attested 'sha' must be a full 40-hex commit SHA when present")
        if item.get("expected_conclusion", "success") != "success":
            problems.append(f"{where}: ci_attested 'expected_conclusion' must be 'success'")
    elif method == "self_claimed":
        if not isinstance(item.get("claim"), str) or not item["claim"].strip():
            problems.append(f"{where}: self_claimed requires a non-empty 'claim'")
    return problems


def validate_receipt(receipt: Any, *, require_signature: bool = True) -> list[str]:
    """Structural validation. Returns a list of problems; empty means valid.
    This checks shape only — trust, diff recomputation, and evidence
    verification are the gate's job."""
    if not isinstance(receipt, dict):
        return ["receipt is not a JSON object"]
    problems: list[str] = []

    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {RECEIPT_SCHEMA_VERSION!r}")
    for field in ("receipt_id", "issued_at", "purpose", "audience", "nonce"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            problems.append(f"missing or empty string field: {field}")

    request = receipt.get("request")
    if not isinstance(request, dict):
        problems.append("missing 'request' object")
    else:
        if not isinstance(request.get("hash"), str) or not SHA256_PREFIXED_RE.match(request["hash"]):
            problems.append("request.hash must match sha256:<64 hex>")

    deliverable = receipt.get("deliverable")
    if not isinstance(deliverable, dict):
        problems.append("missing 'deliverable' object")
    else:
        if deliverable.get("type") != "git_pr":
            problems.append("deliverable.type must be 'git_pr' (only type in v0.2)")
        repo = deliverable.get("repo")
        if not isinstance(repo, str) or not REPO_RE.match(repo):
            problems.append("deliverable.repo must be 'owner/name'")
        if not isinstance(deliverable.get("pr_number"), int) or deliverable["pr_number"] < 1:
            problems.append("deliverable.pr_number must be a positive integer")
        for sha_field in ("base_sha", "head_sha"):
            value = deliverable.get(sha_field)
            if not isinstance(value, str) or not GIT_SHA_RE.match(value):
                problems.append(f"deliverable.{sha_field} must be a full 40-hex commit SHA")
        if not isinstance(deliverable.get("diff_hash"), str) or not SHA256_PREFIXED_RE.match(deliverable["diff_hash"]):
            problems.append("deliverable.diff_hash must match sha256:<64 hex>")

    evidence = receipt.get("evidence")
    if not isinstance(evidence, list):
        problems.append("'evidence' must be an array")
    else:
        for i, item in enumerate(evidence):
            problems.extend(validate_evidence_item(item, i))

    key = receipt.get("key")
    if not isinstance(key, dict):
        problems.append("missing 'key' object")
    else:
        if not isinstance(key.get("key_id"), str) or not SHA256_PREFIXED_RE.match(key["key_id"]):
            problems.append("key.key_id must match sha256:<64 hex>")
        for field in ("valid_from", "valid_until"):
            if not isinstance(key.get(field), str) or parse_iso(key[field]) is None:
                problems.append(f"key.{field} must be an ISO-8601 datetime")

    for reserved in ("authorization", "signers", "judgment_calls"):
        if reserved not in receipt:
            problems.append(f"missing reserved field: {reserved} (may be null/empty, must be present)")
    if "signers" in receipt and not isinstance(receipt["signers"], list):
        problems.append("'signers' must be an array")
    if "judgment_calls" in receipt and not isinstance(receipt["judgment_calls"], list):
        problems.append("'judgment_calls' must be an array")

    signature = receipt.get(SIGNATURE_FIELD)
    if require_signature:
        if not isinstance(signature, dict):
            problems.append("missing 'signature' object")
        elif isinstance(key, dict) and signature.get("key_id") != key.get("key_id"):
            problems.append("signature.key_id does not match key.key_id")

    return problems


def parse_iso(value: str) -> _dt.datetime | None:
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def write_receipt(receipt: dict[str, Any], path: str | Path) -> Path:
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return out


def load_receipt(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
