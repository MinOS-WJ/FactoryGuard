from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .geometry import crossing_direction, point_in_polygon
from .models import Detection, Event, Frame, Rule, RuleType


@dataclass(slots=True)
class _Candidate:
    hits: int = 0
    last_seen: datetime | None = None
    track: list[tuple[float, float]] = field(default_factory=list)
    last_detection: Detection | None = None


class RuleEvaluator:
    """Stateful, deterministic multi-frame confirmation and cooldown engine."""

    def __init__(self, rules: list[Rule] | tuple[Rule, ...] = ()) -> None:
        self._rules = {rule.id: rule for rule in rules}
        self._candidates: dict[tuple[str, str], _Candidate] = {}
        self._cooldown_until: dict[tuple[str, str], datetime] = {}

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
            # A tracker or detector can occasionally emit duplicate rows for
            # one object in the same frame.  Confirmation is frame based, so
            # only the strongest matching detection may advance one key.
            matching: dict[tuple[str, str], Detection] = {}
            for detection in frame.detections:
                if not self._eligible(detection, rule):
                    continue
                key = (rule.id, detection.track_id or self._fallback_track(detection))
                if not self._matches(rule, detection, key):
                    continue
                previous = matching.get(key)
                if previous is None or detection.confidence > previous.confidence:
                    matching[key] = detection
            for key, detection in matching.items():
                seen.add(key)
                candidate = self._candidates.setdefault(key, _Candidate())
                candidate.hits += 1
                candidate.last_seen = frame.captured_at
                candidate.last_detection = detection
                candidate.track.append(detection.anchor)
                cooldown_until = self._cooldown_until.get(key)
                if candidate.hits < rule.confirm_frames or (
                    cooldown_until is not None and frame.captured_at < cooldown_until
                ):
                    continue
                emitted.append(Event.create(rule=rule, frame=frame, detection=detection, track=candidate.track))
                self._cooldown_until[key] = frame.captured_at + timedelta(seconds=rule.cooldown_seconds)
                candidate.hits = 0
                candidate.track.clear()
        self._expire(frame.captured_at, seen)
        return emitted

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
