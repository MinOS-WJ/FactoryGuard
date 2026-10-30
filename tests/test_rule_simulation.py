from factoryguard.domain.models import Rule, RuleType
from factoryguard.domain.rule_simulation import replay_rule_from_track, simulate_rule


def _polygon_rule(condition: str, **kwargs) -> Rule:
    return Rule(
        f"sim-{condition}",
        "CH01",
        condition,
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        condition=condition,
        **kwargs,
    )


def _line_rule(condition: str, **kwargs) -> Rule:
    return Rule(
        f"sim-{condition}",
        "CH01",
        condition,
        RuleType.LINE,
        ((0, 200), (500, 200)),
        condition=condition,
        direction="down",
        **kwargs,
    )



def test_replay_rule_from_historical_track_triggers_line_cross():
    rule = _line_rule("line_cross", confirm_frames=1, cooldown_seconds=30)
    report = replay_rule_from_track(
        rule,
        track=((100, 180), (100, 220)),
        label="person",
        confidence=0.95,
    )
    assert report.triggered is True
    assert report.events == 1


def test_replay_rule_rejects_empty_track():
    import pytest
    rule = _line_rule("line_cross")
    with pytest.raises(ValueError):
        replay_rule_from_track(rule, track=())

def test_rule_simulation_covers_all_condition_components():
    rules = [
        _polygon_rule("intrusion", confirm_frames=3),
        _polygon_rule("occupancy", count_threshold=3),
        _polygon_rule("dwell", dwell_seconds=3),
        _line_rule("line_cross"),
        _line_rule("line_count", count_threshold=4, window_seconds=30),
    ]
    for rule in rules:
        report = simulate_rule(rule)
        assert report.triggered is True, report.summary
        assert report.events >= 1
        assert report.sample_event is not None
