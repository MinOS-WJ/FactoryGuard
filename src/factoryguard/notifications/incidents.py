from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from math import isfinite
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from factoryguard.domain.incidents import SEVERITY_LABELS, STATUS_LABELS, Incident
from factoryguard.net import close_http_error
from factoryguard.notifications.adapters import parse_phone_recipients, parse_recipients
from factoryguard.notifications.providers import sms_preset


@dataclass(frozen=True, slots=True)
class IncidentNotificationConfig:
    """JSON-backed delivery settings for newly created incidents."""

    enabled: bool = False
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    email_enabled: bool = False
    email_host: str = ""
    email_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_sender: str = ""
    email_recipients: tuple[str, ...] = ()
    email_security: str = "starttls"
    sms_enabled: bool = False
    sms_endpoint: str = ""
    sms_provider: str = "custom_http"
    sms_token: str = ""
    sms_sender: str = ""
    sms_template_id: str = ""
    sms_recipients: tuple[str, ...] = ()
    timeout: float = 10.0

    def __post_init__(self) -> None:
        active_email = self.enabled and self.email_enabled
        active_sms = self.enabled and self.sms_enabled
        active_webhook = self.enabled and self.webhook_enabled

        email_port = self.email_port
        if active_email and (
            isinstance(email_port, bool)
            or not isinstance(email_port, int)
            or not 1 <= email_port <= 65535
        ):
            raise ValueError("邮件端口必须在 1 到 65535 之间")
        security = str(self.email_security or "").strip().lower()
        if active_email and security not in {"starttls", "ssl", "none"}:
            raise ValueError("邮件安全模式必须是 starttls、ssl 或 none")

        email_recipients = self.email_recipients
        if active_email:
            try:
                email_recipients = (
                    parse_recipients(self.email_recipients) if self.email_recipients else ()
                )
            except ValueError as exc:
                raise ValueError(f"邮件收件人无效：{exc}") from exc

        sms_endpoint = str(self.sms_endpoint or "").strip()
        if any(character in sms_endpoint for character in "\r\n\x00"):
            raise ValueError("短信网关地址包含无效控制字符")
        if active_sms and not sms_endpoint.lower().startswith("https://"):
            raise ValueError("短信网关地址必须以 https:// 开头")

        sms_recipients = self.sms_recipients
        if active_sms:
            try:
                sms_recipients = (
                    parse_phone_recipients(self.sms_recipients) if self.sms_recipients else ()
                )
            except ValueError as exc:
                raise ValueError(f"短信收件人无效：{exc}") from exc

        sms_provider = str(self.sms_provider or "custom_http").strip().lower()
        if active_sms:
            try:
                preset = sms_preset(sms_provider)
            except ValueError as exc:
                raise ValueError(f"短信服务商无效：{exc}") from exc
            if not preset.implemented:
                raise ValueError(f"{preset.label} 尚未提供内置短信适配器")
            if not sms_recipients:
                raise ValueError("启用短信通知前请填写接收号码")
            sms_provider = preset.key

        if isinstance(self.timeout, bool) or not isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("通知超时时间必须为正数")

        webhook_url = str(self.webhook_url or "").strip()
        if any(character in webhook_url for character in "\r\n\x00"):
            raise ValueError("Webhook 地址包含无效控制字符")
        if active_webhook and not webhook_url.lower().startswith("https://"):
            raise ValueError("Webhook 地址必须以 https:// 开头")

        email_sender = str(self.email_sender or "").strip()
        if active_email:
            if email_sender:
                try:
                    email_sender = parse_recipients((email_sender,))[0]
                except ValueError as exc:
                    raise ValueError(f"邮件发件人无效：{exc}") from exc
            if not self.email_host.strip():
                raise ValueError("启用邮件通知前请填写 SMTP 地址")
            if not email_sender:
                raise ValueError("启用邮件通知前请填写发件人")
            if not email_recipients:
                raise ValueError("启用邮件通知前请填写收件人")

        object.__setattr__(self, "email_port", email_port)
        object.__setattr__(self, "email_security", security or self.email_security)
        object.__setattr__(self, "email_recipients", email_recipients)
        object.__setattr__(self, "email_sender", email_sender)
        object.__setattr__(self, "sms_endpoint", sms_endpoint)
        object.__setattr__(self, "sms_recipients", sms_recipients)
        object.__setattr__(self, "sms_provider", sms_provider)
        object.__setattr__(self, "webhook_url", webhook_url)
        object.__setattr__(self, "timeout", float(self.timeout))

    @property
    def enabled_channels(self) -> tuple[str, ...]:
        return tuple(
            channel
            for channel, enabled in (
                ("webhook", self.webhook_enabled),
                ("email", self.email_enabled),
                ("sms", self.sms_enabled),
            )
            if enabled
        )

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.enabled_channels)

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> IncidentNotificationConfig:
        webhook = settings.get("webhook", {})
        email = settings.get("email", {})
        sms = settings.get("sms", {})
        webhook = webhook if isinstance(webhook, Mapping) else {}
        email = email if isinstance(email, Mapping) else {}
        sms = sms if isinstance(sms, Mapping) else {}
        recipients = email.get("recipients", ())
        if isinstance(recipients, str):
            recipients = tuple(value.strip() for value in recipients.split(";") if value.strip())
        sms_recipients = sms.get("recipients", ())
        if isinstance(sms_recipients, str):
            sms_recipients = tuple(
                value.strip() for value in sms_recipients.split(";") if value.strip()
            )
        return cls(
            enabled=bool(settings.get("enabled", False)),
            webhook_enabled=bool(webhook.get("enabled", False)),
            webhook_url=str(webhook.get("url", "")),
            webhook_secret=str(webhook.get("secret", "")),
            email_enabled=bool(email.get("enabled", False)),
            email_host=str(email.get("host", "")),
            email_port=email.get("port", 587),
            email_username=str(email.get("username", "")),
            email_password=str(email.get("password", "")),
            email_sender=str(email.get("sender", "")),
            email_recipients=tuple(str(value) for value in recipients if str(value).strip()),
            email_security=str(email.get("security", "starttls")),
            sms_enabled=bool(sms.get("enabled", False)),
            sms_endpoint=str(sms.get("endpoint", "")),
            sms_provider=str(sms.get("provider", "custom_http")),
            sms_token=str(sms.get("token", "")),
            sms_sender=str(sms.get("sender", "")),
            sms_template_id=str(sms.get("template_id", "")),
            sms_recipients=tuple(str(value) for value in sms_recipients if str(value).strip()),
            timeout=settings.get("timeout", 10.0),
        )


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    channel: str
    success: bool
    detail: str


