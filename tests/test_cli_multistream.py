import json
from pathlib import Path

import numpy as np
import pytest

from factoryguard import cli
from factoryguard.domain.models import Area, CameraDevice, Detection, Rule, RuleType, Site, VideoStream
from factoryguard.domain.schedules import ArmingSchedule
from factoryguard.domain.topology import EntityRegistry
from factoryguard.inference.base import InferenceHealth
from factoryguard.media.ffmpeg import FFmpegInstallation
from factoryguard.media.rtsp import RawFrame
from factoryguard.notifications.dispatcher import DispatchReport
from factoryguard.storage.retention import RetentionReport
from factoryguard.storage.sqlite import SQLiteStore


class _FakeEngine:
    def __init__(self, _model: str, *, confidence: float, input_size: int = 640) -> None:
        self.confidence = confidence
        self.input_size = input_size

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


class _VehicleEngine(_FakeEngine):
    def infer_image(self, _image: np.ndarray) -> tuple[Detection, ...]:
        return (Detection("car", 0.82, (10, 10, 80, 80), "car-1"),)

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

            "--input-size",
            "320",
            "--fps",
            "7",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert [stream["stream_id"] for stream in result["streams"]] == ["main", "sub"]
    assert all(stream["frames"][0]["frame_no"] == 0 for stream in result["streams"])
    assert result["streams"][0]["input_size"] == 320
    assert result["streams"][0]["fps"] == 7.0
    assert result["fps"] == 7.0
    assert result["measured_fps"] is None
    assert result["elapsed_seconds"] is None
    assert all(stream["measured_fps"] is None for stream in result["streams"])
    assert all(stream["elapsed_seconds"] >= 0 for stream in result["streams"])
    assert all(stream["startup_seconds"] >= 0 for stream in result["streams"])


def test_monitor_cli_isolates_multistream_events_and_media(monkeypatch, capsys, tmp_path):
    media_calls = []
    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    monkeypatch.setattr(
        cli,
        "encode_raw_image",
        lambda _installation, _image, output: media_calls.append(("image", str(output))) or str(output),
    )
    monkeypatch.setattr(
        cli,
        "encode_raw_clip",
        lambda _installation, _frames, output, **_kwargs: media_calls.append(("clip", str(output))) or str(output),
    )
    database = tmp_path / "multistream-isolation.db"

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--confirm-frames",
            "1",
            "--target-labels",
            "car",
            "--media-dir",
            str(tmp_path / "media"),
            "--db",
            str(database),
            "--stream",
            "one=rtsp://admin:secret@camera/one",
            "--stream",
            "two=rtsp://admin:secret@camera/two",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert {stream["stream_id"] for stream in result["streams"]} == {"one", "two"}
    stream_urls = {stream["url"] for stream in result["streams"]}
    assert stream_urls == {
        "rtsp://***:***@camera/one",
        "rtsp://***:***@camera/two",
    }
    assert len(media_calls) == 4
    for stream in result["streams"]:
        event = stream["frames"][0]["events"][0]
        assert event["stream_id"] == stream["stream_id"]
        assert event["rule_id"] == f"cli-full-frame-{stream['stream_id']}"
        assert event["snapshot_uri"]
        assert event["clip_uri"]

    store = SQLiteStore(database)
    rows = store.list_events()
    assert {row["stream_id"] for row in rows} == {"one", "two"}
    assert len(store.list_pending_outbox()) == 2
    store.close()

