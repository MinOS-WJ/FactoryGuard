"""Reusable on-frame geometry editor for polygons and tripwires."""

from __future__ import annotations

from typing import Any

from .qt import HAS_QT

if HAS_QT:
    from .qt import QColor, QFont, QImage, QPainter, QPen, QRectF, Qt, QWidget, Signal

    class GeometryCanvas(QWidget):
        """Draw one polygon or tripwire directly over a frame or coordinate grid."""

        geometry_changed = Signal(object)

        def __init__(self, *, width: int = 1280, height: int = 720, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._reference = QImage()
            self._video_size = (max(1, width), max(1, height))
            self._mode = "polygon"
            self._points: list[tuple[float, float]] = []
            self.setMinimumSize(420, 260)

        @property
        def geometry(self) -> tuple[tuple[float, float], ...]:
            return tuple(self._points)

        @property
        def mode(self) -> str:
            return self._mode

        def set_reference_image(self, image: QImage | None) -> None:
            self._reference = image.copy() if image is not None and not image.isNull() else QImage()
            if not self._reference.isNull():
                self._video_size = (self._reference.width(), self._reference.height())
            self.update()

        def set_video_size(self, width: int, height: int) -> None:
            self._video_size = (max(1, int(width)), max(1, int(height)))
            self.update()

        def set_mode(self, mode: str) -> None:
            if mode not in {"polygon", "line"}:
                raise ValueError("geometry mode must be polygon or line")
            self._mode = mode
            if mode == "line" and len(self._points) > 2:
                self._points = self._points[:2]
            self.update()

        def set_geometry(self, points: tuple[tuple[float, float], ...]) -> None:
            self._points = [(float(x), float(y)) for x, y in points]
            self.update()
            self.geometry_changed.emit(self.geometry)

        def clear(self) -> None:
            self._points.clear()
            self.update()
            self.geometry_changed.emit(self.geometry)

        def undo(self) -> None:
            if self._points:
                self._points.pop()
                self.update()
                self.geometry_changed.emit(self.geometry)

        def finish(self) -> bool:
            if self._mode == "line":
                return len(self._points) == 2
            return len(self._points) >= 3

        def add_source_point(self, x: float, y: float) -> None:
            width, height = self._video_size
            point = (min(max(0.0, x), float(width)), min(max(0.0, y), float(height)))
            if self._mode == "line" and len(self._points) >= 2:
                self._points.clear()
            self._points.append(point)
            self.update()
            self.geometry_changed.emit(self.geometry)

        def _content_rect(self) -> QRectF:
            width, height = self._video_size
            scale = min(self.width() / width, self.height() / height)
            target_width = width * scale
            target_height = height * scale
            return QRectF(
                (self.width() - target_width) / 2,
                (self.height() - target_height) / 2,
                target_width,
                target_height,
            )

        def _source_point(self, position: Any) -> tuple[float, float] | None:
            rect = self._content_rect()
            if not rect.contains(position):
                return None
            width, height = self._video_size
            return (
                (position.x() - rect.left()) * width / rect.width(),
                (position.y() - rect.top()) * height / rect.height(),
            )

        def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if event.button() == Qt.RightButton:
                self.undo()
                return
            if event.button() != Qt.LeftButton:
                return
            point = self._source_point(event.position())
            if point is not None:
                self.add_source_point(*point)

        def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
            if event.button() == Qt.LeftButton and self._mode == "polygon" and self.finish():
                self.geometry_changed.emit(self.geometry)

        def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt API
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), QColor("#101722"))
            rect = self._content_rect()
            if not self._reference.isNull():
                painter.drawImage(rect, self._reference)
            else:
                painter.fillRect(rect, QColor("#1a2535"))
                painter.setPen(QColor("#52627a"))
                for x in range(1, 10):
                    line_x = rect.left() + rect.width() * x / 10
                    painter.drawLine(int(line_x), int(rect.top()), int(line_x), int(rect.bottom()))
                for y in range(1, 6):
                    line_y = rect.top() + rect.height() * y / 6
                    painter.drawLine(int(rect.left()), int(line_y), int(rect.right()), int(line_y))

            if self._points:
                painter.setPen(QPen(QColor("#ffd54a"), 3))
                mapped = [
                    (
                        rect.left() + x * rect.width() / self._video_size[0],
                        rect.top() + y * rect.height() / self._video_size[1],
                    )
                    for x, y in self._points
                ]
                if self._mode == "line" and len(mapped) >= 2:
                    painter.drawLine(int(mapped[0][0]), int(mapped[0][1]), int(mapped[1][0]), int(mapped[1][1]))
                else:
                    for first, second in zip(mapped, mapped[1:]):
                        painter.drawLine(int(first[0]), int(first[1]), int(second[0]), int(second[1]))
                    if len(mapped) >= 3:
                        painter.drawLine(int(mapped[-1][0]), int(mapped[-1][1]), int(mapped[0][0]), int(mapped[0][1]))
                painter.setBrush(QColor("#ffd54a"))
                for x, y in mapped:
                    painter.drawEllipse(QRectF(x - 5, y - 5, 10, 10))

            painter.setPen(QColor("#e7edf6"))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            instruction = "单击添加点，右键撤销；绊线取 2 点，多边形取 3 点以上"
            painter.fillRect(QRectF(rect.left() + 8, rect.top() + 8, rect.width() - 16, 28), QColor(16, 23, 34, 190))
            painter.drawText(
                QRectF(rect.left() + 14, rect.top() + 10, rect.width() - 28, 24),
                Qt.AlignLeft,
                instruction,
            )

else:

    class GeometryCanvas:  # pragma: no cover - only used without Qt
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("PySide6 is required for geometry editing")


__all__ = ["GeometryCanvas"]