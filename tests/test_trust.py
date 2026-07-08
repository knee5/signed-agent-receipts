import tempfile
import unittest
from pathlib import Path

from agent_receipts.receipt import build_receipt, hash_request_text
from agent_receipts.signing import public_key_material
from agent_receipts.trust import TrustConfigError, check_signer, parse_trusted_signers

BASE = "a" * 40
HEAD = "b" * 40
DIFF = "sha256:" + "c" * 64


def _receipt(key_path, **overrides):
    kwargs = dict(
        request_hash=hash_request_text("task"),
        repo="owner/repo",
        pr_number=1,
        base_sha=BASE,
        head_sha=HEAD,
        diff_hash=DIFF,
        key_path=key_path,
    )
    kwargs.update(overrides)
    return build_receipt(**kwargs)


def _anchor_yaml(key_id, public_key, *, valid_from="2020-01-01T00:00:00+00:00", valid_until=None, revoked=()):
    until = f'\n    valid_until: "{valid_until}"' if valid_until else ""
    revoked_block = "revoked_keys:\n" + (
        "".join(f'  - key_id: "{k}"\n    reason: test\n' for k in revoked) if revoked else "  []\n"
    )
    return (
        "version: 1\n"
        "signers:\n"
        "  - name: tester\n"
        f'    key_id: "{key_id}"\n'
        f'    public_key: "{public_key}"\n'
        f'    valid_from: "{valid_from}"{until}\n'
        + revoked_block
    )


class TrustTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.key_path = Path(self._td.name) / "key.pem"
        self.public_key, self.key_id = public_key_material(self.key_path)

    def test_trusted_signer_passes(self):
        anchor = parse_trusted_signers(_anchor_yaml(self.key_id, self.public_key))
        receipt = _receipt(self.key_path)
        self.assertEqual(check_signer(anchor, receipt), [])

    def test_unknown_key_rejected(self):
        other_key = Path(self._td.name) / "other.pem"
        other_public, other_id = public_key_material(other_key)
        anchor = parse_trusted_signers(_anchor_yaml(other_id, other_public))
        problems = check_signer(anchor, _receipt(self.key_path))
        self.assertTrue(any("not in trusted_signers" in p for p in problems))

    def test_revoked_key_rejected_even_if_listed_as_signer(self):
        anchor = parse_trusted_signers(
            _anchor_yaml(self.key_id, self.public_key, revoked=[self.key_id])
        )
        problems = check_signer(anchor, _receipt(self.key_path))
        self.assertTrue(any("REVOKED" in p for p in problems))

    def test_pinned_public_key_mismatch_rejected(self):
        other_public, _ = public_key_material(Path(self._td.name) / "other.pem")
        anchor = parse_trusted_signers(_anchor_yaml(self.key_id, other_public))
        problems = check_signer(anchor, _receipt(self.key_path))
        self.assertTrue(any("pinned" in p for p in problems))

    def test_issued_outside_granted_window_rejected(self):
        anchor = parse_trusted_signers(
            _anchor_yaml(
                self.key_id,
                self.public_key,
                valid_from="2019-01-01T00:00:00+00:00",
                valid_until="2019-12-31T00:00:00+00:00",
            )
        )
        problems = check_signer(anchor, _receipt(self.key_path))
        self.assertTrue(any("after trusted window" in p for p in problems))

    def test_issued_outside_claimed_key_window_rejected(self):
        anchor = parse_trusted_signers(_anchor_yaml(self.key_id, self.public_key))
        receipt = _receipt(self.key_path, issued_at="2019-06-01T00:00:00+00:00")
        problems = check_signer(anchor, receipt)
        self.assertTrue(any("claimed valid_from" in p for p in problems))

    def test_malformed_yaml_fails_closed(self):
        with self.assertRaises(TrustConfigError):
            parse_trusted_signers("version: 1\nsigners: not-a-list\n")
        with self.assertRaises(TrustConfigError):
            parse_trusted_signers("version: 2\nsigners: []\n")
        with self.assertRaises(TrustConfigError):
            parse_trusted_signers("version: 1\nsigners:\n  - name: x\n")  # missing key material


if __name__ == "__main__":
    unittest.main()
