"""Heuristic normalizers for local agent runtime state."""

from __future__ import annotations

import json
import os
import sqlite3
import math
import datetime as _dt
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

from .evidence import attach_evidence, discover_local_images
from .records import make_record
from .utils import (
    extract_urls,
    home_path,
    parse_time,
    read_text_sample,
    safe_json,
    stable_id,
    truncate_text,
    workspace_path,
)

TEXT_EXTENSIONS = {".log", ".txt", ".md"}
JSON_EXTENSIONS = {".json", ".jsonl", ".ndjson"}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
SOURCE_LIMIT_PER_ROOT = 120
DEFAULT_OUTPUT_ROOT = Path("~/.config/signed-agent-receipts/output").expanduser()


def normalize_all(
    *,
    limit: int = 10,
    home: Path | None = None,
    workspace: Path | None = None,
    evidence_roots: Iterable[Path] | None = None,
) -> list[dict]:
    home = home or home_path()
    workspace = workspace or workspace_path()
    roots = [(runtime, root) for runtime, root in source_roots(home) if root.exists()]
    records: list[dict] = []
    seen: set[str] = set()
    image_roots = default_evidence_roots(home, workspace, evidence_roots)
    images = discover_local_images(image_roots, limit=5)

    if limit <= 0:
        return records

    quota = max(1, math.ceil(limit / max(1, len(roots))))
    for runtime, root in roots:
        for record in normalize_root(runtime, root, limit=quota):
            if record["run_id"] in seen:
                continue
            attach_evidence(record, images)
            seen.add(record["run_id"])
            records.append(record)
            if len(records) >= limit:
                return records

    for runtime, root in roots:
        for record in normalize_root(runtime, root, limit=max(limit - len(records), 0) + quota):
            if record["run_id"] in seen:
                continue
            attach_evidence(record, images)
            seen.add(record["run_id"])
            records.append(record)
            if len(records) >= limit:
                return records
    return records


def default_evidence_roots(home: Path, workspace: Path, evidence_roots: Iterable[Path] | None = None) -> list[Path]:
    roots = list(evidence_roots or [workspace, DEFAULT_OUTPUT_ROOT])
    deduped = []
    seen = set()
    for root in roots:
        path = Path(root).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def source_roots(home: Path) -> list[tuple[str, Path]]:
    return [
        ("hermes", home / ".hermes"),
        ("codex", home / ".codex"),
        ("claude-code", home / ".claude" / "projects"),
    ]


def normalize_root(runtime: str, root: Path, *, limit: int) -> Iterator[dict]:
    if limit <= 0 or not root.exists():
        return
    for path in recent_source_files(root):
        try:
            suffix = path.suffix.lower()
            if suffix in SQLITE_EXTENSIONS:
                yield from normalize_sqlite(runtime, path, limit=limit)
            elif suffix in JSON_EXTENSIONS:
                yield from normalize_json_file(runtime, path, limit=limit)
            elif suffix in TEXT_EXTENSIONS:
                yield normalize_text_file(runtime, path)
            else:
                continue
        except Exception as exc:  # pragma: no cover - defensive boundary
            record = make_record(source_runtime=runtime, source_path=path, title=f"Unreadable source: {path.name}")
            record["policy_flags"].append(
                {"severity": "warning", "code": "source_read_failed", "message": truncate_text(str(exc), 240)}
            )
            yield record
        limit -= 1
        if limit <= 0:
            return


def recent_source_files(root: Path) -> list[Path]:
    priority: list[Path] = []
    candidates: list[Path] = []
    root = root.expanduser()
    root_depth = len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            depth = len(current.parts) - root_depth
            if depth > 5:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__"}]
            for name in filenames:
                path = current / name
                if path.suffix.lower() in TEXT_EXTENSIONS | JSON_EXTENSIONS | SQLITE_EXTENSIONS:
                    if is_codex_rollout_file(path):
                        priority.append(path)
                    elif len(candidates) < SOURCE_LIMIT_PER_ROOT:
                        candidates.append(path)
    except OSError:
        return []
    return sorted(priority, key=lambda p: _mtime(p), reverse=True) + sorted(candidates, key=lambda p: _mtime(p), reverse=True)


