import json
import os
import unittest
from unittest.mock import patch

from agent_receipts.analytics import PostHogAnalytics, capture_event


class AnalyticsTests(unittest.TestCase):
    def test_capture_event_noops_without_posthog_key(self):
        calls = []
        analytics = PostHogAnalytics(env={}, opener=lambda request, timeout: calls.append(request))

        self.assertFalse(analytics.enabled)
        self.assertFalse(analytics.capture("agent_receipts_test", {"ok": True}))
        self.assertEqual(calls, [])

    def test_capture_event_posts_when_posthog_key_exists(self):
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"{}"

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response()

        analytics = PostHogAnalytics(
            env={
                "POSTHOG_KEY": "phc_test",
                "POSTHOG_HOST": "https://posthog.example.test",
                "AGENT_RECEIPTS_ANALYTICS_DISTINCT_ID": "unit-test-runner",
            },
            opener=opener,
        )

        self.assertTrue(analytics.enabled)
        self.assertTrue(analytics.capture("agent_receipts_test", {"ok": True}))
        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(timeout, 2.0)
        self.assertEqual(request.full_url, "https://posthog.example.test/capture/")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["api_key"], "phc_test")
        self.assertEqual(payload["event"], "agent_receipts_test")
        self.assertEqual(payload["distinct_id"], "unit-test-runner")
        self.assertEqual(payload["properties"]["ok"], True)
        self.assertEqual(payload["properties"]["app"], "signed-agent-receipts")

    def test_capture_event_helper_uses_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(capture_event("agent_receipts_test"))


if __name__ == "__main__":
    unittest.main()
