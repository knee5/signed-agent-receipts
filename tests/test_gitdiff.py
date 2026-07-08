import tempfile
import unittest
from pathlib import Path

from agent_receipts.gitdiff import (
    canonical_diff_bytes,
    canonical_diff_hash,
    changed_paths,
    is_ancestor,
    rev_parse,
)

from tests.support import git, make_fixture


class GitDiffTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.fixture = make_fixture(Path(self._td.name))
        self.repo = self.fixture.repo_dir

    def test_diff_is_stable_across_invocations(self):
        first = canonical_diff_bytes(self.repo, self.fixture.base_sha, self.fixture.work_head)
        second = canonical_diff_bytes(self.repo, self.fixture.base_sha, self.fixture.work_head)
        self.assertEqual(first, second)
        self.assertIn(b"src/app.py", first)
        self.assertTrue(first.startswith(b"diff --git a/src/app.py b/src/app.py"))

    def test_diff_hash_ignores_base_branch_drift(self):
        # base...head diffs from the merge-base, so commits landing on main
        # after the PR forked must not change the hash.
        before = canonical_diff_hash(self.repo, self.fixture.base_sha, self.fixture.work_head)
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "unrelated.txt").write_text("drift\n", encoding="utf-8")
        git(self.repo, "add", "unrelated.txt")
        git(self.repo, "commit", "-qm", "unrelated mainline commit")
        git(self.repo, "checkout", "-q", "feat")
        after = canonical_diff_hash(self.repo, "main", self.fixture.work_head)
        self.assertEqual(before, after)

    def test_diff_uses_full_index_and_pinned_prefixes(self):
        diff = canonical_diff_bytes(self.repo, self.fixture.base_sha, self.fixture.work_head)
        index_line = next(l for l in diff.splitlines() if l.startswith(b"index "))
        blob_ids = index_line.split()[1]
        left, _, right = blob_ids.partition(b"..")
        self.assertEqual(len(left), 40, "abbreviated blob ids would vary with repo size")
        self.assertEqual(len(right), 40)

    def test_changed_paths_three_dot(self):
        paths = changed_paths(self.repo, self.fixture.base_sha, self.fixture.work_head)
        self.assertEqual(paths, ["src/app.py"])

    def test_rev_parse_and_ancestry(self):
        head = rev_parse(self.repo, "HEAD")
        self.assertEqual(head, self.fixture.work_head)
        self.assertTrue(is_ancestor(self.repo, self.fixture.base_sha, head))
        self.assertFalse(is_ancestor(self.repo, head, self.fixture.base_sha))


if __name__ == "__main__":
    unittest.main()
