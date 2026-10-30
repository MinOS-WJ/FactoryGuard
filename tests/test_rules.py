from datetime import UTC, datetime, timedelta

from factoryguard.domain.models import Detection, Frame, Rule, RuleType
from factoryguard.domain.rules import RuleEvaluator


def _frame(no: int, *, inside: bool = True) -> Frame:
    x = 100 if inside else 900
    return Frame(
        stream_id="CH01",
        frame_no=no,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=no),
        width=1280,
        height=720,
        detections=(Detection("person", 0.9, (x, 100, x + 60, 260), "t1"),),
    )


def test_three_frame_confirmation_and_cooldown():
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (500, 0), (500, 500), (0, 500)))
    evaluator = RuleEvaluator([rule])
    assert evaluator.evaluate(_frame(0)) == []
    assert evaluator.evaluate(_frame(1)) == []
    events = evaluator.evaluate(_frame(2))
    assert len(events) == 1
    assert evaluator.evaluate(_frame(3)) == []
    assert evaluator.evaluate(_frame(4, inside=False)) == []


def test_disarmed_state_clears_candidates():
    rule = Rule("r1", "CH01", "fence", RuleType.POLYGON, ((0, 0), (500, 0), (500, 500), (0, 500)), confirm_frames=2)
    evaluator = RuleEvaluator([rule])
    evaluator.evaluate(_frame(0))
    assert evaluator.evaluate(_frame(1), armed=False) == []
    assert evaluator.evaluate(_frame(2)) == []


def test_duplicate_track_rows_count_as_one_frame():
    rule = Rule(
        "r1",
        "CH01",
        "fence",
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        confirm_frames=2,
    )
    evaluator = RuleEvaluator([rule])
    duplicate = Detection("person", 0.95, (100, 100, 160, 260), "t1")
    frame = Frame(
        stream_id="CH01",
        frame_no=0,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        width=1280,
        height=720,
        detections=(duplicate, duplicate),
    )
    assert evaluator.evaluate(frame) == []
    assert len(evaluator.evaluate(_frame(1))) == 1


def _line_frame(no: int, detections: tuple[Detection, ...]) -> Frame:
    return Frame(
        stream_id="CH01",
        frame_no=no,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=no),
        width=1280,
        height=720,
        detections=detections,
    )


def test_line_count_emits_only_when_window_threshold_is_reached():
    rule = Rule(
        "line-count",
        "CH01",
        "gate count",
        RuleType.LINE,
        ((0, 100), (500, 100)),
        condition="line_count",
        direction="down",
        count_threshold=2,
        window_seconds=10,
        confirm_frames=1,
        cooldown_seconds=30,
    )
    evaluator = RuleEvaluator([rule])
    assert evaluator.evaluate(_line_frame(0, (Detection("person", 0.9, (10, 20, 30, 90), "a"),))) == []
    assert evaluator.evaluate(_line_frame(1, (Detection("person", 0.9, (10, 110, 30, 200), "a"),))) == []
    assert evaluator.evaluate(_line_frame(2, (Detection("person", 0.9, (100, 20, 120, 90), "b"),))) == []
    events = evaluator.evaluate(
        _line_frame(3, (Detection("person", 0.9, (100, 110, 120, 200), "b"),))
    )
    assert len(events) == 1
    assert events[0].target["condition"] == "line_count"
    assert events[0].target["count"] == 2
    assert events[0].target["threshold"] == 2


def test_line_count_does_not_double_count_one_track_jitter():
    rule = Rule(
        "line-count-jitter",
        "CH01",
        "gate count",
        RuleType.LINE,
        ((0, 100), (500, 100)),
        condition="line_count",
        direction="down",
        count_threshold=2,
        window_seconds=10,
        confirm_frames=1,
        cooldown_seconds=30,
    )
    evaluator = RuleEvaluator([rule])
    assert evaluator.evaluate(_line_frame(0, (Detection("person", 0.9, (10, 20, 30, 90), "a"),))) == []
    assert evaluator.evaluate(_line_frame(1, (Detection("person", 0.9, (10, 110, 30, 200), "a"),))) == []
    assert evaluator.evaluate(_line_frame(2, (Detection("person", 0.9, (10, 20, 30, 90), "a"),))) == []
    assert evaluator.evaluate(_line_frame(3, (Detection("person", 0.9, (10, 110, 30, 200), "a"),))) == []
    assert evaluator.evaluate(_line_frame(4, (Detection("person", 0.9, (100, 20, 120, 90), "b"),))) == []
    events = evaluator.evaluate(
        _line_frame(5, (Detection("person", 0.9, (100, 110, 120, 200), "b"),))
    )
    assert len(events) == 1
    assert events[0].target["count"] == 2


def test_occupancy_condition_emits_aggregate_count_event():
    rule = Rule(
        "occupancy",
        "CH01",
        "yard occupancy",
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        condition="occupancy",
        count_threshold=2,
        confirm_frames=1,
        cooldown_seconds=30,
    )
    evaluator = RuleEvaluator([rule])
    detections = (
        Detection("person", 0.9, (10, 10, 50, 100), "a"),
        Detection("person", 0.8, (100, 10, 150, 100), "b"),
    )
    events = evaluator.evaluate(_line_frame(0, detections))
    assert len(events) == 1
    assert events[0].target["condition"] == "occupancy"
    assert events[0].target["count"] == 2
    assert evaluator.evaluate(_line_frame(1, detections)) == []


def test_occupancy_condition_emits_recovery_when_count_drops():
    rule = Rule(
        "occupancy-recovery",
        "CH01",
        "yard recovery",
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        condition="occupancy",
        count_threshold=2,
        confirm_frames=1,
        cooldown_seconds=0,
    )
    evaluator = RuleEvaluator([rule])
    detections = (
        Detection("person", 0.9, (10, 10, 50, 100), "a"),
        Detection("person", 0.8, (100, 10, 150, 100), "b"),
    )
    assert len(evaluator.evaluate(_line_frame(0, detections))) == 1
    events = evaluator.evaluate(_line_frame(1, detections[:1]))
    assert len(events) == 1
    assert events[0].target["condition"] == "occupancy_recovered"
    assert events[0].target["count"] == 1
    assert events[0].target["threshold"] == 2

def test_dwell_condition_emits_after_configured_duration():
    rule = Rule(
        "dwell",
        "CH01",
        "danger zone dwell",
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        condition="dwell",
        dwell_seconds=2,
        confirm_frames=1,
        cooldown_seconds=30,
    )
    evaluator = RuleEvaluator([rule])
    detection = Detection("person", 0.9, (10, 10, 50, 100), "a")
    assert evaluator.evaluate(_line_frame(0, (detection,))) == []
    assert evaluator.evaluate(_line_frame(1, (detection,))) == []
    events = evaluator.evaluate(_line_frame(2, (detection,)))
    assert len(events) == 1
    assert events[0].target["condition"] == "dwell"
    assert events[0].target["dwell_seconds"] >= 2
