"""Wiring-level regressions for the self-referential-gate class of bugs.

The gate's code checks are only as good as where the gate RUNS FROM: a
workflow (or a composite action) that checks out a PR and then executes the
gate — or even pip — from that checkout lets any PR rewrite its own verifier.
These tests pin the dogfood workflow AND the published action to the trusted,
isolated-execution pattern and pin maintainer ownership over every path that
defines what the gate is.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _run_steps(steps):
    return [str(s.get("run", "")) for s in steps if s.get("run")]


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
        install_lines = [
            line
            for run in _run_steps(self.steps)
            for line in run.splitlines()
            if "pip install" in line
        ]
        self.assertTrue(install_lines, "expected a pip install of the gate")
        for line in install_lines:
            self.assertIn("TRUSTED_GATE", line, "install must come from the trusted base checkout")
            self.assertNotIn("/pr", line, "install must never come from the PR checkout")

    def test_python_invocations_are_isolated_from_cwd(self):
        # Every `python -m pip`/`python -m agent_receipts` in the workflow must
        # use -I so a pip.py/agent_receipts dropped in CWD cannot be imported.
        for run in _run_steps(self.steps):
            for line in run.splitlines():
                if "python -m pip" in line or "python -m agent_receipts" in line:
                    self.fail(f"non-isolated python -m invocation in workflow: {line.strip()!r}")

    def test_gate_run_step_uses_neutral_working_directory(self):
        runs_python = [
            s
            for s in self.steps
            if "python -I -m" in str(s.get("run", ""))
        ]
        self.assertTrue(runs_python, "expected an isolated python run step")
        for step in runs_python:
            wd = str(step.get("working-directory", ""))
            self.assertIn("runner.temp", wd, "python steps must run from a neutral dir, not the PR workspace")


class PublishedActionIsolationTests(unittest.TestCase):
    """The dogfood workflow is fixed, but downstream adopters run the PUBLISHED
    composite action. Its default working-directory is the untrusted PR
    workspace, so `python -m pip` there would import a PR-dropped pip.py before
    the real pip. The action must isolate both python invocations."""

    def setUp(self):
        self.action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
        self.steps = self.action["runs"]["steps"]
        self.run_steps = [s for s in self.steps if s.get("run")]

    def test_pip_install_is_isolated(self):
        installs = [s for s in self.run_steps if "pip install" in str(s.get("run", ""))]
        self.assertTrue(installs)
        for step in installs:
            run = str(step["run"])
            self.assertIn("python -I -m pip", run, "pip must run isolated (-I) so CWD pip.py cannot hijack it")
            self.assertIn("--isolated", run)

    def test_no_bare_python_m_or_console_script(self):
        for step in self.run_steps:
            run = str(step["run"])
            for line in run.splitlines():
                stripped = line.strip()
                if stripped.startswith("python -m ") or stripped.startswith("signed-agent-receipts "):
                    self.fail(f"published action has a non-isolated invocation: {stripped!r}")

    def test_python_steps_run_outside_the_pr_workspace(self):
        pysteps = [s for s in self.run_steps if "python -I -m" in str(s.get("run", ""))]
        self.assertTrue(pysteps)
        for step in pysteps:
            wd = str(step.get("working-directory", ""))
            self.assertIn("runner.temp", wd, "action python steps must not run from $GITHUB_WORKSPACE (the PR)")


class IsolationExecutionTest(unittest.TestCase):
    """Prove the mechanism, not just the wiring: a pip.py dropped in the CWD is
    executed by `python -m pip` but ignored by `python -I -m pip`."""

    def _run(self, isolated: bool, workdir: Path):
        args = [sys.executable] + (["-I"] if isolated else []) + ["-m", "pip", "--version"]
        return subprocess.run(args, cwd=str(workdir), capture_output=True, text=True, timeout=60)

    def test_dropped_pip_py_runs_without_isolation_but_not_with_it(self):
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            canary = workdir / "CANARY"
            (workdir / "pip.py").write_text(
                f"open({str(canary)!r}, 'w').write('hijacked')\n", encoding="utf-8"
            )
            # Vulnerable form proves the attack is real in this interpreter.
            self._run(False, workdir)
            self.assertTrue(canary.exists(), "sanity: non-isolated python -m pip must run the dropped pip.py")
            canary.unlink()
            # The form the action uses: CWD is not on sys.path, so pip.py never runs.
            self._run(True, workdir)
            self.assertFalse(canary.exists(), "python -I -m pip must NOT execute a CWD-resident pip.py")


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
