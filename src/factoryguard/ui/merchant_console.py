"""Merchant-facing FactoryGuard console.

The console is the product's primary entry point.  It focuses on camera and
area operations plus a grounded text assistant. Camera streams and native
alarms are ingested without loading or running a visual model.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sqlite3
import sys
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

from factoryguard.agent.context import CameraView, build_operational_context
from factoryguard.agent.llm import LLMConfig, OpenAICompatibleClient
from factoryguard.agent.service import AssistantReply, FactoryAssistant, PreparedQuestion
from factoryguard.app import autostart
from factoryguard.app.device_manager import DeviceManager
from factoryguard.config import (
    DEFAULT_CONFIG_PATH,
    AppConfigStore,
    ConfigFileError,
    MerchantConfig,
    migrate_legacy_merchant_config,
)
from factoryguard.devices.base import DeviceCredentials
from factoryguard.devices.gb28181.server import Gb28181Platform
from factoryguard.domain.incidents import (
    SEVERITY_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    Incident,
    IncidentSeverity,
    IncidentStatus,
)
from factoryguard.domain.models import (
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
    EventSource,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.notifications.incidents import (
    DeliveryResult,
    IncidentNotificationConfig,
    IncidentNotifier,
)
from factoryguard.security.credentials import PlaintextCredentialVault, SecretProtectionError
from factoryguard.storage.backup import (
    apply_pending_restore,
    create_backup,
    inspect_backup,
    stage_restore,
)
from factoryguard.storage.incident_export import write_incident_csv
from factoryguard.storage.sqlite import SQLiteStore

from .icon import application_icon
from .merchant_dialogs import (
    AreaDialog,
    CameraAccessDialog,
    CameraEditDialog,
    DeviceStreamResolveWorker,
    LivePreviewDialog,
    LiveWallDialog,
    LiveWallPickerDialog,
    OnvifDiscoveryWorker,
    QuickIncidentDialog,
    ResolvedStream,
    SiteDialog,
    StreamHealthWorker,
    _split_url_credentials,
)
from .merchant_theme import apply_merchant_theme
from .merchant_widgets import (
    AreaCard,
    BrandBanner,
    CameraCard,
    ChatBubble,
    EmptyState,
    IncidentRow,
    MetricCard,
    SectionCard,
    StatusPill,
    clear_layout,
    make_line,
    make_nav_icon,
)
from .qt import HAS_QT, require_qt
from .tray import MerchantTrayController

NOTIFICATION_CHANNEL_LABELS = {
    "webhook": "Webhook",
    "email": "邮件",
    "sms": "短信",
}

AUDIT_ACTOR_LABELS = {
    "merchant-console": "当前用户",
    "native-event-service": "设备告警服务",
    "inquiry-assistant": "现场问询",
    "device-event": "设备事件服务",
    "device-probe": "设备检测服务",
    "config-migration": "配置迁移",
    "retention": "媒体清理",
    "operator": "操作员",
    "system": "系统",
    "demo-seed": "演示数据",
    "tester": "测试",
    "test": "测试",
    "worker": "设备检测服务",
}

AUDIT_ACTION_LABELS = {
    "incident.created": "创建险情",
    "incident.status.changed": "状态变更",
    "incident.location.changed": "补充位置",
}


def _gb_area_choices(topology: Any) -> tuple[tuple[str, Area], ...]:
    """Return user-readable GB import choices with duplicate labels disambiguated."""
    bases = [f"{area.name}（{topology.get_site(area.site_id).name}）" for area in topology.areas]
    counts = Counter(bases)
    return tuple(
        (base if counts[base] == 1 else f"{base} · {area.id}", area)
        for base, area in zip(bases, topology.areas, strict=True)
    )


def _gb_imported_channel_keys(topology: Any) -> set[tuple[str, str]]:
    """Return the (device-id, channel-id) pairs already present in the catalogue.

    Imported GB/T 28181 channels are matched by their source device id and
    channel id so the settings page can tell registered channels apart from
    channels that still need to be imported.
    """
    device_ids: dict[str, str] = {}
    for device in topology.devices:
        if str(device.protocol) != "gb28181":
            continue
        source_id = str(device.device_id or "").strip()
        if source_id:
            device_ids[device.id] = source_id
    keys: set[tuple[str, str]] = set()
    for camera in topology.cameras:
        if camera.device_id is None or camera.device_id not in device_ids:
            continue
        source_id = device_ids[camera.device_id]
        keys.add((source_id, _normalize_channel_id(camera.channel_id)))
    return keys


def _normalize_channel_id(value: str) -> str:
    """Normalise a protocol channel id so alarms match imported channels.

    GB/T 28181 alarms report ``<device-id>:<ss>`` while the catalogue keeps
    the plain device id.  ONVIF property operations add a ``/Profile_x``
    suffix.  Both are removed before comparing.
    """
    text = str(value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[0].strip()
    if "/" in text:
        text = text.split("/", 1)[0].strip()
    return text


def _coerce_event_timestamp(value: Any) -> datetime:
    """Normalise a device event timestamp into an aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _stream_failure_hint(camera: CameraView, error: str) -> str:
    """Explain why a live stream could not be opened, with operator actions."""
    detail = " ".join(str(error or "").split())[:300]
    if not detail:
        detail = "设备没有返回可用地址。"
    protocol = str(camera.protocol or "").lower()
    if protocol == "gb28181":
        advice = (
            "请确认“系统设置”中的国标服务已启用、该设备已注册并出现在"
            "“导入已注册设备”的列表中，然后刷新设备状态再试。"
        )
    elif protocol == "onvif":
        advice = (
            "请确认设备地址、账号和密码正确，设备与本机在同一局域网，"
            "并且摄像头已开启 ONVIF 视频与事件服务，然后刷新设备状态再试。"
        )
    else:
        advice = "请确认码流地址、账号和密码正确，然后刷新设备状态再试。"
    return f"{detail}\n\n{advice}"


def _startup_failure_hint(config: MerchantConfig, exc: Exception, detail: str) -> str:
    """Explain a startup failure in terms the operator can act on."""
    lowered = str(exc).casefold()
    database = Path(config.db).expanduser().resolve() if config.db != ":memory:" else None
    if "not a database" in lowered or "malformed" in lowered or "corrupt" in lowered:
        lines = ["本地业务数据文件已损坏，FactoryGuard 无法启动。", "", f"错误详情：{detail}", ""]
        if database is not None:
            lines.append(f"数据文件：{database}")
        lines.append("恢复方法：")
        lines.append("1. 关闭本程序，把损坏的数据库文件改名保留（例如加 .broken 后缀）；")
        lines.append("2. 在“系统设置”里用最近的备份包执行“恢复备份”；")
        lines.append("3. 若没有备份，重新启动后平台会创建新的数据库，再重新接入设备。")
        lines.append("改名保留原文件可以避免数据被覆盖，便于后续排查。")
        return "\n".join(lines)
    if "locked" in lowered or "busy" in lowered:
        return (
            "业务数据正在被其他程序占用，FactoryGuard 无法启动。\n\n"
            f"{detail}\n\n"
            "请关闭其他正在使用本数据的程序（例如另一个 FactoryGuard 实例），"
            "等待几秒后重新启动。"
        )
    if "unable to open database file" in lowered or "readonly" in lowered:
        return (
            "无法打开业务数据文件，FactoryGuard 无法启动。\n\n"
            f"{detail}\n\n"
            "请检查数据目录是否存在、当前账户是否有读写权限，以及磁盘剩余空间。"
        )
    return (
        "无法读取本地配置或业务数据。\n\n"
        f"{detail}\n\n"
        "请检查配置文件格式、数据目录权限、磁盘空间，以及数据库是否被其他程序占用。"
    )


