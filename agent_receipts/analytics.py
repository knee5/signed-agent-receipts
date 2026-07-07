"""Environment-gated PostHog analytics for signed-agent-receipts.

This module is deliberately dependency-free and safe-by-default: if POSTHOG_KEY
is absent, all calls are no-ops. It exists so deployments become instrument-ready
as soon as a project-scoped PostHog key is provided.
"""

from __future__ import annotations

import json
import os
import platform
from typing import Any, Callable, Mapping
from urllib import request as urllib_request
from urllib.error import URLError

DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
DEFAULT_TIMEOUT_SECONDS = 2.0

Opener = Callable[..., Any]


class PostHogAnalytics:
    """Tiny PostHog capture client guarded by POSTHOG_KEY."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        opener: Opener | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.env = env if env is not None else os.environ
        self.api_key = self.env.get("POSTHOG_KEY", "").strip()
        self.host = self.env.get("POSTHOG_HOST", DEFAULT_POSTHOG_HOST).rstrip("/")
        self.distinct_id = self.env.get(
            "AGENT_RECEIPTS_ANALYTICS_DISTINCT_ID",
            f"signed-agent-receipts-{platform.node() or 'local'}",
        )
        self.opener = opener if opener is not None else urllib_request.urlopen
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def capture(self, event: str, properties: Mapping[str, Any] | None = None) -> bool:
        """Send an event to PostHog; return False when disabled or failed.

        Analytics must never break product behavior, so network and serialization
        errors are swallowed. Callers can use the boolean in tests/diagnostics.
        """
        if not self.enabled:
            return False

        payload = {
            "api_key": self.api_key,
            "event": event,
            "distinct_id": self.distinct_id,
            "properties": {
                "app": "signed-agent-receipts",
                **dict(properties or {}),
            },
        }
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        req = urllib_request.Request(
            f"{self.host}/capture/",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(req, timeout=self.timeout) as response:
                response.read()
            return True
        except (OSError, TypeError, ValueError, URLError):
            return False


def capture_event(event: str, properties: Mapping[str, Any] | None = None) -> bool:
    """Capture an event using process environment configuration."""
    return PostHogAnalytics().capture(event, properties)
