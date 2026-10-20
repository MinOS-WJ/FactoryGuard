import json
from datetime import UTC, datetime, timedelta

from factoryguard.domain.models import Detection, Event, EventStatus, Frame, Rule, RuleType
from factoryguard.domain.models import Area, CameraDevice, Site, StreamRole, VideoStream
from factoryguard.domain.schedules import ArmingSchedule, TimeWindow
from factoryguard.domain.topology import EntityRegistry
from factoryguard.simulation.runner import run_demo
from factoryguard.storage.sqlite import SQLiteStore


def test_event_outbox_transaction_is_idempotent(tmp_path):
    report = run_demo(tmp_path / "demo.db", frames=6)
    assert report.events == 1
    assert report.pending_notifications == 1
    assert report.integrity == "ok"

    store = SQLiteStore(tmp_path / "idempotency.db")
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (10, 0), (10, 10), (0, 10)))
    frame = Frame("CH01", 1, datetime(2026, 1, 1, tzinfo=UTC), 20, 20)
    event = Event.create(rule=rule, frame=frame, detection=Detection("person", 0.9, (1, 1, 4, 8), "t1"))
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


def test_configuration_package_round_trips_topology_rules_and_schedules(tmp_path):
    source = SQLiteStore(tmp_path / "source.db")
    topology = EntityRegistry(
        sites=(Site("site-1", "Main"),),
        areas=(Area("area-1", "site-1", "Yard"),),
        cameras=(CameraDevice("camera-1", "area-1", "Gate"),),
        streams=(VideoStream("stream-1", "camera-1", "Main", "rtsp://camera/main", StreamRole.MAIN),),
    )
    source.save_topology(topology)
    source.save_rule(
        Rule(
            "rule-1",
            stream_id="stream-1",
            name="Gate polygon",
            type=RuleType.POLYGON,
            geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
            target_labels=frozenset({"person", "forklift"}),
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
    assert restored.load_rules()[0].target_labels == frozenset({"person", "forklift"})
    assert restored.list_stream_arming() == {"stream-1": False}
    assert restored.get_arming_schedule("stream-1").timezone == "Asia/Shanghai"
    audit = restored._connection_or_raise().execute(
        "SELECT action, actor FROM audit_log WHERE action = 'configuration.imported'"
    ).fetchone()
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
    store.set_stream_health("stream-1", "ready", "model ready", actor="worker")
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
    actions = store._connection_or_raise().execute(
        "SELECT action FROM audit_log WHERE resource = ? ORDER BY id", ("stream-1",)
    ).fetchall()
    assert [row[0] for row in actions] == ["stream.health.changed"] * 3
    store.close()


def test_event_status_transition_updates_payload_and_audit(tmp_path):
    store = SQLiteStore(tmp_path / "status.db")
    rule = Rule("r-status", "CH01", "status", RuleType.POLYGON, ((0, 0), (100, 0), (100, 100)))
    event = Event.create(
        rule=rule,
        frame=Frame("CH01", 1, datetime.now(UTC), 100, 100),
        detection=Detection("person", 0.9, (1, 1, 20, 40)),
    )
    assert store.create_event_with_outbox(event)
    assert store.update_event_status(event.id, EventStatus.CONFIRMED, actor="operator", detail="verified")
    row = store.get_event(event.id)
    assert row is not None
    assert row["status"] == "confirmed"
    assert json.loads(row["payload"])["status"] == "confirmed"
    audit = store._connection_or_raise().execute(
        "SELECT action, actor, detail FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1", (event.id,)
    ).fetchone()
    assert tuple(audit) == ("event.status.confirmed", "operator", "verified")
    store.close()


def test_detection_rules_persist_and_can_be_scoped_to_stream(tmp_path):
    store = SQLiteStore(tmp_path / "rules.db")
    rule = Rule(
        "rule-persisted",
        stream_id="stream-1",
        site_id="site-1",
        area_id="area-1",
        camera_id="camera-1",
        name="仓库围栏",
        type=RuleType.POLYGON,
        geometry=((0, 0), (100, 0), (100, 100), (0, 100)),
        target_labels=frozenset({"person", "forklift"}),
        confirm_frames=4,
        cooldown_seconds=12,
    )
    store.save_rule(rule)
    loaded = store.load_rules(stream_id="stream-1", enabled_only=True)
    assert len(loaded) == 1
    assert loaded[0].id == rule.id
    assert loaded[0].target_labels == rule.target_labels
    assert loaded[0].confirm_frames == 4
    assert store.load_rules(stream_id="other") == []
    audit = store._connection_or_raise().execute(
        "SELECT action, actor FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1",
        (rule.id,),
    ).fetchone()
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
    audit = store._connection_or_raise().execute(
        "SELECT action, detail FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1", ("stream-1",)
    ).fetchone()
    assert tuple(audit) == ("stream.arming.changed", "armed=0")
    store.close()


def test_outbox_claim_lease_retry_and_sent_state():
    store = SQLiteStore()
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (10, 0), (10, 10), (0, 10)))
    occurred = datetime(2026, 1, 1, tzinfo=UTC)
    event = Event.create(
        rule=rule,
        frame=Frame("CH01", 1, occurred, 20, 20),
        detection=Detection("person", 0.9, (1, 1, 4, 8), "t1"),
    )
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
    rule = Rule("r-console", "CH01", "fence", RuleType.POLYGON, ((0, 0), (10, 0), (10, 10)))
    event = Event.create(
        rule=rule,
        frame=Frame("CH01", 1, datetime(2026, 1, 1, tzinfo=UTC), 20, 20),
        detection=Detection("person", 0.9, (1, 1, 4, 8), "console-track"),
    )
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
    audit = store._connection_or_raise().execute(
        "SELECT action, actor FROM audit_log WHERE resource = ? ORDER BY id DESC LIMIT 1", (outbox_id,)
    ).fetchone()
    assert tuple(audit) == ("notification.requeue", "operator")
    store.close()
