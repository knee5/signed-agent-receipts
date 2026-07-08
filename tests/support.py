"""Shared fixture: a real git repo with a base branch, a PR branch, and a
trust anchor + policy on the base branch, driven entirely offline."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_receipts.gitdiff import canonical_diff_hash, rev_parse
from agent_receipts.receipt import build_receipt, hash_request_text, write_receipt
from agent_receipts.signing import SIGNATURE_FIELD, public_key_material, sign_record

REPO_NAME = "demo/fixture-repo"
REQUEST_TEXT = "Refactor src/app.py to greet by name, per issue #12."

POLICY_YML = """\
version: 1
settings:
  require_receipt: true
  waiver_label: human-waiver
  require_request_binding: false
  distrust_ci_when_workflows_change: true
  re_executable_allowlist:
    - "echo fixture-check"
default_require: [re_executable, ci_attested]
rules:
  - name: docs-prose-ok
    paths: ["docs/**", "**/*.md"]
    require: []
  - name: receipt-attachments
    paths: ["receipts/**"]
    require: []
"""

TRUSTED_SIGNERS_TEMPLATE = """\
version: 1
signers:
  - name: fixture-agent
    key_id: "{key_id}"
    public_key: "{public_key}"
    valid_from: "2020-01-01T00:00:00+00:00"
revoked_keys: []
"""


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@dataclass
class RepoFixture:
    root: Path
    repo_dir: Path
    key_path: Path
    key_id: str
    public_key: str
    base_sha: str = ""
    work_head: str = ""
    pr_head: str = ""
    extra_config: dict = field(default_factory=dict)

    def request_hash(self) -> str:
        return hash_request_text(REQUEST_TEXT)


def make_fixture(
    tmp: Path,
    *,
    with_trust_anchor: bool = True,
    with_policy: bool = True,
    policy_text: str | None = None,
    consumed_lines: list[str] | None = None,
) -> RepoFixture:
    key_path = tmp / "keys" / "agent.pem"
    public_key, key_id = public_key_material(key_path)

    repo = tmp / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "commit.gpgsign", "false")

    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    if with_trust_anchor or with_policy or consumed_lines:
        (repo / ".agent-receipts").mkdir()
    if with_trust_anchor:
        (repo / ".agent-receipts" / "trusted_signers.yml").write_text(
            TRUSTED_SIGNERS_TEMPLATE.format(key_id=key_id, public_key=public_key), encoding="utf-8"
        )
    if with_policy:
        (repo / ".agent-receipts" / "policy.yml").write_text(policy_text or POLICY_YML, encoding="utf-8")
    if consumed_lines:
        (repo / ".agent-receipts" / "consumed.jsonl").write_text(
            "".join(line + "\n" for line in consumed_lines), encoding="utf-8"
        )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")

    fixture = RepoFixture(
        root=tmp, repo_dir=repo, key_path=key_path, key_id=key_id, public_key=public_key
    )
    fixture.base_sha = rev_parse(repo, "main")

    git(repo, "checkout", "-qb", "feat")
    (repo / "src" / "app.py").write_text(
        "def greet(name):\n    return f'hello {name}'\n", encoding="utf-8"
    )
    git(repo, "commit", "-qam", "work: greet by name")
    fixture.work_head = rev_parse(repo, "HEAD")
    fixture.pr_head = fixture.work_head
    return fixture


def emit_and_attach_receipt(
    fixture: RepoFixture,
    *,
    pr_number: int = 7,
    evidence: list[dict] | None = None,
    request_hash: str | None = None,
    diff_hash: str | None = None,
    nonce: str | None = None,
    key_path: Path | None = None,
) -> dict:
    """Emit a receipt bound to the current work head and commit it as a
    trailing receipt-only commit (the sign-then-attach flow the gate expects)."""
    receipt = build_receipt(
        request_hash=request_hash or fixture.request_hash(),
        repo=REPO_NAME,
        pr_number=pr_number,
        base_sha=fixture.base_sha,
        head_sha=fixture.work_head,
        diff_hash=diff_hash
        or canonical_diff_hash(fixture.repo_dir, fixture.base_sha, fixture.work_head),
        evidence=evidence or [],
        nonce=nonce,
        key_path=key_path or fixture.key_path,
    )
    write_receipt(receipt, fixture.repo_dir / "receipts" / f"pr-{pr_number}.receipt.json")
    git(fixture.repo_dir, "add", "receipts")
    git(fixture.repo_dir, "commit", "-qm", "attach receipt")
    fixture.pr_head = rev_parse(fixture.repo_dir, "HEAD")
    return receipt


def resign_and_attach(fixture: RepoFixture, receipt: dict, *, pr_number: int = 7, key_path: Path | None = None) -> dict:
    """Re-sign a (possibly structurally invalid) receipt body and commit it.
    Simulates a malicious emitter: custom tooling, but a REAL signature from a
    trusted key — the gate must reject on content, not on signature."""
    receipt.pop(SIGNATURE_FIELD, None)
    receipt = sign_record(receipt, key_path=key_path or fixture.key_path)
    write_receipt(receipt, fixture.repo_dir / "receipts" / f"pr-{pr_number}.receipt.json")
    git(fixture.repo_dir, "add", "receipts")
    git(fixture.repo_dir, "commit", "-qm", "attach receipt")
    fixture.pr_head = rev_parse(fixture.repo_dir, "HEAD")
    return receipt


def passing_check_runs(repo: str, sha: str) -> list[dict]:
    # Mirrors the real API: check runs carry the head_sha they ran against,
    # and the gate refuses runs whose head_sha is not the signed head.
    return [{"id": 11, "name": "python-ci", "status": "completed", "conclusion": "success", "head_sha": sha}]


def label_event_log(label: str, actions: list[tuple[str, str]]):
    """Fetcher stub for issue events. `actions` is a chronological list of
    (event_type, actor_login) — e.g. [("labeled", "max"), ("unlabeled", "max"),
    ("labeled", "bot")] — rendered with increasing ids/timestamps so the gate's
    latest-event logic sees them in order regardless of list order."""
    log = [
        {
            "id": 1000 + i,
            "event": etype,
            "created_at": f"2026-07-08T00:00:{i:02d}+00:00",
            "label": {"name": label},
            "actor": {"login": actor},
        }
        for i, (etype, actor) in enumerate(actions, start=1)
    ]
    return lambda repo, pr_number: list(log)


def waiver_label_events(label: str, actor: str):
    """Fetcher stub: the waiver label was applied once by `actor`."""
    return label_event_log(label, [("labeled", actor)])


def permission_map(mapping: dict[str, str]):
    """Fetcher stub: repo permission per login; unknown users have none."""
    return lambda repo, login: mapping.get(login, "none")
