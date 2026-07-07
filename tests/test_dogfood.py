import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_receipts.__main__ import main
from agent_receipts.jsonl import read_jsonl
from agent_receipts.normalizers import normalize_all


class DogfoodTests(unittest.TestCase):
    def test_normalize_codex_rollout_session_as_single_record(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as workspace:
            session_dir = Path(home) / ".codex" / "sessions" / "2026" / "07"
            session_dir.mkdir(parents=True)
            rollout = session_dir / "rollout-test.jsonl"
            lines = [
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "type": "session_meta",
                    "payload": {"session_id": "session-test", "cwd": str(workspace)},
                },
                {
                    "timestamp": "2026-07-01T12:00:01Z",
                    "type": "user_message",
                    "payload": {"message": "Aggregate Codex rollout receipts\nKeep it stdlib-only."},
                },
                {
                    "timestamp": "2026-07-01T12:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "python3 -m unittest"}),
                    },
                },
                {
                    "timestamp": "2026-07-01T12:00:03Z",
                    "type": "response_item",
                    "payload": {"type": "function_call_output", "call_id": "call-1", "output": "OK"},
                },
                {
                    "timestamp": "2026-07-01T12:00:04Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                },
            ]
            rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

            records = normalize_all(limit=10, home=Path(home), workspace=Path(workspace), evidence_roots=[Path(workspace)])

            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["source_runtime"], "codex")
            self.assertEqual(record["session_id"], "session-test")
            self.assertEqual(record["title"], "Aggregate Codex rollout receipts")
            self.assertEqual(record["duration_ms"], 4000)
            self.assertEqual(len(record["tool_calls"]), 1)
            self.assertEqual(record["tool_calls"][0]["name"], "exec_command")
            self.assertEqual(record["tool_calls"][0]["status"], "ok")
            self.assertEqual(record["costs"]["tokens_in"], 10)
            self.assertEqual(record["costs"]["tokens_out"], 20)
            self.assertEqual(record["costs"]["total"], 30)
            self.assertEqual(record["raw_refs"], [str(rollout)])

    def test_codex_rollout_extracts_git_diff_from_exec_output(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as workspace:
            session_dir = Path(home) / ".codex" / "sessions" / "2026" / "07"
            session_dir.mkdir(parents=True)
            rollout = session_dir / "rollout-diff.jsonl"
            git_diff = """diff --git a/agent_receipts/evidence.py b/agent_receipts/evidence.py
index 1111111..2222222 100644
--- a/agent_receipts/evidence.py
+++ b/agent_receipts/evidence.py
@@ -1,2 +1,3 @@
 old line
+new line
diff --git a/agent_receipts/render.py b/agent_receipts/render.py
index 3333333..4444444 100644
--- a/agent_receipts/render.py
+++ b/agent_receipts/render.py
@@ -9,3 +9,2 @@
 keep
-delete me
"""
            lines = [
                {"timestamp": "2026-07-01T12:00:00Z", "type": "session_meta", "payload": {"session_id": "session-diff", "cwd": str(workspace)}},
                {"timestamp": "2026-07-01T12:00:01Z", "type": "user_message", "payload": {"message": "Fix receipt diff extraction"}},
                {"timestamp": "2026-07-01T12:00:02Z", "type": "response_item", "payload": {"type": "function_call", "call_id": "call-diff", "name": "exec_command", "arguments": json.dumps({"cmd": "git diff"})}},
                {"timestamp": "2026-07-01T12:00:03Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-diff", "output": git_diff}},
            ]
            rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

            records = normalize_all(limit=10, home=Path(home), workspace=Path(workspace), evidence_roots=[Path(workspace)])

            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["file_diffs"],
                [
                    {"path": "agent_receipts/evidence.py", "status": "modified", "additions": 1, "deletions": 0, "provenance": {"source": "tool_call_id", "ref": "call-diff", "heuristic": False}},
                    {"path": "agent_receipts/render.py", "status": "modified", "additions": 0, "deletions": 1, "provenance": {"source": "tool_call_id", "ref": "call-diff", "heuristic": False}},
                ],
            )

    def test_codex_rollout_extracts_patch_apply_event_diffs(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as workspace:
            session_dir = Path(home) / ".codex" / "sessions" / "2026" / "07"
            session_dir.mkdir(parents=True)
            rollout = session_dir / "rollout-patch.jsonl"
            changed_path = str(Path(workspace) / "agent_receipts" / "evidence.py")
            lines = [
                {"timestamp": "2026-07-01T12:00:00Z", "type": "session_meta", "payload": {"session_id": "session-patch", "cwd": str(workspace)}},
                {"timestamp": "2026-07-01T12:00:01Z", "type": "user_message", "payload": {"message": "Fix provenance rendering"}},
                {
                    "timestamp": "2026-07-01T12:00:02Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "patch_apply_end",
                        "call_id": "call-patch",
                        "changes": {
                            changed_path: {
                                "type": "update",
                                "unified_diff": "@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+extra\n",
                            }
                        },
                    },
                },
            ]
            rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

            records = normalize_all(limit=10, home=Path(home), workspace=Path(workspace), evidence_roots=[Path(workspace)])

            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["file_diffs"],
                [
                    {
                        "path": changed_path,
                        "status": "modified",
                        "additions": 2,
                        "deletions": 1,
                        "provenance": {"source": "patch_apply_end", "ref": "call-patch", "heuristic": False},
                    }
                ],
            )

    def test_normalize_temp_codex_fixture_does_not_backfill_shared_screenshots(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as workspace:
            codex = Path(home) / ".codex"
            codex.mkdir()
            screenshots = Path(home) / ".hermes" / "cache" / "screenshots"
            screenshots.mkdir(parents=True)
            screenshot = screenshots / "browser-screenshot.png"
            screenshot.write_bytes(b"fake png bytes")
            fixture = {
                "run_id": "fixture-run",
                "session_id": "session-1",
                "title": "Fixture run",
                "prompt": "Review https://example.test?token=secret",
                "tool_calls": [{"name": "exec_command", "args": "date", "status": "ok"}],
                "file_diffs": [{"path": "x.py", "status": "modified", "additions": 1, "deletions": 0}],
                "usage": {"tokens_in": 10, "tokens_out": 20},
            }
            (codex / "history.jsonl").write_text(json.dumps(fixture) + "\n", encoding="utf-8")
            records = normalize_all(limit=10, home=Path(home), workspace=Path(workspace), evidence_roots=[Path(workspace)])
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["run_id"], "fixture-run")
            self.assertIn("https://example.test?token=[REDACTED]", records[0]["urls"][0])
            self.assertTrue(records[0]["evidence"])
            evidence_types = {item["type"] for item in records[0]["evidence"]}
            self.assertIn("url", evidence_types)
            self.assertNotIn("screenshot", evidence_types)
            self.assertFalse(any(item.get("path") == str(screenshot) for item in records[0]["evidence"]))
            self.assertEqual(records[0]["evidence"][0]["provenance"], {"source": "record_url", "ref": "fixture-run", "heuristic": False})

    def test_dogfood_cli_temp_fixture(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as out:
            codex = Path(home) / ".codex"
            codex.mkdir()
            (codex / "history.jsonl").write_text(
                json.dumps({"title": "CLI run", "prompt": "Do work", "tool_calls": [{"name": "tool", "args": "x"}]})
                + "\n",
                encoding="utf-8",
            )
            signing_key = str(Path(out) / "ed25519_private.pem")
            with mock.patch.dict(os.environ, {"AGENT_RECEIPTS_HOME": home, "AGENT_RECEIPTS_WORKSPACE": out, "AGENT_RECEIPTS_SIGNING_KEY": signing_key}):
                code = main(["dogfood", "--out-dir", out, "--limit", "5"])
            self.assertEqual(code, 0)
            jsonl_path = Path(out) / "agent_run.jsonl"
            self.assertTrue(jsonl_path.exists())
            records = read_jsonl(jsonl_path)
            self.assertEqual(len(records), 1)
            receipts = list((Path(out) / "receipts").glob("*.md"))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(records[0]["receipt_path"], str(receipts[0]))
            self.assertTrue(Path(records[0]["receipt_path"]).exists())


if __name__ == "__main__":
    unittest.main()