def test_monitor_cli_uses_stream_input_size_from_topology(monkeypatch, capsys, tmp_path):
    database = tmp_path / "topology-input.db"
    store = SQLiteStore(database)
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "Site"),),
            areas=(Area("area-1", "site-1", "Area"),),
            cameras=(CameraDevice("camera-1", "area-1", "Camera"),),
            streams=(
                VideoStream(
                    "stream-1",
                    "camera-1",
                    "Sub",
                    "rtsp://camera/sub",
                    input_size=320,
                ),
            ),
        )
    )
    store.close()
    monkeypatch.setattr(cli, "YoloOnnxEngine", _FakeEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--topology-db",
            str(database),
            "--input-size",
            "640",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["streams"][0]["input_size"] == 320
    assert result["streams"][0]["fps"] == 2.0
    assert result["fps"] == 2.0

def test_monitor_cli_target_labels_generate_events(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    database = tmp_path / "target-labels.db"
    media_calls = []
    monkeypatch.setattr(
        cli,
        "encode_raw_image",
        lambda _installation, image, output: media_calls.append(("image", image.shape, str(output))) or str(output),
    )
    monkeypatch.setattr(
        cli,
        "encode_raw_clip",
        lambda _installation, frames, output, **_kwargs: media_calls.append(("clip", len(frames), str(output))) or str(output),
    )

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "2",
            "--confirm-frames",
            "1",
            "--confidence",
            "0.25",
            "--target-labels",
            "car,bus",
            "--site-id",
            "site-cli",
            "--area-id",
            "area-cli",
            "--camera-id",
            "camera-cli",
            "--media-dir",
            str(tmp_path / "media"),
            "--db",
            str(database),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["events"] == 1
    assert result["measured_fps"] >= 0
    assert result["frames"][0]["detection_labels"] == {"car": 1}
    assert result["frames"][0]["detection_confidence"] == {"car": 0.82}
    assert result["frames"][0]["target_label_match"] is True
    assert result["frames"][0]["diagnostic_reason"] == "event_generated"
    assert result["frames"][0]["confidence_match"] is True
    assert result["rule_target_labels"] == ["bus", "car"]
    assert result["rule_parameters"][0]["confirm_frames"] == 1
    assert result["rule_parameters"][0]["min_confidence"] == 0.25
    assert result["elapsed_seconds"] >= 0
    assert result["startup_seconds"] >= 0
    assert media_calls and media_calls[0][0] == "image"
    assert media_calls[1][0] == "clip"
    assert result["frames"][0]["events"][0]["snapshot_uri"]
    assert result["frames"][0]["events"][0]["clip_uri"]
    assert result["frames"][0]["events"][0]["clip_status"] == "ready"
    assert result["streams"][0]["entity"] == {
        "site_id": "site-cli",
        "area_id": "area-cli",
        "camera_id": "camera-cli",
        "stream_id": "CH01",
    }

    store = SQLiteStore(database)
    rule = store.load_rules(stream_id="CH01")[0]
    store.close()
    assert rule.target_labels == frozenset({"car", "bus"})
    assert rule.min_confidence == 0.25
    assert rule.actions == ("event", "snapshot", "clip", "notify")
    assert (rule.site_id, rule.area_id, rule.camera_id) == ("site-cli", "area-cli", "camera-cli")

def test_monitor_cli_reports_target_label_mismatch(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    monkeypatch.setattr(cli, "encode_raw_image", lambda _installation, _image, output: str(output))
    monkeypatch.setattr(cli, "encode_raw_clip", lambda _installation, _frames, output, **_kwargs: str(output))

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "2",
            "--confirm-frames",
            "1",
            "--target-labels",
            "person",
            "--db",
            str(tmp_path / "label-mismatch.db"),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["events"] == 0
    assert result["frames"][0]["detection_labels"] == {"car": 1}
    assert result["frames"][0]["detection_confidence"] == {"car": 0.82}
    assert result["rule_target_labels"] == ["person"]
    assert result["frames"][0]["target_label_match"] is False
    assert result["frames"][0]["diagnostic_reason"] == "target_label_mismatch"
    assert result["frames"][0]["confidence_match"] is False

def test_monitor_cli_reports_no_active_rules(monkeypatch, capsys, tmp_path):
    database = tmp_path / "disabled-rule.db"
    rule = Rule(
        "disabled-rule",
        "CH01",
        "Disabled",
        RuleType.POLYGON,
        ((0, 0), (10, 0), (10, 10)),
        target_labels=frozenset({"person"}),
        enabled=False,
    )
    store = SQLiteStore(database)
    store.save_rule(rule)
    store.close()

    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--confirm-frames",
            "1",
            "--db",
            str(database),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["frames"][0]["detection_labels"] == {"car": 1}
    assert result["rule_target_labels"] == []
    assert result["frames"][0]["target_label_match"] is False
    assert result["frames"][0]["diagnostic_reason"] == "no_active_rules"

def test_monitor_cli_reports_confidence_and_confirmation_diagnostics(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    monkeypatch.setattr(cli, "encode_raw_image", lambda _installation, _image, output: str(output))
    monkeypatch.setattr(cli, "encode_raw_clip", lambda _installation, _frames, output, **_kwargs: str(output))

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--confirm-frames",
            "1",
            "--confidence",
            "0.9",
            "--target-labels",
            "car",
            "--db",
            str(tmp_path / "confidence-diagnostic.db"),
        ]
    ) == 0
    confidence_result = json.loads(capsys.readouterr().out)
    assert confidence_result["frames"][0]["target_label_match"] is True
    assert confidence_result["frames"][0]["confidence_match"] is False
    assert confidence_result["frames"][0]["diagnostic_reason"] == "confidence_below_rule"

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--confirm-frames",
            "3",
            "--target-labels",
            "car",
            "--db",
            str(tmp_path / "confirmation-diagnostic.db"),
        ]
    ) == 0
    confirmation_result = json.loads(capsys.readouterr().out)
    assert confirmation_result["frames"][0]["target_label_match"] is True
    assert confirmation_result["frames"][0]["confidence_match"] is True
    assert confirmation_result["frames"][0]["diagnostic_reason"] == "confirmation_or_geometry_pending"

