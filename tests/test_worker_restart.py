import pytest

pytest.importorskip("PySide6")

from factoryguard.ui.worker import MonitorConfig, MonitorWorker, StreamRuntimeConfig


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
