"""Fleet ledger day-log generation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jsonl import read_jsonl, write_jsonl
from .records import make_record
from .signing import verify_record
from .utils import ensure_dir


def utc_day(value: str | None = None) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    return value[:10]


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    runtimes = Counter(str(record.get("source_runtime") or "unknown") for record in records)
    valid = 0
    invalid = 0
    for record in records:
        if verify_record(record).valid:
            valid += 1
        else:
            invalid += 1
    return {
        "total_records": len(records),
        "valid_signatures": valid,
        "invalid_signatures": invalid,
        "runtimes": dict(sorted(runtimes.items())),
    }


def write_day_digest(jsonl_path: str | Path, out_dir: str | Path, *, day: str | None = None, key_path: str | Path | None = None) -> tuple[Path, Path]:
    records = read_jsonl(jsonl_path)
    digest_day = day or utc_day(records[0].get("started_at") if records else None)
    summary = summarize_records(records)
    out = ensure_dir(out_dir)
    md_path = out / f"{digest_day}-fleet-ledger.md"
    digest_jsonl = out / f"{digest_day}-fleet-ledger.jsonl"
    lines = [
        f"# Fleet Ledger Digest — {digest_day}",
        "",
        "Verifiable day-log generated from signed-agent-receipts JSONL.",
        "",
        "## Summary",
        f"- Total records: {summary['total_records']}",
        f"- Valid signatures: {summary['valid_signatures']}",
        f"- Invalid signatures: {summary['invalid_signatures']}",
        "",
        "## Runtimes",
    ]
    for runtime, count in summary["runtimes"].items():
        lines.append(f"- {runtime}: {count}")
    lines.extend(["", "## Records"])
    for record in records:
        result = verify_record(record)
        lines.append(f"- `{record.get('run_id')}` — {record.get('title') or 'Untitled'} — {result.status}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    digest_record = make_record(
        source_runtime="fleet-ledger",
        source_path=str(jsonl_path),
        title=f"Fleet ledger digest {digest_day}",
        run_id=f"fleet-ledger-{digest_day}",
    )
    digest_record["inputs"] = [{"type": "source_jsonl", "path": str(jsonl_path), "summary": str(summary)}]
    digest_record["evidence"] = [{"type": "markdown_digest", "path": str(md_path), "caption": "Fleet ledger digest markdown"}]
    write_jsonl([digest_record], digest_jsonl, key_path=key_path)
    return md_path, digest_jsonl
