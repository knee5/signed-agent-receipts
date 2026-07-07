"""Command line interface for agent_receipts."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import DEFAULT_OUTPUT_DIR
from .analytics import capture_event
from .jsonl import read_jsonl, write_jsonl
from .normalizers import normalize_all
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
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