class IncidentNotifier:
    """Send incident summaries to configured Webhook and SMTP channels."""

    def __init__(self, config: IncidentNotificationConfig) -> None:
        self.config = config

    def send(
        self,
        incident: Incident,
        *,
        site_name: str = "",
        area_name: str = "",
        camera_name: str = "",
        channels: tuple[str, ...] | None = None,
    ) -> tuple[DeliveryResult, ...]:
        if not self.config.available:
            return ()
        requested = set(channels) if channels is not None else {"webhook", "email", "sms"}
        location = " / ".join(value for value in (site_name, area_name, camera_name) if value)
        subject = (
            f"[FactoryGuard] {SEVERITY_LABELS.get(incident.severity, '险情')} · {incident.title}"
        )
        body = _incident_body(incident, location)
        results: list[DeliveryResult] = []
        if self.config.webhook_enabled and "webhook" in requested:
            results.append(
                self._deliver(
                    "webhook",
                    lambda: self._send_webhook(incident, location, subject, body),
                    self.config.webhook_secret,
                )
            )
        if self.config.email_enabled and "email" in requested:
            results.append(
                self._deliver(
                    "email",
                    lambda: self._send_email(subject, body),
                    self.config.email_password,
                )
            )
        if self.config.sms_enabled and "sms" in requested:
            results.append(
                self._deliver(
                    "sms",
                    lambda: self._send_sms(incident, location, subject, body),
                    self.config.sms_token,
                )
            )
        return tuple(results)

    def send_test(self) -> tuple[DeliveryResult, ...]:
        if not self.config.available:
            return ()
        subject = "[FactoryGuard] 通知测试"
        body = "这是一条 FactoryGuard 通知测试消息。收到后说明 Webhook/邮件配置可正常使用。"
        results: list[DeliveryResult] = []
        if self.config.webhook_enabled:
            results.append(
                self._deliver(
                    "webhook",
                    lambda: self._send_webhook(None, "", subject, body),
                    self.config.webhook_secret,
                )
            )
        if self.config.email_enabled:
            results.append(
                self._deliver(
                    "email",
                    lambda: self._send_email(subject, body),
                    self.config.email_password,
                )
            )
        if self.config.sms_enabled:
            results.append(
                self._deliver(
                    "sms",
                    lambda: self._send_sms(None, "", subject, body),
                    self.config.sms_token,
                )
            )
        return tuple(results)

    def _deliver(
        self,
        channel: str,
        operation: Callable[[], tuple[bool, str]],
        secret: str,
    ) -> DeliveryResult:
        """Keep one broken channel from aborting the remaining deliveries."""
        try:
            success, detail = operation()
        except Exception as exc:
            success, detail = False, _redacted_detail(exc, secret)
        return DeliveryResult(channel, success, detail)

    def _send_webhook(
        self,
        incident: Incident | None,
        location: str,
        subject: str,
        body: str,
    ) -> tuple[bool, str]:
        timestamp = str(int(time.time()))
        nonce = uuid4().hex
        payload: dict[str, Any] = {
            "type": "factoryguard.incident" if incident else "factoryguard.test",
            "subject": subject,
            "message": body,
            "timestamp": timestamp,
            "nonce": nonce,
        }
        if incident is not None:
            payload["incident"] = {
                "id": incident.id,
                "title": incident.title,
                "description": incident.description,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "site": location,
                "reporter": incident.reporter,
                "occurred_at": incident.occurred_at.isoformat(),
            }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-FactoryGuard-Timestamp": timestamp,
            "X-FactoryGuard-Nonce": nonce,
        }
        if self.config.webhook_secret:
            signed = timestamp.encode("ascii") + b"." + encoded
            signature = hmac.new(
                self.config.webhook_secret.encode("utf-8"), signed, hashlib.sha256
            ).hexdigest()
            headers["X-FactoryGuard-Signature"] = f"sha256={signature}"
        request = Request(self.config.webhook_url, data=encoded, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                status = int(getattr(response, "status", 200))
        except (OSError, URLError, ValueError) as exc:
            close_http_error(exc)
            return False, _redacted_detail(exc, self.config.webhook_secret)
        if not 200 <= status < 300:
            return False, f"Webhook 返回 HTTP {status}"
        return True, f"Webhook 已接受（HTTP {status}）"

    def _send_email(self, subject: str, body: str) -> tuple[bool, str]:
        safe_subject = " ".join(subject.split())
        message = EmailMessage()
        message["From"] = self.config.email_sender
        message["To"] = ", ".join(self.config.email_recipients)
        message["Subject"] = safe_subject
        message.set_content(body)
        client_factory = smtplib.SMTP_SSL if self.config.email_security == "ssl" else smtplib.SMTP
        try:
            with client_factory(
                self.config.email_host, self.config.email_port, timeout=self.config.timeout
            ) as client:
                if self.config.email_security == "starttls":
                    client.starttls()
                if self.config.email_username:
                    client.login(self.config.email_username, self.config.email_password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            return False, _redacted_detail(exc, self.config.email_password)
        return True, f"邮件已发送给 {len(self.config.email_recipients)} 位收件人"

    def _send_sms(
        self,
        incident: Incident | None,
        location: str,
        subject: str,
        body: str,
    ) -> tuple[bool, str]:
        payload = {
            "provider": self.config.sms_provider,
            "to": list(self.config.sms_recipients),
            "sender": self.config.sms_sender,
            "template_id": self.config.sms_template_id,
            "message": body[:500],
            "subject": subject[:120],
        }
        if incident is not None:
            payload["incident"] = {
                "id": incident.id,
                "title": incident.title,
                "severity": incident.severity.value,
                "occurred_at": incident.occurred_at.isoformat(),
                "location": location,
            }
        headers = {"Content-Type": "application/json"}
        if self.config.sms_token:
            headers["Authorization"] = f"Bearer {self.config.sms_token}"
        request = Request(
            self.config.sms_endpoint,
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                status = int(getattr(response, "status", 200))
        except (OSError, URLError, ValueError) as exc:
            close_http_error(exc)
            return False, _redacted_detail(exc, self.config.sms_token)
        if not 200 <= status < 300:
            return False, f"短信网关返回 HTTP {status}"
        return True, f"短信网关已接受 {len(self.config.sms_recipients)} 个号码"


def _incident_body(incident: Incident, location: str) -> str:
    severity = SEVERITY_LABELS.get(incident.severity, str(incident.severity))
    status = STATUS_LABELS.get(incident.status, str(incident.status))
    lines = [
        f"险情：{incident.title}",
        f"等级：{severity}",
        f"状态：{status}",
        f"时间：{incident.occurred_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if location:
        lines.append(f"位置：{location}")
    if incident.reporter:
        lines.append(f"上报人：{incident.reporter}")
    if incident.description:
        lines.extend(("", incident.description))
    lines.extend(("", "请登录 FactoryGuard 控制台查看并处置。"))
    return "\n".join(lines)


def _redacted_detail(exc: BaseException, secret: str) -> str:
    detail = str(exc).replace("\r", " ").replace("\n", " ")[:500] or exc.__class__.__name__
    return detail.replace(secret, "***") if secret else detail


__all__ = [
    "DeliveryResult",
    "IncidentNotificationConfig",
    "IncidentNotifier",
]
