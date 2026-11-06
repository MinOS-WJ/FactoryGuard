import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from factoryguard.agent.context import build_operational_context
from factoryguard.domain.incidents import Incident, IncidentSeverity, IncidentStatus
from factoryguard.domain.models import (
    AnalysisPolicy,
    Area,
    CameraDevice,
    DeviceProtocol,
    Event,
    EventSource,
    EventStatus,
    Rule,
    Site,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.schedules import ArmingSchedule, TimeWindow
from factoryguard.domain.topology import EntityRegistry
from factoryguard.storage.sqlite import SQLiteStore


def _event(rule: Rule | None = None) -> Event:
    rule_id = rule.id if rule is not None else "native:person"
    channel_id = rule.channel_id if rule is not None else "CH01"
    stream_id = rule.stream_id if rule is not None else "CH01"
    target = {
        "label": "person",
        "rule_name": rule.name if rule is not None else "设备原生告警",
        "assignees": list(rule.assignees) if rule is not None else [],
    }
    return Event(
        id=f"evt-{uuid4().hex}",
        dedup_key=f"test:{uuid4().hex}",
        rule_id=rule_id,
        channel_id=channel_id,
        stream_id=stream_id,
        level="high",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=target,
        assignees=rule.assignees if rule is not None else (),
        source=EventSource.SYSTEM,
    )


def test_event_outbox_transaction_is_idempotent(tmp_path):
    store = SQLiteStore(tmp_path / "idempotency.db")
    rule = Rule("r1", "CH01", "fence", "polygon", ((0, 0), (10, 0), (10, 10), (0, 10)))
    event = _event(rule=rule)
    assert store.create_event_with_outbox(event)
    assert not store.create_event_with_outbox(event)
    assert len(store.list_events()) == 1
    assert len(store.search_events(query="person", level="high", channel_id="CH01")) == 1
    assert len(store.list_pending_outbox()) == 1
    store.close()


def test_sqlite_store_creates_database_parent(tmp_path):
    database = tmp_path / "nested" / "events.db"
    store = SQLiteStore(database)
    assert database.exists()
    assert store.integrity_check() == "ok"
    store.close()
    store.close()


def test_sqlite_store_creates_integrity_checked_backup(tmp_path):
    source = tmp_path / "factoryguard.db"
    destination = tmp_path / "backups" / "factoryguard-copy.db"
    store = SQLiteStore(source)
    try:
        backup = store.backup_to(destination)
    finally:
        store.close()

    assert backup == destination.resolve()
    copy = SQLiteStore(destination)
    try:
        assert copy.integrity_check() == "ok"
    finally:
        copy.close()


def test_save_camera_bundle_rejects_inconsistent_references(tmp_path):
    store = SQLiteStore(tmp_path / "camera-bundle-invalid.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
        )
    )
    device = VideoDevice(
        "device-1",
        "site-1",
        "area-1",
        "摄像头",
        DeviceProtocol.RTSP,
        "rtsp://camera/1",
    )
    camera = CameraDevice("camera-1", "area-1", "摄像头", device_id="device-other")
    stream = VideoStream("stream-1", "camera-other", "主码流", "rtsp://camera/1")
    with pytest.raises(ValueError, match="device_id"):
        store.save_camera_bundle(device, camera, stream, actor="operator")
    with pytest.raises(ValueError, match="stream camera_id"):
        store.save_camera_bundle(
            device,
            CameraDevice("camera-1", "area-1", "摄像头", device_id=device.id),
            stream,
            actor="operator",
        )
    store.close()


def test_save_area_bundle_is_atomic_on_child_failure(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "area-bundle.db")
    site = Site("site-new", "星河园区")
    area = Area("area-new", site.id, "仓库")

    def fail_area(_connection, _area):
        raise RuntimeError("area write failed")

    monkeypatch.setattr(store, "_upsert_area", fail_area)
    with pytest.raises(RuntimeError, match="area write failed"):
        store.save_area_bundle(site, area, actor="operator")

    topology = store.load_topology()
    assert all(item.id != "site-new" for item in topology.sites)
    assert all(item.id != "area-new" for item in topology.areas)
    store.close()


def test_save_camera_bundle_is_atomic_on_child_failure(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "camera-bundle.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
        )
    )
    device = VideoDevice(
        "device-new",
        "site-1",
        "area-1",
        "新摄像头",
        DeviceProtocol.RTSP,
        "rtsp://camera/new",
    )
    camera = CameraDevice("camera-new", "area-1", "新摄像头", device_id=device.id)
    stream = VideoStream("stream-new", "camera-new", "主码流", "rtsp://camera/new")

    def fail_stream(_connection, _stream):
        raise RuntimeError("stream write failed")

    monkeypatch.setattr(store, "_upsert_stream", fail_stream)
    with pytest.raises(RuntimeError, match="stream write failed"):
        store.save_camera_bundle(device, camera, stream, actor="operator")

    topology = store.load_topology()
    assert all(item.id != "device-new" for item in topology.devices)
    assert all(item.id != "camera-new" for item in topology.cameras)
    assert all(item.id != "stream-new" for item in topology.streams)
    store.close()


