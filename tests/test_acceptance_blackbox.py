"""Black-box acceptance checks for the real src/main.py entry point.

These tests deliberately avoid importing FactoryGuard internals. They start
the same script an operator starts and assert only process results and
observable JSON/files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "src" / "main.py"


def _run_cli(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run the checkout entry point without relying on package installation."""
    environment = os.environ.copy()
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


def test_real_entrypoint_help_lists_only_current_commands() -> None:
    result = _run_cli("--help")

    assert result.returncode == 0, result.stderr
    for command in (
        "gui",
        "tray",
        "probe-rtsp",
        "health",
        "channels",
        "dispatch-outbox",
        "cleanup-media",
    ):
        assert command in result.stdout
    for removed in ("simulate", "detect-rtsp", "monitor-rtsp"):
        assert removed not in result.stdout


def test_real_entrypoint_health_returns_machine_readable_report(tmp_path: Path) -> None:
    database = tmp_path / "health.db"
    media = tmp_path / "media"

    report = _json_output(
        _run_cli(
            "health",
            "--db",
            str(database),
            "--media-dir",
            str(media),
        )
    )

    assert report["database"] == str(database)
    assert isinstance(report["checks"], list)
    check_names = {str(item["name"]) for item in report["checks"]}
    assert {
        "SQLite 数据库",
        "FFmpeg",
        "媒体目录",
        "磁盘空间",
    } <= check_names
    assert all("ONNX" not in name for name in check_names)
    assert media.is_dir()


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


def test_real_entrypoint_cleanup_media_is_observable_and_safe(tmp_path: Path) -> None:
    database = tmp_path / "cleanup.db"
    media = tmp_path / "events-media"

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


def test_real_entrypoint_dispatch_outbox_handles_empty_queue(tmp_path: Path) -> None:
    database = tmp_path / "dispatch.db"

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

    assert report["claimed"] == 0
    assert report["sent"] == 0
    assert report["retried"] == 0
    assert report["failed"] == 0
