from __future__ import annotations

"""Qt control panel for live monitoring and local event review."""

import csv
import json
import platform
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from factoryguard.domain.models import Area, CameraDevice, EventStatus, Rule, RuleType, Site, StreamRole, VideoStream
from factoryguard.domain.permissions import Permission, ROLE_LABELS, UserRole, can, role_from_value
from factoryguard.domain.schedules import ArmingSchedule
from factoryguard.domain.topology import EntityRegistry
from factoryguard.topology_io import topology_from_csv, topology_to_csv
from .preview import PreviewWidget
from .probe_worker import StreamProbeWorker
from .qt import HAS_QT, require_qt
from .rule_dialog import RuleEditorDialog
from .schedule_dialog import ScheduleEditorDialog
from .notification_worker import NotificationDispatchOnceWorker, NotificationWorker
from .settings_pages import SettingsPagesMixin
from .worker import MonitorConfig, MonitorWorker, StreamRuntimeConfig
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.storage.retention import MediaRetentionManager
from factoryguard.media.ffmpeg import find_ffmpeg
from factoryguard.notifications.adapters import (
    EmailAdapter,
    SmsHttpAdapter,
    parse_phone_recipients,
    parse_recipients,
)
from factoryguard.notifications.destinations import (
    NotificationDestination,
    default_destination,
    new_destination_id,
)

if HAS_QT:
    from .qt import (
        QApplication,
        QAbstractItemView,
        QCloseEvent,
        QComboBox,
        QDesktopServices,
        QDoubleSpinBox,
        QFileDialog,
        QFrame,
        QFormLayout,
        QGroupBox,
        QGridLayout,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QScrollArea,
        QSizePolicy,
        QStatusBar,
        QSettings,
        QTableWidget,
        QTableWidgetItem,
        QTreeWidget,
        QTreeWidgetItem,
        QThread,
        QTimer,
        QUrl,
        QVBoxLayout,
        QWidget,
        Qt,
    )
    from .tray import TrayController


    class ResponsiveStackedWidget(QStackedWidget):
        """Use the current page's size hint so the outer scroll area can adapt."""

        def sizeHint(self):  # type: ignore[override]
            current = self.currentWidget()
            return current.sizeHint() if current is not None else super().sizeHint()

        def minimumSizeHint(self):  # type: ignore[override]
            current = self.currentWidget()
            return current.minimumSizeHint() if current is not None else super().minimumSizeHint()


_THEMES = {
    "浅色": """
        QMainWindow, QWidget { background: #f4f7fb; color: #172033; }
        QGroupBox { border: 1px solid #d7e0ec; border-radius: 10px; margin-top: 10px; padding: 12px; background: #ffffff; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #52627a; }
        QLabel#title { font-size: 24px; font-weight: 700; color: #132238; }
        QLabel#subtitle, QLabel#muted { color: #687991; }
        QLabel#status { padding: 6px 12px; border-radius: 14px; background: #e6edf6; color: #536176; }
        QLabel#channelStatus { color: #536176; padding: 5px 8px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { padding: 6px 8px; border: 1px solid #cbd7e6; border-radius: 6px; background: #ffffff; }
        QPushButton { padding: 7px 14px; border: 1px solid #cbd7e6; border-radius: 6px; background: #ffffff; }
        QPushButton:hover { border-color: #3984e8; }
        QPushButton#primary { background: #1f6feb; color: #ffffff; border-color: #1f6feb; }
        QPushButton#secondary { color: #245fa8; }
        QPushButton:disabled { color: #9aa6b8; }
        QTableWidget { border: 1px solid #d7e0ec; gridline-color: #edf1f6; background: #ffffff; alternate-background-color: #f8fafd; }
        QHeaderView::section { padding: 7px; background: #edf3fa; color: #52627a; border: 0; }
        QSplitter::handle { background: #dbe4ef; }
        QWidget#sidebar { background: #ffffff; border-right: 1px solid #d7e0ec; }
        QLabel#brand { font-size: 18px; font-weight: 700; color: #132238; }
        QLabel#navSection { color: #8795aa; font-size: 11px; font-weight: 700; padding: 12px 10px 4px; }
        QPushButton#navButton { text-align: left; padding: 9px 12px; border: 0; border-radius: 6px; color: #52627a; background: transparent; }
        QPushButton#navButton:hover { background: #f0f4f9; }
        QPushButton#navButton:checked { color: #1f6feb; background: #e8f0ff; font-weight: 600; }
    """,
    "深色": """
        QMainWindow, QWidget { background: #141a24; color: #e8eef7; }
        QGroupBox { border: 1px solid #303c50; border-radius: 10px; margin-top: 10px; padding: 12px; background: #1b2432; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #aebbd0; }
        QLabel#title { font-size: 24px; font-weight: 700; color: #f3f7fc; }
        QLabel#subtitle, QLabel#muted { color: #95a5bc; }
        QLabel#status { padding: 6px 12px; border-radius: 14px; background: #293549; color: #c6d3e6; }
        QLabel#channelStatus { color: #aebbd0; padding: 5px 8px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { padding: 6px 8px; border: 1px solid #3b4a61; border-radius: 6px; background: #202b3c; color: #e8eef7; }
        QPushButton { padding: 7px 14px; border: 1px solid #3b4a61; border-radius: 6px; background: #202b3c; color: #e8eef7; }
        QPushButton:hover { border-color: #65a3f5; }
        QPushButton#primary { background: #287be8; color: #ffffff; border-color: #287be8; }
        QPushButton#secondary { color: #9bc4fb; }
        QPushButton:disabled { color: #687890; }
        QTableWidget { border: 1px solid #303c50; gridline-color: #2a3547; background: #1b2432; alternate-background-color: #202b3c; }
        QHeaderView::section { padding: 7px; background: #27354a; color: #c6d3e6; border: 0; }
        QSplitter::handle { background: #303c50; }
        QWidget#sidebar { background: #1b2432; border-right: 1px solid #303c50; }
        QLabel#brand { font-size: 18px; font-weight: 700; color: #f3f7fc; }
        QLabel#navSection { color: #8190a8; font-size: 11px; font-weight: 700; padding: 12px 10px 4px; }
        QPushButton#navButton { text-align: left; padding: 9px 12px; border: 0; border-radius: 6px; color: #aebbd0; background: transparent; }
        QPushButton#navButton:hover { background: #27354a; }
        QPushButton#navButton:checked { color: #9bc4fb; background: #2a4264; font-weight: 600; }
    """,
    "海蓝": """
        QMainWindow, QWidget { background: #eef8fa; color: #12313b; }
        QGroupBox { border: 1px solid #b9dce2; border-radius: 10px; margin-top: 10px; padding: 12px; background: #ffffff; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #3c707c; }
        QLabel#title { font-size: 24px; font-weight: 700; color: #0b3e4d; }
        QLabel#subtitle, QLabel#muted { color: #5d8992; }
        QLabel#status { padding: 6px 12px; border-radius: 14px; background: #d9f1f3; color: #2f707a; }
        QLabel#channelStatus { color: #3c707c; padding: 5px 8px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { padding: 6px 8px; border: 1px solid #afd1d8; border-radius: 6px; background: #ffffff; }
        QPushButton { padding: 7px 14px; border: 1px solid #afd1d8; border-radius: 6px; background: #ffffff; color: #195563; }
        QPushButton:hover { border-color: #168fa0; }
        QPushButton#primary { background: #078c9c; color: #ffffff; border-color: #078c9c; }
        QPushButton#secondary { color: #14798b; }
        QPushButton:disabled { color: #91abb1; }
        QTableWidget { border: 1px solid #b9dce2; gridline-color: #e0f0f2; background: #ffffff; alternate-background-color: #f3fbfc; }
        QHeaderView::section { padding: 7px; background: #d9f1f3; color: #3c707c; border: 0; }
        QSplitter::handle { background: #b9dce2; }
        QWidget#sidebar { background: #ffffff; border-right: 1px solid #b9dce2; }
        QLabel#brand { font-size: 18px; font-weight: 700; color: #0b3e4d; }
        QLabel#navSection { color: #6e9ca5; font-size: 11px; font-weight: 700; padding: 12px 10px 4px; }
        QPushButton#navButton { text-align: left; padding: 9px 12px; border: 0; border-radius: 6px; color: #3c707c; background: transparent; }
        QPushButton#navButton:hover { background: #e8f5f7; }
        QPushButton#navButton:checked { color: #078c9c; background: #d9f1f3; font-weight: 600; }
    """,
}

_EVENT_STATUS_LABELS = {
    "open": "待处理",
    "confirmed": "已确认",
    "false_positive": "误报",
    "closed": "已关闭",
}

_STREAM_STATUS_LABELS = {
    "starting": "启动中",
    "ready": "已就绪",
    "running": "运行中",
    "degraded": "降级",
    "offline": "离线",
    "stopped": "已停止",
}


