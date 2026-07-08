import unittest

from agent_receipts.policy import (
    Policy,
    PolicyConfigError,
    evaluate,
    glob_to_regex,
    parse_policy,
)

POLICY = """\
version: 1
settings:
  require_receipt: true
  waiver_label: human-waiver
default_require: [re_executable, ci_attested]
rules:
  - name: docs
    paths: ["docs/**", "**/*.md"]
    require: []
  - name: receipts
    paths: ["receipts/**"]
    require: []
  - name: generated
    paths: ["gen/**"]
    require: [content_addressed]
"""


class GlobTests(unittest.TestCase):
    def test_double_star_spans_directories(self):
        self.assertTrue(glob_to_regex("docs/**").match("docs/a/b/c.md"))
        self.assertTrue(glob_to_regex("docs/**").match("docs/top.md"))
        self.assertFalse(glob_to_regex("docs/**").match("src/docs.py"))

    def test_leading_double_star_matches_any_depth_including_root(self):
        rx = glob_to_regex("**/*.py")
        self.assertTrue(rx.match("top.py"))
        self.assertTrue(rx.match("a/b/c.py"))
        self.assertFalse(rx.match("a/b/c.pyc"))

    def test_single_star_stays_within_segment(self):
        rx = glob_to_regex("*.md")
        self.assertTrue(rx.match("README.md"))
        self.assertFalse(rx.match("docs/README.md"))

    def test_trailing_slash_means_subtree(self):
        self.assertTrue(glob_to_regex("vendor/").match("vendor/lib/x.js"))


class ParseTests(unittest.TestCase):
    def test_parses_valid_policy(self):
        policy = parse_policy(POLICY)
        self.assertEqual(policy.default_require, ["re_executable", "ci_attested"])
        self.assertEqual([r.name for r in policy.rules], ["docs", "receipts", "generated"])

    def test_self_claimed_may_not_be_required(self):
        bad = POLICY.replace("require: [content_addressed]", "require: [self_claimed]")
        with self.assertRaises(PolicyConfigError):
            parse_policy(bad)

    def test_unknown_method_rejected(self):
        bad = POLICY.replace("require: [content_addressed]", "require: [vibes]")
        with self.assertRaises(PolicyConfigError):
            parse_policy(bad)

    def test_unknown_settings_key_rejected(self):
        with self.assertRaises(PolicyConfigError):
            parse_policy("version: 1\nsettings:\n  frobnicate: true\n")

    def test_version_required(self):
        with self.assertRaises(PolicyConfigError):
            parse_policy("settings: {}\n")


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.policy = parse_policy(POLICY)

    def test_code_paths_need_strong_evidence(self):
        decision = evaluate(self.policy, ["src/app.py"], set())
        self.assertFalse(decision.passed)
        decision = evaluate(self.policy, ["src/app.py"], {"ci_attested"})
        self.assertTrue(decision.passed)
        decision = evaluate(self.policy, ["src/app.py"], {"re_executable"})
        self.assertTrue(decision.passed)

    def test_docs_pass_without_evidence(self):
        decision = evaluate(self.policy, ["docs/guide.md", "README.md"], set())
        self.assertTrue(decision.passed)

    def test_self_claimed_never_satisfies_even_if_injected(self):
        decision = evaluate(self.policy, ["src/app.py"], {"self_claimed"})
        self.assertFalse(decision.passed)

    def test_first_matching_rule_wins(self):
        # docs rule (require: []) precedes generated; a md file under gen/
        # matches "**/*.md" in the docs rule only if docs rule patterns hit
        # first — it does, by rule order.
        decision = evaluate(self.policy, ["gen/notes.md"], set())
        self.assertTrue(decision.passed)
        self.assertEqual(decision.findings[0].rule, "docs")

    def test_unmatched_paths_fail_closed_with_default(self):
        decision = evaluate(Policy.default(), ["mystery.bin"], set())
        self.assertFalse(decision.passed)
        self.assertEqual(decision.findings[0].rule, "(default)")

    def test_mixed_paths_report_only_failures(self):
        decision = evaluate(self.policy, ["docs/guide.md", "src/app.py"], set())
        self.assertFalse(decision.passed)
        self.assertEqual([f.path for f in decision.failed_paths], ["src/app.py"])


if __name__ == "__main__":
    unittest.main()
