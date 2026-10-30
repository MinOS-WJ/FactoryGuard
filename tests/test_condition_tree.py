from datetime import UTC, datetime, timedelta

from factoryguard.domain.condition_tree import evaluate_condition_tree, iter_leaf_nodes, normalize_condition_nodes
from factoryguard.domain.models import Detection, Frame, Rule, RuleType
from factoryguard.domain.rules import RuleEvaluator


def _frame(no: int, detections: tuple[Detection, ...]) -> Frame:
    return Frame(
        "CH01",
        no,
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=no),
        1280,
        720,
        detections=detections,
    )


def test_condition_tree_normalizes_and_evaluates_nested_logic():
    nodes = normalize_condition_nodes([
        {"id": "g1", "op": "and", "children": [
            {"id": "a", "condition": "intrusion"},
            {"id": "g2", "op": "or", "children": [
                {"id": "b", "condition": "line_cross", "direction": "both"},
                {"id": "c", "condition": "occupancy", "count_threshold": 2},
            ]},
        ]},
    ])
    leaves = iter_leaf_nodes(nodes)
    assert [leaf["id"] for leaf in leaves] == ["a", "b", "c"]
    assert evaluate_condition_tree(nodes, {"a": True, "b": False, "c": True}) is True
    assert evaluate_condition_tree(nodes, {"a": False, "b": False, "c": True}) is False


def test_condition_tree_rule_uses_or_across_leaf_conditions():
    rule = Rule(
        "expr-or",
        "CH01",
        "expr or",
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        condition="intrusion",
        condition_nodes=(
            {
                "id": "g1",
                "op": "or",
                "children": [
                    {"id": "occ", "condition": "occupancy", "count_threshold": 2},
                    {"id": "intr", "condition": "intrusion", "confirm_frames": 1},
                ],
            },
        ),
    )
    evaluator = RuleEvaluator([rule])
    detections = (
        Detection("person", 0.9, (10, 10, 50, 100), "a"),
        Detection("person", 0.8, (100, 10, 150, 100), "b"),
    )
    events = evaluator.evaluate(_frame(0, detections))
    assert len(events) == 1
    assert "condition_expression" in events[0].target


def test_condition_tree_not_node_triggers_when_leaf_is_false():
    rule = Rule(
        "expr-not",
        "CH01",
        "expr not",
        RuleType.POLYGON,
        ((0, 0), (500, 0), (500, 500), (0, 500)),
        condition_nodes=(
            {"id": "not1", "op": "not", "children": [
                {"id": "occ", "condition": "occupancy", "count_threshold": 2},
            ]},
        ),
    )
    evaluator = RuleEvaluator([rule])
    events = evaluator.evaluate(
        _frame(0, (Detection("person", 0.9, (10, 10, 50, 100), "a"),))
    )
    assert len(events) == 1
    assert "condition_expression" in events[0].target
