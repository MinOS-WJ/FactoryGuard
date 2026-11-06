import io
import threading
from math import nan
from pathlib import Path

import numpy as np
import pytest

from factoryguard.media.ffmpeg import FFmpegInstallation, encode_raw_image, find_ffmpeg
from factoryguard.media.rtsp import RTSPCapture


def test_rtsp_command_is_argument_safe():
    installation = FFmpegInstallation(Path("ffmpeg.exe"), "test")
    command = installation.command_for_rtsp(
        "rtsp://user:p@ss@camera.example/live?x=1;echo bad",
        fps=2,
        width=640,
        height=360,
    )
    assert command[0] == "ffmpeg.exe"
    assert command[command.index("-i") + 1].startswith("rtsp://")
    assert "echo bad" in command[command.index("-i") + 1]
    assert "scale=640:360,fps=2" in command
    assert command[command.index("-timeout") + 1] == "15000000"


def test_rtsp_command_rejects_non_finite_fps():
    with pytest.raises(ValueError, match="fps"):
        FFmpegInstallation(Path("ffmpeg.exe")).command_for_rtsp("rtsp://localhost/live", fps=nan)
    with pytest.raises(ValueError, match="read_timeout"):
        FFmpegInstallation(Path("ffmpeg.exe")).command_for_rtsp(
            "rtsp://localhost/live", read_timeout=0
        )


def test_rawvideo_clip_command_uses_pipe_and_separate_output_argument(tmp_path):
    command = FFmpegInstallation(Path("ffmpeg.exe")).command_for_rawvideo(
        tmp_path / "event.mp4", width=640, height=360, fps=2, frames=4
    )
    assert command[command.index("-f") + 1] == "rawvideo"
    assert command[command.index("-i") + 1] == "pipe:0"
    assert command[-1].endswith("event.mp4")


def test_ffprobe_resolution_reads_source_dimensions(monkeypatch):
    class Completed:
        returncode = 0
        stdout = b'{"streams":[{"width":1920,"height":1080}]}'
        stderr = b""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr("factoryguard.media.ffmpeg.subprocess.run", fake_run)
    installation = FFmpegInstallation(Path("ffmpeg.exe"), "test", Path("ffprobe.exe"))
    assert installation.probe_resolution("rtsp://camera/live", timeout=4) == (1920, 1080)
    assert calls[0][0][0] == "ffprobe.exe"


def test_bundled_ffmpeg_is_discovered():
    installation = find_ffmpeg()
    assert installation is not None
    assert installation.executable.name.lower() == "ffmpeg.exe"
    assert installation.version and "ffmpeg version" in installation.version.lower()
    bundled = Path(__file__).resolve().parents[1] / "src" / "third_party"
    if bundled.exists():
        assert bundled in installation.executable.parents


def test_encode_raw_image_uses_png_pipe(monkeypatch, tmp_path):
    calls = []

    class Completed:
        returncode = 0
        stderr = b""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"png")
        return Completed()

    monkeypatch.setattr("factoryguard.media.ffmpeg.subprocess.run", fake_run)
    image = np.zeros((2, 3, 3), dtype=np.uint8)
    output = encode_raw_image(
        FFmpegInstallation(Path("ffmpeg.exe")),
        image,
        tmp_path / "snapshot.png",
    )
    assert output.endswith("snapshot.png")
    assert "-vcodec" in calls[0][0]
    assert calls[0][0][calls[0][0].index("-vcodec") + 1] == "png"


def test_rtsp_stream_keeps_one_process_and_reads_multiple_frames(monkeypatch):
    frame_bytes = np.zeros((2, 2, 3), dtype=np.uint8).tobytes()
    payload = frame_bytes * 2

    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(payload)
            self.stderr = io.BytesIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    process = Process()
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr("factoryguard.media.rtsp.subprocess.Popen", fake_popen)
    capture = RTSPCapture(
        FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2
    )
    with capture.stream(timeout=3) as frames:
        received = list(frames)
    assert len(received) == 2
    assert [frame.frame_no for frame in received] == [0, 1]
    assert len(calls) == 1
    assert "-frames:v" not in calls[0][0]


