"""Command line interface for agent_receipts."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import DEFAULT_OUTPUT_DIR
from .analytics import capture_event
from .jsonl import read_jsonl, write_jsonl
from .ci_gate import main as ci_gate_main
from .fleet_digest import write_day_digest
from .normalizers import normalize_all
from .records import make_record
from .render import render_jsonl, render_records
from .signing import default_private_key_path, verify_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signed-agent-receipts")
    sub = parser.add_subparsers(dest="command", required=True)

    normalize = sub.add_parser("normalize", help="Normalize local agent runtime state into JSONL.")
    normalize.add_argument("--out", default=str(Path(DEFAULT_OUTPUT_DIR).expanduser() / "agent_run.jsonl"))
    normalize.add_argument("--limit", type=int, default=10)
    normalize.add_argument("--signing-key", default=None, help=f"Ed25519 private key path. Defaults to {default_private_key_path()}.")

    render = sub.add_parser("render", help="Render Markdown receipts from JSONL.")
    render.add_argument("--jsonl", required=True)
    render.add_argument("--out-dir", default=str(Path(DEFAULT_OUTPUT_DIR).expanduser() / "receipts"))
    render.add_argument("--signing-key", default=None, help=f"Ed25519 private key path. Defaults to {default_private_key_path()}.")

    dogfood = sub.add_parser("dogfood", help="Normalize then render into the default local output tree.")
    dogfood.add_argument("--out-dir", default=DEFAULT_OUTPUT_DIR)
    dogfood.add_argument("--limit", type=int, default=10)
    dogfood.add_argument("--signing-key", default=None, help=f"Ed25519 private key path. Defaults to {default_private_key_path()}.")

    verify = sub.add_parser("verify", help="Verify signed receipt JSONL records.")
    verify.add_argument("--jsonl", required=True)

    self_receipt = sub.add_parser("self-receipt", help="Emit one signed receipt for install/self-test workflows.")
    self_receipt.add_argument("--out", required=True)
    self_receipt.add_argument("--title", default="signed-agent-receipts self-test")
    self_receipt.add_argument("--summary", default="Self-test receipt emitted by signed-agent-receipts.")
    self_receipt.add_argument("--signing-key", default=None, help=f"Ed25519 private key path. Defaults to {default_private_key_path()}.")

    ci_gate = sub.add_parser("ci-gate", help="Fail when an agent PR lacks a valid signed receipt.")
    ci_gate.add_argument("--receipt-glob", action="append", dest="receipt_globs")
    ci_gate.add_argument("--actor", default=None)
    ci_gate.add_argument("--changed-files", default=None)

    digest = sub.add_parser("fleet-digest", help="Write a signed daily fleet-ledger digest from receipt JSONL.")
    digest.add_argument("--jsonl", required=True)
    digest.add_argument("--out-dir", required=True)
    digest.add_argument("--day", default=None)
    digest.add_argument("--signing-key", default=None, help=f"Ed25519 private key path. Defaults to {default_private_key_path()}.")

    args = parser.parse_args(argv)
    capture_event("agent_receipts_cli_started", {"command": args.command})
    if args.command == "normalize":
        records = normalize_all(limit=max(args.limit, 0))
        count = write_jsonl(records, args.out, key_path=args.signing_key)
        capture_event("agent_receipts_normalized", {"record_count": count})
        print(f"Wrote {count} records to {args.out}")
        return 0
    if args.command == "render":
        paths = render_jsonl(args.jsonl, args.out_dir, key_path=args.signing_key)
        capture_event("agent_receipts_rendered", {"receipt_count": len(paths)})
        print(f"Rendered {len(paths)} receipts to {args.out_dir}")
        return 0
    if args.command == "dogfood":
        out_dir = Path(args.out_dir).expanduser()
        jsonl_path = out_dir / "agent_run.jsonl"
        receipts_dir = out_dir / "receipts"
        records = normalize_all(limit=max(args.limit, 0), evidence_roots=[Path.cwd(), out_dir])
        if receipts_dir.exists():
            for stale in receipts_dir.glob("*.md"):
                try:
                    stale.unlink()
                except OSError:
                    pass
        paths = render_records(records, receipts_dir, key_path=args.signing_key)
        count = write_jsonl(records, jsonl_path, key_path=args.signing_key)
        capture_event(
            "agent_receipts_dogfood_completed",
            {"record_count": count, "receipt_count": len(paths)},
        )
        print(f"Dogfood complete: {count} records, {len(paths)} receipts")
        print(f"JSONL: {jsonl_path}")
        print(f"Receipts: {receipts_dir}")
        return 0
    if args.command == "verify":
        records = read_jsonl(args.jsonl)
        results = [verify_record(record) for record in records]
        valid = sum(1 for result in results if result.valid)
        invalid = [result for result in results if not result.valid]
        for result in results:
            run = result.run_id or "unknown-run"
            print(f"{result.status}: {run} ({result.reason})")
        print(f"Verified {valid}/{len(results)} records in {args.jsonl}")
        return 0 if not invalid else 1
    if args.command == "self-receipt":
        record = make_record(source_runtime="signed-agent-receipts", source_path=args.out, title=args.title, actor="agent")
        record["inputs"] = [{"type": "summary", "summary": args.summary}]
        count = write_jsonl([record], args.out, key_path=args.signing_key)
        result = verify_record(read_jsonl(args.out)[0])
        print(f"Wrote {count} self-test receipt to {args.out}: {result.status}")
        return 0 if result.valid else 1
    if args.command == "ci-gate":
        ci_args = []
        for pattern in args.receipt_globs or []:
            ci_args.extend(["--receipt-glob", pattern])
        if args.actor:
            ci_args.extend(["--actor", args.actor])
        if args.changed_files:
            ci_args.extend(["--changed-files", args.changed_files])
        return ci_gate_main(ci_args)
    if args.command == "fleet-digest":
        md_path, digest_jsonl = write_day_digest(args.jsonl, args.out_dir, day=args.day, key_path=args.signing_key)
        print(f"Fleet digest: {md_path}")
        print(f"Signed digest JSONL: {digest_jsonl}")
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
