from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from factoryguard.ui.stream_probe import OnvifProbeWorker, StreamProbeWorker


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


def test_stream_probe_reports_resolution_and_first_frame(app, monkeypatch):
    class Frame:
        image = type("Image", (), {"shape": (180, 320, 3)})()

    class Capture:
        def __init__(self, _ffmpeg, url, *, width, height, fps):
            assert url == "rtsp://camera/live"
            assert (width, height, fps) == (640, 360, 1.0)

        def capture_one(self, *, timeout):
            assert timeout == 4.0
            return Frame()

    class FFmpeg:
        @staticmethod
        def probe_resolution(url, *, timeout):
            assert url == "rtsp://camera/live"
            assert timeout == 4.0
            return (1920, 1080)

    monkeypatch.setattr("factoryguard.ui.stream_probe.require_ffmpeg", lambda: FFmpeg())
    monkeypatch.setattr("factoryguard.ui.stream_probe.RTSPCapture", Capture)
    worker = StreamProbeWorker(url="rtsp://camera/live", width=640, height=360, timeout=4.0)
    completed = []
    resolutions = []
    worker.resolution_ready.connect(lambda width, height: resolutions.append((width, height)))
    worker.finished.connect(lambda ok, detail: completed.append((ok, detail)))

    worker.run()

    assert resolutions == [(1920, 1080)]
    assert completed == [(True, "连接成功 · 源 1920×1080 · 处理 320×180 首帧")]


def test_stream_probe_validates_inputs(app):
    with pytest.raises(ValueError, match="URL"):
        StreamProbeWorker(url="", width=640, height=360)
    with pytest.raises(ValueError, match="dimensions"):
        StreamProbeWorker(url="rtsp://camera/live", width=0, height=360)
    with pytest.raises(ValueError, match="timeout"):
        StreamProbeWorker(url="rtsp://camera/live", width=640, height=360, timeout=0)


def test_onvif_probe_reports_success_with_channel_count(app, monkeypatch):
    class Adapter:
        def __init__(self, device, credentials):
            self.device = device
            self.credentials = credentials
            self.closed = False

        def probe(self, *, timeout):
            assert timeout == 8.0
            assert self.device.endpoint == "http://10.0.0.9/onvif"
            assert self.credentials.username == "admin"
            return SimpleNamespace(ok=True, detail="ONVIF 连接与认证成功")

        def list_channels(self):
            return (SimpleNamespace(id="ch1"), SimpleNamespace(id="ch2"))

        def close(self):
            self.closed = True

    created = []

    def factory(device, credentials):
        adapter = Adapter(device, credentials)
        created.append(adapter)
        return adapter

    monkeypatch.setattr("factoryguard.ui.stream_probe.OnvifAdapter", factory)
    worker = OnvifProbeWorker(endpoint="http://10.0.0.9/onvif", username="admin", password="secret")
    completed = []
    worker.finished.connect(lambda ok, detail: completed.append((ok, detail)))

    worker.run()

    assert completed == [(True, "ONVIF 连接与认证成功，发现 2 个视频通道")]
    assert created and created[0].closed


def test_onvif_probe_reports_failure(app, monkeypatch):
    class Adapter:
        def __init__(self, device, credentials):
            self.device = device

        def probe(self, *, timeout):
            return SimpleNamespace(ok=False, detail="ONVIF HTTP 401 Unauthorized")

        def close(self):
            return None

    monkeypatch.setattr("factoryguard.ui.stream_probe.OnvifAdapter", Adapter)
    worker = OnvifProbeWorker(endpoint="http://10.0.0.9/onvif")
    completed = []
    worker.finished.connect(lambda ok, detail: completed.append((ok, detail)))

    worker.run()

    assert completed == [(False, "ONVIF HTTP 401 Unauthorized")]


def test_onvif_probe_validates_inputs(app):
    with pytest.raises(ValueError, match="ONVIF"):
        OnvifProbeWorker(endpoint="")
    with pytest.raises(ValueError, match="timeout"):
        OnvifProbeWorker(endpoint="http://10.0.0.9/onvif", timeout=0)
