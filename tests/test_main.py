import json
from pathlib import Path
import pytest

from main import main


def test_unified_main_dispatches_to_simulation(capsys):
    assert main(["simulate", "--frames", "1"]) == 0
    assert '"frames": 1' in capsys.readouterr().out


def test_gui_passes_analysis_input_size_to_monitor_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "factoryguard.ui.main_window.launch",
        lambda **kwargs: captured.update(kwargs) or 0,
    )
    assert main(["gui", "--input-size", "320"]) == 0
    assert captured["config"].input_size == 320
    assert captured["config"].media_dir == "events-media"

def test_health_command_reports_resource_snapshot(capsys):
    import json

    assert main(["health"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert "resources" in result
    assert set(result["inventory"]) == {"sites", "areas", "cameras", "streams", "rules", "active_rules"}
    assert any(item["name"] == "规则运行状态" for item in result["checks"])
    assert result["resource_state"] in {"ok", "warn", "error"}
    assert {"cpu_percent", "memory_used_percent", "process_memory", "disk_free", "logical_cpus", "threads"}.issubset(
        result["resources"]
    )

def test_health_command_can_probe_rtsp(monkeypatch, capsys):
    class _Frame:
        image = type("Image", (), {"shape": (720, 1280, 3)})()

    class _Capture:
        def __init__(self, _ffmpeg, url, *, width, height, fps):
            assert url == "rtsp://camera/live"
            assert (width, height, fps) == (1280, 720, 1.0)

        def capture_one(self, *, timeout):
            assert timeout == 4.0
            return _Frame()

    class _FFmpeg:
        executable = Path("fake.exe")

        def probe_resolution(self, _url, *, timeout):
            assert timeout == 4.0
            return (1920, 1080)

    monkeypatch.setattr("factoryguard.cli.find_ffmpeg", lambda: _FFmpeg())
    monkeypatch.setattr("factoryguard.cli.RTSPCapture", _Capture)
    assert main(["health", "--rtsp", "rtsp://camera/live", "--rtsp-timeout", "4"]) == 0
    result = json.loads(capsys.readouterr().out)
    check = next(item for item in result["checks"] if item["name"] == "RTSP 探测")
    assert check == {"name": "RTSP 探测", "state": "ok", "detail": "1920x1080 source"}

def test_health_command_returns_nonzero_for_critical_failure(tmp_path, capsys):
    assert main(["health", "--model", str(tmp_path / "missing.onnx")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    model_check = next(item for item in result["checks"] if item["name"] == "ONNX 模型")
    assert model_check["state"] == "error"

def test_health_command_returns_nonzero_for_resource_overload(monkeypatch, capsys):
    from factoryguard.system_metrics import ResourceSnapshot

    class _Sampler:
        def sample(self, _root):
            return ResourceSnapshot(
                cpu_percent=95.0,
                memory_total_bytes=100,
                memory_available_bytes=10,
                memory_used_percent=90.0,
                process_memory_bytes=20,
                process_cpu_percent=5.0,
                disk_total_bytes=100,
                disk_free_bytes=80,
                logical_cpu_count=4,
                thread_count=2,
                captured_at=1.0,
            )

    monkeypatch.setattr("factoryguard.cli.ResourceSampler", _Sampler)
    assert main(["health"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["resource_state"] == "error"

def test_monitor_help_documents_operational_parameters(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["monitor-rtsp", "--help"])
    assert exc.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    for text in (
        "number of frames processed per stream",
        "sampling and clip frame rate",
        "SQLite database containing Site/Area/Camera/VideoStream entities",
        "local event media directory for default media actions",
    ):
        assert text in output

def test_rtsp_help_documents_capture_parameters(capsys):
    expected = {
        "detect-rtsp": (
            "number of frames to detect",
            "minimum detection confidence",
            "sampling frame rate",
        ),
        "probe-rtsp": (
            "processing frame width",
            "processing frame height",
            "connection and source probe timeout in seconds",
        ),
    }
    for command, fragments in expected.items():
        with pytest.raises(SystemExit) as exc:
            main([command, "--help"])
        assert exc.value.code == 0
        output = " ".join(capsys.readouterr().out.split())
        for fragment in fragments:
            assert fragment in output

def test_health_strict_mode_fails_on_warnings(capsys):
    assert main(["health", "--db", ":memory:"]) == 0
    normal = json.loads(capsys.readouterr().out)
    assert any(item["state"] == "warn" for item in normal["checks"])

    assert main(["health", "--db", ":memory:", "--strict"]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert any(item["state"] == "warn" for item in strict["checks"])

def test_monitor_rejects_invalid_media_action_parameters(capsys):
    cases = (
        (["--clip-timeout", "0"], "clip-timeout and prebuffer-seconds must be positive"),
        (["--prebuffer-seconds", "nan"], "clip-timeout and prebuffer-seconds must be positive"),
        (["--http-action-url", "ftp://example.test/action"], "http-action-url must use HTTP or HTTPS"),
    )
    for extra, message in cases:
        with pytest.raises(SystemExit):
            main(["monitor-rtsp", "--frames", "1", *extra])
        assert message in capsys.readouterr().err


def test_gui_rejects_non_finite_fps():
    with pytest.raises(SystemExit):
        main(["gui", "--fps", "nan"])
