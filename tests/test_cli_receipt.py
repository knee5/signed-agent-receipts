import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_receipts.__main__ import main
from agent_receipts.receipt import load_receipt

from tests.support import REQUEST_TEXT, make_fixture


class ReceiptCliTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.fixture = make_fixture(Path(self._td.name))

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_receipt_then_verify_receipt_roundtrip(self):
        repo = str(self.fixture.repo_dir)
        task_file = Path(self._td.name) / "task.md"
        task_file.write_text(REQUEST_TEXT, encoding="utf-8")

        code, out = self._run(
            [
                "receipt",
                "--request-file", str(task_file),
                "--repo", "demo/fixture-repo",
                "--pr", "7",
                "--base", "main",
                "--head", "feat",
                "--repo-dir", repo,
                "--claim", "cli smoke claim",
                "--signing-key", str(self.fixture.key_path),
            ]
        )
        self.assertEqual(code, 0, out)
        receipt_path = self.fixture.repo_dir / "receipts" / "pr-7.receipt.json"
        self.assertTrue(receipt_path.exists())
        receipt = load_receipt(receipt_path)
        self.assertEqual(receipt["deliverable"]["pr_number"], 7)
        self.assertEqual(receipt["evidence"][0]["claim"], "cli smoke claim")

        code, out = self._run(
            ["verify-receipt", "--receipt", str(receipt_path), "--repo-dir", repo]
        )
        self.assertEqual(code, 0, out)
        self.assertIn("recomputed and matched", out)

    def test_verify_receipt_fails_on_tampered_file(self):
        repo = str(self.fixture.repo_dir)
        task_file = Path(self._td.name) / "task.md"
        task_file.write_text(REQUEST_TEXT, encoding="utf-8")
        code, _ = self._run(
            [
                "receipt",
                "--request-file", str(task_file),
                "--repo", "demo/fixture-repo",
                "--pr", "7",
                "--base", "main",
                "--head", "feat",
                "--repo-dir", repo,
                "--signing-key", str(self.fixture.key_path),
            ]
        )
        self.assertEqual(code, 0)
        receipt_path = self.fixture.repo_dir / "receipts" / "pr-7.receipt.json"
        doc = json.loads(receipt_path.read_text())
        doc["deliverable"]["pr_number"] = 8
        receipt_path.write_text(json.dumps(doc), encoding="utf-8")

        code, out = self._run(
            ["verify-receipt", "--receipt", str(receipt_path), "--repo-dir", repo]
        )
        self.assertEqual(code, 1)
        self.assertIn("tampered", out)


if __name__ == "__main__":
    unittest.main()
