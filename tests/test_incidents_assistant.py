from __future__ import annotations

import io
import urllib.error
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from factoryguard.agent import FactoryAssistant, LLMConfig, build_operational_context
from factoryguard.agent.llm import (
    LLMError,
    OpenAICompatibleClient,
    _chat_endpoint,
    _extract_content,
)
from factoryguard.domain.incidents import Incident, IncidentSeverity, IncidentSource, IncidentStatus
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceCapabilities,
    DeviceProtocol,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.sqlite import SQLiteStore


def _topology() -> EntityRegistry:
    return EntityRegistry(
        sites=(Site("site-1", "星河园区"),),
        areas=(Area("area-gate", "site-1", "东门"), Area("area-warehouse", "site-1", "成品仓库")),
        devices=(
            VideoDevice(
                "device-1",
                "site-1",
                "area-gate",
                "东门摄像机",
                DeviceProtocol.ONVIF,
                "http://192.168.1.10/onvif/device_service",
                capabilities=DeviceCapabilities(live_video=True, native_events=True),
                analysis_policy=AnalysisPolicy.NATIVE_ONLY,
            ),
        ),
        cameras=(CameraDevice("camera-1", "area-gate", "东门摄像机", device_id="device-1"),),
        streams=(
            VideoStream(
                "stream-1",
                "camera-1",
                "子码流",
                "rtsp://192.168.1.10/sub",
                role=StreamRole.SUB,
                analysis_policy=AnalysisPolicy.NATIVE_ONLY,
            ),
        ),
    )


def test_incident_persistence_filters_and_status_updates():
    store = SQLiteStore(":memory:")
    incident = Incident.create(
        title="仓库北门有人闯入",
        description="安保人员正在前往现场",
        severity=IncidentSeverity.HIGH,
        site_id="site-1",
        area_id="area-warehouse",
        source=IncidentSource.USER,
        reporter="值班员",
    )
    assert store.create_incident(incident)
    assert not store.create_incident(incident)
    assert [item.id for item in store.list_incidents(area_id="area-warehouse")] == [incident.id]
    assert store.list_incidents(severity=IncidentSeverity.LOW) == []
    assert store.update_incident_status(incident.id, IncidentStatus.ACKNOWLEDGED, actor="tester")
    stored = store.get_incident(incident.id)
    assert stored is not None
    assert stored.status is IncidentStatus.ACKNOWLEDGED
    audit_entries = store.list_incident_audit(incident.id)
    assert [entry["action"] for entry in audit_entries] == [
        "incident.status.changed",
        "incident.created",
    ]
    assert audit_entries[0]["actor"] == "tester"

    assert store.update_incident_status(incident.id, IncidentStatus.RESOLVED, actor="tester")
    with pytest.raises(ValueError, match="先重新打开"):
        store.update_incident_status(incident.id, IncidentStatus.FALSE_ALARM, actor="tester")
    assert store.update_incident_status(
        incident.id,
        IncidentStatus.OPEN,
        actor="tester",
        note="reopened from resolved",
    )
    assert store.update_incident_status(incident.id, IncidentStatus.RESOLVED, actor="tester")
    with pytest.raises(ValueError, match="先重新打开"):
        store.update_incident_status(incident.id, IncidentStatus.ACKNOWLEDGED, actor="tester")
    assert store.get_incident(incident.id).status is IncidentStatus.RESOLVED
    assert len(store.list_incident_audit(incident.id)) == 5
    store.close()


def test_operational_context_reports_camera_health_and_empty_state():
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    context = build_operational_context(store)
    assert len(context.cameras) == 1
    assert context.cameras[0].status_label == "待检测"
    assert context.unknown_cameras == context.cameras
    assert "尚未完成状态检测" in context.dashboard_summary()
    store.set_stream_health("stream-1", "online", "1280×720", actor="test")
    context = build_operational_context(store)
    assert context.cameras[0].is_online
    assert context.online_cameras == context.cameras
    prompt = context.to_prompt()
    assert "东门摄像机" in prompt
    assert "只能基于以上快照" in prompt
    store.close()


