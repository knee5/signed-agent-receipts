"""Gate tests double as the adversarial harness: every rejection case here is
a lie the plan says the gate must catch (mismatched deliverable, fabricated
evidence, replay, task substitution, config tampering, stale signatures)."""

import json
import tempfile
import unittest
from pathlib import Path

from agent_receipts.gate import GateContext, consume_receipt, run_gate
from agent_receipts.gitdiff import rev_parse
from agent_receipts.receipt import hash_request_text
from agent_receipts.signing import public_key_material

from tests.support import (
    REPO_NAME,
    emit_and_attach_receipt,
    git,
    make_fixture,
    passing_check_runs,
)


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)

    def ctx(self, fixture, **overrides):
        kwargs = dict(
            repo_dir=fixture.repo_dir,
            repo=REPO_NAME,
            pr_number=7,
            pr_head_sha=fixture.pr_head,
            base_ref="main",
            base_rev="main",
            check_runs_fetcher=passing_check_runs,
        )
        kwargs.update(overrides)
        return GateContext(**kwargs)

    def assertReportContains(self, report, fragment):
        joined = "\n".join(line.text for line in report.lines)
        self.assertIn(fragment, joined, f"expected {fragment!r} in report:\n{joined}")


class HappyPathTests(GateTestCase):
    def test_ci_attested_receipt_passes(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[
                {"method": "ci_attested", "provider": "github", "check_name": "python-ci"},
                {"method": "self_claimed", "claim": "I also manually checked the docs render."},
            ],
        )
        report = run_gate(self.ctx(fixture))
        self.assertTrue(report.passed, [l.text for l in report.lines])
        self.assertReportContains(report, "canonical diff hash independently recomputed and matched")
        self.assertReportContains(report, "policy satisfied")

    def test_allowlisted_re_executable_passes(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[
                {
                    "method": "re_executable",
                    "cmd": "echo fixture-check",
                    "expected_exit_code": 0,
                    "expected_output_contains": "fixture-check",
                }
            ],
        )
        report = run_gate(self.ctx(fixture))
        self.assertTrue(report.passed, [l.text for l in report.lines])

    def test_content_addressed_verifies_but_code_still_needs_strong_evidence(self):
        fixture = make_fixture(self.tmp)
        blob = (fixture.repo_dir / "src" / "app.py").read_bytes()
        import hashlib

        emit_and_attach_receipt(
            fixture,
            evidence=[
                {
                    "method": "content_addressed",
                    "path": "src/app.py",
                    "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
                }
            ],
        )
        report = run_gate(self.ctx(fixture))
        # content_addressed verifies, but default_require for code is
        # re_executable|ci_attested, so acceptance still fails.
        self.assertReportContains(report, "hash matches at signed head")
        self.assertFalse(report.passed)

    def test_trailing_receipt_only_commit_is_allowed(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertTrue(report.passed)
        self.assertReportContains(report, "receipt-only commits")


class LyingAgentTests(GateTestCase):
    def test_no_receipt_fails(self):
        fixture = make_fixture(self.tmp)
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "no agent-receipt.v0.2 receipt found")

    def test_self_claimed_only_never_passes_code_changes(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "self_claimed", "claim": "trust me, all tests pass"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "disclosure only, never satisfies policy")

    def test_diff_hash_mismatch_rejected(self):
        # The cheater signs a diff hash for work it did NOT deliver.
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            diff_hash="sha256:" + "d" * 64,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "diff hash mismatch")

    def test_work_added_after_signing_rejected(self):
        # Sign honest work, then sneak an extra code commit into the PR.
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        (fixture.repo_dir / "src" / "sneaky.py").write_text("EVIL = True\n", encoding="utf-8")
        git(fixture.repo_dir, "add", "src")
        git(fixture.repo_dir, "commit", "-qm", "sneaky extra work")
        fixture.pr_head = rev_parse(fixture.repo_dir, "HEAD")
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "touch non-receipt paths")

    def test_force_push_after_signing_rejected_as_stale(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        receipt_blob = (fixture.repo_dir / "receipts" / "pr-7.receipt.json").read_bytes()
        # Rewrite history: amend the work commit, recommit the old receipt.
        git(fixture.repo_dir, "reset", "--hard", fixture.base_sha)
        (fixture.repo_dir / "src" / "app.py").write_text("def greet():\n    return 'replaced'\n", encoding="utf-8")
        git(fixture.repo_dir, "add", "-A")
        git(fixture.repo_dir, "commit", "-qm", "rewritten work")
        (fixture.repo_dir / "receipts").mkdir(exist_ok=True)
        (fixture.repo_dir / "receipts" / "pr-7.receipt.json").write_bytes(receipt_blob)
        git(fixture.repo_dir, "add", "receipts")
        git(fixture.repo_dir, "commit", "-qm", "reattach old receipt")
        fixture.pr_head = rev_parse(fixture.repo_dir, "HEAD")
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "STALE")

    def test_task_substitution_caught_when_issuer_provides_request(self):
        # The agent solved (and honestly signed) a DIFFERENT task.
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            request_hash=hash_request_text("an easier task the agent picked for itself"),
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(
            self.ctx(
                fixture,
                request_hash_expected=fixture.request_hash(),
                request_source_desc="issuer task file",
            )
        )
        self.assertFalse(report.passed)
        self.assertReportContains(report, "different task than issued")

    def test_request_binding_passes_when_hashes_match(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture, request_hash_expected=fixture.request_hash()))
        self.assertTrue(report.passed)
        self.assertReportContains(report, "request.hash matches")

    def test_untrusted_signer_rejected(self):
        fixture = make_fixture(self.tmp)
        rogue_key = self.tmp / "rogue.pem"
        public_key_material(rogue_key)
        emit_and_attach_receipt(
            fixture,
            key_path=rogue_key,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "not in trusted_signers.yml")

    def test_pr_modifying_gate_config_rejected(self):
        fixture = make_fixture(self.tmp)
        signers_path = fixture.repo_dir / ".agent-receipts" / "trusted_signers.yml"
        rogue_key = self.tmp / "rogue.pem"
        rogue_public, rogue_id = public_key_material(rogue_key)
        signers_path.write_text(
            signers_path.read_text()
            + f'  - name: rogue\n    key_id: "{rogue_id}"\n    public_key: "{rogue_public}"\n',
            encoding="utf-8",
        )
        git(fixture.repo_dir, "add", ".agent-receipts")
        git(fixture.repo_dir, "commit", "-qm", "add my own key")
        fixture.pr_head = rev_parse(fixture.repo_dir, "HEAD")
        emit_and_attach_receipt(
            fixture,
            key_path=rogue_key,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "PR modifies gate configuration")

    def test_replayed_nonce_rejected(self):
        nonce = "ab" * 16
        ledger_line = json.dumps(
            {"nonce": nonce, "receipt_id": "rcpt_older", "pr": 3, "consumed_at": "2026-01-01T00:00:00+00:00"}
        )
        fixture = make_fixture(self.tmp, consumed_lines=[ledger_line])
        emit_and_attach_receipt(
            fixture,
            nonce=nonce,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "replay rejected")

    def test_fabricated_ci_evidence_rejected(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "made-up-check"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "no check run matching")

    def test_failed_ci_run_rejected(self):
        fixture = make_fixture(self.tmp)
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )

        def failing_runs(repo, sha):
            return [{"id": 11, "name": "python-ci", "status": "completed", "conclusion": "failure"}]

        report = run_gate(self.ctx(fixture, check_runs_fetcher=failing_runs))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "concluded 'failure'")

    def test_non_allowlisted_re_executable_not_run(self):
        fixture = make_fixture(self.tmp)
        canary = self.tmp / "canary"
        emit_and_attach_receipt(
            fixture,
            evidence=[
                {
                    "method": "re_executable",
                    "cmd": f"touch {canary}",
                    "expected_exit_code": 0,
                }
            ],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "not in policy re_executable_allowlist")
        self.assertFalse(canary.exists(), "gate must never execute non-allowlisted receipt commands")

    def test_ci_attestation_discounted_when_pr_changes_workflows(self):
        fixture = make_fixture(self.tmp)
        workflows = fixture.repo_dir / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        git(fixture.repo_dir, "add", ".github")
        git(fixture.repo_dir, "commit", "-qm", "add workflow")
        fixture.work_head = rev_parse(fixture.repo_dir, "HEAD")
        emit_and_attach_receipt(
            fixture,
            evidence=[{"method": "ci_attested", "provider": "github", "check_name": "python-ci"}],
        )
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "DISCOUNTED")


