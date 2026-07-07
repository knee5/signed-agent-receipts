"""Markdown receipt rendering."""

from __future__ import annotations

import re
from pathlib import Path

from .jsonl import read_jsonl
from .signing import sign_record
from .utils import ensure_dir, truncate_text


def render_jsonl(jsonl_path: str | Path, out_dir: str | Path, *, key_path: str | Path | None = None) -> list[Path]:
    return render_records(read_jsonl(jsonl_path), out_dir, key_path=key_path)


def render_records(records: list[dict], out_dir: str | Path, *, key_path: str | Path | None = None) -> list[Path]:
    out = ensure_dir(out_dir)
    paths = []
    for record in records:
        path = render_receipt(record, out, key_path=key_path)
        paths.append(path)
    return paths


def render_receipt(record: dict, out_dir: str | Path, *, key_path: str | Path | None = None) -> Path:
    out = ensure_dir(out_dir)
    filename = safe_filename(record.get("run_id") or record.get("title") or "agent-run") + ".md"
    path = out / filename
    record["receipt_path"] = str(path)
    sign_record(record, key_path=key_path)
    path.write_text(render_markdown(record), encoding="utf-8")
    return path


def render_markdown(record: dict) -> str:
    flags = record.get("policy_flags") or []
    has_high = any(flag.get("severity") in {"high", "critical"} for flag in flags if isinstance(flag, dict))
    decision = "REJECT / ESCALATE" if has_high else "APPROVE CANDIDATE"
    lines = [
        f"# Agent Run Receipt: {md(record.get('title') or 'Untitled agent run')}",
        "",
        "## Approve / Reject",
        f"Suggested decision: **{decision}**",
        "",
        "- Approve when the listed inputs, tool calls, file diffs, URLs, and evidence match the expected run.",
        "- Reject or escalate when policy flags are high severity, evidence is missing for a critical claim, or file diffs are unexpected.",
        "",
        "## Identity",
        f"- Run ID: `{md(record.get('run_id'))}`",
        f"- Runtime: `{md(record.get('source_runtime'))}`",
        f"- Source: `{md(record.get('source_path'))}`",
        f"- Actor: {md(record.get('actor') or 'unknown')}",
        f"- Profile: {md(record.get('profile') or 'unknown')}",
        f"- Session: `{md(record.get('session_id') or 'unknown')}`",
        f"- Started: {md(record.get('started_at') or 'unknown')}",
        f"- Ended: {md(record.get('ended_at') or 'unknown')}",
        f"- Duration: {md(format_duration(record.get('duration_ms')))}",
        "",
        "## Inputs",
    ]
    lines.extend(render_inputs(record.get("inputs") or []))
    lines.extend(["", "## Tool Calls", ""])
    lines.extend(render_tool_table(record.get("tool_calls") or []))
    lines.extend(["", "## File Diffs"])
    lines.extend(render_file_diffs(record.get("file_diffs") or []))
    lines.extend(["", "## URLs"])
    lines.extend(render_simple_list(record.get("urls") or [], empty="No URLs found."))
    lines.extend(["", "## Evidence"])
    lines.extend(render_evidence(record.get("evidence") or []))
    lines.extend(["", "## Costs / Time"])
    lines.extend(render_costs(record.get("costs") or {}, record.get("duration_ms")))
    lines.extend(["", "## Policy Flags"])
    lines.extend(render_policy_flags(flags))
    lines.extend(["", "## Raw Refs"])
    lines.extend(render_simple_list(record.get("raw_refs") or [], empty="No raw refs recorded."))
    lines.extend(["", "## Signature"])
    lines.extend(render_signature(record.get("signature")))
    lines.append("")
    return "\n".join(lines)


def render_inputs(inputs: list[dict]) -> list[str]:
    if not inputs:
        return ["No inputs extracted."]
    lines = []
    for item in inputs:
        label = item.get("type") or "input"
        value = item.get("summary") or item.get("content") or item.get("path") or item.get("url") or ""
        detail = truncate_text(value, 700)
        lines.append(f"- **{md(label)}**: {md(detail)}")
    return lines