def test_operational_context_counts_nonstandard_health_as_pending():
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    store.set_stream_health(
        "stream-1",
        "probing",
        "检测进行中",
        updated_at=now,
        actor="test",
    )

    context = build_operational_context(store, now=now)

    assert context.cameras[0].status_label == "待检测"
    assert context.unknown_cameras == context.cameras
    assert "尚未完成状态检测" in context.dashboard_summary()
    store.close()


def test_operational_context_does_not_claim_unverified_device_as_online():
    store = SQLiteStore(":memory:")
    topology = _topology()
    store.save_topology(
        EntityRegistry(
            sites=topology.sites,
            areas=topology.areas,
            devices=(replace(topology.devices[0], health_status="ready"),),
            cameras=topology.cameras,
            streams=topology.streams,
        )
    )

    context = build_operational_context(store, now=datetime(2026, 11, 5, 12, 0, tzinfo=UTC))

    assert context.cameras[0].status_label == "待检测"
    assert not context.cameras[0].is_online
    assert context.cameras[0].health_detail == ""
    store.close()


def test_operational_context_marks_stale_stream_health_as_unverified():
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    store.set_stream_health(
        "stream-1",
        "online",
        "1280×720",
        updated_at=now - timedelta(minutes=31),
        actor="test",
    )

    context = build_operational_context(store, now=now)

    camera = context.cameras[0]
    assert camera.status_label == "待检测"
    assert camera.health_status == "unknown"
    assert "已过期" in camera.health_detail
    assert camera.health_checked_text.startswith("检测于")
    store.close()


def test_operational_context_uses_the_same_best_stream_as_preview():
    store = SQLiteStore(":memory:")
    topology = _topology()
    main_stream = replace(
        topology.streams[0],
        id="stream-main",
        name="主码流",
        role=StreamRole.MAIN,
        url="rtsp://camera/main",
    )
    sub_stream = replace(
        topology.streams[0],
        id="stream-sub",
        name="子码流",
        role=StreamRole.SUB,
        url="rtsp://camera/sub",
    )
    store.save_topology(
        EntityRegistry(
            sites=topology.sites,
            areas=topology.areas,
            devices=topology.devices,
            cameras=topology.cameras,
            streams=(main_stream, sub_stream),
        )
    )
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    store.set_stream_health("stream-main", "online", "1920×1080", updated_at=now, actor="test")
    store.set_stream_health(
        "stream-sub", "offline", "预览码流连接失败", updated_at=now, actor="test"
    )

    camera = build_operational_context(store, now=now).cameras[0]

    assert camera.best_stream is not None
    assert camera.best_stream.id == "stream-sub"
    assert camera.is_offline
    assert camera.health_detail == "预览码流连接失败"
    store.close()


def test_operational_context_accepts_fresh_device_probe_and_degradation():
    store = SQLiteStore(":memory:")
    topology = _topology()
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    ready = replace(
        topology.devices[0],
        health_status="ready",
        health_detail="ONVIF connected",
        last_probe_at=now.isoformat(),
    )
    store.save_topology(
        EntityRegistry(
            sites=topology.sites,
            areas=topology.areas,
            devices=(ready,),
            cameras=topology.cameras,
            streams=topology.streams,
        )
    )

    assert build_operational_context(store, now=now).cameras[0].is_online

    degraded = replace(ready, health_status="degraded", health_detail="认证失败")
    store.save_topology(
        EntityRegistry(
            sites=topology.sites,
            areas=topology.areas,
            devices=(degraded,),
            cameras=topology.cameras,
            streams=topology.streams,
        )
    )
    camera = build_operational_context(store, now=now).cameras[0]
    assert camera.is_offline
    assert camera.health_detail == "认证失败"
    store.close()