if HAS_QT:

    class MainWindow(SettingsPagesMixin, QMainWindow):
        """Desktop monitor with live overlays and event review tools."""

        def __init__(self, *, config: MonitorConfig | None = None, start_hidden: bool = False) -> None:
            super().__init__()
            self._base_config = config or MonitorConfig()
            self._thread: QThread | None = None
            self._worker: MonitorWorker | None = None
            self._notification_thread: QThread | None = None
            self._notification_worker: NotificationWorker | None = None
            self._notification_once_thread: QThread | None = None
            self._notification_once_worker: NotificationDispatchOnceWorker | None = None
            self._notification_reports = 0
            self._notification_last_detail = "未启动"
            self._notification_state = "disabled"
            self._notification_detail = "未配置"
            self._probe_thread: QThread | None = None
            self._probe_worker: StreamProbeWorker | None = None
            self._probe_stream_id: str | None = None
            self._allow_quit = False
            self._quit_after_stop = False
            self._frames = 0
            self._events = 0
            self._event_records: list[dict[str, Any]] = []
            self._visible_records: list[dict[str, Any]] = []
            self._audit_records: list[dict[str, Any]] = []
            self._visible_audit_records: list[dict[str, Any]] = []
            self._audit_loaded = False
            self._stream_states: dict[str, tuple[str, str]] = {}
            self._stream_health_history: dict[str, dict[str, Any]] = {}
            self._stream_metrics: dict[str, dict[str, int]] = {}
            self._arming_states: dict[str, bool] = {}
            self._preview_grid_widgets: dict[str, PreviewWidget] = {}
            self._preview_grid_cells: dict[str, QWidget] = {}
            self._preview_grid_layout: QGridLayout | None = None
            self._selected_event: dict[str, Any] | None = None
            self._selected_stream_id: str | None = None
            self._topology = self._base_config.topology
            self._loaded_db_path: str | None = None
            self._replay_locked = False
            self._settings = QSettings("FactoryGuard", "FactoryGuard")
            self._local_settings_path = self._resolve_local_settings_path()
            self._local_settings = self._read_local_settings()
            self._initialize_notification_destinations()
            saved_theme = str(self._settings.value("theme", "浅色"))
            self._saved_theme = saved_theme if saved_theme in _THEMES else "浅色"
            self.setWindowTitle("FactoryGuard · 本地视频安全监控")
            screen = QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self.resize(
                    min(1440, max(720, int(available.width() * 0.86))),
                    min(960, max(480, int(available.height() * 0.84))),
                )
            else:
                self.resize(1180, 820)
            self.setMinimumSize(720, 480)
            self._build_ui()
            self.theme_combo.setCurrentText(self._saved_theme)
            self._tray: TrayController | None = None
            if TrayController is not None and QSystemTrayIcon_available():
                self._tray = TrayController(
                    on_show=self._show_from_tray,
                    on_start=self.start_monitor,
                    on_stop=self.stop_monitor,
                    on_quit=self.quit_application,
                )
                self._tray.show()
                self._tray.set_running(False)
            self._apply_theme(self._saved_theme)
            self._set_status("ready", "就绪 · 等待启动监控")
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setInterval(5000)
            self._refresh_timer.timeout.connect(self._refresh_runtime_surfaces)
            self._refresh_timer.start()
            self._refresh_events()
            self._refresh_audit()
            self._refresh_notification_queue()
            self._refresh_entity_tree()
            if start_hidden and self._tray is not None:
                self.hide()

        def _build_sidebar(self) -> QWidget:
            sidebar = QWidget()
            sidebar.setObjectName("sidebar")
            sidebar.setMinimumWidth(176)
            sidebar.setMaximumWidth(220)
            sidebar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            layout = QVBoxLayout(sidebar)
            layout.setContentsMargins(14, 16, 14, 14)
            layout.setSpacing(4)

            brand = QLabel("FactoryGuard")
            brand.setObjectName("brand")
            layout.addWidget(brand)
            edition = QLabel("厂区视频安全平台")
            edition.setObjectName("muted")
            layout.addWidget(edition)
            layout.addSpacing(18)

            self._nav_buttons: dict[str, QPushButton] = {}

            def section(text: str) -> None:
                label = QLabel(text.upper())
                label.setObjectName("navSection")
                layout.addWidget(label)

            def nav(text: str, page: str) -> None:
                button = QPushButton(text)
                button.setObjectName("navButton")
                button.setCheckable(True)
                button.clicked.connect(lambda _checked=False, index=page: self._switch_page(index))
                self._nav_buttons[page] = button
                layout.addWidget(button)

            section("工作台")
            nav("◉  总览", "overview")
            nav("▣  监控中心", "monitor")
            section("网络与设备")
            nav("⌘  拓扑与设备", "topology")
            nav("◷  事件审计", "events")
            nav("▤  系统审计", "audit")
            nav("⇄  通知中心", "notifications")
            section("系统")
            nav("☷  常规设置", "settings-general")
            nav("◎  集中推理", "settings-inference")
            nav("↗  Webhook 通道", "settings-webhook")
            nav("✉  邮件 SMTP", "settings-email")
            nav("▤  短信通知服务", "settings-sms")
            nav("▣  存储与媒体", "settings-storage")
            nav("◎  健康诊断", "settings-health")
            layout.addStretch(1)
            footer = QLabel("本地部署 · SQLite\nCPU 推理 · RTSP")
            footer.setObjectName("muted")
            layout.addWidget(footer)
            return sidebar

        def _build_ui(self) -> None:
            root = QWidget(self)
            shell = QHBoxLayout(root)
            shell.setContentsMargins(0, 0, 0, 0)
            shell.setSpacing(0)
            shell.addWidget(self._build_sidebar())
            content = QWidget()
            outer = QVBoxLayout(content)
            outer.setContentsMargins(22, 18, 22, 18)
            outer.setSpacing(12)
            shell.addWidget(content, 1)

            header = QHBoxLayout()
            title_box = QVBoxLayout()
            self.header_title = QLabel("总览")
            self.header_title.setObjectName("title")
            self.header_subtitle = QLabel("FactoryGuard · 厂区视频安全系统")
            self.header_subtitle.setObjectName("subtitle")
            title_box.addWidget(self.header_title)
            title_box.addWidget(self.header_subtitle)
            header.addLayout(title_box)
            header.addStretch(1)
            header.addWidget(QLabel("主题"))
            self.theme_combo = QComboBox()
            self.theme_combo.addItems(["浅色", "深色", "海蓝"])
            self.theme_combo.currentTextChanged.connect(self._apply_theme)
            header.addWidget(self.theme_combo)
            self.status_label = QLabel()
            self.status_label.setObjectName("status")
            header.addWidget(self.status_label)
            self.channel_status_label = QLabel("CH01 · 待机")
            self.channel_status_label.setObjectName("channelStatus")
            header.addWidget(self.channel_status_label)
            outer.addLayout(header)

            top = QWidget()
            top_layout = QHBoxLayout(top)
            top_layout.setContentsMargins(0, 0, 0, 0)
            preview_group = QGroupBox("实时预览 · 检测框")
            preview_layout = QVBoxLayout(preview_group)
            self.preview = PreviewWidget()
            preview_layout.addWidget(self.preview, 1)
            self.preview_info = QLabel("等待视频流")
            self.preview_info.setObjectName("muted")
            preview_layout.addWidget(self.preview_info)
            top_layout.addWidget(preview_group, 3)

            side = QWidget()
            side_layout = QVBoxLayout(side)
            side_layout.setContentsMargins(0, 0, 0, 0)
            settings = QGroupBox("监控配置")
            form = QFormLayout(settings)
            self.url_edit = QLineEdit(self._base_config.url)
            self.model_edit = QLineEdit(self._base_config.model)
            self.db_edit = QLineEdit(self._base_config.db)
            self.width_spin = QSpinBox()
            self.width_spin.setRange(160, 7680)
            self.width_spin.setValue(self._base_config.width)
            self.height_spin = QSpinBox()
            self.height_spin.setRange(120, 4320)
            self.height_spin.setValue(self._base_config.height)
            self.confidence_spin = QDoubleSpinBox()
            self.confidence_spin.setRange(0.01, 1.0)
            self.confidence_spin.setSingleStep(0.05)
            self.confidence_spin.setValue(self._base_config.confidence)
            self.confirm_spin = QSpinBox()
            self.confirm_spin.setRange(1, 30)
            self.confirm_spin.setValue(self._base_config.confirm_frames)
            form.addRow("RTSP 地址", self.url_edit)
            form.addRow("ONNX 模型", self.model_edit)
            form.addRow("事件数据库", self.db_edit)
            size_row = QHBoxLayout()
            size_row.addWidget(QLabel("宽"))
            size_row.addWidget(self.width_spin)
            size_row.addWidget(QLabel("高"))
            size_row.addWidget(self.height_spin)
            form.addRow("画面尺寸", size_row)
            form.addRow("置信度", self.confidence_spin)
            form.addRow("确认帧数", self.confirm_spin)
            self.monitor_scope_combo = QComboBox()
            self.monitor_scope_combo.addItem("全部启用推流", "all")
            self.monitor_scope_combo.addItem("当前选中推流", "selected")
            form.addRow("启动范围", self.monitor_scope_combo)
            side_layout.addWidget(settings)

            controls = QHBoxLayout()
            self.start_button = QPushButton("启动监控")
            self.start_button.setObjectName("primary")
            self.stop_button = QPushButton("停止")
            self.stop_button.setEnabled(False)
            controls.addWidget(self.start_button)
            controls.addWidget(self.stop_button)
            side_layout.addLayout(controls)
            self.runtime_label = QLabel("帧数 0 · 检测 0 · 事件 0")
            self.runtime_label.setObjectName("muted")
            side_layout.addWidget(self.runtime_label)

            replay_group = QGroupBox("事件回放")
            replay_layout = QVBoxLayout(replay_group)
            self.event_detail = QLabel("选择一条事件记录查看快照")
            self.event_detail.setWordWrap(True)
            self.event_detail.setObjectName("muted")
            replay_layout.addWidget(self.event_detail)
            replay_buttons = QHBoxLayout()
            self.replay_button = QPushButton("查看回放媒体")
            self.replay_button.setObjectName("secondary")
            self.replay_button.setEnabled(False)
            self.live_button = QPushButton("返回实时")
            replay_buttons.addWidget(self.replay_button)
            replay_buttons.addWidget(self.live_button)
            replay_layout.addLayout(replay_buttons)
            side_layout.addWidget(replay_group)
            self.open_db_button = QPushButton("打开数据库目录")
            side_layout.addWidget(self.open_db_button)
            side_layout.addStretch(1)
            top_layout.addWidget(side, 2)

            multiview_group = QGroupBox("多路预览 · 独立状态")
            self._preview_grid_layout = QGridLayout(multiview_group)
            self._preview_grid_layout.setContentsMargins(8, 8, 8, 8)
            self._preview_grid_layout.setSpacing(8)
            monitor_page = QWidget()
            monitor_layout = QVBoxLayout(monitor_page)
            monitor_layout.setContentsMargins(0, 0, 0, 0)
            monitor_layout.addWidget(top, 3)
            monitor_layout.addWidget(multiview_group, 2)

            events_group = QGroupBox("事件记录 · 搜索 / 筛选")
            events_layout = QVBoxLayout(events_group)
            filters = QHBoxLayout()
            self.search_edit = QLineEdit()
            self.search_edit.setPlaceholderText("搜索事件 ID、目标、通道或规则…")
            self.level_filter = QComboBox()
            self.level_filter.addItem("全部级别", None)
            self.level_filter.addItem("高", "high")
            self.level_filter.addItem("中", "medium")
            self.level_filter.addItem("低", "low")
            self.status_filter = QComboBox()
            self.status_filter.addItem("全部状态", None)
            self.status_filter.addItem("待处理", "open")
            self.status_filter.addItem("已确认", "confirmed")
            self.status_filter.addItem("误报", "false_positive")
            self.status_filter.addItem("已关闭", "closed")
            self.channel_filter = QComboBox()
            self.channel_filter.addItem("全部通道", None)
            self.time_filter = QComboBox()
            self.time_filter.addItem("全部时间", None)
            self.time_filter.addItem("最近 1 小时", 3600)
            self.time_filter.addItem("最近 24 小时", 86400)
            self.time_filter.addItem("最近 7 天", 604800)
            self.refresh_button = QPushButton("刷新")
            filters.addWidget(self.search_edit, 1)
            filters.addWidget(self.level_filter)
            filters.addWidget(self.status_filter)
            filters.addWidget(self.channel_filter)
            filters.addWidget(self.time_filter)
            filters.addWidget(self.refresh_button)
            events_layout.addLayout(filters)
            entity_filters = QHBoxLayout()
            self.site_filter = QComboBox()
            self.site_filter.addItem("全部厂区", None)
            self.area_filter = QComboBox()
            self.area_filter.addItem("全部区域", None)
            self.camera_filter = QComboBox()
            self.camera_filter.addItem("全部摄像头", None)
            entity_filters.addWidget(self.site_filter)
            entity_filters.addWidget(self.area_filter)
            entity_filters.addWidget(self.camera_filter)
            entity_filters.addStretch(1)
            events_layout.addLayout(entity_filters)
            self.events_table = QTableWidget(0, 6)
            self.events_table.setHorizontalHeaderLabels(["时间", "级别", "实体路径", "目标", "状态", "媒体"])
            self.events_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.events_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.events_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.events_table.setAlternatingRowColors(True)
            self.events_table.horizontalHeader().setStretchLastSection(True)
            events_layout.addWidget(self.events_table, 1)
            review_controls = QHBoxLayout()
            self.confirm_event_button = QPushButton("确认事件")
            self.false_positive_button = QPushButton("标记误报")
            self.close_event_button = QPushButton("关闭事件")
            self.export_events_button = QPushButton("导出事件 CSV")
            self.export_audit_button = QPushButton("导出审计 JSON")
            for button in (self.confirm_event_button, self.false_positive_button, self.close_event_button):
                button.setEnabled(False)
                review_controls.addWidget(button)
            review_controls.addWidget(self.export_events_button)
            review_controls.addWidget(self.export_audit_button)
            review_controls.addStretch(1)
            events_layout.addLayout(review_controls)
            inspector_group = QGroupBox("事件详情")
            inspector_layout = QVBoxLayout(inspector_group)
            self.event_inspector = QLabel("选择一条事件记录查看详细信息")
            self.event_inspector.setWordWrap(True)
            self.event_inspector.setObjectName("muted")
            inspector_layout.addWidget(self.event_inspector)
            events_layout.addWidget(inspector_group)
            events_page = QWidget()
            events_page_layout = QVBoxLayout(events_page)
            events_page_layout.setContentsMargins(0, 0, 0, 0)
            events_page_layout.addWidget(events_group, 1)

            self._page_routes = {
                "overview": 0,
                "monitor": 1,
                "topology": 2,
                "events": 3,
                "audit": 4,
                "notifications": 5,
                "settings-general": 6,
                "settings-inference": 7,
                "settings-webhook": 8,
                "settings-email": 9,
                "settings-sms": 10,
                "settings-storage": 11,
                "settings-health": 12,
            }
            self.page_stack = ResponsiveStackedWidget()
            self.page_stack.addWidget(self._build_overview_page())
            self.page_stack.addWidget(monitor_page)
            self.page_stack.addWidget(self._build_topology_page())
            self.page_stack.addWidget(events_page)
            self.page_stack.addWidget(self._build_audit_page())
            self.page_stack.addWidget(self._build_notification_center_page())
            self.page_stack.addWidget(self._build_general_settings_page())
            self.page_stack.addWidget(self._build_inference_settings_page())
            self.page_stack.addWidget(self._build_webhook_settings_page())
            self.page_stack.addWidget(self._build_email_settings_page())
            self.page_stack.addWidget(self._build_sms_settings_page())
            self.page_stack.addWidget(self._build_storage_settings_page())
            self.page_stack.addWidget(self._build_health_settings_page())
            self.page_stack.setMinimumHeight(680)
            self.page_scroll = QScrollArea()
            self.page_scroll.setObjectName("pageScroll")
            self.page_scroll.setFrameShape(QFrame.NoFrame)
            self.page_scroll.setWidgetResizable(True)
            self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.page_scroll.setWidget(self.page_stack)
            outer.addWidget(self.page_scroll, 1)
            self._switch_page(0)
            self.setCentralWidget(root)
            self.setStatusBar(QStatusBar(self))

            self.start_button.clicked.connect(self.start_monitor)
            self.stop_button.clicked.connect(self.stop_monitor)
            self.refresh_button.clicked.connect(self._refresh_events)
            self.refresh_button.clicked.connect(self._refresh_entity_tree)
            self.refresh_button.clicked.connect(self._refresh_audit)
            self.search_edit.textChanged.connect(self._apply_event_filters)
            self.level_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.status_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.channel_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.time_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.site_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.area_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.camera_filter.currentIndexChanged.connect(self._apply_event_filters)
            self.events_table.itemSelectionChanged.connect(self._on_event_selected)
            self.events_table.cellDoubleClicked.connect(lambda _row, _column: self._show_selected_replay())
            self.confirm_event_button.clicked.connect(lambda: self._set_selected_event_status(EventStatus.CONFIRMED))
            self.false_positive_button.clicked.connect(lambda: self._set_selected_event_status(EventStatus.FALSE_POSITIVE))
            self.close_event_button.clicked.connect(lambda: self._set_selected_event_status(EventStatus.CLOSED))
            self.export_events_button.clicked.connect(self._export_events_csv)
            self.export_audit_button.clicked.connect(self._export_audit_json)
            self.replay_button.clicked.connect(self._show_selected_replay)
            self.live_button.clicked.connect(self._show_live_preview)
            self.open_db_button.clicked.connect(self._open_database)

        def _build_audit_page(self) -> QWidget:
            """Build the operational audit view used by administrators."""
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            title = QLabel("系统审计")
            title.setObjectName("title")
            layout.addWidget(title)
            hint = QLabel("查看配置变更、推流健康、事件处置和通知链路的操作记录。")
            hint.setObjectName("muted")
            layout.addWidget(hint)

            audit_group = QGroupBox("审计记录")
            audit_layout = QVBoxLayout(audit_group)
            filters = QHBoxLayout()
            self.audit_search_edit = QLineEdit()
            self.audit_search_edit.setPlaceholderText("搜索操作人、动作、资源或详情…")
            self.audit_actor_filter = QComboBox()
            self.audit_actor_filter.addItem("全部操作人", None)
            self.audit_action_filter = QComboBox()
            self.audit_action_filter.addItem("全部动作", None)
            self.audit_refresh_button = QPushButton("刷新")
            self.audit_export_button = QPushButton("导出当前结果")
            filters.addWidget(self.audit_search_edit, 1)
            filters.addWidget(self.audit_actor_filter)
            filters.addWidget(self.audit_action_filter)
            filters.addWidget(self.audit_refresh_button)
            filters.addWidget(self.audit_export_button)
            audit_layout.addLayout(filters)

            self.audit_table = QTableWidget(0, 6)
            self.audit_table.setHorizontalHeaderLabels(["时间", "操作人", "动作", "资源", "结果", "详情"])
            self.audit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.audit_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.audit_table.setAlternatingRowColors(True)
            self.audit_table.horizontalHeader().setStretchLastSection(True)
            audit_layout.addWidget(self.audit_table, 1)
            self.audit_summary_label = QLabel("等待读取审计记录")
            self.audit_summary_label.setObjectName("muted")
            audit_layout.addWidget(self.audit_summary_label)
            layout.addWidget(audit_group, 1)

            self.audit_search_edit.textChanged.connect(self._apply_audit_filters)
            self.audit_actor_filter.currentIndexChanged.connect(self._apply_audit_filters)
            self.audit_action_filter.currentIndexChanged.connect(self._apply_audit_filters)
            self.audit_refresh_button.clicked.connect(self._refresh_audit)
            self.audit_export_button.clicked.connect(self._export_audit_json)
            return page

        def _build_overview_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            intro = QLabel("运行概览")
            intro.setObjectName("title")
            layout.addWidget(intro)
            hint = QLabel("从厂区拓扑、实时视频到事件闭环，统一查看系统运行状态。")
            hint.setObjectName("muted")
            layout.addWidget(hint)

            cards = QHBoxLayout()
            cards.setContentsMargins(0, 4, 0, 4)
            cards.setSpacing(8)
            self._overview_labels: dict[str, QLabel] = {}
            for key, title in (
                ("sites", "厂区"),
                ("areas", "区域"),
                ("cameras", "摄像头"),
                ("streams", "推流"),
                ("events", "事件"),
            ):
                card = QFrame()
                card.setObjectName("metricCard")
                card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 10, 12, 10)
                card_layout.setSpacing(2)
                caption = QLabel(title)
                caption.setObjectName("metricCaption")
                card_layout.addWidget(caption)
                value = QLabel("0")
                value.setObjectName("metricValue")
                card_layout.addWidget(value)
                detail = QLabel("已登记" if key != "events" else "已记录")
                detail.setObjectName("metricDetail")
                card_layout.addWidget(detail)
                self._overview_labels[key] = value
                cards.addWidget(card, 1)
            layout.addLayout(cards)

            actions = QGroupBox("快捷入口")
            action_layout = QHBoxLayout(actions)
            monitor_button = QPushButton("进入监控中心")
            monitor_button.setObjectName("primary")
            monitor_button.clicked.connect(lambda: self._switch_page(1))
            topology_button = QPushButton("管理拓扑与设备")
            topology_button.clicked.connect(lambda: self._switch_page(2))
            events_button = QPushButton("查看事件审计")
            events_button.clicked.connect(lambda: self._switch_page(3))
            action_layout.addWidget(monitor_button)
            action_layout.addWidget(topology_button)
            action_layout.addWidget(events_button)
            action_layout.addStretch(1)
            layout.addWidget(actions)

            status = QGroupBox("系统状态")
            status_layout = QVBoxLayout(status)
            self.overview_status = QLabel("正在读取本地配置…")
            self.overview_status.setObjectName("muted")
            status_layout.addWidget(self.overview_status)
            layout.addWidget(status)
            health_group = QGroupBox("推流健康概览")
            health_layout = QVBoxLayout(health_group)
            self.overview_stream_table = QTableWidget(0, 6)
            self.overview_stream_table.setHorizontalHeaderLabels(["推流", "实体路径", "状态", "帧 / 检测", "最近成功", "重连次数"])
            self.overview_stream_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.overview_stream_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.overview_stream_table.setAlternatingRowColors(True)
            self.overview_stream_table.horizontalHeader().setStretchLastSection(True)
            self.overview_stream_table.cellDoubleClicked.connect(
                lambda row, _column: self._select_overview_stream(row)
            )
            health_layout.addWidget(self.overview_stream_table)
            layout.addWidget(health_group, 1)

            analytics_group = QGroupBox("事件运营概览")
            analytics_layout = QHBoxLayout(analytics_group)
            self.overview_event_summary = QLabel("正在统计事件")
            self.overview_event_summary.setWordWrap(True)
            self.overview_event_summary.setObjectName("muted")
            analytics_layout.addWidget(self.overview_event_summary, 1)
            self.overview_event_table = QTableWidget(0, 4)
            self.overview_event_table.setHorizontalHeaderLabels(["实体", "事件数", "待处理", "高危"])
            self.overview_event_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.overview_event_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.overview_event_table.setAlternatingRowColors(True)
            self.overview_event_table.horizontalHeader().setStretchLastSection(True)
            self.overview_event_table.cellDoubleClicked.connect(
                lambda row, _column: self._select_overview_event_stream(row)
            )
            analytics_layout.addWidget(self.overview_event_table, 2)
            layout.addWidget(analytics_group)
            layout.addStretch(1)
            return page

        def _build_topology_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            title = QLabel("拓扑与设备")
            title.setObjectName("title")
            layout.addWidget(title)
            hint = QLabel("站点、嵌套区域、摄像头和每一路主码流/子码流/分析流在这里统一管理。")
            hint.setObjectName("muted")
            layout.addWidget(hint)
            self.topology_search_edit = QLineEdit()
            self.topology_search_edit.setPlaceholderText("搜索厂区、区域、摄像头或推流…")
            layout.addWidget(self.topology_search_edit)
            actions = QHBoxLayout()
            self.refresh_topology_button = QPushButton("刷新")
            self.add_site_button = QPushButton("新增厂区")
            self.add_area_button = QPushButton("新增区域")
            self.add_camera_button = QPushButton("新增摄像头")
            self.add_stream_button = QPushButton("新增推流")
            self.add_bulk_stream_button = QPushButton("批量新增推流")
            self.import_topology_button = QPushButton("导入 CSV")
            self.export_topology_button = QPushButton("导出 CSV")
            self.save_stream_button = QPushButton("保存推流配置")
            self.edit_entity_button = QPushButton("编辑选中实体")
            self.toggle_entity_button = QPushButton("启用/停用选中实体")
            self.toggle_entity_button.setEnabled(False)
            self.save_stream_button.setEnabled(False)
            self.edit_entity_button.setEnabled(False)
            actions.addWidget(self.refresh_topology_button)
            actions.addWidget(self.add_site_button)
            actions.addWidget(self.add_area_button)
            actions.addWidget(self.add_camera_button)
            actions.addWidget(self.add_stream_button)
            actions.addWidget(self.add_bulk_stream_button)
            actions.addWidget(self.import_topology_button)
            actions.addWidget(self.export_topology_button)
            actions.addWidget(self.save_stream_button)
            actions.addWidget(self.edit_entity_button)
            actions.addWidget(self.toggle_entity_button)
            actions.addStretch(1)
            layout.addLayout(actions)
            body = QHBoxLayout()
            tree_group = QGroupBox("实体层级")
            tree_layout = QVBoxLayout(tree_group)
            self.entity_tree = QTreeWidget()
            self.entity_tree.setHeaderHidden(True)
            self.entity_tree.setMinimumWidth(300)
            self.entity_tree.itemSelectionChanged.connect(self._on_entity_selected)
            tree_layout.addWidget(self.entity_tree)
            body.addWidget(tree_group, 1)

            streams_group = QGroupBox("推流清单")
            streams_layout = QVBoxLayout(streams_group)
            self.stream_table = QTableWidget(0, 8)
            self.stream_table.setHorizontalHeaderLabels(["站点", "区域", "摄像头", "推流", "角色", "地址", "运行状态", "布防"])
            self.stream_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.stream_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.stream_table.setAlternatingRowColors(True)
            self.stream_table.horizontalHeader().setStretchLastSection(True)
            self.stream_table.itemSelectionChanged.connect(self._on_stream_table_selected)
            streams_layout.addWidget(self.stream_table, 3)
            rule_actions = QHBoxLayout()
            self.add_rule_button = QPushButton("新增规则")
            self.edit_rule_button = QPushButton("编辑选中规则")
            self.toggle_rule_button = QPushButton("启用/停用选中规则")
            self.refresh_rules_button = QPushButton("刷新规则")
            self.arm_button = QPushButton("布防")
            self.disarm_button = QPushButton("撤防")
            self.arm_all_button = QPushButton("全部布防")
            self.disarm_all_button = QPushButton("全部撤防")
            self.schedule_button = QPushButton("时间表")
            self.probe_stream_button = QPushButton("测试连接")
            self.add_rule_button.setEnabled(False)
            self.edit_rule_button.setEnabled(False)
            self.toggle_rule_button.setEnabled(False)
            self.arm_button.setEnabled(False)
            self.disarm_button.setEnabled(False)
            self.schedule_button.setEnabled(False)
            self.probe_stream_button.setEnabled(False)
            rule_actions.addWidget(self.add_rule_button)
            rule_actions.addWidget(self.edit_rule_button)
            rule_actions.addWidget(self.toggle_rule_button)
            rule_actions.addWidget(self.refresh_rules_button)
            rule_actions.addWidget(self.arm_button)
            rule_actions.addWidget(self.disarm_button)
            rule_actions.addWidget(self.arm_all_button)
            rule_actions.addWidget(self.disarm_all_button)
            rule_actions.addWidget(self.schedule_button)
            rule_actions.addWidget(self.probe_stream_button)
            rule_actions.addStretch(1)
            streams_layout.addLayout(rule_actions)
            self.rule_table = QTableWidget(0, 5)
            self.rule_table.setHorizontalHeaderLabels(["规则", "推流", "类型", "确认帧", "状态"])
            self.rule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.rule_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.rule_table.setAlternatingRowColors(True)
            self.rule_table.horizontalHeader().setStretchLastSection(True)
            self.rule_table.itemSelectionChanged.connect(self._on_rule_selected)
            self.rule_table.cellDoubleClicked.connect(lambda _row, _column: self._edit_selected_rule())
            streams_layout.addWidget(self.rule_table, 2)
            body.addWidget(streams_group, 2)
            layout.addLayout(body, 1)
            self.refresh_topology_button.clicked.connect(self._refresh_entity_tree)
            self.add_site_button.clicked.connect(self._add_site)
            self.add_area_button.clicked.connect(self._add_area)
            self.add_camera_button.clicked.connect(self._add_camera)
            self.add_stream_button.clicked.connect(self._add_stream)
            self.add_bulk_stream_button.clicked.connect(self._add_bulk_streams)
            self.import_topology_button.clicked.connect(self._import_topology_csv)
            self.export_topology_button.clicked.connect(self._export_topology_csv)
            self.save_stream_button.clicked.connect(self._save_selected_stream_config)
            self.edit_entity_button.clicked.connect(self._edit_selected_entity)
            self.toggle_entity_button.clicked.connect(self._toggle_selected_entity)
            self.add_rule_button.clicked.connect(self._add_default_rule)
            self.edit_rule_button.clicked.connect(self._edit_selected_rule)
            self.toggle_rule_button.clicked.connect(self._toggle_selected_rule)
            self.refresh_rules_button.clicked.connect(self._refresh_rule_table)
            self.arm_button.clicked.connect(lambda: self._set_stream_armed(True))
            self.disarm_button.clicked.connect(lambda: self._set_stream_armed(False))
            self.arm_all_button.clicked.connect(lambda: self._set_all_streams_armed(True))
            self.disarm_all_button.clicked.connect(lambda: self._set_all_streams_armed(False))
            self.schedule_button.clicked.connect(self._edit_stream_schedule)
            self.probe_stream_button.clicked.connect(self._probe_selected_stream)
            self.topology_search_edit.textChanged.connect(self._apply_topology_filter)
            return page

        def _notification_channels_from_form(self) -> tuple[str, ...]:
            self._sync_all_destination_editors()
            return tuple(
                destination.outbox_channel
                for destination in self._destinations
                if destination.enabled
            )

        def _validate_notification_destinations(self) -> None:
            for destination in self._destinations:
                if not destination.enabled:
                    continue
                settings = destination.settings
                if destination.kind == "webhook":
                    url = str(settings.get("url", "")).strip()
                    secret = str(destination.secrets.get("secret", ""))
                    if not url.lower().startswith("https://"):
                        raise ValueError(f"{destination.name}: Webhook 地址必须使用 HTTPS")
                    if not secret:
                        raise ValueError(f"{destination.name}: 缺少 Webhook 签名密钥")
                elif destination.kind == "email":
                    EmailAdapter(
                        host=str(settings.get("host", "")),
                        port=int(settings.get("port", 587)),
                        username=str(settings.get("username", "")),
                        password=str(destination.secrets.get("password", "")),
                        sender=str(settings.get("sender", "")),
                        recipients=settings.get("recipients", ()),
                        use_ssl=str(settings.get("security", "starttls")) == "ssl",
                        starttls=str(settings.get("security", "starttls")) == "starttls",
                    )
                elif destination.kind == "sms":
                    SmsHttpAdapter(
                        endpoint=str(settings.get("endpoint", "")),
                        recipients=settings.get("recipients", ()),
                        provider=destination.provider,
                        token=str(destination.secrets.get("token", "")),
                        sender=str(settings.get("sender", "")),
                        template_id=str(settings.get("template_id", "")),
                    )
                else:
                    raise ValueError(f"{destination.name}: 不支持的通知类型 {destination.kind}")

        def _email_security_settings(self) -> tuple[bool, bool]:
            mode = str(self.email_security_combo.currentData() or "starttls")
            if mode == "ssl":
                return True, False
            if mode == "none":
                return False, False
            return False, True

        def _email_recipients_from_form(self) -> tuple[str, ...]:
            try:
                return parse_recipients(self.email_recipients_edit.text())
            except ValueError:
                return ()

        def _sms_recipients_from_form(self) -> tuple[str, ...]:
            try:
                return parse_phone_recipients(self.sms_recipients_edit.text())
            except ValueError:
                return ()

        def _save_notification_settings(self) -> None:
            """Persist non-sensitive notification settings in the local data directory."""
            if not self._require_permission(Permission.MANAGE_SYSTEM, "通知配置"):
                return
            channels = self._notification_channels_from_form()
            if not channels:
                self.statusBar().showMessage("请至少启用一个通知通道，未保存", 6000)
                return
            try:
                self._validate_notification_destinations()
            except (TypeError, ValueError) as exc:
                self.statusBar().showMessage(f"通知配置无效：{exc}", 7000)
                return
            url = self.webhook_url_edit.text().strip()
            if "webhook" in channels and not url:
                self.statusBar().showMessage("请填写 Webhook 回调地址，未保存", 6000)
                return
            if "webhook" in channels and url and not url.lower().startswith("https://"):
                self.statusBar().showMessage("Webhook 地址必须使用 HTTPS，未保存", 6000)
                return
            recipients = self._email_recipients_from_form()
            use_ssl, starttls = self._email_security_settings()
            if "email" in channels:
                try:
                    EmailAdapter(
                        host=self.email_host_edit.text(),
                        port=self.email_port_spin.value(),
                        username=self.email_username_edit.text(),
                        password=self.email_password_edit.text(),
                        sender=self.email_sender_edit.text(),
                        recipients=recipients,
                        use_ssl=use_ssl,
                        starttls=starttls,
                    )
                except ValueError as exc:
                    self.statusBar().showMessage(f"邮件配置无效：{exc}", 7000)
                    return
            sms_recipients = self._sms_recipients_from_form()
            if "sms" in channels:
                try:
                    SmsHttpAdapter(
                        endpoint=self.sms_endpoint_edit.text(),
                        recipients=sms_recipients,
                        provider=str(self.sms_provider_combo.currentData() or "custom_http"),
                        token=self.sms_token_edit.text(),
                        sender=self.sms_sender_edit.text(),
                        template_id=self.sms_template_edit.text(),
                    )
                except ValueError as exc:
                    self.statusBar().showMessage(f"短信配置无效：{exc}", 7000)
                    return
            self._local_settings["notification"] = {
                "channels": list(channels),
                "destinations": [destination.to_public_dict() for destination in self._destinations],
                "webhook_provider": str(self.webhook_provider_combo.currentData() or "custom"),
                "webhook_url": url,
                "email": {
                    "provider": str(self.email_provider_combo.currentData() or "custom"),
                    "host": self.email_host_edit.text().strip(),
                    "port": self.email_port_spin.value(),
                    "username": self.email_username_edit.text().strip(),
                    "sender": self.email_sender_edit.text().strip(),
                    "recipients": list(recipients),
                    "security": str(self.email_security_combo.currentData() or "starttls"),
                },
                "sms": {
                    "provider": str(self.sms_provider_combo.currentData() or "custom_http"),
                    "endpoint": self.sms_endpoint_edit.text().strip(),
                    "sender": self.sms_sender_edit.text().strip(),
                    "template_id": self.sms_template_edit.text().strip(),
                    "recipients": list(sms_recipients),
                },
                "interval": self.notification_interval_spin.value(),
                "batch": self.notification_batch_spin.value(),
            }
            try:
                self._local_settings_path.parent.mkdir(parents=True, exist_ok=True)
                self._local_settings_path.write_text(
                    json.dumps(self._local_settings, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.statusBar().showMessage(f"通知配置保存失败：{exc}", 6000)
                return
            self._refresh_destination_table("webhook")
            self._refresh_destination_table("email")
            self._refresh_destination_table("sms")
            self.statusBar().showMessage(
                "通知配置已保存（Webhook 密钥、SMTP 密码和短信令牌仅保留在当前进程）",
                6000,
            )

        def _save_operator_settings(self) -> None:
            actor = self._current_actor()
            role = self._current_role()
            self._local_settings["operator"] = actor
            self._local_settings["role"] = role.value
            try:
                self._local_settings_path.parent.mkdir(parents=True, exist_ok=True)
                self._local_settings_path.write_text(
                    json.dumps(self._local_settings, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.statusBar().showMessage(f"操作身份保存失败：{exc}", 6000)
                return
            self.statusBar().showMessage(f"当前操作人已设置为：{actor} · {ROLE_LABELS[role]}", 5000)

        def _current_actor(self) -> str:
            value = ""
            if hasattr(self, "operator_name_edit"):
                value = self.operator_name_edit.text().strip()
            if not value:
                value = str(self._local_settings.get("operator", "ui") or "ui").strip()
            # Keep audit rows single-line and bounded even when the value came
            # from a copied note or an external settings file.
            value = " ".join(value.split())[:64]
            return value or "ui"

        def _current_role(self) -> UserRole:
            if hasattr(self, "operator_role_combo"):
                return role_from_value(self.operator_role_combo.currentData())
            return role_from_value(self._local_settings.get("role", UserRole.ADMIN.value))

        def _require_permission(self, permission: Permission, action: str) -> bool:
            role = self._current_role()
            if can(role, permission):
                return True
            self.statusBar().showMessage(
                f"当前角色“{ROLE_LABELS[role]}”没有“{action}”权限", 6000
            )
            return False

        def _initialize_notification_destinations(self) -> None:
            """Load multi-instance destinations with single-channel migration."""
            saved = self._saved_notification_config()
            loaded: list[NotificationDestination] = []
            raw_destinations = saved.get("destinations", ())
            if isinstance(raw_destinations, list):
                for value in raw_destinations:
                    if not isinstance(value, dict):
                        continue
                    try:
                        loaded.append(NotificationDestination.from_public_dict(value))
                    except (TypeError, ValueError):
                        continue
            loaded_kinds = {value.kind for value in loaded}
            raw_channels = saved.get("channels", self._base_config.notification_channels)
            if isinstance(raw_channels, str):
                raw_channels = (raw_channels,)
            try:
                configured_channels = {str(value).strip().lower() for value in raw_channels}
            except TypeError:
                configured_channels = set(self._base_config.notification_channels)

            saved_email = saved.get("email", {})
            if not isinstance(saved_email, dict):
                saved_email = {}
            saved_sms = saved.get("sms", {})
            if not isinstance(saved_sms, dict):
                saved_sms = {}

            if "webhook" not in loaded_kinds:
                destination = default_destination(
                    "webhook",
                    enabled=any(value == "webhook" or value.startswith("webhook:") for value in configured_channels),
                    provider=str(saved.get("webhook_provider", "custom") or "custom"),
                )
                destination.settings["url"] = self._base_config.webhook_url or str(
                    saved.get("webhook_url", "") or ""
                )
                destination.secrets["secret"] = self._base_config.webhook_secret
                loaded.append(destination)

            if "email" not in loaded_kinds:
                destination = default_destination(
                    "email",
                    enabled=any(value == "email" or value.startswith("email:") for value in configured_channels),
                    provider=str(saved_email.get("provider", "custom") or "custom"),
                )
                destination.settings.update(
                    {
                        "host": self._base_config.email_host or str(saved_email.get("host", "") or ""),
                        "port": int(saved_email.get("port", self._base_config.email_port) or 587),
                        "username": self._base_config.email_username
                        or str(saved_email.get("username", "") or ""),
                        "sender": self._base_config.email_sender or str(saved_email.get("sender", "") or ""),
                        "recipients": list(
                            saved_email.get("recipients", self._base_config.email_recipients) or ()
                        ),
                        "security": str(saved_email.get("security", "starttls") or "starttls"),
                    }
                )
                destination.secrets["password"] = self._base_config.email_password
                loaded.append(destination)

            if "sms" not in loaded_kinds:
                destination = default_destination(
                    "sms",
                    enabled=any(value == "sms" or value.startswith("sms:") for value in configured_channels),
                    provider=str(saved_sms.get("provider", "custom_http") or "custom_http"),
                )
                destination.settings.update(
                    {
                        "endpoint": str(saved_sms.get("endpoint", "") or ""),
                        "sender": str(saved_sms.get("sender", "") or ""),
                        "template_id": str(saved_sms.get("template_id", "") or ""),
                        "recipients": list(saved_sms.get("recipients", self._base_config.sms_recipients) or ()),
                    }
                )
                destination.secrets["token"] = self._base_config.sms_token
                loaded.append(destination)

            for destination in loaded:
                if destination.id != f"default-{destination.kind}":
                    continue
                if destination.kind == "webhook":
                    destination.settings.setdefault(
                        "url", self._base_config.webhook_url or str(saved.get("webhook_url", "") or "")
                    )
                    destination.secrets.setdefault("secret", self._base_config.webhook_secret)
                elif destination.kind == "email":
                    destination.settings.setdefault("host", self._base_config.email_host)
                    destination.secrets.setdefault("password", self._base_config.email_password)
                elif destination.kind == "sms":
                    destination.secrets.setdefault("token", self._base_config.sms_token)

            self._destinations = loaded
            self._selected_webhook_destination_id = self._first_destination_id("webhook")
            self._selected_email_destination_id = self._first_destination_id("email")
            self._selected_sms_destination_id = self._first_destination_id("sms")

        def _destinations_for_kind(self, kind: str) -> list[NotificationDestination]:
            return [value for value in self._destinations if value.kind == kind]

        def _first_destination_id(self, kind: str) -> str:
            values = self._destinations_for_kind(kind)
            return values[0].id if values else f"default-{kind}"

        def _destination_by_id(self, kind: str, destination_id: str | None) -> NotificationDestination | None:
            for value in self._destinations_for_kind(kind):
                if value.id == destination_id:
                    return value
            return None

        def _resolve_local_settings_path(self) -> Path:
            database = self._base_config.db
            if database == ":memory:":
                return Path.cwd() / "data" / "factoryguard-ui-settings.json"
            return Path(database).expanduser().resolve().parent / "factoryguard-ui-settings.json"

        def _read_local_settings(self) -> dict[str, Any]:
            try:
                value = json.loads(self._local_settings_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return {}
            return dict(value) if isinstance(value, dict) else {}

        def _health_snapshot(self) -> dict[str, Any]:
            path_text = self.db_edit.text().strip() or self._base_config.db
            database = Path(path_text).expanduser().resolve() if path_text != ":memory:" else None
            disk_root = database.parent if database is not None else Path.cwd()
            while not disk_root.exists() and disk_root != disk_root.parent:
                disk_root = disk_root.parent
            usage = shutil.disk_usage(disk_root)
            integrity = "not_checked"
            pending_notifications = 0
            if database is not None and database.exists():
                store = SQLiteStore(database)
                try:
                    integrity = store.integrity_check()
                    pending_notifications = len(store.list_pending_outbox())
                finally:
                    store.close()
            topology = self._topology
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "application": "FactoryGuard",
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "database": str(database) if database is not None else ":memory:",
                "database_exists": database.exists() if database is not None else False,
                "database_integrity": integrity,
                "pending_notifications": pending_notifications,
                "disk": {
                    "path": str(disk_root),
                    "free_gb": round(usage.free / (1024**3), 2),
                    "total_gb": round(usage.total / (1024**3), 2),
                },
                "topology": {
                    "sites": len(topology.sites) if topology is not None else 0,
                    "areas": len(topology.areas) if topology is not None else 0,
                    "cameras": len(topology.cameras) if topology is not None else 0,
                    "streams": len(topology.streams) if topology is not None else 0,
                },
                "stream_states": {
                    stream_id: {"status": status, "detail": _redact_text(detail)}
                    for stream_id, (status, detail) in self._stream_states.items()
                },
                "stream_health_history": {
                    stream_id: {
                        **snapshot,
                        "detail": _redact_text(snapshot.get("detail", "")),
                    }
                    for stream_id, snapshot in self._stream_health_history.items()
                },
                "notifications": {
                    "status": self._notification_state,
                    "detail": _redact_text(self._notification_detail),
                    "polls": self._notification_reports,
                },
                "events_loaded": len(self._event_records),
                "monitoring": self._thread is not None and self._thread.isRunning(),
                "preflight": self._preflight_checks(database=database, disk_root=disk_root),
            }

        def _preflight_checks(self, *, database: Path | None, disk_root: Path) -> list[dict[str, str]]:
            """Run lightweight local checks before an operator starts monitoring."""
            checks: list[dict[str, str]] = []

            def add(name: str, state: str, detail: str) -> None:
                checks.append({"name": name, "state": state, "detail": detail})

            if database is None:
                add("SQLite 数据库", "warn", "当前使用内存数据库，重启后不会保留数据")
            elif database.exists() and self._health_database_integrity(database) == "ok":
                add("SQLite 数据库", "ok", "文件存在且完整性检查通过")
            elif database.exists():
                add("SQLite 数据库", "error", "完整性检查未通过")
            else:
                add("SQLite 数据库", "warn", "数据库尚未创建，启动监控时会自动初始化")

            ffmpeg = find_ffmpeg()
            if ffmpeg is None:
                add("FFmpeg", "error", "未找到可用 FFmpeg，请配置 FACTORYGUARD_FFMPEG")
            else:
                add("FFmpeg", "ok", str(ffmpeg.executable))

            model_paths: set[str] = {self.model_edit.text().strip() or self._base_config.model}
            if self._topology is not None:
                model_paths.update(
                    stream.model.strip()
                    for stream in self._topology.streams
                    if stream.model and stream.model.strip()
                )
            missing_models = [value for value in sorted(model_paths) if not Path(value).expanduser().is_file()]
            if missing_models:
                add("ONNX 模型", "error", "缺少：" + ", ".join(missing_models[:3]))
            else:
                add("ONNX 模型", "ok", f"已检查 {len(model_paths)} 个模型文件")

            media_text = self._base_config.media_dir
            media_root = Path(media_text).expanduser()
            if not media_root.is_absolute() and database is not None:
                media_root = database.parent / media_root
            try:
                media_root.mkdir(parents=True, exist_ok=True)
                probe = media_root / ".factoryguard-write-check"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                add("媒体目录", "ok", str(media_root.resolve()))
            except OSError as exc:
                add("媒体目录", "error", str(exc))

            webhook_url = self.webhook_url_edit.text().strip()
            webhook_secret = self.webhook_secret_edit.text()
            channels = self._notification_channels_from_form()
            if not channels:
                add("通知配置", "warn", "未启用通知通道，事件仍会写入本地 Outbox")
            else:
                try:
                    self._validate_notification_destinations()
                except (TypeError, ValueError) as exc:
                    add("通知实例", "error", str(exc))
                else:
                    add("通知实例", "ok", f"已启用 {len(channels)} 个独立投递实例")
            if "webhook" in channels:
                if not webhook_url:
                    add("通知配置", "warn", "未配置 Webhook 地址")
                elif not webhook_url.lower().startswith("https://"):
                    add("通知配置", "error", "Webhook 地址必须使用 HTTPS")
                elif not webhook_secret:
                    add("通知配置", "warn", "已配置地址，但当前进程缺少签名密钥")
                else:
                    add("通知配置", "ok", "HTTPS Webhook 与签名密钥已配置")
            if "email" in channels:
                email_host = self.email_host_edit.text().strip()
                email_sender = self.email_sender_edit.text().strip()
                email_recipients = self._email_recipients_from_form()
                if not email_host or not email_sender or not email_recipients:
                    add("邮件通知", "warn", "SMTP 主机、发件人或收件人尚未配置完整")
                elif self.email_username_edit.text().strip() and not self.email_password_edit.text():
                    add("邮件通知", "warn", "已配置 SMTP 账号，但当前进程缺少密码或授权码")
                else:
                    add("邮件通知", "ok", f"SMTP {email_host}:{self.email_port_spin.value()} 已配置")
            if "sms" in channels:
                sms_endpoint = self.sms_endpoint_edit.text().strip()
                sms_recipients = self._sms_recipients_from_form()
                if not sms_endpoint or not sms_recipients:
                    add("短信通知", "warn", "短信网关或接收号码尚未配置完整")
                elif not sms_endpoint.lower().startswith("https://"):
                    add("短信通知", "error", "短信网关必须使用 HTTPS")
                else:
                    add("短信通知", "ok", f"HTTP 网关已配置，共 {len(sms_recipients)} 个号码")

            usage = shutil.disk_usage(disk_root)
            free_ratio = usage.free / max(usage.total, 1)
            if free_ratio < 0.05:
                add("磁盘空间", "error", f"可用空间仅 {usage.free / (1024**3):.2f} GB")
            elif free_ratio < 0.15:
                add("磁盘空间", "warn", f"可用空间 {usage.free / (1024**3):.2f} GB")
            else:
                add("磁盘空间", "ok", f"可用空间 {usage.free / (1024**3):.2f} GB")
            return checks

        @staticmethod
        def _health_database_integrity(database: Path) -> str:
            try:
                store = SQLiteStore(database)
                try:
                    return store.integrity_check()
                finally:
                    store.close()
            except (OSError, sqlite3.Error, ValueError):
                return "error"

        def _refresh_health(self) -> None:
            try:
                snapshot = self._health_snapshot()
                disk = snapshot["disk"]
                topology = snapshot["topology"]
                self.health_label.setText(
                    f"数据库：{snapshot['database_integrity']} · 待发送通知：{snapshot['pending_notifications']}\n"
                    f"磁盘：{disk['free_gb']} GB 可用 / {disk['total_gb']} GB · "
                    f"实体：{topology['sites']} 厂区、{topology['areas']} 区域、"
                    f"{topology['cameras']} 摄像头、{topology['streams']} 推流\n"
                    f"运行状态：{'监控中' if snapshot['monitoring'] else '待机'} · "
                    f"通知：{snapshot['notifications']['status']}\n"
                    + "预检："
                    + " · ".join(
                        f"{item['name']}={item['state']}" for item in snapshot.get("preflight", [])
                    )
                )
                state_labels = {"ok": "正常", "warn": "注意", "error": "异常"}
                self.preflight_table.setRowCount(0)
                for check in snapshot.get("preflight", []):
                    row = self.preflight_table.rowCount()
                    self.preflight_table.insertRow(row)
                    for column, value in enumerate(
                        (
                            str(check.get("name", "")),
                            state_labels.get(str(check.get("state", "")), str(check.get("state", ""))),
                            _redact_text(check.get("detail", "")),
                        )
                    ):
                        self.preflight_table.setItem(row, column, QTableWidgetItem(value))
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.health_label.setText(f"健康检查失败：{exc}")
                if hasattr(self, "preflight_table"):
                    self.preflight_table.setRowCount(0)

        def _export_diagnostics(self) -> None:
            try:
                snapshot = self._health_snapshot()
                path_text = self.db_edit.text().strip() or self._base_config.db
                root = Path(path_text).expanduser().resolve().parent if path_text != ":memory:" else Path.cwd()
                destination = root / "diagnostics" / f"factoryguard-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                self.statusBar().showMessage(f"诊断文件已导出：{destination}", 8000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"诊断导出失败：{exc}", 8000)

        def _switch_page(self, index: int | str) -> None:
            if not hasattr(self, "page_stack"):
                return
            if isinstance(index, str):
                index = self._page_routes.get(index, -1)
            if index < 0 or index >= self.page_stack.count():
                return
            self.page_stack.setCurrentIndex(index)
            self.page_stack.updateGeometry()
            if hasattr(self, "page_scroll"):
                self.page_scroll.verticalScrollBar().setValue(0)
                self.page_scroll.horizontalScrollBar().setValue(0)
            for page, button in self._nav_buttons.items():
                button.setChecked(self._page_routes.get(page) == index)
            titles = {
                0: ("总览", "FactoryGuard · 厂区视频安全系统"),
                1: ("监控中心", "实时视频 · CPU 推理 · 事件闭环"),
                2: ("拓扑与设备", "站点 · 区域 · 摄像头 · 多路推流"),
                3: ("事件审计", "搜索、筛选和事件媒体回放"),
                4: ("系统审计", "配置变更、健康状态和操作记录"),
                5: ("通知中心", "通道开关 · Outbox 队列 · 投递调度"),
                6: ("常规设置", "操作身份、运行策略与界面外观"),
                7: ("集中推理", "中心模型服务与低成本部署"),
                8: ("Webhook 通道", "HTTPS 回调地址与签名凭据"),
                9: ("邮件 SMTP", "发件账号、收件人与传输加密"),
                10: ("短信通知服务", "短信网关、签名、模板与接收号码"),
                11: ("存储与媒体", "数据库备份、配置包与媒体保留"),
                12: ("健康诊断", "部署预检、运行状态与诊断导出"),
            }
            title, subtitle = titles.get(index, titles[0])
            self.header_title.setText(title)
            self.header_subtitle.setText(subtitle)
            if index == 0:
                self._refresh_overview()
            elif index == 2:
                self._refresh_entity_tree()
            elif index == 3:
                self._refresh_events()
            elif index == 4:
                self._refresh_audit()
            elif index == 5:
                self._refresh_notification_queue()
            elif index == 12:
                self._refresh_health()

        def _refresh_overview(self) -> None:
            if not hasattr(self, "_overview_labels"):
                return
            topology = self._topology
            counts = {
                "sites": len(topology.sites) if topology is not None else 0,
                "areas": len(topology.areas) if topology is not None else 0,
                "cameras": len(topology.cameras) if topology is not None else 0,
                "streams": len(topology.streams) if topology is not None else 0,
                "events": len(self._event_records),
            }
            for key, value in counts.items():
                self._overview_labels[key].setText(str(value))
            if hasattr(self, "overview_stream_table"):
                self.overview_stream_table.setRowCount(0)
                if topology is not None:
                    for stream in topology.streams:
                        path = topology.stream_path(stream.id)
                        state, _detail = self._stream_states.get(stream.id, ("", ""))
                        health = self._stream_health_history.get(stream.id, {})
                        row = self.overview_stream_table.rowCount()
                        self.overview_stream_table.insertRow(row)
                        values = (
                            stream.name,
                            path.display_name,
                            _STREAM_STATUS_LABELS.get(state, "未启动"),
                            f"{self._stream_metrics.get(stream.id, {}).get('frames', 0)} / "
                            f"{self._stream_metrics.get(stream.id, {}).get('detections', 0)}",
                            str(health.get("last_ok_at") or "—"),
                            str(health.get("reconnect_count", 0)),
                        )
                        for column, value in enumerate(values):
                            item = QTableWidgetItem(value)
                            item.setData(Qt.UserRole, stream.id)
                            self.overview_stream_table.setItem(row, column, item)
            running = self._thread is not None and self._thread.isRunning()
            if running and self._stream_states:
                healthy = sum(state in {"ready", "running"} for state, _detail in self._stream_states.values())
                degraded = sum(state in {"degraded", "offline"} for state, _detail in self._stream_states.values())
                self.overview_status.setText(
                    f"监控服务运行中 · 推流 {healthy}/{len(self._stream_states)} 正常"
                    + (f" · {degraded} 路异常" if degraded else "")
                )
            else:
                self.overview_status.setText("监控服务运行中" if running else "监控服务待机 · 本地数据库已就绪")

            if hasattr(self, "overview_event_summary"):
                now = datetime.now(UTC)
                recent_cutoff = now - timedelta(hours=24)
                recent = [
                    record for record in self._event_records if _event_after(record.get("occurred_at"), recent_cutoff)
                ]
                open_count = sum(str(record.get("status", "open")) == "open" for record in self._event_records)
                high_count = sum(str(record.get("level", "")) == "high" for record in recent)
                confirmed_count = sum(
                    str(record.get("status", "")) in {"confirmed", "closed"} for record in recent
                )
                self.overview_event_summary.setText(
                    f"最近 24 小时：{len(recent)} 条事件\n"
                    f"当前待处理：{open_count} 条\n"
                    f"高危事件：{high_count} 条\n"
                    f"已确认或关闭：{confirmed_count} 条\n"
                    f"统计样本：已加载 {len(self._event_records)} 条记录"
                )
                grouped: dict[str, dict[str, int]] = {}
                for record in recent:
                    stream_id = str(record.get("stream_id") or record.get("channel_id") or "未标识推流")
                    bucket = grouped.setdefault(stream_id, {"total": 0, "open": 0, "high": 0})
                    bucket["total"] += 1
                    bucket["open"] += int(str(record.get("status", "open")) == "open")
                    bucket["high"] += int(str(record.get("level", "")) == "high")
                self.overview_event_table.setRowCount(0)
                for stream_id, bucket in sorted(
                    grouped.items(), key=lambda item: (-item[1]["total"], item[0])
                )[:8]:
                    row = self.overview_event_table.rowCount()
                    self.overview_event_table.insertRow(row)
                    label = stream_id
                    if topology is not None:
                        try:
                            label = topology.stream_path(stream_id).display_name
                        except (KeyError, ValueError):
                            pass
                    for column, value in enumerate(
                        (label, str(bucket["total"]), str(bucket["open"]), str(bucket["high"]))
                    ):
                        item = QTableWidgetItem(value)
                        item.setData(Qt.UserRole, stream_id)
                        self.overview_event_table.setItem(row, column, item)

        def _refresh_runtime_surfaces(self) -> None:
            """Keep operational surfaces current while the window remains open."""
            current_page = self.page_stack.currentIndex() if hasattr(self, "page_stack") else -1
            if current_page == 0:
                self._refresh_overview()
            elif current_page == 4:
                self._refresh_audit()
            elif current_page == 5:
                self._refresh_notification_queue()
            elif current_page == 12:
                self._refresh_health()

        def _select_overview_stream(self, row: int) -> None:
            if not hasattr(self, "overview_stream_table") or self._topology is None:
                return
            item = self.overview_stream_table.item(row, 0)
            if item is None:
                return
            stream_id = item.data(Qt.UserRole)
            if not stream_id:
                return
            self._switch_page(2)
            self._select_stream(str(stream_id))

        def _select_overview_event_stream(self, row: int) -> None:
            if not hasattr(self, "overview_event_table"):
                return
            item = self.overview_event_table.item(row, 0)
            if item is None:
                return
            stream_id = item.data(Qt.UserRole)
            if not stream_id:
                return
            self._switch_page(3)
            index = self.channel_filter.findData(str(stream_id))
            if index >= 0:
                self.channel_filter.setCurrentIndex(index)

        def _refresh_stream_table(self) -> None:
            if not hasattr(self, "stream_table"):
                return
            selected_stream_id = self._selected_stream_id
            self._refresh_arming_states()
            self.stream_table.setRowCount(0)
            if self._topology is None:
                return
            needle = self.topology_search_edit.text().strip().lower() if hasattr(self, "topology_search_edit") else ""
            for stream in self._topology.streams:
                path = self._topology.stream_path(stream.id)
                row = self.stream_table.rowCount()
                self.stream_table.insertRow(row)
                values = (
                    path.site.name,
                    path.area.name,
                    path.camera.name,
                    stream.name,
                    str(stream.role),
                    stream.url,
                    (
                        "停用"
                        if not stream.enabled
                        else (
                            "上级停用"
                            if not path.site.enabled or not path.camera.enabled or not all(area.enabled for area in path.areas)
                            else _STREAM_STATUS_LABELS.get(self._stream_states.get(stream.id, ("", ""))[0], "启用")
                        )
                    ),
                    "布防" if self._arming_states.get(stream.id, True) else "撤防",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, stream.id)
                    self.stream_table.setItem(row, column, item)
                row_text = " ".join(str(value) for value in values).lower()
                self.stream_table.setRowHidden(row, bool(needle) and needle not in row_text)
            if selected_stream_id:
                for row in range(self.stream_table.rowCount()):
                    item = self.stream_table.item(row, 0)
                    if item is not None and item.data(Qt.UserRole) == selected_stream_id:
                        self.stream_table.selectRow(row)
                        break

        def _apply_topology_filter(self) -> None:
            """Filter both the hierarchy tree and stream table by one keyword."""
            if not hasattr(self, "topology_search_edit"):
                return
            needle = self.topology_search_edit.text().strip().lower()
            for index in range(self.entity_tree.topLevelItemCount()):
                self._filter_tree_item(self.entity_tree.topLevelItem(index), needle)
            if hasattr(self, "stream_table"):
                for row in range(self.stream_table.rowCount()):
                    values = [
                        self.stream_table.item(row, column).text()
                        for column in range(self.stream_table.columnCount())
                        if self.stream_table.item(row, column) is not None
                    ]
                    row_text = " ".join(values).lower()
                    self.stream_table.setRowHidden(row, bool(needle) and needle not in row_text)

        def _filter_tree_item(self, item: QTreeWidgetItem, needle: str) -> bool:
            own_text = item.text(0).lower()
            child_match = False
            for index in range(item.childCount()):
                if self._filter_tree_item(item.child(index), needle):
                    child_match = True
            matched = not needle or needle in own_text or child_match
            item.setHidden(not matched)
            if child_match and needle:
                item.setExpanded(True)
            return matched

        def _entity_enabled(self, kind: str, entity_id: str) -> bool:
            if self._topology is None:
                return False
            if kind == "site":
                return self._topology.get_site(entity_id).enabled
            if kind == "area":
                return self._topology.get_area(entity_id).enabled
            if kind == "camera":
                return self._topology.get_camera(entity_id).enabled
            if kind == "stream":
                return self._topology.get_stream(entity_id).enabled
            return False

        def _update_entity_toggle_button(self) -> None:
            if not hasattr(self, "toggle_entity_button"):
                return
            selected = self._selected_entity()
            if selected is None and self._selected_stream_id:
                selected = ("stream", self._selected_stream_id)
            if selected is None:
                self.toggle_entity_button.setEnabled(False)
                self._update_entity_edit_button()
                return
            try:
                enabled = self._entity_enabled(*selected)
            except (KeyError, ValueError):
                self.toggle_entity_button.setEnabled(False)
                self._update_entity_edit_button()
                return
            self.toggle_entity_button.setEnabled(True)
            self.toggle_entity_button.setText("停用选中实体" if enabled else "启用选中实体")
            self._update_entity_edit_button()

        def _update_entity_edit_button(self) -> None:
            if not hasattr(self, "edit_entity_button"):
                return
            selected = self._selected_entity()
            if selected is None:
                self.edit_entity_button.setEnabled(False)
                return
            kind, _entity_id = selected
            self.edit_entity_button.setEnabled(kind in {"site", "area", "camera"})

        def _toggle_selected_entity(self) -> None:
            if not self._require_permission(Permission.MANAGE_TOPOLOGY, "启停实体"):
                return
            selected = self._selected_entity()
            if selected is None and self._selected_stream_id:
                selected = ("stream", self._selected_stream_id)
            if selected is None:
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库无法持久化实体状态", 5000)
                return
            kind, entity_id = selected
            try:
                enabled = self._entity_enabled(kind, entity_id)
                store = SQLiteStore(path)
                try:
                    if not store.set_entity_enabled(kind, entity_id, not enabled, actor=self._current_actor()):
                        return
                    self._topology = store.load_topology()
                finally:
                    store.close()
                self._selected_stream_id = entity_id if kind == "stream" else None
                self._refresh_entity_tree()
                self.statusBar().showMessage(f"实体已{'启用' if not enabled else '停用'}：{entity_id}", 5000)
            except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
                self.statusBar().showMessage(f"实体状态更新失败：{exc}", 5000)

        def _edit_selected_entity(self) -> None:
            """Edit the display metadata of a site, area, or camera."""
            if not self._require_permission(Permission.MANAGE_TOPOLOGY, "编辑实体"):
                return
            selected = self._selected_entity()
            topology = self._topology
            if selected is None or topology is None:
                self.statusBar().showMessage("请先在实体树中选择厂区、区域或摄像头", 5000)
                return
            kind, entity_id = selected
            if kind == "stream":
                self.statusBar().showMessage("推流请使用右侧参数并点击“保存推流配置”", 5000)
                return
            try:
                if kind == "site":
                    current = topology.get_site(entity_id)
                    name = self._prompt_name("编辑厂区", "厂区名称")
                    if not name:
                        return
                    updated: Site | Area | CameraDevice = Site(
                        current.id,
                        name,
                        current.description,
                        current.enabled,
                    )
                elif kind == "area":
                    current = topology.get_area(entity_id)
                    name = self._prompt_name("编辑区域", "区域名称")
                    if not name:
                        return
                    updated = Area(
                        current.id,
                        current.site_id,
                        name,
                        parent_id=current.parent_id,
                        geometry=current.geometry,
                        enabled=current.enabled,
                    )
                elif kind == "camera":
                    current = topology.get_camera(entity_id)
                    name = self._prompt_name("编辑摄像头", "摄像头名称")
                    if not name:
                        return
                    address = self._prompt_name("编辑摄像头地址", "设备地址（可选）")
                    if address is None:
                        return
                    updated = CameraDevice(
                        current.id,
                        current.area_id,
                        name,
                        vendor=current.vendor,
                        address=address,
                        enabled=current.enabled,
                    )
                else:
                    self.statusBar().showMessage("当前实体类型不支持编辑", 5000)
                    return

                path = self.db_edit.text().strip() or self._base_config.db
                if path == ":memory:":
                    self._topology = self._replace_topology_entity(updated)
                else:
                    store = SQLiteStore(path)
                    try:
                        if isinstance(updated, Site):
                            store.save_site(updated, actor=self._current_actor())
                        elif isinstance(updated, Area):
                            store.save_area(updated, actor=self._current_actor())
                        else:
                            store.save_camera(updated, actor=self._current_actor())
                        self._topology = store.load_topology()
                    finally:
                        store.close()
                self._selected_stream_id = None
                self._refresh_entity_tree()
                self.statusBar().showMessage(f"实体已更新：{name}", 6000)
            except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                self._show_topology_error(f"实体编辑失败：{exc}")

        def _replace_topology_entity(self, updated: Site | Area | CameraDevice) -> EntityRegistry:
            """Return a validated registry with one non-stream entity replaced."""
            topology = self._topology
            if topology is None:
                raise ValueError("topology is not loaded")
            sites = tuple(updated if isinstance(updated, Site) and item.id == updated.id else item for item in topology.sites)
            areas = tuple(updated if isinstance(updated, Area) and item.id == updated.id else item for item in topology.areas)
            cameras = tuple(
                updated if isinstance(updated, CameraDevice) and item.id == updated.id else item
                for item in topology.cameras
            )
            return EntityRegistry(sites=sites, areas=areas, cameras=cameras, streams=topology.streams)

        def _refresh_arming_states(self) -> None:
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:" or not Path(path).expanduser().exists():
                return
            store = SQLiteStore(path)
            try:
                self._arming_states.update(store.list_stream_arming())
            finally:
                store.close()

        def _update_arming_buttons(self) -> None:
            selected = self._selected_stream_id
            self._update_stream_edit_button()
            has_streams = self._topology is not None and bool(self._topology.streams)
            if hasattr(self, "arm_all_button"):
                self.arm_all_button.setEnabled(has_streams)
                self.disarm_all_button.setEnabled(has_streams)
            if not selected:
                self.arm_button.setEnabled(False)
                self.disarm_button.setEnabled(False)
                self.schedule_button.setEnabled(False)
                self.probe_stream_button.setEnabled(False)
                return
            armed = self._arming_states.get(selected, True)
            self.arm_button.setEnabled(not armed)
            self.disarm_button.setEnabled(armed)
            self.schedule_button.setEnabled(True)
            self.probe_stream_button.setEnabled(self._probe_thread is None or not self._probe_thread.isRunning())

        def _update_stream_edit_button(self) -> None:
            """Enable stream persistence only when a real stream is selected."""
            if not hasattr(self, "save_stream_button"):
                return
            selected = self._selected_stream_id
            if not selected or self._topology is None:
                self.save_stream_button.setEnabled(False)
                return
            try:
                self._topology.get_stream(selected)
            except (KeyError, ValueError):
                self.save_stream_button.setEnabled(False)
                return
            self.save_stream_button.setEnabled(True)

        def _save_selected_stream_config(self) -> None:
            if not self._require_permission(Permission.MANAGE_TOPOLOGY, "推流配置"):
                return
            """Persist the editable stream fields for the selected topology row."""
            stream_id = self._selected_stream_id
            topology = self._topology
            if not stream_id or topology is None:
                self.statusBar().showMessage("请先选择一个推流", 5000)
                self._update_stream_edit_button()
                return
            try:
                existing = topology.get_stream(stream_id)
            except (KeyError, ValueError) as exc:
                self.statusBar().showMessage(f"无法读取选中推流：{exc}", 5000)
                self._update_stream_edit_button()
                return

            url = self.url_edit.text().strip()
            model_text = self.model_edit.text().strip()
            if not url:
                self.statusBar().showMessage("RTSP 地址不能为空", 5000)
                return
            if not model_text:
                self.statusBar().showMessage("ONNX 模型不能为空", 5000)
                return

            try:
                updated = VideoStream(
                    id=existing.id,
                    camera_id=existing.camera_id,
                    name=existing.name,
                    url=url,
                    role=existing.role,
                    width=self.width_spin.value(),
                    height=self.height_spin.value(),
                    fps=existing.fps,
                    model=model_text,
                    confidence=self.confidence_spin.value(),
                    enabled=existing.enabled,
                )
                path = self.db_edit.text().strip() or self._base_config.db
                if path == ":memory:":
                    # A new SQLiteStore(":memory:") would be a different
                    # database, so keep the session-local topology coherent.
                    self._topology = EntityRegistry(
                        sites=topology.sites,
                        areas=topology.areas,
                        cameras=topology.cameras,
                        streams=(updated if stream.id == stream_id else stream for stream in topology.streams),
                    )
                    self._refresh_entity_tree()
                    self.statusBar().showMessage("推流配置已更新（仅当前内存会话）", 6000)
                    return

                store = SQLiteStore(path)
                try:
                    store.save_stream(updated, actor=self._current_actor())
                    self._topology = store.load_topology()
                finally:
                    store.close()
                self._selected_stream_id = stream_id
                self._refresh_entity_tree()
                running = self._thread is not None and self._thread.isRunning()
                suffix = "；重启监控后生效" if running else ""
                self.statusBar().showMessage(f"推流配置已保存：{existing.name}{suffix}", 8000)
            except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                self.statusBar().showMessage(f"推流配置保存失败：{exc}", 8000)

        def _probe_selected_stream(self) -> None:
            stream_id = self._selected_stream_id
            if not stream_id or self._topology is None:
                return
            if self._probe_thread is not None and self._probe_thread.isRunning():
                return
            try:
                stream = self._topology.get_stream(stream_id)
                self._probe_stream_id = stream_id
                self._probe_thread = QThread(self)
                self._probe_worker = StreamProbeWorker(
                    url=stream.url,
                    width=stream.width,
                    height=stream.height,
                    fps=min(stream.fps, 2.0),
                    timeout=10.0,
                )
                self._probe_worker.moveToThread(self._probe_thread)
                self._probe_thread.started.connect(self._probe_worker.run)
                self._probe_worker.status_changed.connect(self._on_probe_status)
                self._probe_worker.finished.connect(self._on_probe_finished)
                self._probe_worker.finished.connect(self._probe_thread.quit)
                self._probe_worker.finished.connect(self._probe_worker.deleteLater)
                self._probe_thread.finished.connect(self._on_probe_thread_finished)
                self._probe_thread.finished.connect(self._probe_thread.deleteLater)
                self.probe_stream_button.setEnabled(False)
                self.channel_status_label.setText(f"{stream_id} · 正在测试连接")
                self._probe_thread.start()
            except (KeyError, OSError, RuntimeError, ValueError) as exc:
                self._probe_stream_id = None
                self._probe_worker = None
                if self._probe_thread is not None:
                    self._probe_thread.deleteLater()
                    self._probe_thread = None
                self.statusBar().showMessage(f"推流测试启动失败：{exc}", 8000)

        def _on_probe_status(self, detail: str) -> None:
            if self._probe_stream_id:
                self.channel_status_label.setText(f"{self._probe_stream_id} · {detail}")

        def _on_probe_finished(self, success: bool, detail: str) -> None:
            stream_id = self._probe_stream_id or "推流"
            self.statusBar().showMessage(f"{stream_id}：{detail}", 8000)
            if self._selected_stream_id == self._probe_stream_id:
                self.channel_status_label.setText(f"{stream_id} · {detail}")
            self._stream_states[stream_id] = ("ready" if success else "degraded", detail)
            self._refresh_stream_table()

        def _on_probe_thread_finished(self) -> None:
            self._probe_worker = None
            self._probe_thread = None
            self._probe_stream_id = None
            self._update_arming_buttons()

        def _set_stream_armed(self, armed: bool) -> None:
            if not self._require_permission(Permission.MANAGE_ARMING, "布防状态"):
                return
            stream_id = self._selected_stream_id
            if not stream_id:
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库无法持久化布防状态", 5000)
                return
            try:
                store = SQLiteStore(path)
                try:
                    store.set_stream_armed(stream_id, armed, actor=self._current_actor())
                finally:
                    store.close()
                self._arming_states[stream_id] = armed
                self._refresh_stream_table()
                self.statusBar().showMessage(f"{stream_id} 已{'布防' if armed else '撤防'}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"布防状态更新失败：{exc}", 5000)

        def _set_all_streams_armed(self, armed: bool, *, confirm: bool = True) -> None:
            if not self._require_permission(Permission.MANAGE_ARMING, "批量布防"):
                return
            """Apply one arming state to every stream in the loaded topology."""
            topology = self._topology
            if topology is None or not topology.streams:
                self.statusBar().showMessage("当前没有可操作的推流", 5000)
                return
            action = "全部布防" if armed else "全部撤防"
            if confirm:
                answer = QMessageBox.question(
                    self,
                    action,
                    f"确定要对 {len(topology.streams)} 路推流执行“{action}”吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self._arming_states.update({stream.id: armed for stream in topology.streams})
                self._refresh_stream_table()
                self._update_arming_buttons()
                self.statusBar().showMessage(f"已{action}（仅当前内存会话）", 6000)
                return
            try:
                store = SQLiteStore(path)
                try:
                    for stream in topology.streams:
                        store.set_stream_armed(stream.id, armed, actor=self._current_actor())
                    self._arming_states.update(store.list_stream_arming())
                finally:
                    store.close()
                self._refresh_stream_table()
                self._update_arming_buttons()
                self.statusBar().showMessage(f"已{action}：{len(topology.streams)} 路", 6000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"批量布防操作失败：{exc}", 6000)

        def _edit_stream_schedule(self) -> None:
            if not self._require_permission(Permission.MANAGE_ARMING, "布防时间表"):
                return
            stream_id = self._selected_stream_id
            if not stream_id:
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库无法持久化时间表", 5000)
                return
            try:
                store = SQLiteStore(path)
                try:
                    schedule = store.get_arming_schedule(stream_id)
                finally:
                    store.close()
                dialog = ScheduleEditorDialog(stream_id=stream_id, schedule=schedule, parent=self)
                if dialog.exec() != dialog.Accepted or dialog.schedule is None:
                    return
                store = SQLiteStore(path)
                try:
                    store.save_arming_schedule(dialog.schedule, actor=self._current_actor())
                finally:
                    store.close()
                self.statusBar().showMessage(f"已保存 {stream_id} 的布防时间表", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"时间表保存失败：{exc}", 5000)

        def _refresh_rule_table(self) -> None:
            if not hasattr(self, "rule_table"):
                return
            self.rule_table.setRowCount(0)
            self.toggle_rule_button.setEnabled(False)
            self.edit_rule_button.setEnabled(False)
            stream_id = self._selected_stream_id
            if not stream_id:
                self.add_rule_button.setEnabled(False)
                return
            self.add_rule_button.setEnabled(True)
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                return
            store = SQLiteStore(path)
            try:
                rules = store.load_rules(stream_id=stream_id)
            except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
                self.statusBar().showMessage(f"无法读取规则：{exc}", 5000)
                return
            finally:
                store.close()
            type_labels = {RuleType.POLYGON.value: "多边形", RuleType.LINE.value: "绊线"}
            for rule in rules:
                row = self.rule_table.rowCount()
                self.rule_table.insertRow(row)
                values = (
                    rule.name,
                    rule.stream_id,
                    type_labels.get(rule.type.value, rule.type.value),
                    str(rule.confirm_frames),
                    "启用" if rule.enabled else "停用",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, rule.id)
                    self.rule_table.setItem(row, column, item)

        def _selected_rule_id(self) -> str | None:
            selected = self.rule_table.selectedItems()
            if not selected:
                return None
            value = selected[0].data(Qt.UserRole)
            return str(value) if value else None

        def _on_rule_selected(self) -> None:
            selected = self._selected_rule_id() is not None
            self.toggle_rule_button.setEnabled(selected)
            self.edit_rule_button.setEnabled(selected)

        def _add_default_rule(self) -> None:
            if not self._require_permission(Permission.MANAGE_RULES, "新增规则"):
                return
            stream_id = self._selected_stream_id
            topology = self._topology
            if not stream_id or topology is None:
                self.statusBar().showMessage("请先选择一个推流", 5000)
                return
            try:
                path = topology.stream_path(stream_id)
            except (KeyError, ValueError) as exc:
                self.statusBar().showMessage(f"无法解析推流实体：{exc}", 5000)
                return
            dialog = RuleEditorDialog(path=path, parent=self)
            if dialog.exec() != dialog.Accepted or dialog.rule is None:
                return
            rule = Rule(
                id=f"rule-{uuid4().hex[:10]}",
                channel_id=dialog.rule.channel_id,
                name=dialog.rule.name,
                type=dialog.rule.type,
                geometry=dialog.rule.geometry,
                target_labels=dialog.rule.target_labels,
                min_confidence=dialog.rule.min_confidence,
                min_box_height=dialog.rule.min_box_height,
                confirm_frames=dialog.rule.confirm_frames,
                cooldown_seconds=dialog.rule.cooldown_seconds,
                direction=dialog.rule.direction,
                enabled=dialog.rule.enabled,
                version=dialog.rule.version,
                stream_id=path.stream.id,
                site_id=path.site.id,
                area_id=path.area.id,
                camera_id=path.camera.id,
            )
            path_text = self.db_edit.text().strip() or self._base_config.db
            if path_text == ":memory:":
                self.statusBar().showMessage("内存数据库无法持久化规则", 5000)
                return
            try:
                store = SQLiteStore(path_text)
                try:
                    store.save_rule(rule, actor=self._current_actor())
                finally:
                    store.close()
                self._refresh_rule_table()
                self.statusBar().showMessage(f"已保存规则：{name}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"规则保存失败：{exc}", 5000)

        def _edit_selected_rule(self) -> None:
            if not self._require_permission(Permission.MANAGE_RULES, "编辑规则"):
                return
            rule_id = self._selected_rule_id()
            topology = self._topology
            if not rule_id or topology is None:
                return
            path_text = self.db_edit.text().strip() or self._base_config.db
            if path_text == ":memory:":
                return
            try:
                store = SQLiteStore(path_text)
                try:
                    rule = next((item for item in store.load_rules() if item.id == rule_id), None)
                finally:
                    store.close()
                if rule is None:
                    return
                path = topology.stream_path(rule.stream_id)
                dialog = RuleEditorDialog(path=path, rule=rule, parent=self)
                if dialog.exec() != dialog.Accepted or dialog.rule is None:
                    return
                store = SQLiteStore(path_text)
                try:
                    store.save_rule(dialog.rule, actor=self._current_actor())
                finally:
                    store.close()
                self._refresh_rule_table()
                self.statusBar().showMessage(f"规则已更新：{dialog.rule.name}", 5000)
            except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
                self.statusBar().showMessage(f"规则更新失败：{exc}", 5000)

        def _toggle_selected_rule(self) -> None:
            if not self._require_permission(Permission.MANAGE_RULES, "启停规则"):
                return
            rule_id = self._selected_rule_id()
            if not rule_id:
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                return
            try:
                store = SQLiteStore(path)
                try:
                    rules = store.load_rules()
                    rule = next((item for item in rules if item.id == rule_id), None)
                    if rule is None:
                        return
                    if not store.set_rule_enabled(rule.id, not rule.enabled, actor=self._current_actor()):
                        return
                finally:
                    store.close()
                self._refresh_rule_table()
                self.statusBar().showMessage(f"规则已{'启用' if not rule.enabled else '停用'}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"规则状态更新失败：{exc}", 5000)

        def _clear_preview_grid(self) -> None:
            if self._preview_grid_layout is None:
                return
            while self._preview_grid_layout.count():
                item = self._preview_grid_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            self._preview_grid_widgets.clear()
            self._preview_grid_cells.clear()

        @staticmethod
        def _preview_grid_columns(count: int) -> int:
            if count <= 1:
                return 1
            if count <= 4:
                return 2
            if count <= 9:
                return 3
            return 4

        def _refresh_preview_grid(self) -> None:
            """Rebuild the multiview surface from enabled topology streams."""
            self._clear_preview_grid()
            if self._preview_grid_layout is None:
                return
            streams = tuple(
                stream for stream in (self._topology.streams if self._topology else ()) if stream.enabled
            )
            if not streams:
                empty = QLabel("暂无启用推流 · 请先在“拓扑与设备”页登记视频流")
                empty.setObjectName("muted")
                self._preview_grid_layout.addWidget(empty, 0, 0)
                return
            columns = self._preview_grid_columns(len(streams))
            for index, stream in enumerate(streams):
                path = self._topology.stream_path(stream.id) if self._topology else None
                title = f"{path.camera.name} · {stream.name}" if path else stream.name
                cell = QGroupBox(title)
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(4, 4, 4, 4)
                preview = PreviewWidget(cell)
                preview.setMinimumSize(180, 110)
                cell_layout.addWidget(preview)
                self._preview_grid_cells[stream.id] = cell
                self._preview_grid_widgets[stream.id] = preview
                self._preview_grid_layout.addWidget(cell, index // columns, index % columns)

        def _ensure_preview_widget(self, stream_id: str) -> PreviewWidget | None:
            preview = self._preview_grid_widgets.get(stream_id)
            if preview is not None:
                return preview
            if self._preview_grid_layout is None:
                return None
            # A legacy single-stream configuration has no topology row yet;
            # create a live cell when its first frame arrives.
            for index in range(self._preview_grid_layout.count() - 1, -1, -1):
                item = self._preview_grid_layout.itemAt(index)
                widget = item.widget()
                if isinstance(widget, QLabel) and widget.text().startswith("暂无启用推流"):
                    self._preview_grid_layout.takeAt(index)
                    widget.setParent(None)
                    widget.deleteLater()
            cell = QGroupBox(stream_id)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(4, 4, 4, 4)
            preview = PreviewWidget(cell)
            preview.setMinimumSize(180, 110)
            cell_layout.addWidget(preview)
            self._preview_grid_cells[stream_id] = cell
            self._preview_grid_widgets[stream_id] = preview
            count = len(self._preview_grid_widgets)
            columns = self._preview_grid_columns(count)
            self._preview_grid_layout.addWidget(cell, (count - 1) // columns, (count - 1) % columns)
            return preview

        def _selected_entity(self) -> tuple[str, str] | None:
            selected = self.entity_tree.selectedItems()
            if not selected:
                return None
            metadata = selected[0].data(0, Qt.UserRole) or {}
            kind = str(metadata.get("kind", ""))
            entity_id = str(metadata.get("id", ""))
            return (kind, entity_id) if kind and entity_id else None

        def _selection_context(self) -> tuple[str | None, str | None, str | None]:
            """Return the site, area and camera IDs implied by the tree selection."""
            if self._topology is None:
                return None, None, None
            selected = self._selected_entity()
            if selected is None:
                return None, None, None
            kind, entity_id = selected
            try:
                if kind == "site":
                    self._topology.get_site(entity_id)
                    return entity_id, None, None
                if kind == "area":
                    area = self._topology.get_area(entity_id)
                    return area.site_id, area.id, None
                if kind == "camera":
                    camera = self._topology.get_camera(entity_id)
                    area = self._topology.get_area(camera.area_id)
                    return area.site_id, area.id, camera.id
                if kind == "stream":
                    path = self._topology.stream_path(entity_id)
                    return path.site.id, path.area.id, path.camera.id
            except (KeyError, ValueError):
                return None, None, None
            return None, None, None

        def _persist_topology_entity(self, entity: Site | Area | CameraDevice | VideoStream) -> None:
            """Write one newly-created topology entity to SQLite and memory."""
            if not self._require_permission(Permission.MANAGE_TOPOLOGY, "修改拓扑"):
                raise PermissionError("当前角色没有修改拓扑权限")
            path = self.db_edit.text().strip() or self._base_config.db
            if path != ":memory:":
                store = SQLiteStore(path)
                try:
                    if isinstance(entity, Site):
                        store.save_site(entity, actor=self._current_actor())
                    elif isinstance(entity, Area):
                        store.save_area(entity, actor=self._current_actor())
                    elif isinstance(entity, CameraDevice):
                        store.save_camera(entity, actor=self._current_actor())
                    else:
                        store.save_stream(entity, actor=self._current_actor())
                finally:
                    store.close()

            if self._topology is None:
                if not isinstance(entity, Site):
                    raise ValueError("请先创建厂区")
                self._topology = EntityRegistry(sites=(entity,))
            elif isinstance(entity, Site):
                self._topology.add_site(entity)
            elif isinstance(entity, Area):
                self._topology.add_area(entity)
            elif isinstance(entity, CameraDevice):
                self._topology.add_camera(entity)
            else:
                self._topology.add_stream(entity)
            self._refresh_entity_tree()
            self._refresh_overview()

        def _prompt_name(self, title: str, label: str) -> str | None:
            value, accepted = QInputDialog.getText(self, title, label)
            value = value.strip()
            return value if accepted and value else None

        def _show_topology_error(self, detail: str) -> None:
            QMessageBox.warning(self, "拓扑操作失败", detail)
            self.statusBar().showMessage(detail, 5000)

        def _add_site(self) -> None:
            name = self._prompt_name("新增厂区", "厂区名称")
            if not name:
                return
            try:
                self._persist_topology_entity(Site(f"site-{uuid4().hex[:10]}", name))
                self.statusBar().showMessage(f"已新增厂区：{name}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self._show_topology_error(str(exc))

        def _add_area(self) -> None:
            site_id, parent_id, _camera_id = self._selection_context()
            if not site_id:
                self._show_topology_error("请先在实体树中选择一个厂区或区域")
                return
            name = self._prompt_name("新增区域", "区域名称")
            if not name:
                return
            area = Area(f"area-{uuid4().hex[:10]}", site_id, name, parent_id=parent_id)
            try:
                self._persist_topology_entity(area)
                self.statusBar().showMessage(f"已新增区域：{name}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self._show_topology_error(str(exc))

        def _add_camera(self) -> None:
            site_id, area_id, _camera_id = self._selection_context()
            if not site_id or not area_id:
                self._show_topology_error("请先在实体树中选择一个区域")
                return
            name = self._prompt_name("新增摄像头", "摄像头名称")
            if not name:
                return
            address = self._prompt_name("摄像头地址", "设备地址（可选）") or ""
            camera = CameraDevice(f"camera-{uuid4().hex[:10]}", area_id, name, address=address)
            try:
                self._persist_topology_entity(camera)
                self.statusBar().showMessage(f"已新增摄像头：{name}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self._show_topology_error(str(exc))

        def _add_stream(self) -> None:
            _site_id, _area_id, camera_id = self._selection_context()
            if not camera_id:
                self._show_topology_error("请先在实体树中选择一个摄像头")
                return
            name = self._prompt_name("新增推流", "推流名称")
            if not name:
                return
            url = self._prompt_name("推流地址", "RTSP 地址")
            if not url:
                return
            role_text, accepted = QInputDialog.getItem(
                self,
                "推流角色",
                "角色",
                [str(role) for role in StreamRole],
                0,
                False,
            )
            if not accepted:
                return
            stream = VideoStream(
                f"stream-{uuid4().hex[:10]}",
                camera_id,
                name,
                url,
                role=StreamRole(str(role_text)),
            )
            try:
                self._persist_topology_entity(stream)
                self.statusBar().showMessage(f"已新增推流：{name}", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self._show_topology_error(str(exc))

        def _add_bulk_streams(self) -> None:
            """Create several streams for one camera from a compact NVR list."""
            _site_id, _area_id, camera_id = self._selection_context()
            if not camera_id:
                self._show_topology_error("请先在实体树中选择一个摄像头")
                return
            text, accepted = QInputDialog.getMultiLineText(
                self,
                "批量新增推流",
                "每行格式：推流名称 | RTSP 地址 | 角色（main / sub / analysis）",
                "主码流 | rtsp://camera/main | main\n子码流 | rtsp://camera/sub | sub",
            )
            if not accepted or not text.strip():
                return
            streams: list[VideoStream] = []
            errors: list[str] = []
            for line_number, raw_line in enumerate(text.splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue
                parts = [part.strip() for part in line.split("|")]
                if len(parts) not in {2, 3} or not parts[0] or not parts[1]:
                    errors.append(f"第 {line_number} 行格式错误")
                    continue
                role_text = parts[2] if len(parts) == 3 and parts[2] else "main"
                try:
                    streams.append(
                        VideoStream(
                            f"stream-{uuid4().hex[:10]}",
                            camera_id,
                            parts[0],
                            parts[1],
                            role=StreamRole(role_text),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"第 {line_number} 行：{exc}")
            if not streams:
                self._show_topology_error("没有可保存的推流\n" + "\n".join(errors))
                return
            saved = 0
            for stream in streams:
                try:
                    self._persist_topology_entity(stream)
                    saved += 1
                except (OSError, sqlite3.Error, ValueError) as exc:
                    errors.append(f"{stream.name}：{exc}")
            detail = f"已批量新增 {saved} 路推流"
            if errors:
                detail += "；部分行未保存：" + "；".join(errors)
            self.statusBar().showMessage(detail, 8000)

        def _export_topology_csv(self) -> None:
            if self._topology is None or not self._topology.sites:
                self.statusBar().showMessage("当前没有可导出的厂区拓扑", 5000)
                return
            path = self.db_edit.text().strip() or self._base_config.db
            root = Path(path).expanduser().resolve().parent if path != ":memory:" else Path.cwd()
            destination, _ = QFileDialog.getSaveFileName(
                self,
                "导出设备拓扑 CSV",
                str(root / "topology.csv"),
                "CSV 文件 (*.csv);;所有文件 (*.*)",
            )
            if not destination:
                return
            try:
                Path(destination).expanduser().resolve().write_text(
                    topology_to_csv(self._topology),
                    encoding="utf-8-sig",
                    newline="",
                )
                self.statusBar().showMessage(f"拓扑 CSV 已导出：{destination}", 8000)
            except (OSError, ValueError) as exc:
                self._show_topology_error(f"拓扑 CSV 导出失败：{exc}")

        def _import_topology_csv(self) -> None:
            if not self._require_permission(Permission.MANAGE_TOPOLOGY, "导入拓扑"):
                return
            if self._workers_running():
                self.statusBar().showMessage("请先停止监控，再导入拓扑 CSV", 6000)
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库不支持拓扑导入", 5000)
                return
            source, _ = QFileDialog.getOpenFileName(
                self,
                "导入设备拓扑 CSV",
                str(Path(path).expanduser().resolve().parent),
                "CSV 文件 (*.csv);;所有文件 (*.*)",
            )
            if not source:
                return
            try:
                topology = topology_from_csv(Path(source).read_text(encoding="utf-8-sig"))
                store = SQLiteStore(path)
                try:
                    store.save_topology(topology)
                    self._topology = store.load_topology()
                finally:
                    store.close()
                self._selected_stream_id = None
                self._refresh_entity_tree()
                self.statusBar().showMessage(
                    f"拓扑 CSV 导入完成：{len(topology.sites)} 个厂区、{len(topology.cameras)} 个摄像头、"
                    f"{len(topology.streams)} 路推流",
                    10000,
                )
            except (OSError, UnicodeError, sqlite3.Error, TypeError, ValueError) as exc:
                self._show_topology_error(f"拓扑 CSV 导入失败：{exc}")

        def _select_stream(self, stream_id: str) -> None:
            """Apply a stream selection to the monitoring form."""
            if self._topology is None:
                return
            try:
                path = self._topology.stream_path(stream_id)
            except (KeyError, ValueError):
                return
            stream = path.stream
            self._selected_stream_id = stream.id
            self.monitor_scope_combo.setCurrentIndex(self.monitor_scope_combo.findData("selected"))
            self.url_edit.setText(stream.url)
            self.model_edit.setText(stream.model or self._base_config.model)
            self.width_spin.setValue(stream.width)
            self.height_spin.setValue(stream.height)
            self.confidence_spin.setValue(stream.confidence)
            self.channel_status_label.setText(f"{path.display_name} · 已选择")
            self._refresh_rule_table()
            self._update_arming_buttons()
            self._update_entity_toggle_button()

        def _on_stream_table_selected(self) -> None:
            selected = self.stream_table.selectedItems()
            if not selected:
                return
            stream_id = selected[0].data(Qt.UserRole)
            if stream_id:
                self._select_stream(str(stream_id))

        def _config_from_form(self) -> MonitorConfig:
            selected_streams = ()
            if self.monitor_scope_combo.currentData() == "selected" and self._selected_stream_id:
                site_id = area_id = camera_id = None
                if self._topology is not None:
                    try:
                        path = self._topology.stream_path(self._selected_stream_id)
                        site_id, area_id, camera_id = path.site.id, path.area.id, path.camera.id
                    except (KeyError, ValueError):
                        pass
                selected_streams = (
                    StreamRuntimeConfig(
                        stream_id=self._selected_stream_id,
                        url=self.url_edit.text().strip(),
                        model=self.model_edit.text().strip(),
                        width=self.width_spin.value(),
                        height=self.height_spin.value(),
                        fps=self._base_config.fps,
                        confidence=self.confidence_spin.value(),
                        site_id=site_id,
                        area_id=area_id,
                        camera_id=camera_id,
                    ),
                )
            email_use_ssl, email_starttls = self._email_security_settings()
            notification_channels = self._notification_channels_from_form()
            return MonitorConfig(
                url=self.url_edit.text().strip(),
                model=self.model_edit.text().strip(),
                db=self.db_edit.text().strip() or "data/factoryguard.db",
                width=self.width_spin.value(),
                height=self.height_spin.value(),
                confidence=self.confidence_spin.value(),
                confirm_frames=self.confirm_spin.value(),
                fps=self._base_config.fps,
                media_dir=self._base_config.media_dir,
                streams=selected_streams,
                topology=self._topology,
                inference_mode=str(self.inference_mode_combo.currentData() or "central"),
                central_inference_url=self.inference_url_edit.text().strip(),
                central_inference_api_key=self.inference_api_key_edit.text(),
                central_inference_timeout=self.inference_timeout_spin.value(),
                webhook_provider=str(self.webhook_provider_combo.currentData() or "custom"),
                webhook_url=self.webhook_url_edit.text().strip(),
                webhook_secret=self.webhook_secret_edit.text(),
                email_provider=str(self.email_provider_combo.currentData() or "custom"),
                email_host=self.email_host_edit.text().strip(),
                email_port=self.email_port_spin.value(),
                email_username=self.email_username_edit.text().strip(),
                email_password=self.email_password_edit.text(),
                email_sender=self.email_sender_edit.text().strip(),
                email_recipients=self._email_recipients_from_form(),
                email_use_ssl=email_use_ssl,
                email_starttls=email_starttls,
                sms_provider=str(self.sms_provider_combo.currentData() or "custom_http"),
                sms_endpoint=self.sms_endpoint_edit.text().strip(),
                sms_token=self.sms_token_edit.text(),
                sms_sender=self.sms_sender_edit.text().strip(),
                sms_template_id=self.sms_template_edit.text().strip(),
                sms_recipients=self._sms_recipients_from_form(),
                delivery_destinations=tuple(self._destinations),
                notification_channels=notification_channels,
                notification_interval=self.notification_interval_spin.value(),
                notification_batch=self.notification_batch_spin.value(),
            )

        def start_monitor(self) -> None:
            if self._thread is not None and self._thread.isRunning():
                return
            if not self._notification_channels_from_form():
                self._set_status("error", "请至少启用一个通知通道")
                return
            config = self._config_from_form()
            if not config.url or not config.model:
                self._set_status("error", "RTSP 地址和模型路径不能为空")
                return
            if config.db != ":memory:":
                Path(config.db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            self._frames = 0
            self._events = 0
            self._stream_metrics.clear()
            self._replay_locked = False
            self._thread = QThread(self)
            self._worker = MonitorWorker(config)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.status_changed.connect(self._set_status)
            self._worker.stream_preview_ready.connect(self._on_stream_preview)
            self._worker.stream_status_changed.connect(self._on_stream_status)
            self._worker.stream_frame_processed.connect(self._on_stream_frame)
            self._worker.frame_processed.connect(self._on_frame)
            self._worker.event_created.connect(self._on_event)
            self._worker.error.connect(self._on_worker_error)
            self._worker.finished.connect(self._thread.quit)
            self._worker.finished.connect(self._worker.deleteLater)
            self._thread.finished.connect(self._on_thread_finished)
            self._thread.finished.connect(self._thread.deleteLater)
            self._set_controls(True)
            self._start_notification_dispatcher(config)
            self._thread.start()

        def stop_monitor(self) -> None:
            if self._worker is not None:
                self._set_status("stopping", "正在停止监控")
                self._worker.request_stop()
            if self._notification_worker is not None:
                self._notification_worker.request_stop()
            if self._probe_worker is not None:
                self._probe_worker.request_stop()

        def quit_application(self) -> None:
            self._allow_quit = True
            self._quit_after_stop = True
            self.stop_monitor()
            if not self._workers_running():
                QApplication.instance().quit()

        def _on_thread_finished(self) -> None:
            self._worker = None
            self._thread = None
            self._set_controls(False)
            self._maybe_quit_after_workers()

        def _start_notification_dispatcher(self, config: MonitorConfig) -> None:
            """Start the outbox loop when at least one delivery channel is ready."""
            if self._notification_thread is not None and self._notification_thread.isRunning():
                return
            channels = config.notification_channels
            if not channels:
                self._notification_state = "disabled"
                self._notification_detail = "未启用通知通道"
                self.notification_status.setText("未启动 · 请至少启用一个通知通道")
                return
            if config.delivery_destinations:
                try:
                    self._validate_notification_destinations()
                except (TypeError, ValueError) as exc:
                    self._notification_state = "error"
                    self._notification_detail = str(exc)
                    self.notification_status.setText(f"未启动 · {exc}")
                    return
            url = config.webhook_url.strip() if "webhook" in channels else ""
            secret = config.webhook_secret if "webhook" in channels else ""
            if "webhook" in channels:
                if not url:
                    self._notification_state = "disabled"
                    self._notification_detail = "未配置 Webhook 地址"
                    self.notification_status.setText("未启动 · 未配置 Webhook 地址")
                    return
                if not secret:
                    self._notification_state = "disabled"
                    self._notification_detail = "缺少签名密钥"
                    self.notification_status.setText("未启动 · 缺少 Webhook 签名密钥")
                    self.statusBar().showMessage("通知调度未启动：请配置 webhook secret", 6000)
                    return
                if not url.lower().startswith("https://"):
                    self._notification_state = "disabled"
                    self._notification_detail = "Webhook 必须使用 HTTPS"
                    self.notification_status.setText("未启动 · Webhook 必须使用 HTTPS")
                    self.statusBar().showMessage("通知调度未启动：Webhook 必须使用 HTTPS", 6000)
                    return
            email_host = config.email_host if "email" in channels else ""
            email_sender = config.email_sender if "email" in channels else ""
            email_recipients = config.email_recipients if "email" in channels else ()
            if "email" in channels and (not email_host or not email_sender or not email_recipients):
                self._notification_state = "disabled"
                self._notification_detail = "邮件配置不完整"
                self.notification_status.setText("未启动 · 请配置 SMTP 主机、发件人和收件人")
                return
            sms_endpoint = config.sms_endpoint if "sms" in channels else ""
            sms_recipients = config.sms_recipients if "sms" in channels else ()
            if "sms" in channels and (not sms_endpoint or not sms_recipients):
                self._notification_state = "disabled"
                self._notification_detail = "短信配置不完整"
                self.notification_status.setText("未启动 · 请配置短信网关和接收号码")
                return
            try:
                self._notification_thread = QThread(self)
                self._notification_worker = NotificationWorker(
                    db=config.db,
                    webhook_url=url,
                    webhook_secret=secret,
                    email_host=email_host,
                    email_port=config.email_port,
                    email_username=config.email_username if "email" in channels else "",
                    email_password=config.email_password if "email" in channels else "",
                    email_sender=email_sender,
                    email_recipients=email_recipients,
                    email_use_ssl=config.email_use_ssl,
                    email_starttls=config.email_starttls,
                    sms_provider=config.sms_provider,
                    sms_endpoint=sms_endpoint,
                    sms_token=config.sms_token if "sms" in channels else "",
                    sms_sender=config.sms_sender if "sms" in channels else "",
                    sms_template_id=config.sms_template_id if "sms" in channels else "",
                    sms_recipients=sms_recipients,
                    destinations=config.delivery_destinations,
                    interval=config.notification_interval,
                    batch_size=config.notification_batch,
                )
                self._notification_worker.moveToThread(self._notification_thread)
                self._notification_thread.started.connect(self._notification_worker.run)
                self._notification_worker.status_changed.connect(self._on_notification_status)
                self._notification_worker.report_ready.connect(self._on_notification_report)
                self._notification_worker.error.connect(self._on_notification_error)
                self._notification_worker.finished.connect(self._notification_thread.quit)
                self._notification_worker.finished.connect(self._notification_worker.deleteLater)
                self._notification_thread.finished.connect(self._on_notification_thread_finished)
                self._notification_thread.finished.connect(self._notification_thread.deleteLater)
                self.notification_status.setText("启动中")
                self._notification_state = "starting"
                self._notification_detail = "通知调度器正在启动"
                self._notification_thread.start()
            except (OSError, RuntimeError, ValueError) as exc:
                self._notification_worker = None
                if self._notification_thread is not None:
                    self._notification_thread.deleteLater()
                    self._notification_thread = None
                self._notification_state = "error"
                self._notification_detail = str(exc)
                self.notification_status.setText(f"启动失败：{exc}")
                self.statusBar().showMessage(f"通知调度启动失败：{exc}", 8000)

        def _dispatch_notifications_once(self) -> None:
            """Run one delivery batch from the notification center."""
            if not self._require_permission(Permission.MANAGE_SYSTEM, "手动发送通知"):
                return
            if self._notification_once_thread is not None and self._notification_once_thread.isRunning():
                return
            if not self._notification_channels_from_form():
                self.statusBar().showMessage("请先在 Webhook、邮件 SMTP 或短信页面启用通知通道", 6000)
                return
            config = self._config_from_form()
            channels = config.notification_channels
            if not channels:
                self.statusBar().showMessage("请先在 Webhook、邮件 SMTP 或短信页面启用通知通道", 6000)
                return
            if config.delivery_destinations:
                try:
                    self._validate_notification_destinations()
                except (TypeError, ValueError) as exc:
                    self.statusBar().showMessage(f"通知配置无效：{exc}", 7000)
                    return
            url = config.webhook_url.strip() if "webhook" in channels else ""
            secret = config.webhook_secret if "webhook" in channels else ""
            if "webhook" in channels and not url:
                self.statusBar().showMessage("请先在 Webhook 通道页面配置回调地址", 6000)
                return
            if "webhook" in channels and not secret:
                self.statusBar().showMessage("请先输入 Webhook 签名密钥", 6000)
                return
            if "webhook" in channels and not url.lower().startswith("https://"):
                self.statusBar().showMessage("Webhook 地址必须使用 HTTPS", 6000)
                return
            email_host = config.email_host if "email" in channels else ""
            email_sender = config.email_sender if "email" in channels else ""
            email_recipients = config.email_recipients if "email" in channels else ()
            if "email" in channels and (not email_host or not email_sender or not email_recipients):
                self.statusBar().showMessage("请先配置 SMTP 主机、发件人和收件人", 6000)
                return
            sms_endpoint = config.sms_endpoint if "sms" in channels else ""
            sms_recipients = config.sms_recipients if "sms" in channels else ()
            if "sms" in channels and (not sms_endpoint or not sms_recipients):
                self.statusBar().showMessage("请先配置短信网关和接收号码", 6000)
                return
            if config.db == ":memory:":
                self.statusBar().showMessage("内存数据库不支持跨线程通知发送", 6000)
                return
            try:
                self._notification_once_thread = QThread(self)
                self._notification_once_worker = NotificationDispatchOnceWorker(
                    db=config.db,
                    webhook_url=url,
                    webhook_secret=secret,
                    email_host=email_host,
                    email_port=config.email_port,
                    email_username=config.email_username if "email" in channels else "",
                    email_password=config.email_password if "email" in channels else "",
                    email_sender=email_sender,
                    email_recipients=email_recipients,
                    email_use_ssl=config.email_use_ssl,
                    email_starttls=config.email_starttls,
                    sms_provider=config.sms_provider,
                    sms_endpoint=sms_endpoint,
                    sms_token=config.sms_token if "sms" in channels else "",
                    sms_sender=config.sms_sender if "sms" in channels else "",
                    sms_template_id=config.sms_template_id if "sms" in channels else "",
                    sms_recipients=sms_recipients,
                    destinations=config.delivery_destinations,
                    batch_size=config.notification_batch,
                )
                self._notification_once_worker.moveToThread(self._notification_once_thread)
                self._notification_once_thread.started.connect(self._notification_once_worker.run)
                self._notification_once_worker.status_changed.connect(self._on_notification_status)
                self._notification_once_worker.report_ready.connect(self._on_notification_report)
                self._notification_once_worker.report_ready.connect(lambda _report: self._refresh_notification_queue())
                self._notification_once_worker.error.connect(self._on_notification_error)
                self._notification_once_worker.finished.connect(self._notification_once_thread.quit)
                self._notification_once_worker.finished.connect(self._notification_once_worker.deleteLater)
                self._notification_once_thread.finished.connect(self._on_notification_once_thread_finished)
                self._notification_once_thread.finished.connect(self._notification_once_thread.deleteLater)
                self.send_notification_button.setEnabled(False)
                self._notification_once_thread.start()
            except (OSError, RuntimeError, ValueError) as exc:
                self._notification_once_worker = None
                if self._notification_once_thread is not None:
                    self._notification_once_thread.deleteLater()
                    self._notification_once_thread = None
                self.statusBar().showMessage(f"通知发送启动失败：{exc}", 8000)

        def _on_notification_once_thread_finished(self) -> None:
            self._notification_once_worker = None
            self._notification_once_thread = None
            self.send_notification_button.setEnabled(True)
            if self._notification_thread is None or not self._notification_thread.isRunning():
                self._notification_state = "stopped"
                self._notification_detail = "单次通知发送完成"
                self.notification_status.setText("已完成 · 单次通知发送")
            self._refresh_notification_queue()
            self._refresh_audit()
            self._maybe_quit_after_workers()

        def _on_notification_status(self, state: str, detail: str) -> None:
            labels = {
                "starting": "启动中",
                "running": "运行中",
                "degraded": "降级",
                "error": "异常",
                "disabled": "已停用",
                "stopped": "已停止",
            }
            self._notification_state = state
            self._notification_detail = detail
            self.notification_status.setText(f"{labels.get(state, state)} · {detail}")

        def _on_notification_report(self, report: object) -> None:
            self._notification_reports += 1
            self._notification_last_detail = str(report)
            self._refresh_notification_queue()

        def _on_notification_error(self, detail: str) -> None:
            self.statusBar().showMessage(f"通知调度：{detail}", 8000)

        def _on_notification_thread_finished(self) -> None:
            self._notification_worker = None
            self._notification_thread = None
            if self.notification_status.text().startswith(("运行中", "启动中")):
                self.notification_status.setText("已停止")
            self._notification_state = "stopped"
            self._notification_detail = "通知调度器已停止"
            self._maybe_quit_after_workers()

        def _workers_running(self) -> bool:
            return any(
                thread is not None and thread.isRunning()
                for thread in (
                    self._thread,
                    self._notification_thread,
                    self._notification_once_thread,
                    self._probe_thread,
                )
            )

        def _maybe_quit_after_workers(self) -> None:
            if self._quit_after_stop and not self._workers_running():
                QApplication.instance().quit()

        def _set_controls(self, running: bool) -> None:
            self.start_button.setEnabled(not running)
            self.stop_button.setEnabled(running)
            if self._tray is not None:
                self._tray.set_running(running)
            self._refresh_overview()

        def _on_preview(self, image: object, detections: object) -> None:
            if self._replay_locked:
                return
            values = tuple(detections) if detections is not None else ()
            self.preview.set_frame(image, values)
            self.preview_info.setText(f"实时 · 检测框 {len(values)} 个")

        def _on_stream_preview(self, stream_id: str, image: object, detections: object) -> None:
            values = tuple(detections) if detections is not None else ()
            grid_preview = self._ensure_preview_widget(stream_id)
            if grid_preview is not None:
                grid_preview.set_frame(image, values)
            if self._selected_stream_id and stream_id != self._selected_stream_id:
                return
            self._on_preview(image, detections)
            self.preview_info.setText(f"{stream_id} · 实时 · 检测框 {len(values)} 个")

        def _on_stream_status(self, stream_id: str, status: str, detail: str) -> None:
            self._stream_states[stream_id] = (status, detail)
            history = self._stream_health_history.setdefault(stream_id, {})
            history.update({"status": status, "detail": detail, "updated_at": datetime.now(UTC).isoformat()})
            self._refresh_stream_table()
            self._refresh_rule_table()
            if stream_id == self._selected_stream_id:
                self.channel_status_label.setText(
                    f"{stream_id} · {_STREAM_STATUS_LABELS.get(status, status)}"
                )
            self._refresh_overview()

        def _on_frame(self, _frame_no: int, detections: int) -> None:
            self._frames += 1
            self.runtime_label.setText(f"帧数 {self._frames} · 检测 {detections} · 事件 {self._events}")

        def _on_stream_frame(self, stream_id: str, _frame_no: int, detections: int) -> None:
            metrics = self._stream_metrics.setdefault(stream_id, {"frames": 0, "detections": 0})
            metrics["frames"] += 1
            metrics["detections"] += int(detections)
            self._refresh_overview()

        def _on_event(self, payload: object) -> None:
            self._events += 1
            if isinstance(payload, dict):
                self._event_records.insert(0, dict(payload))
                self._refresh_channel_options()
                self._refresh_event_entity_filters()
            self._apply_event_filters()
            self.runtime_label.setText(f"帧数 {self._frames} · 检测中 · 事件 {self._events}")
            self._refresh_overview()
            if self._tray is not None:
                self._tray.notify("FactoryGuard 事件", "检测到目标进入规则区域")

        def _on_worker_error(self, detail: str) -> None:
            self.statusBar().showMessage(detail, 8000)

        def _refresh_entity_tree(self) -> None:
            """Load and render the physical site hierarchy for stream selection."""
            if not hasattr(self, "entity_tree"):
                return
            path = self.db_edit.text().strip() or self._base_config.db
            path_changed = path != self._loaded_db_path
            if path_changed:
                self._stream_states.clear()
                self._stream_health_history.clear()
                self._stream_metrics.clear()
            topology = self._topology
            selected_stream_id = self._selected_stream_id
            store: SQLiteStore | None = None
            try:
                if path == ":memory:":
                    if path_changed:
                        topology = self._base_config.topology
                        self._topology = topology
                    else:
                        topology = self._topology
                elif Path(path).expanduser().exists():
                    store = SQLiteStore(path)
                    topology = store.load_topology()
                    self._topology = topology
                elif path_changed and self._loaded_db_path is not None:
                    topology = None
                    self._topology = None
                self._loaded_db_path = path
                self._load_persisted_stream_health(path, store=store)
                self.entity_tree.clear()
                if topology is None:
                    self._selected_stream_id = None
                    self._refresh_preview_grid()
                    self._update_entity_toggle_button()
                    self._update_stream_edit_button()
                    return
                for site in topology.sites:
                    site_item = QTreeWidgetItem([site.name])
                    site_item.setData(0, Qt.UserRole, {"kind": "site", "id": site.id})
                    self.entity_tree.addTopLevelItem(site_item)
                    for area in topology.areas:
                        if area.site_id == site.id and area.parent_id is None:
                            self._append_area_item(site_item, topology, area.id)
                self.entity_tree.expandToDepth(1)
                self._selected_stream_id = (
                    selected_stream_id
                    if selected_stream_id and any(stream.id == selected_stream_id for stream in topology.streams)
                    else None
                )
                self._refresh_preview_grid()
                self._refresh_stream_table()
                self._refresh_rule_table()
                self._update_entity_toggle_button()
                self._update_stream_edit_button()
                self._apply_topology_filter()
                self._refresh_overview()
            except (OSError, sqlite3.Error, ValueError, KeyError, json.JSONDecodeError) as exc:
                self.statusBar().showMessage(f"无法读取实体层级: {exc}", 5000)
            finally:
                if store is not None:
                    store.close()

        def _load_persisted_stream_health(self, path: str, *, store: SQLiteStore | None = None) -> None:
            if path == ":memory:" or not Path(path).expanduser().exists():
                return
            owned_store = store
            try:
                if owned_store is None:
                    owned_store = SQLiteStore(path)
                self._stream_health_history = owned_store.list_stream_health()
                for stream_id, snapshot in self._stream_health_history.items():
                    self._stream_states.setdefault(
                        stream_id,
                        (str(snapshot["status"]), str(snapshot["detail"])),
                    )
            except (OSError, sqlite3.Error, ValueError):
                return
            finally:
                if store is None and owned_store is not None:
                    owned_store.close()

        def _append_area_item(self, parent_item: QTreeWidgetItem, topology: Any, area_id: str) -> None:
            area = topology.get_area(area_id)
            item = QTreeWidgetItem([area.name])
            item.setData(0, Qt.UserRole, {"kind": "area", "id": area.id})
            parent_item.addChild(item)
            for camera in topology.cameras:
                if camera.area_id != area.id:
                    continue
                camera_item = QTreeWidgetItem([camera.name])
                camera_item.setData(0, Qt.UserRole, {"kind": "camera", "id": camera.id})
                item.addChild(camera_item)
                for stream in topology.streams:
                    if stream.camera_id != camera.id:
                        continue
                    stream_item = QTreeWidgetItem([f"{stream.name} ({stream.role})"])
                    stream_item.setData(0, Qt.UserRole, {"kind": "stream", "id": stream.id})
                    camera_item.addChild(stream_item)
            for child in topology.areas:
                if child.parent_id == area.id:
                    self._append_area_item(item, topology, child.id)

        def _on_entity_selected(self) -> None:
            selected = self.entity_tree.selectedItems()
            if not selected or self._topology is None:
                self._selected_stream_id = None
                self.monitor_scope_combo.setCurrentIndex(self.monitor_scope_combo.findData("all"))
                self._update_entity_toggle_button()
                self._update_arming_buttons()
                return
            metadata = selected[0].data(0, Qt.UserRole) or {}
            self._update_entity_toggle_button()
            if metadata.get("kind") != "stream":
                self._selected_stream_id = None
                self.monitor_scope_combo.setCurrentIndex(self.monitor_scope_combo.findData("all"))
                self._update_arming_buttons()
                return
            self._select_stream(str(metadata["id"]))

        def _set_status(self, state: str, detail: str) -> None:
            labels = {"ready": "就绪", "starting": "启动中", "running": "运行中", "degraded": "降级", "offline": "离线", "stopping": "停止中", "stopped": "已停止", "error": "异常"}
            self.status_label.setText(labels.get(state, state))
            colors = {"running": ("#dff5e7", "#19703b"), "degraded": ("#fff0cf", "#916000"), "error": ("#ffe1e1", "#a52626"), "starting": ("#dfebff", "#2457a6")}
            bg, fg = colors.get(state, ("#e6edf6", "#536176"))
            self.status_label.setStyleSheet(f"padding: 6px 12px; border-radius: 14px; background: {bg}; color: {fg};")
            stream_label = self._selected_stream_id or "多路推流"
            self.channel_status_label.setText(f"{stream_label} · {labels.get(state, state)}")
            self.statusBar().showMessage(detail)

        def _refresh_events(self) -> None:
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self._event_records = []
                self._refresh_event_entity_filters()
                self._apply_event_filters()
                self._refresh_overview()
                return
            try:
                with sqlite3.connect(path) as connection:
                    connection.row_factory = sqlite3.Row
                    rows = connection.execute("SELECT * FROM event ORDER BY occurred_at DESC LIMIT 1000").fetchall()
                self._event_records = [_event_record(row) for row in rows]
                self._refresh_channel_options()
                self._refresh_event_entity_filters()
                self._apply_event_filters()
                self._refresh_overview()
            except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
                self._event_records = []
                self._refresh_event_entity_filters()
                self._apply_event_filters()
                self._refresh_overview()
                self.statusBar().showMessage(f"无法读取事件记录: {exc}", 5000)

        def _refresh_channel_options(self) -> None:
            current = self.channel_filter.currentData()
            channels = sorted(
                {
                    str(row.get("stream_id") or row.get("channel_id", ""))
                    for row in self._event_records
                    if row.get("stream_id") or row.get("channel_id")
                }
            )
            self.channel_filter.blockSignals(True)
            self.channel_filter.clear()
            self.channel_filter.addItem("全部通道", None)
            for channel in channels:
                self.channel_filter.addItem(channel, channel)
            index = self.channel_filter.findData(current)
            self.channel_filter.setCurrentIndex(max(0, index))
            self.channel_filter.blockSignals(False)

        def _refresh_event_entity_filters(self) -> None:
            """Populate site, area and camera selectors from loaded events."""
            if not hasattr(self, "site_filter"):
                return
            current_values = {
                "site": self.site_filter.currentData(),
                "area": self.area_filter.currentData(),
                "camera": self.camera_filter.currentData(),
            }
            options: dict[str, dict[str, str]] = {"site": {}, "area": {}, "camera": {}}
            topology = self._topology
            for record in self._event_records:
                for kind, field in (("site", "site_id"), ("area", "area_id"), ("camera", "camera_id")):
                    value = record.get(field)
                    if not value:
                        continue
                    identifier = str(value)
                    label = identifier
                    try:
                        if topology is not None and kind == "site":
                            label = topology.get_site(identifier).name
                        elif topology is not None and kind == "area":
                            label = topology.get_area(identifier).name
                        elif topology is not None and kind == "camera":
                            label = topology.get_camera(identifier).name
                    except (KeyError, ValueError):
                        pass
                    options[kind][identifier] = label
            for kind, combo, empty_label in (
                ("site", self.site_filter, "全部厂区"),
                ("area", self.area_filter, "全部区域"),
                ("camera", self.camera_filter, "全部摄像头"),
            ):
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(empty_label, None)
                for identifier, label in sorted(options[kind].items(), key=lambda item: item[1]):
                    combo.addItem(label, identifier)
                selected = combo.findData(current_values[kind])
                combo.setCurrentIndex(max(0, selected))
                combo.blockSignals(False)

        def _refresh_audit(self) -> None:
            """Load recent audit rows and refresh the administrator filters."""
            if not hasattr(self, "audit_table"):
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self._audit_records = []
                self._audit_loaded = True
                self._refresh_audit_filter_options()
                self._apply_audit_filters()
                return
            try:
                store = SQLiteStore(path)
                try:
                    self._audit_records = [dict(row) for row in store.list_audit(limit=5000)]
                finally:
                    store.close()
                self._audit_loaded = True
                self._refresh_audit_filter_options()
                self._apply_audit_filters()
            except (OSError, sqlite3.Error, ValueError) as exc:
                self._audit_records = []
                self._audit_loaded = True
                self._refresh_audit_filter_options()
                self._apply_audit_filters()
                self.statusBar().showMessage(f"无法读取审计记录：{exc}", 5000)

        def _refresh_notification_queue(self) -> None:
            """Refresh delivery state and queue rows for operators."""
            if not hasattr(self, "notification_outbox_table"):
                return
            path = self.db_edit.text().strip() or self._base_config.db
            rows: list[sqlite3.Row] = []
            counts: dict[str, int] = {}
            if path != ":memory:":
                try:
                    store = SQLiteStore(path)
                    try:
                        rows = store.list_outbox(limit=500)
                        counts = store.outbox_counts()
                    finally:
                        store.close()
                except (OSError, sqlite3.Error, ValueError) as exc:
                    self.notification_queue_summary.setText(f"通知队列读取失败：{exc}")
                    self.notification_outbox_table.setRowCount(0)
                    self.requeue_notification_button.setEnabled(False)
                    return
            labels = {
                "pending": "待发送",
                "retry": "等待重试",
                "sending": "发送中",
                "sent": "已发送",
                "failed": "失败",
            }
            channel_labels = {"webhook": "Webhook", "email": "邮件", "sms": "短信"}
            self.notification_outbox_table.setRowCount(0)
            for row_data in rows:
                row = self.notification_outbox_table.rowCount()
                self.notification_outbox_table.insertRow(row)
                status = str(row_data["status"])
                values = (
                    labels.get(status, status),
                    channel_labels.get(str(row_data["channel"]), str(row_data["channel"])),
                    str(row_data["event_id"]),
                    str(row_data["event_level"] or "—"),
                    str(row_data["attempts"] or 0),
                    str(row_data["next_retry_at"] or "—"),
                    str(row_data["sent_at"] or "—"),
                    _redact_text(row_data["last_error"] or "—"),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, {"id": str(row_data["id"]), "status": status})
                    self.notification_outbox_table.setItem(row, column, item)
            summary = " · ".join(
                f"{label} {counts.get(key, 0)}"
                for key, label in (
                    ("pending", "待发送"),
                    ("retry", "重试"),
                    ("sending", "发送中"),
                    ("sent", "已发送"),
                    ("failed", "失败"),
                )
            )
            self.notification_queue_summary.setText(summary or "通知队列为空")
            self.requeue_notification_button.setEnabled(False)

        def _on_notification_selected(self) -> None:
            selected = self.notification_outbox_table.selectedItems()
            if not selected:
                self.requeue_notification_button.setEnabled(False)
                return
            metadata = selected[0].data(Qt.UserRole) or {}
            self.requeue_notification_button.setEnabled(metadata.get("status") == "failed")

        def _requeue_selected_notification(self) -> None:
            if not self._require_permission(Permission.MANAGE_SYSTEM, "通知重新入队"):
                return
            selected = self.notification_outbox_table.selectedItems()
            if not selected:
                return
            metadata = selected[0].data(Qt.UserRole) or {}
            outbox_id = str(metadata.get("id") or "")
            if not outbox_id or metadata.get("status") != "failed":
                return
            answer = QMessageBox.question(
                self,
                "重新入队",
                "确定将这条失败通知重新放回待发送队列吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库不支持跨页面通知队列操作", 5000)
                return
            try:
                store = SQLiteStore(path)
                try:
                    changed = store.requeue_outbox(outbox_id, actor=self._current_actor())
                finally:
                    store.close()
                if changed:
                    self.statusBar().showMessage("失败通知已重新入队", 5000)
                    self._refresh_notification_queue()
                    self._refresh_audit()
                else:
                    self.statusBar().showMessage("通知状态已变化，请刷新队列", 5000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"通知重新入队失败：{exc}", 8000)


        def _refresh_audit_filter_options(self) -> None:
            """Keep actor/action selectors stable while their values change."""
            if not hasattr(self, "audit_actor_filter"):
                return
            actor = self.audit_actor_filter.currentData()
            action = self.audit_action_filter.currentData()
            actors = sorted({str(row.get("actor", "")) for row in self._audit_records if row.get("actor")})
            actions = sorted({str(row.get("action", "")) for row in self._audit_records if row.get("action")})
            for combo, current, values, label in (
                (self.audit_actor_filter, actor, actors, "全部操作人"),
                (self.audit_action_filter, action, actions, "全部动作"),
            ):
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(label, None)
                for value in values:
                    combo.addItem(value, value)
                index = combo.findData(current)
                combo.setCurrentIndex(max(0, index))
                combo.blockSignals(False)

        def _apply_audit_filters(self) -> None:
            if not hasattr(self, "audit_table"):
                return
            needle = self.audit_search_edit.text().strip().lower()
            actor = self.audit_actor_filter.currentData()
            action = self.audit_action_filter.currentData()
            visible: list[dict[str, Any]] = []
            for record in self._audit_records:
                if actor and record.get("actor") != actor:
                    continue
                if action and record.get("action") != action:
                    continue
                detail = _redact_text(record.get("detail", ""))
                if needle:
                    haystack = " ".join(
                        str(record.get(key, "")) for key in ("actor", "action", "resource", "result")
                    ) + f" {detail}"
                    if needle not in haystack.lower():
                        continue
                visible.append({**record, "detail": detail})
            self._visible_audit_records = visible
            self.audit_table.setRowCount(0)
            for record in visible:
                row = self.audit_table.rowCount()
                self.audit_table.insertRow(row)
                values = (
                    str(record.get("ts", "")),
                    str(record.get("actor", "")),
                    str(record.get("action", "")),
                    str(record.get("resource", "")),
                    str(record.get("result", "")),
                    str(record.get("detail", "")),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, record.get("id"))
                    self.audit_table.setItem(row, column, item)
            self.audit_summary_label.setText(
                f"已显示 {len(visible)} 条记录 · 共加载 {len(self._audit_records)} 条"
            )

        def _apply_event_filters(self) -> None:
            needle = self.search_edit.text().strip().lower()
            level = self.level_filter.currentData()
            status = self.status_filter.currentData()
            channel = self.channel_filter.currentData()
            site_id = self.site_filter.currentData()
            area_id = self.area_filter.currentData()
            camera_id = self.camera_filter.currentData()
            seconds = self.time_filter.currentData()
            cutoff = datetime.now(UTC) - timedelta(seconds=int(seconds)) if seconds else None
            visible: list[dict[str, Any]] = []
            for record in self._event_records:
                if level and record.get("level") != level:
                    continue
                if status and record.get("status") != status:
                    continue
                if channel and (record.get("stream_id") or record.get("channel_id")) != channel:
                    continue
                if site_id and record.get("site_id") != site_id:
                    continue
                if area_id and record.get("area_id") != area_id:
                    continue
                if camera_id and record.get("camera_id") != camera_id:
                    continue
                if cutoff is not None and not _event_after(record.get("occurred_at"), cutoff):
                    continue
                if needle and needle not in _search_text(record):
                    continue
                visible.append(record)
            self._visible_records = visible
            self.events_table.setRowCount(0)
            for record in visible:
                row = self.events_table.rowCount()
                self.events_table.insertRow(row)
                target = record.get("target") or {}
                values = (
                    str(record.get("occurred_at", "")),
                    str(record.get("level", "")),
                    _record_entity_text(record),
                    _target_text(target),
                    _EVENT_STATUS_LABELS.get(str(record.get("status", "open")), str(record.get("status", "open"))),
                    "可回放" if _media_path(record) else "无媒体",
                )
                for column, value in enumerate(values):
                    self.events_table.setItem(row, column, QTableWidgetItem(value))
            self.event_detail.setText(f"已显示 {len(visible)} 条记录 · 双击可查看回放媒体")

        def _on_event_selected(self) -> None:
            records = self._selected_event_records()
            if not records:
                self._selected_event = None
                self.replay_button.setEnabled(False)
                self.confirm_event_button.setEnabled(False)
                self.false_positive_button.setEnabled(False)
                self.close_event_button.setEnabled(False)
                self.event_inspector.setText("选择一条事件记录查看详细信息")
                return
            if len(records) > 1:
                self._selected_event = None
                self.replay_button.setEnabled(False)
                self.confirm_event_button.setEnabled(
                    any(str(record.get("status", "open")) in {"open", "false_positive"} for record in records)
                )
                self.false_positive_button.setEnabled(
                    any(str(record.get("status", "open")) in {"open", "confirmed"} for record in records)
                )
                self.close_event_button.setEnabled(any(str(record.get("status", "open")) != "closed" for record in records))
                self.event_detail.setText(f"已选择 {len(records)} 条事件 · 可批量处置")
                self.event_inspector.setText(
                    f"已选择 {len(records)} 条事件\n可使用下方按钮批量确认、标记误报或关闭。"
                )
                return
            record = records[0]
            self._selected_event = record
            target = record.get("target") or {}
            snapshot = _existing_path(record.get("snapshot_uri"))
            clip = _existing_path(record.get("clip_uri"))
            self.replay_button.setEnabled(snapshot is not None or clip is not None)
            current_status = str(record.get("status", "open"))
            self.confirm_event_button.setEnabled(current_status in {"open", "false_positive"})
            self.false_positive_button.setEnabled(current_status in {"open", "confirmed"})
            self.close_event_button.setEnabled(current_status != "closed")
            entity_text = _record_entity_text(record) or str(record.get("stream_id") or record.get("channel_id", ""))
            status_text = _EVENT_STATUS_LABELS.get(current_status, current_status)
            media_text = "已生成媒体，可查看回放" if snapshot or clip else "该事件没有可用媒体"
            detail_text = (
                f"{record.get('occurred_at', '')}\n{entity_text} · {target.get('label', 'object')} · "
                f"置信度 {float(target.get('confidence', 0.0)):.0%} · {status_text}\n{media_text}"
            )
            self.event_detail.setText(detail_text)
            bbox = target.get("bbox") or ()
            track_id = target.get("track_id") or "—"
            self.event_inspector.setText(
                f"事件 ID：{record.get('id', '—')}\n"
                f"规则：{record.get('rule_id', '—')}\n"
                f"实体路径：{entity_text or '—'}\n"
                f"目标：{target.get('label', 'object')} · 置信度 {float(target.get('confidence', 0.0)):.1%} · "
                f"轨迹 {track_id}\n"
                f"边界框：{bbox or '—'}\n"
                f"发生时间：{record.get('occurred_at', '—')} · 状态：{status_text}\n"
                f"快照：{record.get('snapshot_uri') or '—'}\n"
                f"片段：{record.get('clip_uri') or '—'}"
            )

        def _selected_event_records(self) -> list[dict[str, Any]]:
            rows = sorted({index.row() for index in self.events_table.selectionModel().selectedRows()})
            return [self._visible_records[row] for row in rows if 0 <= row < len(self._visible_records)]

        def _set_selected_event_status(self, status: EventStatus, *, confirm: bool = True) -> None:
            if not self._require_permission(Permission.REVIEW_EVENTS, "事件处置"):
                return
            records = self._selected_event_records()
            if not records:
                return
            if len(records) == 1:
                self._selected_event = records[0]
                self._set_event_status(status)
                return
            if confirm:
                answer = QMessageBox.question(
                    self,
                    "确认批量处置",
                    f"确定要将选中的 {len(records)} 条事件更新为“{_EVENT_STATUS_LABELS[status.value]}”吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库不支持跨页面事件处置", 5000)
                return
            updated_count = 0
            try:
                store = SQLiteStore(path)
                try:
                    for record in records:
                        if store.update_event_status(
                            str(record.get("id", "")),
                            status,
                            actor=self._current_actor(),
                            detail=f"desktop bulk review: {status.value}",
                        ):
                            updated_count += 1
                finally:
                    store.close()
                self._refresh_events()
                self.statusBar().showMessage(
                    f"已批量更新 {updated_count} 条事件：{_EVENT_STATUS_LABELS[status.value]}",
                    6000,
                )
            except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
                self.statusBar().showMessage(f"批量事件处置失败：{exc}", 8000)

        def _set_event_status(self, status: EventStatus) -> None:
            if not self._require_permission(Permission.REVIEW_EVENTS, "事件处置"):
                return
            record = self._selected_event
            if record is None:
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库不支持跨页面事件处置", 5000)
                return
            try:
                store = SQLiteStore(path)
                try:
                    updated = store.update_event_status(
                        str(record.get("id", "")),
                        status,
                        actor=self._current_actor(),
                        detail=f"desktop review: {status.value}",
                    )
                finally:
                    store.close()
                if not updated:
                    self.statusBar().showMessage("事件记录已不存在或已被删除", 5000)
                    return
                self._refresh_events()
                self.statusBar().showMessage(f"事件已更新为：{_EVENT_STATUS_LABELS[status.value]}", 5000)
            except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
                self.statusBar().showMessage(f"事件处置失败: {exc}", 5000)

        def _export_root(self) -> Path:
            path = self.db_edit.text().strip() or self._base_config.db
            return Path(path).expanduser().resolve().parent if path != ":memory:" else Path.cwd()

        def _export_events_csv(self) -> None:
            try:
                destination = self._export_root() / "exports" / f"events-{datetime.now(UTC):%Y%m%d-%H%M%S}.csv"
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["时间", "级别", "实体路径", "目标", "状态", "媒体", "事件 ID", "规则 ID"])
                    for record in self._visible_records:
                        target = record.get("target") or {}
                        writer.writerow(
                            [
                                record.get("occurred_at", ""),
                                record.get("level", ""),
                                _record_entity_text(record),
                                _target_text(target),
                                _EVENT_STATUS_LABELS.get(str(record.get("status", "open")), record.get("status", "open")),
                                _media_path(record) or "",
                                record.get("id", ""),
                                record.get("rule_id", ""),
                            ]
                        )
                self.statusBar().showMessage(f"事件 CSV 已导出：{destination}", 8000)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(f"事件导出失败：{exc}", 8000)

        def _export_audit_json(self) -> None:
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库没有可导出的审计日志", 5000)
                return
            try:
                if self._audit_loaded:
                    records = list(self._visible_audit_records)
                else:
                    store = SQLiteStore(path)
                    try:
                        records = [dict(row) for row in store.list_audit(limit=5000)]
                    finally:
                        store.close()
                records = [
                    {**record, "detail": _redact_text(record.get("detail", ""))}
                    for record in records
                ]
                destination = self._export_root() / "exports" / f"audit-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                self.statusBar().showMessage(f"审计 JSON 已导出：{destination}", 8000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"审计导出失败：{exc}", 8000)

        def _show_selected_replay(self) -> None:
            record = self._selected_event
            if record is None:
                return
            clip = _existing_path(record.get("clip_uri"))
            if clip:
                QDesktopServices.openUrl(QUrl.fromLocalFile(clip))
                self.statusBar().showMessage("已打开事件视频回放", 5000)
                return
            snapshot = _existing_path(record.get("snapshot_uri"))
            if snapshot is None:
                self.statusBar().showMessage("该事件没有可用的回放快照", 5000)
                return
            if self.preview.set_image_file(snapshot, [record], mode="事件回放"):
                self._replay_locked = True
                self.preview_info.setText(f"事件回放 · {record.get('occurred_at', '')}")

        def _show_live_preview(self) -> None:
            self._replay_locked = False
            self.preview.clear("等待下一帧实时画面")
            self.preview_info.setText("已返回实时预览")

        def _open_database(self) -> None:
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库没有可打开的文件", 5000)
                return
            database = Path(path).expanduser().resolve()
            database.parent.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(database.parent)))

        def _backup_database(self) -> None:
            if not self._require_permission(Permission.MANAGE_SYSTEM, "数据库备份"):
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库没有可备份的文件", 5000)
                return
            source = Path(path).expanduser().resolve()
            if not source.exists():
                self.statusBar().showMessage("数据库文件尚未创建，暂时无法备份", 5000)
                return
            default = source.with_name(f"{source.stem}-backup-{datetime.now():%Y%m%d-%H%M%S}.db")
            destination, _ = QFileDialog.getSaveFileName(
                self,
                "选择数据库备份位置",
                str(default),
                "SQLite 数据库 (*.db);;所有文件 (*.*)",
            )
            if not destination:
                return
            try:
                store = SQLiteStore(source)
                try:
                    backup = store.backup_to(destination)
                finally:
                    store.close()
                self.statusBar().showMessage(f"数据库备份已完成：{backup}", 8000)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"数据库备份失败：{exc}", 8000)

        def _export_configuration(self) -> None:
            if not self._require_permission(Permission.MANAGE_SYSTEM, "导出配置"):
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库没有可导出的配置", 5000)
                return
            source = Path(path).expanduser().resolve()
            if not source.exists():
                self.statusBar().showMessage("数据库文件尚未创建，暂时无法导出配置", 5000)
                return
            default = source.parent / "config" / f"factoryguard-config-{datetime.now():%Y%m%d-%H%M%S}.json"
            destination, _ = QFileDialog.getSaveFileName(
                self,
                "选择配置包位置",
                str(default),
                "FactoryGuard 配置包 (*.json);;所有文件 (*.*)",
            )
            if not destination:
                return
            try:
                store = SQLiteStore(source)
                try:
                    payload = store.export_configuration()
                finally:
                    store.close()
                target = Path(destination).expanduser().resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self.statusBar().showMessage(f"配置包已导出：{target}", 8000)
            except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
                self.statusBar().showMessage(f"配置导出失败：{exc}", 8000)

        def _import_configuration(self) -> None:
            if not self._require_permission(Permission.MANAGE_SYSTEM, "导入配置"):
                return
            if self._workers_running():
                self.statusBar().showMessage("请先停止监控，再导入配置包", 6000)
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库不支持配置导入", 5000)
                return
            source, _ = QFileDialog.getOpenFileName(
                self,
                "选择 FactoryGuard 配置包",
                str(Path(path).expanduser().resolve().parent),
                "FactoryGuard 配置包 (*.json);;所有文件 (*.*)",
            )
            if not source:
                return
            answer = QMessageBox.question(
                self,
                "确认导入配置",
                "导入将替换当前厂区、推流、规则和布防配置，但保留事件与审计记录。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                payload = json.loads(Path(source).read_text(encoding="utf-8"))
                store = SQLiteStore(path)
                try:
                    store.import_configuration(payload, actor=self._current_actor(), replace=True)
                    self._topology = store.load_topology()
                finally:
                    store.close()
                self._selected_stream_id = None
                self._refresh_entity_tree()
                self._refresh_events()
                detail = "配置包导入完成"
                if payload.get("credentials_redacted"):
                    detail += " · RTSP 凭据已脱敏，请重新填写"
                self.statusBar().showMessage(detail, 10000)
            except (OSError, json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
                self.statusBar().showMessage(f"配置导入失败：{exc}", 8000)

        def _cleanup_media(self) -> None:
            if not self._require_permission(Permission.MANAGE_SYSTEM, "清理媒体"):
                return
            if self._workers_running():
                self.statusBar().showMessage("请先停止监控，再清理过期媒体", 6000)
                return
            path = self.db_edit.text().strip() or self._base_config.db
            if path == ":memory:":
                self.statusBar().showMessage("内存数据库没有媒体目录", 5000)
                return
            days = self.retention_days_spin.value()
            answer = QMessageBox.question(
                self,
                "确认清理媒体",
                f"将删除超过 {days:g} 天的截图和事件片段，但保留事件记录。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            media_root = Path(self._base_config.media_dir)
            if not media_root.is_absolute():
                media_root = Path(path).expanduser().resolve().parent / media_root
            try:
                store = SQLiteStore(path)
                try:
                    report = MediaRetentionManager(media_root=media_root).cleanup(
                        store,
                        retention_days=days,
                    )
                finally:
                    store.close()
                self.statusBar().showMessage(
                    f"媒体清理完成：删除 {report.removed} 个文件，释放 {report.bytes_freed / (1024**2):.1f} MB，"
                    f"失败 {report.errors} 个",
                    10000,
                )
                self._refresh_events()
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"媒体清理失败：{exc}", 8000)

        def _apply_theme(self, name: str) -> None:
            if name in _THEMES:
                QApplication.instance().setStyleSheet(
                    _THEMES[name]
                    + """
                    QFrame#metricCard {
                        background: palette(base);
                        border: 1px solid palette(mid);
                        border-left: 3px solid #4a78a8;
                        border-radius: 4px;
                    }
                    QLabel#metricCaption { color: palette(mid); font-size: 12px; }
                    QLabel#metricValue { color: palette(text); font-size: 25px; font-weight: 600; }
                    QLabel#metricDetail { color: palette(mid); font-size: 11px; }
                    QScrollArea#pageScroll { background: transparent; }
                    """
                )

        def _show_from_tray(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
            self._settings.setValue("theme", self.theme_combo.currentText())
            if not self._allow_quit and self._tray is not None:
                self.hide()
                self._tray.notify("FactoryGuard 仍在运行", "窗口已隐藏到系统托盘")
                event.ignore()
                return
            if self._workers_running():
                self._allow_quit = True
                self._quit_after_stop = True
                self.stop_monitor()
                event.ignore()
                return
            self.stop_monitor()
            event.accept()


    def QSystemTrayIcon_available() -> bool:
        from .qt import QSystemTrayIcon

        return QSystemTrayIcon.isSystemTrayAvailable()


else:

    class MainWindow:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()

    def QSystemTrayIcon_available() -> bool:
        return False


def launch(*, config: MonitorConfig | None = None, start_hidden: bool = False, argv: list[str] | None = None) -> int:
    """Start the Qt application and return its event-loop exit code."""
    require_qt()
    app = QApplication.instance() or QApplication(argv or [])
    app.setApplicationName("FactoryGuard")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(config=config, start_hidden=start_hidden)
    if not start_hidden or window._tray is None:  # type: ignore[attr-defined]
        window.show()
    return app.exec()


def _event_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    payload = json.loads(record.get("payload") or "{}")
    record.update(payload)
    record["target"] = payload.get("target") or {}
    record["snapshot_uri"] = record.get("snapshot_uri") or payload.get("snapshot_uri")
    record["clip_uri"] = record.get("clip_uri") or payload.get("clip_uri")
    return record


def _search_text(record: dict[str, Any]) -> str:
    target = record.get("target") or {}
    return " ".join(
        str(value).lower()
        for value in (
            record.get("id", ""),
            record.get("channel_id", ""),
            record.get("stream_id", ""),
            record.get("site_id", ""),
            record.get("area_id", ""),
            record.get("camera_id", ""),
            record.get("level", ""),
            record.get("rule_id", ""),
            target.get("label", ""),
            target.get("track_id", ""),
        )
    )


def _record_entity_text(record: dict[str, Any]) -> str:
    entity = record.get("entity") or {}
    values = (
        entity.get("site_id") or record.get("site_id"),
        entity.get("area_id") or record.get("area_id"),
        entity.get("camera_id") or record.get("camera_id"),
        entity.get("stream_id") or record.get("stream_id") or record.get("channel_id"),
    )
    return " / ".join(str(value) for value in values if value)


def _event_after(value: Any, cutoff: datetime) -> bool:
    if not value:
        return False
    try:
        occurred = datetime.fromisoformat(str(value))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=UTC)
        return occurred.astimezone(UTC) >= cutoff
    except ValueError:
        return False


def _target_text(target: dict[str, Any]) -> str:
    label = str(target.get("label", "unknown"))
    confidence = target.get("confidence")
    return label if confidence is None else f"{label} ({float(confidence):.0%})"


def _existing_path(value: Any) -> str | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    return str(path) if path.is_file() else None


def _media_path(record: dict[str, Any]) -> str | None:
    return _existing_path(record.get("clip_uri")) or _existing_path(record.get("snapshot_uri"))


def _redact_text(value: Any) -> str:
    """Remove credentials from diagnostic text before it leaves the host."""
    text = str(value or "")
    return re.sub(r"((?:rtsp|https?)://)[^/@\s]+@", r"\1***:***@", text, flags=re.IGNORECASE)


__all__ = ["MainWindow", "launch"]
