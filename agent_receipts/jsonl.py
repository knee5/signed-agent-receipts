"""JSONL IO helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .signing import sign_record
from .utils import ensure_dir


def write_jsonl(records: Iterable[dict], path: str | Path, *, key_path: str | Path | None = None) -> int:
    out = Path(path).expanduser()
    ensure_dir(out.parent)
    count = 0
    with out.open("w", encoding="utf-8") as f:
        for record in records:
            sign_record(record, key_path=key_path)
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> list[dict]:
    records = []
    with Path(path).expanduser().open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records