def test_context_disambiguates_same_area_name_across_sites(tmp_path):
    from factoryguard.domain.models import Area, CameraDevice, DeviceProtocol, Site, VideoDevice
    from factoryguard.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "context-labels.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-a", "一号店"), Site("site-b", "二号店")),
            areas=(
                Area("area-a", "site-a", "东门"),
                Area("area-b", "site-b", "东门"),
                Area("area-c", "site-a", "仓库"),
            ),
            devices=(
                VideoDevice(
                    "device-a",
                    "site-a",
                    "area-a",
                    "一号店东门设备",
                    DeviceProtocol.RTSP,
                    "rtsp://a/1",
                ),
                VideoDevice(
                    "device-b",
                    "site-b",
                    "area-b",
                    "二号店东门设备",
                    DeviceProtocol.RTSP,
                    "rtsp://b/1",
                ),
                VideoDevice(
                    "device-c",
                    "site-a",
                    "area-c",
                    "一号店仓库设备",
                    DeviceProtocol.RTSP,
                    "rtsp://c/1",
                ),
            ),
            cameras=(
                CameraDevice("camera-a", "area-a", "东门摄像机", device_id="device-a"),
                CameraDevice("camera-b", "area-b", "东门摄像机", device_id="device-b"),
                CameraDevice("camera-c", "area-c", "仓库摄像机", device_id="device-c"),
            ),
        )
    )
    context = build_operational_context(store)

    assert context.area_name("area-a") == "一号店 · 东门"
    assert context.area_name("area-b") == "二号店 · 东门"
    assert context.area_name("area-c") == "仓库"
    assert context.camera_name("camera-a") == "一号店 · 东门摄像机"
    assert context.camera_name("camera-b") == "二号店 · 东门摄像机"
    assert context.camera_name("camera-c") == "仓库摄像机"
    store.close()


def test_assistant_answers_grounded_question_and_persists_history():
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)
    prepared = assistant.prepare("哪些摄像头离线了？")
    assert prepared.incident is None
    assert "没有已确认离线" in prepared.local_reply.text
    assistant.save_exchange(prepared, prepared.local_reply)
    history = assistant.history()
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "哪些摄像头离线了？"
    store.close()


def test_assistant_creates_structured_incident_from_chat_report():
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)
    prepared = assistant.prepare("上报险情：成品仓库北门有人翻越围栏，目前仍在现场")
    assert prepared.incident is not None
    assert prepared.incident.area_id == "area-warehouse"
    assert prepared.incident.severity is IncidentSeverity.HIGH
    assert prepared.incident.source is IncidentSource.AI_AGENT
    assert prepared.local_reply.incident_id == prepared.incident.id
    assert prepared.citations == (prepared.incident.id,)
    prompt = build_operational_context(store).to_prompt()
    assert prompt.count("【最近险情】") == 1
    assert "【最近设备事件】" not in prompt
    incidents = store.list_incidents()
    assert len(incidents) == 1
    assert incidents[0].title.startswith("成品仓库北门")
    assert prepared.incident_created is True

    duplicate = assistant.prepare(
        "上报险情：成品仓库北门有人翻越围栏，目前仍在现场",
        now=prepared.incident.occurred_at + timedelta(minutes=1),
    )
    assert duplicate.incident is not None
    assert duplicate.incident.id == prepared.incident.id
    assert duplicate.incident_created is False
    assert "未重复创建" in duplicate.local_reply.text
    assert len(store.list_incidents()) == 1
    store.close()


def test_assistant_matches_short_area_names_when_reporting():
    """Owners say "仓库" for "成品仓库"; the incident must keep its area."""
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：仓库门口有人闯入")

    assert prepared.incident is not None
    assert prepared.incident.area_id == "area-warehouse"
    assert prepared.incident.area_name_snapshot == "成品仓库"
    store.close()


def test_assistant_matches_camera_by_short_name():
    """A camera named "东门高清摄像头" is still found when the owner says "东门"."""
    store = SQLiteStore(":memory:")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-gate", "site-1", "东侧大门"),),
            devices=(
                VideoDevice(
                    "device-1",
                    "site-1",
                    "area-gate",
                    "大门 NVR",
                    DeviceProtocol.ONVIF,
                    "http://192.168.1.10/onvif/device_service",
                ),
            ),
            cameras=(
                CameraDevice(
                    "camera-1",
                    "area-gate",
                    "东大门高清摄像头",
                    device_id="device-1",
                ),
            ),
        )
    )
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：东大门有人闯入")

    assert prepared.incident is not None
    assert prepared.incident.camera_id == "camera-1"
    assert prepared.incident.area_id == "area-gate"
    store.close()


