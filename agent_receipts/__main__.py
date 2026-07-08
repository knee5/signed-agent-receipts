"""Command line interface for agent_receipts."""

from __future__ import annotations

import argparse
import json
import sys
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

    receipt_cmd = sub.add_parser(
        "receipt",
        help="Emit a signed v0.2 receipt binding a request to a git PR deliverable.",
    )
    request_group = receipt_cmd.add_mutually_exclusive_group(required=True)
    request_group.add_argument("--request-file", help="File whose exact bytes are the task/prompt you were given.")
    request_group.add_argument("--request-text", help="The task/prompt text itself (hashed as UTF-8 bytes).")
    request_group.add_argument(
        "--request-hash",
        help="Precomputed sha256:<hex> of the request, when the request text must not leave the issuer.",
    )
    receipt_cmd.add_argument("--repo", default=None, help="owner/name. Defaults to the GitHub origin remote.")
    receipt_cmd.add_argument("--pr", type=int, required=True, help="Pull request number the deliverable lands as.")
    receipt_cmd.add_argument("--base", default="origin/main", help="Base rev the PR diffs against (default origin/main).")
    receipt_cmd.add_argument("--head", default="HEAD", help="Head rev being delivered (default HEAD).")
    receipt_cmd.add_argument("--repo-dir", default=".", help="Path to the git checkout (default .).")
    receipt_cmd.add_argument("--out", default=None, help="Output path (default receipts/pr-<n>.receipt.json).")
    receipt_cmd.add_argument("--request-source", default=None, help="Where the request came from (issue URL, file, ...).")
    receipt_cmd.add_argument("--request-preview", default=None, help="Short human-readable request summary (disclosure).")
    receipt_cmd.add_argument("--evidence-file", default=None, help="JSON file with a list of typed evidence items.")
    receipt_cmd.add_argument(
        "--claim", action="append", default=[], help="Add a self_claimed disclosure (repeatable, never satisfies policy)."
    )
    receipt_cmd.add_argument(
        "--judgment", action="append", default=[], help="Optional judgment-call disclosure (repeatable, never a gate input)."
    )
    receipt_cmd.add_argument("--signing-key", default=None, help=f"Ed25519 private key path. Defaults to {default_private_key_path()}.")

    verify_receipt = sub.add_parser(
        "verify-receipt",
        help="Stateless verification of a v0.2 receipt: structure, signature, and diff-hash recompute.",
    )
    verify_receipt.add_argument("--receipt", required=True)
    verify_receipt.add_argument("--repo-dir", default=".", help="Git checkout used to recompute the diff hash (default .).")
    verify_receipt.add_argument(
        "--no-recompute", action="store_true", help="Skip the diff-hash recompute (no suitable checkout available)."
    )

    gate = sub.add_parser("gate", help="Run the receipts-gate against a GitHub pull_request event (CI use).")
    gate.add_argument("--event", required=True, help="Path to the pull_request event payload (GITHUB_EVENT_PATH).")
    gate.add_argument("--repo-dir", default=".", help="Path to the checked-out repository.")
    gate.add_argument("--token", default=None, help="GitHub token for check-run lookups (or GITHUB_TOKEN env).")
    gate.add_argument("--request-source-file", default=None, help="Base-branch path holding the issued request bytes.")
    gate.add_argument("--request-hash", default=None, help="Expected sha256:<hex> of the issued request.")
    gate.add_argument("--no-fetch", action="store_true", help="Skip git fetch (refs already available locally).")

    consume = sub.add_parser(
        "consume", help="Append a merged receipt's nonce to the consumed-nonce ledger (post-merge job)."
    )
    consume.add_argument("--receipt", required=True)
    consume.add_argument("--ledger", default=".agent-receipts/consumed.jsonl")
    consume.add_argument("--pr", type=int, default=None)

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
    if args.command == "receipt":
        return _cmd_receipt(args)
    if args.command == "verify-receipt":
        return _cmd_verify_receipt(args)
    if args.command == "gate":
        return _cmd_gate(args)
    if args.command == "consume":
        return _cmd_consume(args)
    return 2


