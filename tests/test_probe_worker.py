from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from factoryguard.media.rtsp import RawFrame
from factoryguard.ui import probe_worker
from factoryguard.ui.probe_worker import StreamProbeWorker


class _FFmpeg:
    def probe_resolution(self, _url, *, timeout):
        assert timeout == 10
        return (1920, 1080)


class _FakeCapture:
    def __init__(self, _ffmpeg, _url, *, width, height, fps):
        assert (width, height, fps) == (320, 180, 1.0)

    def capture_one(self, *, timeout):
        assert timeout == 10
        return RawFrame(np.zeros((180, 320, 3), dtype=np.uint8), 0)


def test_stream_probe_worker_reports_first_frame(monkeypatch):
    monkeypatch.setattr(probe_worker, "require_ffmpeg", lambda: _FFmpeg())
    monkeypatch.setattr(probe_worker, "RTSPCapture", _FakeCapture)
    worker = StreamProbeWorker(url="rtsp://camera/live", width=320, height=180)
    statuses: list[str] = []
    results: list[tuple[bool, str]] = []
    resolutions: list[tuple[int, int]] = []
    worker.resolution_ready.connect(lambda width, height: resolutions.append((width, height)))
    worker.status_changed.connect(statuses.append)
    worker.finished.connect(lambda success, detail: results.append((success, detail)))

    worker.run()

    assert statuses == ["正在查找 FFmpeg", "正在连接 RTSP"]
    assert results == [(True, "连接成功 · 源 1920×1080 · 处理 320×180 首帧")]
    assert resolutions == [(1920, 1080)]


def test_stream_probe_worker_surfaces_connection_failures(monkeypatch):
    class _FailingCapture:
        def __init__(self, *_args, **_kwargs):
            pass

        def capture_one(self, *, timeout):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(probe_worker, "require_ffmpeg", lambda: _FFmpeg())
    monkeypatch.setattr(probe_worker, "RTSPCapture", _FailingCapture)
    worker = StreamProbeWorker(url="rtsp://camera/live", width=320, height=180)
    results: list[tuple[bool, str]] = []
    worker.finished.connect(lambda success, detail: results.append((success, detail)))

    worker.run()

    assert results == [(False, "connection refused")]