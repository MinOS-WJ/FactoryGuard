from __future__ import annotations
from collections.abc import Mapping

from dataclasses import dataclass, replace
from datetime import datetime

from factoryguard.domain.models import Detection, Event, Frame
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.notifications.destinations import resolve_notification_channels


@dataclass(frozen=True, slots=True)
class PipelineResult:
    frame_no: int
    detections: int
    events: tuple[Event, ...]


@dataclass(slots=True)
class _TrackState:
    track_id: str
    label: str
    bbox: tuple[float, float, float, float]
    last_seen: datetime


class DetectionPipeline:
    """Connect inference output to rules and the transactional event store."""

    def __init__(
        self,
        evaluator: RuleEvaluator,
        store: SQLiteStore,
        *,
        outbox_channel: str = "webhook",
        outbox_channels: tuple[str, ...] | list[str] | None = None,
        destination_channels: Mapping[str, str] | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.store = store
        self.outbox_channel = outbox_channel
        self.outbox_channels = (
            (outbox_channel,)
            if outbox_channels is None
            else tuple(outbox_channels)
        )
        self.destination_channels = dict(destination_channels or {})
        self._tracks: dict[str, list[_TrackState]] = {}
        self._track_counters: dict[str, int] = {}

    def process(self, frame: Frame, detections: tuple[Detection, ...] | list[Detection], *, armed: bool = True) -> PipelineResult:
        normalized = self._associate_tracks(frame.stream_id, frame.captured_at, detections)
        evaluated_frame = replace(frame, detections=normalized)
        events = tuple(self.evaluator.evaluate(evaluated_frame, armed=armed))
        rules = {rule.id: rule for rule in self.evaluator.rules}
        for event in events:
            rule = rules.get(event.rule_id)
            if rule is None:
                channels = self.outbox_channels
            elif "notify" in rule.actions:
                channels = resolve_notification_channels(
                    rule.destination_ids,
                    fallback_channels=self.outbox_channels,
                    destination_channels=self.destination_channels,
                )
            else:
                channels = ()
            if channels:
                self.store.create_event_with_outbox(event, channels=channels)
            else:
                self.store.create_event(event)
        return PipelineResult(frame.frame_no, len(normalized), events)

    def _associate_tracks(
        self,
        stream_id: str,
        captured_at: datetime,
        detections: tuple[Detection, ...] | list[Detection],
    ) -> tuple[Detection, ...]:
        """Give detector outputs stable IDs without requiring a model tracker.

        Detection order is not a track identity.  Same-label candidates are
        matched globally using bounding-box IoU plus normalized center
        distance.  The distance fallback keeps identity when a target moves far
        enough between frames that IoU alone becomes too small, while global
        assignment prevents two nearby targets from claiming the same track.
        Explicit IDs from a model tracker are preserved unchanged.
        """
        previous = [
            state
            for state in self._tracks.get(stream_id, ())
            if (captured_at - state.last_seen).total_seconds() <= 5.0
        ]
        explicit_ids = {str(detection.track_id) for detection in detections if detection.track_id}
        used: set[str] = set(explicit_ids)
        assigned: dict[int, str] = {}
        candidates: list[tuple[float, int, int]] = []
        for detection_index, detection in enumerate(detections):
            if detection.track_id:
                assigned[detection_index] = str(detection.track_id)
                continue
            for previous_index, state in enumerate(previous):
                if state.label != detection.label or state.track_id in used:
                    continue
                overlap = _bbox_iou(state.bbox, detection.bbox)
                distance_ratio = _bbox_center_distance_ratio(state.bbox, detection.bbox)
                if overlap < 0.15 and distance_ratio > 0.75:
                    continue
                score = overlap + max(0.0, 1.0 - min(distance_ratio, 1.0)) * 0.35
                candidates.append((score, detection_index, previous_index))
        for _score, detection_index, previous_index in sorted(candidates, reverse=True):
            if detection_index in assigned:
                continue
            state = previous[previous_index]
            if state.track_id in used:
                continue
            assigned[detection_index] = state.track_id
            used.add(state.track_id)

        current: list[_TrackState] = []
        normalized: list[Detection] = []
        for detection_index, detection in enumerate(detections):
            track_id = assigned.get(detection_index)
            if track_id is None:
                sequence = self._track_counters.get(stream_id, 0)
                self._track_counters[stream_id] = sequence + 1
                track_id = f"auto:{stream_id}:{detection.label}:{sequence}"
                assigned[detection_index] = track_id
                used.add(track_id)
            normalized_detection = replace(detection, track_id=track_id)
            normalized.append(normalized_detection)
            current.append(_TrackState(track_id, detection.label, detection.bbox, captured_at))
        retained = [state for state in previous if state.track_id not in used]
        self._tracks[stream_id] = current + retained
        return tuple(normalized)


def _bbox_center_distance_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_center = ((left[0] + left[2]) / 2.0, (left[1] + left[3]) / 2.0)
    right_center = ((right[0] + right[2]) / 2.0, (right[1] + right[3]) / 2.0)
    distance = ((left_center[0] - right_center[0]) ** 2 + (left_center[1] - right_center[1]) ** 2) ** 0.5
    scale = max(
        left[2] - left[0],
        left[3] - left[1],
        right[2] - right[0],
        right[3] - right[1],
        1.0,
    )
    return distance / scale


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0
