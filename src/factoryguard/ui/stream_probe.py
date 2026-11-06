"""Background workers that verify camera connectivity before saving."""

from __future__ import annotations

import threading
from math import isfinite
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from factoryguard.devices.base import DeviceCredentials
from factoryguard.devices.onvif import OnvifAdapter
from factoryguard.domain.models import DeviceProtocol, VideoDevice
from factoryguard.media.ffmpeg import require_ffmpeg
from factoryguard.media.rtsp import RTSPCapture


class StreamProbeWorker(QObject):
    status_changed = Signal(str)
    resolution_ready = Signal(int, int)
    finished = Signal(bool, str)

    def __init__(
        self,
        *,
        url: str,
        width: int,
        height: int,
        fps: float = 1.0,
        timeout: float = 10.0,
    ) -> None:
        super().__init__()
        if not url.strip():
            raise ValueError("RTSP URL is required")
        if width <= 0 or height <= 0:
            raise ValueError("probe dimensions must be positive")
        if not isfinite(fps) or fps <= 0:
            raise ValueError("probe fps must be positive")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("probe timeout must be positive")
        self.url = url.strip()
        self.width = width
        self.height = height
        self.fps = fps
        self.timeout = timeout
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        try:
            self.status_changed.emit("正在查找 FFmpeg")
            ffmpeg = require_ffmpeg()
            self.status_changed.emit("正在连接 RTSP")
            capture = RTSPCapture(
                ffmpeg,
                self.url,
                width=self.width,
                height=self.height,
                fps=self.fps,
            )
            probe_resolution = getattr(ffmpeg, "probe_resolution", None)
            source_resolution = (
                probe_resolution(self.url, timeout=self.timeout) if probe_resolution else None
            )
            frame = capture.capture_one(timeout=self.timeout)
            if self._stop.is_set():
                return
            frame_height, frame_width = frame.image.shape[:2]
            if source_resolution:
                width, height = source_resolution
            else:
                width, height = frame_width, frame_height
            self.resolution_ready.emit(int(width), int(height))
            detail = f"连接成功 · 源 {width}×{height} · 处理 {frame_width}×{frame_height} 首帧"
            self.status_changed.emit(detail)
            self.finished.emit(True, detail)
        except Exception as exc:
            if not self._stop.is_set():
                self.finished.emit(False, str(exc))


class OnvifProbeWorker(QObject):
    """Check ONVIF credentials and service availability before saving."""

    status_changed = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        *,
        endpoint: str,
        username: str = "",
        password: str = "",
        timeout: float = 8.0,
    ) -> None:
        super().__init__()
        if not endpoint.strip():
            raise ValueError("ONVIF 地址不能为空")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("probe timeout must be positive")
        self.endpoint = endpoint.strip()
        self.username = username.strip()
        self.password = password
        self.timeout = timeout
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        adapter: Any = None
        try:
            self.status_changed.emit("正在连接 ONVIF 设备")
            device = VideoDevice(
                id="probe-device",
                site_id="probe-site",
                area_id="probe-area",
                name="ONVIF 连接测试",
                protocol=DeviceProtocol.ONVIF,
                endpoint=self.endpoint,
            )
            adapter = OnvifAdapter(
                device,
                DeviceCredentials(username=self.username, password=self.password),
            )
            result = adapter.probe(timeout=self.timeout)
            if self._stop.is_set():
                return
            if not result.ok:
                self.finished.emit(False, result.detail or "ONVIF 连接失败")
                return
            channels = 0
            try:
                channels = len(adapter.list_channels())
            except (KeyError, RuntimeError, ValueError):
                channels = 0
            detail = "ONVIF 连接与认证成功"
            if channels:
                detail += f"，发现 {channels} 个视频通道"
            self.status_changed.emit(detail)
            self.finished.emit(True, detail)
        except Exception as exc:
            if not self._stop.is_set():
                self.finished.emit(False, str(exc))
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    pass


__all__ = ["OnvifProbeWorker", "StreamProbeWorker"]