def test_incident_can_be_filed_to_an_area_after_the_report(tmp_path):
    """A report from the field often has no place; the operator files it later."""
    store = SQLiteStore(tmp_path / "incident-location.db")
    store.save_topology(_topology())
    incident = Incident.create(
        title="有人闯入",
        description="门口有人闯入",
        severity=IncidentSeverity.MEDIUM,
        site_id="site-1",
    )
    assert store.create_incident(incident, actor="tester")

    assert store.assign_incident_location(
        incident.id,
        area_id="area-warehouse",
        actor="merchant-console",
    )
    stored = store.get_incident(incident.id)
    assert stored is not None
    assert stored.area_id == "area-warehouse"
    assert stored.area_name_snapshot == "成品仓库"
    assert stored.site_id == "site-1"

    audit = store.list_incident_audit(incident.id)
    assert any(item["action"] == "incident.location.changed" for item in audit)
    store.close()


def test_incident_location_rejects_a_camera_from_another_area(tmp_path):
    store = SQLiteStore(tmp_path / "incident-location-mismatch.db")
    store.save_topology(_topology())
    incident = Incident.create(title="测试", description="测试")
    assert store.create_incident(incident, actor="tester")

    with pytest.raises(ValueError):
        store.assign_incident_location(
            incident.id,
            area_id="area-warehouse",
            camera_id="camera-1",
            actor="merchant-console",
        )
    store.close()


def test_assistant_does_not_guess_between_similar_areas():
    """Two matching areas are ambiguous, so the incident stays unfiled."""
    store = SQLiteStore(":memory:")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(
                Area("area-a", "site-1", "成品仓库"),
                Area("area-b", "site-1", "原料仓库"),
            ),
        )
    )
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：仓库门口有人闯入")

    assert prepared.incident is not None
    assert prepared.incident.area_id is None
    assert "险情中心" in prepared.local_reply.text
    store.close()


def test_assistant_tells_the_operator_when_no_area_was_matched():
    """A location-less incident must say so instead of silently losing the place."""
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：有人闯进来了")

    assert prepared.incident is not None
    assert prepared.incident.area_id is None
    assert "未指定区域" in prepared.local_reply.text
    assert "险情中心" in prepared.local_reply.text
    store.close()


def test_incident_keeps_area_name_after_area_is_removed(tmp_path):
    """History must stay readable after the area is deleted."""
    store = SQLiteStore(tmp_path / "incident-snapshot.db")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：东门摄像机拍到有人闯入")
    assert prepared.incident is not None
    assert prepared.incident.area_name_snapshot == "东门"
    assert prepared.incident.camera_name_snapshot == "东门摄像机"

    # The operator renames the area afterwards; history keeps the old name.
    store.save_area_bundle(
        Site("site-1", "星河园区"), Area("area-gate", "site-1", "正门"), actor="operator"
    )
    context = build_operational_context(store)

    assert (
        context.area_name(prepared.incident.area_id, snapshot=prepared.incident.area_name_snapshot)
        == "东门"
    )
    assert (
        context.camera_name(
            prepared.incident.camera_id, snapshot=prepared.incident.camera_name_snapshot
        )
        == "东门摄像机"
    )
    store.close()


def test_incident_keeps_area_name_after_area_is_deleted(tmp_path):
    """A removed area must not erase the location recorded with the incident."""
    store = SQLiteStore(tmp_path / "incident-snapshot-deleted.db")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：东门摄像机拍到有人闯入")
    assert prepared.incident is not None
    assert prepared.incident.area_name_snapshot == "东门"

    store.delete_camera("camera-1", actor="operator")
    store.delete_area("area-gate", actor="operator")
    context = build_operational_context(store)

    assert (
        context.area_name(prepared.incident.area_id, snapshot=prepared.incident.area_name_snapshot)
        == "东门"
    )
    assert (
        context.camera_name(
            prepared.incident.camera_id, snapshot=prepared.incident.camera_name_snapshot
        )
        == "东门摄像机"
    )
    store.close()


def test_assistant_incident_inherits_area_from_matched_camera():
    """A report naming only a camera still keeps its area for reporting."""
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)

    prepared = assistant.prepare("上报险情：东门摄像机拍到有人闯入")

    assert prepared.incident is not None
    assert prepared.incident.camera_id == "camera-1"
    assert prepared.incident.area_id == "area-gate"
    assert prepared.incident.site_id == "site-1"
    store.close()