def test_rtsp_stream_times_out_when_frame_read_stalls(monkeypatch):
    released = threading.Event()

    class BlockingStdout:
        def read(self, _size):
            released.wait(timeout=2.0)
            return b""

        def close(self):
            released.set()

    class Process:
        def __init__(self):
            self.stdout = BlockingStdout()
            self.stderr = io.BytesIO()
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15
            released.set()

        def kill(self):
            self.returncode = -9
            released.set()

        def wait(self, timeout=None):
            released.set()
            return self.returncode

    monkeypatch.setattr(
        "factoryguard.media.rtsp.subprocess.Popen", lambda *args, **kwargs: Process()
    )
    capture = RTSPCapture(
        FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2
    )
    with (
        pytest.raises(RuntimeError, match="read timed out"),
        capture.stream(timeout=0.05) as frames,
    ):
        next(frames)


def test_rtsp_capture_error_redacts_credentials(monkeypatch):
    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"connect rtsp://admin:secret@camera/live failed")
            self.returncode = 1

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

    def fake_popen(*_args, **kwargs):
        kwargs["stderr"].write(b"connect rtsp://admin:secret@camera/live failed")
        return Process()

    monkeypatch.setattr("factoryguard.media.rtsp.subprocess.Popen", fake_popen)
    capture = RTSPCapture(
        FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://admin:secret@camera/live", width=2, height=2
    )
    with pytest.raises(RuntimeError) as exc:
        list(capture.capture(1))
    assert "secret" not in str(exc.value)
    assert "***:***@camera/live" in str(exc.value)


def test_rtsp_capture_uses_one_streaming_process(monkeypatch):
    frame_bytes = np.zeros((2, 2, 3), dtype=np.uint8).tobytes()

    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(frame_bytes * 2)
            self.stderr = io.BytesIO()
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr("factoryguard.media.rtsp.subprocess.Popen", fake_popen)
    capture = RTSPCapture(
        FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2
    )
    frames = capture.capture(1)
    assert next(frames).frame_no == 0
    frames.close()
    assert len(calls) == 1
    assert "-frames:v" not in calls[0][0]


def test_rtsp_capture_rejects_short_output(monkeypatch):
    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(b"short")
            self.stderr = io.BytesIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(
        "factoryguard.media.rtsp.subprocess.Popen", lambda *args, **kwargs: Process()
    )
    capture = RTSPCapture(
        FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2
    )
    with pytest.raises(RuntimeError, match="returned 0 frames; expected 1"):
        list(capture.capture(1))


def test_rtsp_capture_validates_constructor_and_timeout():
    with pytest.raises(ValueError, match="URL"):
        RTSPCapture(FFmpegInstallation(Path("ffmpeg.exe")), "", width=2, height=2)
    capture = RTSPCapture(
        FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2
    )
    with pytest.raises(ValueError, match="timeout"):
        list(capture.capture(1, timeout=0))


def test_media_errors_are_translated_for_operators():
    from factoryguard.media.errors import describe_media_error

    assert "无法连接" in describe_media_error("Connection refused")
    assert "超时" in describe_media_error("Connection timed out")
    assert "账号或密码" in describe_media_error("401 Unauthorized")
    assert "权限" in describe_media_error("403 Forbidden")
    assert "视频地址" in describe_media_error("404 Not Found")
    assert "无法解析" in describe_media_error("Could not find codec parameters")
    assert describe_media_error("something entirely different") == ""


def test_media_error_summary_falls_back_to_shortened_detail():
    from factoryguard.media.errors import summarise_media_error

    message = summarise_media_error("x" * 400)
    assert len(message) <= 160
    assert message.endswith("…")
    assert summarise_media_error("") == ""
    assert summarise_media_error("", fallback="取流失败") == "取流失败"
    assert "无法连接" in summarise_media_error("Connection refused")


def test_media_error_keeps_credentials_redacted():
    from factoryguard.media.errors import redact_media_credentials

    redacted = redact_media_credentials("rtsp://admin:secret@camera/live")
    assert "secret" not in redacted
    assert "***:***@" in redacted
