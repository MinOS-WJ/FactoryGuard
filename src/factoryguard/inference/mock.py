from __future__ import annotations

from collections.abc import Callable

from factoryguard.domain.models import Detection, Frame

from .base import InferenceHealth


class MockInferenceEngine:
    """Scriptable detector used by the no-camera simulation and tests."""

    def __init__(self, detector: Callable[[Frame], tuple[Detection, ...]] | None = None) -> None:
        self._detector = detector or (lambda frame: frame.detections)

    def health_check(self) -> InferenceHealth:
        return InferenceHealth("ready", "mock", model_version="simulation")

    def infer(self, frame: Frame) -> tuple[Detection, ...]:
        return tuple(self._detector(frame))

    def reset(self) -> None:
        return None
