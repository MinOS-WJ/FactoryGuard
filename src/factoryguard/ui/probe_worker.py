from __future__ import annotations

"""Asynchronous RTSP connectivity probe for the topology page."""

import threading
from math import isfinite

from factoryguard.media.ffmpeg import require_ffmpeg
from factoryguard.media.rtsp import RTSPCapture

from .qt import HAS_QT


if HAS_QT:
    from .qt import QObject, Signal, Slot

    class StreamProbeWorker(QObject):
        status_changed = Signal(str)
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
                frame = capture.capture_one(timeout=self.timeout)
                if self._stop.is_set():
                    self.finished.emit(False, "测试已取消")
                    return
                height, width = frame.image.shape[:2]
                self.finished.emit(True, f"连接成功 · 收到 {width}×{height} 首帧")
            except Exception as exc:  # noqa: BLE001 - show the concrete probe failure in the UI
                self.finished.emit(False, str(exc))

else:

    class StreamProbeWorker:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            raise RuntimeError("PySide6 is required for the RTSP probe worker")


__all__ = ["StreamProbeWorker"]
