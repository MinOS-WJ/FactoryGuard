"""Camera-only live preview widget for the merchant console."""

from __future__ import annotations

from typing import Any

import numpy as np

from .qt import HAS_QT

if HAS_QT:
    from .qt import QColor, QImage, QPainter, QRectF, QSizePolicy, Qt, QWidget


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


if HAS_QT:

    class PreviewWidget(QWidget):
        """Aspect-ratio-preserving camera viewer without visual overlays."""

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._image = QImage()
            self._message = "等待视频流"
            self.setMinimumSize(420, 250)
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        @property
        def has_image(self) -> bool:
            return not self._image.isNull()

        def current_image(self) -> QImage:
            return self._image.copy() if not self._image.isNull() else QImage()

        def set_image(self, image: QImage, *, message: str = "") -> None:
            """Display an already owned Qt image."""
            self._image = image.copy() if not image.isNull() else QImage()
            self._message = message
            self.update()

        def clear(self, message: str = "等待视频流") -> None:
            self._image = QImage()
            self._message = message
            self.update()

        def paintEvent(self, _event: Any) -> None:
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
            painter.drawImage(QRectF(left, top, target.width(), target.height()), self._image)


else:

    class PreviewWidget:  # pragma: no cover - only used without Qt
        def __init__(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("PySide6 is required for image previews")


__all__ = ["PreviewWidget", "numpy_to_qimage"]
