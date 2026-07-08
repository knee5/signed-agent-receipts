import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_receipts.__main__ import main


class InstallAndMcpTests(unittest.TestCase):
    def test_self_receipt_cli_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "self.jsonl"
            self.assertEqual(main(["self-receipt", "--out", str(out), "--title", "Smoke"]), 0)
            self.assertEqual(main(["verify", "--jsonl", str(out)]), 0)

    def test_mcp_create_and_verify_tool(self):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "mcp.jsonl")
            proc = subprocess.Popen(
                [sys.executable, "-m", "agent_receipts.mcp_server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "create_signed_receipt", "arguments": {"out": out, "summary": "mcp smoke"}}}) + "\n")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "verify_receipt_jsonl", "arguments": {"jsonl": out}}}) + "\n")
            proc.stdin.close()
            lines = [json.loads(proc.stdout.readline()) for _ in range(3)]
            proc.wait(timeout=5)
            self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "signed-agent-receipts")
            self.assertIn("verify=valid", lines[1]["result"]["content"][0]["text"])
            self.assertIn("verified 1/1", lines[2]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
