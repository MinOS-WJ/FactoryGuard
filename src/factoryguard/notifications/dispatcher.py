"""Transactional Notification Outbox delivery."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from factoryguard.domain.models import Event, EventStatus
from factoryguard.notifications.adapters import NotificationAdapter, WebhookAdapter
from factoryguard.notifications.webhook import WebhookNotifier
from factoryguard.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class DispatchReport:
    claimed: int
    sent: int
    failed: int
    retried: int


class OutboxDispatcher:
    """Claim, deliver and finalize outbox rows with retry-safe leases."""

    def __init__(
        self,
        *,
        webhook_url: str = "",
        webhook_secret: str = "",
        notifier: WebhookNotifier | None = None,
        adapters: Mapping[str, NotificationAdapter] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.notifier = notifier or WebhookNotifier()
        self.adapters: dict[str, NotificationAdapter] = dict(adapters or {})
        if self.webhook_url.strip():
            self.adapters.setdefault(
                "webhook",
                WebhookAdapter(
                    url=self.webhook_url, secret=self.webhook_secret, notifier=self.notifier
                ),
            )
        self.clock = clock or (lambda: datetime.now(UTC))

    def dispatch_once(self, store: SQLiteStore, *, limit: int = 100) -> DispatchReport:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("dispatcher clock must return an aware datetime")
        store.recover_expired_outbox(now=now)
        claimed_rows = store.claim_pending_outbox(limit=limit, now=now)
        sent = failed = retried = 0
        for row in claimed_rows:
            ok, detail = self._deliver_row(store, row)
            if ok:
                store.mark_outbox_sent(str(row["id"]), sent_at=now)
                sent += 1
                continue
            attempts = int(row["attempts"] or 1)
            retry_at = now + timedelta(seconds=min(300, 2 ** min(attempts, 8)))
            if store.mark_outbox_failed(str(row["id"]), detail, retry_at=retry_at):
                retried += 1
            else:
                failed += 1
        return DispatchReport(len(claimed_rows), sent, failed, retried)

    def _deliver_row(self, store: SQLiteStore, row: object) -> tuple[bool, str]:
        outbox = row  # sqlite3.Row supports mapping access and keeps this helper testable.
        event_row = store.get_event(str(outbox["event_id"]))  # type: ignore[index]
        if event_row is None:
            return False, "event record is missing"
        try:
            event = _event_from_row(event_row)
            channel = str(outbox["channel"])  # type: ignore[index]
            adapter = self.adapters.get(channel)
            if adapter is None:
                return False, f"unsupported notification channel: {channel}"
            return adapter.send(event, outbox=outbox)  # type: ignore[arg-type]
        except (TypeError, ValueError, KeyError) as exc:
            return False, str(exc)


def _event_from_row(row: object) -> Event:
    payload = json.loads(row["payload"] or "{}")  # type: ignore[index]
    occurred_at = datetime.fromisoformat(str(row["occurred_at"]))  # type: ignore[index]
    nvr_value = row["nvr_time"]  # type: ignore[index]
    nvr_time = datetime.fromisoformat(str(nvr_value)) if nvr_value else None
    status = EventStatus(str(row["status"]))  # type: ignore[index]
    return Event(
        id=str(row["id"]),  # type: ignore[index]
        dedup_key=str(row["dedup_key"]),  # type: ignore[index]
        rule_id=str(row["rule_id"]),  # type: ignore[index]
        channel_id=str(row["channel_id"]),  # type: ignore[index]
        stream_id=str(row["stream_id"]) if row["stream_id"] else None,  # type: ignore[index]
        site_id=str(row["site_id"]) if row["site_id"] else None,  # type: ignore[index]
        area_id=str(row["area_id"]) if row["area_id"] else None,  # type: ignore[index]
        camera_id=str(row["camera_id"]) if row["camera_id"] else None,  # type: ignore[index]
        level=str(row["level"]),  # type: ignore[index]
        occurred_at=occurred_at,
        nvr_time=nvr_time,
        clock_delta_ms=row["clock_delta_ms"],  # type: ignore[index]
        snapshot_uri=row["snapshot_uri"],  # type: ignore[index]
        clip_uri=row["clip_uri"],  # type: ignore[index]
        clip_status=str(row["clip_status"]),  # type: ignore[index]
        status=status,
        target=payload.get("target") or {},
    )


__all__ = ["DispatchReport", "OutboxDispatcher"]
