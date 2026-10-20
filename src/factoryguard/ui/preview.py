"""Live and event-replay preview widgets for the Qt control panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from factoryguard.domain.models import Detection

from .qt import HAS_QT

if HAS_QT:
    from .qt import QColor, QFont, QImage, QPainter, QPen, QRectF, Qt, QWidget


def numpy_to_qimage(image: np.ndarray):
    """Copy a BGR NumPy frame into an owned Qt image."""
    if not HAS_QT:
        raise RuntimeError("PySide6 is required for image previews")
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("preview image must be an HxWx3 BGR NumPy array")
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    contiguous = np.ascontiguousarray(image)
    qimage = QImage(
        contiguous.data,
        contiguous.shape[1],
        contiguous.shape[0],
        contiguous.strides[0],
        QImage.Format_BGR888,
    )
    return qimage.copy()


def save_frame_image(image: np.ndarray, path: str | Path) -> str:
    """Save one BGR frame as PNG and return its absolute path."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    qimage = numpy_to_qimage(image)
    if not qimage.save(str(destination), "PNG"):
        raise OSError(f"could not save preview image: {destination}")
    return str(destination)


if HAS_QT:

    class PreviewWidget(QWidget):
        """Aspect-ratio-preserving frame viewer with detection overlays."""

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._image = QImage()
            self._detections: tuple[Any, ...] = ()
            self._mode = "live"
            self._message = "等待视频流"
            self.setMinimumSize(420, 250)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        @property
        def has_image(self) -> bool:
            return not self._image.isNull()

        def set_frame(self, image: np.ndarray, detections: tuple[Detection, ...] | list[Detection] = ()) -> None:
            self._image = numpy_to_qimage(image)
            self._detections = tuple(detections)
            self._mode = "live"
            self._message = ""
            self.update()

        def set_image_file(
            self,
            path: str | Path,
            detections: tuple[Any, ...] | list[Any] = (),
            *,
            mode: str = "回放",
        ) -> bool:
            image = QImage(str(path))
            if image.isNull():
                self.clear("无法读取回放图片")
                return False
            self._image = image.convertToFormat(QImage.Format_RGB888)
            self._detections = tuple(detections)
            self._mode = mode
            self._message = ""
            self.update()
            return True

        def clear(self, message: str = "等待视频流") -> None:
            self._image = QImage()
            self._detections = ()
            self._message = message
            self.update()

        def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), QColor("#101722"))
            if self._image.isNull():
                painter.setPen(QColor("#9aa8bb"))
                painter.drawText(self.rect(), Qt.AlignCenter, self._message)
                return

            target = self._image.size()
            target.scale(self.size(), Qt.KeepAspectRatio)
            left = (self.width() - target.width()) / 2
            top = (self.height() - target.height()) / 2
            image_rect = QRectF(left, top, target.width(), target.height())
            painter.drawImage(image_rect, self._image)

            scale_x = image_rect.width() / max(1, self._image.width())
            scale_y = image_rect.height() / max(1, self._image.height())
            for detection in self._detections:
                label, confidence, bbox = _detection_values(detection)
                if bbox is None:
                    continue
                x1, y1, x2, y2 = bbox
                box = QRectF(
                    image_rect.left() + x1 * scale_x,
                    image_rect.top() + y1 * scale_y,
                    max(1.0, (x2 - x1) * scale_x),
                    max(1.0, (y2 - y1) * scale_y),
                )
                color = _label_color(label)
                painter.setPen(QPen(color, 2))
                painter.drawRect(box)
                text = f"{label} {confidence:.0%}"
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                metrics = painter.fontMetrics()
                text_rect = QRectF(box.left(), max(image_rect.top(), box.top() - 22), metrics.horizontalAdvance(text) + 12, 20)
                painter.fillRect(text_rect, color)
                painter.setPen(QColor("#08111d"))
                painter.drawText(text_rect, Qt.AlignCenter, text)

            painter.setPen(QColor("#e7edf6"))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            painter.drawText(QRectF(image_rect.left() + 10, image_rect.top() + 8, 120, 24), Qt.AlignLeft, self._mode)


else:

    class PreviewWidget:  # pragma: no cover - only used without Qt
        def __init__(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("PySide6 is required for image previews")


def _detection_values(value: Any) -> tuple[str, float, tuple[float, float, float, float] | None]:
    if isinstance(value, Detection):
        return value.label, value.confidence, value.bbox
    if isinstance(value, dict):
        target = value.get("target", value)
        bbox = target.get("bbox")
        if bbox is None or len(bbox) != 4:
            return str(target.get("label", "object")), float(target.get("confidence", 0.0)), None
        return str(target.get("label", "object")), float(target.get("confidence", 0.0)), tuple(float(v) for v in bbox)
    return "object", 0.0, None


def _label_color(label: str):
    if label == "person":
        return QColor("#ffb020")
    if label in {"fire", "smoke"}:
        return QColor("#ff5b5b")
    return QColor("#58d6c3")


__all__ = ["PreviewWidget", "numpy_to_qimage", "save_frame_image"]
