"""Pluggable notification delivery adapters for the transactional Outbox."""

from __future__ import annotations

import json
import re
import smtplib
from collections.abc import Iterable, Mapping
from email.message import EmailMessage
from math import isfinite
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from factoryguard.domain.models import Event
from factoryguard.net import close_http_error
from factoryguard.notifications.providers import sms_preset
from factoryguard.notifications.webhook import WebhookNotifier, build_webhook_request


class NotificationAdapter(Protocol):
    """Contract implemented by one concrete notification channel."""

    def send(self, event: Event, *, outbox: Mapping[str, object]) -> tuple[bool, str]:
        """Deliver one event and return ``(success, detail)``."""


class WebhookAdapter:
    """Adapter for the built-in signed HTTPS Webhook channel."""

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        notifier: WebhookNotifier | None = None,
    ) -> None:
        self.url = url
        self.secret = secret
        self.notifier = notifier or WebhookNotifier()

    def send(self, event: Event, *, outbox: Mapping[str, object]) -> tuple[bool, str]:
        del outbox  # The event dedup key is already part of the signed request.
        request = build_webhook_request(event, url=self.url, secret=self.secret)
        return self.notifier.send(request)


class EmailAdapter:
    """SMTP email adapter using provider-neutral standard-library APIs."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        sender: str,
        recipients: str | Iterable[str],
        use_ssl: bool = False,
        starttls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        if not host.strip():
            raise ValueError("SMTP host is required")
        if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        normalized_sender = _clean_mailbox(sender, field="email sender")
        normalized_recipients = parse_recipients(recipients)
        if use_ssl and starttls:
            raise ValueError("SMTP SSL and STARTTLS cannot both be enabled")
        if isinstance(timeout, bool) or not isfinite(timeout) or timeout <= 0:
            raise ValueError("SMTP timeout must be positive")
        self.host = host.strip()
        self.port = port
        self.username = username.strip()
        self.password = password
        self.sender = normalized_sender
        self.recipients = normalized_recipients
        self.use_ssl = use_ssl
        self.starttls = starttls
        self.timeout = timeout

    def send(self, event: Event, *, outbox: Mapping[str, object]) -> tuple[bool, str]:
        del outbox
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = f"FactoryGuard event {event.level}: {event.id}"
        message["X-FactoryGuard-Event-Id"] = event.id
        message["X-FactoryGuard-Dedup-Key"] = event.dedup_key
        message.set_content(_email_body(event))
        _attach_event_media(message, event)
        client_factory = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        try:
            with client_factory(self.host, self.port, timeout=self.timeout) as client:
                if self.starttls:
                    client.starttls()
                if self.username:
                    client.login(self.username, self.password)
                client.send_message(message)
            return True, f"SMTP accepted for {len(self.recipients)} recipient(s)"
        except (OSError, smtplib.SMTPException) as exc:
            return False, _redact_error(exc, self.password)


class SmsHttpAdapter:
    """Generic JSON-over-HTTPS adapter for an organization-owned SMS gateway."""

    def __init__(
        self,
        *,
        endpoint: str,
        recipients: str | Iterable[str],
        provider: str = "custom_http",
        token: str = "",
        sender: str = "",
        template_id: str = "",
        timeout: float = 10.0,
    ) -> None:
        preset = sms_preset(provider)
        if not preset.implemented:
            raise ValueError(f"{preset.label} is reserved and has no built-in adapter yet")
        normalized_endpoint = endpoint.strip()
        if not normalized_endpoint.lower().startswith("https://"):
            raise ValueError("SMS endpoint must use HTTPS")
        normalized_recipients = parse_phone_recipients(recipients)
        if isinstance(timeout, bool) or not isfinite(timeout) or timeout <= 0:
            raise ValueError("SMS timeout must be positive")
        self.endpoint = normalized_endpoint
        self.recipients = normalized_recipients
        self.provider = preset.key
        self.token = token
        self.sender = sender.strip()
        self.template_id = template_id.strip()
        self.timeout = timeout

    def send(self, event: Event, *, outbox: Mapping[str, object]) -> tuple[bool, str]:
        payload = {
            "provider": self.provider,
            "to": list(self.recipients),
            "sender": self.sender,
            "template_id": self.template_id,
            "message": _event_message(event),
            "event": {
                "id": event.id,
                "dedup_key": event.dedup_key,
                "level": event.level,
                "stream_id": event.stream_id or event.channel_id,
                "occurred_at": event.occurred_at.isoformat(),
                "snapshot_uri": event.snapshot_uri,
                "clip_uri": event.clip_uri,
                "clip_status": event.clip_status,
            },
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
            if not 200 <= status < 300:
                return False, f"SMS gateway returned HTTP {status}"
            return True, f"SMS gateway accepted for {len(self.recipients)} recipient(s)"
        except (OSError, URLError, ValueError) as exc:
            close_http_error(exc)
            return False, _redact_error(exc, self.token)


def parse_recipients(value: str | Iterable[str]) -> tuple[str, ...]:
    """Parse comma/semicolon/newline-separated mailboxes without retaining blanks."""
    if isinstance(value, str):
        candidates: list[str] = re.split(r"[,;\n]+", value)
    else:
        candidates = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("email recipients must be strings")
            candidates.extend(re.split(r"[,;\n]+", item))
    normalized: list[str] = []
    for candidate in candidates:
        mailbox = candidate.strip()
        if not mailbox:
            continue
        mailbox = _clean_mailbox(mailbox, field="email recipient")
        if mailbox not in normalized:
            normalized.append(mailbox)
    if not normalized:
        raise ValueError("at least one email recipient is required")
    return tuple(normalized)


def parse_phone_recipients(value: str | Iterable[str]) -> tuple[str, ...]:
    """Parse and normalize mobile numbers separated by comma, semicolon or newline."""
    if isinstance(value, str):
        candidates = re.split(r"[,;\n]+", value)
    else:
        candidates = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("SMS recipients must be strings")
            candidates.extend(re.split(r"[,;\n]+", item))
    normalized: list[str] = []
    for candidate in candidates:
        number = re.sub(r"[\s()-]", "", candidate.strip())
        if not number:
            continue
        if not re.fullmatch(r"\+?[0-9]{6,20}", number):
            raise ValueError(f"SMS recipient is invalid: {candidate.strip()!r}")
        if number not in normalized:
            normalized.append(number)
    if not normalized:
        raise ValueError("at least one SMS recipient is required")
    return tuple(normalized)


def _clean_mailbox(value: str, *, field: str) -> str:
    mailbox = value.strip()
    if not mailbox:
        raise ValueError(f"{field} is required")
    if any(character in mailbox for character in "\r\n\x00"):
        raise ValueError(f"{field} contains invalid control characters")
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", mailbox):
        raise ValueError(f"{field} is invalid")
    if mailbox.count("@") != 1:
        raise ValueError(f"{field} must contain exactly one @")
    local, domain = mailbox.rsplit("@", 1)
    if (
        not local.strip()
        or not domain.strip()
        or domain.startswith(".")
        or domain.endswith(".")
        or ".." in domain
    ):
        raise ValueError(f"{field} is invalid")
    return mailbox


def _redact_error(exc: BaseException, password: str) -> str:
    detail = str(exc).replace("\r", " ").replace("\n", " ")
    if password:
        detail = detail.replace(password, "***")
    return detail[:500] or exc.__class__.__name__


_MAX_EMAIL_MEDIA_BYTES = 20 * 1024 * 1024


def _attach_event_media(message: EmailMessage, event: Event) -> None:
    for uri, maintype, subtype in (
        (event.snapshot_uri, "image", "png"),
        (event.clip_uri, "video", "mp4"),
    ):
        if not uri:
            continue
        path = Path(uri)
        try:
            if not path.is_file() or path.stat().st_size > _MAX_EMAIL_MEDIA_BYTES:
                continue
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )
        except OSError:
            continue


def _email_body(event: Event) -> str:
    stream_id = event.stream_id or event.channel_id
    entity = (
        "/".join(value for value in (event.site_id, event.area_id, event.camera_id) if value) or "-"
    )
    return (
        "FactoryGuard detected an event.\n\n"
        f"Event: {event.id}\n"
        f"Level: {event.level}\n"
        f"Stream: {stream_id}\n"
        f"Entity: {entity}\n"
        f"Rule: {event.rule_id}\n"
        f"Occurred at: {event.occurred_at.isoformat()}\n"
        f"Status: {event.status.value}\n"
        f"Assignees: {', '.join(event.assignees) if event.assignees else '-'}\n"
        f"Dedup key: {event.dedup_key}\n"
        f"Snapshot: {event.snapshot_uri or '-'}\n"
        f"Clip: {event.clip_uri or '-'}\n"
    )


def _event_message(event: Event) -> str:
    stream_id = event.stream_id or event.channel_id
    entity = (
        "/".join(value for value in (event.site_id, event.area_id, event.camera_id) if value) or "-"
    )
    assignees = ",".join(event.assignees) if event.assignees else "-"
    return (
        f"FactoryGuard {event.level} event {event.id}; "
        f"stream={stream_id}; entity={entity}; rule={event.rule_id}; assignees={assignees}"
    )


__all__ = [
    "EmailAdapter",
    "NotificationAdapter",
    "SmsHttpAdapter",
    "WebhookAdapter",
    "parse_phone_recipients",
    "parse_recipients",
]
