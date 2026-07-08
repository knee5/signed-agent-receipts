"""Canonical git diff computation for receipt deliverable binding.

The deliverable binding in a v0.2 receipt is a hash of `git diff base...head`
output. Emitter and gate MUST produce byte-identical diffs for the same pair
of SHAs, on different machines, years apart. Everything that can influence
diff bytes is therefore pinned here: config is isolated from system/global
files, and every relevant knob is forced by explicit flag.

The exact command (also documented in docs/RECEIPTS-GATE.md so third parties
can recompute without this package):

    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null LC_ALL=C \
    git -c core.quotePath=true -c diff.suppressBlankEmpty=false \
        diff --no-color --no-ext-diff --no-textconv --no-renames \
        --full-index --binary --diff-algorithm=myers -U3 \
        --inter-hunk-context=0 --src-prefix=a/ --dst-prefix=b/ \
        --ignore-submodules=none <base_sha>...<head_sha> --

`base...head` (three dots) diffs from merge-base(base, head) to head, which is
what a GitHub PR shows. Given two fixed SHAs the merge-base is fixed, so the
output is deterministic regardless of where the base branch tip has moved.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

CANONICAL_DIFF_FLAGS = [
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--full-index",
    "--binary",
    "--diff-algorithm=myers",
    "-U3",
    "--inter-hunk-context=0",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--ignore-submodules=none",
]

_CANONICAL_CONFIG = [
    "-c", "core.quotePath=true",
    "-c", "diff.suppressBlankEmpty=false",
]


class GitError(RuntimeError):
    """A git invocation failed."""


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["LC_ALL"] = "C"
    return env


def run_git(repo_dir: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", *_CANONICAL_CONFIG, *args]
    proc = subprocess.run(cmd, cwd=str(repo_dir), env=_git_env(), capture_output=True)
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args[:3])}... failed (exit {proc.returncode}): {stderr}")
    return proc


def canonical_diff_bytes(repo_dir: str | Path, base_sha: str, head_sha: str) -> bytes:
    proc = run_git(repo_dir, "diff", *CANONICAL_DIFF_FLAGS, f"{base_sha}...{head_sha}", "--")
    return proc.stdout


def canonical_diff_hash(repo_dir: str | Path, base_sha: str, head_sha: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_diff_bytes(repo_dir, base_sha, head_sha)).hexdigest()


def changed_paths(repo_dir: str | Path, base_sha: str, head_sha: str, *, three_dot: bool = True) -> list[str]:
    """Paths changed between base and head, NUL-delimited so no quoting ambiguity."""
    sep = "..." if three_dot else ".."
    proc = run_git(repo_dir, "diff", "--no-renames", "--name-only", "-z", f"{base_sha}{sep}{head_sha}", "--")
    raw = proc.stdout.decode("utf-8", "replace")
    return [p for p in raw.split("\0") if p]


def rev_parse(repo_dir: str | Path, rev: str) -> str:
    proc = run_git(repo_dir, "rev-parse", "--verify", f"{rev}^{{commit}}")
    return proc.stdout.decode("ascii").strip()


def is_ancestor(repo_dir: str | Path, ancestor: str, descendant: str) -> bool:
    proc = run_git(repo_dir, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    stderr = proc.stderr.decode("utf-8", "replace").strip()
    raise GitError(f"git merge-base --is-ancestor failed (exit {proc.returncode}): {stderr}")


def commit_exists(repo_dir: str | Path, sha: str) -> bool:
    proc = run_git(repo_dir, "cat-file", "-e", f"{sha}^{{commit}}", check=False)
    return proc.returncode == 0


def file_bytes_at(repo_dir: str | Path, rev: str, path: str) -> bytes | None:
    """Raw blob content at rev:path, bypassing all filters/textconv. None if absent."""
    proc = run_git(repo_dir, "cat-file", "blob", f"{rev}:{path}", check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def origin_github_repo(repo_dir: str | Path) -> str | None:
    """'owner/name' parsed from the origin remote URL, if it is a GitHub URL."""
    proc = run_git(repo_dir, "remote", "get-url", "origin", check=False)
    if proc.returncode != 0:
        return None
    url = proc.stdout.decode("utf-8", "replace").strip()
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/", "http://github.com/"):
        if url.startswith(prefix):
            tail = url[len(prefix):]
            if tail.endswith(".git"):
                tail = tail[:-4]
            if tail.count("/") == 1:
                return tail
    return None


def ls_files_at(repo_dir: str | Path, rev: str, prefix: str) -> list[str]:
    proc = run_git(repo_dir, "ls-tree", "-r", "--name-only", "-z", rev, "--", prefix, check=False)
    if proc.returncode != 0:
        return []
    raw = proc.stdout.decode("utf-8", "replace")
    return [p for p in raw.split("\0") if p]
