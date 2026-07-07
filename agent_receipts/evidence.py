"""Evidence discovery and hashing."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .utils import extract_urls, sha256_file, truncate_text

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff", ".bmp"}


def _recent_files(root: Path, *, max_depth: int = 4, max_files: int = 1000) -> list[Path]:
    root = root.expanduser()
    if not root.exists():
        return []
    found: list[Path] = []
    root_depth = len(root.parts)
    try:
        for path in root.rglob("*"):
            if len(found) >= max_files:
                break
            if len(path.parts) - root_depth > max_depth:
                continue
            if path.is_file():
                found.append(path)
    except OSError:
        return []
    return sorted(found, key=lambda p: _mtime(p), reverse=True)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def discover_local_images(roots: Iterable[Path], limit: int = 5) -> list[dict]:
    evidence = []
    seen = set()
    for root in roots:
        for path in _recent_files(Path(root), max_depth=4):
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            resolved = str(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            evidence.append(
                {
                    "type": evidence_type_for_path(path),
                    "path": resolved,
                    "caption": f"Recent local image evidence: {path.name}",
                    "sha256": sha256_file(path),
                    "provenance": {"source": "time-window-heuristic", "ref": resolved, "heuristic": True},
                }
            )
            if len(evidence) >= limit:
                return evidence
    return evidence


def evidence_type_for_path(path: Path) -> str:
    text = str(path).lower()
    if "screenshot" in text:
        return "screenshot"
    return "image"


def attach_evidence(record: dict, discovered_images: list[dict] | None = None, limit: int = 3) -> dict:
    existing = record.setdefault("evidence", [])
    for item in existing:
        if item.get("path") and item.get("sha256") is None:
            item["sha256"] = sha256_file(Path(item["path"]))
        item.setdefault("provenance", {"source": "session_artifact", "ref": record.get("run_id"), "heuristic": False})

    if not existing:
        for url in record.get("urls", [])[:limit]:
            existing.append(
                {
                    "type": "url",
                    "url": url,
                    "caption": "URL referenced by source run",
                    "provenance": {"source": "record_url", "ref": record.get("run_id"), "heuristic": False},
                }
            )

    if not existing and record.get("source_path"):
        existing.append(
            {
                "type": "log",
                "path": record["source_path"],
                "caption": truncate_text("Source artifact used as fallback evidence", limit=160),
                "sha256": sha256_file(Path(record["source_path"])),
                "provenance": {"source": "session_artifact", "ref": record.get("source_path"), "heuristic": False},
            }
        )

    if discovered_images:
        seen = {(item.get("path"), item.get("url")) for item in existing}
        attached = 0
        for item in discovered_images:
            key = (item.get("path"), item.get("url"))
            if key in seen:
                continue
            existing.append(dict(item))
            seen.add(key)
            attached += 1
            if attached >= limit:
                break

    return record


def evidence_from_text(text: str) -> list[dict]:
    return [
        {
            "type": "url",
            "url": url,
            "caption": "URL found in source text",
            "provenance": {"source": "record_url", "ref": url, "heuristic": False},
        }
        for url in extract_urls(text)
    ]
