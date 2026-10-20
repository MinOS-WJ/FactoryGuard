from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from factoryguard.notifications.dispatcher import DispatchReport
from factoryguard.notifications.destinations import NotificationDestination
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.ui import notification_worker
from factoryguard.ui.notification_worker import NotificationDispatchOnceWorker, NotificationWorker


def test_notification_worker_dispatches_until_requested_to_stop(monkeypatch, tmp_path):
    database = tmp_path / "notifications.db"
    SQLiteStore(database).close()
    reports: list[DispatchReport] = []
    statuses: list[tuple[str, str]] = []

    class _FakeDispatcher:
        def __init__(self, **_kwargs):
            pass

        def dispatch_once(self, _store, *, limit):
            assert limit == 7
            return DispatchReport(claimed=1, sent=1, failed=0, retried=0)

    monkeypatch.setattr(notification_worker, "OutboxDispatcher", _FakeDispatcher)
    worker = NotificationWorker(
        db=str(database),
        webhook_url="https://example.test/hook",
        webhook_secret="secret",
        interval=30,
        batch_size=7,
    )
    worker.report_ready.connect(reports.append)
    worker.status_changed.connect(lambda state, detail: statuses.append((state, detail)))
    worker.report_ready.connect(lambda _report: worker.request_stop())

    worker.run()

    assert reports == [DispatchReport(claimed=1, sent=1, failed=0, retried=0)]
    assert statuses[0][0] == "starting"
    assert statuses[-1][0] == "stopped"


def test_notification_worker_rejects_invalid_delivery_configuration(tmp_path):
    database = tmp_path / "notifications.db"
    with pytest.raises(ValueError, match="webhook URL"):
        NotificationWorker(db=str(database), webhook_url="", webhook_secret="secret")
    with pytest.raises(ValueError, match="webhook secret"):
        NotificationWorker(db=str(database), webhook_url="https://example.test/hook", webhook_secret="")
    with pytest.raises(ValueError, match="interval"):
        NotificationWorker(
            db=str(database),
            webhook_url="https://example.test/hook",
            webhook_secret="secret",
            interval=0,
        )


def test_one_shot_notification_worker_dispatches_one_batch(monkeypatch, tmp_path):
    database = tmp_path / "one-shot.db"
    SQLiteStore(database).close()
    reports: list[DispatchReport] = []

    class _FakeDispatcher:
        def __init__(self, **_kwargs):
            pass

        def dispatch_once(self, _store, *, limit):
            assert limit == 3
            return DispatchReport(claimed=2, sent=2, failed=0, retried=0)

    monkeypatch.setattr(notification_worker, "OutboxDispatcher", _FakeDispatcher)
    worker = NotificationDispatchOnceWorker(
        db=str(database),
        webhook_url="https://example.test/hook",
        webhook_secret="secret",
        batch_size=3,
    )
    worker.report_ready.connect(reports.append)
    worker.run()
    assert reports == [DispatchReport(claimed=2, sent=2, failed=0, retried=0)]


def test_notification_worker_supports_email_only_delivery(tmp_path):
    database = tmp_path / "email-only.db"
    SQLiteStore(database).close()
    worker = NotificationWorker(
        db=str(database),
        email_host="smtp.example.test",
        email_port=465,
        email_username="factoryguard@example.test",
        email_password="secret",
        email_sender="factoryguard@example.test",
        email_recipients=("operator@example.test",),
        email_use_ssl=True,
        email_starttls=False,
        interval=30,
        batch_size=4,
    )

    assert set(worker.adapters) == {"email"}
    assert worker.adapters["email"].use_ssl is True
    assert worker.adapters["email"].starttls is False


def test_notification_worker_supports_sms_only_delivery(tmp_path):
    database = tmp_path / "sms-only.db"
    SQLiteStore(database).close()
    worker = NotificationWorker(
        db=str(database),
        sms_provider="custom_http",
        sms_endpoint="https://sms.example.test/send",
        sms_token="token",
        sms_sender="FactoryGuard",
        sms_template_id="event-alert",
        sms_recipients=("13800138000",),
        interval=30,
        batch_size=4,
    )

    assert set(worker.adapters) == {"sms"}
    assert worker.adapters["sms"].endpoint == "https://sms.example.test/send"
    assert worker.adapters["sms"].recipients == ("13800138000",)


def test_notification_worker_builds_independent_same_kind_adapters(tmp_path):
    database = tmp_path / "multi-webhook.db"
    SQLiteStore(database).close()
    destinations = (
        NotificationDestination(
            id="gate-a",
            kind="webhook",
            name="Gate A",
            provider="custom",
            enabled=True,
            settings={"url": "https://gate-a.example.test/hook"},
            secrets={"secret": "secret-a"},
        ),
        NotificationDestination(
            id="gate-b",
            kind="webhook",
            name="Gate B",
            provider="dingtalk",
            enabled=True,
            settings={"url": "https://gate-b.example.test/hook"},
            secrets={"secret": "secret-b"},
        ),
    )

    worker = NotificationWorker(db=str(database), destinations=destinations, interval=30)

    assert set(worker.adapters) == {"webhook:gate-a", "webhook:gate-b"}
    assert worker.adapters["webhook:gate-a"].url == "https://gate-a.example.test/hook"
    assert worker.adapters["webhook:gate-b"].url == "https://gate-b.example.test/hook"