def normalize_json_file(runtime: str, path: Path, *, limit: int) -> Iterator[dict]:
    if is_codex_rollout_file(path):
        yield normalize_codex_rollout_file(path)
        return

    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        yielded = 0
        for line_no, line in enumerate(read_text_sample(path).splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield record_from_object(runtime, path, obj, raw_ref=f"{path}:{line_no}")
            yielded += 1
            if yielded >= limit:
                return
        if yielded == 0:
            yield normalize_text_file(runtime, path)
        return

    try:
        obj = json.loads(read_text_sample(path, limit=2_000_000))
    except json.JSONDecodeError:
        yield normalize_text_file(runtime, path)
        return

    if isinstance(obj, list):
        for item in obj[:limit]:
            yield record_from_object(runtime, path, item)
    else:
        yield record_from_object(runtime, path, obj)


def is_codex_rollout_file(path: Path) -> bool:
    parts = path.parts
    return (
        path.suffix.lower() == ".jsonl"
        and path.name.startswith("rollout")
        and ".codex" in parts
        and "sessions" in parts
    )


def normalize_codex_rollout_file(path: Path) -> dict:
    raw_text = read_full_text(path)
    entries = parse_jsonl_objects(raw_text)
    session_meta = first_payload(entries, top_type="session_meta")
    session_id = string_or_none(first_value(session_meta, "session_id")) or codex_session_id_from_filename(path)
    cwd = find_codex_cwd(entries, raw_text)
    timestamps = [ts for ts in (first_time(entry, "timestamp", "ts", "time", "created_at") for entry in entries) if ts]
    started = timestamps[0] if timestamps else None
    ended = timestamps[-1] if timestamps else None
    prompt = first_codex_user_prompt(entries)
    title_source = prompt or infer_title_from_text(raw_text) or path.name
    title = first_useful_line(title_source)
    record = make_record(
        source_runtime="codex",
        source_path=path,
        title=title,
        session_id=session_id,
        started_at=started,
        ended_at=ended,
        duration_ms=duration_between_ms(started, ended),
    )
    if prompt:
        record["inputs"].append({"type": "user", "summary": truncate_text(prompt, 900), "path": str(path)})
    if cwd:
        record["inputs"].append({"type": "environment", "summary": truncate_text(f"cwd: {cwd}", 500), "path": str(path)})
    if not record["inputs"]:
        record["inputs"].append({"type": "source_summary", "summary": truncate_text(raw_text, 900), "path": str(path)})
    record["tool_calls"] = extract_codex_tool_calls(entries, path, cwd)
    record["file_diffs"] = extract_codex_file_diffs(entries)
    record["urls"] = extract_urls(raw_text + "\n" + safe_json(entries, 200_000))
    record["costs"] = extract_codex_costs(entries)
    record["raw_refs"] = [str(path)]
    return record


def read_full_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_jsonl_objects(text: str) -> list[dict]:
    objects = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def first_payload(entries: list[dict], *, top_type: str) -> dict:
    for entry in entries:
        if entry.get("type") == top_type and isinstance(entry.get("payload"), dict):
            return entry["payload"]
    return {}


def codex_session_id_from_filename(path: Path) -> str:
    stem = path.stem
    for prefix in ("rollout-", "rollout_"):
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def find_codex_cwd(entries: list[dict], raw_text: str) -> str | None:
    for entry in entries:
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
        value = first_value(payload, "cwd", "current_dir", "working_directory")
        if value:
            return truncate_text(value, 500)
        for key in ("environment", "env", "context"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = first_value(nested, "cwd", "current_dir", "working_directory")
                if value:
                    return truncate_text(value, 500)
    marker = "<cwd>"
    end_marker = "</cwd>"
    start = raw_text.find(marker)
    end = raw_text.find(end_marker, start + len(marker))
    if start >= 0 and end > start:
        return truncate_text(raw_text[start + len(marker) : end], 500)
    return None


def first_codex_user_prompt(entries: list[dict]) -> str | None:
    for entry in entries:
        text = codex_user_text(entry)
        if text:
            cleaned = strip_environment_context(text)
            if cleaned:
                return cleaned
            # Pure environment-context records precede the real prompt in Codex rollouts.
            # Skip them so receipt titles reflect the user task, not the cwd wrapper.
            if "<environment_context>" in str(text):
                continue
            return text
    return None


def codex_user_text(entry: dict) -> str | None:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    entry_type = str(entry.get("type") or "")
    payload_type = str(payload.get("type") or "")
    role = str(first_value(payload, "role", "author") or first_value(entry, "role", "author") or "").lower()
    if role == "user" or entry_type in {"user_message", "user_input", "user_prompt"} or payload_type in {"user_message", "user_input"}:
        return content_to_text(first_value(payload, "message", "content", "text", "prompt", "input") or first_value(entry, "message", "content", "text"))
    if entry_type == "response_item" and payload_type == "message" and role == "user":
        return content_to_text(first_value(payload, "content", "message", "text"))
    if entry_type == "event_msg" and role == "user":
        return content_to_text(first_value(payload, "message", "content", "text", "prompt", "input"))
    return None


def content_to_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = first_value(item, "text", "content", "message")
                if text:
                    parts.append(str(text))
            elif item not in (None, ""):
                parts.append(str(item))
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        text = first_value(value, "text", "content", "message")
        return str(text) if text else safe_json(value)
    return str(value)


def strip_environment_context(text: str) -> str:
    start_tag = "<environment_context>"
    end_tag = "</environment_context>"
    clean = str(text)
    while start_tag in clean and end_tag in clean:
        start = clean.find(start_tag)
        end = clean.find(end_tag, start + len(start_tag))
        if end < 0:
            break
        clean = clean[:start] + clean[end + len(end_tag) :]
    return clean.strip()


def first_useful_line(text: Any) -> str:
    for line in str(text or "").splitlines():
        clean = truncate_text(line, 160)
        if clean:
            return clean
    return "Codex session"


def extract_codex_tool_calls(entries: list[dict], path: Path, cwd: str | None) -> list[dict]:
    output_call_ids = {
        payload.get("call_id")
        for payload in (entry.get("payload") for entry in entries)
        if isinstance(payload, dict) and payload.get("type") == "function_call_output" and payload.get("call_id")
    }
    calls = []
    for entry in entries:
        payload = entry.get("payload")
        if entry.get("type") != "response_item" or not isinstance(payload, dict) or payload.get("type") != "function_call":
            continue
        artifacts = [str(path)]
        if cwd:
            artifacts.append(f"cwd: {cwd}")
        call_id = payload.get("call_id")
        calls.append(
            {
                "name": truncate_text(payload.get("name") or "function_call", 120),
                "args_summary": truncate_text(payload.get("arguments"), 700),
                "status": "ok" if call_id in output_call_ids else "unknown",
                "started_at": first_time(entry, "timestamp", "ts", "time", "created_at"),
                "ended_at": None,
                "elapsed_ms": None,
                "artifacts": artifacts,
            }
        )
    return calls


def extract_codex_file_diffs(entries: list[dict]) -> list[dict]:
    """Extract file diffs from Codex exec outputs and apply_patch events."""
    diffs: list[dict] = []
    seen: set[tuple[str, str | None, str]] = set()
    for entry in entries:
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue

        if entry.get("type") == "response_item" and payload.get("type") == "function_call_output":
            call_id = string_or_none(payload.get("call_id"))
            text = content_to_text(first_value(payload, "output", "content", "text", "message")) or ""
            for diff in parse_unified_git_diff(text):
                append_diff_once(
                    diffs,
                    seen,
                    diff,
                    provenance={"source": "tool_call_id", "ref": call_id, "heuristic": False},
                )
            continue

        if entry.get("type") == "event_msg" and payload.get("type") == "patch_apply_end":
            call_id = string_or_none(payload.get("call_id"))
            for diff in parse_patch_apply_changes(payload.get("changes")):
                append_diff_once(
                    diffs,
                    seen,
                    diff,
                    provenance={"source": "patch_apply_end", "ref": call_id, "heuristic": False},
                )
    return diffs[:50]


def append_diff_once(diffs: list[dict], seen: set[tuple[str, str | None, str]], diff: dict, *, provenance: dict) -> None:
    key = (diff["path"], provenance.get("ref"), provenance.get("source") or "unknown")
    if key in seen:
        return
    seen.add(key)
    diff["provenance"] = provenance
    diffs.append(diff)


def parse_patch_apply_changes(changes: Any) -> list[dict]:
    if not isinstance(changes, dict):
        return []
    result = []
    for path, change in changes.items():
        if not isinstance(change, dict):
            continue
        status = patch_change_status(change.get("type"))
        diff_text = str(change.get("unified_diff") or "")
        additions, deletions = count_unified_diff_lines(diff_text)
        result.append(
            {
                "path": str(change.get("move_path") or path),
                "status": status,
                "additions": additions,
                "deletions": deletions,
            }
        )
    return result


def patch_change_status(change_type: Any) -> str:
    value = str(change_type or "").lower()
    if value == "add":
        return "added"
    if value == "delete":
        return "deleted"
    if value == "move":
        return "renamed"
    return "modified"


def count_unified_diff_lines(text: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in str(text or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def parse_unified_git_diff(text: str) -> list[dict]:
    result: list[dict] = []
    current: dict | None = None
    in_hunk = False
    for line in str(text or "").splitlines():
        match = re.match(r"^diff --git a/(.*?) b/(.*)$", line)
        if match:
            if current:
                result.append(current)
            old_path, new_path = match.groups()
            current = {"path": new_path or old_path, "status": "modified", "additions": 0, "deletions": 0}
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif line.startswith("rename from "):
            current["status"] = "renamed"
        elif line.startswith("+++ b/"):
            current["path"] = line[len("+++ b/") :]
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+") and not line.startswith("+++"):
            current["additions"] += 1
        elif in_hunk and line.startswith("-") and not line.startswith("---"):
            current["deletions"] += 1
    if current:
        result.append(current)
    return result


def extract_codex_costs(entries: list[dict]) -> dict:
    usage: dict[str, Any] = {}
    for entry in entries:
        payload = entry.get("payload")
        if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
            usage = payload
    if not usage:
        return {
            "currency": None,
            "total": None,
            "tokens_in": None,
            "tokens_out": None,
            "notes": "Token usage not found in Codex session.",
        }
    tokens_in = int_or_none(first_nested_value(usage, "input_tokens", "tokens_in", "prompt_tokens", "input"))
    tokens_out = int_or_none(first_nested_value(usage, "output_tokens", "tokens_out", "completion_tokens", "output"))
    total = int_or_none(first_nested_value(usage, "total_tokens", "tokens_total", "total"))
    if total is None and (tokens_in is not None or tokens_out is not None):
        total = (tokens_in or 0) + (tokens_out or 0)
    return {
        "currency": None,
        "total": total,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "notes": "Token usage normalized from Codex token_count event; cost amount unavailable.",
    }


def first_nested_value(obj: Any, *keys: str) -> Any:
    if isinstance(obj, dict):
        direct = first_value(obj, *keys)
        if direct not in (None, ""):
            return direct
        for value in obj.values():
            found = first_nested_value(value, *keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = first_nested_value(item, *keys)
            if found not in (None, ""):
                return found
    return None


def duration_between_ms(started: str | None, ended: str | None) -> int | None:
    if not started or not ended:
        return None
    try:
        start_dt = _dt.datetime.fromisoformat(started)
        end_dt = _dt.datetime.fromisoformat(ended)
    except ValueError:
        return None
    elapsed = int((end_dt - start_dt).total_seconds() * 1000)
    return elapsed if elapsed >= 0 else None


def normalize_text_file(runtime: str, path: Path) -> dict:
    text = read_text_sample(path)
    title = infer_title_from_text(text) or path.name
    record = make_record(source_runtime=runtime, source_path=path, title=title)
    record["inputs"].append({"type": "log_excerpt", "summary": truncate_text(text, 900), "path": str(path)})
    record["urls"] = extract_urls(text)
    record["raw_refs"] = [str(path)]
    return record


def normalize_sqlite(runtime: str, path: Path, *, limit: int) -> Iterator[dict]:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        record = make_record(source_runtime=runtime, source_path=path, title=f"Unreadable SQLite: {path.name}")
        record["policy_flags"].append({"severity": "warning", "code": "sqlite_open_failed", "message": str(exc)})
        yield record
        return
    try:
        con.row_factory = sqlite3.Row
        tables = [
            row["name"]
            for row in con.execute("select name from sqlite_master where type='table' order by name").fetchall()
            if any(word in row["name"].lower() for word in ("run", "session", "task", "message", "log", "event"))
        ]
        yielded = 0
        for table in tables[:8]:
            columns = [row["name"] for row in con.execute(f'pragma table_info("{table}")').fetchall()]
            order_col = next((c for c in columns if c.lower() in {"created_at", "updated_at", "timestamp", "time"}), None)
            order = f' order by "{order_col}" desc' if order_col else ""
            try:
                rows = con.execute(f'select * from "{table}"{order} limit ?', (max(1, limit - yielded),)).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                obj = {key: row[key] for key in row.keys()}
                yield record_from_object(runtime, path, obj, raw_ref=f"sqlite:{path}:{table}")
                yielded += 1
                if yielded >= limit:
                    return
        if yielded == 0:
            record = make_record(source_runtime=runtime, source_path=path, title=f"SQLite source: {path.name}")
            record["policy_flags"].append(
                {"severity": "info", "code": "sqlite_no_candidate_rows", "message": "No run/session/log rows found."}
            )
            yield record
    finally:
        con.close()


def record_from_object(runtime: str, path: Path, obj: Any, raw_ref: str | None = None) -> dict:
    if not isinstance(obj, dict):
        obj = {"value": obj}
    session_id = first_value(obj, "session_id", "sessionId", "conversation_id", "conversationId", "thread_id", "id")
    title = first_value(obj, "title", "name", "summary", "prompt", "input") or infer_title_from_text(safe_json(obj, 400))
    started = first_time(obj, "started_at", "created_at", "timestamp", "ts", "time")
    ended = first_time(obj, "ended_at", "completed_at", "updated_at", "finished_at")
    run_id = first_value(obj, "run_id", "runId")
    record = make_record(
        source_runtime=runtime,
        source_path=path,
        title=str(title or path.name),
        session_id=str(session_id) if session_id is not None else None,
        started_at=started,
        ended_at=ended,
        actor=string_or_none(first_value(obj, "actor", "user", "username")),
        profile=string_or_none(first_value(obj, "profile", "model", "runtime")),
        run_id=str(run_id) if run_id else None,
    )
    record["duration_ms"] = int_or_none(first_value(obj, "duration_ms", "elapsed_ms", "durationMs"))
    record["inputs"] = extract_inputs(obj, path)
    record["tool_calls"] = extract_tool_calls(obj)
    record["file_diffs"] = extract_file_diffs(obj)
    record["urls"] = extract_urls(safe_json(obj, 20_000))
    record["costs"] = extract_costs(obj)
    record["policy_flags"] = extract_policy_flags(obj)
    record["raw_refs"] = [raw_ref or str(path)]
    if not record["inputs"]:
        record["inputs"].append({"type": "source_summary", "summary": truncate_text(safe_json(obj), 700), "path": str(path)})
    return record


def extract_inputs(obj: dict, path: Path) -> list[dict]:
    inputs: list[dict] = []
    for key in ("prompt", "input", "instruction", "request"):
        value = obj.get(key)
        if value:
            inputs.append({"type": key, "content": truncate_text(value), "path": str(path)})
    messages = obj.get("messages") or obj.get("conversation") or []
    if isinstance(messages, list):
        for message in messages[:5]:
            if isinstance(message, dict):
                content = first_value(message, "content", "text", "message")
                role = first_value(message, "role", "type") or "message"
                if content:
                    inputs.append({"type": str(role), "summary": truncate_text(content, 500), "path": str(path)})
    return inputs[:10]


def extract_tool_calls(obj: dict) -> list[dict]:
    calls = []
    candidates = obj.get("tool_calls") or obj.get("tools") or obj.get("toolCalls") or obj.get("events") or []
    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except json.JSONDecodeError:
            candidates = [{"name": "tool_calls", "args": candidates}]
    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    if isinstance(candidates, list):
        for item in candidates[:20]:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = first_value(item, "name", "tool") or first_value(function, "name") or first_value(item, "type") or "tool"
            if not any(word in str(name).lower() for word in ("tool", "exec", "browser", "computer", "read", "write", "apply", "command", "function")) and not item.get("args"):
                continue
            calls.append(
                {
                    "name": truncate_text(name, 120),
                    "args_summary": truncate_text(
                        first_value(item, "args", "arguments", "input", "command")
                        or first_value(function, "arguments", "args"),
                        500,
                    ),
                    "status": string_or_none(first_value(item, "status", "state", "result")),
                    "started_at": first_time(item, "started_at", "created_at", "timestamp", "time"),
                    "ended_at": first_time(item, "ended_at", "completed_at", "finished_at"),
                    "elapsed_ms": int_or_none(first_value(item, "elapsed_ms", "duration_ms", "durationMs")),
                    "artifacts": list_strings(first_value(item, "artifacts", "files", "paths")),
                }
            )
    return calls


def extract_file_diffs(obj: dict) -> list[dict]:
    diffs = obj.get("file_diffs") or obj.get("diffs") or obj.get("changes") or []
    if isinstance(diffs, dict):
        diffs = list(diffs.values())
    result = []
    if isinstance(diffs, list):
        for item in diffs[:50]:
            if isinstance(item, str):
                result.append({"path": item, "status": None, "additions": None, "deletions": None})
            elif isinstance(item, dict):
                path = first_value(item, "path", "file", "filename")
                if path:
                    diff = {
                        "path": str(path),
                        "status": string_or_none(first_value(item, "status", "change_type", "type")),
                        "additions": int_or_none(first_value(item, "additions", "added", "lines_added")),
                        "deletions": int_or_none(first_value(item, "deletions", "deleted", "lines_deleted")),
                    }
                    patch_path = first_value(item, "patch_path", "patch")
                    if patch_path:
                        diff["patch_path"] = str(patch_path)
                    result.append(diff)
    return result


def extract_costs(obj: dict) -> dict:
    costs = obj.get("costs") or obj.get("usage") or {}
    if not isinstance(costs, dict):
        costs = {}
    return {
        "currency": string_or_none(first_value(costs, "currency")) or string_or_none(first_value(obj, "currency")),
        "total": float_or_none(first_value(costs, "total", "cost", "amount")),
        "tokens_in": int_or_none(first_value(costs, "tokens_in", "prompt_tokens", "input_tokens")),
        "tokens_out": int_or_none(first_value(costs, "tokens_out", "completion_tokens", "output_tokens")),
        "notes": string_or_none(first_value(costs, "notes")) or "Cost data normalized when present; otherwise unavailable.",
    }


def extract_policy_flags(obj: dict) -> list[dict]:
    flags = obj.get("policy_flags") or obj.get("policyFlags") or obj.get("warnings") or []
    if isinstance(flags, str):
        flags = [{"severity": "warning", "code": "source_warning", "message": flags}]
    if not isinstance(flags, list):
        return []
    normalized = []
    for item in flags[:20]:
        if isinstance(item, dict):
            severity = str(first_value(item, "severity", "level") or "warning").lower()
            if severity not in {"info", "warning", "high", "critical"}:
                severity = "warning"
            normalized.append(
                {
                    "severity": severity,
                    "code": truncate_text(first_value(item, "code", "type") or "source_flag", 80),
                    "message": truncate_text(first_value(item, "message", "text", "detail") or safe_json(item), 300),
                }
            )
        else:
            normalized.append({"severity": "warning", "code": "source_flag", "message": truncate_text(item, 300)})
    return normalized


def first_value(obj: dict, *keys: str) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    lower = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def first_time(obj: dict, *keys: str) -> str | None:
    return parse_time(first_value(obj, *keys))


def string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return truncate_text(value, 240)


def int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def list_strings(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [truncate_text(item, 240) for item in value[:20]]
    return [truncate_text(value, 240)]


def infer_title_from_text(text: str) -> str | None:
    for line in str(text or "").splitlines():
        clean = truncate_text(line, 160)
        if len(clean) >= 8:
            return clean
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0
