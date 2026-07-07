import unittest

from agent_receipts.utils import extract_urls, redact_text, truncate_text


class UtilsTests(unittest.TestCase):
    def test_redacts_common_secret_shapes(self):
        text = "token=abc123456789 password: hunter2 Authorization=Bearer abcdefghijklmnop https://x.test?a=1&api_key=secret"
        redacted = redact_text(text)
        self.assertNotIn("abc123456789", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("api_key=secret", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_truncates_after_redaction(self):
        out = truncate_text("secret=abc123456789 " + ("x" * 200), limit=60)
        self.assertLessEqual(len(out), 80)
        self.assertIn("[truncated]", out)
        self.assertNotIn("abc123456789", out)

    def test_extract_urls_redacts_query_secret(self):
        urls = extract_urls("See https://example.test/path?token=secret&ok=1")
        self.assertEqual(urls, ["https://example.test/path?token=[REDACTED]&ok=1"])


if __name__ == "__main__":
    unittest.main()
