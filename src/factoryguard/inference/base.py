from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from factoryguard.domain.models import Detection, Frame


@dataclass(frozen=True, slots=True)
class InferenceHealth:
    state: str
    provider: str
    model_version: str | None = None
    detail: str | None = None


class InferenceEngine(Protocol):
    def health_check(self) -> InferenceHealth: ...

    def infer(self, frame: Frame) -> tuple[Detection, ...]: ...

    def reset(self) -> None: ...


def cpu_health(model_path: str | None = None) -> InferenceHealth:
    if not model_path:
        return InferenceHealth("degraded", "cpu", detail="no ONNX model configured; use simulation or configure a model")
    return InferenceHealth("ready", "cpu", detail="ONNX Runtime CPU provider selected")
