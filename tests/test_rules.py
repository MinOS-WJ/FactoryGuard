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
