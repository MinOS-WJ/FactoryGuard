"""Background ONNX model health checks for desktop settings pages."""

from __future__ import annotations

from factoryguard.inference.yolo_onnx import YoloOnnxEngine

from .qt import HAS_QT


if HAS_QT:
    from .qt import QObject, Signal, Slot

    class ModelHealthWorker(QObject):
        result_ready = Signal(bool, str)
        finished = Signal()

        def __init__(
            self,
            model_path: str,
            *,
            confidence: float = 0.25,
            iou: float = 0.45,
        ) -> None:
            super().__init__()
            self.model_path = model_path
            self.confidence = confidence
            self.iou = iou

        @Slot()
        def run(self) -> None:
            try:
                engine = YoloOnnxEngine(
                    self.model_path,
                    confidence=self.confidence,
                    iou=self.iou,
                )
                health = engine.health_check()
                detail = f"{health.state} · {health.provider} · {health.detail or '本机模型已就绪'}"
                ok = health.state == "ready"
            except (OSError, RuntimeError, ValueError) as exc:
                ok, detail = False, f"检查失败：{exc}"
            self.result_ready.emit(ok, detail)
            self.finished.emit()


else:

    class ModelHealthWorker:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            raise RuntimeError("PySide6 is required for model health checks")


__all__ = ["ModelHealthWorker"]