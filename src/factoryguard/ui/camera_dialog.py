"""Compact camera-and-stream onboarding dialog."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from factoryguard.devices.base import DeviceCredentials
from factoryguard.domain.models import AnalysisPolicy, CameraDevice, DeviceProtocol, StreamRole, VideoDevice, VideoStream

from .probe_worker import StreamProbeWorker

from .qt import HAS_QT, require_qt

if HAS_QT:
    from .qt import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QThread,
        QVBoxLayout,
    )


if HAS_QT:

    class CameraQuickAddDialog(QDialog):
        """Create one camera and its primary RTSP stream in one step."""

        def __init__(
            self,
            *,
            area_id: str,
            area_label: str,
            site_id: str = "site-unknown",
            default_model: str = "models/yolo11n.onnx",
            default_width: int = 1280,
            default_height: int = 720,
            default_input_size: int = 640,
            default_fps: float = 2.0,
            parent: Any = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("快速接入视频设备")
            self.setModal(True)
            self.resize(620, 500)
            self._site_id = site_id or "site-unknown"
            self._area_id = area_id
            self.camera: CameraDevice | None = None
            self.stream: VideoStream | None = None
            self.device: VideoDevice | None = None
            self.credentials = DeviceCredentials()
            self._probe_thread: QThread | None = None
            self._probe_worker: StreamProbeWorker | None = None
            self._probe_active = False
            self._pending_reject = False

            layout = QVBoxLayout(self)
            target = QLabel(f"添加到区域：{area_label}")
            target.setWordWrap(True)
            target.setObjectName("muted")
            layout.addWidget(target)
            form = QFormLayout()
            self.camera_name_edit = QLineEdit("新摄像头")
            self.device_id_edit = QLineEdit()
            self.device_id_edit.setPlaceholderText("国标设备编号（可选）")
            self.camera_address_edit = QLineEdit()
            self.camera_address_edit.setPlaceholderText("可选，例如：东门立柱、仓库西北角")
            self.username_edit = QLineEdit()
            self.username_edit.setPlaceholderText("设备账号（可选）")
            self.password_edit = QLineEdit()
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.password_edit.setPlaceholderText("设备密码（可选）")
            self.protocol_combo = QComboBox()
            for label, value in (("RTSP", "rtsp"), ("ONVIF", "onvif"), ("GB/T 28181", "gb28181")):
                self.protocol_combo.addItem(label, value)
            self.stream_name_edit = QLineEdit("子码流")
            self.url_edit = QLineEdit()
            self.url_edit.setPlaceholderText("rtsp://user:password@camera-or-nvr/live")
            self.role_combo = QComboBox()
            for label, value in (("主码流", "main"), ("子码流", "sub"), ("分析码流", "analysis")):
                self.role_combo.addItem(label, value)
            self.role_combo.setCurrentIndex(self.role_combo.findData("sub"))
            self.model_edit = QLineEdit(default_model)
            self.width_spin = QSpinBox()
            self.width_spin.setRange(160, 7680)
            self.width_spin.setValue(default_width)
            self.height_spin = QSpinBox()
            self.height_spin.setRange(120, 4320)
            self.height_spin.setValue(default_height)
            self.input_size_combo = QComboBox()
            for value in (320, 416, 640, 960):
                self.input_size_combo.addItem(f"{value} × {value}", value)
            self.input_size_combo.setCurrentIndex(max(0, self.input_size_combo.findData(default_input_size)))
            self.fps_spin = QDoubleSpinBox()
            self.fps_spin.setRange(0.5, 30.0)
            self.fps_spin.setSingleStep(0.5)
            self.fps_spin.setDecimals(1)
            self.fps_spin.setValue(default_fps)
            self.confidence_spin = QDoubleSpinBox()
            self.confidence_spin.setRange(0.01, 1.0)
            self.confidence_spin.setSingleStep(0.05)
            self.confidence_spin.setValue(0.25)
            for advanced in (self.model_edit, self.width_spin, self.height_spin, self.input_size_combo, self.fps_spin, self.confidence_spin):
                advanced.setVisible(False)
            self.enabled_check = QCheckBox("启用设备与视频流")
            self.enabled_check.setChecked(True)
            form.addRow("接入协议", self.protocol_combo)
            form.addRow("设备名称", self.camera_name_edit)
            form.addRow("设备编号", self.device_id_edit)
            form.addRow("安装位置", self.camera_address_edit)
            form.addRow("推流名称", self.stream_name_edit)
            form.addRow("视频地址", self.url_edit)
            form.addRow("设备账号", self.username_edit)
            form.addRow("设备密码", self.password_edit)
            probe_row = QHBoxLayout()
            self.test_connection_button = QPushButton("测试连接")
            self.probe_status_label = QLabel("尚未测试")
            self.probe_status_label.setWordWrap(True)
            self.probe_status_label.setObjectName("muted")
            probe_row.addWidget(self.test_connection_button)
            probe_row.addWidget(self.probe_status_label, 1)
            form.addRow("连接状态", probe_row)
            form.addRow("推流角色", self.role_combo)
            form.addRow("备用模型", self.model_edit)
            form.addRow("分析分辨率", self._size_row())
            form.addRow("模型输入尺寸", self.input_size_combo)
            form.addRow("分析帧率", self.fps_spin)
            form.addRow("默认置信度", self.confidence_spin)
            form.addRow("状态", self.enabled_check)
            for advanced in (self.model_edit, self.input_size_combo, self.fps_spin, self.confidence_spin):
                label = form.labelForField(advanced)
                if label is not None:
                    label.setVisible(False)
            layout.addLayout(form)
            hint = QLabel("保存后可直接在监控中心选择该推流；模型始终在本机 ONNX Runtime 中运行。")
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
            self.save_button = buttons.button(QDialogButtonBox.Save)
            buttons.accepted.connect(self._validate_and_accept)
            buttons.rejected.connect(self.reject)
            self.test_connection_button.clicked.connect(self._test_connection)
            layout.addWidget(buttons)

        def _size_row(self):
            from .qt import QHBoxLayout, QLabel

            row = QHBoxLayout()
            row.addWidget(QLabel("宽"))
            row.addWidget(self.width_spin)
            row.addWidget(QLabel("高"))
            row.addWidget(self.height_spin)
            return row

        def _test_connection(self) -> None:
            if self._probe_active:
                return
            url = self.url_edit.text().strip()
            if not url.lower().startswith(("rtsp://", "rtsps://")):
                self.probe_status_label.setText("RTSP 地址格式不正确")
                return
            try:
                self._probe_worker = StreamProbeWorker(
                    url=url,
                    width=self.width_spin.value(),
                    height=self.height_spin.value(),
                    fps=1.0,
                    timeout=12.0,
                )
            except (TypeError, ValueError) as exc:
                self.probe_status_label.setText(f"无法测试：{exc}")
                return
            self._probe_thread = QThread(self)
            self._probe_worker.moveToThread(self._probe_thread)
            self._probe_thread.started.connect(self._probe_worker.run)
            self._probe_worker.status_changed.connect(self.probe_status_label.setText)
            self._probe_worker.resolution_ready.connect(self._apply_probe_resolution)
            self._probe_worker.finished.connect(self._on_probe_finished)
            self._probe_worker.finished.connect(self._probe_thread.quit)
            self._probe_worker.finished.connect(self._probe_worker.deleteLater)
            self._probe_thread.finished.connect(self._clear_probe_thread)
            self._probe_thread.finished.connect(self._probe_thread.deleteLater)
            self._probe_active = True
            self.test_connection_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self.probe_status_label.setText("正在连接 RTSP...")
            self._probe_thread.start()

        def _apply_probe_resolution(self, width: int, height: int) -> None:
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)

        def _on_probe_finished(self, success: bool, detail: str) -> None:
            if success:
                message = detail if detail.startswith("连接成功") else f"连接成功 · {detail}"
            else:
                message = f"连接失败 · {detail}"
            self.probe_status_label.setText(message)
            self.test_connection_button.setEnabled(True)
            self.save_button.setEnabled(True)
            self._probe_active = False
            if self._pending_reject:
                self._pending_reject = False
                QDialog.reject(self)

        def _clear_probe_thread(self) -> None:
            self._probe_worker = None
            self._probe_thread = None

        def reject(self) -> None:
            if self._probe_active:
                self._pending_reject = True
                if self._probe_worker is not None:
                    self._probe_worker.request_stop()
                self.probe_status_label.setText("正在取消连接测试...")
                return
            super().reject()

        def _validate_and_accept(self) -> None:
            try:
                camera_name = self.camera_name_edit.text().strip()
                stream_name = self.stream_name_edit.text().strip()
                url = self.url_edit.text().strip()
                model = self.model_edit.text().strip()
                protocol = DeviceProtocol(str(self.protocol_combo.currentData() or "rtsp"))
                self.credentials = DeviceCredentials(
                    username=self.username_edit.text().strip(),
                    password=self.password_edit.text(),
                )
                if not camera_name:
                    raise ValueError("设备名称不能为空")
                if not url:
                    raise ValueError("设备地址不能为空")
                is_rtsp = url.lower().startswith(("rtsp://", "rtsps://"))
                if is_rtsp and not stream_name:
                    raise ValueError("视频流名称不能为空")
                if protocol is DeviceProtocol.ONVIF and not url.lower().startswith(("http://", "https://", "rtsp://", "rtsps://")):
                    raise ValueError("ONVIF 地址必须使用 HTTP、HTTPS 或 RTSP")
                if protocol is DeviceProtocol.GB28181 and not url.lower().startswith(("sip://", "gb28181://", "rtsp://", "rtsps://")):
                    raise ValueError("GB/T 28181 地址必须使用 sip://、gb28181:// 或 RTSP")
                if not model:
                    raise ValueError("本机备用模型路径不能为空")
                device = VideoDevice(
                    f"device-{uuid4().hex[:10]}",
                    site_id=self._site_id,
                    area_id=self._area_id,
                    name=camera_name,
                    protocol=protocol,
                    endpoint=url,
                    device_id=self.device_id_edit.text().strip(),
                    analysis_policy=AnalysisPolicy.LOCAL_FALLBACK,
                    enabled=self.enabled_check.isChecked(),
                )
                camera = CameraDevice(
                    f"camera-{uuid4().hex[:10]}",
                    self._area_id,
                    camera_name,
                    address=self.camera_address_edit.text().strip(),
                    enabled=self.enabled_check.isChecked(),
                    device_id=device.id,
                    channel_id="ch1",
                )
                stream = None
                if is_rtsp:
                    stream = VideoStream(
                        f"stream-{uuid4().hex[:10]}",
                        camera.id,
                        stream_name,
                        url,
                        role=StreamRole(str(self.role_combo.currentData() or "sub")),
                        width=self.width_spin.value(),
                        height=self.height_spin.value(),
                        model=model,
                        confidence=self.confidence_spin.value(),
                        fps=self.fps_spin.value(),
                        input_size=int(self.input_size_combo.currentData() or 640),
                        enabled=self.enabled_check.isChecked(),
                        device_id=device.id,
                        analysis_policy=AnalysisPolicy.LOCAL_FALLBACK,
                    )
                self.device = device
                self.camera = camera
                self.stream = stream
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "摄像头配置校验失败", str(exc))
                return
            self.accept()

else:

    class CameraQuickAddDialog:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = ["CameraQuickAddDialog"]