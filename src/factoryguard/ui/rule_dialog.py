from __future__ import annotations

"""Rule editing dialog shared by the desktop topology page."""

from typing import Any

from factoryguard.domain.models import Rule, RuleType
from factoryguard.domain.topology import EntityPath

from .qt import HAS_QT, require_qt

if HAS_QT:
    from .qt import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QSpinBox,
        QVBoxLayout,
    )


def parse_geometry(text: str) -> tuple[tuple[float, float], ...]:
    """Parse ``x,y; x,y`` or newline-separated points for a Rule."""
    points: list[tuple[float, float]] = []
    chunks = text.replace("\n", ";").split(";")
    for chunk in chunks:
        value = chunk.strip()
        if not value:
            continue
        coordinates = [part.strip() for part in value.split(",")]
        if len(coordinates) != 2:
            raise ValueError(f"几何点格式错误：{value}，应为 x,y")
        try:
            points.append((float(coordinates[0]), float(coordinates[1])))
        except ValueError as exc:
            raise ValueError(f"几何点不是数字：{value}") from exc
    if not points:
        raise ValueError("至少需要一个几何点")
    return tuple(points)


def format_geometry(geometry: tuple[tuple[float, float], ...]) -> str:
    return "; ".join(f"{x:g},{y:g}" for x, y in geometry)


if HAS_QT:

    class RuleEditorDialog(QDialog):
        """Edit a polygon or tripwire rule with domain-level validation."""

        def __init__(self, *, path: EntityPath, rule: Rule | None = None, parent: Any = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("编辑规则" if rule is not None else "新增规则")
            self.setModal(True)
            self.resize(520, 560)
            self._path = path
            self._source_rule = rule
            self.rule: Rule | None = None

            layout = QVBoxLayout(self)
            form = QFormLayout()
            self.name_edit = QLineEdit(rule.name if rule else "全画面入侵")
            self.type_combo = QComboBox()
            self.type_combo.addItem("多边形", RuleType.POLYGON.value)
            self.type_combo.addItem("绊线", RuleType.LINE.value)
            if rule is not None:
                self.type_combo.setCurrentIndex(self.type_combo.findData(rule.type.value))
            self.geometry_edit = QPlainTextEdit(
                format_geometry(rule.geometry)
                if rule is not None
                else format_geometry(((0, 0), (path.stream.width, 0), (path.stream.width, path.stream.height), (0, path.stream.height)))
            )
            self.geometry_edit.setPlaceholderText("多边形：0,0; 1280,0; 1280,720; 0,720\n绊线：100,200; 900,200")
            self.geometry_edit.setMinimumHeight(90)
            self.labels_edit = QLineEdit(
                ", ".join(sorted(rule.target_labels)) if rule is not None else "person"
            )
            self.confidence_spin = QDoubleSpinBox()
            self.confidence_spin.setRange(0.01, 1.0)
            self.confidence_spin.setSingleStep(0.05)
            self.confidence_spin.setValue(rule.min_confidence if rule else 0.45)
            self.box_height_spin = QDoubleSpinBox()
            self.box_height_spin.setRange(0.0, 10000.0)
            self.box_height_spin.setValue(rule.min_box_height if rule else 32.0)
            self.confirm_spin = QSpinBox()
            self.confirm_spin.setRange(1, 30)
            self.confirm_spin.setValue(rule.confirm_frames if rule else 3)
            self.cooldown_spin = QDoubleSpinBox()
            self.cooldown_spin.setRange(0.0, 86400.0)
            self.cooldown_spin.setValue(rule.cooldown_seconds if rule else 60.0)
            self.direction_combo = QComboBox()
            for label, value in (("双向", "both"), ("向上", "up"), ("向下", "down"), ("向左", "left"), ("向右", "right")):
                self.direction_combo.addItem(label, value)
            if rule is not None:
                self.direction_combo.setCurrentIndex(self.direction_combo.findData(rule.direction))
            self.enabled_check = QCheckBox("规则启用")
            self.enabled_check.setChecked(rule.enabled if rule is not None else True)

            form.addRow("规则名称", self.name_edit)
            form.addRow("规则类型", self.type_combo)
            form.addRow("几何坐标", self.geometry_edit)
            form.addRow("目标类别", self.labels_edit)
            form.addRow("最低置信度", self.confidence_spin)
            form.addRow("最小框高", self.box_height_spin)
            form.addRow("确认帧数", self.confirm_spin)
            form.addRow("冷却秒数", self.cooldown_spin)
            form.addRow("绊线方向", self.direction_combo)
            form.addRow("状态", self.enabled_check)
            layout.addLayout(form)
            hint = QLabel("坐标以原始视频分辨率为准；保存前会校验多边形自交、点数和参数范围。")
            hint.setWordWrap(True)
            layout.addWidget(hint)

            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
            buttons.accepted.connect(self._validate_and_accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _validate_and_accept(self) -> None:
            try:
                name = self.name_edit.text().strip()
                if not name:
                    raise ValueError("规则名称不能为空")
                labels = frozenset(item.strip() for item in self.labels_edit.text().split(",") if item.strip())
                if not labels:
                    raise ValueError("至少需要一个目标类别")
                rule_type = RuleType(str(self.type_combo.currentData()))
                direction = str(self.direction_combo.currentData())
                source_id = self._source_rule.id if self._source_rule is not None else "rule-new"
                self.rule = Rule(
                    id=source_id,
                    stream_id=self._path.stream.id,
                    site_id=self._path.site.id,
                    area_id=self._path.area.id,
                    camera_id=self._path.camera.id,
                    name=name,
                    type=rule_type,
                    geometry=parse_geometry(self.geometry_edit.toPlainText()),
                    target_labels=labels,
                    min_confidence=self.confidence_spin.value(),
                    min_box_height=self.box_height_spin.value(),
                    confirm_frames=self.confirm_spin.value(),
                    cooldown_seconds=self.cooldown_spin.value(),
                    direction=direction,
                    enabled=self.enabled_check.isChecked(),
                    version=(self._source_rule.version + 1 if self._source_rule is not None else 1),
                )
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "规则校验失败", str(exc))
                return
            self.accept()

else:

    class RuleEditorDialog:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = ["RuleEditorDialog", "format_geometry", "parse_geometry"]
