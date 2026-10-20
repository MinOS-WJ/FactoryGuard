"""Black-box acceptance checks for the real ``src/main.py`` entry point.

These tests deliberately avoid importing FactoryGuard internals. They start
the same script an operator starts and assert only process results and
observable JSON/files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "src" / "main.py"


def _run_cli(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run the checkout entry point without relying on package installation."""
    environment = os.environ.copy()
    # Removing an inherited PYTHONPATH makes this a real entry-point check.
    environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_real_entrypoint_help_lists_operational_commands() -> None:
    result = _run_cli("--help")

    assert result.returncode == 0, result.stderr
    assert "simulate" in result.stdout
    assert "health" in result.stdout
    assert "channels" in result.stdout
    assert "dispatch-outbox" in result.stdout
    assert "cleanup-media" in result.stdout


def test_real_entrypoint_health_returns_machine_readable_report(tmp_path: Path) -> None:
    database = tmp_path / "health.db"
    model = tmp_path / "model.onnx"
    model.write_bytes(b"acceptance-fixture")
    media = tmp_path / "media"

    report = _json_output(
        _run_cli(
            "health",
            "--db",
            str(database),
            "--model",
            str(model),
            "--media-dir",
            str(media),
        )
    )

    assert report["database"] == str(database)
    assert isinstance(report["checks"], list)
    check_names = {str(item["name"]) for item in report["checks"]}
    assert {
        "SQLite \u6570\u636e\u5e93",
        "FFmpeg",
        "ONNX \u6a21\u578b",
        "\u5a92\u4f53\u76ee\u5f55",
        "\u78c1\u76d8\u7a7a\u95f4",
    } <= check_names
    assert media.is_dir()


def test_real_entrypoint_simulation_persists_event_and_outbox(tmp_path: Path) -> None:
    database = tmp_path / "simulation.db"

    report = _json_output(_run_cli("simulate", "--db", str(database), "--frames", "3"))

    assert report == {
        "frames": 3,
        "events": 1,
        "pending_notifications": 1,
        "integrity": "ok",
    }
    assert database.is_file()


def test_real_entrypoint_channels_reports_adapter_and_authorization_state() -> None:
    report = _json_output(_run_cli("channels"))

    channels = {str(item["key"]): item for item in report["channels"]}
    assert set(channels) == {"webhook", "email", "sms", "wecom", "wechat", "dingtalk", "feishu"}
    assert channels["webhook"]["implemented"] is True
    assert channels["webhook"]["authorization_required"] is True
    assert all(
        channels[key]["authorization_required"] is True
        for key in ("email", "sms", "wecom", "wechat", "dingtalk", "feishu")
    )
    assert channels["email"]["implemented"] is True
    assert channels["sms"]["implemented"] is True
    assert all(channels[key]["implemented"] is False for key in ("wecom", "wechat", "dingtalk", "feishu"))


def test_real_entrypoint_cleanup_media_is_observable_and_safe(tmp_path: Path) -> None:
    database = tmp_path / "cleanup.db"
    media = tmp_path / "events-media"
    _json_output(_run_cli("simulate", "--db", str(database), "--frames", "3"))

    report = _json_output(
        _run_cli(
            "cleanup-media",
            "--db",
            str(database),
            "--media-dir",
            str(media),
            "--retention-days",
            "0",
            "--dry-run",
        )
    )

    assert report == {
        "scanned": 0,
        "eligible": 0,
        "removed": 0,
        "bytes_freed": 0,
        "skipped": 0,
        "errors": 0,
    }


def test_real_entrypoint_dispatch_outbox_reports_retry_without_losing_event(tmp_path: Path) -> None:
    database = tmp_path / "dispatch.db"
    _json_output(_run_cli("simulate", "--db", str(database), "--frames", "3"))

    report = _json_output(
        _run_cli(
            "dispatch-outbox",
            "--db",
            str(database),
            "--webhook-url",
            "https://127.0.0.1:1/factoryguard",
            "--webhook-secret",
            "acceptance-secret",
            "--limit",
            "1",
            timeout=20.0,
        )
    )

    assert report["claimed"] == 1
    assert report["sent"] == 0
    assert report["retried"] == 1
    assert report["failed"] == 0
