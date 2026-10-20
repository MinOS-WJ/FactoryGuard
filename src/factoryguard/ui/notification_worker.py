from __future__ import annotations

"""Qt workers for continuous and one-shot notification delivery."""

from collections.abc import Iterable
import threading
from math import isfinite

from factoryguard.notifications.adapters import EmailAdapter, NotificationAdapter, SmsHttpAdapter, WebhookAdapter
from factoryguard.notifications.destinations import NotificationDestination
from factoryguard.notifications.dispatcher import DispatchReport, OutboxDispatcher
from factoryguard.storage.sqlite import SQLiteStore

from .qt import HAS_QT


if HAS_QT:
    from .qt import QObject, Signal, Slot

    class NotificationWorker(QObject):
        """Run webhook delivery away from the Qt GUI thread."""

        status_changed = Signal(str, str)
        report_ready = Signal(object)
        error = Signal(str)
        finished = Signal()

        def __init__(
            self,
            *,
            db: str,
            webhook_url: str = "",
            webhook_secret: str = "",
            email_host: str = "",
            email_port: int = 587,
            email_username: str = "",
            email_password: str = "",
            email_sender: str = "",
            email_recipients: str | Iterable[str] = (),
            email_use_ssl: bool = False,
            email_starttls: bool = True,
            sms_provider: str = "custom_http",
            sms_endpoint: str = "",
            sms_token: str = "",
            sms_sender: str = "",
            sms_template_id: str = "",
            sms_recipients: str | Iterable[str] = (),
            destinations: Iterable[NotificationDestination] = (),
            interval: float = 5.0,
            batch_size: int = 50,
        ) -> None:
            super().__init__()
            if not db.strip():
                raise ValueError("notification database path is required")
            if not isfinite(interval) or interval <= 0:
                raise ValueError("notification interval must be positive")
            if isinstance(batch_size, bool) or batch_size < 1:
                raise ValueError("notification batch size must be positive")
            self.db = db
            self.webhook_url = webhook_url.strip()
            self.webhook_secret = webhook_secret
            self.email_host = email_host.strip()
            self.email_port = email_port
            self.email_username = email_username
            self.email_password = email_password
            self.email_sender = email_sender
            self.email_recipients = email_recipients
            self.email_use_ssl = email_use_ssl
            self.email_starttls = email_starttls
            self.sms_provider = sms_provider
            self.sms_endpoint = sms_endpoint.strip()
            self.sms_token = sms_token
            self.sms_sender = sms_sender
            self.sms_template_id = sms_template_id
            self.sms_recipients = sms_recipients
            self.destinations = tuple(destinations)
            self.adapters = _delivery_adapters(
                webhook_url=self.webhook_url,
                webhook_secret=self.webhook_secret,
                email_host=self.email_host,
                email_port=self.email_port,
                email_username=self.email_username,
                email_password=self.email_password,
                email_sender=self.email_sender,
                email_recipients=self.email_recipients,
                email_use_ssl=self.email_use_ssl,
                email_starttls=self.email_starttls,
                sms_provider=self.sms_provider,
                sms_endpoint=self.sms_endpoint,
                sms_token=self.sms_token,
                sms_sender=self.sms_sender,
                sms_template_id=self.sms_template_id,
                sms_recipients=self.sms_recipients,
                destinations=self.destinations,
            )
            self.interval = interval
            self.batch_size = batch_size
            self._stop = threading.Event()

        def request_stop(self) -> None:
            """Request a prompt stop, including while waiting between polls."""
            self._stop.set()

        @Slot()
        def run(self) -> None:
            store: SQLiteStore | None = None
            try:
                if self.db == ":memory:":
                    self.status_changed.emit("disabled", "内存数据库不支持跨线程通知调度")
                    return
                store = SQLiteStore(self.db)
                dispatcher = OutboxDispatcher(adapters=self.adapters)
                self.status_changed.emit("starting", "通知调度器正在启动")
                while not self._stop.is_set():
                    try:
                        report = dispatcher.dispatch_once(store, limit=self.batch_size)
                        self.report_ready.emit(report)
                        self.status_changed.emit("running", _report_detail(report))
                    except Exception as exc:  # noqa: BLE001 - keep delivery alive after transient failures
                        detail = str(exc)
                        self.status_changed.emit("degraded", f"通知调度失败：{detail}")
                        self.error.emit(detail)
                    if self._stop.wait(self.interval):
                        break
            except Exception as exc:  # noqa: BLE001 - surface setup failures to the desktop UI
                detail = str(exc)
                self.status_changed.emit("error", f"通知调度器无法启动：{detail}")
                self.error.emit(detail)
            finally:
                if store is not None:
                    store.close()
                self.status_changed.emit("stopped", "通知调度器已停止")
                self.finished.emit()


    class NotificationDispatchOnceWorker(QObject):
        """Deliver one notification batch without blocking the Qt GUI thread."""

        status_changed = Signal(str, str)
        report_ready = Signal(object)
        error = Signal(str)
        finished = Signal()

        def __init__(
            self,
            *,
            db: str,
            webhook_url: str = "",
            webhook_secret: str = "",
            email_host: str = "",
            email_port: int = 587,
            email_username: str = "",
            email_password: str = "",
            email_sender: str = "",
            email_recipients: str | Iterable[str] = (),
            email_use_ssl: bool = False,
            email_starttls: bool = True,
            sms_provider: str = "custom_http",
            sms_endpoint: str = "",
            sms_token: str = "",
            sms_sender: str = "",
            sms_template_id: str = "",
            sms_recipients: str | Iterable[str] = (),
            destinations: Iterable[NotificationDestination] = (),
            batch_size: int = 50,
        ) -> None:
            super().__init__()
            if not db.strip():
                raise ValueError("notification database path is required")
            if isinstance(batch_size, bool) or batch_size < 1:
                raise ValueError("notification batch size must be positive")
            self.db = db
            self.webhook_url = webhook_url.strip()
            self.webhook_secret = webhook_secret
            self.email_host = email_host.strip()
            self.email_port = email_port
            self.email_username = email_username
            self.email_password = email_password
            self.email_sender = email_sender
            self.email_recipients = email_recipients
            self.email_use_ssl = email_use_ssl
            self.email_starttls = email_starttls
            self.sms_provider = sms_provider
            self.sms_endpoint = sms_endpoint.strip()
            self.sms_token = sms_token
            self.sms_sender = sms_sender
            self.sms_template_id = sms_template_id
            self.sms_recipients = sms_recipients
            self.destinations = tuple(destinations)
            self.adapters = _delivery_adapters(
                webhook_url=self.webhook_url,
                webhook_secret=self.webhook_secret,
                email_host=self.email_host,
                email_port=self.email_port,
                email_username=self.email_username,
                email_password=self.email_password,
                email_sender=self.email_sender,
                email_recipients=self.email_recipients,
                email_use_ssl=self.email_use_ssl,
                email_starttls=self.email_starttls,
                sms_provider=self.sms_provider,
                sms_endpoint=self.sms_endpoint,
                sms_token=self.sms_token,
                sms_sender=self.sms_sender,
                sms_template_id=self.sms_template_id,
                sms_recipients=self.sms_recipients,
                destinations=self.destinations,
            )
            self.batch_size = batch_size

        @Slot()
        def run(self) -> None:
            store: SQLiteStore | None = None
            try:
                if self.db == ":memory:":
                    self.status_changed.emit("disabled", "内存数据库不支持跨线程通知调度")
                    return
                store = SQLiteStore(self.db)
                dispatcher = OutboxDispatcher(adapters=self.adapters)
                self.status_changed.emit("starting", "正在执行一次通知发送")
                report = dispatcher.dispatch_once(store, limit=self.batch_size)
                self.report_ready.emit(report)
                self.status_changed.emit("running", _report_detail(report))
            except Exception as exc:  # noqa: BLE001 - surface one-shot failures to the UI
                detail = str(exc)
                self.status_changed.emit("error", f"通知发送失败：{detail}")
                self.error.emit(detail)
            finally:
                if store is not None:
                    store.close()
                self.finished.emit()

