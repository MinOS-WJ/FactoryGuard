from math import nan
from pathlib import Path

import pytest

from factoryguard.media.ffmpeg import FFmpegInstallation, find_ffmpeg
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


def test_rtsp_command_rejects_non_finite_fps():
    with pytest.raises(ValueError, match="fps"):
        FFmpegInstallation(Path("ffmpeg.exe")).command_for_rtsp("rtsp://localhost/live", fps=nan)


def test_rawvideo_clip_command_uses_pipe_and_separate_output_argument(tmp_path):
    command = FFmpegInstallation(Path("ffmpeg.exe")).command_for_rawvideo(
        tmp_path / "event.mp4", width=640, height=360, fps=2, frames=4
    )
    assert command[command.index("-f") + 1] == "rawvideo"
    assert command[command.index("-i") + 1] == "pipe:0"
    assert command[-1].endswith("event.mp4")


def test_bundled_ffmpeg_is_discovered():
    installation = find_ffmpeg()
    assert installation is not None
    assert installation.executable.name.lower() == "ffmpeg.exe"
    assert installation.version and "ffmpeg version" in installation.version.lower()
    bundled = Path(__file__).resolve().parents[1] / "src" / "third_party"
    if bundled.exists():
        assert bundled in installation.executable.parents


def test_rtsp_capture_rejects_short_output(monkeypatch):
    class Completed:
        returncode = 0
        stdout = b"short"
        stderr = b""

    monkeypatch.setattr("factoryguard.media.rtsp.subprocess.run", lambda *args, **kwargs: Completed())
    capture = RTSPCapture(FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2)
    with pytest.raises(RuntimeError, match="returned 0 frames; expected 1"):
        list(capture.capture(1))


def test_rtsp_capture_validates_constructor_and_timeout():
    with pytest.raises(ValueError, match="URL"):
        RTSPCapture(FFmpegInstallation(Path("ffmpeg.exe")), "", width=2, height=2)
    capture = RTSPCapture(FFmpegInstallation(Path("ffmpeg.exe")), "rtsp://localhost/live", width=2, height=2)
    with pytest.raises(ValueError, match="timeout"):
        list(capture.capture(1, timeout=0))