def test_save_area_bundle_rejects_duplicate_name_in_same_site(tmp_path):
    store = SQLiteStore(tmp_path / "area-duplicate.db")
    site = Site("site-1", "星河园区")
    store.save_area_bundle(site, Area("area-1", site.id, "东门"), actor="operator")

    with pytest.raises(ValueError, match="unique"):
        store.save_area_bundle(site, Area("area-2", site.id, "东门"), actor="operator")

    with pytest.raises(ValueError, match="unique"):
        store.save_area_bundle(site, Area("area-3", site.id, "东门"), actor="operator")

    areas = store.load_topology().areas
    assert [item.id for item in areas] == ["area-1"]
    store.close()


def test_save_area_bundle_allows_same_area_name_across_sites(tmp_path):
    store = SQLiteStore(tmp_path / "area-duplicate-sites.db")
    store.save_area_bundle(
        Site("site-a", "门店"), Area("area-a", "site-a", "东门"), actor="operator"
    )
    store.save_area_bundle(
        Site("site-b", "门店"), Area("area-b", "site-b", "东门"), actor="operator"
    )

    areas = {item.id: item for item in store.load_topology().areas}
    assert set(areas) == {"area-a", "area-b"}
    assert areas["area-b"].site_id == "site-b"
    store.close()


def test_save_device_camera_batch_saves_shared_device_channels(tmp_path):
    store = SQLiteStore(tmp_path / "camera-import-success.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
        )
    )
    device = VideoDevice(
        "device-gb",
        "site-1",
        "area-1",
        "国标 NVR",
        DeviceProtocol.GB28181,
        "gb28181://34020000001320000001",
        device_id="34020000001320000001",
    )
    cameras = (
        CameraDevice("camera-gb-1", "area-1", "东门", device_id=device.id, channel_id="1"),
        CameraDevice("camera-gb-2", "area-1", "西门", device_id=device.id, channel_id="2"),
    )
    store.save_device_camera_batch((device,), cameras, actor="operator")

    topology = store.load_topology()
    assert [item.id for item in topology.devices] == [device.id]
    assert {item.channel_id for item in topology.cameras} == {"1", "2"}
    store.close()


def test_save_device_camera_batch_is_atomic_across_channels(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "camera-import-atomic.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
        )
    )
    device = VideoDevice(
        "device-gb",
        "site-1",
        "area-1",
        "国标 NVR",
        DeviceProtocol.GB28181,
        "gb28181://34020000001320000001",
        device_id="34020000001320000001",
    )
    cameras = (
        CameraDevice("camera-gb-1", "area-1", "东门", device_id=device.id, channel_id="1"),
        CameraDevice("camera-gb-2", "area-1", "西门", device_id=device.id, channel_id="2"),
    )

    def fail_second_channel(_connection, camera):
        if camera.id == "camera-gb-2":
            raise RuntimeError("channel write failed")

    monkeypatch.setattr(store, "_upsert_camera", fail_second_channel)
    with pytest.raises(RuntimeError, match="channel write failed"):
        store.save_device_camera_batch((device,), cameras, actor="operator")

    topology = store.load_topology()
    assert all(item.id != device.id for item in topology.devices)
    assert all(not item.id.startswith("camera-gb-") for item in topology.cameras)
    store.close()


def test_save_device_camera_batch_rejects_area_mismatch(tmp_path):
    store = SQLiteStore(tmp_path / "camera-import-area.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(
                Area("area-1", "site-1", "仓库"),
                Area("area-2", "site-1", "东门"),
            ),
        )
    )
    device = VideoDevice(
        "device-gb",
        "site-1",
        "area-1",
        "国标 NVR",
        DeviceProtocol.GB28181,
        "gb28181://34020000001320000001",
        device_id="34020000001320000001",
    )
    camera = CameraDevice(
        "camera-gb-1",
        "area-2",
        "东门",
        device_id=device.id,
        channel_id="1",
    )
    with pytest.raises(ValueError, match="area_id"):
        store.save_device_camera_batch((device,), (camera,), actor="operator")

    topology = store.load_topology()
    assert all(item.id != device.id for item in topology.devices)
    assert all(item.id != camera.id for item in topology.cameras)
    store.close()


def test_referenced_credential_refs_only_lists_devices_in_use(tmp_path):
    store = SQLiteStore(tmp_path / "credential-refs.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "星河园区"),),
            areas=(Area("area-1", "site-1", "仓库"),),
            devices=(
                VideoDevice(
                    "device-1",
                    "site-1",
                    "area-1",
                    "在用设备",
                    DeviceProtocol.RTSP,
                    "rtsp://camera/1",
                    credentials_ref="cred-used",
                ),
                VideoDevice(
                    "device-2",
                    "site-1",
                    "area-1",
                    "无凭据设备",
                    DeviceProtocol.RTSP,
                    "rtsp://camera/2",
                ),
            ),
        )
    )

    assert store.referenced_credential_refs() == ("cred-used",)
    store.close()


