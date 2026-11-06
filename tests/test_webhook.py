from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from factoryguard.domain.models import Event, EventSource, NativeEvent, Rule
from factoryguard.notifications.dispatcher import OutboxDispatcher
from factoryguard.notifications.webhook import build_webhook_request
from factoryguard.storage.sqlite import SQLiteStore


def _event() -> Event:
    rule = Rule(
        "r-dispatch",
        "CH01",
        "设备原生告警",
        native_event_codes=("person",),
    )
    native = NativeEvent(
        device_id="device-1",
        channel_id="CH01",
        event_code="person",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source=EventSource.SYSTEM,
        stream_id="CH01",
    )
    return native.to_event(rule=rule)


def test_webhook_signature_and_idempotency_headers():
    event = _event()
    request = build_webhook_request(
        event, url="https://example.test/hook", secret="secret", timestamp=123, nonce="abc"
    )
    expected = hmac.new(b"secret", request.body + b"123abc", hashlib.sha256).hexdigest()
    assert request.headers["signature"] == expected
    assert request.headers["Idempotency-Key"] == event.dedup_key


class _FakeNotifier:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.result


def test_outbox_dispatcher_sends_and_marks_success(tmp_path):
    store = SQLiteStore(tmp_path / "dispatch.db")
    assert store.create_event_with_outbox(_event())
    notifier = _FakeNotifier((True, "HTTP 204"))
    report = OutboxDispatcher(
        webhook_url="https://example.test/hook",
        webhook_secret="secret",
        notifier=notifier,
    ).dispatch_once(store)
    assert report.claimed == 1
    assert report.sent == 1
    assert len(notifier.requests) == 1
    assert store.list_pending_outbox() == []
    store.close()


def test_outbox_dispatcher_schedules_retry_on_delivery_failure(tmp_path):
    store = SQLiteStore(tmp_path / "dispatch-fail.db")
    assert store.create_event_with_outbox(_event())
    notifier = _FakeNotifier((False, "network down"))
    report = OutboxDispatcher(
        webhook_url="https://example.test/hook",
        webhook_secret="secret",
        notifier=notifier,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    ).dispatch_once(store)
    assert report.claimed == 1
    assert report.retried == 1
    assert store.list_pending_outbox()[0]["status"] == "retry"
    store.close()


class _FakeChannelAdapter:
    def __init__(self, result):
        self.result = result
        self.events = []

    def send(self, event, *, outbox):
        self.events.append((event, dict(outbox)))
        return self.result


def test_outbox_dispatcher_accepts_pluggable_channel_adapter(tmp_path):
    store = SQLiteStore(tmp_path / "dispatch-custom-channel.db")
    event = _event()
    assert store.create_event_with_outbox(event, channel="sms")
    adapter = _FakeChannelAdapter((True, "provider accepted"))
    dispatcher = OutboxDispatcher(
        webhook_url="https://example.test/hook",
        webhook_secret="secret",
        adapters={"sms": adapter},
    )

    report = dispatcher.dispatch_once(store)

    assert report.claimed == 1
    assert report.sent == 1
    assert len(adapter.events) == 1
    assert adapter.events[0][1]["channel"] == "sms"
    assert store.list_pending_outbox() == []
    store.close()


def test_outbox_dispatcher_delivers_same_event_to_webhook_and_email(tmp_path):
    store = SQLiteStore(tmp_path / "dispatch-multi.db")
    event = _event()
    assert store.create_event_with_outbox(event, channels=("webhook", "email"))
    webhook = _FakeChannelAdapter((True, "HTTP 204"))
    email = _FakeChannelAdapter((True, "SMTP accepted for 1 recipient(s)"))
    dispatcher = OutboxDispatcher(adapters={"webhook": webhook, "email": email})

    report = dispatcher.dispatch_once(store)

    assert report.claimed == 2
    assert report.sent == 2
    assert len(store.list_events()) == 1
    assert len(webhook.events) == 1
    assert len(email.events) == 1
    assert webhook.events[0][0].id == email.events[0][0].id == event.id
    assert store.list_pending_outbox() == []
    store.close()