def test_assistant_requests_details_before_creating_empty_incident():
    store = SQLiteStore(":memory:")
    assistant = FactoryAssistant(store)
    prepared = assistant.prepare("帮我上报险情")
    assert prepared.incident is None
    assert "请直接描述" in prepared.local_reply.text
    assert store.list_incidents() == []
    store.close()


def test_llm_config_rejects_invalid_endpoint_and_timeout():
    with pytest.raises(ValueError, match="http"):
        LLMConfig(endpoint="llm.example.test", model="demo", api_key="secret")
    with pytest.raises(ValueError, match="超时"):
        LLMConfig(endpoint="https://llm.example.test/v1", timeout=0)


def test_llm_config_requires_endpoint_model_and_key(monkeypatch):
    assert not LLMConfig().available
    assert not LLMConfig(
        endpoint="https://example.test/v1/chat/completions",
        model="demo-model",
        api_key="secret",
        enabled=False,
    ).available
    monkeypatch.setenv("FACTORYGUARD_LLM_ENDPOINT", "https://example.test/v1/chat/completions")
    monkeypatch.setenv("FACTORYGUARD_LLM_MODEL", "demo-model")
    monkeypatch.setenv("FACTORYGUARD_LLM_API_KEY", "secret")
    config = LLMConfig.from_environment()
    assert config.available
    assert config.model == "demo-model"
    assert config.api_key == "secret"


def test_llm_endpoint_and_chat_response_parsing(monkeypatch):
    assert (
        _chat_endpoint("https://llm.example.test/v1")
        == "https://llm.example.test/v1/chat/completions"
    )
    assert (
        _chat_endpoint("https://llm.example.test/v1/chat/completions")
        == "https://llm.example.test/v1/chat/completions"
    )
    assert _extract_content({"choices": [{"message": {"content": "现场正常"}}]}) == "现场正常"

    def fail_request(_request, *, timeout):
        del timeout
        raise urllib.error.HTTPError(
            "https://llm.example.test/v1/chat/completions",
            401,
            "unauthorized",
            {},
            io.BytesIO(b'{"error":"bad key: provider-secret"}'),
        )

    monkeypatch.setattr("factoryguard.agent.llm.urllib.request.urlopen", fail_request)
    with pytest.raises(LLMError) as raised:
        OpenAICompatibleClient(
            LLMConfig(
                endpoint="https://llm.example.test/v1",
                model="demo",
                api_key="provider-secret",
            )
        ).complete(system_prompt="system", user_prompt="user")
    assert "provider-secret" not in str(raised.value)
    assert "***" in str(raised.value)
    assert "文字问询服务" in str(raised.value)
    assert "大模型" not in str(raised.value)


def test_llm_http_error_response_is_closed(monkeypatch):
    """A failed request must not leave the error socket open."""
    closed: list[bool] = []

    class TrackedError(urllib.error.HTTPError):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def fail_request(_request, *, timeout):
        del timeout
        raise TrackedError(
            "https://llm.example.test/v1/chat/completions",
            401,
            "unauthorized",
            {},
            io.BytesIO(b'{"error":"bad key"}'),
        )

    monkeypatch.setattr("factoryguard.agent.llm.urllib.request.urlopen", fail_request)
    with pytest.raises(LLMError):
        OpenAICompatibleClient(
            LLMConfig(
                endpoint="https://llm.example.test/v1",
                model="demo",
                api_key="provider-secret",
            )
        ).complete(system_prompt="system", user_prompt="user")
    assert closed == [True]


def test_incident_csv_export_is_readable_by_excel(tmp_path):
    from factoryguard.storage.incident_export import COLUMNS, write_incident_csv

    incident = Incident.create(
        title="仓库北门有人闯入",
        description="已通知安保。",
        severity=IncidentSeverity.HIGH,
        site_id="site-1",
        area_id="area-warehouse",
        camera_id="cam-1",
        reporter="当前用户",
    )
    target = write_incident_csv(
        tmp_path / "报告.csv",
        [incident],
        area_names={"area-warehouse": "成品仓库"},
        camera_names={"cam-1": "北门摄像机"},
        site_names={"site-1": "星河园区"},
        audit_trail={incident.id: ["2026-11-06 10:00:00 当前用户 incident.created"]},
    )

    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].split(",") == list(COLUMNS)
    assert "仓库北门有人闯入" in text
    assert "成品仓库" in text
    assert "北门摄像机" in text
    assert "星河园区" in text
    assert "高危" in text
    assert "当前用户" in text
    assert "incident.created" in text