class ModeTests(GateTestCase):
    def test_waiver_label_passes_loudly_without_verification(self):
        fixture = make_fixture(self.tmp)
        report = run_gate(self.ctx(fixture, labels=["human-waiver"]))
        self.assertTrue(report.passed)
        self.assertTrue(report.waived)
        self.assertReportContains(report, "WAIVED")

    def test_bootstrap_mode_passes_with_notice_when_no_trust_anchor(self):
        fixture = make_fixture(self.tmp, with_trust_anchor=False)
        report = run_gate(self.ctx(fixture))
        self.assertTrue(report.passed)
        self.assertTrue(report.bootstrap)
        self.assertReportContains(report, "NOT CONFIGURED")

    def test_malformed_trust_anchor_fails_closed(self):
        fixture = make_fixture(self.tmp)
        git(fixture.repo_dir, "checkout", "-q", "main")
        (fixture.repo_dir / ".agent-receipts" / "trusted_signers.yml").write_text(
            "version: 1\nsigners: broken", encoding="utf-8"
        )
        git(fixture.repo_dir, "add", ".agent-receipts")
        git(fixture.repo_dir, "commit", "-qm", "break config")
        git(fixture.repo_dir, "checkout", "-q", "feat")
        report = run_gate(self.ctx(fixture))
        self.assertFalse(report.passed)
        self.assertReportContains(report, "failing closed")


class LedgerTests(unittest.TestCase):
    def test_consume_appends_once(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "consumed.jsonl"
            receipt = {"nonce": "aa" * 16, "receipt_id": "rcpt_x", "deliverable": {"pr_number": 9}}
            self.assertTrue(consume_receipt(receipt, ledger))
            self.assertFalse(consume_receipt(receipt, ledger))
            lines = ledger.read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["pr"], 9)


if __name__ == "__main__":
    unittest.main()
