from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from factoryguard.domain.incidents import Incident, IncidentSeverity
from factoryguard.notifications.incidents import (
    IncidentNotificationConfig,
    IncidentNotifier,
)


def _incident() -> Incident:
    return Incident.create(
        title="仓库北门人员闯入",
        description="请安保人员现场确认。",
        severity=IncidentSeverity.HIGH,
        reporter="测试用户",
        occurred_at=datetime(2026, 11, 5, 2, 0, tzinfo=UTC),
    )


def test_incident_notification_config_validates_enabled_channels():
    with pytest.raises(ValueError, match="https"):
        IncidentNotificationConfig(enabled=True, webhook_enabled=True, webhook_url="http://x")
    with pytest.raises(ValueError, match="SMTP"):
        IncidentNotificationConfig(enabled=True, email_enabled=True)
    with pytest.raises(ValueError, match="发件人"):
        IncidentNotificationConfig(
            enabled=True,
            email_enabled=True,
            email_host="smtp.example.test",
            email_sender="bad\nBcc: attacker@example.test",
            email_recipients=("operator@example.test",),
        )
    with pytest.raises(ValueError, match="短信网关"):
        IncidentNotificationConfig(
            enabled=True,
            sms_enabled=True,
            sms_endpoint="http://sms.example.test/send",
            sms_recipients=("13800138000",),
        )
    with pytest.raises(ValueError, match="短信收件人"):
        IncidentNotificationConfig(
            enabled=True,
            sms_enabled=True,
            sms_endpoint="https://sms.example.test/send",
            sms_recipients=("invalid",),
        )
    with pytest.raises(ValueError, match="收件人"):
        IncidentNotificationConfig(
            enabled=True,
            email_enabled=True,
            email_host="smtp.example.test",
            email_sender="factoryguard@example.test",
        )


def test_disabled_master_can_preserve_incomplete_channel_profiles():
    config = IncidentNotificationConfig(
        enabled=False,
        webhook_enabled=True,
        webhook_url="http://draft.example.test/hook",
        email_enabled=True,
        sms_enabled=True,
        sms_endpoint="http://draft.example.test/sms",
    )

    assert config.available is False
    assert config.enabled_channels == ("webhook", "email", "sms")


def test_disabled_master_can_preserve_invalid_channel_drafts():
    config = IncidentNotificationConfig.from_settings(
        {
            "enabled": False,
            "webhook": {"enabled": True, "url": "draft"},
            "email": {
                "enabled": True,
                "host": "",
                "port": "bad",
                "sender": "not-an-email",
                "recipients": "invalid",
                "security": "invalid",
            },
            "sms": {
                "enabled": True,
                "endpoint": "draft",
                "provider": "unsupported",
                "recipients": "invalid",
            },
        }
    )

    assert config.available is False
    assert config.webhook_enabled is True
    assert config.email_enabled is True
    assert config.sms_enabled is True


def test_incident_notifier_webhook_payload_and_signature(monkeypatch):
    captured = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("factoryguard.notifications.incidents.urlopen", fake_urlopen)
    config = IncidentNotificationConfig(
        enabled=True,
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/incident",
        webhook_secret="signing-secret",
        timeout=6.0,
    )
    result = IncidentNotifier(config).send(
        _incident(),
        site_name="星河园区",
        area_name="仓库",
        camera_name="北门",
    )[0]

    assert result.success is True
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["type"] == "factoryguard.incident"
    assert payload["incident"]["title"] == "仓库北门人员闯入"
    assert "仓库 / 北门" in payload["incident"]["site"]
    timestamp = request.get_header("X-factoryguard-timestamp")
    signature = request.get_header("X-factoryguard-signature")
    expected = hmac.new(
        b"signing-secret",
        timestamp.encode("ascii") + b"." + request.data,
        hashlib.sha256,
    ).hexdigest()
    assert signature == f"sha256={expected}"
    assert captured["timeout"] == 6.0