def test_delete_site_requires_no_areas(tmp_path):
    store = SQLiteStore(tmp_path / "site-delete.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "一号店"),),
            areas=(Area("area-1", "site-1", "东门"),),
        )
    )

    with pytest.raises(ValueError, match="区域"):
        store.delete_site("site-1", actor="operator")

    assert any(item.id == "site-1" for item in store.load_topology().sites)

    store.delete_area("area-1", actor="operator")
    assert store.delete_site("site-1", actor="operator")
    assert all(item.id != "site-1" for item in store.load_topology().sites)
    assert store.delete_site("site-missing", actor="operator") is False
    store.close()


def test_prune_history_removes_old_events_and_audit_but_keeps_incidents(tmp_path):
    store = SQLiteStore(tmp_path / "prune-history.db")
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=200)
    recent = now - timedelta(days=10)

    old_event = replace(_event(), occurred_at=old)
    store.create_event_with_outbox(old_event, channels=("webhook",), actor="tester")
    old_claim = store.claim_pending_outbox(now=old)[0]
    store.mark_outbox_sent(old_claim["id"], sent_at=old)
    recent_event = replace(_event(), dedup_key="recent:test", occurred_at=recent)
    store.create_event_with_outbox(recent_event, channels=("webhook",), actor="tester")
    recent_claim = store.claim_pending_outbox(now=recent)[0]
    store.mark_outbox_sent(recent_claim["id"], sent_at=recent)

    incident = Incident.create(
        title="保留的险情",
        severity=IncidentSeverity.HIGH,
        occurred_at=old,
    )
    store.create_incident(incident, actor="tester")

    report = store.prune_history(retention_days=90, now=now)

    assert report["outbox"] == 1
    assert report["events"] == 1
    assert report["audit"] >= 1
    remaining = [row["id"] for row in store.list_events()]
    assert old_event.id not in remaining
    assert recent_event.id in remaining
    assert [item.id for item in store.list_incidents()] == [incident.id]
    store.close()


def test_prune_history_trims_old_chat_records(tmp_path):
    """Chat history is a working buffer and must not grow forever."""
    store = SQLiteStore(tmp_path / "prune-assistant.db")
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)

    store.append_assistant_message("merchant-main", role="user", content="旧记录")
    store.append_assistant_message("merchant-main", role="assistant", content="旧回答")
    assert len(store.list_assistant_messages("merchant-main", limit=50)) == 2

    # Backdate the rows so the retention window really expires them.
    connection = store._connection_or_raise()
    with connection:
        connection.execute(
            "UPDATE assistant_message SET created_at = ?",
            ((now - timedelta(days=200)).isoformat(),),
        )

    report = store.prune_history(retention_days=90, now=now)

    assert report["assistant"] == 2
    assert store.list_assistant_messages("merchant-main", limit=50) == []
    store.close()


def test_prune_history_keeps_recent_chat_records(tmp_path):
    """Recent chat must survive a normal retention sweep."""
    store = SQLiteStore(tmp_path / "prune-assistant-keep.db")
    now = datetime.now(UTC)

    store.append_assistant_message("merchant-main", role="user", content="新记录")
    assert len(store.list_assistant_messages("merchant-main", limit=50)) == 1

    report = store.prune_history(retention_days=90, now=now)

    assert report["assistant"] == 0
    assert len(store.list_assistant_messages("merchant-main", limit=50)) == 1
    store.close()


def test_prune_history_rejects_invalid_retention(tmp_path):
    store = SQLiteStore(tmp_path / "prune-invalid.db")
    with pytest.raises(ValueError, match="retention_days"):
        store.prune_history(retention_days=-1)
    with pytest.raises(ValueError, match="retention_days"):
        store.prune_history(retention_days=float("inf"))
    store.close()


def test_checkpoint_wal_keeps_database_usable(tmp_path):
    store = SQLiteStore(tmp_path / "checkpoint.db")
    store.prune_history(retention_days=90)
    store.checkpoint_wal()
    assert store.integrity_check() == "ok"
    store.close()


