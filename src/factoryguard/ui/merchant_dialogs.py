"""Operator-friendly dialogs and background workers for the new console."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

from factoryguard.agent.context import CameraView
from factoryguard.config import AppConfigStore
from factoryguard.devices.base import DeviceCredentials
from factoryguard.domain.incidents import (
    SEVERITY_LABELS,
    Incident,
    IncidentSeverity,
)
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceProtocol,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.media.errors import summarise_media_error
from factoryguard.media.ffmpeg import find_ffmpeg, require_ffmpeg
from factoryguard.media.rtsp import RTSPCapture
from factoryguard.security.credentials import PlaintextCredentialVault
from factoryguard.storage.sqlite import SQLiteStore

from .qt import HAS_QT, require_qt


@dataclass(slots=True)
class ResolvedStream:
    uri: str
    error: str = ""
    adapter: Any = None


def _area_option_label(topology: Any, area: Area) -> str:
    """Label an area with its site so multi-site setups stay distinguishable."""
    try:
        site_name = topology.get_site(area.site_id).name
    except KeyError:
        return area.name
    return f"{site_name} / {area.name}"


def _camera_option_label(camera: CameraView) -> str:
    """Label a camera with its site and area."""
    return f"{camera.site.name} / {camera.area.name} / {camera.camera.name}"


def _split_url_credentials(value: str) -> tuple[str, str, str]:
    """Strip userinfo from a media URL and return it separately."""
    if "://" not in value:
        return value, "", ""
    parsed = urlsplit(value)
    if parsed.username is None:
        return value, "", ""
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    sanitized = urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return sanitized, unquote(parsed.username), unquote(parsed.password or "")


if HAS_QT:
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from .preview import PreviewWidget, numpy_to_qimage
    from .stream_probe import OnvifProbeWorker, StreamProbeWorker


if HAS_QT:

    def _dialog_shell(title: str, hint: str) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog()
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(13)
        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        subtitle = QLabel(hint)
        subtitle.setObjectName("dialogHint")
        subtitle.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(subtitle)
        return dialog, layout

    class LivePreviewDialog(QDialog):
        """Decode one camera stream for live viewing."""

        def __init__(self, *, title: str, url: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle(f"实时画面 · {title}")
            self.resize(1040, 680)
            self._worker: CameraPreviewWorker | None = None
            self._thread: QThread | None = None
            self._close_pending = False
            self._url = url
            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
            heading = QHBoxLayout()
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            self.live_pill = QLabel("连接中")
            self.live_pill.setObjectName("statusPill")
            self.live_pill.setProperty("state", "unknown")
            self.reconnect_button = QPushButton("重新连接")
            self.reconnect_button.setObjectName("secondary")
            self.reconnect_button.setVisible(False)
            self.reconnect_button.clicked.connect(self._reconnect)
            heading.addWidget(label)
            heading.addStretch(1)
            heading.addWidget(self.reconnect_button)
            heading.addWidget(self.live_pill)
            layout.addLayout(heading)
            self.preview = PreviewWidget()
            self.preview.clear("正在连接摄像头…")
            layout.addWidget(self.preview, 1)
            safe_url, _username, _password = _split_url_credentials(url)
            self.status = QLabel(safe_url)
            self.status.setObjectName("muted")
            self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(self.status)
            self._start(url)

        def _start(self, url: str) -> None:
            if self._thread is not None:
                try:
                    if self._thread.isRunning():
                        return
                except RuntimeError:
                    self._thread = None
            self.reconnect_button.setVisible(False)
            self.preview.clear("正在连接摄像头…")
            self.live_pill.setText("连接中")
            self.live_pill.setProperty("state", "unknown")
            thread = CameraPreviewWorker(url, self)
            self._thread = thread
            self._worker = thread
            thread.frame_ready.connect(self._on_frame)
            thread.state_changed.connect(self._on_state)
            thread.finished.connect(self._on_thread_finished)
            thread.start()

        @Slot()
        def _on_thread_finished(self) -> None:
            thread = self._thread
            self._worker = None
            self._thread = None
            if thread is not None:
                try:
                    thread.deleteLater()
                except RuntimeError:
                    pass
            if self._close_pending:
                self._close_pending = False
                QTimer.singleShot(0, self.close)
            else:
                self.reconnect_button.setVisible(True)
                if self.live_pill.text() == "连接中":
                    self.live_pill.setText("连接中断")
                    self.live_pill.setProperty("state", "offline")

        @Slot(QImage)
        def _on_frame(self, image: QImage) -> None:
            self.preview.set_image(image)

        @Slot(str, bool)
        def _on_state(self, text: str, online: bool) -> None:
            self.status.setText(text)
            self.live_pill.setText("实时" if online else "连接中断")
            self.live_pill.setProperty("state", "online" if online else "offline")
            self.reconnect_button.setVisible(not online)
            style = self.live_pill.style()
            style.unpolish(self.live_pill)
            style.polish(self.live_pill)

        def _reconnect(self) -> None:
            if self._thread is not None:
                try:
                    if self._thread.isRunning():
                        return
                except RuntimeError:
                    self._thread = None
            self.status.setText("正在重新连接…")
            self._start(self._url)

        def closeEvent(self, event: Any) -> None:
            if self._worker is not None:
                try:
                    self._worker.request_stop()
                except RuntimeError:
                    self._worker = None
            thread = self._thread
            if thread is not None:
                try:
                    if thread.isRunning():
                        self._close_pending = True
                        self.status.setText("正在断开视频流…")
                        event.ignore()
                        return
                except RuntimeError:
                    self._thread = None
            event.accept()

    class LiveWallDialog(QDialog):
        """Decode up to four camera streams in one operational view."""

        MAX_STREAMS = 4
        PREVIEW_FPS = 4.0
        PREVIEW_WIDTH = 960
        PREVIEW_HEIGHT = 540

        def __init__(
            self,
            previews: Sequence[tuple[str, str]],
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            selected = tuple(previews[: self.MAX_STREAMS])
            self.setWindowTitle("多路实时画面")
            self.resize(1180, 760)
            self.setMinimumSize(980, 640)
            self._workers: list[CameraPreviewWorker] = []
            self._active_tiles: set[int] = set()
            self._stream_count = len(selected)
            self._online_tiles: set[int] = set()
            self._close_pending = False
            self._close_timer = QTimer(self)
            self._close_timer.setInterval(30)
            self._close_timer.timeout.connect(self._finish_pending_close)
            self.preview_widgets: list[PreviewWidget] = []
            self.reconnect_buttons: list[QPushButton] = []
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(12)
            heading = QHBoxLayout()
            title = QLabel("多路实时画面")
            title.setObjectName("sectionTitle")
            hint = QLabel(f"同时查看 {len(selected)} 路 · 关闭窗口会停止全部解码")
            hint.setObjectName("cardMuted")
            self.wall_status = QLabel(f"正在连接 0/{self._stream_count} 路")
            self.wall_status.setObjectName("muted")
            heading.addWidget(title)
            heading.addWidget(hint)
            heading.addStretch(1)
            heading.addWidget(self.wall_status)
            layout.addLayout(heading)

            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            columns = 1 if len(selected) == 1 else 2
            for index, (camera_title, url) in enumerate(selected):
                tile = QFrame()
                tile.setObjectName("liveTile")
                tile_layout = QVBoxLayout(tile)
                tile_layout.setContentsMargins(10, 9, 10, 10)
                tile_layout.setSpacing(7)
                header = QHBoxLayout()
                name = QLabel(camera_title)
                name.setObjectName("cameraName")
                pill = QLabel("连接中")
                pill.setObjectName("statusPill")
                pill.setProperty("state", "unknown")
                pill.setAlignment(Qt.AlignCenter)
                reconnect = QPushButton("重连")
                reconnect.setObjectName("linkButton")
                reconnect.setVisible(False)
                self.reconnect_buttons.append(reconnect)
                header.addWidget(name, 1)
                header.addWidget(reconnect)
                header.addWidget(pill)
                tile_layout.addLayout(header)
                preview = PreviewWidget()
                preview.setMinimumSize(360, 220)
                preview.clear("正在连接摄像头…")
                self.preview_widgets.append(preview)
                tile_layout.addWidget(preview, 1)
                grid.addWidget(tile, index // columns, index % columns)
                reconnect.clicked.connect(
                    lambda _checked=False, current_url=url, current_preview=preview, current_pill=pill, current_index=index, current_button=reconnect: (
                        self._retry_tile(
                            current_url,
                            current_preview,
                            current_pill,
                            current_index,
                            current_button,
                        )
                    )
                )
                self._start_preview(url, preview, pill, index, reconnect)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            for row in range((len(selected) + columns - 1) // columns):
                grid.setRowStretch(row, 1)
            layout.addLayout(grid, 1)

        def _start_preview(
            self,
            url: str,
            preview: PreviewWidget,
            pill: QLabel,
            index: int,
            reconnect: QPushButton,
        ) -> None:
            if index in self._active_tiles:
                return
            self._active_tiles.add(index)
            reconnect.setVisible(False)
            preview.clear("正在连接摄像头…")
            pill.setText("连接中")
            pill.setProperty("state", "unknown")
            worker = CameraPreviewWorker(
                url,
                self,
                fps=self.PREVIEW_FPS,
                width=self.PREVIEW_WIDTH,
                height=self.PREVIEW_HEIGHT,
            )
            self._workers.append(worker)
            worker.frame_ready.connect(preview.set_image)
            worker.state_changed.connect(
                lambda text, online, current_pill=pill, current_index=index, current_button=reconnect: (
                    self._set_tile_state(current_pill, current_index, current_button, text, online)
                )
            )
            worker.finished.connect(
                lambda current=worker, current_index=index, current_pill=pill, current_button=reconnect: (
                    self._on_preview_finished(current, current_index, current_pill, current_button)
                )
            )
            worker.start()

        def _set_tile_state(
            self,
            pill: QLabel,
            index: int,
            reconnect: QPushButton,
            text: str,
            online: bool,
        ) -> None:
            pill.setText("实时" if online else "连接中断")
            pill.setProperty("state", "online" if online else "offline")
            style = pill.style()
            style.unpolish(pill)
            style.polish(pill)
            pill.setToolTip(text)
            reconnect.setVisible(not online and not self._close_pending)
            if online:
                self._online_tiles.add(index)
            else:
                self._online_tiles.discard(index)
            if not self._close_pending:
                self.wall_status.setText(f"在线 {len(self._online_tiles)}/{self._stream_count} 路")

        def _on_preview_finished(
            self,
            worker: CameraPreviewWorker,
            index: int,
            pill: QLabel,
            reconnect: QPushButton,
        ) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            self._active_tiles.discard(index)
            self._online_tiles.discard(index)
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            if not self._close_pending:
                self._set_tile_state(pill, index, reconnect, "视频流已断开", False)
                self.wall_status.setText(
                    f"在线 {len(self._online_tiles)}/{self._stream_count} 路"
                    if self._workers
                    else "全部视频已断开"
                )
            if self._close_pending:
                self._finish_pending_close()

        def _retry_tile(
            self,
            url: str,
            preview: PreviewWidget,
            pill: QLabel,
            index: int,
            reconnect: QPushButton,
        ) -> None:
            if self._close_pending or index in self._active_tiles:
                return
            self._start_preview(url, preview, pill, index, reconnect)

        def _finish_pending_close(self) -> None:
            if not self._close_pending or self._running_workers():
                return
            self._close_timer.stop()
            self._close_pending = False
            QTimer.singleShot(0, self.close)

        def _running_workers(self) -> bool:
            for worker in tuple(self._workers):
                try:
                    if worker.isRunning():
                        return True
                except RuntimeError:
                    continue
            return False

        def closeEvent(self, event: Any) -> None:
            for worker in tuple(self._workers):
                try:
                    worker.request_stop()
                except RuntimeError:
                    continue
            if self._running_workers():
                self._close_pending = True
                self._close_timer.start()
                for button in self.reconnect_buttons:
                    button.setVisible(False)
                self.wall_status.setText("正在断开全部视频流…")
                event.ignore()
                return
            self._close_timer.stop()
            event.accept()

    class LiveWallPickerDialog(QDialog):
        """Choose which cameras to place on the four-stream live wall."""

        MAX_STREAMS = 4

        def __init__(
            self,
            options: Sequence[tuple[str, str, str]],
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("选择多路画面")
            self.setModal(True)
            self.resize(680, 500)
            self.setMinimumSize(560, 420)
            selected_camera_ids: tuple[str, ...] = ()
            self.selected_camera_ids = selected_camera_ids
            layout = QVBoxLayout(self)
            layout.setContentsMargins(22, 20, 22, 18)
            layout.setSpacing(12)
            title = QLabel("选择要同时查看的摄像头")
            title.setObjectName("sectionTitle")
            self.selection_hint = QLabel("最多选择 4 路，当前筛选结果中共有可播放摄像头。")
            self.selection_hint.setObjectName("cardMuted")
            self.selection_hint.setWordWrap(True)
            self.camera_list = QListWidget()
            self.camera_list.setObjectName("selectionList")
            self.camera_list.setSelectionMode(QListWidget.NoSelection)
            self._updating = False
            for index, (camera_id, name, detail) in enumerate(options):
                item = QListWidgetItem(f"{name}    {detail}")
                item.setData(Qt.UserRole, camera_id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if index < self.MAX_STREAMS else Qt.Unchecked)
                self.camera_list.addItem(item)
            self.camera_list.itemChanged.connect(self._on_item_changed)
            layout.addWidget(title)
            layout.addWidget(self.selection_hint)
            layout.addWidget(self.camera_list, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("打开画面墙")
            buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self._accept)
            self.buttons = buttons
            layout.addWidget(buttons)
            self._update_selection_state()

        def checked_ids(self) -> tuple[str, ...]:
            return tuple(
                str(item.data(Qt.UserRole))
                for index in range(self.camera_list.count())
                if (item := self.camera_list.item(index)).checkState() == Qt.Checked
            )

        def _on_item_changed(self, _item: QListWidgetItem) -> None:
            if self._updating:
                return
            selected = self.checked_ids()
            over_limit = len(selected) > self.MAX_STREAMS
            if over_limit:
                self._updating = True
                try:
                    for index in range(self.camera_list.count()):
                        item = self.camera_list.item(index)
                        if (
                            item.checkState() == Qt.Checked
                            and str(item.data(Qt.UserRole)) not in selected[: self.MAX_STREAMS]
                        ):
                            item.setCheckState(Qt.Unchecked)
                finally:
                    self._updating = False
            self._update_selection_state()
            if over_limit:
                self.selection_hint.setText("最多只能选择 4 路，已保留前 4 个勾选项。")

        def _update_selection_state(self) -> None:
            count = len(self.checked_ids())
            if count <= self.MAX_STREAMS:
                self.selection_hint.setText(f"最多选择 4 路，当前已选择 {count} 路。")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(1 <= count <= self.MAX_STREAMS)

        def _accept(self) -> None:
            selected = self.checked_ids()
            if not 1 <= len(selected) <= self.MAX_STREAMS:
                self.selection_hint.setText("请选择 1 至 4 路摄像头。")
                return
            self.selected_camera_ids = selected
            self.accept()

    class CameraPreviewWorker(QThread):
        """One-shot camera decode thread which exits when the stream ends."""

        frame_ready = Signal(QImage)
        state_changed = Signal(str, bool)

        def __init__(
            self,
            url: str,
            parent: QObject | None = None,
            *,
            fps: float = 8.0,
            width: int = 1280,
            height: int = 720,
        ) -> None:
            super().__init__()
            self.url = url
            self.fps = fps
            self.width = width
            self.height = height
            self._stop = threading.Event()

        def request_stop(self) -> None:
            self._stop.set()

        def run(self) -> None:
            try:
                ffmpeg = require_ffmpeg()
                capture = RTSPCapture(
                    ffmpeg,
                    self.url,
                    width=self.width,
                    height=self.height,
                    fps=self.fps,
                )
                self.state_changed.emit("正在接收视频流", True)
                with capture.stream(timeout=6.0) as frames:
                    for frame in frames:
                        if self._stop.is_set():
                            break
                        self.frame_ready.emit(numpy_to_qimage(frame.image))
                if not self._stop.is_set():
                    self.state_changed.emit("视频流已结束", False)
            except Exception as exc:
                if not self._stop.is_set():
                    self.state_changed.emit(f"无法播放：{summarise_media_error(str(exc))}", False)

    class StreamHealthWorker(QObject):
        progress = Signal(int, int, str)
        finished = Signal(dict)

        def __init__(self, *, db_path: str, stream_specs: tuple[tuple[str, str], ...]) -> None:
            super().__init__()
            self.db_path = db_path
            self.stream_specs = stream_specs
            self._stop = threading.Event()

        def request_stop(self) -> None:
            self._stop.set()

        @Slot()
        def run(self) -> None:
            statuses: dict[str, str] = {}
            ffmpeg = find_ffmpeg()
            if ffmpeg is None:
                store: SQLiteStore | None = None
                try:
                    if self.db_path != ":memory:":
                        store = SQLiteStore(self.db_path)
                        for stream_id, _url in self.stream_specs:
                            store.set_stream_health(
                                stream_id,
                                "unknown",
                                "未找到 FFmpeg，无法确认视频流状态",
                                actor="merchant-console",
                            )
                finally:
                    if store is not None:
                        store.close()
                self.finished.emit({stream_id: "unknown" for stream_id, _url in self.stream_specs})
                return
            store: SQLiteStore | None = None
            try:
                store = SQLiteStore(self.db_path) if self.db_path != ":memory:" else None
                total = len(self.stream_specs)
                for index, (stream_id, url) in enumerate(self.stream_specs, start=1):
                    if self._stop.is_set():
                        break
                    self.progress.emit(index, total, stream_id)
                    status = "offline"
                    detail = "未取得视频分辨率"
                    try:
                        resolution = ffmpeg.probe_resolution(url, timeout=4.0)
                        if resolution is not None:
                            status = "online"
                            detail = f"{resolution[0]}×{resolution[1]}"
                        else:
                            # Some cameras do not answer ffprobe reliably.  A
                            # single decoded frame is the final health signal.
                            frame = RTSPCapture(
                                ffmpeg, url, width=640, height=360, fps=1.0
                            ).capture_one(timeout=5.0)
                            height, width = frame.image.shape[:2]
                            status = "online"
                            detail = f"{width}×{height}"
                    except Exception as exc:
                        detail = summarise_media_error(str(exc))[:260]
                    statuses[stream_id] = status
                    if store is not None:
                        store.set_stream_health(stream_id, status, detail, actor="merchant-console")
            finally:
                if store is not None:
                    store.close()
                self.finished.emit(statuses)

    class OnvifDiscoveryWorker(QObject):
        finished = Signal(object)

        def __init__(self) -> None:
            super().__init__()

        @Slot()
        def run(self) -> None:
            try:
                from factoryguard.devices.onvif import OnvifDiscovery

                self.finished.emit(tuple(OnvifDiscovery().discover(timeout=1.5)))
            except Exception as exc:
                self.finished.emit(exc)

    class DeviceStreamResolveWorker(QObject):
        """Resolve a playable URI for a standards-based camera."""

        finished = Signal(object)

        def __init__(
            self,
            *,
            db_path: str,
            device_id: str,
            channel_id: str = "",
            gb_platform: Any = None,
            config_path: str = "",
        ) -> None:
            super().__init__()
            self.db_path = db_path
            self.device_id = device_id
            self.channel_id = channel_id
            self.gb_platform = gb_platform
            self.config_path = config_path

        @Slot()
        def run(self) -> None:
            store: SQLiteStore | None = None
            adapter = None
            keep_adapter_open = False
            try:
                from factoryguard.app.device_manager import DeviceManager

                if self.db_path == ":memory:":
                    raise RuntimeError("临时数据库无法在后台解析设备码流")
                store = SQLiteStore(self.db_path)
                topology = store.load_topology()
                device = topology.get_device(self.device_id)
                credential_store = AppConfigStore(self.config_path or None)
                manager = DeviceManager(
                    store,
                    vault=PlaintextCredentialVault(),
                    gb_platform=self.gb_platform,
                    credential_store=credential_store,
                )
                adapter = manager.adapter_for(device)
                result = adapter.probe(timeout=6.0)
                if not result.ok:
                    raise RuntimeError(result.detail or "设备连接失败")
                channels = adapter.list_channels()
                if not channels:
                    raise RuntimeError("设备没有可用的视频通道")
                channel = next(
                    (item for item in channels if item.id == self.channel_id), channels[0]
                )
                live = adapter.get_live_stream(channel.id, role="sub")
                keep_adapter_open = True
                self.finished.emit(ResolvedStream(uri=live.uri, adapter=adapter))
            except Exception as exc:
                self.finished.emit(ResolvedStream(uri="", error=str(exc)))
            finally:
                if adapter is not None and not keep_adapter_open:
                    adapter.close()
                if store is not None:
                    store.close()

    class QuickIncidentDialog(QDialog):
        def __init__(self, context: Any, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("上报险情")
            self.setModal(True)
            self.setMinimumWidth(560)
            self.context = context
            self.incident: Incident | None = None
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(13)
            heading = QLabel("快速上报险情")
            heading.setObjectName("dialogTitle")
            hint = QLabel("只需描述发生了什么。平台会记录时间、区域和处置状态。")
            hint.setObjectName("dialogHint")
            layout.addWidget(heading)
            layout.addWidget(hint)
            form = QFormLayout()
            form.setSpacing(11)
            self.title_edit = QLineEdit()
            self.title_edit.setPlaceholderText("例如：仓库北门有人闯入")
            self.description_edit = QPlainTextEdit()
            self.description_edit.setPlaceholderText("补充现场情况、人员安全、是否需要报警等")
            self.description_edit.setMaximumHeight(100)
            self.severity_combo = QComboBox()
            for severity in (
                IncidentSeverity.HIGH,
                IncidentSeverity.CRITICAL,
                IncidentSeverity.MEDIUM,
                IncidentSeverity.LOW,
            ):
                self.severity_combo.addItem(SEVERITY_LABELS[severity], severity.value)
            self.severity_combo.setCurrentIndex(self.severity_combo.findData("high"))
            self.area_combo = QComboBox()
            self.area_combo.addItem("未指定区域", "")
            for area in context.topology.areas:
                self.area_combo.addItem(_area_option_label(context.topology, area), area.id)
            self.camera_combo = QComboBox()
            self.camera_combo.addItem("未指定摄像头", "")
            for camera in context.cameras:
                self.camera_combo.addItem(_camera_option_label(camera), camera.camera.id)
            self.area_combo.currentIndexChanged.connect(self._filter_cameras)
            form.addRow("发生了什么 *", self.title_edit)
            form.addRow("严重程度", self.severity_combo)
            form.addRow("所在区域", self.area_combo)
            form.addRow("关联摄像头", self.camera_combo)
            form.addRow("补充说明", self.description_edit)
            layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("提交险情")
            buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self._accept)
            layout.addWidget(buttons)

        def _filter_cameras(self) -> None:
            area_id = str(self.area_combo.currentData() or "")
            self.camera_combo.clear()
            self.camera_combo.addItem("未指定摄像头", "")
            for camera in self.context.cameras:
                if not area_id or camera.area.id == area_id:
                    self.camera_combo.addItem(_camera_option_label(camera), camera.camera.id)

        def _accept(self) -> None:
            title = self.title_edit.text().strip()
            if not title:
                QMessageBox.warning(self, "缺少信息", "请填写发生了什么。")
                return
            area_id = str(self.area_combo.currentData() or "")
            camera_id = str(self.camera_combo.currentData() or "")
            if camera_id and not area_id:
                # A camera always belongs to one area; keeping the incident
                # without that area would hide it from area-based reporting.
                camera = next(
                    (item for item in self.context.cameras if item.camera.id == camera_id),
                    None,
                )
                if camera is not None:
                    area_id = camera.area.id
            site_id = next(
                (area.site_id for area in self.context.topology.areas if area.id == area_id), None
            )
            self.incident = Incident.create(
                title=title,
                description=self.description_edit.toPlainText().strip(),
                severity=str(self.severity_combo.currentData()),
                site_id=site_id,
                area_id=area_id or None,
                camera_id=camera_id or None,
                site_name=(self.context.topology.get_site(site_id).name if site_id else ""),
                area_name=next(
                    (
                        item.area.name
                        for item in self.context.cameras
                        if item.camera.id == camera_id
                    ),
                    "",
                ),
                camera_name=next(
                    (
                        item.camera.name
                        for item in self.context.cameras
                        if item.camera.id == camera_id
                    ),
                    "",
                ),
                source="user",
                reporter="当前用户",
            )
            self.accept()

    class SiteDialog(QDialog):
        """Create or rename a managed site (for example a shop branch)."""

        def __init__(
            self,
            topology: EntityRegistry,
            parent: QWidget | None = None,
            *,
            site: Site | None = None,
        ) -> None:
            super().__init__(parent)
            self._editing = site
            self.setWindowTitle("编辑场所" if site else "新建场所")
            self.setModal(True)
            self.setMinimumWidth(480)
            self.topology = topology
            self.site: Site | None = None
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(13)
            heading = QLabel("编辑场所" if site else "新建场所")
            heading.setObjectName("dialogTitle")
            hint = QLabel(
                "修改名称。"
                if site
                else "按实际经营的门店、仓库或园区划分场所，便于分别管理摄像头和险情。"
            )
            hint.setObjectName("dialogHint")
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            form = QFormLayout()
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("例如：一号店、星河园区")
            if site is not None:
                self.name_edit.setText(site.name)
            form.addRow("场所名称 *", self.name_edit)
            layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("保存修改" if site else "创建场所")
            buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self._accept)
            layout.addWidget(buttons)

        def _accept(self) -> None:
            name = self.name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "缺少信息", "请输入场所名称。")
                return
            duplicate = next(
                (
                    item
                    for item in self.topology.sites
                    if item.name.casefold() == name.casefold()
                    and (self._editing is None or item.id != self._editing.id)
                ),
                None,
            )
            if duplicate is not None:
                QMessageBox.warning(self, "场所名称重复", "已存在同名场所，请使用其他名称。")
                return
            if self._editing is not None:
                self.site = replace(self._editing, name=name)
            else:
                self.site = Site(f"site-{uuid4().hex[:10]}", name)
            self.accept()

    class AreaDialog(QDialog):
        def __init__(
            self,
            topology: EntityRegistry,
            parent: QWidget | None = None,
            *,
            area: Area | None = None,
        ) -> None:
            super().__init__(parent)
            self._editing = area
            self.setWindowTitle("编辑区域" if area else "新增区域")
            self.setModal(True)
            self.setMinimumWidth(500)
            self.topology = topology
            self.site: Site | None = None
            self.area: Area | None = None
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(13)
            heading = QLabel("编辑管理区域" if area else "新增管理区域")
            heading.setObjectName("dialogTitle")
            hint = QLabel(
                "修改区域名称。"
                if area
                else "可以按门店、仓库、停车场、出入口等实际位置划分，便于快速定位险情。"
            )
            hint.setObjectName("dialogHint")
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            form = QFormLayout()
            self.site_combo = QComboBox()
            self.site_combo.setEditable(True)
            if topology.sites:
                for site in topology.sites:
                    self.site_combo.addItem(site.name, site.id)
            else:
                self.site_combo.addItem("")
                line_edit = self.site_combo.lineEdit()
                if line_edit is not None:
                    line_edit.setPlaceholderText("请输入场所名称")
            if area is not None:
                index = self.site_combo.findData(area.site_id)
                if index >= 0:
                    self.site_combo.setCurrentIndex(index)
                self.site_combo.setEnabled(False)
            self.area_edit = QLineEdit()
            self.area_edit.setPlaceholderText("例如：成品仓库、东门、停车场")
            if area is not None:
                self.area_edit.setText(area.name)
            form.addRow("所属场所", self.site_combo)
            form.addRow("区域名称 *", self.area_edit)
            layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("保存修改" if area else "创建区域")
            buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self._accept)
            layout.addWidget(buttons)

        def _accept(self) -> None:
            name = self.area_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "缺少信息", "请输入区域名称。")
                return
            if self._editing is not None:
                site = self.topology.get_site(self._editing.site_id)
                duplicate = next(
                    (
                        item
                        for item in self.topology.areas
                        if item.site_id == site.id
                        and item.id != self._editing.id
                        and item.name.casefold() == name.casefold()
                    ),
                    None,
                )
                if duplicate is not None:
                    QMessageBox.warning(
                        self, "区域名称重复", "该场所已存在同名区域，请使用其他名称。"
                    )
                    return
                self.site = site
                self.area = replace(self._editing, name=name)
                self.accept()
                return
            site_name = self.site_combo.currentText().strip()
            existing_id = self.site_combo.currentData()
            site = next(
                (
                    item
                    for item in self.topology.sites
                    if item.id == existing_id or item.name == site_name
                ),
                None,
            )
            if site is None and not site_name:
                QMessageBox.warning(self, "缺少信息", "请输入场所名称。")
                return
            if site is None:
                site = Site(f"site-{uuid4().hex[:10]}", site_name)
            duplicate = next(
                (
                    item
                    for item in self.topology.areas
                    if item.site_id == site.id and item.name.casefold() == name.casefold()
                ),
                None,
            )
            if duplicate is not None:
                QMessageBox.warning(self, "区域名称重复", "该场所已存在同名区域，请使用其他名称。")
                return
            self.site = site
            self.area = Area(f"area-{uuid4().hex[:10]}", site.id, name)
            self.accept()

    class CameraAccessDialog(QDialog):
        def __init__(
            self,
            topology: EntityRegistry,
            *,
            parent: QWidget | None = None,
            preferred_area_id: str | None = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("添加摄像头")
            self.setModal(True)
            self.setMinimumWidth(560)
            self.setMaximumWidth(680)
            self.resize(620, 520)
            self.topology = topology
            self.device: VideoDevice | None = None
            self.camera: CameraDevice | None = None
            self.stream: VideoStream | None = None
            self.credentials = DeviceCredentials()
            self._probe_thread: QThread | None = None
            self._probe_worker: StreamProbeWorker | None = None
            self._onvif_probe_worker: OnvifProbeWorker | None = None
            self._pending_reject = False
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(13)
            heading = QLabel("添加摄像头")
            heading.setObjectName("dialogTitle")
            hint = QLabel(
                "支持 ONVIF 发现、GB/T 28181 国标注册和 RTSP 手动地址。平台接入实时视频与设备原生告警。"
            )
            hint.setObjectName("dialogHint")
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            form = QFormLayout()
            form.setSpacing(10)
            self.form = form
            self.area_combo = QComboBox()
            for area in topology.areas:
                try:
                    site = topology.get_site(area.site_id)
                except KeyError:
                    site = None
                label = f"{site.name} / {area.name}" if site else area.name
                self.area_combo.addItem(label, area.id)
            if preferred_area_id:
                index = self.area_combo.findData(preferred_area_id)
                if index >= 0:
                    self.area_combo.setCurrentIndex(index)
            self.protocol_combo = QComboBox()
            self.protocol_combo.addItem("ONVIF 摄像头（推荐）", DeviceProtocol.ONVIF.value)
            self.protocol_combo.addItem("GB/T 28181 国标设备", DeviceProtocol.GB28181.value)
            self.protocol_combo.addItem("RTSP / NVR 手动地址", DeviceProtocol.RTSP.value)
            self.protocol_combo.currentIndexChanged.connect(self._update_protocol_hint)
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("例如：仓库北门摄像头")
            self.endpoint_edit = QLineEdit()
            self.endpoint_edit.setPlaceholderText("例如：http://192.168.1.64/onvif/device_service")
            self.device_id_edit = QLineEdit()
            self.device_id_edit.setPlaceholderText(
                "国标设备编号，例如 34020000001320000001（可选）"
            )
            self.channel_edit = QLineEdit()
            self.channel_edit.setPlaceholderText("例如 ch1")
            self.stream_name_edit = QLineEdit()
            self.stream_name_edit.setPlaceholderText("例如 主码流或子码流")
            self.username_edit = QLineEdit()
            self.username_edit.setPlaceholderText("设备账号（可选）")
            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setPlaceholderText("设备密码（可选）")
            form.addRow("所属区域 *", self.area_combo)
            form.addRow("接入方式", self.protocol_combo)
            form.addRow("摄像头名称 *", self.name_edit)
            form.addRow("设备地址 *", self.endpoint_edit)
            form.addRow("国标设备编号", self.device_id_edit)
            form.addRow("通道编号", self.channel_edit)
            form.addRow("码流名称", self.stream_name_edit)
            form.addRow("设备账号", self.username_edit)
            form.addRow("设备密码", self.password_edit)
            layout.addLayout(form)
            self.protocol_hint = QLabel()
            self.protocol_hint.setObjectName("cardMuted")
            self.protocol_hint.setWordWrap(True)
            layout.addWidget(self.protocol_hint)
            self.probe_status = QLabel("")
            self.probe_status.setObjectName("muted")
            self.probe_status.setWordWrap(True)
            layout.addWidget(self.probe_status)
            actions = QHBoxLayout()
            self.test_button = QPushButton("测试连接")
            self.test_button.setObjectName("secondary")
            self.test_button.clicked.connect(self._test_connection)
            actions.addWidget(self.test_button)
            actions.addStretch(1)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("保存摄像头")
            buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self._accept)
            actions.addWidget(buttons)
            layout.addLayout(actions)
            self._update_protocol_hint()
            if not topology.areas:
                self.test_button.setEnabled(False)

        def _update_protocol_hint(self) -> None:
            protocol = str(self.protocol_combo.currentData())
            if protocol == DeviceProtocol.ONVIF.value:
                self.endpoint_edit.setPlaceholderText("http://192.168.1.64/onvif/device_service")
                self.protocol_hint.setText(
                    "ONVIF：填写摄像头 ONVIF 服务地址和设备账号，可先点“测试连接”验证，再保存接入。"
                )
                self._set_row_visible(self.device_id_edit, False)
                self._set_row_visible(self.channel_edit, False)
                self._set_row_visible(self.stream_name_edit, False)
                self._set_row_visible(self.endpoint_edit, True)
                self._set_row_visible(self.username_edit, True)
                self._set_row_visible(self.password_edit, True)
            elif protocol == DeviceProtocol.GB28181.value:
                self.endpoint_edit.setPlaceholderText("gb28181://34020000001320000001")
                self.protocol_hint.setText(
                    "GB/T 28181：先在系统设置启用国标服务。填写已注册设备的国标编号和通道即可。"
                )
                self._set_row_visible(self.device_id_edit, True)
                self._set_row_visible(self.channel_edit, True)
                self._set_row_visible(self.stream_name_edit, False)
                self._set_row_visible(self.endpoint_edit, False)
                self._set_row_visible(self.username_edit, False)
                self._set_row_visible(self.password_edit, False)
            else:
                self.endpoint_edit.setPlaceholderText("rtsp://user:password@camera-or-nvr/live")
                self.protocol_hint.setText(
                    "RTSP：适合已有 NVR 或支持标准 RTSP 地址的摄像头，保存后可直接查看画面。"
                )
                self._set_row_visible(self.device_id_edit, False)
                self._set_row_visible(self.channel_edit, False)
                self._set_row_visible(self.stream_name_edit, True)
                self._set_row_visible(self.endpoint_edit, True)
                self._set_row_visible(self.username_edit, True)
                self._set_row_visible(self.password_edit, True)
            is_rtsp = protocol == DeviceProtocol.RTSP.value
            is_onvif = protocol == DeviceProtocol.ONVIF.value
            self.test_button.setVisible(is_rtsp or is_onvif)
            self.test_button.setEnabled(is_rtsp and self._is_rtsp() and bool(self.topology.areas))
            QTimer.singleShot(0, self.adjustSize)

        def _set_row_visible(self, field: QWidget, visible: bool) -> None:
            field.setVisible(visible)
            label = self.form.labelForField(field)
            if label is not None:
                label.setVisible(visible)

        def _is_rtsp(self) -> bool:
            return self.endpoint_edit.text().strip().lower().startswith(("rtsp://", "rtsps://"))

        def _test_connection(self) -> None:
            protocol = str(self.protocol_combo.currentData())
            if protocol == DeviceProtocol.ONVIF.value:
                self._test_onvif_connection()
                return
            url = self.endpoint_edit.text().strip()
            if not self._is_rtsp():
                self.probe_status.setText("国标设备会在保存后由平台设备服务自动检测。")
                return
            if not url:
                self.probe_status.setText("请先填写 RTSP 地址。")
                return
            self.test_button.setEnabled(False)
            self.probe_status.setText("正在连接摄像头…")
            self._probe_thread = QThread(self)
            self._probe_worker = StreamProbeWorker(url=url, width=640, height=360, timeout=10.0)
            self._probe_worker.moveToThread(self._probe_thread)
            self._probe_thread.started.connect(self._probe_worker.run)
            self._probe_worker.finished.connect(self._on_probe_finished)
            self._probe_worker.finished.connect(self._probe_thread.quit)
            self._probe_worker.finished.connect(self._probe_worker.deleteLater)
            self._probe_thread.finished.connect(self._probe_thread.deleteLater)
            self._probe_thread.start()

        def _test_onvif_connection(self) -> None:
            endpoint = self.endpoint_edit.text().strip()
            if not endpoint:
                self.probe_status.setText("请先填写 ONVIF 设备地址。")
                return
            self.test_button.setEnabled(False)
            self.probe_status.setText("正在连接 ONVIF 设备…")
            self._probe_thread = QThread(self)
            self._onvif_probe_worker = OnvifProbeWorker(
                endpoint=endpoint,
                username=self.username_edit.text().strip(),
                password=self.password_edit.text(),
            )
            self._probe_worker = self._onvif_probe_worker
            self._onvif_probe_worker.moveToThread(self._probe_thread)
            self._probe_thread.started.connect(self._onvif_probe_worker.run)
            self._onvif_probe_worker.finished.connect(self._on_probe_finished)
            self._onvif_probe_worker.finished.connect(self._probe_thread.quit)
            self._onvif_probe_worker.finished.connect(self._onvif_probe_worker.deleteLater)
            self._probe_thread.finished.connect(self._probe_thread.deleteLater)
            self._probe_thread.start()

        def _on_probe_finished(self, ok: bool, detail: str) -> None:
            self.test_button.setEnabled(True)
            self.probe_status.setText(("连接成功 · " if ok else "连接失败 · ") + detail)
            self._probe_worker = None
            self._onvif_probe_worker = None
            self._probe_thread = None
            if self._pending_reject:
                self._pending_reject = False
                QDialog.reject(self)

        def _accept(self) -> None:
            try:
                if not self.topology.areas:
                    raise ValueError("请先创建一个区域")
                area_id = str(self.area_combo.currentData())
                area = next(area for area in self.topology.areas if area.id == area_id)
                name = self.name_edit.text().strip()
                device_number = self.device_id_edit.text().strip()
                protocol = DeviceProtocol(str(self.protocol_combo.currentData()))
                raw_endpoint = self.endpoint_edit.text().strip()
                if (
                    protocol is DeviceProtocol.GB28181
                    and device_number
                    and (not raw_endpoint or raw_endpoint.startswith("gb28181://"))
                ):
                    raw_endpoint = f"gb28181://{device_number}"
                if not name:
                    raise ValueError("请填写摄像头名称")
                if not raw_endpoint:
                    raise ValueError("请填写设备地址或设备编号")
                endpoint, url_username, url_password = _split_url_credentials(raw_endpoint)
                channel_number = self.channel_edit.text().strip()
                stream_name = self.stream_name_edit.text().strip()
                if protocol is DeviceProtocol.ONVIF and not endpoint.lower().startswith(
                    ("http://", "https://", "rtsp://", "rtsps://")
                ):
                    raise ValueError("ONVIF 地址应为 http://、https:// 或 RTSP 地址")
                if protocol is DeviceProtocol.GB28181 and not endpoint.lower().startswith(
                    ("gb28181://", "sip://", "rtsp://", "rtsps://")
                ):
                    raise ValueError("GB/T 28181 设备地址应为 gb28181://设备编号 或 RTSP 地址")
                if protocol is DeviceProtocol.RTSP and not self._is_rtsp():
                    raise ValueError("RTSP 接入地址应以 rtsp:// 或 rtsps:// 开头")
                if protocol is DeviceProtocol.GB28181 and not channel_number:
                    raise ValueError("请填写国标设备通道编号")
                if protocol is DeviceProtocol.RTSP and not stream_name:
                    raise ValueError("请填写 RTSP 码流名称")
                device_id = f"device-{uuid4().hex[:12]}"
                device = VideoDevice(
                    id=device_id,
                    site_id=area.site_id,
                    area_id=area.id,
                    name=name,
                    protocol=protocol,
                    endpoint=endpoint,
                    device_id=device_number,
                    analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                )
                camera = CameraDevice(
                    id=f"camera-{uuid4().hex[:12]}",
                    area_id=area.id,
                    name=name,
                    address=endpoint,
                    device_id=device.id,
                    channel_id=channel_number or "ch1",
                )
                stream = None
                if self._is_rtsp():
                    stream = VideoStream(
                        id=f"stream-{uuid4().hex[:12]}",
                        camera_id=camera.id,
                        name=stream_name,
                        url=endpoint,
                        role=StreamRole.SUB,
                        fps=2.0,
                        analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                        device_id=device.id,
                    )
                self.device = device
                self.camera = camera
                self.stream = stream
                self.credentials = DeviceCredentials(
                    username=self.username_edit.text().strip() or url_username,
                    password=self.password_edit.text() or url_password,
                )
            except (KeyError, StopIteration, TypeError, ValueError) as exc:
                QMessageBox.warning(self, "摄像头信息不完整", str(exc))
                return
            self.accept()

        def reject(self) -> None:
            if self._probe_worker is not None:
                self._pending_reject = True
                self._probe_worker.request_stop()
                self.probe_status.setText("正在取消连接测试…")
                return
            super().reject()

    class CameraEditDialog(QDialog):
        """Edit connection and placement fields without recreating the camera."""

        def __init__(
            self,
            topology: EntityRegistry,
            camera: CameraView,
            *,
            allow_area_change: bool = True,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.topology = topology
            self.camera_view = camera
            self._shared_device = not allow_area_change
            self.device: VideoDevice | None = None
            self.camera: CameraDevice | None = None
            self.stream: VideoStream | None = None
            self.credentials: DeviceCredentials | None = None
            self.setWindowTitle(f"编辑摄像头 · {camera.camera.name}")
            self.setModal(True)
            self.setMinimumWidth(560)
            self.setMaximumWidth(680)
            self.resize(620, 500)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(13)
            heading = QLabel("编辑摄像头")
            heading.setObjectName("dialogTitle")
            hint = QLabel("修改名称、区域或连接参数；设备密码留空时保持现有凭据不变。")
            hint.setObjectName("dialogHint")
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            form = QFormLayout()
            form.setSpacing(10)
            self.form = form
            self.area_combo = QComboBox()
            for area in topology.areas:
                try:
                    site = topology.get_site(area.site_id)
                except KeyError:
                    site = None
                label = f"{site.name} / {area.name}" if site else area.name
                self.area_combo.addItem(label, area.id)
            index = self.area_combo.findData(camera.camera.area_id)
            if index >= 0:
                self.area_combo.setCurrentIndex(index)
            self.area_combo.setEnabled(allow_area_change)
            if not allow_area_change:
                self.area_combo.setToolTip("同一物理设备存在多个通道，不能单独迁移区域")
            protocol_label = QLabel(camera.protocol_label)
            protocol_label.setObjectName("muted")
            self.name_edit = QLineEdit(camera.camera.name)
            self.endpoint_edit = QLineEdit(
                camera.device.endpoint if camera.device else camera.camera.address
            )
            self.device_id_edit = QLineEdit(camera.device.device_id if camera.device else "")
            if not allow_area_change:
                self.endpoint_edit.setReadOnly(True)
                self.endpoint_edit.setToolTip("共享物理设备的地址需通过设备管理统一修改")
                self.device_id_edit.setReadOnly(True)
                self.device_id_edit.setToolTip("共享物理设备的编号需通过设备管理统一修改")
            self.channel_edit = QLineEdit(camera.camera.channel_id)
            self.stream_name_edit = QLineEdit(
                camera.best_stream.name if camera.best_stream else "主码流"
            )
            self.username_edit = QLineEdit()
            self.username_edit.setPlaceholderText("留空保持现有账号")
            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setPlaceholderText("留空保持现有密码")
            form.addRow("所属区域", self.area_combo)
            form.addRow("接入方式", protocol_label)
            form.addRow("摄像头名称 *", self.name_edit)
            form.addRow("设备地址 *", self.endpoint_edit)
            form.addRow("国标设备编号", self.device_id_edit)
            form.addRow("通道编号", self.channel_edit)
            form.addRow("码流名称", self.stream_name_edit)
            form.addRow("设备账号", self.username_edit)
            form.addRow("设备密码", self.password_edit)
            layout.addLayout(form)
            layout.addStretch(1)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("保存修改")
            buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
            buttons.button(QDialogButtonBox.Cancel).setText("取消")
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self._accept)
            layout.addWidget(buttons)
            self._update_protocol_fields()

        def _set_row_visible(self, widget: QWidget, visible: bool) -> None:
            widget.setVisible(visible)
            label = self.form.labelForField(widget)
            if label is not None:
                label.setVisible(visible)

        def _update_protocol_fields(self) -> None:
            protocol = self.camera_view.protocol
            is_gb = protocol == "gb28181"
            is_rtsp = protocol == "rtsp"
            is_direct_stream = (
                self.endpoint_edit.text().strip().lower().startswith(("rtsp://", "rtsps://"))
            )
            self._set_row_visible(self.endpoint_edit, not is_gb or bool(self.endpoint_edit.text()))
            self._set_row_visible(self.device_id_edit, is_gb)
            self._set_row_visible(self.channel_edit, is_gb)
            self._set_row_visible(self.stream_name_edit, is_rtsp or is_direct_stream)

        def _accept(self) -> None:
            try:
                if self.area_combo.count() == 0:
                    raise ValueError("没有可用区域")
                area_id = str(self.area_combo.currentData())
                area = self.topology.get_area(area_id)
                name = self.name_edit.text().strip()
                if not name:
                    raise ValueError("请填写摄像头名称")
                protocol = self.camera_view.protocol
                device_number = self.device_id_edit.text().strip()
                raw_endpoint = self.endpoint_edit.text().strip()
                if (
                    protocol == "gb28181"
                    and device_number
                    and (not raw_endpoint or raw_endpoint.startswith("gb28181://"))
                ):
                    raw_endpoint = f"gb28181://{device_number}"
                if not raw_endpoint:
                    raise ValueError("请填写设备地址或设备编号")
                endpoint, url_username, url_password = _split_url_credentials(raw_endpoint)
                channel_number = self.channel_edit.text().strip() or "ch1"
                stream_name = self.stream_name_edit.text().strip()
                if protocol == "onvif" and not endpoint.lower().startswith(
                    ("http://", "https://", "rtsp://", "rtsps://")
                ):
                    raise ValueError("ONVIF 地址应为 http://、https:// 或 RTSP 地址")
                if protocol == "gb28181" and not endpoint.lower().startswith(
                    ("gb28181://", "sip://", "rtsp://", "rtsps://")
                ):
                    raise ValueError("GB/T 28181 设备地址格式不正确")
                if protocol == "rtsp" and not endpoint.lower().startswith(("rtsp://", "rtsps://")):
                    raise ValueError("RTSP 接入地址应以 rtsp:// 或 rtsps:// 开头")
                if protocol == "gb28181" and not device_number:
                    raise ValueError("请填写国标设备编号")
                if protocol == "rtsp" and not stream_name:
                    raise ValueError("请填写 RTSP 码流名称")
                existing_device = self.camera_view.device
                device_id = (
                    existing_device.id
                    if existing_device is not None
                    else f"device-{self.camera_view.camera.id}"
                )
                if existing_device is not None:
                    endpoint_changed = endpoint != existing_device.endpoint
                    device = replace(
                        existing_device,
                        site_id=area.site_id,
                        area_id=area.id,
                        name=existing_device.name if self._shared_device else name,
                        endpoint=existing_device.endpoint if self._shared_device else endpoint,
                        device_id=(
                            existing_device.device_id if self._shared_device else device_number
                        ),
                        health_status="unknown"
                        if endpoint_changed
                        else existing_device.health_status,
                        health_detail=(
                            "设备地址已更新，等待重新检测"
                            if endpoint_changed
                            else existing_device.health_detail
                        ),
                        last_probe_at=None if endpoint_changed else existing_device.last_probe_at,
                        last_ok_at=None if endpoint_changed else existing_device.last_ok_at,
                    )
                else:
                    device = VideoDevice(
                        id=device_id,
                        site_id=area.site_id,
                        area_id=area.id,
                        name=name,
                        protocol=protocol,
                        endpoint=endpoint,
                        device_id=device_number,
                        analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                    )
                camera = replace(
                    self.camera_view.camera,
                    area_id=area.id,
                    name=name,
                    address=endpoint,
                    device_id=device.id,
                    channel_id=channel_number,
                )
                stream = None
                is_direct_stream = endpoint.lower().startswith(("rtsp://", "rtsps://"))
                if is_direct_stream:
                    existing_stream = self.camera_view.best_stream
                    effective_stream_name = stream_name or (
                        existing_stream.name if existing_stream is not None else "实时码流"
                    )
                    if existing_stream is None:
                        stream = VideoStream(
                            id=f"stream-{uuid4().hex[:12]}",
                            camera_id=camera.id,
                            name=effective_stream_name,
                            url=endpoint,
                            role=StreamRole.SUB,
                            fps=2.0,
                            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
                            device_id=device.id,
                        )
                    else:
                        stream = replace(
                            existing_stream,
                            camera_id=camera.id,
                            name=effective_stream_name,
                            url=endpoint,
                            device_id=device.id,
                        )
                username = self.username_edit.text().strip() or url_username
                password = self.password_edit.text() or url_password
                credentials = (
                    DeviceCredentials(username=username, password=password)
                    if username or password
                    else None
                )
                self.device = device
                self.camera = camera
                self.stream = stream
                self.credentials = credentials
            except (KeyError, StopIteration, TypeError, ValueError) as exc:
                QMessageBox.warning(self, "摄像头信息不完整", str(exc))
                return
            self.accept()

else:

    class LivePreviewDialog:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class LiveWallPickerDialog:  # pragma: no cover
        MAX_STREAMS = 4

        def __init__(self, *_: object, **__: object) -> None:
            require_qt()

    class LiveWallDialog:  # pragma: no cover
        MAX_STREAMS = 4

        def __init__(self, *_: object, **__: object) -> None:
            require_qt()

    class QuickIncidentDialog:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class SiteDialog:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class AreaDialog:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class CameraAccessDialog:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class CameraEditDialog:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class CameraPreviewWorker:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class StreamHealthWorker:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class OnvifDiscoveryWorker:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()

    class DeviceStreamResolveWorker:  # pragma: no cover
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = [
    "AreaDialog",
    "CameraAccessDialog",
    "CameraEditDialog",
    "CameraPreviewWorker",
    "DeviceStreamResolveWorker",
    "LivePreviewDialog",
    "LiveWallDialog",
    "LiveWallPickerDialog",
    "OnvifDiscoveryWorker",
    "QuickIncidentDialog",
    "ResolvedStream",
    "SiteDialog",
    "StreamHealthWorker",
    "_split_url_credentials",
]
