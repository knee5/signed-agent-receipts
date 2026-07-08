"""receipts-gate: verify + accept v0.2 receipts on a GitHub pull request.

Two distinct stages, kept distinct on purpose:

1. VERIFICATION (stateless): signature over the canonical body, trusted
   signer from the base branch, key windows, purpose/audience, and an
   INDEPENDENT recomputation of the canonical PR diff hash. Anyone with the
   repo and the receipt can redo this.
2. ACCEPTANCE (stateful policy): is the verified evidence strong enough for
   the paths this PR changes, per `.agent-receipts/policy.yml`? Plus replay
   dedup against the consumed-nonce ledger. Only a stateful verifier (this
   gate, run inside the repo) can do this part.

Security posture notes, mirrored in SECURITY-MODEL.md:

- The gate itself must execute from a TRUSTED ref (the base branch, or a
  pinned release of this action) — never from the PR's own working copy. A
  PR that can rewrite its verifier passes trivially. This module cannot
  enforce where it was loaded from; the workflow wiring must (see
  .github/workflows/receipts-gate.yml and docs/RECEIPTS-GATE.md).
- Trust anchor and policy are read ONLY from the PR's base branch. A PR that
  touches `.agent-receipts/**` fails, and the waiver label does NOT bypass
  that failure — config changes land only through a repo-settings-level
  bypass by an admin, which GitHub audits.
- The gate covers ALL PRs, not "agent PRs": an agent pushing under a human's
  token is indistinguishable from the human, so scoping by author would be
  decorative. The only bypass is the waiver label, honored only after the
  gate confirms via the GitHub API that it was applied by a user holding
  write/maintain/admin — label presence alone proves nothing, because any
  token with triage rights (including a bot's) can apply labels.
- re_executable evidence is executed only if the exact command string is
  allowlisted in the base-branch policy, without a shell, in a cwd contained
  inside the verification worktree. Receipts are signed but NOT trusted
  content; executing arbitrary receipt-supplied commands would hand CI
  execution to any trusted signer's compromised key.
- ci_attested evidence is accepted only for the signed head_sha. A receipt
  cannot point the gate at some other (green) commit.
- head_sha binding allows trailing commits that touch only `receipts/**`,
  because attaching the receipt necessarily creates a commit after signing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # non-POSIX; ledger writes fall back to best-effort (see consume_receipt)
    fcntl = None  # type: ignore[assignment]

from .gitdiff import (
    GitError,
    canonical_diff_hash,
    changed_paths,
    commit_exists,
    file_bytes_at,
    is_ancestor,
    ls_files_at,
    rev_parse,
    run_git,
)
from .policy import POLICY_PATH, Policy, PolicyConfigError, evaluate, parse_policy
from .receipt import (
    PURPOSE_GITHUB_PR_GATE,
    RECEIPT_SCHEMA_VERSION,
    audience_for_repo,
    validate_receipt,
)
from .signing import verify_record
from .trust import TRUSTED_SIGNERS_PATH, TrustConfigError, check_signer, parse_trusted_signers
from .utils import utc_now

LEDGER_PATH = ".agent-receipts/consumed.jsonl"
RECEIPTS_DIR = "receipts"
MAX_RECEIPT_BYTES = 1_000_000
RE_EXECUTABLE_TIMEOUT_SECONDS = 600

# Repo permissions whose holder may waive the gate. GitHub lets triage+ apply
# labels, but triage is a housekeeping grant, not a merge authority — the
# waiver requires the same level of trust as pushing code.
WAIVER_PERMISSIONS = frozenset({"admin", "maintain", "write"})

CheckRunsFetcher = Callable[[str, str], list[dict]]
LabelEventsFetcher = Callable[[str, int], list[dict]]
PermissionFetcher = Callable[[str, str], "str | None"]


@dataclass
class GateContext:
    repo_dir: Path
    repo: str
    pr_number: int
    pr_head_sha: str
    base_ref: str
    labels: list[str] = field(default_factory=list)
    token: str | None = None
    base_rev: str = ""
    request_hash_expected: str | None = None
    request_source_desc: str | None = None
    check_runs_fetcher: CheckRunsFetcher | None = None
    label_events_fetcher: LabelEventsFetcher | None = None
    permission_fetcher: PermissionFetcher | None = None

    def __post_init__(self) -> None:
        self.repo_dir = Path(self.repo_dir)
        if not self.base_rev:
            self.base_rev = f"origin/{self.base_ref}"


@dataclass
class GateLine:
    level: str  # ok | fail | warn | info
    text: str


@dataclass
class GateReport:
    passed: bool = True
    waived: bool = False
    bootstrap: bool = False
    lines: list[GateLine] = field(default_factory=list)

    def ok(self, text: str) -> None:
        self.lines.append(GateLine("ok", text))

    def fail(self, text: str) -> None:
        self.passed = False
        self.lines.append(GateLine("fail", text))

    def warn(self, text: str) -> None:
        self.lines.append(GateLine("warn", text))

    def info(self, text: str) -> None:
        self.lines.append(GateLine("info", text))


def context_from_event(
    event_path: str | Path,
    repo_dir: str | Path,
    *,
    token: str | None = None,
    request_hash_expected: str | None = None,
    request_source_desc: str | None = None,
) -> GateContext:
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull = event.get("pull_request")
    if not isinstance(pull, dict):
        raise ValueError("event payload has no pull_request object; run the gate on pull_request events")
    repo = event.get("repository", {}).get("full_name")
    if not isinstance(repo, str) or "/" not in repo:
        raise ValueError("event payload has no repository.full_name")
    return GateContext(
        repo_dir=Path(repo_dir),
        repo=repo,
        pr_number=int(pull["number"]),
        pr_head_sha=str(pull["head"]["sha"]),
        base_ref=str(pull["base"]["ref"]),
        labels=[str(l["name"]) for l in pull.get("labels", []) if isinstance(l, dict) and "name" in l],
        token=token,
        request_hash_expected=request_hash_expected,
        request_source_desc=request_source_desc,
    )


def prepare_repo(ctx: GateContext) -> None:
    """Fetch the refs the gate needs. Network; skipped by unit tests."""
    run_git(ctx.repo_dir, "fetch", "--no-tags", "origin", ctx.base_ref)
    if not commit_exists(ctx.repo_dir, ctx.pr_head_sha):
        proc = run_git(ctx.repo_dir, "fetch", "--no-tags", "origin", ctx.pr_head_sha, check=False)
        if proc.returncode != 0:
            run_git(ctx.repo_dir, "fetch", "--no-tags", "origin", f"refs/pull/{ctx.pr_number}/head")
    if not commit_exists(ctx.repo_dir, ctx.pr_head_sha):
        raise GitError(f"PR head {ctx.pr_head_sha} not reachable after fetch")


def run_gate(ctx: GateContext) -> GateReport:
    report = GateReport()
    report.info(f"receipts-gate on {ctx.repo}#{ctx.pr_number} head={ctx.pr_head_sha[:12]} base={ctx.base_ref}")

    try:
        base_tip = rev_parse(ctx.repo_dir, ctx.base_rev)
    except GitError as exc:
        report.fail(f"cannot resolve base branch {ctx.base_rev}: {exc}")
        return report

    trusted_bytes = file_bytes_at(ctx.repo_dir, base_tip, TRUSTED_SIGNERS_PATH)
    policy_bytes = file_bytes_at(ctx.repo_dir, base_tip, POLICY_PATH)

    if trusted_bytes is None:
        if policy_bytes is not None:
            report.fail(
                f"{POLICY_PATH} exists on base branch '{ctx.base_ref}' but {TRUSTED_SIGNERS_PATH} does not. "
                "A policy without a trust anchor is a half-configured gate — someone armed it and someone "
                "(or something) disarmed the anchor. Failing closed instead of dropping to bootstrap mode."
            )
            return report
        return _bootstrap_report(ctx, report)

    try:
        anchor = parse_trusted_signers(trusted_bytes.decode("utf-8"))
    except TrustConfigError as exc:
        report.fail(f"trusted_signers.yml on base branch is malformed (failing closed): {exc}")
        return report

    if policy_bytes is None:
        policy = Policy.default()
        report.info("no policy.yml on base branch; using built-in default policy (strong evidence for all paths)")
    else:
        try:
            policy = parse_policy(policy_bytes.decode("utf-8"))
        except PolicyConfigError as exc:
            report.fail(f"policy.yml on base branch is malformed (failing closed): {exc}")
            return report

    try:
        pr_changed = changed_paths(ctx.repo_dir, base_tip, ctx.pr_head_sha)
    except GitError as exc:
        report.fail(f"cannot compute PR changed paths: {exc}")
        return report

    # The config-tamper check comes BEFORE the waiver on purpose: a waiver must
    # never be able to smuggle in a trust-anchor or policy change.
    config_touched = sorted(p for p in pr_changed if p == ".agent-receipts" or p.startswith(".agent-receipts/"))
    if config_touched:
        report.fail(
            "PR modifies gate configuration: " + ", ".join(config_touched) + ". "
            "Trust anchor and policy changes require maintainer review — a PR must not be able to admit its own "
            "signing key. The waiver label does NOT bypass this check; landing a config change requires a "
            "repo-settings-level bypass by an admin (which GitHub audits). See docs/RECEIPTS-GATE.md."
        )
        return report

    if policy.settings.waiver_label in ctx.labels:
        if _waiver_authorized(ctx, report, policy.settings.waiver_label):
            report.waived = True
            report.warn(
                f"WAIVED: '{policy.settings.waiver_label}' label applied by a verified maintainer. No receipt "
                "was verified for this PR; the waiver is the audit trail that a human took responsibility for "
                "this merge."
            )
            return report
        # Waiver not honored (reasons reported above); continue and require a receipt.

    candidates = _discover_receipts(ctx, report)
    if not candidates:
        if policy.settings.require_receipt:
            report.fail(
                f"no {RECEIPT_SCHEMA_VERSION} receipt found under {RECEIPTS_DIR}/ for {ctx.repo}#{ctx.pr_number}. "
                f"All PRs require a receipt; the only bypass is the maintainer-applied "
                f"'{policy.settings.waiver_label}' label."
            )
        else:
            report.warn("no receipt found; passing because policy sets require_receipt: false (report-only mode)")
        return report

    ledger = _read_ledger(ctx, base_tip)
    any_accepted = False
    for path, receipt in candidates:
        accepted = _evaluate_candidate(ctx, report, receipt, path, policy, anchor, base_tip, ledger)
        any_accepted = any_accepted or accepted

    if not any_accepted:
        report.fail("no receipt for this PR passed verification + policy")
    return report


def _waiver_authorized(ctx: GateContext, report: GateReport, waiver_label: str) -> bool:
    """Label presence alone is not a waiver: any token with triage rights —
    including a bot holding a maintainer's PAT and labeling its own PR — can
    apply labels. The gate honors the waiver only after confirming via the
    GitHub API that the label was applied by a user holding write, maintain,
    or admin on this repo. Anything it cannot prove, it refuses (fail closed);
    the report lines say exactly what was missing."""
    events_fetcher = ctx.label_events_fetcher
    permission_fetcher = ctx.permission_fetcher
    if events_fetcher is None or permission_fetcher is None:
        if not ctx.token:
            report.warn(
                f"waiver label '{waiver_label}' is present but the gate has no GitHub token to verify who "
                "applied it — waiver NOT honored (fail closed). A label whose applier cannot be identified "
                "is indistinguishable from a self-applied bypass."
            )
            return False
        token = ctx.token
        if events_fetcher is None:
            events_fetcher = lambda repo, pr: _github_issue_events(repo, pr, token)  # noqa: E731
        if permission_fetcher is None:
            permission_fetcher = lambda repo, login: _github_permission(repo, login, token)  # noqa: E731

    try:
        events = events_fetcher(ctx.repo, ctx.pr_number)
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        report.warn(f"waiver label '{waiver_label}': cannot fetch label events ({exc}) — waiver NOT honored (fail closed)")
        return False

    applier: str | None = None
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "labeled":
            continue
        if (event.get("label") or {}).get("name") == waiver_label:
            applier = (event.get("actor") or {}).get("login")
    if not applier:
        report.warn(
            f"waiver label '{waiver_label}': no 'labeled' event with an identifiable actor found for this PR "
            "— waiver NOT honored (fail closed)"
        )
        return False

    try:
        permission = permission_fetcher(ctx.repo, applier)
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        report.warn(
            f"waiver label '{waiver_label}': cannot verify repo permission of applier '{applier}' ({exc}) "
            "— waiver NOT honored (fail closed)"
        )
        return False
    if permission not in WAIVER_PERMISSIONS:
        report.warn(
            f"waiver label '{waiver_label}' was applied by '{applier}' whose repo permission is "
            f"{permission!r}; a waiver requires {sorted(WAIVER_PERMISSIONS)} — NOT honored"
        )
        return False

    report.ok(f"waiver label '{waiver_label}' applied by '{applier}' (verified repo permission: {permission})")
    return True


def _bootstrap_report(ctx: GateContext, report: GateReport) -> GateReport:
    report.bootstrap = True
    report.warn(
        f"NOT CONFIGURED: {TRUSTED_SIGNERS_PATH} does not exist on branch '{ctx.base_ref}'. "
        "The gate anchors trust in that file on the protected base branch and cannot enforce anything without it. "
        "Passing with this notice so adoption/bootstrap PRs are not blocked. To arm the gate, merge a "
        "trusted_signers.yml + policy.yml to the base branch (see docs/RECEIPTS-GATE.md). "
        "Bootstrap mode has NO expiry: until the anchor is merged, every PR passes with this notice, "
        "and only this notice distinguishes an armed repo from an unarmed one."
    )
    for path, receipt_doc in _discover_receipts(ctx, report):
        result = verify_record(receipt_doc)
        structural = validate_receipt(receipt_doc)
        if result.valid and not structural:
            report.info(f"{path}: signature valid, structure valid (UNENFORCED — no trust anchor on base branch)")
        else:
            reasons = "; ".join(([] if result.valid else [result.reason]) + structural)
            report.warn(f"{path}: would not verify: {reasons}")
    return report


def _discover_receipts(ctx: GateContext, report: GateReport) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    for path in ls_files_at(ctx.repo_dir, ctx.pr_head_sha, RECEIPTS_DIR):
        if not path.endswith(".json"):
            continue
        blob = file_bytes_at(ctx.repo_dir, ctx.pr_head_sha, path)
        if blob is None:
            continue
        if len(blob) > MAX_RECEIPT_BYTES:
            report.warn(f"{path}: skipped, larger than {MAX_RECEIPT_BYTES} bytes")
            continue
        try:
            doc = json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            report.warn(f"{path}: skipped, not valid JSON")
            continue
        if not isinstance(doc, dict) or doc.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            continue
        deliverable = doc.get("deliverable") or {}
        if deliverable.get("repo") == ctx.repo and deliverable.get("pr_number") == ctx.pr_number:
            found.append((path, doc))
    return found


def _read_ledger(ctx: GateContext, base_tip: str) -> dict[str, dict]:
    """Consumed-nonce ledger from the base branch: nonce -> entry."""
    blob = file_bytes_at(ctx.repo_dir, base_tip, LEDGER_PATH)
    entries: dict[str, dict] = {}
    if blob is None:
        return entries
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("nonce"), str):
            entries[entry["nonce"]] = entry
    return entries


def _evaluate_candidate(
    ctx: GateContext,
    report: GateReport,
    receipt: dict,
    path: str,
    policy: Policy,
    anchor,
    base_tip: str,
    ledger: dict[str, dict],
) -> bool:
    label = f"receipt {path}"
    problems: list[str] = []

    structural = validate_receipt(receipt)
    if structural:
        for p in structural:
            report.warn(f"{label}: structure: {p}")
        report.warn(f"{label}: REJECTED (structure)")
        return False

    sig_result = verify_record(receipt)
    if not sig_result.valid:
        report.warn(f"{label}: signature: {sig_result.reason}")
        report.warn(f"{label}: REJECTED (signature)")
        return False
    report.ok(f"{label}: signature valid (key_id {receipt['signature']['key_id'][:23]}...)")

    problems.extend(check_signer(anchor, receipt))

    if receipt["purpose"] != PURPOSE_GITHUB_PR_GATE:
        problems.append(f"purpose is {receipt['purpose']!r}, gate requires {PURPOSE_GITHUB_PR_GATE!r}")
    expected_audience = audience_for_repo(ctx.repo)
    if receipt["audience"] != expected_audience:
        problems.append(f"audience is {receipt['audience']!r}, gate requires {expected_audience!r} (anti-replay scope)")

    deliverable = receipt["deliverable"]
    base_sha = deliverable["base_sha"]
    signed_head = deliverable["head_sha"]

    for sha, name in ((base_sha, "base_sha"), (signed_head, "head_sha")):
        if not commit_exists(ctx.repo_dir, sha):
            problems.append(f"{name} {sha[:12]} is not present locally; gate needs a full fetch (fetch-depth: 0)")

    if not problems:
        if not is_ancestor(ctx.repo_dir, base_sha, base_tip):
            problems.append(
                f"base_sha {base_sha[:12]} is not an ancestor of {ctx.base_rev}. The receipt must diff against a "
                "real base-branch commit; otherwise the signed diff can hide changes the PR actually makes."
            )

        if signed_head == ctx.pr_head_sha:
            report.ok(f"{label}: head_sha matches PR head exactly")
        elif is_ancestor(ctx.repo_dir, signed_head, ctx.pr_head_sha):
            trailing = changed_paths(ctx.repo_dir, signed_head, ctx.pr_head_sha, three_dot=False)
            non_receipt = [p for p in trailing if not p.startswith(f"{RECEIPTS_DIR}/")]
            if non_receipt:
                problems.append(
                    "commits after the signed head_sha touch non-receipt paths: "
                    + ", ".join(sorted(non_receipt)[:10])
                    + ". Only receipts/** may change after signing (sign-then-attach)."
                )
            else:
                report.ok(f"{label}: PR head extends signed head with receipt-only commits ({len(trailing)} path(s))")
        else:
            problems.append(
                f"STALE: signed head_sha {signed_head[:12]} is not an ancestor of PR head "
                f"{ctx.pr_head_sha[:12]} (force-push or rebase after signing). Re-emit the receipt for the new head."
            )

    if not problems:
        recomputed = canonical_diff_hash(ctx.repo_dir, base_sha, signed_head)
        if recomputed != deliverable["diff_hash"]:
            problems.append(
                f"diff hash mismatch: receipt claims {deliverable['diff_hash'][:23]}..., gate recomputed "
                f"{recomputed[:23]}... from git diff {base_sha[:12]}...{signed_head[:12]}"
            )
        else:
            report.ok(f"{label}: canonical diff hash independently recomputed and matched")

    nonce = receipt["nonce"]
    prior = ledger.get(nonce)
    if prior and not (prior.get("receipt_id") == receipt["receipt_id"] and prior.get("pr") == ctx.pr_number):
        problems.append(f"nonce already consumed by {prior.get('receipt_id')} (pr #{prior.get('pr')}): replay rejected")

    if ctx.request_hash_expected:
        if receipt["request"]["hash"] == ctx.request_hash_expected:
            report.ok(f"{label}: request.hash matches the issuer-provided request ({ctx.request_source_desc or 'workflow input'})")
        else:
            problems.append(
                f"request.hash {receipt['request']['hash'][:23]}... does not match the issuer-provided request "
                f"hash {ctx.request_hash_expected[:23]}... — the signed work may answer a different task than issued"
            )
    elif policy.settings.require_request_binding:
        problems.append(
            "policy requires request binding but the workflow provided no request source "
            "(set the action's request-source-file or request-hash input)"
        )
    else:
        report.info(
            f"{label}: request.hash present but NOT checked — no issuer-held request was provided to the gate. "
            "Request binding is only as strong as the verifier's knowledge of the original request."
        )

    if problems:
        for p in problems:
            report.warn(f"{label}: {p}")
        report.warn(f"{label}: REJECTED (verification)")
        return False

    changed_for_policy = changed_paths(ctx.repo_dir, base_sha, ctx.pr_head_sha)
    workflows_changed = sorted(p for p in changed_for_policy if p.startswith(".github/workflows/"))

    verified_methods = _verify_evidence(ctx, report, receipt, label, policy, signed_head, workflows_changed)

    decision = evaluate(policy, changed_for_policy, verified_methods)
    if decision.passed:
        report.ok(f"{label}: policy satisfied for all {len(decision.findings)} changed path(s)")
        return True
    for finding in decision.failed_paths[:20]:
        report.warn(
            f"{label}: policy: '{finding.path}' (rule: {finding.rule}) requires verified evidence of "
            f"{ ' or '.join(finding.require) }; receipt has: {sorted(verified_methods) or 'none'}"
        )
    report.warn(f"{label}: REJECTED (policy: {len(decision.failed_paths)} path(s) lack required evidence)")
    return False


def _verify_evidence(
    ctx: GateContext,
    report: GateReport,
    receipt: dict,
    label: str,
    policy: Policy,
    signed_head: str,
    workflows_changed: list[str],
) -> set[str]:
    verified: set[str] = set()
    for i, item in enumerate(receipt.get("evidence", [])):
        method = item["method"]
        where = f"{label}: evidence[{i}] ({method})"
        if method == "self_claimed":
            report.info(f"{where}: \"{item['claim'][:160]}\" — disclosure only, never satisfies policy")
        elif method == "content_addressed":
            blob = file_bytes_at(ctx.repo_dir, signed_head, item["path"])
            if blob is None:
                report.warn(f"{where}: {item['path']} does not exist at signed head — NOT verified")
            elif "sha256:" + hashlib.sha256(blob).hexdigest() == item["sha256"]:
                verified.add(method)
                report.ok(f"{where}: {item['path']} hash matches at signed head")
            else:
                report.warn(f"{where}: {item['path']} hash MISMATCH at signed head — NOT verified")
        elif method == "ci_attested":
            ok, detail = _verify_ci_attested(ctx, item, signed_head)
            if ok and workflows_changed and policy.settings.distrust_ci_when_workflows_change:
                report.warn(
                    f"{where}: {detail}; but this PR modifies {', '.join(workflows_changed[:3])} — CI attestation "
                    "attests whatever the (modified) workflow does, so it is DISCOUNTED for this PR"
                )
            elif ok:
                verified.add(method)
                report.ok(f"{where}: {detail}")
            else:
                report.warn(f"{where}: {detail} — NOT verified")
        elif method == "re_executable":
            cmd = item["cmd"]
            if cmd not in policy.settings.re_executable_allowlist:
                report.warn(
                    f"{where}: command not in policy re_executable_allowlist — NOT executed, NOT verified. "
                    "Receipts are signed, not trusted; the base-branch policy decides what may run."
                )
            else:
                ok, detail = _run_re_executable(ctx, item, signed_head)
                if ok:
                    verified.add(method)
                    report.ok(f"{where}: {detail}")
                else:
                    report.warn(f"{where}: {detail} — NOT verified")
    return verified


def _verify_ci_attested(ctx: GateContext, item: dict, signed_head: str) -> tuple[bool, str]:
    # The receipt does not get to choose which commit CI attests. A receipt-
    # supplied `sha` is accepted only as a redundant statement of the signed
    # head; anything else is an attempt to borrow a green check from an
    # unrelated commit.
    claimed_sha = item.get("sha")
    if claimed_sha and claimed_sha != signed_head:
        return False, (
            f"evidence pins sha {claimed_sha[:12]} but the signed head is {signed_head[:12]} — "
            "ci_attested must attest the signed work, not another commit"
        )
    sha = signed_head
    fetcher = ctx.check_runs_fetcher
    if fetcher is None:
        if not ctx.token:
            return False, "no GitHub token available to fetch check runs"
        fetcher = lambda repo, commit: _github_check_runs(repo, commit, ctx.token)  # noqa: E731
    try:
        runs = fetcher(ctx.repo, sha)
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        return False, f"failed to fetch check runs for {sha[:12]}: {exc}"
    wanted_id = item.get("check_run_id")
    wanted_name = item.get("check_name")
    for run in runs:
        if wanted_id is not None and run.get("id") != wanted_id:
            continue
        if wanted_id is None and run.get("name") != wanted_name:
            continue
        # Defense in depth: require the API's own head_sha on the run to match
        # the commit we asked about. Anything else (missing field included)
        # fails closed.
        if run.get("head_sha") != sha:
            return False, (
                f"check run '{run.get('name')}' reports head_sha "
                f"{str(run.get('head_sha'))[:12]!r}, not the signed head {sha[:12]} — refusing cross-commit attestation"
            )
        if run.get("status") != "completed":
            return False, f"check run '{run.get('name')}' at {sha[:12]} has not completed"
        if run.get("conclusion") != "success":
            return False, f"check run '{run.get('name')}' at {sha[:12]} concluded {run.get('conclusion')!r}"
        return True, f"GitHub reports check run '{run.get('name')}' at {sha[:12]} completed with success"
    return False, f"no check run matching {wanted_id or wanted_name!r} found at {sha[:12]}"


def _github_get_json(url: str, token: str) -> tuple[Any, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "signed-agent-receipts-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload, response.headers.get("Link", "")


def _next_page(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def _github_check_runs(repo: str, sha: str, token: str) -> list[dict]:
    runs: list[dict] = []
    url: str | None = f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100"
    for _ in range(5):  # bounded pagination
        if url is None:
            break
        payload, links = _github_get_json(url, token)
        runs.extend(payload.get("check_runs", []))
        url = _next_page(links)
    return runs


def _github_issue_events(repo: str, pr_number: int, token: str) -> list[dict]:
    """Issue events for a PR (labels applied/removed, with actor). PRs are
    issues in the GitHub API, so this covers pull requests."""
    events: list[dict] = []
    url: str | None = f"https://api.github.com/repos/{repo}/issues/{pr_number}/events?per_page=100"
    for _ in range(10):  # bounded pagination
        if url is None:
            break
        payload, links = _github_get_json(url, token)
        if isinstance(payload, list):
            events.extend(payload)
        url = _next_page(links)
    return events


def _github_permission(repo: str, login: str, token: str) -> str | None:
    """Effective repo permission for a user: admin | maintain | write |
    triage | read | none (custom role names pass through and simply won't be
    in WAIVER_PERMISSIONS). 404 means not a collaborator."""
    url = f"https://api.github.com/repos/{repo}/collaborators/{urllib.parse.quote(login)}/permission"
    try:
        payload, _ = _github_get_json(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "none"
        raise
    role = payload.get("role_name") or payload.get("permission")
    return role if isinstance(role, str) else None


def _run_re_executable(ctx: GateContext, item: dict, signed_head: str) -> tuple[bool, str]:
    # The command string is exact-match allowlisted from the base-branch
    # policy before we get here, and it runs WITHOUT a shell: allowlist
    # entries are argv-split, so shell metacharacters in a receipt cannot
    # change what executes.
    try:
        argv = shlex.split(item["cmd"])
    except ValueError as exc:
        return False, f"cmd could not be parsed as an argv (no shell is used): {exc}"
    if not argv:
        return False, "cmd is empty after argv parsing"

    with _temp_worktree(ctx.repo_dir, signed_head) as workdir:
        # cwd must stay inside the verification worktree. An absolute or
        # escaping cwd would let a receipt "pass" its command in a directory
        # with none of the PR's code in it (e.g. an empty /tmp).
        workdir_resolved = workdir.resolve()
        cwd = workdir_resolved
        raw_cwd = item.get("cwd")
        if raw_cwd:
            candidate = Path(raw_cwd)
            if candidate.is_absolute() or ".." in candidate.parts:
                return False, f"cwd {raw_cwd!r} rejected: must be a relative path with no '..' segments"
            cwd = (workdir_resolved / candidate).resolve()
            if not cwd.is_relative_to(workdir_resolved):  # symlink escapes land here
                return False, f"cwd {raw_cwd!r} rejected: resolves outside the verification worktree"
            if not cwd.is_dir():
                return False, f"cwd {raw_cwd!r} rejected: not a directory at the signed head"
        try:
            proc = subprocess.run(
                argv,
                shell=False,
                cwd=str(cwd),
                capture_output=True,
                timeout=RE_EXECUTABLE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, f"command timed out after {RE_EXECUTABLE_TIMEOUT_SECONDS}s"
        except (FileNotFoundError, PermissionError) as exc:
            return False, f"command could not be executed: {exc}"
        if proc.returncode != item["expected_exit_code"]:
            return False, f"exit code {proc.returncode}, expected {item['expected_exit_code']}"
        if item.get("expected_output_sha256"):
            actual = "sha256:" + hashlib.sha256(proc.stdout).hexdigest()
            if actual != item["expected_output_sha256"]:
                return False, "stdout hash does not match expected_output_sha256"
        if item.get("expected_output_contains"):
            combined = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            if item["expected_output_contains"] not in combined:
                return False, "output does not contain expected_output_contains"
        return True, f"re-executed '{item['cmd']}' at signed head: exit {proc.returncode} as expected"


@contextmanager
def _temp_worktree(repo_dir: Path, sha: str):
    tmp = tempfile.mkdtemp(prefix="receipts-gate-wt-")
    run_git(repo_dir, "worktree", "add", "--detach", tmp, sha)
    try:
        yield Path(tmp)
    finally:
        run_git(repo_dir, "worktree", "remove", "--force", tmp, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def consume_receipt(receipt: dict, ledger_path: str | Path, *, pr_number: int | None = None) -> bool:
    """Append the receipt's nonce to the local ledger file. Returns False if
    the nonce is already present. Intended for a post-merge job on the base
    branch; stateless verifiers cannot enforce freshness (see SECURITY-MODEL.md).

    The check-then-append runs under an exclusive flock so concurrent
    consumers on one machine cannot both claim the same nonce. Across
    machines, serialization comes from the ledger living in git: two racing
    consumers produce conflicting pushes, not a silently doubled nonce. On
    platforms without fcntl the lock is skipped (documented residual)."""
    path = Path(ledger_path).expanduser()
    nonce = receipt["nonce"]
    entry = {
        "nonce": nonce,
        "receipt_id": receipt.get("receipt_id"),
        "pr": pr_number if pr_number is not None else (receipt.get("deliverable") or {}).get("pr_number"),
        "consumed_at": utc_now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as f:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            for line in f.read().splitlines():
                try:
                    if json.loads(line).get("nonce") == nonce:
                        return False
                except ValueError:
                    continue
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return True


def render_summary_markdown(report: GateReport) -> str:
    icon = {"ok": "PASS", "fail": "FAIL", "warn": "WARN", "info": "note"}
    header = "# receipts-gate\n\n"
    if report.waived:
        verdict = "**WAIVED** — human took responsibility via label; nothing was verified.\n\n"
    elif report.bootstrap:
        verdict = "**NOT CONFIGURED** — no trust anchor on base branch; nothing was enforced.\n\n"
    elif report.passed:
        verdict = "**PASSED** — a receipt for this PR verified and satisfied policy.\n\n"
    else:
        verdict = "**FAILED**\n\n"
    body = "\n".join(f"- `{icon[line.level]}` {line.text}" for line in report.lines)
    return header + verdict + body + "\n"


def print_report(report: GateReport) -> None:
    prefix = {"ok": "  ok  ", "fail": " FAIL ", "warn": " warn ", "info": " info "}
    for line in report.lines:
        print(f"[{prefix[line.level]}] {line.text}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(render_summary_markdown(report))
