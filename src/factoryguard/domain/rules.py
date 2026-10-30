from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from dataclasses import replace

from .condition_tree import condition_tree_summary, evaluate_condition_tree, iter_leaf_nodes

from .geometry import crossing_direction, point_in_polygon
from .models import Detection, Event, Frame, Rule, RuleMode, RuleType


@dataclass(slots=True)
class _Candidate:
    hits: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    track: list[tuple[float, float]] = field(default_factory=list)
    last_detection: Detection | None = None


class RuleEvaluator:
    """Stateful, deterministic multi-frame confirmation and cooldown engine."""

    def __init__(self, rules: list[Rule] | tuple[Rule, ...] = ()) -> None:
        self._rules = {rule.id: rule for rule in rules}
        self._candidates: dict[tuple[str, str], _Candidate] = {}
        self._cooldown_until: dict[tuple[str, str], datetime] = {}
        self._condition_counts: dict[tuple[str, str], list[datetime]] = {}
        self._expression_evaluators: dict[str, dict[str, RuleEvaluator]] = {}
        self._occupancy_triggered: dict[tuple[str, str], bool] = {}

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(self._rules.values())

    def add_rule(self, rule: Rule) -> None:
        self._rules[rule.id] = rule

    def evaluate(self, frame: Frame, *, armed: bool = True) -> list[Event]:
        """Evaluate one frame and return newly confirmed events."""
        if not armed:
            self._candidates.clear()
            return []
        emitted: list[Event] = []
        seen: set[tuple[str, str]] = set()
        for rule in self._rules.values():
            if not rule.enabled or rule.stream_id != frame.stream_id:
                continue
            if rule.site_id is not None and rule.site_id != frame.site_id:
                continue
            if rule.area_id is not None and rule.area_id != frame.area_id:
                continue
            if rule.camera_id is not None and rule.camera_id != frame.camera_id:
                continue
            matching: dict[tuple[str, str], Detection] = {}
            for detection in frame.detections:
                if not self._eligible(detection, rule):
                    continue
                key = (rule.id, detection.track_id or self._fallback_track(detection))
                if rule.condition not in {"line_cross", "line_count"} and not self._matches(rule, detection, key):
                    continue
                previous = matching.get(key)
                if previous is None or detection.confidence > previous.confidence:
                    matching[key] = detection

            if rule.condition_nodes:
                self._evaluate_expression_rule(rule, frame, emitted)
                continue

            if rule.condition == "occupancy":
                self._evaluate_occupancy(rule, frame, matching, emitted)
                continue

            for key, detection in matching.items():
                seen.add(key)
                candidate = self._candidates.setdefault(key, _Candidate())
                now = frame.captured_at
                candidate.first_seen = candidate.first_seen or now
                candidate.last_seen = frame.captured_at
                candidate.last_detection = detection
                previous_point = candidate.track[-1] if candidate.track else None
                candidate.track.append(detection.anchor)
                cooldown_until = self._cooldown_until.get(key)

                if rule.condition in {"line_cross", "line_count"}:
                    if previous_point is None:
                        continue
                    direction = crossing_direction(previous_point, detection.anchor, (rule.geometry[0], rule.geometry[1]))
                    if direction is None or (rule.direction != "both" and direction != rule.direction):
                        continue
                    if rule.condition == "line_cross":
                        if cooldown_until is None or now >= cooldown_until:
                            emitted.append(Event.create(rule=rule, frame=frame, detection=detection, track=candidate.track))
                            self._cooldown_until[key] = now + timedelta(seconds=rule.cooldown_seconds)
                        candidate.track[:] = [detection.anchor]
                        continue
                    if cooldown_until is not None and now < cooldown_until:
                        candidate.track[:] = [detection.anchor]
                        continue
                    count_key = (rule.id, "__line_count")
                    timestamps = self._condition_counts.setdefault(count_key, [])
                    timestamps.append(now)
                    cutoff = now - timedelta(seconds=rule.window_seconds)
                    timestamps[:] = [value for value in timestamps if value >= cutoff]
                    count_cooldown = self._cooldown_until.get(count_key)
                    if len(timestamps) >= rule.count_threshold and (
                        count_cooldown is None or now >= count_cooldown
                    ):
                        event = Event.create(rule=rule, frame=frame, detection=detection, track=candidate.track)
                        emitted.append(
                            replace(
                                event,
                                target={
                                    **event.target,
                                    "condition": "line_count",
                                    "direction": direction,
                                    "count": len(timestamps),
                                    "threshold": rule.count_threshold,
                                    "window_seconds": rule.window_seconds,
                                },
                            )
                        )
                        self._cooldown_until[count_key] = now + timedelta(seconds=rule.cooldown_seconds)
                        timestamps.clear()
                    self._cooldown_until[key] = now + timedelta(
                        seconds=max(1.0, rule.cooldown_seconds)
                    )
                    candidate.track[:] = [detection.anchor]
                    continue

                candidate.hits += 1
                if cooldown_until is not None and now < cooldown_until:
                    continue
                if rule.condition == "dwell":
                    dwell = (now - candidate.first_seen).total_seconds()
                    if dwell < rule.dwell_seconds:
                        continue
                    event = Event.create(rule=rule, frame=frame, detection=detection, track=candidate.track)
                    emitted.append(
                        replace(
                            event,
                            target={
                                **event.target,
                                "condition": "dwell",
                                "dwell_seconds": round(dwell, 3),
                                "threshold_seconds": rule.dwell_seconds,
                            },
                        )
                    )
                    self._cooldown_until[key] = now + timedelta(seconds=rule.cooldown_seconds)
                    candidate.hits = 0
                    continue
                if candidate.hits < rule.confirm_frames:
                    continue
                emitted.append(Event.create(rule=rule, frame=frame, detection=detection, track=candidate.track))
                self._cooldown_until[key] = now + timedelta(seconds=rule.cooldown_seconds)
                candidate.hits = 0
                candidate.track.clear()
        self._expire(frame.captured_at, seen)
        return emitted

    def _evaluate_occupancy(
        self,
        rule: Rule,
        frame: Frame,
        matching: dict[tuple[str, str], Detection],
        emitted: list[Event],
    ) -> None:
        key = (rule.id, "__occupancy")
        now = frame.captured_at
        cooldown_until = self._cooldown_until.get(key)
        count = len(matching)
        triggered = self._occupancy_triggered.get(key, False)

        if count >= rule.count_threshold:
            if cooldown_until is not None and now < cooldown_until:
                return
            detection = next(iter(matching.values()))
            event = Event.create(rule=rule, frame=frame, detection=detection)
            emitted.append(
                replace(
                    event,
                    target={
                        **event.target,
                        "condition": "occupancy",
                        "count": count,
                        "threshold": rule.count_threshold,
                    },
                )
            )
            self._cooldown_until[key] = now + timedelta(seconds=rule.cooldown_seconds)
            self._occupancy_triggered[key] = True
            return

        if triggered and (cooldown_until is None or now >= cooldown_until):
            detection = next(iter(matching.values())) if matching else None
            if detection is None:
                # A drop to zero still deserves a recovery event; synthesize a
                # deterministic track marker from the previous threshold.
                event = Event.create(
                    rule=rule,
                    frame=frame,
                    detection=Detection(rule.target_labels and next(iter(rule.target_labels)) or "person", 0.0, (0, 0, 0, 0)),
                    level="info",
                )
            else:
                event = Event.create(rule=rule, frame=frame, detection=detection, level="info")
            emitted.append(
                replace(
                    event,
                    target={
                        **event.target,
                        "condition": "occupancy_recovered",
                        "count": count,
                        "threshold": rule.count_threshold,
                    },
                )
            )
            self._cooldown_until[key] = now + timedelta(seconds=rule.cooldown_seconds)
            self._occupancy_triggered[key] = False

    def _evaluate_expression_rule(self, rule: Rule, frame: Frame, emitted: list[Event]) -> None:
        key = (rule.id, "__expression")
        now = frame.captured_at
        cooldown_until = self._cooldown_until.get(key)
        if cooldown_until is not None and now < cooldown_until:
            return
        leaves = iter_leaf_nodes(rule.condition_nodes)
        leaf_evaluators = self._expression_evaluators.setdefault(rule.id, {})
        truths: dict[str, bool] = {}
        leaf_events: dict[str, list[Event]] = {}
        representative: Event | None = None
        for leaf in leaves:
            leaf_id = str(leaf["id"])
            evaluator = leaf_evaluators.get(leaf_id)
            if evaluator is None:
                evaluator = RuleEvaluator([self._leaf_rule(rule, leaf)])
                leaf_evaluators[leaf_id] = evaluator
            results = evaluator.evaluate(frame)
            truths[leaf_id] = bool(results)
            leaf_events[leaf_id] = results
            if results and representative is None:
                representative = results[0]
        if not evaluate_condition_tree(rule.condition_nodes, truths):
            return
        if representative is None:
            if frame.detections:
                detection = frame.detections[0]
            else:
                label = next(iter(rule.target_labels)) if rule.target_labels else "person"
                detection = Detection(label, 0.0, (0, 0, 0, 0), "expression")
            event = Event.create(rule=rule, frame=frame, detection=detection)
            event = replace(
                event,
                target={**event.target, "condition_expression": condition_tree_summary(rule.condition_nodes)},
            )
        else:
            target = representative.target
            detection = Detection(
                str(target.get("label", "person")),
                float(target.get("confidence", 0.0)),
                tuple(target.get("bbox", (0, 0, 0, 0))),
                str(target.get("track_id", "expression")),
            )
            event = Event.create(rule=rule, frame=frame, detection=detection, track=representative.track)
            event = replace(
                event,
                target={
                    **target,
                    "condition_expression": condition_tree_summary(rule.condition_nodes),
                },
            )
        emitted.append(event)
        self._cooldown_until[key] = now + timedelta(seconds=rule.cooldown_seconds)

    def _leaf_rule(self, rule: Rule, leaf: dict[str, Any]) -> Rule:
        condition = str(leaf["condition"])
        rule_type = RuleType.LINE if condition in {"line_cross", "line_count"} else RuleType.POLYGON
        return Rule(
            id=f"{rule.id}:expr:{leaf['id']}",
            stream_id=rule.stream_id,
            site_id=rule.site_id,
            area_id=rule.area_id,
            camera_id=rule.camera_id,
            name=f"{rule.name}:{leaf['id']}",
            type=rule_type,
            geometry=rule.geometry,
            target_labels=rule.target_labels,
            min_confidence=rule.min_confidence,
            min_box_height=rule.min_box_height,
            confirm_frames=int(leaf.get("confirm_frames", rule.confirm_frames)),
            cooldown_seconds=float(leaf.get("cooldown_seconds", rule.cooldown_seconds)),
            direction=str(leaf.get("direction", rule.direction)),
            condition=condition,
            count_threshold=int(leaf.get("count_threshold", rule.count_threshold or 1)),
            window_seconds=float(leaf.get("window_seconds", rule.window_seconds)),
            dwell_seconds=float(leaf.get("dwell_seconds", rule.dwell_seconds)),
            actions=("event",),
            enabled=True,
            publication_status="published",
            version=rule.version,
        )

    def _eligible(self, detection: Detection, rule: Rule) -> bool:
        return (
            detection.label in rule.target_labels
            and detection.confidence >= rule.min_confidence
            and detection.height >= rule.min_box_height
        )

    def _matches(self, rule: Rule, detection: Detection, key: tuple[str, str]) -> bool:
        if rule.type is RuleType.POLYGON:
            return point_in_polygon(detection.anchor, rule.geometry)
        candidate = self._candidates.get(key)
        if candidate is None or not candidate.track:
            return True
        direction = crossing_direction(candidate.track[-1], detection.anchor, (rule.geometry[0], rule.geometry[1]))
        return direction is not None and (rule.direction == "both" or direction == rule.direction)

    def _expire(self, now: datetime, seen: set[tuple[str, str]]) -> None:
        for key, candidate in list(self._candidates.items()):
            if key in seen:
                continue
            if candidate.last_seen is None or (now - candidate.last_seen).total_seconds() > 5:
                self._candidates.pop(key, None)

    @staticmethod
    def _fallback_track(detection: Detection) -> str:
        x, y = detection.anchor
        return f"anchor:{round(x / 16)}:{round(y / 16)}"
