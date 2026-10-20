import json
from pathlib import Path

import numpy as np
import pytest

from factoryguard import cli
from factoryguard.domain.models import Detection
from factoryguard.domain.schedules import ArmingSchedule
from factoryguard.inference.base import InferenceHealth
from factoryguard.media.ffmpeg import FFmpegInstallation
from factoryguard.media.rtsp import RawFrame
from factoryguard.notifications.dispatcher import DispatchReport
from factoryguard.storage.retention import RetentionReport
from factoryguard.storage.sqlite import SQLiteStore


class _FakeEngine:
    def __init__(self, _model: str, *, confidence: float) -> None:
        self.confidence = confidence

    def health_check(self) -> InferenceHealth:
        return InferenceHealth("ready", "fake")

    def infer_image(self, _image: np.ndarray) -> tuple[()]:
        return ()


class _FakeCapture:
    def __init__(self, _installation: FFmpegInstallation, _url: str, *, width: int, height: int, fps: float = 2.0) -> None:
        self.width = width
        self.height = height

    def capture(self, frames: int):
        for frame_no in range(frames):
            yield RawFrame(np.zeros((self.height, self.width, 3), dtype=np.uint8), frame_no)


class _PersonEngine(_FakeEngine):
    def infer_image(self, _image: np.ndarray) -> tuple[Detection, ...]:
        return (Detection("person", 0.95, (10, 10, 40, 80), "person-1"),)