def test_monitor_cli_default_rule_can_enable_http_action(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    monkeypatch.setattr(cli, "encode_raw_image", lambda _installation, _image, output: str(output))
    monkeypatch.setattr(cli, "encode_raw_clip", lambda _installation, _frames, output, **_kwargs: str(output))
    captured = []
    monkeypatch.setattr(
        cli,
        "execute_http_action",
        lambda event, **_kwargs: captured.append(event) or (True, "HTTP 204"),
    )

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--confirm-frames",
            "1",
            "--target-labels",
            "car",
            "--http-action-url",
            "https://actions.example.test/event",
            "--db",
            str(tmp_path / "default-http.db"),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["events"] == 1
    assert result["frames"][0]["detection_labels"] == {"car": 1}
    assert result["frames"][0]["detection_confidence"] == {"car": 0.82}
    assert result["frames"][0]["target_label_match"] is True
    assert result["frames"][0]["diagnostic_reason"] == "event_generated"
    assert result["frames"][0]["confidence_match"] is True
    assert result["rule_target_labels"] == ["car"]
    assert result["measured_fps"] is None
    assert result["elapsed_seconds"] >= 0
    assert result["startup_seconds"] >= 0
    assert len(captured) == 1
    assert captured[0].snapshot_uri
    assert captured[0].clip_uri

def test_monitor_cli_http_action_receives_generated_media(monkeypatch, capsys, tmp_path):
    database = tmp_path / "http-action.db"
    rule = Rule(
        "http-rule",
        "CH01",
        "vehicle-action",
        RuleType.POLYGON,
        ((0, 0), (1280, 0), (1280, 720), (0, 720)),
        target_labels=frozenset({"car"}),
        min_confidence=0.25,
        confirm_frames=1,
        actions=("event", "snapshot", "clip", "http"),
        http_action_url="https://actions.example.test/event",
    )
    store = SQLiteStore(database)
    store.save_rule(rule)
    store.close()

    captured = []
    monkeypatch.setattr(cli, "YoloOnnxEngine", _VehicleEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    monkeypatch.setattr(
        cli,
        "encode_raw_image",
        lambda _installation, _image, output: str(output),
    )
    monkeypatch.setattr(
        cli,
        "encode_raw_clip",
        lambda _installation, _frames, output, **_kwargs: str(output),
    )
    monkeypatch.setattr(
        cli,
        "execute_http_action",
        lambda event, **_kwargs: captured.append(event) or (True, "HTTP 204"),
    )

    assert cli.main(
        [
            "monitor-rtsp",
            "--frames",
            "1",
            "--confirm-frames",
            "1",
            "--target-labels",
            "car",
            "--db",
            str(database),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["events"] == 1
    assert result["frames"][0]["detection_labels"] == {"car": 1}
    assert result["frames"][0]["detection_confidence"] == {"car": 0.82}
    assert result["frames"][0]["target_label_match"] is True
    assert result["frames"][0]["diagnostic_reason"] == "event_generated"
    assert result["frames"][0]["confidence_match"] is True
    assert result["rule_target_labels"] == ["car"]
    assert result["measured_fps"] is None
    assert result["elapsed_seconds"] >= 0
    assert result["startup_seconds"] >= 0
    assert len(captured) == 1
    assert captured[0].snapshot_uri
    assert captured[0].clip_uri
    assert captured[0].clip_status == "ready"

def test_detect_cli_passes_fps_to_capture(monkeypatch, capsys):
    class _FakeDetectCapture:
        def __init__(self, _installation, url, *, width, height, fps=2.0):
            assert url == "rtsp://camera/live"
            assert (width, height, fps) == (320, 180, 7.5)

        def capture(self, frames: int):
            for frame_no in range(frames):
                yield RawFrame(np.zeros((180, 320, 3), dtype=np.uint8), frame_no)

    monkeypatch.setattr(cli, "YoloOnnxEngine", _FakeEngine)
    monkeypatch.setattr(cli, "RTSPCapture", _FakeDetectCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    assert cli.main(
        [
            "detect-rtsp",
            "--url",
            "rtsp://camera/live",
            "--width",
            "320",
            "--height",
            "180",
            "--frames",
            "1",
            "--fps",
            "7.5",
            "--input-size",
            "416",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["fps"] == 7.5
    assert result["input_size"] == 416
    assert result["measured_fps"] is None
    assert result["elapsed_seconds"] >= 0
    assert result["startup_seconds"] >= 0

def test_probe_rtsp_cli_reports_first_frame(monkeypatch, capsys):
    class _FakeCapture:
        def __init__(self, _installation, url, *, width, height, fps=2.0):
            assert url == "rtsp://admin:secret@camera/live"
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
            "rtsp://admin:secret@camera/live",
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
    assert result["url"] == "rtsp://***:***@camera/live"
    assert (result["width"], result["height"]) == (320, 180)
    assert result["source_width"] is None
    assert result["source_height"] is None


def test_probe_rtsp_cli_error_redacts_credentials(monkeypatch, capsys):
    class _FailingCapture:
        def __init__(self, *_args, **_kwargs):
            pass

        def capture_one(self, *, timeout):
            raise RuntimeError("connect rtsp://admin:secret@camera/live failed")

    monkeypatch.setattr(cli, "RTSPCapture", _FailingCapture)
    monkeypatch.setattr(cli, "require_ffmpeg", lambda: FFmpegInstallation(Path("fake.exe"), "fake"))
    with pytest.raises(SystemExit):
        cli.main(
            [
                "probe-rtsp",
                "--url",
                "rtsp://admin:secret@camera/live",
            ]
        )
    error = capsys.readouterr().err
    assert "secret" not in error
    assert "***:***@camera/live" in error

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
