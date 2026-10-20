from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np

from factoryguard.domain.models import Detection

from .base import InferenceHealth

COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


class YoloOnnxEngine:
    """CPU-only YOLO export adapter for Ultralytics detection models."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        labels: Iterable[str] = COCO_LABELS,
        input_size: int = 640,
        confidence: float = 0.25,
        iou: float = 0.45,
    ) -> None:
        if input_size <= 0 or not 0 < confidence <= 1 or not 0 < iou <= 1:
            raise ValueError("invalid YOLO inference parameters")
        self.model_path = Path(model_path)
        if not self.model_path.is_file() and not self.model_path.is_absolute():
            repository_model = Path(__file__).resolve().parents[3] / self.model_path
            if repository_model.is_file():
                self.model_path = repository_model
        self.labels = tuple(labels)
        self.input_size = input_size
        self.confidence = confidence
        self.iou = iou
        self._session = None
        self._input_name = None
        self._health = InferenceHealth("degraded", "cpu", detail="model not loaded")
        self._load()

    def _load(self) -> None:
        if not self.model_path.is_file():
            self._health = InferenceHealth("degraded", "cpu", detail=f"model not found: {self.model_path}")
            return
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
            self._input_name = self._session.get_inputs()[0].name
            self._health = InferenceHealth("ready", "cpu", detail="CPUExecutionProvider")
        except Exception as exc:  # noqa: BLE001 - surface the model load failure in health state
            self._health = InferenceHealth("degraded", "cpu", detail=f"model load failed: {exc}")

    def health_check(self) -> InferenceHealth:
        return self._health

    def reset(self) -> None:
        self._session = None
        self._input_name = None
        self._load()

    def infer_image(self, image: np.ndarray) -> tuple[Detection, ...]:
        if self._session is None or self._input_name is None:
            raise RuntimeError(self._health.detail or "ONNX model is not ready")
        tensor, scale, pad = _letterbox(image, self.input_size)
        output = self._session.run(None, {self._input_name: tensor})[0]
        return _decode(output, image.shape[:2], scale, pad, self.labels, self.confidence, self.iou)


def _letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[float, float]]:
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be an HxWx3 BGR array")
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    x_idx = np.minimum((np.arange(resized_width) / scale).astype(np.int32), width - 1)
    y_idx = np.minimum((np.arange(resized_height) / scale).astype(np.int32), height - 1)
    resized = image[y_idx[:, None], x_idx[None, :]]
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x = (size - resized_width) / 2
    pad_y = (size - resized_height) / 2
    left, top = round(pad_x), round(pad_y)
    canvas[top : top + resized_height, left : left + resized_width] = resized
    rgb = canvas[:, :, ::-1].astype(np.float32) / 255.0
    return np.transpose(rgb, (2, 0, 1))[None, ...], scale, (pad_x, pad_y)


def _decode(
    output: np.ndarray,
    original_shape: tuple[int, int],
    scale: float,
    pad: tuple[float, float],
    labels: tuple[str, ...],
    confidence: float,
    iou_threshold: float,
) -> tuple[Detection, ...]:
    if output.ndim != 3 or output.shape[0] != 1:
        raise ValueError(f"unexpected YOLO output shape: {output.shape}")
    predictions = output[0]
    if predictions.ndim != 2:
        raise ValueError(f"unexpected YOLO prediction shape: {predictions.shape}")
    # Ultralytics exports [1, features, anchors], while some runtimes
    # transpose it to [1, anchors, features]. Use the known feature count
    # first; the old small-dimension heuristic misread a tiny test output.
    feature_count = 4 + len(labels)
    objectness_feature_count = feature_count + 1
    if predictions.shape[1] in (feature_count, objectness_feature_count):
        pass
    elif predictions.shape[0] in (feature_count, objectness_feature_count) or predictions.shape[0] < predictions.shape[1] and predictions.shape[0] <= 128:
        predictions = predictions.T
    elif predictions.shape[1] > 128:
        pass
    else:
        raise ValueError(f"unexpected YOLO prediction shape: {predictions.shape}")
    if predictions.shape[1] < feature_count:
        raise ValueError(f"YOLO output has {predictions.shape[1]} features; expected at least {feature_count}")
    boxes_xywh = predictions[:, :4]
    if predictions.shape[1] >= objectness_feature_count and predictions.shape[1] != feature_count:
        scores = predictions[:, 5 : 5 + len(labels)] * predictions[:, 4:5]
    else:
        scores = predictions[:, 4 : 4 + len(labels)]
    class_ids = np.argmax(scores, axis=1)
    confidences = scores[np.arange(len(scores)), class_ids]
    keep = confidences >= confidence
    if not np.any(keep):
        return ()
    boxes = _to_original_boxes(boxes_xywh[keep], original_shape, scale, pad)
    class_ids = class_ids[keep]
    confidences = confidences[keep]
    selected = _nms(boxes, confidences, class_ids, iou_threshold)
    return tuple(
        Detection(labels[int(class_ids[i])], float(confidences[i]), tuple(float(v) for v in boxes[i]))
        for i in selected
    )


def _to_original_boxes(boxes: np.ndarray, shape: tuple[int, int], scale: float, pad: tuple[float, float]) -> np.ndarray:
    height, width = shape
    x, y, w, h = boxes.T
    x1 = np.clip((x - w / 2 - pad[0]) / scale, 0, width)
    y1 = np.clip((y - h / 2 - pad[1]) / scale, 0, height)
    x2 = np.clip((x + w / 2 - pad[0]) / scale, 0, width)
    y2 = np.clip((y + h / 2 - pad[1]) / scale, 0, height)
    return np.column_stack((x1, y1, x2, y2))


def _nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, threshold: float) -> list[int]:
    selected: list[int] = []
    for class_id in np.unique(classes):
        indices = np.where(classes == class_id)[0]
        indices = indices[np.argsort(scores[indices])[::-1]]
        while len(indices):
            current = int(indices[0])
            selected.append(current)
            if len(indices) == 1:
                break
            rest = indices[1:]
            overlap = _iou(boxes[current], boxes[rest])
            indices = rest[overlap <= threshold]
    return sorted(selected, key=lambda i: float(scores[i]), reverse=True)


def _iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    left = np.maximum(box[0], others[:, 0])
    top = np.maximum(box[1], others[:, 1])
    right = np.minimum(box[2], others[:, 2])
    bottom = np.minimum(box[3], others[:, 3])
    intersection = np.maximum(0, right - left) * np.maximum(0, bottom - top)
    area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
    other_area = np.maximum(0, others[:, 2] - others[:, 0]) * np.maximum(0, others[:, 3] - others[:, 1])
    return intersection / np.maximum(area + other_area - intersection, 1e-9)