def render_tool_table(calls: list[dict]) -> list[str]:
    if not calls:
        return ["No tool calls extracted."]
    lines = [
        "| Tool | Status | Elapsed | Args | Artifacts |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for call in calls:
        artifacts = ", ".join(call.get("artifacts") or [])
        lines.append(
            "| "
            + " | ".join(
                [
                    cell(call.get("name") or "tool"),
                    cell(call.get("status") or "unknown"),
                    cell(format_duration(call.get("elapsed_ms"))),
                    cell(truncate_text(call.get("args_summary") or "", 220)),
                    cell(truncate_text(artifacts or "", 220)),
                ]
            )
            + " |"
        )
    return lines


def render_file_diffs(diffs: list[dict]) -> list[str]:
    if not diffs:
        return ["No file diffs extracted."]
    lines = []
    for diff in diffs:
        adds = diff.get("additions")
        dels = diff.get("deletions")
        counts = []
        if adds is not None:
            counts.append(f"+{adds}")
        if dels is not None:
            counts.append(f"-{dels}")
        suffix = f" ({', '.join(counts)})" if counts else ""
        patch = f"; patch: `{md(diff.get('patch_path'))}`" if diff.get("patch_path") else ""
        provenance = render_provenance(diff.get("provenance"))
        lines.append(f"- `{md(diff.get('path'))}` [{md(diff.get('status') or 'unknown')}]{suffix}{patch}{provenance}")
    return lines


def render_evidence(evidence: list[dict]) -> list[str]:
    if not evidence:
        return ["No evidence attached."]
    lines = []
    for item in evidence:
        target = item.get("path") or item.get("url") or "unknown"
        sha = f" sha256=`{md(item.get('sha256'))}`" if item.get("sha256") else ""
        provenance = render_provenance(item.get("provenance"))
        lines.append(f"- **{md(item.get('type') or 'evidence')}**: {md(item.get('caption') or '')} -> `{md(target)}`{sha}{provenance}")
    return lines


def render_provenance(provenance: dict | None) -> str:
    if not isinstance(provenance, dict):
        return ""
    source = provenance.get("source") or "unknown"
    ref = provenance.get("ref") or "unknown"
    heuristic = " heuristic" if provenance.get("heuristic") else ""
    return f"; provenance: {md(source)} ref=`{md(ref)}`{heuristic}"


def render_costs(costs: dict, duration_ms: int | None) -> list[str]:
    total = costs.get("total")
    currency = costs.get("currency") or ""
    if total is None:
        total_text = "unknown"
    else:
        total_text = f"{currency} {total}".strip()
    return [
        f"- Total: {md(total_text)}",
        f"- Tokens in: {md(costs.get('tokens_in') if costs.get('tokens_in') is not None else 'unknown')}",
        f"- Tokens out: {md(costs.get('tokens_out') if costs.get('tokens_out') is not None else 'unknown')}",
        f"- Duration: {md(format_duration(duration_ms))}",
        f"- Notes: {md(costs.get('notes') or 'No cost notes.')}",
    ]


def render_policy_flags(flags: list[dict]) -> list[str]:
    if not flags:
        return ["No policy flags extracted."]
    lines = []
    for flag in flags:
        lines.append(f"- **{md(flag.get('severity') or 'warning')}** `{md(flag.get('code') or 'flag')}`: {md(flag.get('message') or '')}")
    return lines


def render_simple_list(items: list, *, empty: str) -> list[str]:
    if not items:
        return [empty]
    return [f"- `{md(item)}`" for item in items]


def render_signature(signature: dict | None) -> list[str]:
    if not isinstance(signature, dict):
        return ["No signature attached."]
    return [
        f"- Algorithm: `{md(signature.get('algorithm') or 'unknown')}`",
        f"- Canonicalization: `{md(signature.get('canonicalization') or 'unknown')}`",
        f"- Public key: `{md(signature.get('public_key') or 'unknown')}`",
        f"- Key ID: `{md(signature.get('key_id') or 'unknown')}`",
        f"- Signature: `{md(signature.get('value') or 'unknown')}`",
    ]


def format_duration(value: int | None) -> str:
    if value is None:
        return "unknown"
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000:.1f} s"


def safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")
    return clean[:120] or "agent-run"


def md(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def cell(value) -> str:
    return md(value).replace("\r", " ").strip()