def test_monitor_cli_runs_each_stream(monkeypatch, capsys):
    monkeypatch.setattr(cli, "YoloOnnxEngine", _FakeEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--stream",
            "main=rtsp://main",
            "--stream",
            "sub=rtsp://sub",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert [stream["stream_id"] for stream in result["streams"]] == ["main", "sub"]
    assert all(stream["frames"][0]["frame_no"] == 0 for stream in result["streams"])


def test_probe_rtsp_cli_reports_first_frame(monkeypatch, capsys):
    class _FakeCapture:
        def __init__(self, _installation, url, *, width, height, fps=2.0):
            assert url == "rtsp://camera/live"
            assert (width, height) == (320, 180)

        def capture_one(self, *, timeout):
            assert timeout == 4
            return RawFrame(np.zeros((180, 320, 3), dtype=np.uint8), 0)

    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    assert cli.main(
        [
            "probe-rtsp",
            "--url",
            "rtsp://camera/live",
            "--width",
            "320",
            "--height",
            "180",
            "--timeout",
            "4",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert (result["width"], result["height"]) == (320, 180)


def test_monitor_cli_honors_persisted_disarm_state(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "YoloOnnxEngine", _PersonEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    database = tmp_path / "arming.db"

    assert cli.main(["monitor-rtsp", "--frames", "1", "--confirm-frames", "1", "--db", str(database)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["events"] == 1

    store = SQLiteStore(database)
    store.set_stream_armed("CH01", False, actor="test")
    store.close()
    assert cli.main(["monitor-rtsp", "--frames", "1", "--confirm-frames", "1", "--db", str(database)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["events"] == 1

    store = SQLiteStore(database)
    store.set_stream_armed("CH01", True, actor="test")
    rule = store.load_rules(stream_id="CH01")[0]
    store.set_rule_enabled(rule.id, False, actor="test")
    store.close()
    assert cli.main(["monitor-rtsp", "--frames", "1", "--confirm-frames", "1", "--db", str(database)]) == 0
    third = json.loads(capsys.readouterr().out)
    assert third["events"] == 1

    store = SQLiteStore(database)
    store.set_rule_enabled(rule.id, True, actor="test")
    store.save_arming_schedule(ArmingSchedule("CH01", default_armed=False), actor="test")
    store.close()
    assert cli.main(["monitor-rtsp", "--frames", "1", "--confirm-frames", "1", "--db", str(database)]) == 0
    fourth = json.loads(capsys.readouterr().out)
    assert fourth["events"] == 1


def test_dispatch_outbox_cli_reports_delivery(monkeypatch, capsys, tmp_path):
    class _FakeDispatcher:
        def __init__(self, **_kwargs):
            pass

        def dispatch_once(self, _store, *, limit):
            assert limit == 5
            return DispatchReport(claimed=2, sent=1, failed=0, retried=1)

    monkeypatch.setattr(cli, "OutboxDispatcher", _FakeDispatcher)
    assert cli.main(
        [
            "dispatch-outbox",
            "--db",
            str(tmp_path / "outbox.db"),
            "--webhook-url",
            "https://example.test/hook",
            "--webhook-secret",
            "secret",
            "--limit",
            "5",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {"claimed": 2, "sent": 1, "failed": 0, "retried": 1}


def test_dispatch_outbox_cli_supports_email_only_delivery(monkeypatch, capsys, tmp_path):
    captured = {}

    class _FakeDispatcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def dispatch_once(self, _store, *, limit):
            assert limit == 10
            return DispatchReport(claimed=0, sent=0, failed=0, retried=0)

    monkeypatch.setattr(cli, "OutboxDispatcher", _FakeDispatcher)
    assert cli.main(
        [
            "dispatch-outbox",
            "--db",
            str(tmp_path / "email-outbox.db"),
            "--email-host",
            "smtp.example.test",
            "--email-port",
            "465",
            "--email-username",
            "factoryguard@example.test",
            "--email-password",
            "secret",
            "--email-sender",
            "factoryguard@example.test",
            "--email-recipients",
            "one@example.test; two@example.test",
            "--email-security",
            "ssl",
            "--limit",
            "10",
        ]
    ) == 0
    assert captured["webhook_url"] == ""
    adapter = captured["adapters"]["email"]
    assert adapter.host == "smtp.example.test"
    assert adapter.port == 465
    assert adapter.recipients == ("one@example.test", "two@example.test")
    assert adapter.use_ssl is True
    assert adapter.starttls is False
    assert json.loads(capsys.readouterr().out) == {"claimed": 0, "sent": 0, "failed": 0, "retried": 0}


def test_dispatch_outbox_cli_supports_sms_only_delivery(monkeypatch, capsys, tmp_path):
    captured = {}

    class _FakeDispatcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def dispatch_once(self, _store, *, limit):
            assert limit == 2
            return DispatchReport(claimed=1, sent=1, failed=0, retried=0)

    monkeypatch.setattr(cli, "OutboxDispatcher", _FakeDispatcher)
    assert cli.main(
        [
            "dispatch-outbox",
            "--db",
            str(tmp_path / "sms-outbox.db"),
            "--sms-endpoint",
            "https://sms.example.test/send",
            "--sms-token",
            "token",
            "--sms-sender",
            "FactoryGuard",
            "--sms-template-id",
            "event-alert",
            "--sms-recipients",
            "13800138000; +86 139-0013-9000",
            "--limit",
            "2",
        ]
    ) == 0
    adapter = captured["adapters"]["sms"]
    assert adapter.endpoint == "https://sms.example.test/send"
    assert adapter.recipients == ("13800138000", "+8613900139000")
    assert adapter.sender == "FactoryGuard"
    assert adapter.template_id == "event-alert"
    assert json.loads(capsys.readouterr().out) == {"claimed": 1, "sent": 1, "failed": 0, "retried": 0}


def test_dispatch_outbox_cli_requires_at_least_one_channel(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["dispatch-outbox", "--db", str(tmp_path / "empty-outbox.db")])


def test_dispatch_outbox_cli_watch_emits_each_poll_and_stops_on_interrupt(monkeypatch, capsys, tmp_path):
    class _FakeDispatcher:
        def __init__(self, **_kwargs):
            self.calls = 0

        def dispatch_once(self, _store, *, limit):
            assert limit == 3
            self.calls += 1
            return DispatchReport(claimed=self.calls, sent=self.calls, failed=0, retried=0)

    def _interrupt(_interval):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "OutboxDispatcher", _FakeDispatcher)
    monkeypatch.setattr(cli.time, "sleep", _interrupt)
    assert cli.main(
        [
            "dispatch-outbox",
            "--db",
            str(tmp_path / "watch.db"),
            "--webhook-url",
            "https://example.test/hook",
            "--webhook-secret",
            "secret",
            "--limit",
            "3",
            "--watch",
            "--interval",
            "0.1",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {"claimed": 1, "sent": 1, "failed": 0, "retried": 0}


def test_cleanup_media_cli_reports_retention_result(monkeypatch, capsys, tmp_path):
    class _FakeRetention:
        def __init__(self, *, media_root):
            assert str(media_root).endswith("events-media")

        def cleanup(self, _store, *, retention_days, dry_run):
            assert retention_days == 14
            assert dry_run is True
            return RetentionReport(scanned=3, eligible=2, removed=0, bytes_freed=0, skipped=1, errors=0)

    monkeypatch.setattr(cli, "MediaRetentionManager", _FakeRetention)
    assert cli.main(
        [
            "cleanup-media",
            "--db",
            str(tmp_path / "events.db"),
            "--retention-days",
            "14",
            "--dry-run",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "scanned": 3,
        "eligible": 2,
        "removed": 0,
        "bytes_freed": 0,
        "skipped": 1,
        "errors": 0,
    }


def test_health_cli_reports_local_preflight(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "find_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    database = tmp_path / "health.db"
    assert cli.main(
        [
            "health",
            "--db",
            str(database),
            "--model",
            str(model),
            "--media-dir",
            "media",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert {item["name"] for item in result["checks"]} >= {
        "SQLite 数据库",
        "FFmpeg",
        "ONNX 模型",
        "媒体目录",
        "磁盘空间",
    }
