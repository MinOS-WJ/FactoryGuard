from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from factoryguard.domain.models import Detection, Event, Frame
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.storage.sqlite import SQLiteStore


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
    ) -> None:
        self.evaluator = evaluator
        self.store = store
        self.outbox_channel = outbox_channel
        self.outbox_channels = tuple(outbox_channels or (outbox_channel,))
        if not self.outbox_channels:
            raise ValueError("at least one outbox channel is required")
        self._tracks: dict[str, list[_TrackState]] = {}
        self._track_counters: dict[str, int] = {}

    def process(self, frame: Frame, detections: tuple[Detection, ...] | list[Detection], *, armed: bool = True) -> PipelineResult:
        normalized = self._associate_tracks(frame.stream_id, frame.captured_at, detections)
        evaluated_frame = replace(frame, detections=normalized)
        events = tuple(self.evaluator.evaluate(evaluated_frame, armed=armed))
        for event in events:
            self.store.create_event_with_outbox(event, channels=self.outbox_channels)
        return PipelineResult(frame.frame_no, len(normalized), events)

    def _associate_tracks(
        self,
        stream_id: str,
        captured_at: datetime,
        detections: tuple[Detection, ...] | list[Detection],
    ) -> tuple[Detection, ...]:
        """Give detector outputs stable IDs when the model has no tracker.

        YOLO detection order is not a track identity.  Matching by same-label
        bounding-box IoU prevents two people swapping output indexes from
        resetting confirmation state or creating duplicate dedup keys.
        Explicit IDs from a tracker are preserved unchanged.
        """
        previous = [
            state
            for state in self._tracks.get(stream_id, ())
            if (captured_at - state.last_seen).total_seconds() <= 5.0
        ]
        used: set[str] = set()
        current: list[_TrackState] = []
        normalized: list[Detection] = []
        for detection in detections:
            track_id = detection.track_id
            if track_id is None:
                candidates = sorted(
                    (
                        state
                        for state in previous
                        if state.track_id not in used and state.label == detection.label
                    ),
                    key=lambda state: _bbox_iou(state.bbox, detection.bbox),
                    reverse=True,
                )
                if candidates and _bbox_iou(candidates[0].bbox, detection.bbox) >= 0.15:
                    track_id = candidates[0].track_id
                else:
                    sequence = self._track_counters.get(stream_id, 0)
                    self._track_counters[stream_id] = sequence + 1
                    track_id = f"auto:{stream_id}:{detection.label}:{sequence}"
            used.add(track_id)
            normalized_detection = replace(detection, track_id=track_id)
            normalized.append(normalized_detection)
            current.append(_TrackState(track_id, detection.label, detection.bbox, captured_at))
        retained = [state for state in previous if state.track_id not in used]
        self._tracks[stream_id] = current + retained
        return tuple(normalized)


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
