from __future__ import annotations

from datetime import UTC, datetime
import json
import smtplib

import pytest

from factoryguard.notifications.adapters import parse_recipients
from factoryguard.domain.models import Detection, Event, Frame, Rule, RuleType
from factoryguard.notifications.adapters import EmailAdapter, SmsHttpAdapter, parse_phone_recipients


def _event() -> Event:
    rule = Rule("email-rule", "CH01", "fence", RuleType.POLYGON, ((0, 0), (10, 0), (10, 10)))
    return Event.create(
        rule=rule,
        frame=Frame("CH01", 1, datetime(2026, 1, 1, tzinfo=UTC), 20, 20),
        detection=Detection("person", 0.9, (1, 1, 4, 8), "track-1"),
    )


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, *, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.message = message


def test_email_adapter_sends_using_smtp_and_does_not_expose_password(monkeypatch):
    monkeypatch.setattr("factoryguard.notifications.adapters.smtplib.SMTP", _FakeSMTP)
    adapter = EmailAdapter(
        host="smtp.example.test",
        username="factoryguard@example.test",
        password="secret",
        sender="factoryguard@example.test",
        recipients=("operator@example.test",),
    )

    success, detail = adapter.send(_event(), outbox={})

    assert (success, detail) == (True, "SMTP accepted for 1 recipient(s)")
    smtp = _FakeSMTP.instances[-1]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("factoryguard@example.test", "secret")
    assert smtp.message["To"] == "operator@example.test"
    assert "secret" not in smtp.message.as_string()
    assert "FactoryGuard event" in str(smtp.message["Subject"])


def test_email_adapter_rejects_conflicting_tls_modes():
    try:
        EmailAdapter(
            host="smtp.example.test",
            sender="factoryguard@example.test",
            recipients=("operator@example.test",),
            use_ssl=True,
            starttls=True,
        )
    except ValueError as exc:
        assert "cannot both" in str(exc)
    else:
        raise AssertionError("expected conflicting TLS modes to be rejected")


def test_parse_recipients_accepts_common_delimiters_and_deduplicates():
    assert parse_recipients(
        "one@example.test, two@example.test; three@example.test\nfour@example.test; one@example.test"
    ) == (
        "one@example.test",
        "two@example.test",
        "three@example.test",
        "four@example.test",
    )
    assert parse_recipients(["one@example.test; two@example.test", "three@example.test"]) == (
        "one@example.test",
        "two@example.test",
        "three@example.test",
    )


def test_parse_recipients_rejects_header_injection_and_invalid_mailboxes():
    for value in ("bad", "a@example.test\nBcc: attacker@example.test", "a@@example.test", "@example.test"):
        try:
            parse_recipients(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid recipients to be rejected: {value!r}")


def test_email_adapter_uses_ssl_client_without_starttls(monkeypatch):
    class _FakeSSL(_FakeSMTP):
        pass

    monkeypatch.setattr("factoryguard.notifications.adapters.smtplib.SMTP_SSL", _FakeSSL)
    adapter = EmailAdapter(
        host="smtp.example.test",
        port=465,
        sender="factoryguard@example.test",
        recipients=("operator@example.test",),
        use_ssl=True,
        starttls=False,
    )

    success, _detail = adapter.send(_event(), outbox={})

    assert success is True
    smtp = _FakeSSL.instances[-1]
    assert smtp.port == 465
    assert smtp.started_tls is False


def test_email_adapter_supports_plaintext_without_starttls(monkeypatch):
    monkeypatch.setattr("factoryguard.notifications.adapters.smtplib.SMTP", _FakeSMTP)
    adapter = EmailAdapter(
        host="smtp.example.test",
        port=25,
        sender="factoryguard@example.test",
        recipients=("operator@example.test",),
        use_ssl=False,
        starttls=False,
    )

    success, _detail = adapter.send(_event(), outbox={})

    assert success is True
    assert _FakeSMTP.instances[-1].started_tls is False


def test_email_adapter_redacts_password_from_delivery_error(monkeypatch):
    class _FailingSMTP(_FakeSMTP):
        def send_message(self, message):
            raise smtplib.SMTPException("authentication failed for password=super-secret")

    monkeypatch.setattr("factoryguard.notifications.adapters.smtplib.SMTP", _FailingSMTP)
    adapter = EmailAdapter(
        host="smtp.example.test",
        username="factoryguard@example.test",
        password="super-secret",
        sender="factoryguard@example.test",
        recipients=("operator@example.test",),
    )

    success, detail = adapter.send(_event(), outbox={})

    assert success is False
    assert "super-secret" not in detail
    assert "***" in detail


def test_parse_phone_recipients_normalizes_and_deduplicates_numbers():
    assert parse_phone_recipients("13800138000; +86 139-0013-9000\n13800138000") == (
        "13800138000",
        "+8613900139000",
    )
    for value in ("not-a-number", "123", "13800138000\n<bad>"):
        with pytest.raises(ValueError):
            parse_phone_recipients(value)


def test_sms_http_adapter_posts_generic_gateway_payload(monkeypatch):
    captured = {}

    class _Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("factoryguard.notifications.adapters.urlopen", _urlopen)
    adapter = SmsHttpAdapter(
        endpoint="https://sms.example.test/send",
        recipients=("13800138000", "+8613900139000"),
        provider="custom_http",
        token="sms-token",
        sender="FactoryGuard",
        template_id="event-alert",
    )

    success, detail = adapter.send(_event(), outbox={})

    assert success is True
    assert detail == "SMS gateway accepted for 2 recipient(s)"
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["provider"] == "custom_http"
    assert payload["to"] == ["13800138000", "+8613900139000"]
    assert payload["sender"] == "FactoryGuard"
    assert payload["template_id"] == "event-alert"
    assert payload["event"]["id"]
    assert request.get_header("Authorization") == "Bearer sms-token"
    assert captured["timeout"] == 10.0


def test_sms_http_adapter_rejects_reserved_cloud_provider():
    with pytest.raises(ValueError, match="reserved"):
        SmsHttpAdapter(
            endpoint="https://sms.example.test/send",
            recipients=("13800138000",),
            provider="aliyun",
        )
