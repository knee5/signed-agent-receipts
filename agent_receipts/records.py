"""Record shape helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .utils import path_mtime_iso, stable_id, truncate_text


def blank_costs(notes: str | None = "Cost data not available from source.") -> dict[str, Any]:
    return {
        "currency": None,
        "total": None,
        "tokens_in": None,
        "tokens_out": None,
        "notes": notes,
    }


def make_record(
    *,
    source_runtime: str,
    source_path: str | Path,
    title: str | None = None,
    session_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    duration_ms: int | None = None,
    actor: str | None = None,
    profile: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    path = str(source_path)
    started = started_at or path_mtime_iso(Path(path))
    rid = run_id or stable_id([source_runtime, path, title or "", session_id or "", started or ""], prefix="run")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "source_runtime": source_runtime,
        "source_path": path,
        "started_at": started,
        "ended_at": ended_at or started,
        "duration_ms": duration_ms,
        "actor": actor,
        "profile": profile,
        "session_id": session_id,
        "title": truncate_text(title or "Untitled agent run", limit=180),
        "inputs": [],
        "tool_calls": [],
        "file_diffs": [],
        "urls": [],
        "evidence": [],
        "costs": blank_costs(),
        "policy_flags": [],
        "receipt_path": None,
        "raw_refs": [path],
    }