def test_incident_export_uses_business_labels_for_the_audit_trail(tmp_path):
    """The spreadsheet must not contain technical action names."""
    from factoryguard.storage.incident_export import write_incident_csv
    from factoryguard.ui.merchant_console import _audit_action_label

    assert _audit_action_label("incident.created") == "创建险情"
    assert _audit_action_label("incident.location.changed") == "补充位置"
    assert _audit_action_label("incident.status.changed") == "状态变更"

    incident = Incident.create(title="门口有人闯入", description="现场反馈")
    target = write_incident_csv(
        tmp_path / "audit.csv",
        [incident],
        audit_trail={incident.id: ["2026-11-06 10:00:00 当前用户 补充位置"]},
    )

    text = target.read_text(encoding="utf-8-sig")
    assert "补充位置" in text
    assert "incident.location.changed" not in text


def test_incident_csv_export_uses_snapshots_when_topology_is_gone(tmp_path):
    from factoryguard.storage.incident_export import write_incident_csv

    incident = Incident.create(
        title="已撤门店的险情",
        area_id="area-old",
        camera_id="cam-old",
        area_name="东门",
        camera_name="东门摄像机",
    )
    target = write_incident_csv(tmp_path / "历史.csv", [incident])
    text = target.read_text(encoding="utf-8-sig")
    assert "东门" in text
    assert "东门摄像机" in text


def test_incident_export_uses_site_snapshot_after_site_is_removed(tmp_path):
    """The exported report must still name the site the incident happened at."""
    from factoryguard.storage.incident_export import write_incident_csv

    incident = Incident.create(
        title="已撤门店的险情",
        site_id="site-1",
        area_id="area-1",
        site_name="一号店",
        area_name="东门",
    )
    target = write_incident_csv(tmp_path / "门店报告.csv", [incident])
    text = target.read_text(encoding="utf-8-sig")
    assert "一号店" in text
    assert "东门" in text


def test_assistant_offline_answer_is_bounded_and_actionable():
    from factoryguard.domain.models import (
        DeviceCapabilities,
        DeviceProtocol,
        VideoDevice,
        VideoStream,
    )

    store = SQLiteStore(":memory:")
    devices = []
    cameras = []
    streams = []
    for index in range(9):
        device = VideoDevice(
            f"dev-{index}",
            "site-1",
            "area-warehouse",
            f"设备{index}",
            DeviceProtocol.RTSP,
            f"rtsp://camera/{index}",
            capabilities=DeviceCapabilities(live_video=True),
        )
        devices.append(device)
        cameras.append(
            CameraDevice(f"cam-{index}", "area-warehouse", f"摄像头{index}", device_id=device.id)
        )
        streams.append(
            VideoStream(f"stream-{index}", f"cam-{index}", "子码流", f"rtsp://camera/{index}")
        )
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-warehouse", "site-1", "成品仓库"),),
            devices=tuple(devices),
            cameras=tuple(cameras),
            streams=tuple(streams),
        )
    )
    for index in range(9):
        store.set_stream_health(f"stream-{index}", "offline", "连接超时", actor="t")
    assistant = FactoryAssistant(store)

    reply = assistant.prepare("哪些摄像头离线了？").local_reply.text

    assert "9 台" in reply
    assert "摄像头0" in reply
    assert "摄像头8" not in reply
    assert "实时画面" in reply
    store.close()


def test_assistant_incident_summary_mentions_remaining_records():
    store = SQLiteStore(":memory:")
    store.save_topology(_topology())
    assistant = FactoryAssistant(store)
    for index in range(7):
        incident = Incident.create(
            title=f"事件 {index}",
            severity=IncidentSeverity.MEDIUM,
            area_id="area-warehouse",
        )
        store.create_incident(incident, actor="tester")

    reply = assistant.prepare("现在有哪些险情？").local_reply.text

    assert "7 条未关闭险情" in reply
    assert "另有 2 条未关闭险情" in reply
    assert "险情中心" in reply
    store.close()
