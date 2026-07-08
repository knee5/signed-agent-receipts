"""CI merge gate helpers for signed agent receipts."""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
from typing import Iterable

from .jsonl import read_jsonl
from .signing import verify_record

DEFAULT_RECEIPT_GLOBS = [
    ".github/receipts/**/*.jsonl",
    "receipts/**/*.jsonl",
    "agent-receipts/**/*.jsonl",
]

AGENT_AUTHOR_HINTS = ("[bot]", "bot", "agent", "claude", "codex", "hermes", "openclaw")


def is_agent_actor(actor: str | None) -> bool:
    """Return True when a GitHub actor string likely represents an agent PR."""
    if not actor:
        return False
    lowered = actor.lower()
    return any(hint in lowered for hint in AGENT_AUTHOR_HINTS)


def expand_receipt_paths(patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = [Path(p) for p in glob.glob(pattern, recursive=True)]
        paths.extend(path for path in matches if path.is_file())
    return sorted(set(paths))


def verify_receipt_paths(paths: Iterable[Path]) -> tuple[int, list[str]]:
    valid = 0
    failures: list[str] = []
    for path in paths:
        try:
            records = read_jsonl(path)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path}: unreadable JSONL ({exc})")
            continue
        if not records:
            failures.append(f"{path}: no records")
            continue
        for idx, record in enumerate(records, start=1):
            result = verify_record(record)
            if result.valid:
                valid += 1
            else:
                failures.append(f"{path}:{idx}: {result.status} ({result.reason})")
    return valid, failures


def gate(
    *,
    receipt_globs: list[str] | None = None,
    require_for_actor: str | None = None,
    changed_files: list[str] | None = None,
) -> tuple[bool, str]:
    """Evaluate whether a PR satisfies the signed-receipt gate."""
    patterns = receipt_globs or DEFAULT_RECEIPT_GLOBS
    should_require = True if require_for_actor is None else is_agent_actor(require_for_actor)
    if changed_files is not None:
        receipt_like = [p for p in changed_files if p.endswith(".jsonl") and "receipt" in p.lower()]
        if receipt_like:
            patterns = receipt_like
    paths = expand_receipt_paths(patterns)
    if not should_require:
        return True, f"receipts-gate skipped: actor {require_for_actor!r} is not classified as an agent"
    if not paths:
        return False, "receipts-gate failed: agent PR requires at least one signed receipt JSONL"
    valid, failures = verify_receipt_paths(paths)
    if failures:
        return False, "receipts-gate failed:\n" + "\n".join(f"- {failure}" for failure in failures)
    if valid <= 0:
        return False, "receipts-gate failed: no valid receipt records found"
    return True, f"receipts-gate passed: verified {valid} signed receipt record(s) in {len(paths)} file(s)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signed-agent-receipts ci-gate")
    parser.add_argument("--receipt-glob", action="append", dest="receipt_globs", help="Receipt JSONL glob. Repeatable.")
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR"), help="GitHub actor to classify as agent/non-agent.")
    parser.add_argument("--changed-files", default=None, help="Optional newline-delimited changed-file list.")
    args = parser.parse_args(argv)
    changed_files = None
    if args.changed_files:
        changed_files = [line.strip() for line in Path(args.changed_files).read_text(encoding="utf-8").splitlines() if line.strip()]
    ok, message = gate(receipt_globs=args.receipt_globs, require_for_actor=args.actor, changed_files=changed_files)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
