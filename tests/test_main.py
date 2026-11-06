from __future__ import annotations

import json
from pathlib import Path

import pytest

from main import main


def test_visual_model_stack_and_assets_are_absent():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "models").exists()
    assert not (root / "src" / "factoryguard" / "inference").exists()
    assert not (root / "src" / "factoryguard" / "simulation").exists()
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "onnxruntime" not in requirements


def test_gui_passes_product_paths_to_merchant_config(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        "factoryguard.ui.merchant_console.launch",
        lambda **kwargs: captured.update(kwargs) or 0,
    )
    database = tmp_path / "factoryguard.db"
    config_path = tmp_path / "factoryguard.json"
    assert main(["gui", "--db", str(database), "--config", str(config_path)]) == 0
    assert captured["config"].db == str(database)
    assert captured["config"].config_path == str(config_path)
    assert captured["start_hidden"] is False


def test_autostart_command_reports_status(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert main(["autostart"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["supported"] is True
    assert payload["enabled"] is False
    assert "Startup" in payload["location"]


def test_autostart_command_rejects_conflicting_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(SystemExit) as raised:
        main(["autostart", "--enable", "--disable"])
    assert raised.value.code == 2


def test_autostart_module_creates_and_removes_shortcut(monkeypatch, tmp_path):
    from factoryguard.app import autostart

    monkeypatch.setenv("APPDATA", str(tmp_path))
    target = (
        tmp_path
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "FactoryGuard 智守.lnk"
    )
    monkeypatch.setattr(
        autostart,
        "_write_shortcut",
        lambda path, command: path.write_text("shortcut", encoding="utf-8"),
    )

    status = autostart.enable(python_executable="C:/python/python.exe")
    assert status.enabled is True
    assert target.is_file()

    status = autostart.disable()
    assert status.enabled is False
    assert not target.exists()


def test_autostart_reports_unsupported_platform(monkeypatch):
    from factoryguard.app import autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    status = autostart.describe()
    assert status.supported is False
    assert status.enabled is False


def test_help_exposes_only_current_product_commands(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    for command in ("gui", "tray", "probe-rtsp", "health", "channels"):
        assert command in output
    for removed in ("simulate", "detect-rtsp", "monitor-rtsp"):
        assert removed not in output


def test_legacy_model_options_are_rejected(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["gui", "--model", "models/yolo11n.onnx"])
    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_health_command_reports_resource_snapshot(capsys):
    assert main(["health"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert "resources" in result
    assert set(result["inventory"]) == {
        "sites",
        "areas",
        "cameras",
        "streams",
        "devices",
    }
    assert any(item["name"] == "摄像头接入" for item in result["checks"])
    assert all("规则" not in item["name"] for item in result["checks"])
    assert all("ONNX" not in item["name"] for item in result["checks"])
    assert result["resource_state"] in {"ok", "warn", "error"}
    assert {
        "cpu_percent",
        "memory_used_percent",
        "process_memory",
        "disk_free",
        "logical_cpus",
        "threads",
    }.issubset(result["resources"])


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
    media_file = tmp_path / "not-a-directory"
    media_file.write_text("occupied", encoding="utf-8")
    assert main(["health", "--db", ":memory:", "--media-dir", str(media_file)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    media_check = next(item for item in result["checks"] if item["name"] == "媒体目录")
    assert media_check["state"] == "error"


def test_health_command_returns_nonzero_for_resource_overload(monkeypatch, capsys):
    from factoryguard.system_metrics import ResourceSnapshot

    class _Sampler:
        def sample(self, _root):
            return ResourceSnapshot(
                cpu_percent=10.0,
                memory_total_bytes=100,
                memory_available_bytes=2,
                memory_used_percent=98.0,
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


def test_health_strict_mode_ignores_machine_load(monkeypatch, capsys):
    """A busy machine must not block a deployment check."""
    from factoryguard.system_metrics import ResourceSnapshot

    class _Sampler:
        def sample(self, _root):
            return ResourceSnapshot(
                cpu_percent=100.0,
                memory_total_bytes=100,
                memory_available_bytes=90,
                memory_used_percent=10.0,
                process_memory_bytes=20,
                process_cpu_percent=5.0,
                disk_total_bytes=100,
                disk_free_bytes=80,
                logical_cpu_count=4,
                thread_count=2,
                captured_at=1.0,
            )

    monkeypatch.setattr("factoryguard.cli.ResourceSampler", _Sampler)
    assert main(["health", "--strict"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["resource_state"] == "warn"


def test_health_command_explains_a_machine_load_warning(monkeypatch, capsys):
    from factoryguard.system_metrics import ResourceSnapshot

    class _Sampler:
        def sample(self, _root):
            return ResourceSnapshot(
                cpu_percent=92.0,
                memory_total_bytes=100,
                memory_available_bytes=90,
                memory_used_percent=10.0,
                process_memory_bytes=20,
                process_cpu_percent=5.0,
                disk_total_bytes=100,
                disk_free_bytes=80,
                logical_cpu_count=4,
                thread_count=2,
                captured_at=1.0,
            )

    monkeypatch.setattr("factoryguard.cli.ResourceSampler", _Sampler)
    assert main(["health"]) == 0
    result = json.loads(capsys.readouterr().out)
    load_check = next(item for item in result["checks"] if item["name"] == "机器负载")
    assert load_check["state"] == "warn"
    assert "其他程序" in load_check["detail"]


def test_health_command_survives_a_busy_machine(monkeypatch, capsys):
    """A full CPU only warns; other programs are not the platform fault."""
    from factoryguard.system_metrics import ResourceSnapshot

    class _Sampler:
        def sample(self, _root):
            return ResourceSnapshot(
                cpu_percent=100.0,
                memory_total_bytes=100,
                memory_available_bytes=90,
                memory_used_percent=10.0,
                process_memory_bytes=20,
                process_cpu_percent=5.0,
                disk_total_bytes=100,
                disk_free_bytes=80,
                logical_cpu_count=4,
                thread_count=2,
                captured_at=1.0,
            )

    monkeypatch.setattr("factoryguard.cli.ResourceSampler", _Sampler)
    assert main(["health"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["resource_state"] == "warn"


def test_rtsp_help_documents_capture_parameters(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["probe-rtsp", "--help"])
    assert raised.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    for fragment in (
        "processing frame width",
        "processing frame height",
        "connection and source probe timeout in seconds",
    ):
        assert fragment in output


def test_health_resolves_relative_media_from_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "health",
                "--db",
                "data/factoryguard.db",
                "--media-dir",
                "events-media",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    media_check = next(item for item in result["checks"] if item["name"] == "媒体目录")
    assert media_check["detail"] == str((tmp_path / "events-media").resolve())
    assert (tmp_path / "events-media").is_dir()
    assert not (tmp_path / "data" / "events-media").exists()


def test_health_strict_mode_fails_on_warnings(capsys):
    assert main(["health", "--db", ":memory:"]) == 0
    normal = json.loads(capsys.readouterr().out)
    assert any(item["state"] == "warn" for item in normal["checks"])

    assert main(["health", "--db", ":memory:", "--strict"]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert any(item["state"] == "warn" for item in strict["checks"])