else:

    class NotificationWorker:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            raise RuntimeError("PySide6 is required for the notification worker")

    class NotificationDispatchOnceWorker:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            raise RuntimeError("PySide6 is required for the notification worker")


def _report_detail(report: DispatchReport) -> str:
    if report.claimed == 0:
        return "通知队列为空"
    return f"本轮领取 {report.claimed} 条 · 成功 {report.sent} · 重试 {report.retried} · 失败 {report.failed}"


def _delivery_adapters(
    *,
    webhook_url: str,
    webhook_secret: str,
    email_host: str,
    email_port: int,
    email_username: str,
    email_password: str,
    email_sender: str,
    email_recipients: str | Iterable[str],
    email_use_ssl: bool,
    email_starttls: bool,
    sms_provider: str,
    sms_endpoint: str,
    sms_token: str,
    sms_sender: str,
    sms_template_id: str,
    sms_recipients: str | Iterable[str],
    destinations: Iterable[NotificationDestination],
) -> dict[str, NotificationAdapter]:
    """Build the concrete adapters used by one dispatcher instance."""
    destination_values = tuple(destinations)
    if destination_values:
        adapters: dict[str, NotificationAdapter] = {}
        for destination in destination_values:
            if not destination.enabled:
                continue
            adapters[destination.outbox_channel] = _destination_adapter(destination)
        if not adapters:
            raise ValueError("at least one notification destination must be enabled")
        return adapters

    adapters: dict[str, NotificationAdapter] = {}
    email_configured = bool(email_host.strip() or email_sender.strip() or email_recipients)
    sms_configured = bool(
        sms_endpoint.strip() or sms_token or sms_sender.strip() or sms_template_id.strip() or sms_recipients
    )
    if not webhook_url.strip() and not email_configured and not sms_configured:
        raise ValueError("webhook URL, SMTP email or SMS gateway configuration is required")
    if webhook_url.strip():
        if not webhook_url.strip().lower().startswith("https://"):
            raise ValueError("webhook URL must use HTTPS")
        if not webhook_secret:
            raise ValueError("webhook secret is required")
        adapters["webhook"] = WebhookAdapter(url=webhook_url.strip(), secret=webhook_secret)
    if email_configured:
        adapters["email"] = EmailAdapter(
            host=email_host,
            port=email_port,
            username=email_username,
            password=email_password,
            sender=email_sender,
            recipients=email_recipients,
            use_ssl=email_use_ssl,
            starttls=email_starttls,
        )
    if sms_configured:
        adapters["sms"] = SmsHttpAdapter(
            endpoint=sms_endpoint,
            provider=sms_provider,
            token=sms_token,
            sender=sms_sender,
            template_id=sms_template_id,
            recipients=sms_recipients,
        )
    if not adapters:
        raise ValueError("at least one notification channel must be configured")
    return adapters


def _destination_adapter(destination: NotificationDestination) -> NotificationAdapter:
    settings = destination.settings
    if destination.kind == "webhook":
        return WebhookAdapter(
            url=str(settings.get("url", "")).strip(),
            secret=str(destination.secrets.get("secret", "")),
        )
    if destination.kind == "email":
        return EmailAdapter(
            host=str(settings.get("host", "")),
            port=int(settings.get("port", 587)),
            username=str(settings.get("username", "")),
            password=str(destination.secrets.get("password", "")),
            sender=str(settings.get("sender", "")),
            recipients=settings.get("recipients", ()),
            use_ssl=str(settings.get("security", "starttls")) == "ssl",
            starttls=str(settings.get("security", "starttls")) == "starttls",
        )
    if destination.kind == "sms":
        return SmsHttpAdapter(
            endpoint=str(settings.get("endpoint", "")),
            recipients=settings.get("recipients", ()),
            provider=destination.provider,
            token=str(destination.secrets.get("token", "")),
            sender=str(settings.get("sender", "")),
            template_id=str(settings.get("template_id", "")),
        )
    raise ValueError(f"unsupported notification destination: {destination.kind}")


__all__ = ["NotificationDispatchOnceWorker", "NotificationWorker"]
