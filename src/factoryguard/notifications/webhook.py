from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from factoryguard.domain.models import Event
from factoryguard.net import close_http_error


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    url: str
    body: bytes
    headers: Mapping[str, str]
    dedup_key: str


def build_webhook_request(
    event: Event,
    *,
    url: str,
    secret: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> WebhookRequest:
    """Build the HTTPS request described in design section 17.4.

    The signature is HMAC-SHA256(body + timestamp + nonce, secret). Callers
    should send the returned body and headers as-is and keep ``dedup_key`` as
    the idempotency key for retries.
    """
    if not url.startswith("https://"):
        raise ValueError("webhook URL must use HTTPS")
    if not secret:
        raise ValueError("webhook secret is required")
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": event.id,
        "dedup_key": event.dedup_key,
        "level": event.level,
        "channel": event.channel_id,
        "stream_id": event.stream_id or event.channel_id,
        "site_id": event.site_id,
        "area_id": event.area_id,
        "camera_id": event.camera_id,
        "entity": {
            "site_id": event.site_id,
            "area_id": event.area_id,
            "camera_id": event.camera_id,
            "stream_id": event.stream_id or event.channel_id,
        },
        "rule_id": event.rule_id,
        "target": dict(event.target),
        "occurred_at": event.occurred_at.isoformat(),
        "nvr_time": event.nvr_time.isoformat() if event.nvr_time else None,
        "clock_delta_ms": event.clock_delta_ms,
        "snapshot_uri": event.snapshot_uri,
        "clip_uri": event.clip_uri,
        "clip_status": event.clip_status,
        "status": event.status.value,
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    nonce_text = nonce or secrets.token_hex(16)
    signature = hmac.new(
        secret.encode("utf-8"),
        body + timestamp_text.encode("ascii") + nonce_text.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return WebhookRequest(
        url=url,
        body=body,
        dedup_key=event.dedup_key,
        headers={
            "Content-Type": "application/json",
            "timestamp": timestamp_text,
            "nonce": nonce_text,
            "signature": signature,
            "Idempotency-Key": event.dedup_key,
        },
    )


class WebhookNotifier:
    """Synchronous HTTPS sender; retries belong to the Outbox worker."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def send(self, request: WebhookRequest) -> tuple[bool, str]:
        http_request = Request(
            request.url, data=request.body, headers=dict(request.headers), method="POST"
        )
        try:
            with urlopen(http_request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                return 200 <= status < 300, f"HTTP {status}"
        except (OSError, URLError) as exc:
            close_http_error(exc)
            return False, str(exc)
