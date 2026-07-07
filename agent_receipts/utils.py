"""Small shared utilities for agent receipt normalization."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

MAX_TEXT = 1200

URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
SECRET_PATTERNS = [
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*Bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*([^\s,;&]+)"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b(ghp_[A-Za-z0-9_]{12,})\b"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{12,})\b"),
    re.compile(r"(?i)([?&](?:token|key|secret|password|api_key)=)[^&\s]+"),
]


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def iso_from_timestamp(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def parse_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return iso_from_timestamp(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_time(int(text))
    normalized = text.replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(normalized).astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def redact_text(text: Any) -> str:
    """Redact obvious secrets while preserving enough context for review."""
    if text is None:
        return ""
    redacted = str(text)
    for pattern in SECRET_PATTERNS:
        def repl(match: re.Match[str]) -> str:
            if pattern.pattern.startswith("(?i)([?&]"):
                return match.group(1) + "[REDACTED]"
            if "authorization" in pattern.pattern:
                return "authorization=Bearer [REDACTED]"
            if "Bearer" in match.group(0):
                return "Bearer [REDACTED]"
            if len(match.groups()) >= 2:
                return f"{match.group(1)}=[REDACTED]"
            return "[REDACTED]"

        redacted = pattern.sub(repl, redacted)
    return redacted


def truncate_text(text: Any, limit: int = MAX_TEXT) -> str:
    text = redact_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)].rstrip() + " ... [truncated]"


def safe_json(obj: Any, limit: int = MAX_TEXT) -> str:
    try:
        text = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        text = str(obj)
    return truncate_text(text, limit=limit)


def extract_urls(text: Any) -> list[str]:
    found = []
    seen = set()
    for url in URL_RE.findall(str(text or "")):
        clean = redact_text(url).rstrip(".,;")
        if clean not in seen:
            seen.add(clean)
            found.append(clean)
    return found


def stable_id(parts: Iterable[Any], prefix: str = "run") -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8", "replace"))
        h.update(b"\0")
    return f"{prefix}_{h.hexdigest()[:16]}"


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def ensure_dir(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def path_mtime_iso(path: Path) -> str | None:
    try:
        return iso_from_timestamp(path.stat().st_mtime)
    except OSError:
        return None


def read_text_sample(path: Path, limit: int = 64_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def home_path() -> Path:
    override = os.environ.get("AGENT_RECEIPTS_HOME")
    return Path(override).expanduser() if override else Path.home()


def workspace_path() -> Path:
    override = os.environ.get("AGENT_RECEIPTS_WORKSPACE")
    return Path(override).expanduser() if override else Path.cwd()