def test_delete_camera_preserves_shared_device_history_and_audit(tmp_path):
    store = SQLiteStore(tmp_path / "camera-delete.db")
    try:
        device = VideoDevice(
            "device-shared",
            "site-1",
            "area-1",
            "NVR",
            DeviceProtocol.RTSP,
            "rtsp://camera/live",
            credentials_ref="credential-shared",
            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        )
        cameras = (
            CameraDevice("camera-1", "area-1", "通道一", device_id=device.id),
            CameraDevice("camera-2", "area-1", "通道二", device_id=device.id),
        )
        streams = (
            VideoStream("stream-1", "camera-1", "主码流", "rtsp://camera/1"),
            VideoStream("stream-2", "camera-2", "主码流", "rtsp://camera/2"),
        )
        store.save_topology(
            EntityRegistry(
                sites=(Site("site-1", "Main"),),
                areas=(Area("area-1", "site-1", "Yard"),),
                devices=(device,),
                cameras=cameras,
                streams=streams,
            )
        )
        store.set_stream_health("stream-1", "ready", "ok", actor="test")
        store.set_stream_health("stream-2", "ready", "ok", actor="test")
        with pytest.raises(ValueError, match="仍包含"):
            store.delete_area("area-1", actor="operator")

        assert store.delete_camera("camera-1", actor="operator") is None
        topology = store.load_topology()
        assert [camera.id for camera in topology.cameras] == ["camera-2"]
        assert [stream.id for stream in topology.streams] == ["stream-2"]
        assert [item.id for item in topology.devices] == ["device-shared"]
        health_row = (
            store._connection_or_raise()
            .execute("SELECT 1 FROM stream_health WHERE stream_id = ?", ("stream-1",))
            .fetchone()
        )
        assert health_row is None

        assert store.delete_camera("camera-2", actor="operator") == "credential-shared"
        topology = store.load_topology()
        assert topology.cameras == ()
        assert topology.streams == ()
        assert topology.devices == ()
        assert store.delete_area("area-1", actor="operator") is True
        actions = {
            str(row["action"])
            for row in store._connection_or_raise().execute(
                "SELECT action FROM audit_log ORDER BY id"
            )
        }
        assert "topology.camera.deleted" in actions
        assert "topology.area.deleted" in actions
    finally:
        store.close()


def test_incident_notification_delivery_history_tracks_failures_and_retries(tmp_path):
    store = SQLiteStore(tmp_path / "notification-history.db")
    try:
        incident = Incident.create(
            title="通知测试险情",
            severity=IncidentSeverity.HIGH,
            reporter="test",
        )
        assert store.create_incident(incident, actor="test")
        store.record_incident_notification(
            incident.id, "webhook", success=False, detail="connection refused"
        )
        assert store.failed_incident_notification_channels(incident.id) == ("webhook",)
        assert store.incident_notification_retry_candidates(max_attempts=3, min_age_seconds=0) == (
            (incident.id, ("webhook",)),
        )
        store.record_incident_notification(incident.id, "webhook", success=True, detail="HTTP 202")
        history = store.list_incident_notifications(incident.id)
        assert history == [
            {
                "channel": "webhook",
                "status": "sent",
                "attempts": 2,
                "detail": "HTTP 202",
                "updated_at": history[0]["updated_at"],
            }
        ]
        assert store.failed_incident_notification_channels(incident.id) == ()
    finally:
        store.close()


