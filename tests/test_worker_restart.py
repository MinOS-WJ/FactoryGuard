from contextlib import contextmanager

import numpy as np
import pytest

pytest.importorskip("PySide6")

from dataclasses import replace

from factoryguard.inference.base import InferenceHealth
from factoryguard.media.rtsp import RawFrame
from factoryguard.ui.worker import MonitorConfig, MonitorWorker, StreamRuntimeConfig


def test_monitor_worker_shares_engine_for_same_model_and_confidence(monkeypatch):
    from factoryguard.inference.base import InferenceHealth

    created = []

    class FakeEngine:
        def __init__(self, model_path, *, confidence, input_size):
            created.append((str(model_path), confidence, input_size))

        def health_check(self):
            return InferenceHealth("ready", "cpu", detail="CPUExecutionProvider")

        def reset(self):
            return None

    monkeypatch.setattr("factoryguard.ui.worker.YoloOnnxEngine", FakeEngine)
    worker = MonitorWorker(MonitorConfig(db=":memory:"))
    first = StreamRuntimeConfig(
        stream_id="stream-1",
        url="rtsp://camera/one",
        model="models/yolo11n.onnx",
        confidence=0.25,
    )
    second = StreamRuntimeConfig(
        stream_id="stream-2",
        url="rtsp://camera/two",
        model="models/yolo11n.onnx",
        confidence=0.25,
    )
    different = StreamRuntimeConfig(
        stream_id="stream-3",
        url="rtsp://camera/three",
        model="models/yolo11n.onnx",
        confidence=0.5,
    )
    engine_one, _ = worker._shared_engine(first)
    engine_two, _ = worker._shared_engine(second)
    engine_three, _ = worker._shared_engine(different)
    engine_small, _ = worker._shared_engine(replace(first, input_size=320))
    assert engine_one is engine_two
    assert engine_one is not engine_three
    assert engine_one is not engine_small
    assert len(created) == 3
    assert {item[2] for item in created} == {320, 640}


def test_monitor_config_allows_local_only_mode_without_notification_channels():
    config = MonitorConfig(notification_channels=())
    assert config.notification_channels == ()

def test_monitor_worker_uses_exponential_backoff_for_stream_reconnects(monkeypatch):
    worker = MonitorWorker(MonitorConfig(db=":memory:", notification_channels=()))
    runtime = StreamRuntimeConfig(stream_id="stream-1", url="rtsp://camera/live")

    class FakeStore:
        def load_rules(self, **_kwargs):
            return []

        def save_rule(self, _rule):
            return None

        def get_stream_armed(self, _stream_id, default=True):
            return default

        def get_arming_schedule(self, _stream_id):
            return None

        def set_stream_health(self, *_args, **_kwargs):
            return None

        def close(self):
            return None

    class FakeEngine:
        def infer_image(self, _image):
            return ()

    class FakeStop:
        def __init__(self):
            self.stopped = False
            self.waits = []

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, timeout):
            self.waits.append(timeout)
            return self.stopped

    stop = FakeStop()
    worker._stop = stop
    attempts = []

    @contextmanager
    def fake_stream(_self, timeout):
        attempts.append(timeout)
        if len(attempts) >= 3:
            stop.set()
        raise RuntimeError("stream unavailable")
        yield

    class FakeCapture:
        def __init__(self, *_args, **_kwargs):
            pass

        stream = fake_stream

    monkeypatch.setattr("factoryguard.ui.worker.SQLiteStore", lambda *_args, **_kwargs: FakeStore())
    monkeypatch.setattr("factoryguard.ui.worker.RTSPCapture", FakeCapture)
    monkeypatch.setattr("factoryguard.ui.worker.time.monotonic", lambda: 0.0)
    worker._shared_engine = lambda _runtime: (FakeEngine(), InferenceHealth("ready", "fake"))

    worker._run_stream_once(None, runtime)

    assert len(attempts) == 3
    assert stop.waits == [1.0, 2.0]
    assert stop.is_set()


def test_monitor_worker_throttles_running_health_heartbeats(monkeypatch):
    worker = MonitorWorker(MonitorConfig(db=":memory:", notification_channels=()))
    runtime = StreamRuntimeConfig(stream_id="stream-1", url="rtsp://camera/live")
    health_calls = []

    class FakeStore:
        def load_rules(self, **_kwargs):
            return []

        def save_rule(self, _rule):
            return None

        def get_stream_armed(self, _stream_id, default=True):
            return default

        def get_arming_schedule(self, _stream_id):
            return None

        def set_stream_health(self, stream_id, status, detail, **_kwargs):
            health_calls.append((stream_id, status, detail))

        def close(self):
            return None

    class FakeEngine:
        def infer_image(self, _image):
            return ()

    @contextmanager
    def fake_stream(_self, timeout):
        def frames():
            yield RawFrame(np.zeros((2, 2, 3), dtype=np.uint8), 0)
            yield RawFrame(np.zeros((2, 2, 3), dtype=np.uint8), 1)
            worker._stop.set()

        yield frames()

    class FakeCapture:
        def __init__(self, *_args, **_kwargs):
            pass

        stream = fake_stream

    ticks = iter((0.0, 1.0, 2.0, 3.0, 4.0))
    monkeypatch.setattr("factoryguard.ui.worker.SQLiteStore", lambda *_args, **_kwargs: FakeStore())
    monkeypatch.setattr("factoryguard.ui.worker.RTSPCapture", FakeCapture)
    monkeypatch.setattr("factoryguard.ui.worker.time.monotonic", lambda: next(ticks))
    worker._shared_engine = lambda _runtime: (FakeEngine(), InferenceHealth("ready", "fake"))

    worker._run_stream_once(None, runtime)

    assert [status for _stream_id, status, _detail in health_calls] == ["ready", "running", "stopped"]


def test_monitor_worker_retries_failed_stream_initialization_with_backoff():
    worker = MonitorWorker(MonitorConfig(db=":memory:"))
    runtime = StreamRuntimeConfig(stream_id="stream-1", url="rtsp://camera/live")

    class _Stop:
        def __init__(self):
            self.stopped = False
            self.waits = []

        def is_set(self):
            return self.stopped

        def wait(self, timeout):
            self.waits.append(timeout)
            return False

    stop = _Stop()
    worker._stop = stop
    calls = []

    def fail_once(_ffmpeg, _runtime):
        calls.append(True)
        if len(calls) == 2:
            stop.stopped = True

    worker._run_stream_once = fail_once
    worker._run_stream(None, runtime)

    assert len(calls) == 2
    assert stop.waits == [1.0]
