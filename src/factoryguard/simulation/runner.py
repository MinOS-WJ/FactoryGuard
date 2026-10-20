from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from factoryguard.domain.models import Detection, Frame, Rule, RuleType
from factoryguard.domain.rules import RuleEvaluator
from factoryguard.inference.mock import MockInferenceEngine
from factoryguard.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class SimulationReport:
    frames: int
    events: int
    pending_notifications: int
    integrity: str


def run_demo(db_path: str | Path = ":memory:", *, frames: int = 6) -> SimulationReport:
    """Run a CPU-only warehouse intrusion scenario without cameras or a model."""
    rule = Rule(
        id="rule-warehouse-fence",
        channel_id="CH07",
        name="warehouse fence",
        type=RuleType.POLYGON,
        geometry=((100, 100), (500, 100), (500, 400), (100, 400)),
        confirm_frames=3,
        cooldown_seconds=60,
    )
    evaluator = RuleEvaluator([rule])
    store = SQLiteStore(db_path)
    inference = MockInferenceEngine()
    start = datetime(2026, 10, 17, 23, 14, tzinfo=UTC)
    for frame_no in range(frames):
        detection = Detection(
            label="person",
            confidence=0.83,
            bbox=(240 + frame_no, 160, 300 + frame_no, 320),
            track_id="track-889",
        )
        frame = Frame(
            stream_id="CH07",
            frame_no=frame_no,
            captured_at=start + timedelta(seconds=frame_no),
            nvr_time=start + timedelta(seconds=frame_no),
            width=640,
            height=480,
            detections=(detection,),
        )
        inferred = inference.infer(frame)
        for event in evaluator.evaluate(replace(frame, detections=inferred)):
            store.create_event_with_outbox(event)
    report = SimulationReport(
        frames=frames,
        events=len(store.list_events()),
        pending_notifications=len(store.list_pending_outbox()),
        integrity=store.integrity_check(),
    )
    store.close()
    return report