def test_incident_notifier_sends_smtp_email(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, *, timeout):
            sent.update(host=host, port=port, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr("factoryguard.notifications.incidents.smtplib.SMTP", FakeSMTP)
    config = IncidentNotificationConfig(
        enabled=True,
        email_enabled=True,
        email_host="smtp.example.test",
        email_username="factoryguard@example.test",
        email_password="secret",
        email_sender="factoryguard@example.test",
        email_recipients=("operator@example.test",),
    )
    result = IncidentNotifier(config).send_test()[0]

    assert result.success is True
    assert sent["host"] == "smtp.example.test"
    assert sent["starttls"] is True
    assert sent["login"] == ("factoryguard@example.test", "secret")
    assert sent["message"]["To"] == "operator@example.test"
    assert "通知测试" in str(sent["message"]["Subject"])


def test_incident_notifier_sends_sms_gateway_payload(monkeypatch):
    captured = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("factoryguard.notifications.incidents.urlopen", fake_urlopen)
    config = IncidentNotificationConfig(
        enabled=True,
        sms_enabled=True,
        sms_endpoint="https://sms.example.test/send",
        sms_token="sms-token",
        sms_sender="FactoryGuard",
        sms_template_id="incident-alert",
        sms_recipients=("13800138000", "+8613900139000"),
    )
    result = IncidentNotifier(config).send(_incident(), site_name="星河园区", area_name="仓库")[0]

    assert result.success is True
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["provider"] == "custom_http"
    assert payload["to"] == ["13800138000", "+8613900139000"]
    assert payload["sender"] == "FactoryGuard"
    assert payload["template_id"] == "incident-alert"
    assert payload["incident"]["title"] == "仓库北门人员闯入"
    assert request.get_header("Authorization") == "Bearer sms-token"


def test_incident_notifier_returns_empty_when_disabled():
    assert IncidentNotifier(IncidentNotificationConfig()).send(_incident()) == ()
    assert IncidentNotifier(IncidentNotificationConfig()).send_test() == ()


def test_incident_notifier_isolates_unexpected_channel_exception(monkeypatch):
    notifier = IncidentNotifier(
        IncidentNotificationConfig(
            enabled=True,
            webhook_enabled=True,
            webhook_url="https://hooks.example.test/incident",
            webhook_secret="signing-secret",
        )
    )

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("unexpected webhook failure")

    monkeypatch.setattr(notifier, "_send_webhook", fail_delivery)
    results = notifier.send(_incident())

    assert len(results) == 1
    assert results[0].channel == "webhook"
    assert results[0].success is False
    assert results[0].detail == "unexpected webhook failure"


def test_failed_notification_keeps_retrying_after_repeated_failures(tmp_path):
    """A safety alert must not be dropped because the network was down."""
    from factoryguard.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "retry-policy.db")
    incident = Incident.create(title="网络中断期间的险情")
    store.create_incident(incident, actor="tester")

    now = datetime.now(UTC)
    for _attempt in range(5):
        store.record_incident_notification(
            incident.id, "webhook", success=False, detail="network unreachable"
        )
        # Age the failure so the backoff window has elapsed.
        store._connection_or_raise().execute(
            "UPDATE incident_notification_delivery SET updated_at = ? WHERE incident_id = ?",
            ((now - timedelta(days=1)).isoformat(), incident.id),
        )

    candidates = store.incident_notification_retry_candidates(min_age_seconds=60)
    assert candidates == ((incident.id, ("webhook",)),)

    # The attempt limit is optional; an explicit limit still applies.
    limited = store.incident_notification_retry_candidates(max_attempts=3, min_age_seconds=60)
    assert limited == ()
    store.close()


def test_notification_retry_backoff_grows_with_attempts(tmp_path):
    from factoryguard.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "retry-backoff.db")
    incident = Incident.create(title="退避测试")
    store.create_incident(incident, actor="tester")
    now = datetime.now(UTC)

    store.record_incident_notification(incident.id, "webhook", success=False, detail="x")
    store._connection_or_raise().execute(
        "UPDATE incident_notification_delivery SET updated_at = ? WHERE incident_id = ?",
        ((now - timedelta(seconds=30)).isoformat(), incident.id),
    )
    # First failure waits at least a minute.
    assert store.incident_notification_retry_candidates(min_age_seconds=60) == ()

    store._connection_or_raise().execute(
        "UPDATE incident_notification_delivery SET updated_at = ? WHERE incident_id = ?",
        ((now - timedelta(seconds=90)).isoformat(), incident.id),
    )
    assert store.incident_notification_retry_candidates(min_age_seconds=60) == (
        (incident.id, ("webhook",)),
    )
    store.close()


def test_closed_incident_stops_automatic_notification_retry(tmp_path):
    """A resolved or false-alarm incident must not keep notifying."""
    from factoryguard.domain.incidents import IncidentStatus
    from factoryguard.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "retry-closed.db")
    incident = Incident.create(title="已误报的历史险情")
    store.create_incident(incident, actor="tester")
    store.record_incident_notification(
        incident.id, "webhook", success=False, detail="network unreachable"
    )
    now = datetime.now(UTC)
    store._connection_or_raise().execute(
        "UPDATE incident_notification_delivery SET updated_at = ? WHERE incident_id = ?",
        ((now - timedelta(days=1)).isoformat(), incident.id),
    )

    # While the incident is still open the alert keeps retrying.
    assert store.incident_notification_retry_candidates(min_age_seconds=60) == (
        (incident.id, ("webhook",)),
    )

    store.update_incident_status(incident.id, IncidentStatus.FALSE_ALARM, actor="tester")
    assert store.incident_notification_retry_candidates(min_age_seconds=60) == ()

    # The failed delivery stays visible for manual review and manual retry.
    assert store.failed_incident_notification_channels(incident.id) == ("webhook",)
    store.close()