def test_configuration_package_round_trips_topology_rules_and_schedules(tmp_path):
    source = SQLiteStore(tmp_path / "source.db")
    topology = EntityRegistry(
        sites=(Site("site-1", "Main"),),
        areas=(Area("area-1", "site-1", "Yard"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
        streams=(
            VideoStream("stream-1", "camera-1", "Main", "rtsp://camera/main", StreamRole.MAIN),
        ),
    )
    source.save_topology(topology)
    source.save_rule(
        Rule(
            "rule-1",
            stream_id="stream-1",
            name="设备告警路由",
            native_event_codes=("motiondetect", "fielddetection"),
        )
    )
    source.set_stream_armed("stream-1", False, actor="test")
    source.save_arming_schedule(
        ArmingSchedule(
            "stream-1",
            weekly=((TimeWindow(8 * 60, 18 * 60),), (), (), (), (), (), ()),
            default_armed=False,
            timezone="Asia/Shanghai",
        ),
        actor="test",
    )
    package = source.export_configuration()
    source.close()

    restored = SQLiteStore(tmp_path / "restored.db")
    restored.import_configuration(package, actor="test", replace=True)
    assert restored.load_topology().to_dict() == topology.to_dict()
    assert restored.load_rules()[0].native_event_codes == ("motiondetect", "fielddetection")
    assert restored.list_stream_arming() == {"stream-1": False}
    assert restored.get_arming_schedule("stream-1").timezone == "Asia/Shanghai"
    audit = (
        restored._connection_or_raise()
        .execute("SELECT action, actor FROM audit_log WHERE action = 'configuration.imported'")
        .fetchone()
    )
    assert tuple(audit) == ("configuration.imported", "test")
    restored.close()


def test_configuration_export_redacts_stream_credentials(tmp_path):
    store = SQLiteStore(tmp_path / "credentials.db")
    store.save_topology(
        EntityRegistry(
            sites=(Site("site-1", "Main"),),
            areas=(Area("area-1", "site-1", "Yard"),),
            cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
            streams=(
                VideoStream(
                    "stream-1",
                    "camera-1",
                    "Main",
                    "rtsp://admin:secret@example.test/live",
                    StreamRole.MAIN,
                ),
            ),
        )
    )
    package = store.export_configuration()
    store.close()
    exported_url = package["topology"]["streams"][0]["url"]
    assert exported_url == "rtsp://***:***@example.test/live"
    assert "secret" not in json.dumps(package)


def test_stream_health_persists_transitions_and_redacts_credentials(tmp_path):
    store = SQLiteStore(tmp_path / "health.db")
    store.set_stream_health("stream-1", "ready", "stream ready", actor="worker")
    store.set_stream_health(
        "stream-1",
        "degraded",
        "rtsp://admin:secret@example.test/live timeout",
        actor="worker",
    )
    store.set_stream_health("stream-1", "offline", "connection refused", actor="worker")
    snapshot = store.list_stream_health()["stream-1"]
    assert snapshot["status"] == "offline"
    assert snapshot["reconnect_count"] == 2
    assert "secret" not in str(snapshot["detail"])
    assert snapshot["last_ok_at"] is not None
    actions = (
        store._connection_or_raise()
        .execute("SELECT action FROM audit_log WHERE resource = ? ORDER BY id", ("stream-1",))
        .fetchall()
    )
    assert [row[0] for row in actions] == ["stream.health.changed"] * 3
    online_at = datetime.now(UTC)
    store.set_stream_health("stream-2", "ONLINE", "connected", updated_at=online_at, actor="worker")
    online = store.list_stream_health()["stream-2"]
    assert online["status"] == "online"
    assert online["last_ok_at"] == online_at.isoformat()
    store.close()


def test_operational_incident_query_keeps_old_unresolved_records(tmp_path):
    store = SQLiteStore(tmp_path / "operational-incidents.db")
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    unresolved = Incident.create(
        title="较早但仍未处理的险情",
        severity=IncidentSeverity.HIGH,
        occurred_at=now - timedelta(days=30),
    )
    assert store.create_incident(unresolved)
    for index in range(101):
        resolved = Incident.create(
            title=f"已解决险情 {index}",
            severity=IncidentSeverity.LOW,
            occurred_at=now - timedelta(minutes=index),
        ).with_status(IncidentStatus.RESOLVED)
        assert store.create_incident(resolved)

    recent = store.list_incidents(limit=100)
    assert unresolved.id not in {incident.id for incident in recent}
    operational = store.list_operational_incidents(recent_limit=100)
    assert unresolved.id in {incident.id for incident in operational}
    context = build_operational_context(store, now=now)
    assert unresolved.id in {incident.id for incident in context.open_incidents}
    store.close()


def test_event_status_transition_updates_payload_and_audit(tmp_path):
    store = SQLiteStore(tmp_path / "status.db")
    rule = Rule("r-status", "CH01", "status", "polygon", ((0, 0), (100, 0), (100, 100)))
    event = _event(rule=rule)
    assert store.create_event_with_outbox(event)
    assert store.update_event_status(
        event.id, EventStatus.CONFIRMED, actor="operator", detail="verified"
    )
    row = store.get_event(event.id)
    assert row is not None
    assert row["status"] == "confirmed"
    assert json.loads(row["payload"])["status"] == "confirmed"
    audit = (
        store._connection_or_raise()
        .execute(
            "SELECT action, actor, detail FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1",
            (event.id,),
        )
        .fetchone()
    )
    assert tuple(audit) == ("event.status.confirmed", "operator", "verified")
    store.close()


def test_native_event_rules_persist_and_can_be_scoped_to_stream(tmp_path):
    store = SQLiteStore(tmp_path / "rules.db")
    rule = Rule(
        "rule-persisted",
        stream_id="stream-1",
        site_id="site-1",
        area_id="area-1",
        camera_id="camera-1",
        name="仓库设备告警",
        native_event_codes=("motiondetect", "tamperdetection"),
    )
    store.save_rule(rule)
    loaded = store.load_rules(stream_id="stream-1", enabled_only=True)
    assert len(loaded) == 1
    assert loaded[0].id == rule.id
    assert loaded[0].native_event_codes == ("motiondetect", "tamperdetection")
    assert store.load_rules(stream_id="other") == []
    audit = (
        store._connection_or_raise()
        .execute(
            "SELECT action, actor FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1",
            (rule.id,),
        )
        .fetchone()
    )
    assert tuple(audit) == ("rule.updated", "system")
    assert store.set_rule_enabled(rule.id, False, actor="operator")
    assert store.load_rules(stream_id="stream-1", enabled_only=True) == []
    assert store.load_rules(stream_id="stream-1")[0].enabled is False
    store.close()


def test_stream_arming_state_persists_and_is_audited(tmp_path):
    store = SQLiteStore(tmp_path / "arming.db")
    assert store.get_stream_armed("stream-1") is True
    store.set_stream_armed("stream-1", False, actor="operator")
    assert store.get_stream_armed("stream-1") is False
    assert store.list_stream_arming() == {"stream-1": False}
    audit = (
        store._connection_or_raise()
        .execute(
            "SELECT action, detail FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1",
            ("stream-1",),
        )
        .fetchone()
    )
    assert tuple(audit) == ("stream.arming.changed", "armed=0")
    store.close()


def test_rule_assignees_round_trip_and_event_target(tmp_path):
    store = SQLiteStore(tmp_path / "assignees.db")
    rule = Rule(
        "assignee-rule",
        stream_id="stream-1",
        name="值班定向",
        type="polygon",
        geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
        assignees=("张三", "李四"),
    )
    store.save_rule(rule)
    loaded = store.load_rules(stream_id="stream-1")[0]
    assert loaded.assignees == ("张三", "李四")
    event = _event(rule=rule)
    assert event.assignees == ("张三", "李四")
    assert event.target["assignees"] == ["张三", "李四"]
    store.close()


def test_builtin_rule_templates_are_importable(tmp_path):
    from factoryguard.rule_templates import BUILTIN_RULE_TEMPLATES

    store = SQLiteStore(tmp_path / "builtin-templates.db")
    assert len(BUILTIN_RULE_TEMPLATES) >= 5
    template = BUILTIN_RULE_TEMPLATES[2]
    imported = store.import_rule_template(
        template,
        actor="operator",
        stream_id="stream-1",
    )
    assert imported.native_event_codes == ("tamperdetection", "tamper")
    assert imported.publication_status == "draft"
    store.close()


def test_rule_template_export_and_import_are_portable(tmp_path):
    store = SQLiteStore(tmp_path / "rule-templates.db")
    source = Rule(
        "template-source",
        stream_id="stream-1",
        site_id="site-1",
        name="原生告警模板",
        native_event_codes=("motiondetect", "motion"),
        dedup_window_seconds=8.0,
    )
    store.save_rule(source)
    template = store.export_rule_template("template-source")
    assert "stream_id" not in template["rule"]
    imported = store.import_rule_template(
        template,
        actor="operator",
        stream_id="stream-2",
        new_id="imported-rule",
    )
    assert imported.id == "imported-rule"
    assert imported.stream_id == "stream-2"
    assert imported.publication_status == "draft"
    assert imported.native_event_codes == source.native_event_codes
    assert imported.dedup_window_seconds == 8.0
    store.close()


def test_legacy_visual_rule_fields_are_ignored(tmp_path):
    store = SQLiteStore(tmp_path / "legacy-visual-fields.db")
    rule = Rule(
        "legacy-rule",
        stream_id="stream-1",
        name="旧配置兼容",
        native_event_codes=("motiondetect",),
        type="polygon",
        geometry=((0, 0), (100, 0), (100, 100)),
        condition_nodes=({"condition": "intrusion"},),
        min_confidence=0.9,
    )
    store.save_rule(rule)
    loaded = store.load_rules(stream_id="stream-1")[0]
    assert loaded.native_event_codes == ("motiondetect",)
    assert not hasattr(loaded, "geometry")
    assert not hasattr(loaded, "condition_nodes")
    assert not hasattr(loaded, "min_confidence")
    store.close()


def test_native_event_action_fields_round_trip(tmp_path):
    store = SQLiteStore(tmp_path / "native-actions.db")
    rule = Rule(
        "native-action-rule",
        stream_id="stream-1",
        name="设备告警动作",
        native_event_codes=("alarm",),
        actions=("event", "notify", "audit", "http"),
        http_action_url="https://actions.example.test/event",
        http_action_timeout=7,
    )
    store.save_rule(rule)
    loaded = store.load_rules(stream_id="stream-1")[0]
    assert loaded.native_event_codes == ("alarm",)
    assert loaded.actions == ("event", "notify", "audit", "http")
    assert loaded.http_action_url == "https://actions.example.test/event"
    assert loaded.http_action_timeout == 7
    store.close()


def test_rule_versions_are_saved_and_restored_as_new_version(tmp_path):
    store = SQLiteStore(tmp_path / "rule-versions.db")
    version_one = Rule(
        "versioned-rule",
        stream_id="stream-1",
        name="版本测试",
        native_event_codes=("motiondetect",),
        version=1,
    )
    store.save_rule(version_one, note="initial")
    version_two = Rule(
        "versioned-rule",
        stream_id="stream-1",
        name="版本测试 v2",
        native_event_codes=("fielddetection",),
        dedup_window_seconds=10,
        version=2,
    )
    store.save_rule(version_two, note="adjust event mapping")
    assert [row["version"] for row in store.list_rule_versions("versioned-rule")] == [2, 1]
    restored = store.restore_rule_version("versioned-rule", 1, actor="operator")
    assert restored.version == 3
    assert restored.native_event_codes == version_one.native_event_codes
    assert restored.name == version_one.name
    assert store.load_rules(stream_id="stream-1")[0].version == 3
    store.close()


def test_rule_dual_approval_requires_two_distinct_reviewers(tmp_path):
    store = SQLiteStore(tmp_path / "dual-approval.db")
    draft = Rule(
        "review-rule",
        stream_id="stream-1",
        name="需要双人审核",
        type="polygon",
        geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
        publication_status="draft",
        version=1,
    )
    store.save_rule(draft)
    pending = store.submit_rule_for_review("review-rule", actor="alice")
    assert pending.publication_status == "pending_review"
    assert pending.version == 2
    assert store.load_rules(stream_id="stream-1", published_only=True) == []

    first = store.approve_rule("review-rule", actor="alice")
    assert first.publication_status == "pending_review"
    assert store.rule_approval_actors("review-rule", first.version) == ("alice",)
    store.approve_rule("review-rule", actor="alice")
    assert store.rule_approval_actors("review-rule", first.version) == ("alice",)

    published = store.approve_rule("review-rule", actor="bob")
    assert published.publication_status == "published"
    assert published.version == 3
    assert [rule.id for rule in store.load_rules(stream_id="stream-1", published_only=True)] == [
        "review-rule"
    ]
    store.close()


def test_rule_rejection_returns_to_draft_and_is_versioned(tmp_path):
    store = SQLiteStore(tmp_path / "reject-review.db")
    draft = Rule(
        "reject-rule",
        stream_id="stream-1",
        name="驳回规则",
        type="line",
        geometry=((0, 100), (500, 100)),
        publication_status="draft",
        version=1,
    )
    store.save_rule(draft)
    store.submit_rule_for_review("reject-rule", actor="alice")
    rejected = store.reject_rule("reject-rule", actor="bob", note="参数不合理")
    assert rejected.publication_status == "draft"
    assert rejected.version == 3
    reviews = store.list_rule_reviews("reject-rule")
    assert reviews[-1]["decision"] == "reject"
    store.close()


def test_draft_rules_are_hidden_from_production_runtime(tmp_path):
    store = SQLiteStore(tmp_path / "draft-rules.db")
    draft = Rule(
        "draft-rule",
        stream_id="stream-1",
        name="draft",
        type="polygon",
        geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
        publication_status="draft",
    )
    published = Rule(
        "published-rule",
        stream_id="stream-1",
        name="published",
        type="polygon",
        geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
        publication_status="published",
    )
    store.save_rule(draft)
    store.save_rule(published)
    assert {rule.id for rule in store.load_rules(stream_id="stream-1")} == {
        "draft-rule",
        "published-rule",
    }
    assert [rule.id for rule in store.load_rules(stream_id="stream-1", published_only=True)] == [
        "published-rule"
    ]
    store.close()


def test_overdue_open_event_escalates_once_and_requeues_notifications(tmp_path):
    store = SQLiteStore(tmp_path / "escalation.db")
    rule = Rule(
        "escalation-rule",
        "CH01",
        "escalate",
        "polygon",
        ((0, 0), (100, 0), (100, 100), (0, 100)),
        confirm_frames=1,
        ack_deadline_seconds=10,
        escalation_level="critical",
        actions=("event", "notify"),
    )
    store.save_rule(rule)
    occurred = datetime(2026, 1, 1, tzinfo=UTC)
    event = _event(rule=rule)
    assert store.create_event_with_outbox(event, channels=("webhook",))
    assert (
        store.escalate_overdue_events(
            channels=("webhook",),
            now=occurred + timedelta(seconds=20),
        )
        == 1
    )
    assert (
        store.escalate_overdue_events(
            channels=("webhook",),
            now=occurred + timedelta(seconds=30),
        )
        == 0
    )
    row = (
        store._connection_or_raise()
        .execute("SELECT level, escalated_at FROM event WHERE id = ?", (event.id,))
        .fetchone()
    )
    assert row["level"] == "critical"
    assert row["escalated_at"] is not None
    assert store.outbox_counts()["pending"] == 2
    store.close()


def test_outbox_claim_lease_retry_and_sent_state():
    store = SQLiteStore()
    rule = Rule("r1", "CH01", "fence", "polygon", ((0, 0), (10, 0), (10, 10), (0, 10)))
    occurred = datetime(2026, 1, 1, tzinfo=UTC)
    event = _event(rule=rule)
    assert store.create_event_with_outbox(event)

    claimed = store.claim_pending_outbox(now=occurred, lease_seconds=10)
    assert len(claimed) == 1
    outbox_id = claimed[0]["id"]
    assert claimed[0]["status"] == "sending"
    assert claimed[0]["attempts"] == 1
    assert store.claim_pending_outbox(now=occurred + timedelta(seconds=1)) == []

    assert store.recover_expired_outbox(now=occurred + timedelta(seconds=11)) == 1
    retry_claim = store.claim_pending_outbox(now=occurred + timedelta(seconds=11), lease_seconds=10)
    assert len(retry_claim) == 1
    assert retry_claim[0]["attempts"] == 2
    assert store.mark_outbox_failed(
        outbox_id,
        "temporary network failure",
        retry_at=occurred + timedelta(seconds=20),
    )
    assert store.claim_pending_outbox(now=occurred + timedelta(seconds=15)) == []
    retry_claim = store.claim_pending_outbox(now=occurred + timedelta(seconds=20), lease_seconds=10)
    assert len(retry_claim) == 1
    assert retry_claim[0]["attempts"] == 3
    assert store.mark_outbox_sent(outbox_id, sent_at=occurred + timedelta(seconds=21))
    assert store.list_pending_outbox() == []
    store.close()


def test_outbox_operations_console_lists_counts_and_requeues_failed_rows():
    store = SQLiteStore()
    rule = Rule("r-console", "CH01", "fence", "polygon", ((0, 0), (10, 0), (10, 10)))
    event = _event(rule=rule)
    assert store.create_event_with_outbox(event)
    outbox_id = str(store.list_pending_outbox()[0]["id"])
    claimed = store.claim_pending_outbox(now=datetime(2026, 1, 1, tzinfo=UTC))
    assert len(claimed) == 1
    assert store.mark_outbox_failed(outbox_id, "webhook unavailable")

    rows = store.list_outbox(status="failed")
    assert len(rows) == 1
    assert rows[0]["event_id"] == event.id
    assert store.outbox_counts()["failed"] == 1
    assert store.requeue_outbox(outbox_id, actor="operator")
    assert store.outbox_counts()["retry"] == 1
    audit = (
        store._connection_or_raise()
        .execute(
            "SELECT action, actor FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1",
            (outbox_id,),
        )
        .fetchone()
    )
    assert tuple(audit) == ("notification.requeue", "operator")
    store.close()


def test_overdue_escalation_uses_rule_destination_ids(tmp_path):
    store = SQLiteStore(tmp_path / "escalation-destination.db")
    rule = Rule(
        "escalation-destination-rule",
        "CH01",
        "escalate destination",
        "polygon",
        ((0, 0), (100, 0), (100, 100), (0, 100)),
        confirm_frames=1,
        ack_deadline_seconds=10,
        escalation_level="high",
        actions=("event", "notify"),
        destination_ids=("gate-a",),
    )
    store.save_rule(rule)
    occurred = datetime(2026, 1, 1, tzinfo=UTC)
    event = _event(rule=rule)
    assert store.create_event_with_outbox(event, channels=("webhook",))
    assert (
        store.escalate_overdue_events(
            channels=("webhook", "email"),
            destination_channels={"gate-a": "webhook:gate-a", "gate-b": "webhook:gate-b"},
            now=occurred + timedelta(seconds=20),
        )
        == 1
    )
    rows = store.list_pending_outbox()
    assert {row["channel"] for row in rows} == {"webhook", "webhook:gate-a"}
    store.close()


def test_set_entity_enabled_supports_physical_device(tmp_path):
    store = SQLiteStore(tmp_path / "entity-enabled.db")
    try:
        device = VideoDevice(
            "device-1",
            "site-1",
            "area-1",
            "摄像头",
            DeviceProtocol.ONVIF,
            "http://10.0.0.8/onvif",
        )
        store.save_topology(
            EntityRegistry(
                sites=(Site("site-1", "测试园区"),),
                areas=(Area("area-1", "site-1", "东门"),),
                devices=(device,),
            )
        )

        assert store.set_entity_enabled("device", device.id, False, actor="operator") is True
        loaded = store.load_topology().get_device(device.id)
        assert loaded.enabled is False
        assert store.set_entity_enabled("device", device.id, True, actor="operator") is True
        assert store.load_topology().get_device(device.id).enabled is True
    finally:
        store.close()


def test_set_camera_enabled_handles_shared_device_and_context(tmp_path):
    store = SQLiteStore(tmp_path / "camera-enabled.db")
    try:
        device = VideoDevice(
            "device-1",
            "site-1",
            "area-1",
            "双通道设备",
            DeviceProtocol.RTSP,
            "rtsp://10.0.0.8/live",
        )
        cameras = (
            CameraDevice("camera-1", "area-1", "东门", device_id=device.id, channel_id="ch1"),
            CameraDevice("camera-2", "area-1", "仓库", device_id=device.id, channel_id="ch2"),
        )
        streams = tuple(
            VideoStream(
                f"stream-{index}",
                f"camera-{index}",
                "子码流",
                f"rtsp://10.0.0.8/ch{index}",
                role=StreamRole.SUB,
            )
            for index in (1, 2)
        )
        store.save_topology(
            EntityRegistry(
                sites=(Site("site-1", "测试园区"),),
                areas=(Area("area-1", "site-1", "重点区域"),),
                devices=(device,),
                cameras=cameras,
                streams=streams,
            )
        )

        assert store.set_camera_enabled("camera-1", False, actor="operator")
        assert store.load_topology().get_camera("camera-1").enabled is False
        assert store.load_topology().get_device(device.id).enabled is True

        assert store.set_camera_enabled("camera-2", False, actor="operator")
        assert store.load_topology().get_device(device.id).enabled is False

        assert store.set_camera_enabled("camera-1", True, actor="operator")
        assert store.load_topology().get_device(device.id).enabled is True
        context = build_operational_context(store)
        views = {view.camera.id: view for view in context.cameras}
        assert not views["camera-1"].is_disabled
        assert views["camera-2"].is_disabled
        assert views["camera-2"].status_label == "已停用"
        assert views["camera-2"].best_stream is None
        audit_count = (
            store._connection_or_raise()
            .execute("SELECT COUNT(*) FROM audit_log WHERE action = 'topology.camera.enabled'")
            .fetchone()[0]
        )
        assert audit_count == 3
    finally:
        store.close()
