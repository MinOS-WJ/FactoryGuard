from datetime import UTC, datetime, timedelta

from factoryguard.app.pipeline import DetectionPipeline
from factoryguard.domain.models import Detection, Frame, Rule, RuleType
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.storage.sqlite import SQLiteStore


def test_pipeline_persists_confirmed_event_and_outbox():
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (100, 0), (100, 100), (0, 100)))
    store = SQLiteStore()
    pipeline = DetectionPipeline(RuleEvaluator([rule]), store)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for frame_no in range(3):
        result = pipeline.process(
            Frame("CH01", frame_no, start + timedelta(seconds=frame_no), 100, 100),
            [Detection("person", 0.9, (20, 10, 40, 80))],
        )
    assert result.events
    assert len(store.list_events()) == 1
    assert len(store.list_pending_outbox()) == 1
    assert store.integrity_check() == "ok"
    store.close()


def test_pipeline_keeps_identity_when_detection_order_changes():
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (200, 0), (200, 200), (0, 200)))
    store = SQLiteStore()
    pipeline = DetectionPipeline(RuleEvaluator([rule]), store)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    first = [
        Detection("person", 0.9, (10, 10, 50, 100)),
        Detection("person", 0.9, (100, 10, 140, 100)),
    ]
    second = list(reversed(first))
    for frame_no, detections in enumerate((first, second, first)):
        result = pipeline.process(
            Frame("CH01", frame_no, start + timedelta(seconds=frame_no), 200, 200),
            detections,
        )
    assert len(result.events) == 2
    assert len(store.list_events()) == 2
    dedup_keys = {row["dedup_key"] for row in store.list_events()}
    assert all(key.startswith(("CH01:r1:auto:CH01:person:0:", "CH01:r1:auto:CH01:person:1:")) for key in dedup_keys)
    store.close()


def test_pipeline_persists_same_track_again_after_cooldown():
    rule = Rule(
        "r1",
        "CH01",
        "fence",
        RuleType.POLYGON,
        ((0, 0), (100, 0), (100, 100), (0, 100)),
        confirm_frames=2,
        cooldown_seconds=1,
    )
    store = SQLiteStore()
    pipeline = DetectionPipeline(RuleEvaluator([rule]), store)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    detection = Detection("person", 0.9, (20, 10, 40, 80), "track-1")
    for frame_no in range(4):
        pipeline.process(
            Frame("CH01", frame_no, start + timedelta(seconds=frame_no), 100, 100),
            [detection],
        )
    assert len(store.list_events()) == 2
    assert len(store.list_pending_outbox()) == 2
    store.close()


def test_pipeline_fans_one_event_out_to_email_and_webhook():
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (100, 0), (100, 100), (0, 100)))
    store = SQLiteStore()
    pipeline = DetectionPipeline(
        RuleEvaluator([rule]),
        store,
        outbox_channels=("webhook", "email"),
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for frame_no in range(3):
        result = pipeline.process(
            Frame("CH01", frame_no, start + timedelta(seconds=frame_no), 100, 100),
            [Detection("person", 0.9, (20, 10, 40, 80), "track-email")],
        )

    assert len(result.events) == 1
    assert len(store.list_events()) == 1
    rows = store.list_pending_outbox()
    assert len(rows) == 2
    assert {row["channel"] for row in rows} == {"webhook", "email"}
    assert {row["event_id"] for row in rows} == {result.events[0].id}
    store.close()


def test_same_rule_alerts_for_multiple_targets_are_independent():
    rule = Rule(
        "r-simultaneous",
        "CH01",
        "fence",
        RuleType.POLYGON,
        ((0, 0), (200, 0), (200, 200), (0, 200)),
        confirm_frames=1,
        cooldown_seconds=60,
    )
    store = SQLiteStore()
    pipeline = DetectionPipeline(
        RuleEvaluator([rule]),
        store,
        outbox_channels=("webhook", "webhook:backup"),
    )

    result = pipeline.process(
        Frame("CH01", 1, datetime(2026, 1, 1, tzinfo=UTC), 200, 200),
        [
            Detection("person", 0.9, (10, 10, 50, 100), "person-a"),
            Detection("person", 0.8, (100, 10, 150, 100), "person-b"),
        ],
    )

    assert len(result.events) == 2
    assert len(store.list_events()) == 2
    assert len({event.dedup_key for event in result.events}) == 2
    rows = store.list_pending_outbox()
    assert len(rows) == 4
    assert {row["channel"] for row in rows} == {"webhook", "webhook:backup"}
    assert {row["event_id"] for row in rows} == {event.id for event in result.events}
    store.close()
