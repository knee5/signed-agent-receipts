import tempfile
import unittest
from pathlib import Path

from agent_receipts.ci_gate import gate, is_agent_actor
from agent_receipts.__main__ import main


class CiGateTests(unittest.TestCase):
    def test_agent_actor_requires_receipt(self):
        self.assertTrue(is_agent_actor("hermes-agent"))
        ok, message = gate(receipt_globs=["/tmp/definitely-missing/*.jsonl"], require_for_actor="hermes-agent")
        self.assertFalse(ok)
        self.assertIn("requires", message)

    def test_non_agent_actor_skips(self):
        ok, message = gate(receipt_globs=["/tmp/definitely-missing/*.jsonl"], require_for_actor="human-reviewer")
        self.assertTrue(ok)
        self.assertIn("skipped", message)

    def test_gate_accepts_self_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = Path(td) / "receipt.jsonl"
            code = main(["self-receipt", "--out", str(receipt), "--summary", "test receipt"])
            self.assertEqual(code, 0)
            ok, message = gate(receipt_globs=[str(receipt)], require_for_actor="codex-agent")
            self.assertTrue(ok, message)


if __name__ == "__main__":
    unittest.main()
