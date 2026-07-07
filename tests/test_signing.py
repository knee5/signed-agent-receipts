import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from agent_receipts.__main__ import main
from agent_receipts.jsonl import read_jsonl, write_jsonl
from agent_receipts.records import make_record
from agent_receipts.render import render_markdown
from agent_receipts.signing import sign_record, verify_record


class SigningTests(unittest.TestCase):
    def test_sign_and_verify_record(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "ed25519_private.pem"
            record = make_record(source_runtime="test", source_path="examples/source/demo.jsonl", title="Demo")

            signed = sign_record(record, key_path=key_path)
            result = verify_record(signed)

            self.assertTrue(result.valid)
            self.assertEqual(result.status, "valid")
            self.assertEqual(signed["signature"]["algorithm"], "ed25519")
            self.assertTrue(key_path.exists())

    def test_verify_detects_tampered_record(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "ed25519_private.pem"
            record = make_record(source_runtime="test", source_path="examples/source/demo.jsonl", title="Demo")
            signed = sign_record(record, key_path=key_path)

            signed["title"] = "Tampered"
            result = verify_record(signed)

            self.assertFalse(result.valid)
            self.assertEqual(result.status, "tampered")

    def test_verify_detects_invalid_unsigned_record(self):
        record = make_record(source_runtime="test", source_path="examples/source/demo.jsonl", title="Demo")
        result = verify_record(record)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "invalid")

    def test_write_jsonl_signs_records(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "ed25519_private.pem"
            out = Path(td) / "runs.jsonl"
            record = make_record(source_runtime="test", source_path="examples/source/demo.jsonl", title="Demo")

            count = write_jsonl([record], out, key_path=key_path)
            loaded = read_jsonl(out)

            self.assertEqual(count, 1)
            self.assertIn("signature", loaded[0])
            self.assertTrue(verify_record(loaded[0]).valid)

    def test_markdown_contains_signature_section(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "ed25519_private.pem"
            record = make_record(source_runtime="test", source_path="examples/source/demo.jsonl", title="Demo")
            signed = sign_record(record, key_path=key_path)

            markdown = render_markdown(signed)

            self.assertIn("## Signature", markdown)
            self.assertIn("ed25519", markdown)
            self.assertIn("canonical-json", markdown)

    def test_verify_cli_returns_nonzero_for_tampered_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "ed25519_private.pem"
            out = Path(td) / "runs.jsonl"
            record = make_record(source_runtime="test", source_path="examples/source/demo.jsonl", title="Demo")
            write_jsonl([record], out, key_path=key_path)
            loaded = read_jsonl(out)
            loaded[0]["title"] = "Tampered"
            out.write_text(json.dumps(loaded[0], sort_keys=True) + "\n", encoding="utf-8")

            with redirect_stdout(StringIO()):
                code = main(["verify", "--jsonl", str(out)])

            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
