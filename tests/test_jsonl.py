import tempfile
import unittest
from pathlib import Path

from agent_receipts.jsonl import read_jsonl, write_jsonl
from agent_receipts.records import make_record


class JsonlTests(unittest.TestCase):
    def test_write_and_read_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "runs.jsonl"
            key_path = Path(td) / "ed25519_private.pem"
            record = make_record(source_runtime="test", source_path=Path(td) / "source.log", title="Example")
            count = write_jsonl([record], path, key_path=key_path)
            self.assertEqual(count, 1)
            loaded = read_jsonl(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["schema_version"], "agent-run-ledger.v1")
            self.assertEqual(loaded[0]["title"], "Example")


if __name__ == "__main__":
    unittest.main()
