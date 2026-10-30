"""Deterministic dry-run scenarios for visual rule validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from .models import Detection, Frame, Rule, RuleType
from .rules import RuleEvaluator

CONDITION_LABELS = {
    "intrusion": "区域入侵",
    "line_cross": "绊线穿越",
    "line_count": "过线计数",
    "occupancy": "区域人数",
    "dwell": "区域滞留",
}


@dataclass(frozen=True, slots=True)
class RuleSimulationReport:
    triggered: bool
    events: int
    summary: str
    sample_event: object | None = None


def replay_rule_from_track(
    rule: Rule,
    *,
    track: tuple[tuple[float, float], ...] | list[tuple[float, float]],
    label: str = "person",
    confidence: float = 0.95,
    width: int = 1280,
    height: int = 720,
    occurred_at: datetime | None = None,
) -> RuleSimulationReport:
    """Replay one historical track against a rule without re-running inference.

    The stored track is typically a detection anchor path saved in the event
    payload.  It is reconstructed as one detection per frame at 200 ms
    intervals so confirmation, crossing, dwell and cooldown behaviour can be
    observed deterministically.
    """
    points = [tuple(float(value) for value in point) for point in track]
    if not points:
        raise ValueError("track must contain at least one point")
    if len(points) < 2 and rule.condition in {"line_cross", "line_count"}:
        return RuleSimulationReport(
            False,
            0,
            "历史轨迹只有 1 个点，无法验证过线条件；请选择包含完整轨迹的事件。",
        )
    condition_label = CONDITION_LABELS.get(rule.condition, rule.condition)
    evaluator = RuleEvaluator([rule])
    start = occurred_at or datetime(2026, 1, 1, tzinfo=UTC)
    if occurred_at is not None and not isinstance(occurred_at, datetime):
        occurred_at = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    frames = []
    for index, point in enumerate(points):
        x, y = point
        detection = Detection(label, float(confidence), (x - 12, y - 70, x + 12, y), "replay-track")
        frames.append(
            _frame(
                rule.stream_id,
                index,
                start + timedelta(milliseconds=200 * index),
                width,
                height,
                (detection,),
            )
        )
    frames = [
        replace(
            frame,
            site_id=rule.site_id,
            area_id=rule.area_id,
            camera_id=rule.camera_id,
        )
        for frame in frames
    ]
    events = []
    for frame in frames:
        events.extend(evaluator.evaluate(frame))
    triggered = bool(events)
    summary = (
        f"历史轨迹回放：条件“{condition_label}”触发 {len(events)} 个事件。"
        if triggered
        else f"历史轨迹回放：该轨迹未满足条件“{condition_label}”。"
    )
    return RuleSimulationReport(triggered, len(events), summary, events[0] if events else None)


def simulate_rule(rule: Rule, *, width: int = 1280, height: int = 720) -> RuleSimulationReport:
    """Exercise one rule with synthetic tracks that should satisfy its condition."""
    condition_label = CONDITION_LABELS.get(rule.condition, rule.condition)
    evaluator = RuleEvaluator([rule])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frames: list[Frame] = []

    if rule.condition in {"intrusion", "dwell"}:
        anchor = _interior_point(rule)
        detection = _detection_at(anchor, "sim-track")
        if rule.condition == "intrusion":
            count = max(1, rule.confirm_frames)
            frames = [
                _frame(rule.stream_id, index, start + timedelta(seconds=index), width, height, (detection,))
                for index in range(count)
            ]
        else:
            frames = [
                _frame(rule.stream_id, 0, start, width, height, (detection,)),
                _frame(
                    rule.stream_id,
                    1,
                    start + timedelta(seconds=max(1.0, rule.dwell_seconds + 0.5)),
                    width,
                    height,
                    (detection,),
                ),
            ]
    elif rule.condition == "occupancy":
        anchor = _interior_point(rule)
        detections = tuple(
            _detection_at((anchor[0] + index * 12, anchor[1]), f"sim-occupant-{index}")
            for index in range(max(1, rule.count_threshold))
        )
        frames = [_frame(rule.stream_id, 0, start, width, height, detections)]
    elif rule.condition in {"line_cross", "line_count"}:
        first, second = _crossing_points(rule)
        tracks = 1 if rule.condition == "line_cross" else max(1, rule.count_threshold)
        first_detections = tuple(
            _detection_at((first[0] + index * 8, first[1]), f"sim-cross-{index}")
            for index in range(tracks)
        )
        second_detections = tuple(
            _detection_at((second[0] + index * 8, second[1]), f"sim-cross-{index}")
            for index in range(tracks)
        )
        frames = [
            _frame(rule.stream_id, 0, start, width, height, first_detections),
            _frame(rule.stream_id, 1, start + timedelta(milliseconds=200), width, height, second_detections),
        ]

    frames = [
        replace(
            frame,
            site_id=rule.site_id,
            area_id=rule.area_id,
            camera_id=rule.camera_id,
        )
        for frame in frames
    ]
    events = []
    for frame in frames:
        events.extend(evaluator.evaluate(frame))
    triggered = bool(events)
    summary = (
        f"试运行成功：条件“{condition_label}”在合成场景中触发 {len(events)} 个事件。"
        if triggered
        else f"试运行未触发：请检查几何范围、方向、阈值或确认帧配置（{condition_label}）。"
    )
    return RuleSimulationReport(triggered, len(events), summary, events[0] if events else None)


def _frame(
    stream_id: str,
    frame_no: int,
    captured_at: datetime,
    width: int,
    height: int,
    detections: tuple[Detection, ...],
) -> Frame:
    return Frame(
        stream_id=stream_id,
        frame_no=frame_no,
        captured_at=captured_at,
        width=width,
        height=height,
        detections=detections,
    )


def _detection_at(anchor: tuple[float, float], track_id: str) -> Detection:
    x, y = anchor
    return Detection(
        "person",
        0.95,
        (x - 12, y - 70, x + 12, y),
        track_id,
    )


def _interior_point(rule: Rule) -> tuple[float, float]:
    points = rule.geometry
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _crossing_points(rule: Rule) -> tuple[tuple[float, float], tuple[float, float]]:
    first_point, second_point = rule.geometry
    middle = ((first_point[0] + second_point[0]) / 2, (first_point[1] + second_point[1]) / 2)
    if rule.direction in {"right", "left"}:
        offset = 30.0 if rule.direction == "right" else -30.0
        return (middle[0] - offset, middle[1]), (middle[0] + offset, middle[1])
    if rule.direction == "down":
        return (middle[0], middle[1] - 30), (middle[0], middle[1] + 30)
    return (middle[0], middle[1] + 30), (middle[0], middle[1] - 30)


__all__ = ["RuleSimulationReport", "replay_rule_from_track", "simulate_rule"]
