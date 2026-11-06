"""Side-effecting actions triggered by one confirmed event."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from factoryguard.domain.models import Event
from factoryguard.net import close_http_error


def execute_http_action(event: Event, *, url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """POST the event to an external HTTP endpoint without affecting delivery."""
    if not url.lower().startswith(("http://", "https://")):
        return False, "HTTP action URL must use HTTP or HTTPS"
    payload = {
        "event_id": event.id,
        "dedup_key": event.dedup_key,
        "rule_id": event.rule_id,
        "stream_id": event.stream_id or event.channel_id,
        "level": event.level,
        "occurred_at": event.occurred_at.isoformat(),
        "target": dict(event.target),
        "status": event.status.value,
        "snapshot_uri": event.snapshot_uri,
        "clip_uri": event.clip_uri,
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": event.dedup_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
        if 200 <= status < 300:
            return True, f"HTTP {status}"
        return False, f"HTTP action returned {status}"
    except (OSError, URLError, HTTPError) as exc:
        close_http_error(exc)
        return False, str(exc)[:500]


__all__ = ["execute_http_action"]