def _config_recovery_hint(config: MerchantConfig, exc: Exception) -> str:
    """Explain how an operator can recover from an unreadable configuration file."""
    config_path = Path(config.config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    backup_root = (
        Path(".").resolve()
        if config.db == ":memory:"
        else Path(config.db).expanduser().resolve().parent / "backups"
    )
    backups = sorted(backup_root.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    lines = [
        "无法读取本地配置文件，FactoryGuard 暂时无法启动。",
        "",
        f"配置文件位置：{config_path}",
        f"错误详情：{' '.join(str(exc).split())[:300]}",
        "",
    ]
    if backups:
        lines.append("可用的备份文件：")
        for item in backups[:3]:
            lines.append(f"· {item}")
        lines.append("")
        lines.append("恢复方法：关闭本程序，把上面的备份包放到数据目录的 backups 文件夹，")
        lines.append("或先修正配置文件中的 JSON 格式后重新启动。")
    else:
        lines.append("当前没有找到备份包。请检查上面的配置文件内容是否为合法的 JSON 对象，")
        lines.append("修正后重新启动即可；业务数据不会因此丢失。")
    return "\n".join(lines)


def _context_site_name(context: Any, site_id: str | None) -> str:
    """Return the current name of a site, or an empty string when unknown."""
    if not site_id:
        return ""
    try:
        return context.topology.get_site(str(site_id)).name
    except KeyError:
        return ""


def _context_area_name(context: Any, area_id: str | None) -> str:
    """Return the current name of an area, or an empty string when unknown."""
    if not area_id:
        return ""
    try:
        return context.topology.get_area(str(area_id)).name
    except KeyError:
        return ""


def _notification_channel_label(channel: str) -> str:
    value = str(channel or "").strip().lower()
    return NOTIFICATION_CHANNEL_LABELS.get(value, value or "通知渠道")


def _audit_actor_label(actor: str) -> str:
    value = str(actor or "").strip()
    return AUDIT_ACTOR_LABELS.get(value, value or "系统")


def _audit_action_label(action: str) -> str:
    """Return a business label for an audit action shown to operators."""
    return AUDIT_ACTION_LABELS.get(str(action or "").strip(), str(action or "").strip())


def _format_local_timestamp(value: str, *, fallback: str = "时间未知") -> str:
    try:
        timestamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")


if HAS_QT:
    NATIVE_EVENT_TITLES = {
        "alarm": "设备主动报警",
        "videolosssignal": "视频信号丢失",
        "videoloss": "视频信号丢失",
        "tamperdetection": "设备遮挡告警",
        "motiondetect": "移动侦测告警",
        "fielddetection": "区域入侵告警",
    }

    LLM_PROVIDER_PRESETS = {
        "": ("请选择服务商", ""),
        "openai": ("OpenAI", "https://api.openai.com/v1/chat/completions"),
        "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions"),
        "dashscope": (
            "通义千问兼容",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        "custom": ("自定义 / 企业网关", ""),
    }

    from PySide6.QtCore import (
        QEvent,
        QLockFile,
        QObject,
        QRectF,
        QSize,
        QStandardPaths,
        Qt,
        QThread,
        QTimer,
        QUrl,
        Signal,
        Slot,
    )
    from PySide6.QtGui import (
        QCloseEvent,
        QColor,
        QDesktopServices,
        QFont,
        QLinearGradient,
        QPainter,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QStackedWidget,
        QStatusBar,
        QVBoxLayout,
        QWidget,
    )

    def _quit_thread_soon(thread: QThread) -> None:
        def quit_safely() -> None:
            try:
                thread.quit()
            except RuntimeError:
                pass

        QTimer.singleShot(20, quit_safely)

    def _acquire_single_instance_lock(
        path: Path | None = None, *, notify: bool = True
    ) -> QLockFile | None:
        lock_path = path or (
            Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation))
            / "FactoryGuard"
            / "merchant-console.lock"
        )
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if notify:
                QMessageBox.critical(None, "无法启动 FactoryGuard", f"无法创建实例锁目录：{exc}")
            return None
        lock = QLockFile(str(lock_path))
        lock.setStaleLockTime(30000)
        if lock.tryLock(150):
            return lock
        if notify:
            QMessageBox.information(
                None,
                "FactoryGuard 已在运行",
                "为避免重复启动摄像头与告警服务，请从任务栏或系统托盘打开现有窗口。",
            )
        return None

    class StartupSplash(QWidget):
        """Branded startup screen with initialization progress."""

        def __init__(self) -> None:
            super().__init__(
                None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            )
            self.setFixedSize(640, 360)
            self._message = "正在启动…"
            self._progress = 0
            self._started_at = time.monotonic()

        def set_progress(self, message: str, value: int) -> None:
            self._message = message
            self._progress = max(0, min(100, int(value)))
            self.repaint()
            application = QApplication.instance()
            if application is not None:
                application.processEvents()

        def paintEvent(self, _event: Any) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor("#0B1220"))
            gradient.setColorAt(0.65, QColor("#111827"))
            gradient.setColorAt(1.0, QColor("#0F172A"))
            painter.fillRect(self.rect(), gradient)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.drawEllipse(QRectF(425, -95, 300, 300))
            painter.setBrush(QColor(255, 255, 255, 5))
            painter.drawEllipse(QRectF(-90, 230, 260, 260))
            icon = application_icon("深色").pixmap(68, 68)
            painter.drawPixmap(54, 46, icon)
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(QFont("Microsoft YaHei UI", 22, QFont.Bold))
            painter.drawText(139, 76, "FactoryGuard 智守")
            painter.setFont(QFont("Microsoft YaHei UI", 10))
            painter.setPen(QColor("#91ABC0"))
            painter.drawText(141, 101, "多摄像头 · 多区域 · 现场安全管理")
            painter.setFont(QFont("Microsoft YaHei UI", 17, QFont.DemiBold))
            painter.setPen(QColor("#F4FBFD"))
            painter.drawText(54, 183, "正在准备现场值守环境")
            painter.setFont(QFont("Microsoft YaHei UI", 10))
            painter.setPen(QColor("#8EB4C4"))
            painter.drawText(54, 211, "设备接入、区域数据与本地安全服务将在此完成加载")
            track = QRectF(54, 274, 532, 6)
            painter.setBrush(QColor(255, 255, 255, 24))
            painter.drawRoundedRect(track, 3, 3)
            fill = QRectF(54, 274, 532 * self._progress / 100.0, 6)
            painter.setBrush(QColor("#2DD4BF"))
            painter.drawRoundedRect(fill, 3, 3)
            painter.setFont(QFont("Microsoft YaHei UI", 9))
            painter.setPen(QColor("#C6D8E5"))
            painter.drawText(54, 307, self._message)
            painter.setPen(QColor("#6F8FA4"))
            painter.drawText(444, 307, f"FactoryGuard 2.0  ·  {self._progress}%")

    class LLMWorker(QObject):
        completed = Signal(object, str)

        def __init__(self, *, config: LLMConfig, prepared: PreparedQuestion) -> None:
            super().__init__()
            self.config = config
            self.prepared = prepared

        @Slot()
        def run(self) -> None:
            try:
                text = OpenAICompatibleClient(self.config).complete(
                    system_prompt=self.prepared.system_prompt,
                    user_prompt=self.prepared.user_prompt,
                )
                reply = AssistantReply(
                    text=text,
                    provider="llm",
                    incident_id=self.prepared.local_reply.incident_id,
                    citations=self.prepared.citations,
                )
                self.completed.emit(reply, "")
            except Exception as exc:
                self.completed.emit(None, str(exc))

    class LLMTestWorker(QObject):
        completed = Signal(bool, str)

        def __init__(self, config: LLMConfig) -> None:
            super().__init__()
            self.config = config

        @Slot()
        def run(self) -> None:
            try:
                text = OpenAICompatibleClient(self.config).complete(
                    system_prompt="你是连接测试助手。只回复“连接成功”。",
                    user_prompt="请确认连接。",
                )
                self.completed.emit(True, text[:200])
            except Exception as exc:
                self.completed.emit(False, str(exc))

    class IncidentNotificationWorker(QObject):
        completed = Signal(str, object, str)

        def __init__(
            self,
            *,
            config: IncidentNotificationConfig,
            incident: Incident | None = None,
            site_name: str = "",
            area_name: str = "",
            camera_name: str = "",
            test: bool = False,
            channels: tuple[str, ...] | None = None,
        ) -> None:
            super().__init__()
            self.config = config
            self.incident = incident
            self.site_name = site_name
            self.area_name = area_name
            self.camera_name = camera_name
            self.test = test
            self.channels = channels

        @Slot()
        def run(self) -> None:
            try:
                notifier = IncidentNotifier(self.config)
                results = (
                    notifier.send_test()
                    if self.test or self.incident is None
                    else notifier.send(
                        self.incident,
                        site_name=self.site_name,
                        area_name=self.area_name,
                        camera_name=self.camera_name,
                        channels=self.channels,
                    )
                )
                incident_id = self.incident.id if self.incident is not None else ""
                self.completed.emit(incident_id, results, "")
            except Exception as exc:
                incident_id = self.incident.id if self.incident is not None else ""
                selected = (
                    self.channels if self.channels is not None else self.config.enabled_channels
                )
                results = tuple(
                    DeliveryResult(
                        channel,
                        False,
                        f"通知任务异常：{exc.__class__.__name__}",
                    )
                    for channel in selected
                )
                self.completed.emit(incident_id, results, "")

    class MerchantMainWindow(QMainWindow):
        """Primary commercial console for shop owners and property operators."""

        NATIVE_EVENT_ID_CACHE_LIMIT: ClassVar[int] = 10_000
        HISTORY_RETENTION_DAYS: ClassVar[float] = 90.0
        INCIDENT_LIST_LIMIT: ClassVar[int] = 200

        PAGE_META: ClassVar[dict[str, tuple[str, str]]] = {
            "dashboard": ("工作台", "今天的安全态势、设备状态与待处理事项"),
            "live": ("实时画面", "按区域查看所有摄像头，连接状态一目了然"),
            "assistant": ("现场问询", "查询设备、区域和险情记录，也可直接登记险情"),
            "incidents": ("险情中心", "集中处理险情、跟踪进度并保留处置记录"),
            "devices": ("区域与设备", "管理场所、摄像头和 ONVIF / GB/T 28181 接入"),
            "settings": ("系统设置", "设置品牌外观、问询服务与设备接入"),
        }

        def __init__(
            self,
            *,
            config: MerchantConfig | None = None,
            start_hidden: bool = False,
            progress_callback: Callable[[str, int], None] | None = None,
        ) -> None:
            super().__init__()
            self._progress_callback = progress_callback
            self._allow_quit = False
            self._tray: MerchantTrayController | None = None
            self._tray_hint_shown = False
            self._report_progress("读取本地配置…", 8)
            application = QApplication.instance()
            if application is not None:
                apply_merchant_theme(application)
            self._config = config or MerchantConfig()
            self._db_path = str(self._config.db)
            self._report_progress("加载业务数据…", 18)
            self.store = SQLiteStore(self._db_path)
            self.assistant = FactoryAssistant(self.store)
            self._config_store = AppConfigStore(self._config.config_path or None)
            if self._config_store.path.resolve() == (Path.cwd() / DEFAULT_CONFIG_PATH).resolve():
                try:
                    migrate_legacy_merchant_config(self.store, self._config_store)
                except Exception:
                    pass
            self._vault = PlaintextCredentialVault()
            self._start_hidden = start_hidden
            self._current_page = "dashboard"
            self._context = build_operational_context(self.store)
            self._nav_buttons: dict[str, QPushButton] = {}
            self._incident_records: dict[str, Incident] = {}
            self._selected_incident_id: str | None = None
            self._assistant_thread: QThread | None = None
            self._assistant_worker: LLMWorker | None = None
            self._assistant_busy = False
            self._llm_test_thread: QThread | None = None
            self._llm_test_worker: LLMTestWorker | None = None
            self._notification_threads: set[QThread] = set()
            self._notification_workers: set[IncidentNotificationWorker] = set()
            self._notification_retry_inflight: set[str] = set()
            self._notification_channels_inflight: dict[str, tuple[str, ...]] = {}
            self._health_thread: QThread | None = None
            self._health_worker: StreamHealthWorker | None = None
            self._resolve_thread: QThread | None = None
            self._resolve_worker: DeviceStreamResolveWorker | None = None
            self._discovery_thread: QThread | None = None
            self._discovery_worker: OnvifDiscoveryWorker | None = None
            self._device_manager: DeviceManager | None = None
            self._native_subscription_manager: DeviceManager | None = None
            self._gb_platform: Gb28181Platform | None = None
            self._last_discovered_onvif: list[str] = []
            self._native_event_queue: queue.Queue[Any] = queue.Queue()
            self._seen_native_event_keys: set[str] = set()
            self._native_event_key_order: deque[str] = deque()
            self._resize_refresh_timer = QTimer(self)
            self._resize_refresh_timer.setSingleShot(True)
            self._resize_refresh_timer.setInterval(120)
            self._resize_refresh_timer.timeout.connect(
                lambda: self._guarded_slot("界面刷新", self._refresh_dashboard_after_resize)
            )
            self._quit_pending = False
            self._background_failures = 0
            self._last_reported_subscription_error = ""
            self._shutdown_timer = QTimer(self)
            self._shutdown_timer.setInterval(100)
            self._shutdown_timer.timeout.connect(
                lambda: self._guarded_slot("退出收尾", self._poll_background_shutdown)
            )
            self._pending_banner_source: Path | None = None
            self._notification_config = self._load_notification_config()
            self._llm_config = self._load_llm_config()
            self._gb_error = ""
            self._gb_active_port = 0
            self._report_progress("构建商户工作台…", 38)
            self._build_ui()
            self._populate_settings()
            self._load_assistant_history()
            self._report_progress("汇总设备、区域与险情…", 68)
            self._refresh_all()
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setInterval(20000)
            self._refresh_timer.timeout.connect(
                lambda: self._guarded_slot("定时巡检", self._periodic_refresh)
            )
            self._refresh_timer.start()
            self._health_refresh_timer = QTimer(self)
            self._health_refresh_timer.setInterval(10 * 60 * 1000)
            self._health_refresh_timer.timeout.connect(
                lambda: self._guarded_slot("设备状态检测", self._auto_scan_stream_health)
            )
            self._health_refresh_timer.start()
            self._initial_health_timer = QTimer(self)
            self._initial_health_timer.setSingleShot(True)
            self._initial_health_timer.setInterval(60 * 1000)
            self._initial_health_timer.timeout.connect(
                lambda: self._guarded_slot("首次设备检测", self._auto_scan_stream_health)
            )
            self._initial_health_timer.start()
            self._native_event_timer = QTimer(self)
            self._native_event_timer.setInterval(1000)
            self._native_event_timer.timeout.connect(
                lambda: self._guarded_slot("告警接收", self._drain_native_incidents)
            )
            self._native_event_timer.start()
            self._maintenance_timer = QTimer(self)
            self._maintenance_timer.setInterval(6 * 60 * 60 * 1000)
            self._maintenance_timer.timeout.connect(
                lambda: self._guarded_slot("数据清理", self._run_local_maintenance)
            )
            self._maintenance_timer.start()
            self._report_progress("启动后台值守服务…", 86)
            self._restore_gb_service_on_start()
            self._sync_native_subscriptions()
            self._initialize_tray()
            self._retry_failed_notifications()
            self._report_progress("准备就绪", 100)

        def _guarded_slot(self, name: str, action: Callable[[], None]) -> None:
            """Run one background task without letting it stop the whole console.

            A failing timer slot would otherwise abort every remaining task in
            the cycle and, in PySide6, can terminate the application process.
            """
            try:
                action()
            except Exception as exc:
                self._report_background_failure(name, exc)

        def _report_background_failure(self, name: str, exc: Exception) -> None:
            """Surface a background task failure without interrupting the service."""
            detail = " ".join(str(exc).split())[:200]
            self._background_failures += 1
            try:
                self.statusBar().showMessage(
                    f"后台任务“{name}”执行失败，已跳过本轮：{detail}", 8000
                )
            except RuntimeError:
                pass

        def _report_progress(self, message: str, value: int) -> None:
            if self._progress_callback is not None:
                try:
                    self._progress_callback(message, value)
                except Exception:
                    pass

        def _initialize_tray(self) -> None:
            if os.environ.get("FACTORYGUARD_DISABLE_TRAY", "") == "1":
                return
            if not MerchantTrayController.available():
                return
            self._tray = MerchantTrayController(
                on_show=self._show_from_tray,
                on_refresh=self._refresh_all,
                on_report=self._show_and_report,
                on_quit=self.quit_application,
            )
            self._tray.show()

        def _show_from_tray(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def _show_and_report(self) -> None:
            self._show_from_tray()
            QTimer.singleShot(80, self._report_incident)

        def quit_application(self) -> None:
            """Shut the console down, whether or not the window is visible.

            A window that is already hidden in the tray does not receive a
            close event, so the shutdown sequence must not rely on one.
            """
            self._allow_quit = True
            if self.isVisible():
                self.close()
                return
            self._shutdown_for_exit()

        def _shutdown_for_exit(self) -> None:
            """Release timers, background workers and the database handle."""
            self._refresh_timer.stop()
            self._native_event_timer.stop()
            self._maintenance_timer.stop()
            self._health_refresh_timer.stop()
            self._initial_health_timer.stop()
            self._stop_native_subscriptions()
            self._stop_gb_service()
            self._request_background_shutdown()
            if self._running_background_threads():
                self._quit_pending = True
                self.statusBar().showMessage("正在安全结束后台任务，请稍候…", 0)
                self._shutdown_timer.start()
                return
            self._shutdown_timer.stop()
            if self._tray is not None:
                self._tray.hide()
                self._tray = None
            try:
                self.store.close()
            finally:
                self._quit_pending = False
                self._allow_quit = True
                self.close()

        def _running_background_threads(self) -> tuple[QThread, ...]:
            running: list[QThread] = []
            for thread in (
                self._assistant_thread,
                self._health_thread,
                self._resolve_thread,
                self._discovery_thread,
                self._llm_test_thread,
                *tuple(self._notification_threads),
            ):
                if thread is None:
                    continue
                try:
                    if thread.isRunning():
                        running.append(thread)
                except RuntimeError:
                    continue
            return tuple(running)

        def _request_background_shutdown(self) -> None:
            if self._health_worker is not None:
                try:
                    self._health_worker.request_stop()
                except RuntimeError:
                    self._health_worker = None
            for thread in self._running_background_threads():
                try:
                    thread.quit()
                except RuntimeError:
                    continue
            # Native subscriptions run on plain threads, so they are not part of
            # the QThread list above; stop them explicitly or their database
            # handle would stay open after the console exits.
            self._stop_native_subscriptions()

        def _poll_background_shutdown(self) -> None:
            if self._running_background_threads():
                return
            self._shutdown_timer.stop()
            self._quit_pending = False
            if self.isVisible():
                self.close()
                return
            # A hidden window never receives a close event, so the remaining
            # resources are released directly here.
            self._stop_native_subscriptions()
            self._stop_gb_service()
            if self._tray is not None:
                self._tray.hide()
                self._tray = None
            self.store.close()

        def can_background(self) -> bool:
            return self._tray is not None

        def notify_background_start(self) -> None:
            if self._tray is not None:
                self._tray.notify(
                    "FactoryGuard 已在后台值守", "设备、险情与告警汇总服务将继续运行。"
                )

        def _build_ui(self) -> None:
            self.setWindowTitle("FactoryGuard 智守 · 多区域安防工作台")
            self.setMinimumSize(1120, 640)
            screen = QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self.resize(
                    min(1520, max(1280, int(available.width() * 0.9))),
                    min(980, max(680, int(available.height() * 0.88))),
                )
            else:
                self.resize(1440, 900)
            root = QWidget()
            root.setObjectName("appRoot")
            self.setCentralWidget(root)
            root_layout = QVBoxLayout(root)
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(0)
            self.banner = BrandBanner()
            root_layout.addWidget(self.banner)
            body = QWidget()
            body.setObjectName("appRoot")
            body_layout = QHBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 0)
            body_layout.setSpacing(0)
            body_layout.addWidget(self._build_sidebar())
            body_layout.addWidget(self._build_main_area(), 1)
            root_layout.addWidget(body, 1)
            self._apply_branding_settings()
            self.setStatusBar(QStatusBar(self))
            self.statusBar().showMessage("本地数据已就绪")

        def _build_sidebar(self) -> QWidget:
            sidebar = QFrame()
            sidebar.setObjectName("sidebar")
            sidebar.setFixedWidth(218)
            layout = QVBoxLayout(sidebar)
            layout.setContentsMargins(16, 24, 16, 16)
            layout.setSpacing(6)
            overline = QLabel("现场管理")
            overline.setObjectName("sidebarOverline")
            heading = QLabel("安全值守导航")
            heading.setObjectName("sidebarTitle")
            layout.addWidget(overline)
            layout.addWidget(heading)
            layout.addSpacing(18)
            self._add_nav_section(layout, "工作区")
            self._add_nav_button(layout, "dashboard", "dashboard", "工作台")
            self._add_nav_button(layout, "live", "live", "实时画面")
            self._add_nav_button(layout, "assistant", "inquiry", "现场问询")
            self._add_nav_button(layout, "incidents", "incident", "险情中心")
            layout.addSpacing(6)
            self._add_nav_section(layout, "管理")
            self._add_nav_button(layout, "devices", "devices", "区域与设备")
            self._add_nav_button(layout, "settings", "settings", "系统设置")
            layout.addStretch(1)
            divider = QFrame()
            divider.setFixedHeight(1)
            divider.setStyleSheet("background: #273244;")
            layout.addWidget(divider)
            footer = QWidget()
            footer.setObjectName("sidebarStatusCard")
            assistant_layout = QVBoxLayout(footer)
            assistant_layout.setContentsMargins(12, 11, 12, 11)
            assistant_layout.setSpacing(4)
            assistant_title = QLabel("系统值守")
            assistant_title.setObjectName("sidebarAssistantTitle")
            self.sidebar_state_text = QLabel("正在汇总设备与险情")
            self.sidebar_state_text.setObjectName("sidebarAssistantText")
            self.sidebar_state_text.setWordWrap(True)
            self.sidebar_ai_state = QLabel()
            self.sidebar_ai_state.setObjectName("sidebarAssistantState")
            assistant_layout.addWidget(assistant_title)
            assistant_layout.addWidget(self.sidebar_state_text)
            assistant_layout.addWidget(self.sidebar_ai_state)
            layout.addWidget(footer)
            version = QLabel("FactoryGuard v2.0")
            version.setObjectName("brandTagline")
            version.setAlignment(Qt.AlignRight)
            layout.addWidget(version)
            return sidebar

        def _add_nav_section(self, layout: QVBoxLayout, title: str) -> None:
            label = QLabel(title.upper())
            label.setObjectName("navSection")
            layout.addWidget(label)

        def _add_nav_button(self, layout: QVBoxLayout, key: str, icon: str, label: str) -> None:
            button = QPushButton(label)
            button.setObjectName("navItem")
            button.setIcon(make_nav_icon(icon))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=key: self._switch_page(page))
            self._nav_buttons[key] = button
            layout.addWidget(button)

        def _build_main_area(self) -> QWidget:
            area = QWidget()
            area.setObjectName("pageCanvas")
            layout = QVBoxLayout(area)
            layout.setContentsMargins(30, 20, 30, 12)
            layout.setSpacing(14)
            topbar = QFrame()
            topbar.setObjectName("topBar")
            top_layout = QHBoxLayout(topbar)
            top_layout.setContentsMargins(0, 0, 0, 0)
            top_layout.setSpacing(10)
            titles = QVBoxLayout()
            titles.setSpacing(2)
            self.page_title = QLabel("工作台")
            self.page_title.setObjectName("pageTitle")
            self.page_subtitle = QLabel("今天的安全态势、设备状态与待处理事项")
            self.page_subtitle.setObjectName("pageSubtitle")
            titles.addWidget(self.page_title)
            titles.addWidget(self.page_subtitle)
            top_layout.addLayout(titles, 1)
            self.today_label = QLabel(self._today_text())
            self.today_label.setObjectName("todayLabel")
            self.today_label.setAlignment(Qt.AlignCenter)
            top_layout.addWidget(self.today_label)
            refresh = QPushButton("刷新")
            refresh.setObjectName("toolButton")
            refresh.clicked.connect(self._refresh_all)
            top_layout.addWidget(refresh)
            self.top_ai_button = QPushButton("现场问询")
            self.top_ai_button.setObjectName("secondary")
            self.top_ai_button.clicked.connect(lambda: self._switch_page("assistant"))
            top_layout.addWidget(self.top_ai_button)
            self.report_button = QPushButton("＋ 上报险情")
            self.report_button.setObjectName("primary")
            self.report_button.clicked.connect(self._report_incident)
            top_layout.addWidget(self.report_button)
            layout.addWidget(topbar)
            self.page_stack = QStackedWidget()
            self.page_stack.setObjectName("pageCanvas")
            self._page_widgets: dict[str, QWidget] = {}
            self._page_widgets["dashboard"] = self._scroll_page(self._build_dashboard_page())
            self._page_widgets["live"] = self._scroll_page(self._build_live_page())
            self._page_widgets["assistant"] = self._scroll_page(self._build_assistant_page())
            self._page_widgets["incidents"] = self._scroll_page(self._build_incidents_page())
            self._page_widgets["devices"] = self._scroll_page(self._build_devices_page())
            self._page_widgets["settings"] = self._scroll_page(self._build_settings_page())
            for page in self._page_widgets.values():
                self.page_stack.addWidget(page)
            layout.addWidget(self.page_stack, 1)
            return area

        def _resolve_banner_path(self, value: str) -> Path | None:
            path_text = value.strip()
            if not path_text:
                return None
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                path = self._config_store.path.parent / path
            return path.resolve()

        def _branding_height(self, value: Any) -> int:
            try:
                height = int(value)
            except (TypeError, ValueError):
                height = BrandBanner.DEFAULT_HEIGHT
            return max(BrandBanner.MIN_HEIGHT, min(BrandBanner.MAX_HEIGHT, height))

        def _apply_branding_settings(self) -> None:
            branding = self._config_store.section("branding")
            path = self._resolve_banner_path(str(branding.get("banner_svg", "")))
            height = self._branding_height(branding.get("banner_height"))
            loaded = bool(
                path and path.is_file() and self.banner.load_svg(str(path), height=height)
            )
            if not loaded:
                self.banner.show_default(height=height)
            self.banner.set_context(self._banner_site_name(), self._context.dashboard_summary())

        def _banner_site_name(self) -> str:
            """Describe the managed sites for the top banner without hiding extra sites."""
            sites = self._context.topology.sites
            if not sites:
                return "本地安防管理"
            if len(sites) == 1:
                return sites[0].name
            return f"{sites[0].name} 等 {len(sites)} 个场所"

        def _scroll_page(self, content: QWidget) -> QScrollArea:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            return scroll

        def _today_text(self) -> str:
            now = datetime.now().astimezone()
            weekdays = "一二三四五六日"
            return f"{now.year}年{now.month}月{now.day}日  星期{weekdays[now.weekday()]}"

        def _switch_page(self, key: str) -> None:
            if key not in self._page_widgets:
                return
            self._current_page = key
            self.page_stack.setCurrentWidget(self._page_widgets[key])
            self.page_title.setText(self.PAGE_META[key][0])
            self.page_subtitle.setText(self.PAGE_META[key][1])
            self.top_ai_button.setVisible(key != "assistant")
            for name, button in self._nav_buttons.items():
                button.setChecked(name == key)
            if key == "dashboard":
                self._refresh_dashboard()
            elif key == "live":
                self._refresh_live()
            elif key == "incidents":
                self._refresh_incidents()
            elif key == "devices":
                self._refresh_devices()
            elif key == "assistant":
                self._refresh_assistant_context()
            elif key == "settings":
                self._refresh_runtime_labels()
            self._refresh_backup_status()

        def _build_dashboard_page(self) -> QWidget:
            page = QWidget()
            page.setObjectName("pageCanvas")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 2, 4, 22)
            layout.setSpacing(14)
            hero = QFrame()
            hero.setObjectName("heroCard")
            hero.setMinimumHeight(122)
            hero_layout = QHBoxLayout(hero)
            hero_layout.setContentsMargins(24, 21, 24, 21)
            hero_layout.setSpacing(24)
            left = QVBoxLayout()
            left.setSpacing(5)
            eyebrow = QLabel("今日值守概览")
            eyebrow.setObjectName("heroEyebrow")
            self.hero_title = QLabel("正在读取现场…")
            self.hero_title.setObjectName("heroTitle")
            self.hero_title.setWordWrap(True)
            self.hero_text = QLabel("平台会汇总摄像头、区域和险情状态。")
            self.hero_text.setObjectName("heroText")
            self.hero_text.setWordWrap(True)
            left.addWidget(eyebrow)
            left.addWidget(self.hero_title)
            left.addWidget(self.hero_text)
            left.addStretch(1)
            hero_layout.addLayout(left, 1)
            self.hero_state_pill = StatusPill("检查中", "unknown")
            hero_layout.addWidget(self.hero_state_pill, 0, Qt.AlignTop)
            layout.addWidget(hero)

            metric_row = QGridLayout()
            metric_row.setHorizontalSpacing(12)
            metric_row.setVerticalSpacing(12)
            self.metric_cameras = MetricCard("摄像头", "▣", "teal")
            self.metric_areas = MetricCard("管理区域", "◇", "blue")
            self.metric_incidents = MetricCard("未关闭险情", "!", "red")
            self.metric_online_rate = MetricCard("设备在线率", "✓", "orange")
            for column, card in enumerate(
                (
                    self.metric_cameras,
                    self.metric_areas,
                    self.metric_incidents,
                    self.metric_online_rate,
                )
            ):
                metric_row.addWidget(card, 0, column)
                metric_row.setColumnStretch(column, 1)
            layout.addLayout(metric_row)

            body = QGridLayout()
            body.setHorizontalSpacing(14)
            body.setVerticalSpacing(14)
            camera_panel = SectionCard()
            view_all = QPushButton("查看全部")
            view_all.setObjectName("linkButton")
            view_all.clicked.connect(lambda: self._switch_page("live"))
            camera_panel.add_header("重点区域画面", "摄像头接入后可在这里快速查看", view_all)
            self.dashboard_camera_grid = QGridLayout()
            self.dashboard_camera_grid.setHorizontalSpacing(10)
            self.dashboard_camera_grid.setVerticalSpacing(10)
            camera_panel.body.addLayout(self.dashboard_camera_grid)
            body.addWidget(camera_panel, 0, 0)
            incident_panel = SectionCard()
            open_all = QPushButton("进入险情中心")
            open_all.setObjectName("linkButton")
            open_all.clicked.connect(lambda: self._switch_page("incidents"))
            incident_panel.add_header("待处理险情", "按严重程度和发生时间排序", open_all)
            self.dashboard_incident_layout = QVBoxLayout()
            self.dashboard_incident_layout.setSpacing(8)
            incident_panel.body.addLayout(self.dashboard_incident_layout)
            incident_panel.body.addStretch(1)
            body.addWidget(incident_panel, 0, 1)
            body.setColumnStretch(0, 3)
            body.setColumnStretch(1, 2)
            layout.addLayout(body)
            layout.addStretch(1)
            return page

        def _build_live_page(self) -> QWidget:
            page = QWidget()
            page.setObjectName("pageCanvas")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 2, 4, 22)
            layout.setSpacing(14)
            toolbar = SectionCard()
            row = QHBoxLayout()
            row.setSpacing(9)
            self.live_site_filter = QComboBox()
            # The filter row has to fit a 1120-pixel window, so the pickers
            # may shrink and the search box takes the remaining space.
            self.live_site_filter.setMinimumWidth(78)
            self.live_site_filter.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            self.live_area_filter = QComboBox()
            self.live_area_filter.setMinimumWidth(88)
            self.live_area_filter.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            self.live_status_filter = QComboBox()
            self.live_status_filter.addItem("全部状态", "all")
            self.live_status_filter.addItem("在线", "online")
            self.live_status_filter.addItem("离线", "offline")
            self.live_status_filter.addItem("待检测", "unknown")
            self.live_status_filter.addItem("已停用", "disabled")
            self.live_search = QLineEdit()
            self.live_search.setPlaceholderText("搜索区域或摄像头名称")
            self.live_count = QLabel()
            self.live_count.setObjectName("muted")
            # The filter row is tight on small screens, so the count keeps a
            # narrow fixed width and the row can still shrink.
            self.live_count.setMinimumWidth(58)
            self.live_count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.live_wall_button = QPushButton("多路查看")
            self.live_wall_button.setObjectName("secondary")
            self.live_wall_button.setEnabled(False)
            self.live_wall_button.setToolTip("至少需要一路可播放码流")
            self.live_wall_button.clicked.connect(self._open_live_wall)
            self.live_scan_button = QPushButton("刷新设备状态")
            self.live_scan_button.setObjectName("secondary")
            self.live_scan_button.clicked.connect(self._scan_stream_health)
            add_camera = QPushButton("＋ 添加摄像头")
            add_camera.setObjectName("primary")
            add_camera.clicked.connect(self._add_camera)
            row.addWidget(self.live_site_filter)
            row.addWidget(self.live_area_filter)
            row.addWidget(self.live_status_filter)
            row.addWidget(self.live_search, 1)
            row.addWidget(self.live_count)
            row.addWidget(self.live_wall_button)
            row.addWidget(self.live_scan_button)
            row.addWidget(add_camera)
            toolbar.body.addLayout(row)
            layout.addWidget(toolbar)
            self.live_page_empty = EmptyState(
                title="还没有摄像头",
                text="先创建区域，再添加 ONVIF、GB/T 28181 或 RTSP 摄像头。",
                action_text="添加第一台摄像头",
            )
            self.live_page_empty.action_button.clicked.connect(self._add_camera)
            layout.addWidget(self.live_page_empty)
            self.live_grid_widget = QWidget()
            self.live_grid_widget.setObjectName("pageCanvas")
            self.live_grid = QGridLayout(self.live_grid_widget)
            self.live_grid.setContentsMargins(0, 0, 0, 0)
            self.live_grid.setHorizontalSpacing(12)
            self.live_grid.setVerticalSpacing(12)
            layout.addWidget(self.live_grid_widget)
            layout.addStretch(1)
            self.live_area_filter.currentIndexChanged.connect(self._refresh_live)
            self.live_status_filter.currentIndexChanged.connect(self._refresh_live)
            self.live_site_filter.currentIndexChanged.connect(self._on_live_site_changed)
            self.live_search.textChanged.connect(self._refresh_live)
            return page

        def _build_assistant_page(self) -> QWidget:
            page = QWidget()
            page.setObjectName("pageCanvas")
            outer = QHBoxLayout(page)
            outer.setContentsMargins(0, 2, 4, 22)
            outer.setSpacing(14)
            chat_card = SectionCard()
            chat_card.setMinimumHeight(590)
            chat_card.add_header(
                "问询记录", "依据平台设备、区域和险情记录回答；可启用可选的文字问询服务"
            )
            self.chat_scroll = QScrollArea()
            self.chat_scroll.setWidgetResizable(True)
            self.chat_scroll.setMinimumHeight(390)
            self.chat_canvas = QWidget()
            self.chat_canvas.setObjectName("pageCanvas")
            self.chat_layout = QVBoxLayout(self.chat_canvas)
            self.chat_layout.setContentsMargins(2, 4, 8, 4)
            self.chat_layout.setSpacing(9)
            self.chat_layout.addStretch(1)
            self.chat_scroll.setWidget(self.chat_canvas)
            chat_card.body.addWidget(self.chat_scroll, 1)
            composer = QFrame()
            composer.setObjectName("assistantComposer")
            composer_layout = QVBoxLayout(composer)
            composer_layout.setContentsMargins(11, 9, 11, 9)
            composer_layout.setSpacing(7)
            self.assistant_input = QPlainTextEdit()
            self.assistant_input.setPlaceholderText(
                "查询设备、区域或险情情况，或输入“上报险情：仓库北门有人闯入”…"
            )
            self.assistant_input.setMaximumHeight(72)
            self.assistant_input.installEventFilter(self)
            input_actions = QHBoxLayout()
            hint = QLabel("Ctrl + Enter 发送")
            hint.setObjectName("smallLabel")
            self.assistant_send = QPushButton("发送")
            self.assistant_send.setObjectName("primary")
            self.assistant_send.clicked.connect(self._send_assistant_question)
            self.assistant_input.textChanged.connect(self._update_assistant_send_state)
            input_actions.addWidget(hint)
            input_actions.addStretch(1)
            input_actions.addWidget(self.assistant_send)
            composer_layout.addWidget(self.assistant_input)
            composer_layout.addLayout(input_actions)
            chat_card.body.addWidget(composer)
            outer.addWidget(chat_card, 1)

            context_card = SectionCard(object_name="contextCard")
            context_card.setFixedWidth(286)
            context_card.add_header("问询范围", "回答只会引用以下平台数据")
            self.assistant_context_cameras = QLabel("—")
            self.assistant_context_areas = QLabel("—")
            self.assistant_context_incidents = QLabel("—")
            self.assistant_context_model = QLabel("—")
            for label, widget in (
                ("摄像头", self.assistant_context_cameras),
                ("区域", self.assistant_context_areas),
                ("未关闭险情", self.assistant_context_incidents),
                ("回答来源", self.assistant_context_model),
            ):
                row = QHBoxLayout()
                key = QLabel(label)
                key.setObjectName("smallLabel")
                widget.setStyleSheet("font-weight: 650; color: #26364E;")
                widget.setWordWrap(True)
                row.addWidget(key)
                row.addStretch(1)
                row.addWidget(widget)
                context_card.body.addLayout(row)
            self.assistant_config_button = QPushButton("配置问询服务")
            self.assistant_config_button.setObjectName("linkButton")
            self.assistant_config_button.clicked.connect(lambda: self._switch_page("settings"))
            context_card.body.addWidget(self.assistant_config_button, 0, Qt.AlignLeft)
            context_card.body.addWidget(make_line())
            suggestion_title = QLabel("可以这样问")
            suggestion_title.setObjectName("sectionTitle")
            context_card.body.addWidget(suggestion_title)
            for text in (
                "现在有哪些险情需要处理？",
                "哪些摄像头离线了？",
                "总结一下今天的现场情况",
                "仓库区域现在怎么样？",
            ):
                button = QPushButton(text)
                button.setObjectName("suggestionChip")
                button.clicked.connect(lambda _checked=False, value=text: self._quick_ask(value))
                context_card.body.addWidget(button)
            context_card.body.addWidget(make_line())
            report = QPushButton("通过问询登记险情")
            report.setObjectName("secondary")
            report.clicked.connect(lambda: self._quick_ask("上报险情："))
            context_card.body.addWidget(report)
            context_card.body.addStretch(1)
            outer.addWidget(context_card)
            self._update_assistant_send_state()
            return page

        def _build_incidents_page(self) -> QWidget:
            page = QWidget()
            page.setObjectName("pageCanvas")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 2, 4, 22)
            layout.setSpacing(14)
            toolbar = SectionCard()
            filters = QHBoxLayout()
            filters.setSpacing(9)
            self.incident_area_filter = QComboBox()
            # Keep the filter row inside a 1120-pixel window.
            self.incident_area_filter.setMinimumWidth(92)
            self.incident_area_filter.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon
            )
            self.incident_severity_filter = QComboBox()
            self.incident_severity_filter.addItem("全部等级", "all")
            for value in ("critical", "high", "medium", "low"):
                self.incident_severity_filter.addItem(
                    SEVERITY_LABELS[IncidentSeverity(value)], value
                )
            self.incident_status_filter = QComboBox()
            self.incident_status_filter.addItem("全部状态", "all")
            for value in IncidentStatus:
                self.incident_status_filter.addItem(STATUS_LABELS[value], value.value)
            self.incident_search = QLineEdit()
            self.incident_search.setPlaceholderText("搜索险情、区域、摄像头或状态")
            filters.addWidget(self.incident_area_filter)
            filters.addWidget(self.incident_severity_filter)
            filters.addWidget(self.incident_status_filter)
            filters.addWidget(self.incident_search, 1)
            self.incident_count_label = QLabel()
            self.incident_count_label.setObjectName("muted")
            filters.addWidget(self.incident_count_label)
            export_button = QPushButton("导出清单")
            export_button.setObjectName("secondary")
            export_button.clicked.connect(self._export_incidents)
            filters.addWidget(export_button)
            toolbar.body.addLayout(filters)
            layout.addWidget(toolbar)
            body = QHBoxLayout()
            body.setSpacing(14)
            list_card = SectionCard()
            list_card.add_header("险情列表", "点击任意险情查看详情和处置动作")
            self.incident_list_layout = QVBoxLayout()
            self.incident_list_layout.setSpacing(8)
            list_card.body.addLayout(self.incident_list_layout)
            list_card.body.addStretch(1)
            body.addWidget(list_card, 3)
            detail_card = SectionCard(object_name="contextCard")
            detail_card.setMinimumWidth(330)
            detail_card.setMaximumWidth(410)
            detail_card.add_header("险情详情", "处置结果会写入本地审计记录")
            self.incident_detail_title = QLabel("请选择一条险情")
            self.incident_detail_title.setObjectName("sectionTitle")
            self.incident_detail_title.setWordWrap(True)
            self.incident_detail_meta = QLabel("")
            self.incident_detail_meta.setObjectName("cardMuted")
            self.incident_detail_meta.setWordWrap(True)
            self.incident_detail_description = QLabel("")
            self.incident_detail_description.setWordWrap(True)
            self.incident_detail_description.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail_card.body.addWidget(self.incident_detail_title)
            detail_card.body.addWidget(self.incident_detail_meta)
            detail_card.body.addWidget(make_line())
            detail_card.body.addWidget(self.incident_detail_description)
            self.incident_notification_label = QLabel("")
            self.incident_notification_label.setObjectName("cardMuted")
            self.incident_notification_label.setWordWrap(True)
            detail_card.body.addWidget(self.incident_notification_label)
            self.incident_notification_detail_label = QLabel("")
            self.incident_notification_detail_label.setObjectName("errorHint")
            self.incident_notification_detail_label.setWordWrap(True)
            self.incident_notification_detail_label.setTextInteractionFlags(
                Qt.TextSelectableByMouse
            )
            self.incident_notification_detail_label.setVisible(False)
            detail_card.body.addWidget(self.incident_notification_detail_label)
            history_title = QLabel("处置记录")
            history_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #2B3B33;")
            detail_card.body.addWidget(history_title)
            self.incident_audit_label = QLabel("")
            self.incident_audit_label.setObjectName("cardMuted")
            self.incident_audit_label.setWordWrap(True)
            self.incident_audit_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail_card.body.addWidget(self.incident_audit_label)
            detail_card.body.addStretch(1)
            # An incident reported from the field often has no place yet,
            # so the operator can file it to the right area afterwards.
            self.incident_area_edit = QComboBox()
            self.incident_area_edit.setObjectName("plainInput")
            self.incident_area_edit.setToolTip("选择这条险情发生的区域，便于按区域统计和导出")
            self.incident_area_edit.currentIndexChanged.connect(self._on_incident_area_pick)
            self.incident_area_button = QPushButton("补充位置")
            self.incident_area_button.setObjectName("secondary")
            self.incident_area_button.clicked.connect(self._assign_incident_area)
            location_row = QHBoxLayout()
            location_row.setSpacing(8)
            location_row.addWidget(self.incident_area_edit, 1)
            location_row.addWidget(self.incident_area_button)
            detail_card.body.addLayout(location_row)
            self.incident_note_edit = QLineEdit()
            self.incident_note_edit.setPlaceholderText("处置说明（可选），例如：已通知保安到场")
            self.incident_note_edit.setObjectName("plainInput")
            self.incident_note_edit.setMaxLength(200)
            detail_card.body.addWidget(self.incident_note_edit)
            self.incident_ack_button = QPushButton("确认处理")
            self.incident_ack_button.setObjectName("secondary")
            self.incident_ack_button.clicked.connect(
                lambda: self._set_incident_status(IncidentStatus.ACKNOWLEDGED)
            )
            self.incident_resolve_button = QPushButton("标记已解决")
            self.incident_resolve_button.setObjectName("primary")
            self.incident_resolve_button.clicked.connect(
                lambda: self._set_incident_status(IncidentStatus.RESOLVED)
            )
            self.incident_false_button = QPushButton("标记误报")
            self.incident_false_button.setObjectName("danger")
            self.incident_false_button.clicked.connect(
                lambda: self._set_incident_status(IncidentStatus.FALSE_ALARM)
            )
            self.incident_reopen_button = QPushButton("重新打开")
            self.incident_reopen_button.setObjectName("secondary")
            self.incident_reopen_button.clicked.connect(self._reopen_incident)
            self.incident_reopen_button.setVisible(False)
            self.incident_retry_notification_button = QPushButton("重试险情通知")
            self.incident_retry_notification_button.setObjectName("secondary")
            self.incident_retry_notification_button.clicked.connect(
                self._retry_incident_notification
            )
            detail_card.body.addWidget(self.incident_ack_button)
            detail_card.body.addWidget(self.incident_resolve_button)
            detail_card.body.addWidget(self.incident_false_button)
            detail_card.body.addWidget(self.incident_reopen_button)
            detail_card.body.addWidget(self.incident_retry_notification_button)
            body.addWidget(detail_card, 2)
            layout.addLayout(body)
            layout.addStretch(1)
            self.incident_area_filter.currentIndexChanged.connect(self._refresh_incidents)
            self.incident_severity_filter.currentIndexChanged.connect(self._refresh_incidents)
            self.incident_status_filter.currentIndexChanged.connect(self._refresh_incidents)
            self.incident_search.textChanged.connect(self._refresh_incidents)
            self._set_incident_actions_enabled(False)
            return page

        def _build_devices_page(self) -> QWidget:
            page = QWidget()
            page.setObjectName("pageCanvas")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 2, 4, 22)
            layout.setSpacing(14)
            toolbar = SectionCard()
            row = QHBoxLayout()
            intro = QVBoxLayout()
            title = QLabel("区域与摄像头")
            title.setObjectName("sectionTitle")
            hint = QLabel("按实际位置组织设备。支持 ONVIF、GB/T 28181 与 RTSP。")
            hint.setObjectName("sectionCaption")
            hint.setWordWrap(True)
            intro.addWidget(title)
            intro.addWidget(hint)
            self.onvif_scan_button = QPushButton("扫描 ONVIF")
            self.onvif_scan_button.setObjectName("secondary")
            self.onvif_scan_button.clicked.connect(self._scan_onvif)
            add_site = QPushButton("＋ 新建场所")
            add_site.setObjectName("secondary")
            add_site.clicked.connect(self._add_site)
            add_area = QPushButton("＋ 新建区域")
            add_area.setObjectName("secondary")
            add_area.clicked.connect(self._add_area)
            add_camera = QPushButton("＋ 添加摄像头")
            add_camera.setObjectName("primary")
            add_camera.clicked.connect(self._add_camera)
            row.addLayout(intro, 1)
            row.addWidget(self.onvif_scan_button)
            row.addWidget(add_site)
            row.addWidget(add_area)
            row.addWidget(add_camera)
            toolbar.body.addLayout(row)
            self.site_bar = QWidget()
            site_bar_layout = QHBoxLayout(self.site_bar)
            site_bar_layout.setContentsMargins(0, 0, 0, 0)
            site_bar_layout.setSpacing(8)
            site_bar_layout.addWidget(QLabel("我的场所"))
            site_bar_layout.addStretch(1)
            toolbar.body.addWidget(self.site_bar)
            device_filters = QHBoxLayout()
            device_filters.setSpacing(9)
            self.devices_area_filter = QComboBox()
            self.devices_area_filter.setMinimumWidth(145)
            self.devices_search = QLineEdit()
            self.devices_search.setPlaceholderText("搜索摄像头名称或接入地址")
            self.devices_count_label = QLabel()
            self.devices_count_label.setObjectName("muted")
            device_filters.addWidget(self.devices_area_filter)
            device_filters.addWidget(self.devices_search, 1)
            device_filters.addWidget(self.devices_count_label)
            toolbar.body.addLayout(device_filters)
            self.devices_area_filter.currentIndexChanged.connect(self._refresh_devices)
            self.devices_search.textChanged.connect(self._refresh_devices)
            layout.addWidget(toolbar)
            areas_card = SectionCard()
            areas_card.add_header("场地区域", "每个区域可以独立查看设备与险情")
            self.area_grid = QGridLayout()
            self.area_grid.setHorizontalSpacing(10)
            self.area_grid.setVerticalSpacing(10)
            areas_card.body.addLayout(self.area_grid)
            layout.addWidget(areas_card)
            cameras_card = SectionCard()
            cameras_card.add_header("全部摄像头", "跨区域查看看板；点击“查看画面”进入实时播放")
            self.devices_camera_grid = QGridLayout()
            self.devices_camera_grid.setHorizontalSpacing(10)
            self.devices_camera_grid.setVerticalSpacing(10)
            cameras_card.body.addLayout(self.devices_camera_grid)
            layout.addWidget(cameras_card)
            layout.addStretch(1)
            return page

        def _settings_info_panel(self, title: str, subtitle: str, items: tuple[str, ...]) -> QFrame:
            panel = QFrame()
            panel.setObjectName("settingsInfo")
            panel.setFixedWidth(270)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(15, 14, 15, 14)
            layout.setSpacing(8)
            heading = QLabel(title)
            heading.setStyleSheet("font-size: 13px; font-weight: 700; color: #203047;")
            hint = QLabel(subtitle)
            hint.setObjectName("smallLabel")
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            layout.addWidget(make_line())
            for index, text in enumerate(items, start=1):
                row = QHBoxLayout()
                number = QLabel(str(index))
                number.setAlignment(Qt.AlignCenter)
                number.setFixedSize(20, 20)
                number.setStyleSheet(
                    "background: #E7F8F5; color: #08776D; border-radius: 10px; "
                    "font-size: 10px; font-weight: 700;"
                )
                label = QLabel(text)
                label.setObjectName("smallLabel")
                label.setWordWrap(True)
                row.addWidget(number, 0, Qt.AlignTop)
                row.addWidget(label, 1)
                layout.addLayout(row)
            layout.addStretch(1)
            return panel

        def _build_settings_page(self) -> QWidget:
            page = QWidget()
            page.setObjectName("pageCanvas")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 2, 4, 22)
            layout.setSpacing(14)
            banner_card = SectionCard()
            self.banner_status_badge = StatusPill("未配置", "unknown")
            banner_card.add_header(
                "顶部品牌横幅",
                "使用商户自己的 SVG 替换默认横幅；建议宽高比为 8:1 至 12:1。",
                self.banner_status_badge,
            )
            banner_form_container = QWidget()
            banner_form_container.setMaximumWidth(760)
            banner_form = QFormLayout(banner_form_container)
            banner_form.setContentsMargins(0, 0, 0, 0)
            self.banner_path_edit = QLineEdit()
            self.banner_path_edit.setReadOnly(True)
            self.banner_path_edit.setPlaceholderText("尚未选择 SVG 文件")
            self.banner_height_spin = QSpinBox()
            self.banner_height_spin.setRange(BrandBanner.MIN_HEIGHT, BrandBanner.MAX_HEIGHT)
            self.banner_height_spin.setSuffix(" px")
            banner_form.addRow("SVG 文件", self.banner_path_edit)
            banner_form.addRow("显示高度", self.banner_height_spin)
            banner_card.body.addWidget(banner_form_container)
            self.banner_preview = BrandBanner()
            banner_card.body.addWidget(self.banner_preview)
            self.banner_status_label = QLabel("SVG 会自动裁切铺满横幅，且不会拉伸变形。")
            self.banner_status_label.setObjectName("cardMuted")
            self.banner_status_label.setWordWrap(True)
            banner_card.body.addWidget(self.banner_status_label)
            banner_actions = QHBoxLayout()
            choose_banner = QPushButton("上传 SVG")
            choose_banner.setObjectName("secondary")
            choose_banner.clicked.connect(self._choose_banner_svg)
            clear_banner = QPushButton("恢复默认横幅")
            clear_banner.setObjectName("secondary")
            clear_banner.clicked.connect(self._clear_banner_settings)
            save_banner = QPushButton("保存并应用")
            save_banner.setObjectName("primary")
            save_banner.clicked.connect(self._save_banner_settings)
            banner_actions.addWidget(choose_banner)
            banner_actions.addWidget(clear_banner)
            banner_actions.addStretch(1)
            banner_actions.addWidget(save_banner)
            banner_card.body.addLayout(banner_actions)
            layout.addWidget(banner_card)

            notification_card = SectionCard()
            self.notification_status_badge = StatusPill("未启用", "unknown")
            notification_card.add_header(
                "险情通知（可选）",
                "新险情自动发送到 Webhook 或邮箱；通知失败不影响险情保存。",
                self.notification_status_badge,
            )
            notification_form_container = QWidget()
            notification_form_container.setMaximumWidth(900)
            notification_form = QFormLayout(notification_form_container)
            notification_form.setContentsMargins(0, 0, 0, 0)
            notification_form.setSpacing(10)
            self.notification_enabled_check = QCheckBox("启用险情通知")
            notification_form.addRow("总开关", self.notification_enabled_check)
            self.webhook_enabled_check = QCheckBox("Webhook")
            self.webhook_url_edit = QLineEdit()
            self.webhook_url_edit.setPlaceholderText("https://hooks.example.test/factoryguard")
            self.webhook_secret_edit = QLineEdit()
            self.webhook_secret_edit.setEchoMode(QLineEdit.Password)
            self.webhook_secret_edit.setPlaceholderText("签名密钥，可留空；已保存时留空保持不变")
            notification_form.addRow("Webhook", self.webhook_enabled_check)
            notification_form.addRow("Webhook 地址", self.webhook_url_edit)
            notification_form.addRow("Webhook 密钥", self.webhook_secret_edit)
            self.email_enabled_check = QCheckBox("邮件")
            self.notification_email_host_edit = QLineEdit()
            self.notification_email_host_edit.setPlaceholderText("smtp.example.test")
            self.notification_email_port_spin = QSpinBox()
            self.notification_email_port_spin.setRange(1, 65535)
            self.notification_email_username_edit = QLineEdit()
            self.notification_email_username_edit.setPlaceholderText("SMTP 账号（可选）")
            self.notification_email_password_edit = QLineEdit()
            self.notification_email_password_edit.setEchoMode(QLineEdit.Password)
            self.notification_email_password_edit.setPlaceholderText(
                "SMTP 密码，已保存时留空保持不变"
            )
            self.notification_email_sender_edit = QLineEdit()
            self.notification_email_sender_edit.setPlaceholderText("factoryguard@example.test")
            self.notification_email_recipients_edit = QLineEdit()
            self.notification_email_recipients_edit.setPlaceholderText("多个收件人用分号分隔")
            self.notification_email_security_combo = QComboBox()
            self.notification_email_security_combo.addItem("STARTTLS（常用）", "starttls")
            self.notification_email_security_combo.addItem("SSL/TLS", "ssl")
            self.notification_email_security_combo.addItem("不加密", "none")
            notification_form.addRow("邮件", self.email_enabled_check)
            notification_form.addRow("SMTP 地址", self.notification_email_host_edit)
            notification_form.addRow("SMTP 端口", self.notification_email_port_spin)
            notification_form.addRow("SMTP 账号", self.notification_email_username_edit)
            notification_form.addRow("SMTP 密码", self.notification_email_password_edit)
            notification_form.addRow("发件人", self.notification_email_sender_edit)
            notification_form.addRow("收件人", self.notification_email_recipients_edit)
            notification_form.addRow("邮件安全", self.notification_email_security_combo)
            self.sms_enabled_check = QCheckBox("短信")
            self.sms_endpoint_edit = QLineEdit()
            self.sms_endpoint_edit.setPlaceholderText("https://sms.example.test/send")
            self.sms_token_edit = QLineEdit()
            self.sms_token_edit.setEchoMode(QLineEdit.Password)
            self.sms_token_edit.setPlaceholderText("网关访问令牌，已保存时留空保持不变")
            self.sms_sender_edit = QLineEdit()
            self.sms_sender_edit.setPlaceholderText("短信签名，例如 FactoryGuard")
            self.sms_template_edit = QLineEdit()
            self.sms_template_edit.setPlaceholderText("短信模板 ID（按服务商要求填写）")
            self.sms_recipients_edit = QLineEdit()
            self.sms_recipients_edit.setPlaceholderText("多个手机号用分号分隔")
            notification_form.addRow("短信", self.sms_enabled_check)
            notification_form.addRow("短信网关", self.sms_endpoint_edit)
            notification_form.addRow("网关令牌", self.sms_token_edit)
            notification_form.addRow("短信签名", self.sms_sender_edit)
            notification_form.addRow("短信模板", self.sms_template_edit)
            notification_form.addRow("接收号码", self.sms_recipients_edit)
            self._notification_channel_checks = (
                self.webhook_enabled_check,
                self.email_enabled_check,
                self.sms_enabled_check,
            )
            self._notification_webhook_fields = (
                self.webhook_url_edit,
                self.webhook_secret_edit,
            )
            self._notification_email_fields = (
                self.notification_email_host_edit,
                self.notification_email_port_spin,
                self.notification_email_username_edit,
                self.notification_email_password_edit,
                self.notification_email_sender_edit,
                self.notification_email_recipients_edit,
                self.notification_email_security_combo,
            )
            self._notification_sms_fields = (
                self.sms_endpoint_edit,
                self.sms_token_edit,
                self.sms_sender_edit,
                self.sms_template_edit,
                self.sms_recipients_edit,
            )
            self.notification_enabled_check.toggled.connect(self._update_notification_field_states)
            for channel_check in self._notification_channel_checks:
                channel_check.toggled.connect(self._update_notification_field_states)
            self._update_notification_field_states()
            notification_card.body.addWidget(notification_form_container)
            notification_hint = QLabel(
                "Webhook 仅接受 HTTPS 地址，可使用 X-FactoryGuard-Signature 校验请求。"
                "通知配置和邮件密码以明文保存在本地 JSON，请限制配置文件访问权限。"
            )
            notification_hint.setObjectName("cardMuted")
            notification_hint.setWordWrap(True)
            notification_card.body.addWidget(notification_hint)
            notification_actions = QHBoxLayout()
            self.notification_status_label = QLabel()
            self.notification_status_label.setObjectName("muted")
            notification_test_button = QPushButton("发送测试通知")
            notification_test_button.setObjectName("secondary")
            notification_test_button.clicked.connect(self._test_notifications)
            notification_save_button = QPushButton("保存通知配置")
            notification_save_button.setObjectName("primary")
            notification_save_button.clicked.connect(self._save_notification_settings)
            notification_actions.addWidget(self.notification_status_label, 1)
            notification_actions.addWidget(notification_test_button)
            notification_actions.addWidget(notification_save_button)
            notification_card.body.addLayout(notification_actions)
            layout.addWidget(notification_card)

            llm_card = SectionCard()
            self.llm_status_badge = StatusPill("未启用", "unknown")
            llm_card.add_header(
                "文字问询（可选）",
                "仅处理文字摘要；未启用时自动使用本地规则问答",
                self.llm_status_badge,
            )
            form_container = QWidget()
            form_container.setMaximumWidth(820)
            form = QFormLayout(form_container)
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(10)
            self.llm_enabled_check = QCheckBox("启用文字问询服务")
            self.llm_provider_combo = QComboBox()
            for key, (label, _endpoint) in LLM_PROVIDER_PRESETS.items():
                self.llm_provider_combo.addItem(label, key)
            self.llm_provider_combo.currentIndexChanged.connect(self._apply_llm_provider_preset)
            self.llm_endpoint_edit = QLineEdit()
            self.llm_endpoint_edit.setPlaceholderText(
                "https://your-service.example/v1/chat/completions"
            )
            self.llm_model_edit = QLineEdit()
            self.llm_model_edit.setPlaceholderText("填写服务商提供的模型 ID")
            self.llm_key_edit = QLineEdit()
            self.llm_key_edit.setEchoMode(QLineEdit.Password)
            self.llm_key_edit.setPlaceholderText("留空则读取 FACTORYGUARD_LLM_API_KEY")
            self.llm_timeout_spin = QSpinBox()
            self.llm_timeout_spin.setRange(5, 180)
            self.llm_timeout_spin.setSuffix(" 秒")
            form.addRow("启用状态", self.llm_enabled_check)
            form.addRow("服务商", self.llm_provider_combo)
            form.addRow("接口地址", self.llm_endpoint_edit)
            form.addRow("文字模型 ID", self.llm_model_edit)
            form.addRow("API 密钥", self.llm_key_edit)
            form.addRow("请求超时", self.llm_timeout_spin)
            llm_left = QVBoxLayout()
            llm_left.setSpacing(10)
            llm_left.addWidget(form_container)
            hints = QLabel(
                "接口需兼容 Chat Completions。配置和密钥以明文保存在本地 config/factoryguard.json，"
                "请勿将配置文件发送给他人。此服务只处理文字化运营摘要，不读取、不上传视频画面，也不执行视觉识别。"
            )
            hints.setMaximumWidth(820)
            hints.setObjectName("cardMuted")
            hints.setWordWrap(True)
            llm_left.addWidget(hints)
            llm_actions = QHBoxLayout()
            self.llm_status_label = QLabel()
            self.llm_status_label.setObjectName("muted")
            save_llm = QPushButton("保存配置")
            save_llm.setObjectName("primary")
            save_llm.clicked.connect(self._save_llm_settings)
            self.llm_test_button = QPushButton("测试连接")
            self.llm_test_button.setObjectName("secondary")
            self.llm_test_button.clicked.connect(self._test_llm)
            llm_actions.addWidget(self.llm_status_label, 1)
            llm_actions.addWidget(self.llm_test_button)
            llm_actions.addWidget(save_llm)
            llm_left.addLayout(llm_actions)
            llm_columns = QHBoxLayout()
            llm_columns.setSpacing(18)
            llm_columns.addLayout(llm_left, 1)
            llm_columns.addWidget(
                self._settings_info_panel(
                    "接入提示",
                    "文字问询是可选服务，基础问询与险情登记不依赖外部接口。",
                    (
                        "只发送区域、设备和险情的文字摘要，不读取摄像头画面或设备凭据。",
                        "密钥明文保存在本地配置文件，请限制文件访问权限。",
                        "连接失败时自动回退本地规则，不影响险情处置。",
                        "更换服务商或模型 ID 后，建议先执行连接测试。",
                    ),
                ),
                0,
                Qt.AlignTop,
            )
            llm_card.body.addLayout(llm_columns)
            layout.addWidget(llm_card)

            gb_card = SectionCard()
            self.gb_status_badge = StatusPill("未启用", "unknown")
            gb_card.add_header(
                "GB/T 28181 国标服务",
                "接收摄像头注册、目录与设备告警",
                self.gb_status_badge,
            )
            gb_form_container = QWidget()
            gb_form_container.setMaximumWidth(720)
            gb_form = QFormLayout(gb_form_container)
            gb_form.setContentsMargins(0, 0, 0, 0)
            gb_form.setSpacing(10)
            self.gb_enabled_check = QCheckBox("启用本机国标服务")
            self.gb_platform_edit = QLineEdit()
            self.gb_platform_edit.setPlaceholderText("20 位平台编号")
            self.gb_domain_edit = QLineEdit()
            self.gb_domain_edit.setPlaceholderText("例如 3402000000")
            self.gb_port_spin = QSpinBox()
            self.gb_port_spin.setRange(1, 65535)
            self.gb_password_edit = QLineEdit()
            self.gb_password_edit.setEchoMode(QLineEdit.Password)
            self.gb_password_edit.setPlaceholderText("设备注册密码（可选）")
            gb_form.addRow("服务状态", self.gb_enabled_check)
            gb_form.addRow("平台编号", self.gb_platform_edit)
            gb_form.addRow("SIP 域", self.gb_domain_edit)
            gb_form.addRow("SIP 端口", self.gb_port_spin)
            gb_form.addRow("注册密码", self.gb_password_edit)
            gb_guide = QLabel(
                "仅当摄像头或 NVR 使用 GB/T 28181 国标协议时才需要填写。\n"
                "平台编号与 SIP 域由设备厂商或施工方提供，一般为 20 位数字和 10 位数字；"
                "端口保持默认 5060 即可，注册密码通常留空。\n"
                "不确定时无需启用，直接使用 ONVIF 或 RTSP 接入即可正常查看画面和接收告警。"
            )
            gb_guide.setObjectName("cardMuted")
            gb_guide.setWordWrap(True)
            gb_form.addRow("", gb_guide)
            gb_left = QVBoxLayout()
            gb_left.setSpacing(10)
            gb_left.addWidget(gb_form_container)
            gb_actions = QHBoxLayout()
            self.gb_status_label = QLabel()
            self.gb_status_label.setObjectName("muted")
            self.gb_import_button = QPushButton("导入已注册设备")
            self.gb_import_button.setObjectName("secondary")
            self.gb_import_button.setEnabled(False)
            self.gb_import_button.clicked.connect(self._import_gb_registered_devices)
            save_gb = QPushButton("保存并应用")
            save_gb.setObjectName("primary")
            save_gb.clicked.connect(self._save_gb_settings)
            gb_actions.addWidget(self.gb_status_label, 1)
            gb_actions.addWidget(self.gb_import_button)
            gb_actions.addWidget(save_gb)
            gb_left.addLayout(gb_actions)
            gb_columns = QHBoxLayout()
            gb_columns.setSpacing(18)
            gb_columns.addLayout(gb_left, 1)
            gb_columns.addWidget(
                self._settings_info_panel(
                    "接入流程",
                    "国标服务用于设备注册、目录同步、告警接收和实时媒体会话。",
                    (
                        "配置平台编号、SIP 域和端口后启用服务。",
                        "摄像头使用平台编号、域和密码发起注册。",
                        "注册成功后点击“导入已注册设备”。",
                        "为导入的通道选择所属区域并确认设备名称。",
                    ),
                ),
                0,
                Qt.AlignTop,
            )
            gb_card.body.addLayout(gb_columns)
            layout.addWidget(gb_card)

            runtime_card = SectionCard()
            runtime_card.add_header("本地运行环境", "摄像头视频流由 FFmpeg 直接解码")
            self.runtime_status_label = QLabel()
            self.runtime_status_label.setObjectName("muted")
            self.runtime_status_label.setWordWrap(True)
            runtime_card.body.addWidget(self.runtime_status_label)
            runtime_actions = QHBoxLayout()
            open_config = QPushButton("打开配置文件")
            open_config.setObjectName("secondary")
            open_config.clicked.connect(self._open_config_file)
            open_data = QPushButton("打开数据目录")
            open_data.setObjectName("secondary")
            open_data.clicked.connect(self._open_data_folder)
            refresh_health = QPushButton("刷新设备状态")
            refresh_health.setObjectName("secondary")
            refresh_health.clicked.connect(self._scan_stream_health)
            create_backup_button = QPushButton("创建数据备份")
            create_backup_button.setObjectName("secondary")
            create_backup_button.clicked.connect(self._create_backup)
            restore_backup = QPushButton("恢复备份")
            restore_backup.setObjectName("secondary")
            restore_backup.clicked.connect(self._choose_backup_to_restore)
            open_backups = QPushButton("打开备份目录")
            open_backups.setObjectName("secondary")
            open_backups.clicked.connect(self._open_backup_folder)
            runtime_actions.addWidget(open_config)
            runtime_actions.addWidget(open_data)
            runtime_actions.addWidget(refresh_health)
            runtime_actions.addWidget(create_backup_button)
            runtime_actions.addWidget(restore_backup)
            runtime_actions.addWidget(open_backups)
            runtime_actions.addStretch(1)
            runtime_card.body.addLayout(runtime_actions)
            self.autostart_check = QCheckBox("开机后自动在后台值守")
            self.autostart_status_label = QLabel("")
            self.autostart_status_label.setObjectName("cardMuted")
            self.autostart_status_label.setWordWrap(True)
            self.autostart_check.toggled.connect(self._toggle_autostart)
            autostart_row = QHBoxLayout()
            autostart_row.addWidget(self.autostart_check)
            autostart_row.addWidget(self.autostart_status_label, 1)
            runtime_card.body.addLayout(autostart_row)
            self.backup_status_label = QLabel("本地备份：正在读取…")
            self.backup_status_label.setObjectName("cardMuted")
            self.backup_status_label.setWordWrap(True)
            runtime_card.body.addWidget(self.backup_status_label)
            layout.addWidget(runtime_card)
            layout.addStretch(1)
            return page

        def _load_notification_config(self) -> IncidentNotificationConfig:
            self._notification_config_error = ""
            try:
                return IncidentNotificationConfig.from_settings(
                    self._config_store.section("notifications")
                )
            except (TypeError, ValueError) as exc:
                self._notification_config_error = str(exc)
                return IncidentNotificationConfig()

        def _update_notification_field_states(self) -> None:
            master_enabled = self.notification_enabled_check.isChecked()
            for channel_check in self._notification_channel_checks:
                channel_check.setEnabled(master_enabled)
            for field in self._notification_webhook_fields:
                field.setEnabled(master_enabled and self.webhook_enabled_check.isChecked())
            for field in self._notification_email_fields:
                field.setEnabled(master_enabled and self.email_enabled_check.isChecked())
            for field in self._notification_sms_fields:
                field.setEnabled(master_enabled and self.sms_enabled_check.isChecked())

        def _notification_payload_from_form(self) -> dict[str, Any]:
            current = self._notification_config
            webhook_secret = self.webhook_secret_edit.text() or current.webhook_secret
            email_password = self.notification_email_password_edit.text() or current.email_password
            return {
                "enabled": self.notification_enabled_check.isChecked(),
                "timeout": 10,
                "webhook": {
                    "enabled": self.webhook_enabled_check.isChecked(),
                    "url": self.webhook_url_edit.text().strip(),
                    "secret": webhook_secret,
                },
                "email": {
                    "enabled": self.email_enabled_check.isChecked(),
                    "host": self.notification_email_host_edit.text().strip(),
                    "port": self.notification_email_port_spin.value(),
                    "username": self.notification_email_username_edit.text().strip(),
                    "password": email_password,
                    "sender": self.notification_email_sender_edit.text().strip(),
                    "recipients": [
                        value.strip()
                        for value in self.notification_email_recipients_edit.text().split(";")
                        if value.strip()
                    ],
                    "security": str(
                        self.notification_email_security_combo.currentData() or "starttls"
                    ),
                },
                "sms": {
                    "enabled": self.sms_enabled_check.isChecked(),
                    "endpoint": self.sms_endpoint_edit.text().strip(),
                    "provider": "custom_http",
                    "token": self.sms_token_edit.text() or current.sms_token,
                    "sender": self.sms_sender_edit.text().strip(),
                    "template_id": self.sms_template_edit.text().strip(),
                    "recipients": [
                        value.strip()
                        for value in self.sms_recipients_edit.text().split(";")
                        if value.strip()
                    ],
                },
            }

        def _notification_config_from_form(self) -> IncidentNotificationConfig:
            return IncidentNotificationConfig.from_settings(self._notification_payload_from_form())

        def _populate_notification_settings(self) -> None:
            config = self._notification_config
            self.notification_enabled_check.setChecked(config.enabled)
            self.webhook_enabled_check.setChecked(config.webhook_enabled)
            self.webhook_url_edit.setText(config.webhook_url)
            self.webhook_secret_edit.clear()
            self.webhook_secret_edit.setPlaceholderText(
                "已保存，留空保持不变" if config.webhook_secret else "签名密钥，可留空"
            )
            self.email_enabled_check.setChecked(config.email_enabled)
            self.notification_email_host_edit.setText(config.email_host)
            try:
                email_port = int(config.email_port)
            except (TypeError, ValueError):
                email_port = 587
            self.notification_email_port_spin.setValue(max(1, min(65535, email_port)))
            self.notification_email_username_edit.setText(config.email_username)
            self.notification_email_password_edit.clear()
            self.notification_email_password_edit.setPlaceholderText(
                "已保存，留空保持不变" if config.email_password else "SMTP 密码（可选）"
            )
            self.notification_email_sender_edit.setText(config.email_sender)
            self.notification_email_recipients_edit.setText("; ".join(config.email_recipients))
            security_index = self.notification_email_security_combo.findData(config.email_security)
            self.notification_email_security_combo.setCurrentIndex(max(0, security_index))
            self.sms_enabled_check.setChecked(config.sms_enabled)
            self.sms_endpoint_edit.setText(config.sms_endpoint)
            self.sms_token_edit.clear()
            self.sms_token_edit.setPlaceholderText(
                "已保存，留空保持不变" if config.sms_token else "网关访问令牌（可选）"
            )
            self.sms_sender_edit.setText(config.sms_sender)
            self.sms_template_edit.setText(config.sms_template_id)
            self.sms_recipients_edit.setText("; ".join(config.sms_recipients))
            if config.available:
                self.notification_status_badge.set_state("已启用", "online")
            elif config.enabled:
                self.notification_status_badge.set_state("待配置", "unknown")
            else:
                self.notification_status_badge.set_state("未启用", "unknown")
            self._update_notification_field_states()
            channels = []
            if config.webhook_enabled:
                channels.append("Webhook")
            if config.email_enabled:
                channels.append("邮件")
            if config.sms_enabled:
                channels.append("短信")
            if config.enabled and channels:
                notification_summary = "启用渠道：" + "、".join(channels)
            elif config.enabled:
                notification_summary = "总开关已启用，但尚未选择通知渠道"
            elif channels:
                notification_summary = "通知已关闭；已选择的渠道配置会保留"
            else:
                notification_summary = "通知已关闭，新险情不会发送外部通知"
            self.notification_status_label.setText(notification_summary)
            if self._notification_config_error:
                self.notification_status_badge.set_state("配置错误", "offline")
                self.notification_status_label.setText(
                    f"通知配置无效，当前不会发送：{self._notification_config_error}"
                )
            if self._selected_incident_id:
                self._refresh_incident_notification_status(self._selected_incident_id)

        def _save_notification_settings(self) -> None:
            try:
                config = self._notification_config_from_form()
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "通知配置无效", str(exc))
                return
            try:
                self._config_store.update_section(
                    "notifications", self._notification_payload_from_form()
                )
            except ConfigFileError as exc:
                QMessageBox.warning(self, "配置文件保存失败", str(exc))
                return
            self._notification_config = config
            self._notification_config_error = ""
            self._populate_notification_settings()
            self.statusBar().showMessage("险情通知配置已保存", 4000)

        def _test_notifications(self) -> None:
            try:
                config = self._notification_config_from_form()
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "通知配置无效", str(exc))
                return
            if not config.available:
                QMessageBox.information(self, "通知未启用", "请启用通知总开关和至少一个渠道。")
                return
            self.notification_status_label.setText("正在发送测试通知…")
            self._start_notification_worker(config, test=True)

        def _start_notification_worker(
            self,
            config: IncidentNotificationConfig,
            *,
            incident: Incident | None = None,
            test: bool = False,
            channels: tuple[str, ...] | None = None,
        ) -> None:
            if not config.available:
                return
            site_name = ""
            area_name = ""
            camera_name = ""
            if incident is not None:
                area_name = self._context.area_name(
                    incident.area_id, snapshot=incident.area_name_snapshot
                )
                camera_name = self._context.camera_name(
                    incident.camera_id, snapshot=incident.camera_name_snapshot
                )
                for area in self._context.topology.areas:
                    if area.id == incident.area_id:
                        try:
                            site_name = self._context.topology.get_site(area.site_id).name
                        except KeyError:
                            site_name = ""
                        break
            requested_channels = (
                tuple(channels) if channels is not None else config.enabled_channels
            )
            if incident is not None:
                self._notification_channels_inflight[incident.id] = requested_channels
            thread = QThread(self)
            worker = IncidentNotificationWorker(
                config=config,
                incident=incident,
                site_name=site_name,
                area_name=area_name,
                camera_name=camera_name,
                test=test,
                channels=channels,
            )
            self._notification_threads.add(thread)
            self._notification_workers.add(worker)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self._on_notification_finished)
            worker.completed.connect(lambda *_args: _quit_thread_soon(thread))
            worker.completed.connect(worker.deleteLater)
            thread.finished.connect(lambda t=thread, w=worker: self._cleanup_notification_job(t, w))
            thread.finished.connect(thread.deleteLater)
            thread.start()

        def _cleanup_notification_job(
            self, thread: QThread, worker: IncidentNotificationWorker
        ) -> None:
            self._notification_threads.discard(thread)
            self._notification_workers.discard(worker)

        def _retry_failed_notifications(self) -> None:
            if not self._notification_config.available:
                return
            enabled_channels = set(self._notification_config.enabled_channels)
            candidates = self.store.incident_notification_retry_candidates(
                min_age_seconds=60,
                limit=20,
                backoff_seconds=60,
                max_backoff_seconds=3600,
            )
            for incident_id, channels in candidates:
                retryable = tuple(channel for channel in channels if channel in enabled_channels)
                if not retryable or incident_id in self._notification_retry_inflight:
                    continue
                incident = self.store.get_incident(incident_id)
                if incident is not None:
                    self._dispatch_incident_notification(incident, channels=retryable)

        def _on_notification_finished(self, incident_id: str, results: object, error: str) -> None:
            if incident_id:
                self._notification_retry_inflight.discard(incident_id)
            deliveries = tuple(results) if isinstance(results, tuple) else ()
            requested_channels = self._notification_channels_inflight.pop(
                incident_id, self._notification_config.enabled_channels
            )
            if incident_id and error and not deliveries:
                deliveries = tuple(
                    DeliveryResult(
                        channel,
                        False,
                        f"通知任务异常：{error[:240]}",
                    )
                    for channel in requested_channels
                )
            if incident_id:
                for item in deliveries:
                    self.store.record_incident_notification(
                        incident_id,
                        item.channel,
                        success=item.success,
                        detail=item.detail,
                    )
                if self._selected_incident_id == incident_id:
                    self._refresh_incident_notification_status(incident_id)
            if error:
                self.statusBar().showMessage(f"险情通知失败：{error[:160]}", 8000)
                return
            if not deliveries:
                return
            failed = [item for item in deliveries if not item.success]
            if failed:
                detail = "；".join(
                    f"{_notification_channel_label(item.channel)}: {item.detail}" for item in failed
                )
                self.statusBar().showMessage(f"部分险情通知失败：{detail[:180]}", 8000)
                return
            channels = "、".join(_notification_channel_label(item.channel) for item in deliveries)
            self.statusBar().showMessage(f"险情通知已发送：{channels}", 5000)

        def _dispatch_incident_notification(
            self,
            incident: Incident,
            *,
            channels: tuple[str, ...] | None = None,
        ) -> None:
            if (
                not self._notification_config.available
                or incident.id in self._notification_retry_inflight
            ):
                return
            if channels is not None:
                enabled_channels = set(self._notification_config.enabled_channels)
                channels = tuple(channel for channel in channels if channel in enabled_channels)
                if not channels:
                    return
            self._notification_retry_inflight.add(incident.id)
            self._start_notification_worker(
                self._notification_config, incident=incident, channels=channels
            )

        def _load_llm_config(self) -> LLMConfig:
            self._llm_config_error = ""
            try:
                env_config = LLMConfig.from_environment()
                settings = self._config_store.section("llm")
                endpoint = str(settings.get("endpoint") or env_config.endpoint)
                model = str(settings.get("model") or env_config.model)
                timeout = float(settings.get("timeout", env_config.timeout))
                api_key = str(settings.get("api_key") or env_config.api_key)
                enabled = bool(settings.get("enabled", bool(endpoint and model and api_key)))
                return LLMConfig(
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    timeout=timeout,
                    enabled=enabled,
                )
            except (TypeError, ValueError) as exc:
                self._llm_config_error = f"文字问询配置无效：{exc}"
                return LLMConfig(enabled=False)

        def _apply_llm_provider_preset(self, _index: int) -> None:
            provider = str(self.llm_provider_combo.currentData() or "")
            label, endpoint = LLM_PROVIDER_PRESETS.get(provider, LLM_PROVIDER_PRESETS["custom"])
            if endpoint:
                self.llm_endpoint_edit.setText(endpoint)
            elif not provider:
                self.llm_endpoint_edit.clear()
            if not provider:
                self.llm_model_edit.setPlaceholderText("请选择服务商后填写模型 ID")
            elif provider != "custom":
                self.llm_model_edit.setPlaceholderText(f"{label} 的模型 ID")
            else:
                self.llm_model_edit.setPlaceholderText("填写企业网关提供的模型 ID")

        def _populate_settings(self) -> None:
            self._populate_branding_settings()
            self._refresh_autostart_state()
            self._populate_notification_settings()
            config = self._llm_config
            provider = str(self._config_store.value("llm", "provider", ""))
            if provider not in LLM_PROVIDER_PRESETS:
                provider = next(
                    (
                        key
                        for key, (_label, endpoint) in LLM_PROVIDER_PRESETS.items()
                        if endpoint and endpoint == config.endpoint
                    ),
                    "",
                )
            self.llm_provider_combo.blockSignals(True)
            self.llm_provider_combo.setCurrentIndex(
                max(0, self.llm_provider_combo.findData(provider))
            )
            self.llm_provider_combo.blockSignals(False)
            endpoint = config.endpoint or LLM_PROVIDER_PRESETS.get(provider, ("", ""))[1]
            self.llm_endpoint_edit.setText(endpoint)
            self.llm_model_edit.setText(config.model)
            self.llm_enabled_check.setChecked(config.enabled)
            provider_label = LLM_PROVIDER_PRESETS.get(provider, ("自定义", ""))[0]
            self.llm_model_edit.setPlaceholderText(
                f"{provider_label} 的模型 ID" if provider else "请选择服务商后填写模型 ID"
            )
            self.llm_timeout_spin.setValue(max(5, min(180, int(config.timeout))))
            self.llm_key_edit.setText("")
            self.llm_key_edit.setPlaceholderText(
                "已安全保存，留空保持不变" if config.api_key else "留空则读取环境变量"
            )
            gb = self._config_store.section("gb28181")
            self.gb_enabled_check.setChecked(bool(gb.get("enabled", False)))
            self.gb_platform_edit.setText(str(gb.get("platform_id", "")))
            self.gb_domain_edit.setText(str(gb.get("domain", "")))
            try:
                gb_port = int(gb.get("port", 5060))
                if not 1 <= gb_port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                gb_port = 5060
                self._gb_error = "国标配置无效：SIP 端口必须是 1～65535 的整数"
            self.gb_port_spin.setValue(gb_port)

        def _populate_branding_settings(self) -> None:
            self._pending_banner_source = None
            branding = self._config_store.section("branding")
            path_text = str(branding.get("banner_svg", "")).strip()
            height = self._branding_height(branding.get("banner_height"))
            self.banner_path_edit.setText(path_text)
            self.banner_height_spin.setValue(height)
            path = self._resolve_banner_path(path_text)
            loaded = bool(
                path and path.is_file() and self.banner_preview.load_svg(str(path), height=height)
            )
            if not loaded:
                self.banner_preview.clear()
            self.banner_status_badge.set_state(
                "自定义" if loaded else "默认", "online" if loaded else "unknown"
            )
            self.banner_status_label.setText(
                "正在使用商户 SVG 横幅，保存后立即应用。"
                if loaded
                else "正在使用默认品牌横幅；上传 SVG 后可替换为商户自己的品牌。"
            )

        def _choose_banner_svg(self) -> None:
            source, _filter = QFileDialog.getOpenFileName(
                self, "选择顶部横幅 SVG", "", "SVG 文件 (*.svg)"
            )
            if not source:
                return
            source_path = Path(source)
            if source_path.suffix.lower() != ".svg":
                QMessageBox.warning(self, "文件格式错误", "请选择 .svg 文件。")
                return
            if not self.banner_preview.load_svg(
                str(source_path), height=self.banner_height_spin.value()
            ):
                QMessageBox.warning(
                    self, "SVG 无法读取", "文件不是有效的 SVG，或当前环境不支持 SVG 渲染。"
                )
                return
            self._pending_banner_source = source_path.resolve()
            self.banner_path_edit.setText(source_path.name)
            self.banner_preview.load_svg(str(source_path), height=self.banner_height_spin.value())
            self.banner_status_badge.set_state("待保存", "unknown")
            self.banner_status_label.setText("已预览新 SVG，点击“保存横幅”后才会替换当前素材。")

        def _save_banner_settings(self) -> None:
            destination = self._config_store.path.parent / "branding" / "banner.svg"
            pending_source = self._pending_banner_source
            if pending_source is not None:
                if not pending_source.is_file():
                    QMessageBox.warning(self, "横幅文件不存在", "请重新选择有效的 SVG 文件。")
                    return
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if pending_source != destination.resolve():
                        shutil.copy2(pending_source, destination)
                except OSError as exc:
                    QMessageBox.warning(self, "SVG 保存失败", str(exc))
                    return
                path_text = destination.relative_to(self._config_store.path.parent).as_posix()
                path = destination
            else:
                path_text = self.banner_path_edit.text().strip()
                path = self._resolve_banner_path(path_text)
            if path_text and (path is None or not path.is_file()):
                QMessageBox.warning(self, "横幅文件不存在", "请重新选择有效的 SVG 文件。")
                return
            try:
                self._config_store.update_section(
                    "branding",
                    {
                        "banner_svg": path_text,
                        "banner_height": self.banner_height_spin.value(),
                    },
                )
            except ConfigFileError as exc:
                QMessageBox.warning(self, "配置文件保存失败", str(exc))
                return
            self._pending_banner_source = None
            self._apply_branding_settings()
            self._populate_branding_settings()
            self.statusBar().showMessage("顶部横幅配置已保存", 4000)

        def _clear_banner_settings(self) -> None:
            self._pending_banner_source = None
            self.banner_path_edit.clear()
            self.banner_preview.clear()
            try:
                self._config_store.update_section(
                    "branding",
                    {"banner_svg": "", "banner_height": self.banner_height_spin.value()},
                )
            except ConfigFileError as exc:
                QMessageBox.warning(self, "配置文件保存失败", str(exc))
                return
            self._apply_branding_settings()
            self._populate_branding_settings()
            self.statusBar().showMessage("已恢复默认顶部横幅", 4000)

        def _refresh_all(self, status_message: str | None = None) -> None:
            self.today_label.setText(self._today_text())
            self._context = build_operational_context(self.store)
            self._refresh_dashboard()
            self._refresh_live()
            self._refresh_incidents()
            self._refresh_devices()
            self._refresh_assistant_context()
            self._refresh_runtime_labels()
            self.statusBar().showMessage(
                status_message
                or (
                    f"已更新 · {datetime.now().astimezone().strftime('%H:%M:%S')} · 数据保存在本机"
                ),
                4500,
            )

        def _run_local_maintenance(self) -> None:
            """Trim local history and compact the database during long sessions."""
            if self._db_path == ":memory:":
                return
            try:
                report = self.store.prune_history(retention_days=self.HISTORY_RETENTION_DAYS)
                self.store.checkpoint_wal()
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self.statusBar().showMessage(f"本地数据清理未完成：{str(exc)[:120]}", 6000)
                return
            removed = sum(
                int(report.get(key, 0) or 0) for key in ("events", "audit", "outbox", "assistant")
            )
            stale_credentials = 0
            try:
                referenced = set(self.store.referenced_credential_refs())
                stale_credentials = len(self._config_store.prune_unused_credentials(referenced))
            except (OSError, RuntimeError, ValueError) as exc:
                self.statusBar().showMessage(f"设备凭据整理未完成：{str(exc)[:120]}", 6000)
                return
            if removed or stale_credentials:
                self.statusBar().showMessage(
                    f"已清理 {removed} 条历史记录、{stale_credentials} 条废弃凭据，数据库已整理",
                    6000,
                )

        def _periodic_refresh(self) -> None:
            self._guarded_slot("告警接收", self._drain_native_incidents)
            self._guarded_slot("设备订阅同步", self._sync_native_subscriptions)
            self._guarded_slot("通知重试", self._retry_failed_notifications)
            self._guarded_slot("界面刷新", self._refresh_periodic_visible)

        def _refresh_periodic_visible(self) -> None:
            """Refresh only the visible data page and shared operational summary."""
            self.today_label.setText(self._today_text())
            self._context = build_operational_context(self.store)
            if self._current_page == "live":
                self._refresh_live()
            elif self._current_page == "incidents":
                self._refresh_incidents()
            elif self._current_page == "devices":
                self._refresh_devices()
            elif self._current_page == "assistant":
                self._refresh_assistant_context()
            self._refresh_dashboard()
            self._refresh_runtime_labels()

        def _refresh_dashboard(self) -> None:
            context = self._context
            total = len(context.active_cameras)
            online = len(context.online_cameras)
            unknown = len(context.unknown_cameras)
            if total:
                self.hero_title.setText("现场已接入，安全态势实时汇总")
            elif context.disabled_cameras:
                self.hero_title.setText("摄像头均已暂停接入，现场值守已暂停")
            else:
                self.hero_title.setText("先接入摄像头，开始守护你的场所")
            summary = context.dashboard_summary()
            notification_error = str(getattr(self, "_notification_config_error", "") or "").strip()
            if notification_error:
                summary = (
                    f"通知配置无效，新险情不会发送到外部渠道：{notification_error}。"
                    "请在“系统设置”中修正。"
                )
            self.hero_text.setText(summary)
            self.banner.set_context(self._banner_site_name(), summary)
            if context.high_incidents:
                self.hero_state_pill.set_state("需关注", "open")
            elif context.offline_cameras:
                self.hero_state_pill.set_state("设备异常", "offline")
            elif context.open_incidents:
                self.hero_state_pill.set_state("有待处理", "open")
            elif total and online == total:
                self.hero_state_pill.set_state("运行平稳", "online")
            elif context.disabled_cameras and not total:
                self.hero_state_pill.set_state("已暂停", "unknown")
            else:
                self.hero_state_pill.set_state("待完善", "unknown")
            self.metric_cameras.set_value(
                f"{online}/{total}" if total else "—",
                "在线 / 全部"
                if total
                else "已暂停全部摄像头"
                if context.disabled_cameras
                else "尚未添加摄像头",
            )
            self.metric_areas.set_value(
                str(len(context.topology.areas)),
                "已划分的管理区域" if context.topology.areas else "尚未创建区域",
            )
            self.metric_incidents.set_value(
                str(len(context.open_incidents)),
                f"其中高危 {len(context.high_incidents)} 条"
                if context.high_incidents
                else "当前无需升级"
                if total
                else "险情记录仍保留"
                if context.disabled_cameras
                else "接入设备后开始统计",
            )
            online_rate = round(online * 100 / total) if total else 0
            self.metric_online_rate.set_value(
                f"{online_rate}%" if total else "—",
                f"待检测 {unknown} 台"
                if unknown
                else "摄像头均已暂停"
                if context.disabled_cameras
                else "设备状态已更新",
            )
            clear_layout(self.dashboard_camera_grid)
            camera_cards = self._preview_cameras(
                context.cameras, limit=2 if self.height() < 820 else 4
            )
            if camera_cards:
                for index, camera in enumerate(camera_cards):
                    card = CameraCard(camera, compact=True)
                    card.open_requested.connect(self._open_camera)
                    self.dashboard_camera_grid.addWidget(card, index // 2, index % 2)
                self.dashboard_camera_grid.setColumnStretch(0, 1)
                self.dashboard_camera_grid.setColumnStretch(1, 1)
            else:
                empty = EmptyState(
                    title="还没有摄像头",
                    text="先添加区域与摄像头，工作台会自动显示重点画面。",
                    action_text="添加摄像头",
                )
                empty.action_button.clicked.connect(self._add_camera)
                self.dashboard_camera_grid.addWidget(empty, 0, 0, 1, 2)
            clear_layout(self.dashboard_incident_layout)
            incidents = list(context.open_incidents[:5])
            if incidents:
                for incident in incidents:
                    row = self._incident_row(incident, compact=True)
                    self.dashboard_incident_layout.addWidget(row)
                incident_counts = {status: 0 for status in IncidentStatus}
                for incident in context.incidents:
                    incident_counts[incident.status] += 1
                summary = QLabel(
                    f"待处理 {incident_counts[IncidentStatus.OPEN]}   ·   "
                    f"处理中 {incident_counts[IncidentStatus.ACKNOWLEDGED]}   ·   "
                    f"已解决 {incident_counts[IncidentStatus.RESOLVED]}   ·   "
                    f"误报 {incident_counts[IncidentStatus.FALSE_ALARM]}"
                )
                summary.setObjectName("summaryStrip")
                summary.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                summary.setWordWrap(True)
                self.dashboard_incident_layout.addWidget(summary)
            else:
                empty = EmptyState(
                    title="当前没有待处理险情",
                    text="如有异常，可直接点击右上角“上报险情”。",
                )
                self.dashboard_incident_layout.addWidget(empty)

        def _refresh_dashboard_after_resize(self) -> None:
            if self._current_page == "dashboard" and hasattr(self, "dashboard_camera_grid"):
                self._refresh_dashboard()

        def resizeEvent(self, event: Any) -> None:
            super().resizeEvent(event)
            if hasattr(self, "dashboard_camera_grid"):
                self._resize_refresh_timer.start()

        def _filtered_live_cameras(self) -> list[CameraView]:
            site_id = str(self.live_site_filter.currentData() or "")
            area_id = str(self.live_area_filter.currentData() or "")
            status = str(self.live_status_filter.currentData() or "all")
            query = self.live_search.text().strip().lower()
            cameras = []
            for camera in self._context.cameras:
                if site_id and camera.site.id != site_id:
                    continue
                if area_id and camera.area.id != area_id:
                    continue
                if status == "disabled" and not camera.is_disabled:
                    continue
                if status != "all" and status != "disabled" and camera.is_disabled:
                    continue
                if status == "online" and not camera.is_online:
                    continue
                if status == "offline" and not camera.is_offline:
                    continue
                if status == "unknown" and camera.health_status != "unknown":
                    continue
                haystack = f"{camera.site.name} {camera.area.name} {camera.camera.name}".lower()
                if query and query not in haystack:
                    continue
                cameras.append(camera)
            return cameras

        def _on_live_site_changed(self) -> None:
            """Reset the area filter when the operator switches site."""
            if hasattr(self, "live_area_filter"):
                self.live_area_filter.blockSignals(True)
                self.live_area_filter.clear()
                self.live_area_filter.blockSignals(False)
            self._refresh_live()

        def _refresh_live(self) -> None:
            context = self._context
            self._sync_site_filter()
            self._sync_area_filter(self.live_area_filter)
            cameras = self._filtered_live_cameras()
            playable = [camera for camera in cameras if camera.best_stream is not None]
            self.live_wall_button.setEnabled(bool(playable))
            self.live_wall_button.setToolTip(
                f"同时查看最多 4 路画面（当前 {min(4, len(playable))} 路可播放）"
                if playable
                else "至少需要一路可播放码流"
            )
            total_cameras = len(context.cameras)
            if len(cameras) == total_cameras:
                self.live_count.setText(f"共 {total_cameras} 台")
            else:
                self.live_count.setText(f"{len(cameras)} / {total_cameras} 台")
            clear_layout(self.live_grid)
            has_any = bool(context.cameras)
            self.live_page_empty.setVisible(not has_any)
            self.live_grid_widget.setVisible(bool(cameras))
            if cameras:
                for index, camera in enumerate(cameras):
                    card = CameraCard(camera)
                    card.open_requested.connect(self._open_camera)
                    card.manage_requested.connect(self._manage_camera)
                    self.live_grid.addWidget(card, index // 3, index % 3)
                for column in range(3):
                    self.live_grid.setColumnStretch(column, 1)
            elif has_any and not cameras:
                empty = EmptyState(
                    title="没有符合条件的摄像头", text="调整区域、状态或搜索条件后再试。"
                )
                self.live_grid.addWidget(empty, 0, 0, 1, 3)
                self.live_grid_widget.setVisible(True)

        def _open_live_wall(self) -> None:
            playable = [
                camera for camera in self._filtered_live_cameras() if camera.best_stream is not None
            ]
            if not playable:
                QMessageBox.information(
                    self,
                    "暂无可用画面",
                    "当前筛选条件下没有可播放的摄像头，请先检查码流或调整筛选。",
                )
                return
            if len(playable) > LiveWallDialog.MAX_STREAMS:
                picker = LiveWallPickerDialog(
                    tuple(
                        (
                            camera.camera.id,
                            camera.camera.name,
                            f"{camera.site.name} / {camera.area.name} · {camera.protocol_label}",
                        )
                        for camera in playable
                    ),
                    parent=self,
                )
                if picker.exec() != QDialog.Accepted:
                    return
                selected_ids = set(picker.selected_camera_ids)
                playable = [camera for camera in playable if camera.camera.id in selected_ids]
            previews = tuple(
                (
                    camera.camera.name,
                    self._playable_stream_url(camera, camera.best_stream.url),
                )
                for camera in playable[: LiveWallDialog.MAX_STREAMS]
                if camera.best_stream is not None
            )
            if previews:
                LiveWallDialog(previews, parent=self).exec()

        def _sync_site_filter(self) -> None:
            """Keep the site filter in step with the managed sites."""
            if not hasattr(self, "live_site_filter"):
                return
            current = str(self.live_site_filter.currentData() or "")
            self.live_site_filter.blockSignals(True)
            self.live_site_filter.clear()
            self.live_site_filter.addItem("全部场所", "")
            for site in self._context.topology.sites:
                self.live_site_filter.addItem(site.name, site.id)
            index = self.live_site_filter.findData(current)
            self.live_site_filter.setCurrentIndex(max(0, index))
            self.live_site_filter.blockSignals(False)

        def _sync_area_filter(self, combo: QComboBox) -> None:
            current = str(combo.currentData() or "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("全部区域", "")
            site_id = str(self.live_site_filter.currentData() or "")
            for area in self._context.topology.areas:
                if site_id and area.site_id != site_id:
                    continue
                combo.addItem(self._area_option_label(area), area.id)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

        def _area_option_label(self, area: Area) -> str:
            """Label an area with its site so multi-site setups stay distinguishable."""
            try:
                site_name = self._context.topology.get_site(area.site_id).name
            except KeyError:
                return area.name
            return f"{site_name} / {area.name}"

        def _refresh_incidents(self) -> None:
            if not hasattr(self, "incident_list_layout"):
                return
            self._sync_area_filter(self.incident_area_filter)
            area_id = str(self.incident_area_filter.currentData() or "")
            severity = str(self.incident_severity_filter.currentData() or "all")
            status = str(self.incident_status_filter.currentData() or "all")
            query = self.incident_search.text().strip().lower()
            incidents = []
            self._incident_records = {}
            # Searching must reach the whole history, not only the recent
            # snapshot the dashboard keeps in memory.
            source_incidents = self._context.incidents
            if not query and len(source_incidents) >= self.INCIDENT_LIST_LIMIT // 2:
                # The operational snapshot only keeps a short history, so load
                # a larger page when the operator browses the incident list.
                try:
                    source_incidents = tuple(
                        self.store.list_incidents(limit=self.INCIDENT_LIST_LIMIT * 2)
                    )
                except (OSError, RuntimeError, ValueError):
                    source_incidents = self._context.incidents
            if query:
                # Let the database narrow the history first, then confirm the
                # match against labels the database does not store.
                try:
                    source_incidents = tuple(self.store.list_incidents(query=query, limit=5000))
                except (OSError, RuntimeError, ValueError):
                    source_incidents = self._context.incidents
                if not any(
                    self._incident_matches_query(incident, query) for incident in source_incidents
                ):
                    # The keyword may only match a current area or camera
                    # name, which the database does not store.
                    try:
                        source_incidents = tuple(self.store.list_incidents(limit=5000))
                    except (OSError, RuntimeError, ValueError):
                        source_incidents = self._context.incidents
            for incident in source_incidents:
                if area_id and incident.area_id != area_id:
                    continue
                if severity != "all" and str(incident.severity) != severity:
                    continue
                if status != "all" and str(incident.status) != status:
                    continue
                if query and not self._incident_matches_query(incident, query):
                    continue
                incidents.append(incident)
                self._incident_records[incident.id] = incident
            if hasattr(self, "incident_count_label"):
                total_incidents = len(self._context.incidents)
                if len(incidents) == total_incidents:
                    self.incident_count_label.setText(f"共 {total_incidents} 条")
                else:
                    self.incident_count_label.setText(f"{len(incidents)} / {total_incidents} 条")
            if self._selected_incident_id not in self._incident_records:
                self._selected_incident_id = incidents[0].id if incidents else None
            clear_layout(self.incident_list_layout)
            if not incidents:
                empty = EmptyState(
                    title="没有符合条件的险情",
                    text="险情上报后会出现在这里，并记录完整处置过程。",
                    action_text="上报险情",
                )
                empty.action_button.clicked.connect(self._report_incident)
                self.incident_list_layout.addWidget(empty)
            else:
                shown = incidents[: self.INCIDENT_LIST_LIMIT]
                for incident in shown:
                    self.incident_list_layout.addWidget(self._incident_row(incident))
                if len(incidents) > len(shown):
                    remaining = len(incidents) - len(shown)
                    hint = QLabel(
                        f"共 {len(incidents)} 条记录，已显示最近 {len(shown)} 条，"
                        f"还有 {remaining} 条未显示。请使用搜索或筛选缩小范围。"
                    )
                    hint.setObjectName("cardMuted")
                    hint.setWordWrap(True)
                    self.incident_list_layout.addWidget(hint)
            self._refresh_incident_detail()

        def _incident_matches_query(self, incident: Incident, query: str) -> bool:
            """Return whether an incident matches the free-text search."""
            if not query:
                return True
            haystack = " ".join(
                value
                for value in (
                    incident.title,
                    incident.description,
                    incident.ai_summary,
                    incident.reporter,
                    self._context.area_name(incident.area_id, snapshot=incident.area_name_snapshot),
                    self._context.camera_name(
                        incident.camera_id, snapshot=incident.camera_name_snapshot
                    ),
                    incident.area_name_snapshot,
                    incident.camera_name_snapshot,
                    SEVERITY_LABELS.get(incident.severity, ""),
                    STATUS_LABELS.get(incident.status, ""),
                    SOURCE_LABELS.get(incident.source, ""),
                )
                if value
            ).lower()
            return query in haystack

        def _incident_row(self, incident: Incident, *, compact: bool = False) -> IncidentRow:
            row = IncidentRow(
                incident,
                area_name=self._context.area_name(
                    incident.area_id, snapshot=incident.area_name_snapshot
                ),
                camera_name=self._context.camera_name(
                    incident.camera_id, snapshot=incident.camera_name_snapshot
                ),
            )
            row.set_selected(incident.id == self._selected_incident_id)
            row.open_requested.connect(self._select_incident)
            return row

        def _select_incident(self, incident_id: str) -> None:
            self._selected_incident_id = incident_id
            if self._current_page != "incidents":
                self._switch_page("incidents")
            else:
                self._refresh_incidents()

        def _export_incidents(self) -> None:
            """Save the currently filtered incident list as a CSV report."""
            incidents = [
                incident
                for incident in self._context.incidents
                if incident.id in self._incident_records
            ]
            if not incidents:
                QMessageBox.information(self, "没有可导出的险情", "当前筛选条件下没有险情记录。")
                return
            default_name = f"险情清单-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.csv"
            target, _selected = QFileDialog.getSaveFileName(
                self,
                "导出险情清单",
                str(
                    (
                        Path(".").resolve()
                        if self._db_path == ":memory:"
                        else Path(self._db_path).resolve().parent
                    )
                    / default_name
                ),
                "CSV 表格 (*.csv)",
            )
            if not target:
                return
            area_names = {area.id: area.name for area in self._context.topology.areas}
            camera_names = {
                camera.camera.id: camera.camera.name for camera in self._context.cameras
            }
            site_names = {site.id: site.name for site in self._context.topology.sites}
            trail: dict[str, list[str]] = {}
            for incident in incidents:
                entries = self.store.list_incident_audit(incident.id, limit=20)
                trail[incident.id] = [
                    f"{_format_local_timestamp(entry['ts'])[:16]} "
                    f"{_audit_actor_label(entry['actor'])} {_audit_action_label(entry['action'])}"
                    for entry in entries
                ]
            try:
                written = write_incident_csv(
                    target,
                    incidents,
                    area_names=area_names,
                    camera_names=camera_names,
                    site_names=site_names,
                    audit_trail=trail,
                )
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "导出失败", str(exc))
                return
            self.statusBar().showMessage(f"已导出 {len(incidents)} 条险情记录：{written}", 8000)

        def _refresh_incident_detail(self) -> None:
            incident = self._incident_records.get(self._selected_incident_id or "")
            if incident is None:
                self.incident_detail_title.setText("请选择一条险情")
                self.incident_detail_meta.setText("选中后可查看处置动作和完整记录。")
                self.incident_detail_description.setText("")
                self.incident_notification_label.clear()
                self.incident_notification_label.setToolTip("")
                self.incident_notification_detail_label.clear()
                self.incident_notification_detail_label.setVisible(False)
                self.incident_audit_label.clear()
                self.incident_retry_notification_button.setVisible(False)
                self._sync_incident_area_picker(None)
                self._set_incident_actions_enabled(False)
                return
            self.incident_detail_title.setText(incident.title)
            location = " / ".join(
                value
                for value in (
                    self._context.area_name(incident.area_id, snapshot=incident.area_name_snapshot),
                    self._context.camera_name(
                        incident.camera_id, snapshot=incident.camera_name_snapshot
                    ),
                )
                if value and value != "未指定摄像头"
            )
            self.incident_detail_meta.setText(
                f"{SEVERITY_LABELS[incident.severity]} · {STATUS_LABELS[incident.status]} · "
                f"{incident.occurred_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}\n{location}\n"
                f"来源：{SOURCE_LABELS[incident.source]} · 上报人：{incident.reporter or '系统'}"
            )
            self.incident_detail_description.setText(
                incident.description or incident.ai_summary or "用户未填写更多说明。"
            )
            self._sync_incident_area_picker(incident)
            self._set_incident_actions_enabled(True)
            active = str(incident.status) in {"open", "acknowledged"}
            closed = str(incident.status) in {"resolved", "false_alarm"}
            self.incident_ack_button.setVisible(active and str(incident.status) == "open")
            self.incident_resolve_button.setEnabled(active)
            self.incident_resolve_button.setVisible(active)
            self.incident_false_button.setEnabled(active)
            self.incident_false_button.setVisible(active)
            self.incident_reopen_button.setEnabled(closed)
            self.incident_reopen_button.setVisible(closed)
            self._refresh_incident_notification_status(incident.id)
            self._refresh_incident_audit(incident.id)

        def _sync_incident_area_picker(self, incident: Incident | None) -> None:
            """Keep the location picker aligned with the selected incident."""
            if not hasattr(self, "incident_area_edit"):
                return
            choices = _gb_area_choices(self._context.topology)
            current_area = incident.area_id if incident is not None else None
            blocker = self.incident_area_edit.blockSignals(True)
            self.incident_area_edit.clear()
            self.incident_area_edit.addItem("未指定区域", "")
            for label, area in choices:
                self.incident_area_edit.addItem(label, area.id)
            index = self.incident_area_edit.findData(current_area or "")
            self.incident_area_edit.setCurrentIndex(max(index, 0))
            self.incident_area_edit.blockSignals(blocker)
            editable = incident is not None and current_area is None
            self.incident_area_edit.setEnabled(editable)
            self.incident_area_button.setEnabled(editable)
            if incident is not None and current_area is None:
                self.incident_area_button.setText("补充位置")
            elif incident is not None:
                self.incident_area_button.setText("更换位置")

        def _on_incident_area_pick(self) -> None:
            """Avoid reloading the whole console while the operator picks."""
            return

        def _assign_incident_area(self) -> None:
            """File the selected incident under the area chosen by the operator."""
            incident = self._incident_records.get(self._selected_incident_id or "")
            if incident is None:
                return
            area_id = str(self.incident_area_edit.currentData() or "")
            if not area_id:
                QMessageBox.information(self, "补充位置", "请先选择一个区域。")
                return
            try:
                updated = self.store.assign_incident_location(
                    incident.id,
                    area_id=area_id,
                    actor="merchant-console",
                )
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, "补充位置失败", str(exc))
                return
            if not updated:
                QMessageBox.warning(self, "补充位置失败", "险情记录不存在或已被删除。")
                return
            self._refresh_all("险情位置已更新")

        def _refresh_incident_audit(self, incident_id: str) -> None:
            entries = self.store.list_incident_audit(incident_id, limit=6)
            lines = []
            for entry in entries:
                timestamp = _format_local_timestamp(entry["ts"])[:16]
                actor = _audit_actor_label(entry["actor"])
                if entry["action"] == "incident.created":
                    action = "创建险情"
                elif entry["action"] == "incident.location.changed":
                    action = "补充位置"
                else:
                    action = "状态变更"
                    try:
                        detail = json.loads(entry["detail"])
                    except json.JSONDecodeError:
                        detail = {}
                    status = str(detail.get("status", ""))
                    status_label = STATUS_LABELS.get(status, status)
                    note = str(detail.get("note", ""))
                    if note.startswith("reopened from "):
                        action = "重新打开"
                    elif status_label:
                        action = f"状态 → {status_label}"
                    if note and not note.startswith("reopened from "):
                        lines.append(f"{timestamp} · {actor} · {action} · {note}")
                        continue
                lines.append(f"{timestamp} · {actor} · {action}")
            self.incident_audit_label.setText("\n".join(lines) or "暂无处置记录")

        def _set_incident_actions_enabled(self, enabled: bool) -> None:
            self.incident_ack_button.setEnabled(enabled)
            self.incident_resolve_button.setEnabled(enabled)
            self.incident_false_button.setEnabled(enabled)
            self.incident_reopen_button.setEnabled(enabled)
            if not enabled:
                self.incident_ack_button.setVisible(False)
                self.incident_resolve_button.setVisible(False)
                self.incident_false_button.setVisible(False)
                self.incident_reopen_button.setVisible(False)

        def _refresh_incident_notification_status(self, incident_id: str) -> None:
            if not hasattr(self, "incident_notification_label"):
                return
            deliveries = self.store.list_incident_notifications(incident_id)
            notifications_available = self._notification_config.available
            if not deliveries:
                label = "通知：未启用" if not notifications_available else "通知：尚未发送"
                self.incident_notification_label.setText(label)
                self.incident_notification_label.setToolTip("")
                self.incident_notification_detail_label.clear()
                self.incident_notification_detail_label.setVisible(False)
                self.incident_retry_notification_button.setVisible(False)
                return

            labels = []
            failures = []
            tooltip_lines = []
            retryable_failed = False
            enabled_channels = set(self._notification_config.enabled_channels)
            for item in deliveries:
                is_failed = item["status"] == "failed"
                is_enabled = str(item["channel"]) in enabled_channels
                if item["status"] == "sent":
                    status = "已发送"
                elif notifications_available and not is_enabled:
                    status = "已停用"
                else:
                    status = "失败"
                channel_label = _notification_channel_label(item["channel"])
                labels.append(f"{channel_label} {status}（尝试 {item['attempts']} 次）")
                if is_failed:
                    reason = str(item["detail"]).strip() or "未提供错误详情"
                    failures.append(f"{channel_label}：{reason}")
                tooltip_lines.append(
                    f"{channel_label} · {status} · 尝试 {item['attempts']} 次 · "
                    f"{_format_local_timestamp(str(item['updated_at']))}"
                    f"\n{item['detail'] or '无详情'}"
                )
                retryable_failed = retryable_failed or (
                    is_failed and notifications_available and is_enabled
                )

            prefix = "通知已停用；历史记录：" if not notifications_available else "通知："
            self.incident_notification_label.setText(prefix + " · ".join(labels))
            self.incident_notification_label.setToolTip("\n\n".join(tooltip_lines))
            if failures:
                summary = (
                    "历史失败 · " if not notifications_available else "最近失败 · "
                ) + "；".join(failures)
                self.incident_notification_detail_label.setText(
                    summary if len(summary) <= 220 else summary[:217] + "…"
                )
                self.incident_notification_detail_label.setToolTip("\n\n".join(tooltip_lines))
                self.incident_notification_detail_label.setVisible(True)
            else:
                self.incident_notification_detail_label.clear()
                self.incident_notification_detail_label.setVisible(False)
            self.incident_retry_notification_button.setVisible(retryable_failed)

        def _retry_incident_notification(self) -> None:
            incident = self._incident_records.get(self._selected_incident_id or "")
            if incident is None:
                return
            failed_channels = self.store.failed_incident_notification_channels(incident.id)
            enabled_channels = set(self._notification_config.enabled_channels)
            channels = tuple(channel for channel in failed_channels if channel in enabled_channels)
            if not channels:
                self.statusBar().showMessage("没有可重试的已启用通知渠道", 4000)
                return
            self._dispatch_incident_notification(incident, channels=channels)
            self.statusBar().showMessage("正在重试险情通知…", 3000)

        def _reopen_incident(self) -> None:
            incident = self._incident_records.get(self._selected_incident_id or "")
            if incident is None:
                return
            answer = QMessageBox.question(
                self,
                "重新打开险情",
                f"确定将“{incident.title}”重新打开吗？\n\n"
                "该险情会重新回到待处理队列，并记录一次状态纠错。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                self._set_incident_status(
                    IncidentStatus.OPEN,
                    note=f"reopened from {incident.status}",
                )

        def _incident_note(self) -> str:
            """Return the operator-typed disposition note, if any."""
            if not hasattr(self, "incident_note_edit"):
                return ""
            return self.incident_note_edit.text().strip()

        def _set_incident_status(self, status: IncidentStatus, *, note: str = "") -> None:
            if not self._selected_incident_id:
                return
            if not note:
                note = self._incident_note()
            try:
                updated = self.store.update_incident_status(
                    self._selected_incident_id,
                    status,
                    actor="merchant-console",
                    note=note,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "状态变更无效", str(exc))
                return
            if updated:
                if hasattr(self, "incident_note_edit"):
                    self.incident_note_edit.clear()
                self._refresh_all(f"险情状态已更新为：{STATUS_LABELS[status]}")
            else:
                QMessageBox.warning(self, "更新失败", "险情记录不存在、已被删除或已是当前状态。")

        def _report_incident(self) -> None:
            dialog = QuickIncidentDialog(self._context, self)
            if dialog.exec() != QDialog.Accepted or dialog.incident is None:
                return
            if self.store.create_incident(dialog.incident, actor="merchant-console"):
                self._selected_incident_id = dialog.incident.id
                self._refresh_all("险情已上报，并保存到本机")
                self._switch_page("incidents")
                self._dispatch_incident_notification(dialog.incident)
            else:
                QMessageBox.warning(self, "上报失败", "险情记录无法保存，请稍后重试。")

        def _refresh_devices(self) -> None:
            if not hasattr(self, "area_grid"):
                return
            self._refresh_site_bar()
            clear_layout(self.area_grid)
            if not self._context.topology.areas:
                empty = EmptyState(
                    title="先创建一个区域",
                    text="例如“东门”“仓库”“停车场”，再为区域添加摄像头。",
                    action_text="新建区域",
                )
                empty.action_button.clicked.connect(self._add_area)
                self.area_grid.addWidget(empty, 0, 0)
            else:
                for index, area in enumerate(self._context.topology.areas):
                    try:
                        site = self._context.topology.get_site(area.site_id)
                    except KeyError:
                        site = Site(area.site_id, "未知场所")
                    area_cameras = [
                        camera for camera in self._context.cameras if camera.area.id == area.id
                    ]
                    online = sum(1 for camera in area_cameras if camera.is_online)
                    unknown = sum(
                        1
                        for camera in area_cameras
                        if not camera.is_disabled and not camera.is_online and not camera.is_offline
                    )
                    disabled = sum(1 for camera in area_cameras if camera.is_disabled)
                    incidents = sum(
                        1
                        for incident in self._context.open_incidents
                        if incident.area_id == area.id
                    )
                    area_card = AreaCard(
                        area_id=area.id,
                        name=area.name,
                        site_name=site.name,
                        camera_count=len(area_cameras),
                        online_count=online,
                        unknown_count=unknown,
                        disabled_count=disabled,
                        open_incidents=incidents,
                    )
                    area_card.manage_requested.connect(self._manage_area)
                    self.area_grid.addWidget(
                        area_card,
                        index // 3,
                        index % 3,
                    )
                for column in range(3):
                    self.area_grid.setColumnStretch(column, 1)
            clear_layout(self.devices_camera_grid)
            self._sync_area_filter(self.devices_area_filter)
            selected_area = str(self.devices_area_filter.currentData() or "")
            query = self.devices_search.text().strip().lower()
            visible_cameras = [
                camera
                for camera in self._context.cameras
                if (not selected_area or camera.area.id == selected_area)
                and (
                    not query
                    or query
                    in f"{camera.site.name} {camera.area.name} {camera.camera.name} {camera.camera.address}".lower()
                )
            ]
            total_cameras = len(self._context.cameras)
            if len(visible_cameras) == total_cameras:
                self.devices_count_label.setText(f"共 {total_cameras} 台")
            else:
                self.devices_count_label.setText(f"{len(visible_cameras)} / {total_cameras} 台")
            if not self._context.cameras:
                empty = EmptyState(
                    title="还没有接入摄像头",
                    text="支持 ONVIF 自动发现、GB/T 28181 国标注册和 RTSP 手动地址。",
                    action_text="添加摄像头",
                )
                empty.action_button.clicked.connect(self._add_camera)
                self.devices_camera_grid.addWidget(empty, 0, 0)
            else:
                for index, camera in enumerate(visible_cameras):
                    card = CameraCard(camera)
                    card.open_requested.connect(self._open_camera)
                    card.manage_requested.connect(self._manage_camera)
                    self.devices_camera_grid.addWidget(card, index // 3, index % 3)
                for column in range(3):
                    self.devices_camera_grid.setColumnStretch(column, 1)
                if not visible_cameras:
                    empty = EmptyState(
                        title="没有符合条件的摄像头",
                        text="调整区域筛选或搜索关键词后再试。",
                    )
                    self.devices_camera_grid.addWidget(empty, 0, 0, 1, 3)

        def _manage_area(self, area_id: str) -> None:
            area = next(
                (item for item in self._context.topology.areas if item.id == area_id),
                None,
            )
            if area is None:
                return
            dialog = QMessageBox(self)
            dialog.setWindowTitle("管理区域")
            dialog.setIcon(QMessageBox.Question)
            dialog.setText(f"区域：{area.name}")
            dialog.setInformativeText("可以修改名称，或在区域为空时删除。")
            edit_button = dialog.addButton("编辑", QMessageBox.AcceptRole)
            delete_button = dialog.addButton("删除", QMessageBox.DestructiveRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is edit_button:
                self._edit_area(area)
            elif dialog.clickedButton() is delete_button:
                self._delete_area(area)

        def _edit_area(self, area: Area) -> None:
            dialog = AreaDialog(self._context.topology, self, area=area)
            if dialog.exec() != QDialog.Accepted or dialog.area is None or dialog.site is None:
                return
            try:
                self.store.save_area_bundle(dialog.site, dialog.area, actor="merchant-console")
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "保存区域失败", str(exc))
                return
            self._refresh_all(f"区域“{dialog.area.name}”已更新")

        def _delete_area(self, area: Area) -> None:
            has_children = any(item.parent_id == area.id for item in self._context.topology.areas)
            has_cameras = any(item.area.id == area.id for item in self._context.cameras)
            if has_children or has_cameras:
                QMessageBox.warning(
                    self,
                    "无法删除区域",
                    "该区域仍包含摄像头或子区域。请先移除关联内容后再删除。",
                )
                return
            answer = QMessageBox.question(
                self,
                "删除区域",
                f"确定删除区域“{area.name}”吗？此操作不可撤销。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.store.delete_area(area.id, actor="merchant-console")
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "删除区域失败", str(exc))
                return
            self._refresh_all(f"区域“{area.name}”已删除")

        def _manage_camera(self, camera_id: str) -> None:
            camera = next(
                (item for item in self._context.cameras if item.camera.id == camera_id),
                None,
            )
            if camera is None:
                return
            dialog = QMessageBox(self)
            dialog.setWindowTitle("管理摄像头")
            dialog.setIcon(QMessageBox.Question)
            dialog.setText(f"摄像头：{camera.camera.name}")
            dialog.setInformativeText(
                "可编辑连接参数、暂停接入进行维护，或删除该摄像头。历史险情会保留。"
            )
            edit_button = dialog.addButton("编辑", QMessageBox.AcceptRole)
            toggle_button = dialog.addButton(
                "恢复接入" if camera.is_disabled else "暂停接入",
                QMessageBox.ActionRole,
            )
            delete_button = dialog.addButton("删除", QMessageBox.DestructiveRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is edit_button:
                self._edit_camera(camera)
            elif dialog.clickedButton() is toggle_button:
                self._set_camera_enabled(camera, camera.is_disabled)
            elif dialog.clickedButton() is delete_button:
                self._delete_camera(camera)

        def _set_camera_enabled(self, camera: CameraView, enabled: bool) -> None:
            try:
                self.store.set_camera_enabled(camera.camera.id, enabled, actor="merchant-console")
            except (KeyError, OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "更新摄像头失败", str(exc))
                return
            action = "已恢复接入" if enabled else "已暂停接入"
            self._refresh_all(f"摄像头“{camera.camera.name}”{action}")
            self._sync_native_subscriptions()

        def _edit_camera(self, camera: CameraView) -> None:
            shared_device = bool(
                camera.camera.device_id
                and sum(
                    1
                    for item in self._context.topology.cameras
                    if item.device_id == camera.camera.device_id
                )
                > 1
            )
            dialog = CameraEditDialog(
                self._context.topology,
                camera,
                allow_area_change=not shared_device,
                parent=self,
            )
            if dialog.exec() != QDialog.Accepted or dialog.device is None or dialog.camera is None:
                return
            duplicate = self._find_duplicate_camera(dialog.device, dialog.camera, dialog.stream)
            if duplicate is not None and duplicate.camera.id != camera.camera.id:
                QMessageBox.information(
                    self,
                    "摄像头已存在",
                    f"该连接已接入为“{duplicate.camera.name}”（{duplicate.area.name}）。\n\n"
                    "请编辑现有摄像头，避免重复取流和重复告警。",
                )
                return
            endpoint_changed = bool(
                camera.device is not None
                and dialog.device is not None
                and camera.device.endpoint != dialog.device.endpoint
            )
            credentials_changed = dialog.credentials is not None and not dialog.credentials.is_empty
            connection_changed = endpoint_changed or credentials_changed
            try:
                if (
                    connection_changed
                    and self._native_subscription_manager is not None
                    and camera.device is not None
                ):
                    self._native_subscription_manager.stop_native_subscription(camera.device.id)
                submitted_credentials = (
                    self._merge_edited_credentials(camera.device, dialog.credentials)
                    if credentials_changed
                    else None
                )
                self._save_camera_bundle_with_credentials(
                    dialog.device,
                    dialog.camera,
                    dialog.stream,
                    submitted_credentials,
                    previous_device=camera.device,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "保存摄像头失败", str(exc))
                return
            health_warning = ""
            if connection_changed:
                stream_ids = {stream.id for stream in camera.streams}
                if dialog.stream is not None:
                    stream_ids.add(dialog.stream.id)
                for stream_id in stream_ids:
                    try:
                        self.store.set_stream_health(
                            stream_id,
                            "unknown",
                            "连接参数已更新，等待重新检测",
                            actor="merchant-console",
                        )
                    except (OSError, RuntimeError, ValueError) as exc:
                        health_warning = str(exc)
                        break
            self._refresh_all(f"摄像头“{dialog.camera.name}”已更新")
            self._sync_native_subscriptions()
            if health_warning:
                QMessageBox.warning(
                    self,
                    "摄像头已更新",
                    "摄像头配置已保存，但旧健康状态未能全部失效；"
                    "请手动刷新设备状态后再判断在线情况。\n\n"
                    f"{health_warning}",
                )

        def _save_camera_bundle_with_credentials(
            self,
            device: VideoDevice,
            camera: CameraDevice,
            stream: VideoStream | None,
            submitted_credentials: DeviceCredentials | None,
            *,
            previous_device: VideoDevice | None = None,
        ) -> VideoDevice:
            previous_ref = previous_device.credentials_ref if previous_device is not None else None
            previous_blob = (
                self._config_store.get_device_credential_blob(previous_ref)
                if previous_ref
                else None
            )
            new_ref = device.credentials_ref or previous_ref or f"cred-{device.id}"
            wrote_ref = ""
            try:
                if submitted_credentials is not None and not submitted_credentials.is_empty:
                    self._config_store.save_device_credential(
                        new_ref, self._vault.encode(submitted_credentials)
                    )
                    wrote_ref = new_ref
                    device = replace(device, credentials_ref=new_ref)
                self.store.save_camera_bundle(device, camera, stream, actor="merchant-console")
            except Exception:
                if wrote_ref:
                    try:
                        if previous_blob is not None:
                            self._config_store.save_device_credential(
                                previous_ref or new_ref, previous_blob
                            )
                        if previous_ref != new_ref:
                            self._config_store.delete_device_credential(new_ref)
                    except (OSError, RuntimeError, ValueError):
                        pass
                raise
            if wrote_ref and previous_ref and previous_ref != new_ref:
                try:
                    self._config_store.delete_device_credential(previous_ref)
                except (OSError, RuntimeError, ValueError):
                    pass
            return device

        def _merge_edited_credentials(
            self,
            device: VideoDevice | None,
            submitted: DeviceCredentials,
        ) -> DeviceCredentials:
            existing = DeviceCredentials()
            if device is not None and device.credentials_ref:
                blob = self._config_store.get_device_credential_blob(device.credentials_ref)
                if blob:
                    try:
                        existing = self._vault.decode(blob)
                    except SecretProtectionError as exc:
                        raise ValueError(
                            "无法读取现有设备凭据，请重新填写完整账号和密码。"
                        ) from exc
            return DeviceCredentials(
                username=submitted.username or existing.username,
                password=submitted.password or existing.password,
                token=submitted.token or existing.token,
            )

        def _delete_camera(self, camera: CameraView) -> None:
            answer = QMessageBox.question(
                self,
                "删除摄像头",
                f"确定删除摄像头“{camera.camera.name}”吗？\n关联码流会被移除，历史险情仍会保留。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            last_device_channel = False
            if camera.device is not None:
                topology = self.store.load_topology()
                last_device_channel = (
                    sum(1 for item in topology.cameras if item.device_id == camera.device.id) <= 1
                )
            try:
                credential_ref = self.store.delete_camera(
                    camera.camera.id, actor="merchant-console"
                )
                if (
                    last_device_channel
                    and self._native_subscription_manager is not None
                    and camera.device is not None
                ):
                    self._native_subscription_manager.stop_native_subscription(camera.device.id)
            except (KeyError, OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "删除摄像头失败", str(exc))
                return
            credential_warning = ""
            if credential_ref:
                try:
                    self._config_store.delete_device_credential(credential_ref)
                except (OSError, RuntimeError, ValueError) as exc:
                    credential_warning = str(exc)
            message = f"摄像头“{camera.camera.name}”已删除"
            if credential_warning:
                message += "；设备凭据清理失败，请在配置文件中手工移除"
            self._refresh_all(message)
            self._sync_native_subscriptions()
            if credential_warning:
                QMessageBox.warning(
                    self,
                    "摄像头已删除",
                    "摄像头记录已删除，但本地配置文件中的设备凭据未能清理。\n\n"
                    f"{credential_warning}\n\n请稍后打开配置文件检查 credentials 段。",
                )

        def _refresh_site_bar(self) -> None:
            """Show every managed site with a quick management entry."""
            if not hasattr(self, "site_bar"):
                return
            layout = self.site_bar.layout()
            while layout.count() > 2:
                item = layout.takeAt(1)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            for site in self._context.topology.sites:
                areas = sum(1 for area in self._context.topology.areas if area.site_id == site.id)
                cameras = sum(1 for camera in self._context.cameras if camera.site.id == site.id)
                label = QLabel(f"{site.name} · {areas} 个区域 · {cameras} 台摄像头")
                label.setObjectName("cardMuted")
                layout.insertWidget(layout.count() - 1, label)
                button = QPushButton("管理")
                button.setObjectName("linkButton")
                button.setMaximumWidth(52)
                button.clicked.connect(lambda _checked=False, s=site: self._manage_site(s))
                layout.insertWidget(layout.count() - 1, button)

        def _add_site(self) -> None:
            dialog = SiteDialog(self._context.topology, self)
            if dialog.exec() != QDialog.Accepted or dialog.site is None:
                return
            try:
                self.store.save_site(dialog.site, actor="merchant-console")
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "创建场所失败", str(exc))
                return
            self._refresh_all(f"场所“{dialog.site.name}”已创建")

        def _rename_site(self, site: Site) -> None:
            dialog = SiteDialog(self._context.topology, self, site=site)
            if dialog.exec() != QDialog.Accepted or dialog.site is None:
                return
            try:
                self.store.save_site(dialog.site, actor="merchant-console")
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "保存场所失败", str(exc))
                return
            self._refresh_all(f"场所“{dialog.site.name}”已更新")

        def _delete_site(self, site: Site) -> None:
            answer = QMessageBox.question(
                self,
                "删除场所",
                f"确定删除场所“{site.name}”吗？此操作不可撤销。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                self.store.delete_site(site.id, actor="merchant-console")
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "删除场所失败", str(exc))
                return
            self._refresh_all(f"场所“{site.name}”已删除")

        def _manage_site(self, site: Site) -> None:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("管理场所")
            dialog.setIcon(QMessageBox.Question)
            dialog.setText(f"场所：{site.name}")
            dialog.setInformativeText("可以修改名称，或在场所下没有区域时删除。")
            edit_button = dialog.addButton("编辑", QMessageBox.AcceptRole)
            delete_button = dialog.addButton("删除", QMessageBox.DestructiveRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is edit_button:
                self._rename_site(site)
            elif dialog.clickedButton() is delete_button:
                self._delete_site(site)

        def _add_area(self) -> None:
            dialog = AreaDialog(self._context.topology, self)
            if dialog.exec() != QDialog.Accepted or dialog.area is None or dialog.site is None:
                return
            try:
                self.store.save_area_bundle(dialog.site, dialog.area, actor="merchant-console")
            except Exception as exc:
                QMessageBox.warning(self, "创建区域失败", str(exc))
                return
            self._refresh_all(f"区域“{dialog.area.name}”已创建")

        def _add_camera(self) -> None:
            if not self._context.topology.areas:
                self._add_area()
                if not self._context.topology.areas:
                    return
            preferred = (
                str(self.live_area_filter.currentData() or "")
                if hasattr(self, "live_area_filter")
                else None
            )
            dialog = CameraAccessDialog(
                self._context.topology, parent=self, preferred_area_id=preferred
            )
            if self._last_discovered_onvif:
                dialog.protocol_combo.setCurrentIndex(dialog.protocol_combo.findData("onvif"))
                dialog.endpoint_edit.setText(self._last_discovered_onvif[0])
            if dialog.exec() != QDialog.Accepted or dialog.camera is None or dialog.device is None:
                return
            duplicate = self._find_duplicate_camera(dialog.device, dialog.camera, dialog.stream)
            if duplicate is not None:
                QMessageBox.information(
                    self,
                    "摄像头已存在",
                    f"该连接已接入为“{duplicate.camera.name}”（{duplicate.area.name}）。\n\n"
                    "请直接在现有摄像头卡片中编辑或恢复接入，避免重复取流和重复告警。",
                )
                return
            try:
                self._save_camera_bundle_with_credentials(
                    dialog.device,
                    dialog.camera,
                    dialog.stream,
                    dialog.credentials,
                )
            except Exception as exc:
                QMessageBox.warning(self, "保存摄像头失败", str(exc))
                return
            self._refresh_all(f"摄像头“{dialog.camera.name}”已添加")
            self._sync_native_subscriptions()

        def _find_duplicate_camera(
            self,
            device: VideoDevice,
            camera: CameraDevice,
            stream: VideoStream | None = None,
        ) -> CameraView | None:
            protocol = str(device.protocol)
            new_device_id = device.device_id.strip().casefold()
            channel_id = camera.channel_id.strip().casefold() or "ch1"
            endpoint = device.endpoint.strip().rstrip("/").casefold()
            new_urls = {
                value.strip().rstrip("/").casefold()
                for value in (device.endpoint, stream.url if stream is not None else "")
                if value.strip()
            }
            for existing in self._context.cameras:
                if existing.protocol != protocol:
                    continue
                if protocol == "rtsp":
                    existing_urls = {
                        value.strip().rstrip("/").casefold()
                        for value in (
                            existing.device.endpoint if existing.device is not None else "",
                            existing.camera.address,
                            *(item.url for item in existing.streams),
                        )
                        if value.strip()
                    }
                    if new_urls & existing_urls:
                        return existing
                    continue
                existing_device_id = (
                    existing.device.device_id.strip().casefold()
                    if existing.device is not None
                    else ""
                )
                existing_endpoint = (
                    existing.device.endpoint.strip().rstrip("/").casefold()
                    if existing.device is not None
                    else existing.camera.address.strip().rstrip("/").casefold()
                )
                existing_channel = existing.camera.channel_id.strip().casefold() or "ch1"
                if protocol == "gb28181":
                    if (
                        new_device_id
                        and new_device_id == existing_device_id
                        and channel_id == existing_channel
                    ):
                        return existing
                elif endpoint and endpoint == existing_endpoint and channel_id == existing_channel:
                    return existing
            return None

        def _scan_onvif(self) -> None:
            if self._discovery_thread is not None:
                self.statusBar().showMessage("ONVIF 扫描正在进行，请稍候…", 3000)
                return
            self.onvif_scan_button.setEnabled(False)
            self.onvif_scan_button.setText("扫描中…")
            self.statusBar().showMessage("正在扫描局域网 ONVIF 设备…", 3000)
            thread = QThread(self)
            self._discovery_thread = thread
            self._discovery_worker = OnvifDiscoveryWorker()
            self._discovery_worker.moveToThread(thread)
            thread.started.connect(self._discovery_worker.run)
            self._discovery_worker.finished.connect(self._on_onvif_discovered)
            self._discovery_worker.finished.connect(lambda *_args: _quit_thread_soon(thread))
            self._discovery_worker.finished.connect(self._discovery_worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.start()

        def _on_onvif_discovered(self, result: Any) -> None:
            self._discovery_thread = None
            self._discovery_worker = None
            self.onvif_scan_button.setEnabled(True)
            self.onvif_scan_button.setText("扫描 ONVIF")
            if isinstance(result, Exception):
                QMessageBox.warning(self, "ONVIF 扫描失败", str(result))
                return
            devices = tuple(result or ())
            self._last_discovered_onvif = [item.endpoint for item in devices]
            if not devices:
                QMessageBox.information(
                    self,
                    "ONVIF 扫描",
                    "没有发现设备。请确认摄像头与本机在同一局域网，并已开启 ONVIF。",
                )
                return

            def discovery_label(item: Any) -> str:
                endpoint = str(getattr(item, "endpoint", "")).strip()
                scopes = tuple(getattr(item, "scopes", ()) or ())
                name = ""
                for scope in scopes:
                    lowered = scope.lower()
                    for marker in ("/name/", "/hardware/"):
                        if marker in lowered:
                            name = scope.split(marker, 1)[1].strip().replace("_", " ")
                            break
                    if name:
                        break
                return f"{name} · {endpoint}" if name else endpoint

            labels = [discovery_label(item) for item in devices]
            label, accepted = QInputDialog.getItem(
                self,
                "选择 ONVIF 设备",
                f"发现 {len(devices)} 台设备，请选择要添加的摄像头：",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            selected = devices[labels.index(label)]
            self._last_discovered_onvif = [selected.endpoint] + [
                item.endpoint for item in devices if item.endpoint != selected.endpoint
            ]
            self._add_camera()

        def _scan_stream_health(self) -> None:
            self._start_stream_health_scan(show_empty_message=True)

        def _auto_scan_stream_health(self) -> None:
            self._start_stream_health_scan(show_empty_message=False)

        def _start_stream_health_scan(self, *, show_empty_message: bool) -> None:
            specs_list: list[tuple[str, str]] = []
            stream_labels: dict[str, str] = {}
            for camera in self._context.cameras:
                stream = camera.best_stream
                if stream is None:
                    continue
                specs_list.append((stream.id, self._playable_stream_url(camera, stream.url)))
                stream_labels[stream.id] = camera.camera.name
            specs = tuple(specs_list)
            if not specs:
                if not show_empty_message:
                    return
                if not self._context.cameras:
                    message = "还没有可检测的视频流。请先添加摄像头。"
                elif len(self._context.disabled_cameras) == len(self._context.cameras):
                    message = "所有摄像头均已暂停接入。请先在“区域与设备”中恢复摄像头。"
                else:
                    message = "当前没有可检测的启用码流，请检查摄像头码流配置。"
                QMessageBox.information(self, "设备状态", message)
                return
            if self._health_thread is not None:
                return
            try:
                from factoryguard.media.ffmpeg import find_ffmpeg

                ffmpeg_ready = find_ffmpeg() is not None
            except Exception:
                ffmpeg_ready = False
            if not ffmpeg_ready:
                message = "未找到 FFmpeg，无法检测视频流。请先在系统设置中检查运行环境。"
                if show_empty_message:
                    QMessageBox.warning(self, "无法检测设备状态", message)
                else:
                    self.statusBar().showMessage("未找到 FFmpeg，本轮自动设备检测已跳过", 5000)
                return
            self.live_scan_button.setEnabled(False)
            self.statusBar().showMessage(f"开始检测 {len(specs)} 台摄像头…", 4000)
            thread = QThread(self)
            self._health_thread = thread
            self._health_worker = StreamHealthWorker(db_path=self._db_path, stream_specs=specs)
            self._health_worker.moveToThread(thread)
            thread.started.connect(self._health_worker.run)
            self._health_worker.progress.connect(
                lambda index, total, stream_id: self.statusBar().showMessage(
                    f"正在检测 {index}/{total} · {stream_labels.get(stream_id, '摄像头')}"
                )
            )
            self._health_worker.finished.connect(self._on_health_scan_finished)
            self._health_worker.finished.connect(lambda *_args: _quit_thread_soon(thread))
            self._health_worker.finished.connect(self._health_worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.start()

        def _on_health_scan_finished(self, statuses: dict) -> None:
            self._health_thread = None
            self._health_worker = None
            self.live_scan_button.setEnabled(True)
            total = len(statuses)
            online = sum(1 for value in statuses.values() if value == "online")
            offline = sum(1 for value in statuses.values() if value == "offline")
            unknown = total - online - offline
            message = f"摄像头检测完成：在线 {online}/{total}"
            if offline:
                message += f" · 离线 {offline}"
            if unknown:
                message += f" · 未确认 {unknown}"
            self._refresh_all(message)

        def _playable_stream_url(self, camera: CameraView, url: str) -> str:
            credentials_ref = camera.device.credentials_ref if camera.device is not None else None
            if not credentials_ref or self._vault is None:
                return url
            blob = self._config_store.get_device_credential_blob(credentials_ref)
            if not blob:
                return url
            try:
                return self._vault.decode(blob).with_password_in_url(url)
            except SecretProtectionError:
                return url

        def _should_use_cached_stream(self, camera: CameraView, stream: VideoStream) -> bool:
            if not self._cached_stream_matches_device(camera, stream.url):
                return False
            endpoint = camera.device.endpoint.strip() if camera.device is not None else ""
            return not (
                camera.protocol == "onvif"
                and endpoint.lower().startswith(("http://", "https://"))
                and camera.health_status == "unknown"
            )

        def _cached_stream_matches_device(self, camera: CameraView, url: str) -> bool:
            if camera.device is None:
                return True
            endpoint = camera.device.endpoint.strip()
            if not endpoint.lower().startswith(("http://", "https://")):
                return True
            try:
                device_parts = urlsplit(endpoint)
                stream_parts = urlsplit(url)
            except ValueError:
                return True
            device_host = device_parts.hostname
            stream_host = stream_parts.hostname
            if not device_host or not stream_host:
                return True
            if device_host.casefold() != stream_host.casefold():
                return False
            # The same camera can be reachable on another port; reusing the old
            # stream there would silently keep decoding the previous address.
            device_port = device_parts.port
            stream_port = stream_parts.port
            if device_port is not None and stream_port is not None:
                return device_port == stream_port
            return True

        def _open_camera(self, camera_id: str) -> None:
            camera = next(
                (item for item in self._context.cameras if item.camera.id == camera_id), None
            )
            if camera is None:
                return
            if camera.is_disabled:
                QMessageBox.information(
                    self,
                    "摄像头已停用",
                    "该摄像头当前已暂停接入。请在“区域与设备”中打开管理并选择“恢复接入”。",
                )
                return
            stream = camera.best_stream
            if stream is not None and not self._should_use_cached_stream(camera, stream):
                stream = None
            if stream is not None:
                url = self._playable_stream_url(camera, stream.url)
                dialog = LivePreviewDialog(title=camera.camera.name, url=url, parent=self)
                dialog.exec()
                return
            if camera.protocol in {"onvif", "gb28181"} and camera.device is not None:
                if camera.protocol == "gb28181" and self._gb_platform is None:
                    QMessageBox.information(
                        self,
                        "国标服务未启用",
                        "请先在“系统设置”启用 GB/T 28181 国标服务，并确认设备已注册。",
                    )
                    return
                if self._resolve_thread is not None:
                    return
                self.statusBar().showMessage(
                    f"正在向 {camera.protocol_label} 摄像头获取实时码流…", 4000
                )
                thread = QThread(self)
                self._resolve_thread = thread
                self._resolve_worker = DeviceStreamResolveWorker(
                    db_path=self._db_path,
                    device_id=camera.device.id,
                    channel_id=camera.camera.channel_id,
                    gb_platform=self._gb_platform,
                    config_path=str(self._config_store.path),
                )
                self._resolve_worker.moveToThread(thread)
                thread.started.connect(self._resolve_worker.run)
                self._resolve_worker.finished.connect(
                    lambda result: self._on_stream_resolved(camera, result)
                )
                self._resolve_worker.finished.connect(lambda *_args: _quit_thread_soon(thread))
                self._resolve_worker.finished.connect(self._resolve_worker.deleteLater)
                thread.finished.connect(thread.deleteLater)
                thread.start()
                return
            QMessageBox.information(
                self,
                "暂无视频流",
                "该设备尚未上报可用码流。对于 GB/T 28181 设备，请确认国标服务已启用且设备已注册；随后刷新设备状态。",
            )

        def _on_stream_resolved(self, camera: CameraView, result: ResolvedStream) -> None:
            self._resolve_thread = None
            self._resolve_worker = None
            if result.error or not result.uri:
                if result.adapter is not None:
                    result.adapter.close()
                QMessageBox.warning(
                    self,
                    "获取视频流失败",
                    _stream_failure_hint(camera, result.error),
                )
                return
            if camera.protocol == "onvif" and result.uri.lower().startswith(
                ("rtsp://", "rtsps://")
            ):
                sanitized, _username, _password = _split_url_credentials(result.uri)
                existing_stream = camera.best_stream
                stream = (
                    replace(
                        existing_stream,
                        camera_id=camera.camera.id,
                        url=sanitized,
                        device_id=camera.device.id if camera.device is not None else None,
                    )
                    if existing_stream is not None
                    else VideoStream(
                        id=f"stream-{uuid4().hex[:12]}",
                        camera_id=camera.camera.id,
                        name="设备实时码流",
                        url=sanitized,
                        role=StreamRole.SUB,
                        fps=2.0,
                        analysis_policy="native_only",
                        device_id=camera.device.id if camera.device is not None else None,
                    )
                )
                try:
                    self.store.save_stream(stream, actor="merchant-console")
                    self.store.set_stream_health(
                        stream.id, "online", "ONVIF 码流解析成功", actor="merchant-console"
                    )
                    self._context = build_operational_context(self.store)
                    self._refresh_all()
                except Exception as exc:
                    self.statusBar().showMessage(f"实时码流已连接，但保存码流信息失败：{exc}", 6000)
            try:
                dialog = LivePreviewDialog(title=camera.camera.name, url=result.uri, parent=self)
                dialog.exec()
            finally:
                if result.adapter is not None:
                    result.adapter.close()

        def _refresh_assistant_context(self) -> None:
            if not hasattr(self, "assistant_context_cameras"):
                return
            active_count = len(self._context.active_cameras)
            camera_parts = [f"{len(self._context.online_cameras)}/{active_count} 在线"]
            if self._context.offline_cameras:
                camera_parts.append(f"{len(self._context.offline_cameras)} 离线")
            if self._context.unknown_cameras:
                camera_parts.append(f"{len(self._context.unknown_cameras)} 待检测")
            if self._context.disabled_cameras:
                camera_parts.append(f"{len(self._context.disabled_cameras)} 停用")
            self.assistant_context_cameras.setText(" · ".join(camera_parts))
            self.assistant_context_areas.setText(str(len(self._context.topology.areas)))
            self.assistant_context_incidents.setText(str(len(self._context.open_incidents)))
            self.assistant_context_model.setText(
                self._llm_config.display_name if self._llm_config.available else "本地规则"
            )
            if self._context.cameras:
                if active_count:
                    status_parts = [
                        f"摄像头 {len(self._context.online_cameras)}/{active_count} 在线"
                    ]
                    if self._context.offline_cameras:
                        status_parts.append(f"{len(self._context.offline_cameras)} 离线")
                    if self._context.unknown_cameras:
                        status_parts.append(f"{len(self._context.unknown_cameras)} 待检测")
                    if self._context.disabled_cameras:
                        status_parts.append(f"{len(self._context.disabled_cameras)} 停用")
                    status_parts.append(f"险情 {len(self._context.open_incidents)} 条待处理")
                    self.sidebar_state_text.setText(" · ".join(status_parts))
                else:
                    self.sidebar_state_text.setText(
                        f"所有摄像头已暂停接入 · 险情 {len(self._context.open_incidents)} 条待处理"
                    )
                if not active_count:
                    sidebar_state = "● 已暂停"
                elif self._context.high_incidents or self._context.offline_cameras:
                    sidebar_state = "● 需要关注"
                elif self._context.open_incidents:
                    sidebar_state = "● 有待处理险情"
                elif self._context.unknown_cameras:
                    sidebar_state = "● 状态待确认"
                else:
                    sidebar_state = "● 运行正常"
                self.sidebar_ai_state.setText(sidebar_state)
            else:
                self.sidebar_state_text.setText("尚未接入摄像头与区域")
                self.sidebar_ai_state.setText("● 待配置")

        def _load_assistant_history(self) -> None:
            messages = self.assistant.history(limit=80)
            if not messages:
                self._add_assistant_welcome()
                return
            for message in messages:
                self._append_chat(
                    str(message.get("content", "")),
                    role=str(message.get("role", "assistant")),
                    provider=str(message.get("provider", "offline")),
                )

        def _add_assistant_welcome(self) -> None:
            card = QFrame()
            card.setObjectName("welcomeCard")
            card.setMaximumWidth(760)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(10)
            title = QLabel("今天想先了解什么？")
            title.setStyleSheet("font-size: 16px; font-weight: 700; color: #172239;")
            hint = QLabel("设备、区域和险情数据均来自本机，回答会明确区分已确认信息和数据不足。")
            hint.setObjectName("cardMuted")
            hint.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(hint)
            row = QHBoxLayout()
            row.setSpacing(10)
            for heading, text in (
                ("查现场", "了解在线设备与区域状态"),
                ("问险情", "查看未关闭和高危记录"),
                ("报险情", "用一句话登记突发情况"),
            ):
                item = QWidget()
                item_layout = QVBoxLayout(item)
                item_layout.setContentsMargins(0, 2, 0, 2)
                item_layout.setSpacing(2)
                item_title = QLabel(heading)
                item_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #0B8F81;")
                item_text = QLabel(text)
                item_text.setObjectName("smallLabel")
                item_text.setWordWrap(True)
                item_layout.addWidget(item_title)
                item_layout.addWidget(item_text)
                row.addWidget(item, 1)
            layout.addLayout(row)
            self.chat_layout.insertWidget(max(0, self.chat_layout.count() - 1), card)

        def _append_chat(self, text: str, *, role: str, provider: str = "offline") -> None:
            bubble = ChatBubble(text, role=role, provider=provider)
            row = QWidget()
            row.setObjectName("pageCanvas")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            if role == "user":
                row_layout.addStretch(1)
                row_layout.addWidget(bubble)
            else:
                row_layout.addWidget(bubble)
                row_layout.addStretch(1)
            self.chat_layout.insertWidget(max(0, self.chat_layout.count() - 1), row)
            QTimer.singleShot(20, self._scroll_chat_to_bottom)

        def _scroll_chat_to_bottom(self) -> None:
            scrollbar = self.chat_scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

        def _quick_ask(self, text: str) -> None:
            self.assistant_input.setPlainText(text)
            if text.endswith("："):
                self.assistant_input.setFocus()
                return
            self._send_assistant_question()

        def _send_assistant_question(self) -> None:
            if self._assistant_busy:
                return
            question = self.assistant_input.toPlainText().strip()
            if not question:
                return
            self.assistant_input.clear()
            self._append_chat(question, role="user", provider="user")
            self._set_assistant_busy(True)
            try:
                prepared = self.assistant.prepare(question)
            except Exception as exc:
                self._append_chat(
                    f"暂时无法读取现场数据：{exc}", role="assistant", provider="offline"
                )
                self._set_assistant_busy(False)
                return
            if prepared.incident is not None:
                self._selected_incident_id = prepared.incident.id
                if prepared.incident_created:
                    self.statusBar().showMessage(f"险情已创建：{prepared.incident.title}", 5000)
                    self._dispatch_incident_notification(prepared.incident)
                else:
                    self.statusBar().showMessage(
                        f"已关联现有险情：{prepared.incident.title}，未重复通知", 5000
                    )
                self._context = build_operational_context(self.store)
                self._refresh_dashboard()
                self._refresh_incidents()
                self._refresh_assistant_context()
            if self._llm_config.available:
                self._start_llm_generation(prepared)
            else:
                QTimer.singleShot(
                    20, lambda: self._finish_assistant_exchange(prepared, prepared.local_reply, "")
                )

        def _start_llm_generation(self, prepared: PreparedQuestion) -> None:
            thread = QThread(self)
            self._assistant_thread = thread
            self._assistant_worker = LLMWorker(config=self._llm_config, prepared=prepared)
            self._assistant_worker.moveToThread(thread)
            thread.started.connect(self._assistant_worker.run)
            self._assistant_worker.completed.connect(
                lambda reply, error: self._finish_assistant_exchange(prepared, reply, error)
            )
            self._assistant_worker.completed.connect(lambda *_args: _quit_thread_soon(thread))
            self._assistant_worker.completed.connect(self._assistant_worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.start()

        def _finish_assistant_exchange(
            self,
            prepared: PreparedQuestion,
            reply: AssistantReply | None,
            error: str,
        ) -> None:
            if reply is None:
                reply = prepared.local_reply
                if error:
                    self.statusBar().showMessage(
                        f"问询服务暂不可用，已切换本地规则：{error[:120]}", 7000
                    )
            self.assistant.save_exchange(prepared, reply)
            self._append_chat(reply.text, role="assistant", provider=reply.provider)
            self._assistant_thread = None
            self._assistant_worker = None
            self._set_assistant_busy(False)
            self._context = build_operational_context(self.store)
            self._refresh_assistant_context()
            self._refresh_dashboard()
            self._refresh_incidents()

        def _set_assistant_busy(self, busy: bool) -> None:
            self._assistant_busy = busy
            self.assistant_send.setText("分析中…" if busy else "发送")
            self._update_assistant_send_state()

        def _update_assistant_send_state(self) -> None:
            if not hasattr(self, "assistant_send"):
                return
            self.assistant_send.setEnabled(
                not self._assistant_busy and bool(self.assistant_input.toPlainText().strip())
            )

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if (
                watched is getattr(self, "assistant_input", None)
                and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() & Qt.ControlModifier
            ):
                self._send_assistant_question()
                return True
            return super().eventFilter(watched, event)

        def _save_llm_settings(self) -> None:
            endpoint = self.llm_endpoint_edit.text().strip()
            model = self.llm_model_edit.text().strip()
            api_key = self.llm_key_edit.text().strip()
            if endpoint and not endpoint.lower().startswith(("https://", "http://")):
                QMessageBox.warning(
                    self, "接口地址无效", "接口地址需要以 https:// 或 http:// 开头。"
                )
                return
            if not api_key:
                api_key = self._llm_config.api_key
            enabled = self.llm_enabled_check.isChecked()
            if enabled and not (endpoint and model and api_key):
                QMessageBox.information(
                    self, "配置不完整", "启用文字问询服务前，请填写接口地址、模型 ID 和 API 密钥。"
                )
                return
            try:
                self._config_store.update_section(
                    "llm",
                    {
                        "provider": str(self.llm_provider_combo.currentData() or ""),
                        "endpoint": endpoint,
                        "model": model,
                        "api_key": api_key,
                        "timeout": self.llm_timeout_spin.value(),
                        "enabled": enabled,
                    },
                )
            except ConfigFileError as exc:
                QMessageBox.warning(self, "配置文件保存失败", str(exc))
                return
            self._llm_config = LLMConfig(
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                timeout=float(self.llm_timeout_spin.value()),
                enabled=enabled,
            )
            self._llm_config_error = ""
            self.llm_key_edit.clear()
            self._refresh_assistant_context()
            self._refresh_dashboard()
            self._refresh_runtime_labels()
            self.statusBar().showMessage("问询服务配置已保存", 4000)

        def _test_llm(self) -> None:
            if self._llm_test_thread is not None:
                self.statusBar().showMessage("文字问询服务正在测试连接，请稍候…", 3000)
                return
            endpoint = self.llm_endpoint_edit.text().strip()
            model = self.llm_model_edit.text().strip()
            api_key = self.llm_key_edit.text().strip() or self._llm_config.api_key
            try:
                config = LLMConfig(
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    timeout=float(self.llm_timeout_spin.value()),
                    enabled=True,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "配置无效", str(exc))
                return
            if not config.available:
                QMessageBox.information(self, "配置不完整", "请填写接口地址、模型 ID 和 API 密钥。")
                return
            self.llm_status_label.setText("正在测试连接…")
            self.llm_status_badge.set_state("测试中", "unknown")
            self.llm_test_button.setEnabled(False)
            self.llm_test_button.setText("测试中…")
            thread = QThread(self)
            self._llm_test_thread = thread
            self._llm_test_worker = LLMTestWorker(config)
            self._llm_test_worker.moveToThread(thread)
            thread.started.connect(self._llm_test_worker.run)
            self._llm_test_worker.completed.connect(self._on_llm_test_finished)
            self._llm_test_worker.completed.connect(lambda *_args: _quit_thread_soon(thread))
            self._llm_test_worker.completed.connect(self._llm_test_worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.start()

        def _on_llm_test_finished(self, ok: bool, detail: str) -> None:
            self._llm_test_thread = None
            self._llm_test_worker = None
            self.llm_test_button.setEnabled(True)
            self.llm_test_button.setText("测试连接")
            self.llm_status_label.setText("● 连接成功" if ok else "连接失败")
            self.llm_status_badge.set_state(
                "连接成功" if ok else "连接失败", "online" if ok else "offline"
            )
            if ok:
                QMessageBox.information(self, "问询服务", f"连接成功。\n{detail}")
            else:
                QMessageBox.warning(self, "连接失败", detail)

        def _save_gb_settings(self) -> None:
            enabled = self.gb_enabled_check.isChecked()
            platform_id = self.gb_platform_edit.text().strip()
            domain = self.gb_domain_edit.text().strip()
            port = self.gb_port_spin.value()
            if enabled and (not platform_id or not domain):
                QMessageBox.warning(self, "配置不完整", "启用国标服务前，请填写平台编号和 SIP 域。")
                return
            current = self._config_store.section("gb28181")
            password = self.gb_password_edit.text().strip()
            if not password:
                password = str(current.get("password", ""))
            try:
                self._config_store.update_section(
                    "gb28181",
                    {
                        "enabled": enabled,
                        "platform_id": platform_id,
                        "domain": domain,
                        "port": port,
                        "password": password,
                    },
                )
            except ConfigFileError as exc:
                QMessageBox.warning(self, "配置文件保存失败", str(exc))
                return
            self._gb_error = ""
            self._stop_gb_service()
            if enabled:
                self._start_gb_service(platform_id=platform_id, domain=domain, port=port)
            self._refresh_runtime_labels()

        def _restore_gb_service_on_start(self) -> None:
            gb = self._config_store.section("gb28181")
            enabled = bool(gb.get("enabled", False))
            platform_id = str(gb.get("platform_id", "")).strip()
            domain = str(gb.get("domain", "")).strip()
            try:
                port = int(gb.get("port", 5060))
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                self._gb_error = "国标配置无效：SIP 端口必须是 1～65535 的整数"
                return
            if enabled and (not platform_id or not domain):
                self._gb_error = "国标配置无效：启用前必须填写平台编号和 SIP 域"
                return
            if enabled:
                self._start_gb_service(
                    platform_id=platform_id,
                    domain=domain,
                    port=port,
                )

        def _start_gb_service(self, *, platform_id: str, domain: str, port: int) -> None:
            if self._device_manager is not None:
                return
            self._gb_error = ""
            gb = self._config_store.section("gb28181")
            password = os.environ.get("FACTORYGUARD_GB_PASSWORD", "") or str(gb.get("password", ""))
            worker_store: SQLiteStore | None = None
            try:
                self._gb_platform = Gb28181Platform(
                    platform_id=platform_id,
                    domain=domain,
                    password=password,
                    port=port,
                    on_native_event=self._handle_gb_native_event,
                )
                worker_store = SQLiteStore(self._db_path)
                self._device_manager = DeviceManager(
                    worker_store,
                    vault=self._vault,
                    gb_platform=self._gb_platform,
                    credential_store=self._config_store,
                )
                self._device_manager.start_gb_service(port=port)
                self._gb_active_port = port
                self.gb_status_label.setText(f"● 已启动 · SIP 端口 {port} · 等待设备注册")
            except Exception as exc:
                if self._device_manager is not None:
                    self._device_manager.close()
                elif worker_store is not None:
                    worker_store.close()
                self._device_manager = None
                self._gb_platform = None
                self._gb_active_port = 0
                self._gb_error = f"国标服务启动失败：{exc}"
                self.gb_status_label.setText(self._gb_error)
                QMessageBox.warning(self, "国标服务启动失败", str(exc))

        def _handle_gb_native_event(self, event: Any) -> None:
            """Receive callbacks from the SIP thread without touching SQLite there."""
            self._native_event_queue.put(event)

        def _drain_native_incidents(self) -> None:
            created = False
            while True:
                try:
                    event = self._native_event_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if self._process_native_event(event):
                        created = True
                except (OSError, RuntimeError, ValueError, TypeError) as exc:
                    self.statusBar().showMessage(
                        f"设备告警处理失败，已跳过该条告警：{str(exc)[:160]}", 8000
                    )
            if created:
                self._context = build_operational_context(self.store)
                self._refresh_dashboard()
                self._refresh_incidents()
                self._refresh_assistant_context()

        def _process_native_event(self, event: Any) -> bool:
            """Convert one device event into an incident, returning True when created."""
            device_source_id = str(getattr(event, "device_id", ""))
            channel_id = str(getattr(event, "channel_id", ""))
            source_event_id = str(getattr(event, "source_event_id", "") or "") or (
                f"{getattr(event, 'event_code', '')}:{getattr(event, 'occurred_at', '')}"
            )
            dedupe_key = f"{device_source_id}:{channel_id}:{source_event_id}"
            if not self._remember_native_event(dedupe_key):
                return False
            device = next(
                (
                    item
                    for item in self._context.topology.devices
                    if item.device_id == device_source_id or item.id == device_source_id
                ),
                None,
            )
            device_cameras = (
                tuple(
                    item
                    for item in self._context.topology.cameras
                    if device is not None and item.device_id == device.id
                )
                if device is not None
                else ()
            )
            normalized_channel = _normalize_channel_id(channel_id)
            camera = next(
                (
                    item
                    for item in device_cameras
                    if _normalize_channel_id(item.channel_id) == normalized_channel
                ),
                device_cameras[0] if len(device_cameras) == 1 else None,
            )
            if camera is not None and (
                not camera.enabled or (device is not None and not device.enabled)
            ):
                return False
            area_id = camera.area_id if camera is not None else getattr(event, "area_id", None)
            site_id = getattr(event, "site_id", None)
            if site_id is None and area_id:
                try:
                    site_id = self._context.topology.get_area(str(area_id)).site_id
                except KeyError:
                    site_id = None
            event_code = str(getattr(event, "event_code", "")).strip().lower()
            title = str(
                getattr(event, "title", "") or NATIVE_EVENT_TITLES.get(event_code, "设备上报险情")
            )
            raw_level = str(getattr(event, "level", "high")).strip().lower()
            event_source = getattr(event, "source", "")
            if event_source == EventSource.ONVIF and raw_level in {"info", "debug"}:
                return False
            severity = (
                IncidentSeverity.CRITICAL
                if raw_level in {"critical", "emergency"}
                else IncidentSeverity.HIGH
                if raw_level == "high"
                else IncidentSeverity.LOW
                if raw_level == "low"
                else IncidentSeverity.MEDIUM
            )
            payload = dict(getattr(event, "payload", {}) or {})
            source_label = (
                "ONVIF"
                if event_source == EventSource.ONVIF
                else "GB/T 28181"
                if event_source == EventSource.GB28181
                else "设备原生告警"
            )
            reporter_identity = (
                device.device_id
                if device is not None and device.device_id
                else camera.name
                if camera is not None
                else device.name
                if device is not None
                else device_source_id
            )
            incident = Incident.create(
                title=title,
                description=f"{source_label}设备上报“{title}”，请结合现场视频核实。",
                severity=severity,
                site_id=str(site_id) if site_id else None,
                area_id=str(area_id) if area_id else None,
                camera_id=camera.id if camera is not None else getattr(event, "camera_id", None),
                site_name=(
                    _context_site_name(self._context, site_id)
                    or str(getattr(event, "site_name", "") or "")
                ),
                area_name=(
                    _context_area_name(self._context, area_id)
                    if camera is not None
                    else str(getattr(event, "area_name", "") or "")
                ),
                camera_name=(
                    camera.name
                    if camera is not None
                    else str(getattr(event, "camera_name", "") or "")
                    or (
                        channel_id
                        if channel_id and channel_id != device_source_id
                        else device_source_id
                    )
                ),
                source="device",
                reporter=f"{source_label} · {reporter_identity}",
                metadata={
                    "native_event": payload,
                    "source_event_id": source_event_id,
                    "event_code": event_code,
                },
                occurred_at=_coerce_event_timestamp(getattr(event, "occurred_at", None)),
            )
            if not self.store.create_incident(incident, actor="native-event-service"):
                return False
            self._notify_new_incident(incident)
            self._dispatch_incident_notification(incident)
            return True

        def _remember_native_event(self, source_event_id: str) -> bool:
            if source_event_id in self._seen_native_event_keys:
                return False
            self._seen_native_event_keys.add(source_event_id)
            self._native_event_key_order.append(source_event_id)
            while len(self._native_event_key_order) > self.NATIVE_EVENT_ID_CACHE_LIMIT:
                expired = self._native_event_key_order.popleft()
                self._seen_native_event_keys.discard(expired)
            return True

        def _notify_new_incident(self, incident: Incident) -> None:
            severity = SEVERITY_LABELS.get(incident.severity, str(incident.severity))
            location = " / ".join(
                value
                for value in (
                    self._context.area_name(incident.area_id, snapshot=incident.area_name_snapshot),
                    self._context.camera_name(
                        incident.camera_id, snapshot=incident.camera_name_snapshot
                    ),
                )
                if value and value not in {"未指定摄像头", "未指定区域"}
            )
            message = f"{severity} · {incident.title}"
            if location:
                message += f"（{location}）"
            self.statusBar().showMessage(f"收到新险情：{message}", 8000)
            if self._tray is not None:
                self._tray.notify("FactoryGuard 新险情", message)
            QApplication.alert(self, 4000)

        def _sync_native_subscriptions(self) -> None:
            if self._db_path == ":memory:":
                return
            devices = tuple(
                device
                for device in self._context.topology.devices
                if device.enabled and device.protocol is DeviceProtocol.ONVIF
            )
            if not devices:
                self._stop_native_subscriptions()
                return
            if self._native_subscription_manager is None:
                self._native_subscription_manager = DeviceManager(
                    SQLiteStore(self._db_path),
                    vault=self._vault,
                    credential_store=self._config_store,
                    on_native_event=self._handle_gb_native_event,
                )
            manager = self._native_subscription_manager
            current_ids = {device.id for device in devices}
            for device_id in manager.active_native_subscription_ids():
                if device_id not in current_ids:
                    manager.stop_native_subscription(device_id)
            rules = self.store.load_rules(enabled_only=True, published_only=True)
            for device in devices:
                manager.start_native_subscription(device, rules)
            self._report_subscription_failure(manager)

        def _report_subscription_failure(self, manager: DeviceManager) -> None:
            """Surface a dead alarm subscription instead of losing it silently."""
            error = str(getattr(manager, "last_subscription_error", "") or "").strip()
            if not error or error == self._last_reported_subscription_error:
                return
            self._last_reported_subscription_error = error
            self.statusBar().showMessage(
                f"设备告警订阅异常，已停止接收该设备告警：{error[:180]}",
                10000,
            )

        def _stop_native_subscriptions(self) -> None:
            if self._native_subscription_manager is not None:
                self._native_subscription_manager.close()
                self._native_subscription_manager = None

        def _stop_gb_service(self) -> None:
            if self._device_manager is not None:
                self._device_manager.close()
                self._device_manager = None
            self._gb_platform = None
            self._gb_active_port = 0
            if hasattr(self, "gb_status_label"):
                self.gb_status_label.setText("未启用")

        def _refresh_runtime_labels(self) -> None:
            ffmpeg_text = "未找到 FFmpeg，请设置 FACTORYGUARD_FFMPEG"
            try:
                from factoryguard.media.ffmpeg import find_ffmpeg

                ffmpeg = find_ffmpeg()
                if ffmpeg is not None:
                    ffmpeg_text = f"● FFmpeg 已就绪 · {ffmpeg.executable}"
            except Exception as exc:
                ffmpeg_text = f"FFmpeg 检查失败：{exc}"
            self.runtime_status_label.setText(
                f"{ffmpeg_text}\n"
                f"● 软件配置：{self._config_store.path.resolve()}\n"
                f"● 业务数据库：{Path(self._db_path).resolve() if self._db_path != ':memory:' else '内存数据库'}\n"
                "● 视频接入：FFmpeg 解码；设备原生告警直接进入平台；"
                "启动 60 秒后首次检测，之后每 10 分钟自动检测"
            )
            if getattr(self, "_llm_config_error", ""):
                self.llm_status_label.setText(self._llm_config_error)
                self.llm_status_badge.set_state("配置错误", "offline")
            elif not self._llm_config.enabled:
                if not any(
                    (self._llm_config.endpoint, self._llm_config.model, self._llm_config.api_key)
                ):
                    self.llm_status_label.setText("○ 使用本地规则问答")
                    self.llm_status_badge.set_state("本地规则", "unknown")
                else:
                    self.llm_status_label.setText("○ 文字问询服务已关闭 · 当前使用本地规则问答")
                    self.llm_status_badge.set_state("已关闭", "unknown")
            elif self._llm_config.available:
                self.llm_status_label.setText(f"● 已配置 · {self._llm_config.display_name}")
                self.llm_status_badge.set_state("已启用", "online")
            else:
                self.llm_status_label.setText("○ 文字问询配置未完成 · 当前使用本地规则问答")
                self.llm_status_badge.set_state("未完成", "unknown")
            if self._gb_error:
                self.gb_status_label.setText(self._gb_error)
                self.gb_import_button.setEnabled(False)
                self.gb_status_badge.set_state(
                    "配置错误" if self._gb_error.startswith("国标配置无效") else "启动失败",
                    "offline",
                )
            elif self._device_manager is None:
                self.gb_status_label.setText("未启用")
                self.gb_import_button.setEnabled(False)
                self.gb_status_badge.set_state("未启用", "unknown")
            elif not self._device_manager.gb_service_running:
                self.gb_status_label.setText("○ 服务已停止，请检查端口占用或重新保存配置")
                self.gb_import_button.setEnabled(False)
                self.gb_status_badge.set_state("已停止", "offline")
            else:
                port = self._gb_active_port
                registrations = self._device_manager.gb_registered_devices()
                channel_count = sum(len(item.get("channels", ())) for item in registrations)
                imported_keys = _gb_imported_channel_keys(self._context.topology)
                imported_channels = 0
                for registration in registrations:
                    source_id = str(registration.get("device_id", "")).strip()
                    for channel in registration.get("channels", ()):
                        channel_id = str(channel.get("id", "")).strip()
                        if not channel_id:
                            continue
                        key = (source_id, _normalize_channel_id(channel_id))
                        if key in imported_keys:
                            imported_channels += 1
                pending_channels = max(channel_count - imported_channels, 0)
                self.gb_import_button.setEnabled(channel_count > 0)
                if not registrations:
                    self.gb_status_label.setText(
                        "○ 服务已启动，等待设备注册\n"
                        f"  SIP 端口 {port} · 请在摄像头或 NVR 中填入平台编号与 SIP 域后发起注册"
                    )
                    self.gb_status_badge.set_state("等待注册", "unknown")
                elif not channel_count:
                    self.gb_status_label.setText(
                        f"● 已注册 {len(registrations)} 台设备，等待设备上报通道目录\n"
                        "  请确认设备已开启目录查询，或稍后刷新"
                    )
                    self.gb_status_badge.set_state("等待目录", "unknown")
                elif pending_channels:
                    self.gb_status_label.setText(
                        f"● 已注册 {len(registrations)} 台设备 · {channel_count} 个通道"
                        f"（已导入 {imported_channels} 个）\n"
                        "  点击“导入已注册设备”即可加入平台"
                    )
                    self.gb_status_badge.set_state("待导入", "unknown")
                else:
                    self.gb_status_label.setText(
                        f"● 已注册 {len(registrations)} 台设备 · {channel_count} 个通道已全部导入"
                    )
                    self.gb_status_badge.set_state("已接入", "online")

        def _import_gb_registered_devices(self) -> None:
            if self._device_manager is None:
                QMessageBox.information(self, "国标服务", "请先启用 GB/T 28181 国标服务。")
                return
            registrations = self._device_manager.gb_registered_devices()
            if not registrations:
                QMessageBox.information(self, "国标设备", "当前没有已注册设备。")
                return
            if not self._context.topology.areas:
                self._add_area()
                if not self._context.topology.areas:
                    return
            choices = _gb_area_choices(self._context.topology)
            labels = [label for label, _area in choices]
            selected_label, accepted = QInputDialog.getItem(
                self, "导入国标设备", "选择设备所属区域", labels, 0, False
            )
            if not accepted:
                return
            selected_area = next((area for label, area in choices if label == selected_label), None)
            if selected_area is None:
                QMessageBox.warning(self, "导入失败", "未找到所选区域，请刷新后重试。")
                return
            area = selected_area
            existing_by_source: dict[str, list[VideoDevice]] = {}
            for device in self._context.topology.devices:
                if device.device_id and str(device.protocol) == "gb28181":
                    existing_by_source.setdefault(device.device_id, []).append(device)
            existing_camera_keys = {
                (camera.device_id, _normalize_channel_id(camera.channel_id))
                for camera in self._context.topology.cameras
            }
            planned_camera_keys: set[tuple[str, str]] = set()
            devices_to_save: list[VideoDevice] = []
            cameras_to_save: list[CameraDevice] = []
            try:
                for registration in registrations:
                    source_id = str(registration.get("device_id", "")).strip()
                    channels = tuple(registration.get("channels", ()))
                    if not source_id or not channels:
                        continue
                    matches = existing_by_source.get(source_id, [])
                    if len(matches) > 1:
                        raise ValueError(
                            f"国标设备编号 {source_id} 已对应多个本地设备，请先合并或删除重复设备"
                        )
                    if matches:
                        device = matches[0]
                        if device.site_id != area.site_id or device.area_id != area.id:
                            raise ValueError(
                                f"国标设备 {source_id} 已属于其他场所或区域，请在原区域导入其通道"
                            )
                    else:
                        first = channels[0]
                        device = VideoDevice(
                            id=f"device-gb-{uuid4().hex[:12]}",
                            site_id=area.site_id,
                            area_id=area.id,
                            name=str(first.get("name") or source_id),
                            protocol="gb28181",
                            endpoint=f"gb28181://{source_id}",
                            vendor=str(first.get("manufacturer", "GB28181")),
                            model=str(first.get("model", "")),
                            device_id=source_id,
                            capabilities=DeviceCapabilities(live_video=True, native_events=True),
                            analysis_policy="native_only",
                        )
                        devices_to_save.append(device)
                        existing_by_source[source_id] = [device]
                    address = registration.get("address") or ""
                    if isinstance(address, (tuple, list)):
                        address = ":".join(str(item) for item in address[:2])
                    for channel in channels:
                        channel_id = str(channel.get("id", "")).strip()
                        key = (device.id, _normalize_channel_id(channel_id))
                        if (
                            not channel_id
                            or key in existing_camera_keys
                            or key in planned_camera_keys
                        ):
                            continue
                        camera = CameraDevice(
                            id=f"camera-gb-{uuid4().hex[:12]}",
                            area_id=area.id,
                            name=str(channel.get("name") or channel_id),
                            vendor=str(channel.get("manufacturer", "")),
                            address=str(address),
                            device_id=device.id,
                            channel_id=channel_id,
                        )
                        cameras_to_save.append(camera)
                        planned_camera_keys.add(key)
                if devices_to_save or cameras_to_save:
                    self.store.save_device_camera_batch(
                        devices_to_save, cameras_to_save, actor="merchant-console"
                    )
            except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "导入失败",
                    f"未导入任何通道。\n\n{exc}",
                )
                return
            imported = len(cameras_to_save)
            self._refresh_all()
            if imported:
                self.statusBar().showMessage(f"已从国标服务导入 {imported} 个摄像头通道", 5000)
            else:
                QMessageBox.information(self, "国标设备", "没有新的通道需要导入。")

        def _open_data_folder(self) -> None:
            path = (
                Path(".").resolve()
                if self._db_path == ":memory:"
                else Path(self._db_path).resolve().parent
            )
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def _backup_folder(self) -> Path:
            base = (
                Path(".").resolve()
                if self._db_path == ":memory:"
                else Path(self._db_path).resolve().parent
            )
            return base / "backups"

        def _refresh_backup_status(self) -> None:
            if not hasattr(self, "backup_status_label"):
                return
            folder = self._backup_folder()
            try:
                files = [path for path in folder.glob("*.zip") if path.is_file()]
            except OSError as exc:
                self.backup_status_label.setText(f"本地备份：无法读取（{exc}）")
                return
            if not files:
                self.backup_status_label.setText("本地备份：尚未创建")
                return
            total_bytes = 0
            latest_mtime = 0.0
            for path in files:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total_bytes += stat.st_size
                latest_mtime = max(latest_mtime, stat.st_mtime)
            latest = datetime.fromtimestamp(latest_mtime).astimezone().strftime("%Y-%m-%d %H:%M")
            size_text = (
                f"{total_bytes / (1024 * 1024):.1f} MB"
                if total_bytes >= 1024 * 1024
                else f"{total_bytes / 1024:.1f} KB"
            )
            self.backup_status_label.setText(
                f"本地备份：{len(files)} 个，共 {size_text}，最近 {latest}"
            )

        def _refresh_autostart_state(self) -> None:
            """Show whether the console currently starts at user logon."""
            if not hasattr(self, "autostart_check"):
                return
            status = autostart.describe()
            self.autostart_check.setChecked(status.enabled)
            self.autostart_check.setEnabled(status.supported)
            self.autostart_status_label.setText(status.detail)

        def _toggle_autostart(self, enabled: bool) -> None:
            try:
                status = autostart.enable() if enabled else autostart.disable()
            except (OSError, RuntimeError, ValueError) as exc:
                self.autostart_check.setChecked(not enabled)
                QMessageBox.warning(self, "开机自启动设置失败", str(exc))
                self._refresh_autostart_state()
                return
            self.autostart_check.setChecked(status.enabled)
            self.autostart_status_label.setText(status.detail)
            self.statusBar().showMessage(
                "已设置开机自动值守" if status.enabled else "已取消开机自动值守", 5000
            )

        def _open_backup_folder(self) -> None:
            path = self._backup_folder()
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def _create_backup(self) -> None:
            if self._db_path == ":memory:":
                QMessageBox.information(
                    self, "无法备份", "当前使用内存数据库，请先使用持久化数据库启动应用。"
                )
                return
            now = datetime.now().astimezone()
            destination = self._backup_folder() / (
                f"factoryguard-{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond // 1000:03d}.zip"
            )
            try:
                self._config_store.ensure_file()
                create_backup(self._db_path, self._config_store.path, destination)
            except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
                QMessageBox.warning(self, "备份失败", str(exc))
                return
            self._refresh_backup_status()
            branding = self._config_store.path.parent / "branding" / "banner.svg"
            branding_note = "、客户横幅" if branding.is_file() else ""
            QMessageBox.information(
                self,
                "备份完成",
                f"已备份配置{branding_note}和业务数据库。\n\n"
                f"{destination}\n\n"
                "备份中包含明文配置凭据，请妥善保管。",
            )
            self.statusBar().showMessage(f"数据备份已创建：{destination}", 6000)

        def _choose_backup_to_restore(self) -> None:
            if self._db_path == ":memory:":
                QMessageBox.information(
                    self, "无法恢复", "当前使用内存数据库，请先使用持久化数据库启动应用。"
                )
                return
            source, _filter = QFileDialog.getOpenFileName(
                self,
                "选择 FactoryGuard 备份包",
                str(self._backup_folder()),
                "FactoryGuard 备份 (*.zip)",
            )
            if not source:
                return
            try:
                info = inspect_backup(source)
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "备份校验失败", str(exc))
                return
            config_note = "包含配置文件" if info.config_included else "仅恢复业务数据库"
            if getattr(info, "branding_included", False):
                config_note += "、客户横幅"
            answer = QMessageBox.question(
                self,
                "确认恢复备份",
                f"备份版本：{info.version or '未知'}\n"
                f"创建时间：{_format_local_timestamp(info.created_at)}\n"
                f"{config_note}\n\n"
                "恢复将在应用退出后的下一次启动执行。\n"
                "当前数据会先自动保存为恢复前备份。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                stage_restore(source, self._db_path)
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(self, "无法安排恢复", str(exc))
                return
            QMessageBox.information(
                self,
                "恢复请求已保存",
                "应用将安全退出。请重新启动 FactoryGuard，届时会自动恢复备份。",
            )
            self.quit_application()

        def _open_config_file(self) -> None:
            try:
                self._config_store.ensure_file()
            except ConfigFileError as exc:
                QMessageBox.warning(self, "无法创建配置文件", str(exc))
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_store.path.resolve())))

        def _preview_cameras(
            self, cameras: tuple[CameraView, ...], *, limit: int
        ) -> tuple[CameraView, ...]:
            priority = sorted(
                cameras,
                key=lambda item: (
                    0
                    if item.is_online
                    else 1
                    if not item.is_disabled and not item.is_offline
                    else 2,
                    item.area.name,
                    item.camera.name,
                ),
            )
            return tuple(priority[:limit])

        def __del__(self) -> None:
            """Release the database handle if the window is discarded."""
            store = getattr(self, "store", None)
            if store is None:
                return
            try:
                if store._connection is not None:
                    store.close()
            except Exception:
                pass

        def closeEvent(self, event: QCloseEvent) -> None:
            if not self._allow_quit and self._tray is not None:
                event.ignore()
                self.hide()
                if not self._tray_hint_shown:
                    self._tray.notify(
                        "FactoryGuard 仍在值守", "控制台已隐藏到系统托盘，双击托盘图标可重新打开。"
                    )
                    self._tray_hint_shown = True
                return
            if self._quit_pending:
                event.ignore()
                return
            self._shutdown_for_exit()
            if self._quit_pending:
                event.ignore()
                return
            event.accept()


else:

    class MerchantMainWindow:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()


def launch(
    *,
    config: MerchantConfig | None = None,
    start_hidden: bool = False,
    argv: list[str] | None = None,
) -> int:
    """Start one merchant console instance and return the Qt exit code."""
    require_qt()
    instance_lock = _acquire_single_instance_lock()
    if instance_lock is None:
        return 0
    try:
        return _launch_unlocked(config=config, start_hidden=start_hidden, argv=argv)
    finally:
        instance_lock.unlock()


def _launch_unlocked(
    *,
    config: MerchantConfig | None = None,
    start_hidden: bool = False,
    argv: list[str] | None = None,
) -> int:
    """Build and run the Qt application while the instance lock is held."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "FactoryGuard.MerchantConsole"
            )
        except (AttributeError, OSError):
            pass
    application = QApplication.instance() or QApplication(argv or [])
    application.setApplicationName("FactoryGuard")
    application.setApplicationDisplayName("FactoryGuard 智守")
    application.setStyle("Fusion")
    application.setWindowIcon(application_icon("深色"))
    apply_merchant_theme(application)
    active_config = config or MerchantConfig()
    try:
        restored = apply_pending_restore(active_config.db, active_config.config_path)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        QMessageBox.critical(
            None,
            "恢复备份失败",
            f"{exc}\n\n恢复请求已取消，原数据保持不变。请检查备份包和数据目录权限。",
        )
        return 2
    if restored is not None:
        safety = str(restored.safety_backup) if restored.safety_backup else "未创建"
        QMessageBox.information(
            None,
            "备份恢复完成",
            f"FactoryGuard 已恢复备份数据。\n\n恢复前安全备份：{safety}",
        )
    splash = StartupSplash()
    screen = application.primaryScreen()
    if screen is not None:
        geometry = splash.frameGeometry()
        geometry.moveCenter(screen.availableGeometry().center())
        splash.move(geometry.topLeft())
    splash.show()
    splash.set_progress("正在启动…", 2)
    started = time.monotonic()

    def update_progress(message: str, value: int) -> None:
        splash.set_progress(message, value)

    try:
        window = MerchantMainWindow(
            config=active_config,
            start_hidden=start_hidden,
            progress_callback=update_progress,
        )
    except ConfigFileError as exc:
        splash.close()
        QMessageBox.critical(None, "配置文件错误", _config_recovery_hint(active_config, exc))
        return 2
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        splash.close()
        detail = " ".join(str(exc).split())[:600]
        QMessageBox.critical(
            None,
            "FactoryGuard 启动失败",
            _startup_failure_hint(active_config, exc, detail),
        )
        return 2
    while time.monotonic() - started < 0.75:
        application.processEvents()
        time.sleep(0.025)
    splash.close()
    if start_hidden and window.can_background():
        window.hide()
        window.notify_background_start()
    elif start_hidden:
        window.showMinimized()
    else:
        window.show()
    return application.exec()


__all__ = ["MerchantMainWindow", "launch"]
