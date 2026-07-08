"""Wiring-level regressions for the self-referential-gate class of bugs.

The gate's code checks are only as good as where the gate RUNS FROM: a
workflow that checks out a PR and then executes the gate from that checkout
lets any PR rewrite its own verifier. These tests pin the dogfood workflow to
the trusted-base pattern and pin maintainer ownership over every path that
defines what the gate is.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class GateWorkflowWiringTests(unittest.TestCase):
    def setUp(self):
        text = (ROOT / ".github" / "workflows" / "receipts-gate.yml").read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(text)
        self.steps = self.workflow["jobs"]["receipts-gate"]["steps"]

    def test_no_step_runs_a_local_action_from_pr_content(self):
        # `uses: ./` (or any local path) after a PR checkout executes the
        # PR's own files as the action. That is the self-bypass.
        for step in self.steps:
            uses = str(step.get("uses", ""))
            self.assertFalse(
                uses.startswith("./") or uses.startswith("../"),
                f"gate workflow must not execute a local action from PR content: {uses!r}",
            )

    def test_pr_is_data_and_verifier_comes_from_base_ref(self):
        checkouts = [s for s in self.steps if str(s.get("uses", "")).startswith("actions/checkout")]
        self.assertEqual(len(checkouts), 2, "expect exactly: PR checkout (data) + trusted base checkout (code)")
        by_path = {c.get("with", {}).get("path"): c for c in checkouts}
        self.assertIn("pr", by_path)
        self.assertIn("trusted-gate", by_path)
        trusted = by_path["trusted-gate"]
        self.assertEqual(
            trusted["with"].get("ref"),
            "${{ github.event.pull_request.base.ref }}",
            "the verifier checkout must pin the PR's base ref, which a PR author cannot influence",
        )

    def test_run_steps_install_only_from_the_trusted_checkout(self):
        installs = [s.get("run", "") for s in self.steps if "pip install" in str(s.get("run", ""))]
        self.assertTrue(installs, "expected a pip install of the gate")
        for run in installs:
            self.assertIn("./trusted-gate", run)
            self.assertNotIn("./pr", run)


class OwnershipTests(unittest.TestCase):
    def test_codeowners_covers_the_entire_gate_surface(self):
        text = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        for path in (
            "/.agent-receipts/",
            "/.github/",
            "/action.yml",
            "/agent_receipts/",
            "/schema/",
            "/pyproject.toml",
        ):
            self.assertIn(path, text, f"CODEOWNERS must cover {path}")


class PolicyArmingTests(unittest.TestCase):
    def test_request_binding_is_armed_in_this_repos_policy(self):
        policy = yaml.safe_load((ROOT / ".agent-receipts" / "policy.yml").read_text(encoding="utf-8"))
        self.assertIs(
            policy["settings"]["require_request_binding"],
            True,
            "request binding must stay armed: without it, 'solved an easier task' receipts pass",
        )


if __name__ == "__main__":
    unittest.main()
