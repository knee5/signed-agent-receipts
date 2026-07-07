import tempfile
import unittest
from pathlib import Path

from agent_receipts.records import make_record
from agent_receipts.render import render_markdown, render_receipt


class RenderTests(unittest.TestCase):
    def test_markdown_contains_required_sections(self):
        record = make_record(source_runtime="codex", source_path="/tmp/source.jsonl", title="Run title")
        record["inputs"].append({"type": "prompt", "content": "Build it"})
        record["tool_calls"].append(
            {
                "name": "exec_command",
                "args_summary": "python -m unittest",
                "status": "ok",
                "started_at": None,
                "ended_at": None,
                "elapsed_ms": 1200,
                "artifacts": [],
            }
        )
        record["file_diffs"].append({"path": "README.md", "status": "modified", "additions": 2, "deletions": 1})
        record["urls"].append("https://example.test")
        record["evidence"].append({"type": "url", "url": "https://example.test", "caption": "example", "provenance": {"source": "record_url", "ref": record["run_id"], "heuristic": False}})
        markdown = render_markdown(record)
        self.assertIn("## Approve / Reject", markdown)
        self.assertIn("## Tool Calls", markdown)
        self.assertIn("README.md", markdown)
        self.assertIn("https://example.test", markdown)
        self.assertIn("provenance: record_url", markdown)

    def test_render_receipt_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            record = make_record(source_runtime="codex", source_path="/tmp/source.jsonl", title="Run title")
            path = render_receipt(record, td, key_path=Path(td) / "ed25519_private.pem")
            self.assertTrue(path.exists())
            self.assertEqual(record["receipt_path"], str(path))


if __name__ == "__main__":
    unittest.main()
