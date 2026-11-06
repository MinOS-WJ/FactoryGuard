"""Reusable visual components for the merchant-facing console."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .qt import HAS_QT

if HAS_QT:
    try:
        from PySide6.QtSvg import QSvgRenderer
    except Exception:
        QSvgRenderer = None  # type: ignore[assignment]

    from PySide6.QtCore import QRectF, Qt, Signal
    from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import (
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    from factoryguard.agent.context import CameraView
    from factoryguard.domain.incidents import SEVERITY_LABELS, STATUS_LABELS, Incident


if HAS_QT:

    def clear_layout(layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                clear_layout(child_layout)

    def repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def make_line() -> QFrame:
        line = QFrame()
        line.setObjectName("line")
        line.setFrameShape(QFrame.NoFrame)
        return line

    def _nav_icon_pixmap(kind: str, color: str, size: int = 18) -> QPixmap:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if kind == "dashboard":
            painter.drawRoundedRect(QRectF(2.5, 2.5, 5.5, 5.5), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(10.0, 2.5, 5.5, 5.5), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(2.5, 10.0, 5.5, 5.5), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(10.0, 10.0, 5.5, 5.5), 1.2, 1.2)
        elif kind == "live":
            painter.drawRoundedRect(QRectF(2.0, 3.5, 14.0, 11.0), 2.0, 2.0)
            painter.drawEllipse(QRectF(6.3, 6.8, 5.4, 5.4))
            painter.drawLine(16, 6, 16, 12)
        elif kind == "inquiry":
            painter.drawRoundedRect(QRectF(2.0, 2.8, 14.0, 10.5), 2.5, 2.5)
            painter.drawLine(6.0, 13.3, 5.0, 16.0)
            painter.drawLine(5.0, 16.0, 9.0, 13.3)
            painter.drawEllipse(QRectF(5.5, 6.6, 1.3, 1.3))
            painter.drawEllipse(QRectF(8.3, 6.6, 1.3, 1.3))
            painter.drawEllipse(QRectF(11.1, 6.6, 1.3, 1.3))
        elif kind == "incident":
            painter.drawLine(9, 2.2, 16.0, 15.3)
            painter.drawLine(16.0, 15.3, 2.0, 15.3)
            painter.drawLine(2.0, 15.3, 9, 2.2)
            painter.drawLine(9, 6.0, 9, 10.4)
            painter.drawPoint(9, 12.8)
        elif kind == "devices":
            painter.drawRoundedRect(QRectF(2.2, 4.0, 10.0, 8.0), 1.4, 1.4)
            painter.drawEllipse(QRectF(5.7, 6.9, 3.0, 3.0))
            painter.drawLine(12.2, 6.4, 15.6, 5.2)
            painter.drawLine(4.0, 15.5, 11.0, 15.5)
            painter.drawLine(7.5, 12.0, 7.5, 15.5)
        elif kind == "settings":
            painter.drawEllipse(QRectF(5.8, 5.8, 6.4, 6.4))
            painter.drawEllipse(QRectF(7.7, 7.7, 2.6, 2.6))
            for x1, y1, x2, y2 in (
                (9, 1.0, 9, 4.0),
                (9, 14.0, 9, 17.0),
                (1.0, 9, 4.0, 9),
                (14.0, 9, 17.0, 9),
                (3.4, 3.4, 5.5, 5.5),
                (12.5, 12.5, 14.6, 14.6),
                (14.6, 3.4, 12.5, 5.5),
                (5.5, 12.5, 3.4, 14.6),
            ):
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.end()
        return pixmap

    def make_nav_icon(kind: str, *, size: int = 18) -> QIcon:
        icon = QIcon()
        normal = _nav_icon_pixmap(kind, "#68766F", size)
        hover = _nav_icon_pixmap(kind, "#18382F", size)
        checked = _nav_icon_pixmap(kind, "#FFFFFF", size)
        icon.addPixmap(normal, QIcon.Normal, QIcon.Off)
        icon.addPixmap(hover, QIcon.Active, QIcon.Off)
        icon.addPixmap(checked, QIcon.Normal, QIcon.On)
        icon.addPixmap(checked, QIcon.Selected, QIcon.Off)
        icon.addPixmap(checked, QIcon.Selected, QIcon.On)
        return icon

    class StatusPill(QLabel):
        def __init__(self, text: str, state: str, parent: QWidget | None = None) -> None:
            super().__init__(text, parent)
            self.setObjectName("statusPill")
            self.setProperty("state", state)
            self.setAlignment(Qt.AlignCenter)
            self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

        def set_state(self, text: str, state: str) -> None:
            self.setText(text)
            self.setProperty("state", state)
            repolish(self)

    class BrandBanner(QWidget):
        """A polished masthead with an optional customer-provided SVG banner."""

        DEFAULT_HEIGHT = 82
        MIN_HEIGHT = 64
        MAX_HEIGHT = 160
        MAX_SVG_BYTES = 5 * 1024 * 1024

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._renderer: Any = None
            self._source_path = ""
            self._is_custom = False
            self._site_name = "本地安防管理"
            self._summary = "设备接入、险情处置与现场问询"
            self.setObjectName("brandBanner")
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setFixedHeight(self.DEFAULT_HEIGHT)

        @property
        def source_path(self) -> str:
            return self._source_path

        @property
        def is_custom(self) -> bool:
            return self._is_custom

        def set_context(self, site_name: str, summary: str) -> None:
            self._site_name = site_name.strip() or "本地安防管理"
            self._summary = summary.strip() or "设备接入、险情处置与现场问询"
            self.update()

        def show_default(self, *, height: int = DEFAULT_HEIGHT) -> None:
            self._renderer = None
            self._source_path = ""
            self._is_custom = False
            self.setFixedHeight(max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, int(height))))
            self.show()
            self.update()

        def load_svg(self, path: str, *, height: int = DEFAULT_HEIGHT) -> bool:
            if QSvgRenderer is None or not path.strip():
                self.show_default(height=height)
                return False
            source = Path(path).expanduser()
            try:
                if not source.is_file() or source.stat().st_size > self.MAX_SVG_BYTES:
                    self.show_default(height=height)
                    return False
                svg_text = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self.show_default(height=height)
                return False
            if not self._is_safe_svg(svg_text):
                self.show_default(height=height)
                return False
            renderer = QSvgRenderer(str(source), self)
            if not renderer.isValid():
                self.show_default(height=height)
                return False
            self._renderer = renderer
            self._source_path = str(source)
            self._is_custom = True
            self.setFixedHeight(max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, int(height))))
            self.show()
            self.update()
            return True

        @staticmethod
        def _is_safe_svg(svg_text: str) -> bool:
            lowered = svg_text.lower()
            if "<svg" not in lowered:
                return False
            blocked_markup = ("<script", "javascript:", "<!doctype", "<!entity", "<foreignobject")
            if any(marker in lowered for marker in blocked_markup):
                return False
            if re.search(r"\bon[a-z]+\s*=", lowered):
                return False
            if re.search(r"(?:href|xlink:href)\s*=\s*[\"']\s*(?!#|data:image/)", lowered):
                return False
            return not re.search(r"url\(\s*[\"']?\s*(?:https?:|file:|//)", lowered)

        def clear(self) -> None:
            self.show_default()

        def paintEvent(self, _event: Any) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            if self._renderer is not None:
                painter.fillRect(self.rect(), QColor("#15211D"))
                source_size = self._renderer.defaultSize()
                if source_size.width() > 0 and source_size.height() > 0:
                    scale = max(
                        self.width() / source_size.width(),
                        self.height() / source_size.height(),
                    )
                    target_width = source_size.width() * scale
                    target_height = source_size.height() * scale
                    target = QRectF(
                        (self.width() - target_width) / 2,
                        (self.height() - target_height) / 2,
                        target_width,
                        target_height,
                    )
                    self._renderer.render(painter, target)
                else:
                    self._renderer.render(painter, QRectF(self.rect()))
                return
            self._paint_default(painter)

        def _paint_default(self, painter: QPainter) -> None:
            background = QLinearGradient(0, 0, self.width(), self.height())
            background.setColorAt(0.0, QColor("#17261F"))
            background.setColorAt(0.58, QColor("#1B2D25"))
            background.setColorAt(1.0, QColor("#23392F"))
            painter.fillRect(self.rect(), background)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 9))
            painter.drawEllipse(QRectF(self.width() - 310, -170, 390, 390))
            painter.setBrush(QColor(176, 118, 77, 26))
            painter.drawEllipse(QRectF(self.width() - 510, 34, 260, 260))
            painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
            for x in range(self.width() - 360, self.width(), 42):
                painter.drawLine(x, 0, x - 100, self.height())

            mark = QRectF(26, (self.height() - 38) / 2, 38, 38)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#D7E8DF"))
            painter.drawRoundedRect(mark, 10, 10)
            painter.setPen(QPen(QColor("#1B3B31"), 2.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(mark.left() + 9, mark.top() + 13, 19, 13), 3, 3)
            painter.drawEllipse(QRectF(mark.left() + 15, mark.top() + 17, 7, 7))
            painter.drawLine(mark.left() + 10, mark.top() + 12, mark.left() + 14, mark.top() + 9)
            painter.drawLine(mark.left() + 24, mark.top() + 12, mark.left() + 28, mark.top() + 9)

            text_left = 78
            painter.setPen(QColor("#F7FAF8"))
            painter.setFont(QFont("Microsoft YaHei UI", 13, QFont.DemiBold))
            painter.drawText(
                QRectF(text_left, 12, 250, 27),
                Qt.AlignVCenter | Qt.AlignLeft,
                "FactoryGuard 智守",
            )
            painter.setPen(QColor("#AFC1B8"))
            painter.setFont(QFont("Microsoft YaHei UI", 9))
            painter.drawText(
                QRectF(text_left, 39, max(180, self.width() - text_left - 300), 23),
                Qt.AlignVCenter | Qt.AlignLeft,
                self._summary,
            )

            badge_width = 174
            badge = QRectF(
                self.width() - badge_width - 26,
                (self.height() - 34) / 2,
                badge_width,
                34,
            )
            painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
            painter.setBrush(QColor(255, 255, 255, 12))
            painter.drawRoundedRect(badge, 17, 17)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#76C7A4"))
            painter.drawEllipse(QRectF(badge.left() + 14, badge.center().y() - 3, 6, 6))
            painter.setPen(QColor("#E1ECE6"))
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            status = self._site_name
            if len(status) > 13:
                status = status[:12] + "…"
            painter.drawText(
                QRectF(badge.left() + 27, badge.top(), badge.width() - 37, badge.height()),
                Qt.AlignVCenter | Qt.AlignLeft,
                status,
            )

    class SeverityPill(QLabel):
        def __init__(self, incident: Incident, parent: QWidget | None = None) -> None:
            super().__init__(SEVERITY_LABELS[incident.severity], parent)
            self.setObjectName("severityPill")
            self.setProperty("severity", str(incident.severity))
            self.setAlignment(Qt.AlignCenter)
            self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    class SectionCard(QFrame):
        def __init__(
            self, parent: QWidget | None = None, *, object_name: str = "sectionCard"
        ) -> None:
            super().__init__(parent)
            self.setObjectName(object_name)
            self.body = QVBoxLayout(self)
            self.body.setContentsMargins(18, 17, 18, 17)
            self.body.setSpacing(11)

        def add_header(self, title: str, caption: str = "", action: QWidget | None = None) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            text_box = QVBoxLayout()
            text_box.setSpacing(2)
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            text_box.addWidget(heading)
            if caption:
                sub = QLabel(caption)
                sub.setObjectName("sectionCaption")
                sub.setWordWrap(True)
                text_box.addWidget(sub)
            row.addLayout(text_box, 1)
            if action is not None:
                row.addWidget(action, 0, Qt.AlignTop)
            self.body.addLayout(row)

    class MetricCard(QFrame):
        def __init__(
            self,
            label: str,
            glyph: str,
            tone: str = "teal",
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.setObjectName("metricCard")
            self.setMinimumHeight(118)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(17, 15, 17, 15)
            layout.setSpacing(4)
            top = QHBoxLayout()
            title = QLabel(label)
            title.setObjectName("metricLabel")
            icon = QLabel(glyph)
            icon.setObjectName("metricGlyph")
            icon.setProperty("tone", tone)
            icon.setAlignment(Qt.AlignCenter)
            icon.setFixedSize(38, 38)
            top.addWidget(title, 1)
            top.addWidget(icon)
            layout.addLayout(top)
            self.value_label = QLabel("—")
            self.value_label.setObjectName("metricValue")
            layout.addWidget(self.value_label)
            self.hint_label = QLabel("正在读取")
            self.hint_label.setObjectName("metricHint")
            layout.addWidget(self.hint_label)

        def set_value(self, value: str, hint: str = "") -> None:
            self.value_label.setText(value)
            self.hint_label.setText(hint)

    class CameraThumbnail(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._image = QImage()
            self._caption = "接入视频后在此显示"
            self._state = "unknown"
            self.setMinimumHeight(138)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        def set_image(self, image: QImage | None) -> None:
            self._image = image.copy() if image is not None and not image.isNull() else QImage()
            self.update()

        def set_caption(self, caption: str) -> None:
            self._caption = caption
            self.update()

        def set_status(self, state: str) -> None:
            self._state = state
            self.update()

        def paintEvent(self, _event: Any) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            if not self._image.isNull():
                scaled = self._image.scaled(
                    self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
                source_x = max(0, (scaled.width() - self.width()) // 2)
                source_y = max(0, (scaled.height() - self.height()) // 2)
                painter.drawImage(
                    self.rect(), scaled, QRectF(source_x, source_y, self.width(), self.height())
                )
                painter.fillRect(self.rect(), QColor(4, 12, 22, 42))
                return
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            if self._state == "offline":
                gradient.setColorAt(0.0, QColor("#2A2022"))
                gradient.setColorAt(0.6, QColor("#211D20"))
                gradient.setColorAt(1.0, QColor("#332225"))
                accent = QColor("#D98A8F")
            elif self._state == "online":
                gradient.setColorAt(0.0, QColor("#263244"))
                gradient.setColorAt(0.6, QColor("#1A2433"))
                gradient.setColorAt(1.0, QColor("#202C3D"))
                accent = QColor("#CBD5E1")
            else:
                gradient.setColorAt(0.0, QColor("#303845"))
                gradient.setColorAt(0.6, QColor("#252D38"))
                gradient.setColorAt(1.0, QColor("#323B47"))
                accent = QColor("#94A3B8")
            painter.setBrush(gradient)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bounds, 12, 12)
            painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
            step = 26
            for x in range(0, self.width(), step):
                painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), step):
                painter.drawLine(0, y, self.width(), y)
            icon_center_x = self.width() / 2
            icon_center_y = self.height() / 2 - 10
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(icon_center_x - 23, icon_center_y - 16, 46, 31), 7, 7)
            painter.drawEllipse(QRectF(icon_center_x - 8, icon_center_y - 7, 16, 16))
            painter.drawLine(
                int(icon_center_x - 25),
                int(icon_center_y - 3),
                int(icon_center_x - 32),
                int(icon_center_y - 11),
            )
            painter.setPen(QColor("#9CA8B7"))
            font = QFont("Microsoft YaHei UI", 9)
            painter.setFont(font)
            painter.drawText(
                QRectF(8, self.height() - 37, self.width() - 16, 22),
                Qt.AlignCenter,
                self._caption,
            )

    class CameraCard(QFrame):
        open_requested = Signal(str)
        manage_requested = Signal(str)

        def __init__(
            self, camera: CameraView, parent: QWidget | None = None, *, compact: bool = False
        ) -> None:
            super().__init__(parent)
            self.camera_view = camera
            self.setObjectName("cameraCard")
            self.setMinimumWidth(190 if compact else 230)
            self.setMinimumHeight(172 if compact else 255)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(
                12 if compact else 13,
                11 if compact else 13,
                12 if compact else 13,
                11 if compact else 13,
            )
            layout.setSpacing(6 if compact else 8)
            head = QHBoxLayout()
            text = QVBoxLayout()
            text.setSpacing(1)
            name = QLabel(camera.camera.name)
            name.setObjectName("cameraName")
            meta = QLabel(f"{camera.site.name} · {camera.area.name}")
            meta.setObjectName("cameraMeta")
            checked = QLabel(camera.health_checked_text)
            checked.setObjectName("cameraMeta")
            text.addWidget(name)
            text.addWidget(meta)
            text.addWidget(checked)
            subscription_warning = getattr(camera, "subscription_label", "")
            if subscription_warning:
                warning = QLabel(subscription_warning)
                warning.setObjectName("cardWarning")
                warning.setWordWrap(True)
                text.addWidget(warning)
            state = "online" if camera.is_online else "offline" if camera.is_offline else "unknown"
            status_pill = StatusPill(camera.status_label, state)
            tooltip_parts = [
                value
                for value in (
                    camera.health_checked_text,
                    camera.health_detail,
                    camera.subscription_detail,
                )
                if value
            ]
            if tooltip_parts:
                status_pill.setToolTip(" · ".join(tooltip_parts))
            head.addLayout(text, 1)
            head.addWidget(status_pill, 0, Qt.AlignTop)
            layout.addLayout(head)
            self.thumbnail = CameraThumbnail()
            self.thumbnail.set_status(state)
            if compact:
                self.thumbnail.setMinimumHeight(78)
                self.thumbnail.setMaximumHeight(88)
            self.thumbnail.set_caption(
                "摄像头已暂停接入，可在管理中恢复"
                if camera.is_disabled
                else "设备离线，请检查网络或供电"
                if camera.is_offline
                else "点击查看实时画面"
                if camera.best_stream
                else "等待设备提供视频流"
            )
            layout.addWidget(self.thumbnail, 1)
            footer = QHBoxLayout()
            protocol = QLabel(f"{camera.protocol_label} · {len(camera.streams)} 路码流")
            protocol.setObjectName("cameraMeta")
            play = QPushButton("查看" if compact else "查看画面")
            play.setObjectName("toolButton")
            play.setEnabled(
                not camera.is_disabled
                and (
                    camera.best_stream is not None
                    or camera.protocol in {"onvif", "gb28181", "rtsp"}
                )
            )
            play.clicked.connect(lambda: self.open_requested.emit(camera.camera.id))
            footer.addWidget(protocol, 1)
            if not compact:
                manage = QPushButton("管理")
                manage.setObjectName("linkButton")
                manage.setMaximumWidth(52)
                manage.setToolTip("编辑或删除摄像头")
                manage.clicked.connect(lambda: self.manage_requested.emit(camera.camera.id))
                footer.addWidget(manage)
            footer.addWidget(play)
            layout.addLayout(footer)
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("双击查看实时画面")

        def mouseDoubleClickEvent(self, event: Any) -> None:
            if event.button() == Qt.LeftButton:
                self.open_requested.emit(self.camera_view.camera.id)
            super().mouseDoubleClickEvent(event)

    class IncidentRow(QFrame):
        open_requested = Signal(str)

        def __init__(
            self,
            incident: Incident,
            *,
            area_name: str,
            camera_name: str,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.incident_id = incident.id
            self.setObjectName("incidentRow")
            self.setCursor(Qt.PointingHandCursor)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(13, 11, 13, 11)
            layout.setSpacing(11)
            marker = QFrame()
            marker.setFixedWidth(4)
            marker.setMinimumHeight(42)
            color = {
                "critical": "#D73A42",
                "high": "#F08A47",
                "medium": "#E5B43B",
                "low": "#5A9FC7",
            }.get(str(incident.severity), "#B8C2D0")
            marker.setStyleSheet(f"background: {color}; border-radius: 2px;")
            layout.addWidget(marker)
            content = QVBoxLayout()
            content.setSpacing(3)
            title_row = QHBoxLayout()
            title = QLabel(incident.title)
            title.setObjectName("incidentTitle")
            title.setWordWrap(True)
            title_row.addWidget(title, 1)
            title_row.addWidget(SeverityPill(incident))
            content.addLayout(title_row)
            location = " · ".join(
                value for value in (area_name, camera_name) if value and value != "未指定摄像头"
            )
            detail = QLabel(
                f"{location or '未指定位置'}   ·   {incident.occurred_at.astimezone().strftime('%m-%d %H:%M')}"
                f"   ·   {STATUS_LABELS[incident.status]}"
            )
            detail.setObjectName("incidentMeta")
            detail.setWordWrap(True)
            self.detail = detail
            content.addWidget(detail)
            layout.addLayout(content, 1)
            arrow = QLabel("›")
            arrow.setStyleSheet("font-size: 22px; color: #9AA7B9;")
            layout.addWidget(arrow)

        def set_selected(self, selected: bool) -> None:
            self.setProperty("selected", "true" if selected else "false")
            repolish(self)

        def mousePressEvent(self, event: Any) -> None:
            if event.button() == Qt.LeftButton:
                self.open_requested.emit(self.incident_id)
            super().mousePressEvent(event)

    class AreaCard(QFrame):
        manage_requested = Signal(str)

        def __init__(
            self,
            *,
            area_id: str,
            name: str,
            site_name: str,
            camera_count: int,
            online_count: int,
            unknown_count: int,
            disabled_count: int,
            open_incidents: int,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.setObjectName("areaCard")
            self.setMinimumHeight(130)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(7)
            head = QHBoxLayout()
            title = QLabel(name)
            title.setObjectName("sectionTitle")
            head.addWidget(title, 1)
            manage = QPushButton("管理")
            manage.setObjectName("linkButton")
            manage.setMaximumWidth(52)
            manage.setToolTip("编辑或删除区域")
            manage.clicked.connect(lambda: self.manage_requested.emit(area_id))
            head.addWidget(manage)
            active_count = camera_count - disabled_count
            if camera_count == 0:
                state, status_text = "unknown", "未接入"
            elif active_count == 0:
                state, status_text = "unknown", "已停用"
            elif online_count == active_count:
                state, status_text = "online", "正常"
            elif online_count == 0 and unknown_count == active_count:
                state, status_text = "unknown", "待检测"
            elif unknown_count:
                state, status_text = "unknown", "部分待检测"
            elif online_count == 0:
                state, status_text = "offline", "全部离线"
            else:
                state, status_text = "unknown", "部分离线"
            head.addWidget(StatusPill(status_text, state))
            layout.addLayout(head)
            site = QLabel(site_name)
            site.setObjectName("cardMuted")
            layout.addWidget(site)
            layout.addStretch(1)
            metrics = QHBoxLayout()
            metrics.addWidget(_mini_metric("摄像头", str(camera_count)))
            metrics.addWidget(_mini_metric("在线", str(online_count)))
            metrics.addWidget(_mini_metric("未关闭险情", str(open_incidents)))
            layout.addLayout(metrics)

    def _mini_metric(label: str, value: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 17px; font-weight: 700; color: #1B283D;")
        caption = QLabel(label)
        caption.setObjectName("smallLabel")
        layout.addWidget(value_label)
        layout.addWidget(caption)
        return widget

    class ChatBubble(QFrame):
        def __init__(
            self, text: str, *, role: str, provider: str = "", parent: QWidget | None = None
        ) -> None:
            super().__init__(parent)
            self.setObjectName("chatUser" if role == "user" else "chatAssistant")
            self.setMinimumWidth(360 if role == "assistant" else 220)
            self.setMaximumWidth(680)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 11, 14, 11)
            layout.setSpacing(5)
            body = QLabel(text)
            body.setObjectName("chatTextOnColor" if role == "user" else "chatText")
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(body)
            if role == "assistant" and provider:
                source = QLabel("文字服务" if provider == "llm" else "本地规则")
                source.setObjectName("smallLabel")
                layout.addWidget(source)

    class EmptyState(QFrame):
        def __init__(
            self,
            *,
            title: str,
            text: str,
            action_text: str = "",
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.setObjectName("emptyState")
            self.setMinimumHeight(180)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(8)
            layout.addStretch(1)
            glyph = QLabel("＋")
            glyph.setAlignment(Qt.AlignCenter)
            glyph.setStyleSheet("font-size: 26px; color: #11A99A; font-weight: 600;")
            heading = QLabel(title)
            heading.setAlignment(Qt.AlignCenter)
            heading.setStyleSheet("font-size: 15px; font-weight: 700; color: #233147;")
            hint = QLabel(text)
            hint.setAlignment(Qt.AlignCenter)
            hint.setWordWrap(True)
            hint.setObjectName("cardMuted")
            layout.addWidget(glyph)
            layout.addWidget(heading)
            layout.addWidget(hint)
            self.action_button: QPushButton | None = None
            if action_text:
                self.action_button = QPushButton(action_text)
                self.action_button.setObjectName("primary")
                self.action_button.setMaximumWidth(180)
                layout.addWidget(self.action_button, 0, Qt.AlignHCenter)
            layout.addStretch(1)

    class InfoRow(QWidget):
        def __init__(self, label: str, value: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            key = QLabel(label)
            key.setObjectName("smallLabel")
            val = QLabel(value)
            val.setStyleSheet("color: #26364E; font-weight: 600;")
            val.setWordWrap(True)
            layout.addWidget(key, 0, Qt.AlignTop)
            layout.addStretch(1)
            layout.addWidget(val, 1, Qt.AlignRight | Qt.AlignTop)

else:

    class StatusPill:  # pragma: no cover
        pass

    class SectionCard:  # pragma: no cover
        pass

    class AreaCard:  # pragma: no cover
        pass

    class BrandBanner:  # pragma: no cover
        pass

    class CameraCard:  # pragma: no cover
        pass

    class CameraThumbnail:  # pragma: no cover
        pass

    class ChatBubble:  # pragma: no cover
        pass

    class EmptyState:  # pragma: no cover
        pass

    class IncidentRow:  # pragma: no cover
        pass

    class MetricCard:  # pragma: no cover
        pass

    class SeverityPill:  # pragma: no cover
        pass

    class InfoRow:  # pragma: no cover
        pass

    def clear_layout(_layout: object) -> None:
        return None

    def make_line() -> object:
        return None

    def repolish(_widget: object) -> None:
        return None

    def make_nav_icon(_kind: str, *, size: int = 18) -> object:
        return None


__all__ = [
    "AreaCard",
    "BrandBanner",
    "CameraCard",
    "CameraThumbnail",
    "ChatBubble",
    "EmptyState",
    "InfoRow",
    "MetricCard",
    "SectionCard",
    "SeverityPill",
    "StatusPill",
    "clear_layout",
    "make_line",
    "make_nav_icon",
    "repolish",
]
