"""Local inference interfaces and CPU-safe implementations."""

from .base import InferenceEngine
from .mock import MockInferenceEngine
from .yolo_onnx import YoloOnnxEngine

__all__ = [
    "InferenceEngine",
    "MockInferenceEngine",
    "YoloOnnxEngine",
]