def _cmd_receipt(args: argparse.Namespace) -> int:
    from .gitdiff import GitError, canonical_diff_hash, origin_github_repo, rev_parse, run_git
    from .receipt import (
        SHA256_PREFIXED_RE,
        hash_request_file,
        hash_request_text,
        build_receipt,
        write_receipt,
    )

    repo_dir = Path(args.repo_dir).expanduser()
    repo = args.repo or origin_github_repo(repo_dir)
    if not repo:
        print("error: could not derive owner/name from the origin remote; pass --repo", file=sys.stderr)
        return 2
    try:
        base_sha = rev_parse(repo_dir, args.base)
        head_sha = rev_parse(repo_dir, args.head)
        diff_hash = canonical_diff_hash(repo_dir, base_sha, head_sha)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.request_file:
        request_hash = hash_request_file(args.request_file)
        request_source = args.request_source or args.request_file
    elif args.request_text is not None:
        request_hash = hash_request_text(args.request_text)
        request_source = args.request_source
    else:
        request_hash = args.request_hash
        request_source = args.request_source
        if not SHA256_PREFIXED_RE.match(request_hash or ""):
            print("error: --request-hash must match sha256:<64 hex>", file=sys.stderr)
            return 2

    evidence: list[dict] = []
    if args.evidence_file:
        loaded = json.loads(Path(args.evidence_file).expanduser().read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            print("error: --evidence-file must contain a JSON list of evidence items", file=sys.stderr)
            return 2
        evidence.extend(loaded)
    for claim in args.claim:
        evidence.append({"method": "self_claimed", "claim": claim})

    if args.head == "HEAD":
        dirty = run_git(repo_dir, "status", "--porcelain", check=False).stdout.decode("utf-8", "replace").strip()
        if dirty:
            print(
                "warning: working tree has uncommitted changes; the receipt binds committed state only",
                file=sys.stderr,
            )

    try:
        receipt = build_receipt(
            request_hash=request_hash,
            request_source=request_source,
            request_preview=args.request_preview,
            repo=repo,
            pr_number=args.pr,
            base_sha=base_sha,
            head_sha=head_sha,
            diff_hash=diff_hash,
            evidence=evidence,
            judgment_calls=args.judgment,
            key_path=args.signing_key,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else repo_dir / "receipts" / f"pr-{args.pr}.receipt.json"
    write_receipt(receipt, out)
    capture_event("agent_receipts_receipt_emitted", {"repo": repo, "pr": args.pr})
    print(f"Receipt: {out}")
    print(f"  key_id:    {receipt['signature']['key_id']}")
    print(f"  diff_hash: {diff_hash}")
    print(f"  binds:     {repo}#{args.pr} {base_sha[:12]}...{head_sha[:12]}")
    return 0


def _cmd_verify_receipt(args: argparse.Namespace) -> int:
    from .gitdiff import canonical_diff_hash, commit_exists
    from .receipt import load_receipt, validate_receipt

    receipt = load_receipt(args.receipt)
    failed = False

    problems = validate_receipt(receipt)
    for problem in problems:
        print(f"structure: {problem}")
        failed = True
    if not problems:
        print("structure: valid")

    result = verify_record(receipt)
    print(f"signature: {result.status} ({result.reason})")
    failed = failed or not result.valid

    deliverable = receipt.get("deliverable") or {}
    if not args.no_recompute and not failed:
        repo_dir = Path(args.repo_dir).expanduser()
        base_sha, head_sha = deliverable.get("base_sha", ""), deliverable.get("head_sha", "")
        if commit_exists(repo_dir, base_sha) and commit_exists(repo_dir, head_sha):
            recomputed = canonical_diff_hash(repo_dir, base_sha, head_sha)
            if recomputed == deliverable.get("diff_hash"):
                print(f"diff_hash: recomputed and matched ({recomputed[:23]}...)")
            else:
                print(f"diff_hash: MISMATCH — receipt {deliverable.get('diff_hash')}, recomputed {recomputed}")
                failed = True
        else:
            print("diff_hash: NOT recomputed (base/head commits not in this checkout; fetch them or use --no-recompute)")
            failed = True

    print(
        "note: stateless verification proves authorship, integrity, and binding — not acceptance. "
        "Trusted-signer checks, policy, and replay/freshness require the gate on the receiving repo."
    )
    return 1 if failed else 0


def _cmd_gate(args: argparse.Namespace) -> int:
    import os

    from .gate import context_from_event, prepare_repo, print_report, run_gate
    from .gitdiff import GitError, file_bytes_at, rev_parse
    from .receipt import hash_bytes

    token = args.token or os.environ.get("GITHUB_TOKEN")
    try:
        ctx = context_from_event(
            args.event,
            args.repo_dir,
            token=token,
            request_hash_expected=args.request_hash,
            request_source_desc="request-hash input" if args.request_hash else None,
        )
        if not args.no_fetch:
            prepare_repo(ctx)
        if args.request_source_file:
            base_tip = rev_parse(ctx.repo_dir, ctx.base_rev)
            blob = file_bytes_at(ctx.repo_dir, base_tip, args.request_source_file)
            if blob is None:
                print(
                    f"error: request-source-file {args.request_source_file!r} not found on base branch "
                    f"{ctx.base_ref} (it must live on the protected base, not the PR)",
                    file=sys.stderr,
                )
                return 1
            ctx.request_hash_expected = hash_bytes(blob)
            ctx.request_source_desc = f"{args.request_source_file} @ {ctx.base_ref}"
    except (ValueError, GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = run_gate(ctx)
    print_report(report)
    return 0 if report.passed else 1


def _cmd_consume(args: argparse.Namespace) -> int:
    from .gate import consume_receipt
    from .receipt import load_receipt

    receipt = load_receipt(args.receipt)
    if not isinstance(receipt.get("nonce"), str) or not receipt["nonce"]:
        print("error: receipt has no nonce", file=sys.stderr)
        return 2
    if consume_receipt(receipt, args.ledger, pr_number=args.pr):
        print(f"Consumed nonce {receipt['nonce']} into {args.ledger}")
    else:
        print(f"Nonce {receipt['nonce']} already present in {args.ledger}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
