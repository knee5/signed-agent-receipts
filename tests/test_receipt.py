import json
import tempfile
import unittest
from pathlib import Path

from agent_receipts.receipt import (
    RECEIPT_SCHEMA_VERSION,
    build_receipt,
    hash_request_text,
    key_validity,
    load_receipt,
    validate_receipt,
    write_receipt,
)
from agent_receipts.signing import verify_record

BASE = "a" * 40
HEAD = "b" * 40
DIFF = "sha256:" + "c" * 64


def _build(key_path, **overrides):
    kwargs = dict(
        request_hash=hash_request_text("do the task"),
        repo="owner/repo",
        pr_number=7,
        base_sha=BASE,
        head_sha=HEAD,
        diff_hash=DIFF,
        evidence=[{"method": "self_claimed", "claim": "tests pass"}],
        key_path=key_path,
    )
    kwargs.update(overrides)
    return build_receipt(**kwargs)


class ReceiptTests(unittest.TestCase):
    def test_request_hash_is_exact_bytes(self):
        self.assertEqual(
            hash_request_text("abc"),
            "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertNotEqual(hash_request_text("abc"), hash_request_text("abc "))

    def test_build_receipt_signs_and_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = _build(Path(td) / "key.pem")
            self.assertEqual(receipt["schema_version"], RECEIPT_SCHEMA_VERSION)
            self.assertEqual(validate_receipt(receipt), [])
            self.assertTrue(verify_record(receipt).valid)
            self.assertEqual(receipt["signature"]["key_id"], receipt["key"]["key_id"])
            self.assertEqual(receipt["audience"], "github:owner/repo")
            # Reserved sockets are present but unwired.
            self.assertIsNone(receipt["authorization"])
            self.assertEqual(receipt["signers"], [])

    def test_tampering_with_binding_breaks_signature(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = _build(Path(td) / "key.pem")
            for field, value in [
                ("request", {**receipt["request"], "hash": "sha256:" + "d" * 64}),
                ("deliverable", {**receipt["deliverable"], "pr_number": 8}),
                ("deliverable", {**receipt["deliverable"], "diff_hash": "sha256:" + "e" * 64}),
                ("nonce", "f" * 32),
            ]:
                tampered = json.loads(json.dumps(receipt))
                tampered[field] = value
                result = verify_record(tampered)
                self.assertFalse(result.valid, f"tampering {field} must break the signature")
                self.assertEqual(result.status, "tampered")

    def test_build_refuses_structurally_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                _build(Path(td) / "key.pem", base_sha="not-a-sha")
            with self.assertRaises(ValueError):
                _build(Path(td) / "key.pem", evidence=[{"method": "made_up"}])
            with self.assertRaises(ValueError):
                _build(
                    Path(td) / "key.pem",
                    evidence=[{"method": "re_executable", "cmd": "pytest"}],  # missing expected_exit_code
                )

    def test_validate_catches_key_id_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = _build(Path(td) / "key.pem")
            receipt["key"]["key_id"] = "sha256:" + "0" * 64
            self.assertIn("signature.key_id does not match key.key_id", validate_receipt(receipt))

    def test_key_validity_sidecar_is_stable(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "key.pem"
            _build(key_path)
            first = key_validity(key_path)
            second = key_validity(key_path)
            self.assertEqual(first, second)
            self.assertTrue((Path(td) / "key.pem.meta.json").exists())

    def test_write_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            receipt = _build(Path(td) / "key.pem")
            path = write_receipt(receipt, Path(td) / "out" / "r.receipt.json")
            loaded = load_receipt(path)
            self.assertEqual(loaded, receipt)
            self.assertTrue(verify_record(loaded).valid)

    def test_emitted_receipt_validates_against_json_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "schema" / "receipt.schema.json").read_text()
        )
        with tempfile.TemporaryDirectory() as td:
            receipt = _build(
                Path(td) / "key.pem",
                evidence=[
                    {"method": "self_claimed", "claim": "tests pass"},
                    {"method": "content_addressed", "path": "src/app.py", "sha256": "sha256:" + "a" * 64},
                    {"method": "ci_attested", "provider": "github", "check_name": "python-ci"},
                    {"method": "re_executable", "cmd": "python -m unittest", "expected_exit_code": 0},
                ],
            )
            jsonschema.validate(receipt, schema)


if __name__ == "__main__":
    unittest.main()
