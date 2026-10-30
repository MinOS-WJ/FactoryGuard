"""Structured rule editor with direct geometry drawing and condition controls."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from factoryguard.domain.condition_tree import condition_tree_summary
from factoryguard.domain.models import Rule, RuleType
from factoryguard.domain.rule_simulation import simulate_rule
from factoryguard.domain.topology import EntityPath
from factoryguard.notifications.destinations import NotificationDestination

from .geometry_editor import GeometryCanvas
from .qt import HAS_QT, require_qt

if HAS_QT:
    from .qt import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QImage,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QSplitter,
        Qt,
        QVBoxLayout,
        QWidget,
    )


CONDITION_SPECS: dict[str, tuple[str, RuleType]] = {
    "intrusion": ("区域入侵", RuleType.POLYGON),
    "line_cross": ("绊线穿越", RuleType.LINE),
    "line_count": ("过线计数", RuleType.LINE),
    "occupancy": ("区域人数", RuleType.POLYGON),
    "dwell": ("区域滞留", RuleType.POLYGON),
}

ACTION_SPECS: tuple[tuple[str, str], ...] = (
    ("snapshot", "保存截图"),
    ("clip", "保存事件短视频"),
    ("notify", "发送通知"),
    ("audit", "写入审计"),
    ("http", "调用外部 HTTP"),
)

ESCALATION_LEVELS: tuple[tuple[str, str], ...] = (
    ("不自动升级", ""),
    ("低", "low"),
    ("中", "medium"),
    ("高", "high"),
    ("紧急", "critical"),
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
        """Edit a structured polygon or tripwire rule without exposing JSON."""

        def __init__(
            self,
            *,
            path: EntityPath,
            rule: Rule | None = None,
            reference_image: QImage | None = None,
            destinations: tuple[NotificationDestination, ...] = (),
            allow_publish: bool = True,
            parent: Any = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("编辑规则" if rule is not None else "新增规则")
            self.setModal(True)
            self.setMinimumSize(880, 640)
            self.resize(1020, 760)
            self._path = path
            self._source_rule = rule
            self._source_condition_nodes = rule.condition_nodes if rule is not None else ()
            self._destinations = tuple(destinations)
            self.rule: Rule | None = None

            condition = rule.condition if rule is not None and rule.condition else "intrusion"
            if condition not in CONDITION_SPECS:
                condition = "intrusion"
            initial_geometry = (
                rule.geometry
                if rule is not None and rule.geometry
                else self._default_geometry(CONDITION_SPECS[condition][1])
            )

            outer = QVBoxLayout(self)
            outer.setContentsMargins(12, 12, 12, 12)
            splitter = QSplitter(Qt.Horizontal)
            splitter.setChildrenCollapsible(False)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            form_host = QWidget()
            form_host_layout = QVBoxLayout(form_host)
            form_host_layout.setContentsMargins(4, 4, 12, 4)

            def add_form_group(title: str) -> QFormLayout:
                group = QGroupBox(title)
                form = QFormLayout(group)
                form_host_layout.addWidget(group)
                return form

            basic_form = add_form_group("基本信息")
            self.name_edit = QLineEdit(rule.name if rule is not None else "全画面入侵")
            self.condition_combo = QComboBox()
            for key, (label, _rule_type) in CONDITION_SPECS.items():
                self.condition_combo.addItem(label, key)
            self.condition_combo.setCurrentIndex(max(0, self.condition_combo.findData(condition)))
            self.labels_edit = QLineEdit(
                ", ".join(sorted(rule.target_labels)) if rule is not None else "person"
            )
            self.labels_edit.setPlaceholderText("多个类别用逗号分隔，例如 person, forklift")
            self.confidence_spin = QDoubleSpinBox()
            self.confidence_spin.setRange(0.01, 1.0)
            self.confidence_spin.setSingleStep(0.05)
            self.confidence_spin.setDecimals(2)
            self.confidence_spin.setValue(rule.min_confidence if rule is not None else 0.45)
            self.box_height_spin = QDoubleSpinBox()
            self.box_height_spin.setRange(0.0, 10000.0)
            self.box_height_spin.setValue(rule.min_box_height if rule is not None else 32.0)
            basic_form.addRow("规则名称", self.name_edit)
            basic_form.addRow("判定条件", self.condition_combo)
            basic_form.addRow("目标类别", self.labels_edit)
            basic_form.addRow("最低置信度", self.confidence_spin)
            basic_form.addRow("最小目标框高", self.box_height_spin)

            behavior_form = add_form_group("判定参数")
            self.direction_combo = QComboBox()
            for label, value in (("双向", "both"), ("向上", "up"), ("向下", "down"), ("向左", "left"), ("向右", "right")):
                self.direction_combo.addItem(label, value)
            self.direction_combo.setCurrentIndex(
                max(0, self.direction_combo.findData(rule.direction if rule is not None else "both"))
            )
            self.threshold_spin = QSpinBox()
            self.threshold_spin.setRange(1, 9999)
            self.threshold_spin.setValue(
                rule.count_threshold if rule is not None and rule.count_threshold > 0 else 10
            )
            self.window_spin = QDoubleSpinBox()
            self.window_spin.setRange(1.0, 86400.0)
            self.window_spin.setValue(
                rule.window_seconds if rule is not None and rule.window_seconds > 0 else 300.0
            )
            self.dwell_spin = QDoubleSpinBox()
            self.dwell_spin.setRange(1.0, 86400.0)
            self.dwell_spin.setValue(
                rule.dwell_seconds if rule is not None and rule.dwell_seconds > 0 else 10.0
            )
            self.confirm_spin = QSpinBox()
            self.confirm_spin.setRange(1, 30)
            self.confirm_spin.setValue(rule.confirm_frames if rule is not None else 3)
            self.cooldown_spin = QDoubleSpinBox()
            self.cooldown_spin.setRange(0.0, 86400.0)
            self.cooldown_spin.setValue(rule.cooldown_seconds if rule is not None else 60.0)
            behavior_form.addRow("绊线方向", self.direction_combo)
            behavior_form.addRow("数量阈值", self.threshold_spin)
            behavior_form.addRow("统计窗口（秒）", self.window_spin)
            behavior_form.addRow("滞留时长（秒）", self.dwell_spin)
            behavior_form.addRow("确认帧数", self.confirm_spin)
            behavior_form.addRow("冷却秒数", self.cooldown_spin)

            actions_form = add_form_group("动作与处置")
            source_actions = set(rule.actions if rule is not None else ("event", "snapshot", "clip", "notify"))
            self.event_action_check = QCheckBox("生成事件（必选）")
            self.event_action_check.setChecked(True)
            self.event_action_check.setEnabled(False)
            actions_form.addRow(self.event_action_check)
            self.action_checks: dict[str, QCheckBox] = {}
            for key, label in ACTION_SPECS:
                check = QCheckBox(label)
                check.setChecked(key in source_actions)
                self.action_checks[key] = check
                actions_form.addRow(check)
            self.http_action_url_edit = QLineEdit(rule.http_action_url if rule is not None else "")
            self.http_action_url_edit.setPlaceholderText("https://example.test/factoryguard/event")
            self.http_action_timeout_spin = QDoubleSpinBox()
            self.http_action_timeout_spin.setRange(0.1, 120.0)
            self.http_action_timeout_spin.setValue(rule.http_action_timeout if rule is not None else 5.0)
            actions_form.addRow("HTTP 地址", self.http_action_url_edit)
            actions_form.addRow("HTTP 超时（秒）", self.http_action_timeout_spin)
            self.ack_deadline_spin = QDoubleSpinBox()
            self.ack_deadline_spin.setRange(0.0, 86400.0)
            self.ack_deadline_spin.setValue(rule.ack_deadline_seconds if rule is not None else 0.0)
            self.escalation_level_combo = QComboBox()
            for label, value in ESCALATION_LEVELS:
                self.escalation_level_combo.addItem(label, value)
            self.escalation_level_combo.setCurrentIndex(
                max(0, self.escalation_level_combo.findData(rule.escalation_level if rule is not None else ""))
            )
            self.assignees_edit = QLineEdit(", ".join(rule.assignees) if rule is not None else "")
            self.assignees_edit.setPlaceholderText("值班人员，多个姓名用逗号分隔")
            self.destination_list = QListWidget()
            self.destination_list.setMaximumHeight(120)
            saved_destination_ids = set(rule.destination_ids if rule is not None else ())
            for destination in self._destinations:
                label = f"{destination.name} · {destination.kind}"
                if not destination.enabled:
                    label += " (\u5df2\u505c\u7528)"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, destination.id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                if not destination.enabled:
                    item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if destination.id in saved_destination_ids and destination.enabled else Qt.Unchecked)
                self.destination_list.addItem(item)
            actions_form.addRow("\u901a\u77e5\u5b9e\u4f8b", self.destination_list)
            destination_hint = QLabel("不勾选任何实例时使用全局通知通道；已停用实例不可选择。")
            destination_hint.setWordWrap(True)
            destination_hint.setObjectName("muted")
            actions_form.addRow("", destination_hint)
            self.enabled_check = QCheckBox("规则启用")
            self.enabled_check.setChecked(rule.enabled if rule is not None else True)
            actions_form.addRow("运行状态", self.enabled_check)
            self.publication_combo = QComboBox()
            self.publication_combo.addItem("草稿（不参与运行）", "draft")
            if allow_publish:
                self.publication_combo.addItem("立即发布", "published")
            default_publication = (
                "published"
                if allow_publish and rule is not None and rule.publication_status == "published"
                else "draft"
            )
            self.publication_combo.setCurrentIndex(max(0, self.publication_combo.findData(default_publication)))
            actions_form.addRow("保存状态", self.publication_combo)
            publication_hint = QLabel("草稿不会参与实时告警；提交审核后，需要两名不同审核人通过才会发布。")
            publication_hint.setWordWrap(True)
            publication_hint.setObjectName("muted")
            actions_form.addRow(publication_hint)

            self.clear_condition_tree_check = QCheckBox("清除高级条件树，改用上方单一条件")
            if self._source_condition_nodes:
                tree_group = QGroupBox("高级条件树")
                tree_layout = QVBoxLayout(tree_group)
                tree_label = QLabel(condition_tree_summary(self._source_condition_nodes))
                tree_label.setWordWrap(True)
                tree_label.setObjectName("muted")
                tree_layout.addWidget(tree_label)
                tree_layout.addWidget(self.clear_condition_tree_check)
                form_host_layout.addWidget(tree_group)
            else:
                self.clear_condition_tree_check.setVisible(False)
            form_host_layout.addStretch(1)
            scroll.setWidget(form_host)
            splitter.addWidget(scroll)

            canvas_group = QGroupBox("画面标定")
            canvas_layout = QVBoxLayout(canvas_group)
            self.canvas = GeometryCanvas(
                width=self._path.stream.width,
                height=self._path.stream.height,
            )
            self.canvas.set_reference_image(reference_image)
            canvas_layout.addWidget(self.canvas, 1)
            canvas_buttons = QHBoxLayout()
            self.undo_geometry_button = QPushButton("撤销上一点")
            self.clear_geometry_button = QPushButton("清空绘制")
            self.apply_geometry_button = QPushButton("应用坐标")
            canvas_buttons.addWidget(self.undo_geometry_button)
            canvas_buttons.addWidget(self.clear_geometry_button)
            canvas_buttons.addWidget(self.apply_geometry_button)
            canvas_buttons.addStretch(1)
            canvas_layout.addLayout(canvas_buttons)
            self.geometry_edit = QPlainTextEdit()
            self.geometry_edit.setMinimumHeight(76)
            self.geometry_edit.setMaximumHeight(110)
            self.geometry_edit.setPlaceholderText("例如：100,200; 900,200")
            canvas_layout.addWidget(self.geometry_edit)
            self.geometry_hint = QLabel()
            self.geometry_hint.setWordWrap(True)
            self.geometry_hint.setObjectName("muted")
            canvas_layout.addWidget(self.geometry_hint)
            splitter.addWidget(canvas_group)
            splitter.setSizes([430, 570])
            outer.addWidget(splitter, 1)

            self.summary_label = QLabel()
            self.summary_label.setWordWrap(True)
            self.summary_label.setObjectName("muted")
            outer.addWidget(self.summary_label)
            simulation_row = QHBoxLayout()
            self.run_simulation_button = QPushButton("规则自检")
            self.simulation_label = QLabel("保存前可运行一次合成场景自检，检查几何、方向、阈值和确认帧是否可达。")
            self.simulation_label.setWordWrap(True)
            self.simulation_label.setObjectName("muted")
            simulation_row.addWidget(self.run_simulation_button)
            simulation_row.addWidget(self.simulation_label, 1)
            outer.addLayout(simulation_row)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
            buttons.accepted.connect(self._validate_and_accept)
            buttons.rejected.connect(self.reject)
            outer.addWidget(buttons)

            self.condition_combo.currentIndexChanged.connect(self._on_condition_changed)
            self.canvas.geometry_changed.connect(self._on_canvas_geometry_changed)
            self.undo_geometry_button.clicked.connect(self.canvas.undo)
            self.clear_geometry_button.clicked.connect(self.canvas.clear)
            self.apply_geometry_button.clicked.connect(self._apply_geometry_text)
            self.run_simulation_button.clicked.connect(self._run_simulation)
            for check in self.action_checks.values():
                check.toggled.connect(self._sync_action_controls)
            for signal_source in (
                self.confidence_spin,
                self.threshold_spin,
                self.window_spin,
                self.dwell_spin,
                self.confirm_spin,
                self.cooldown_spin,
                self.ack_deadline_spin,
            ):
                signal_source.valueChanged.connect(lambda *_args: self._refresh_summary())
            for text_source in (self.name_edit, self.labels_edit, self.assignees_edit):
                text_source.textChanged.connect(lambda *_args: self._refresh_summary())

            self.canvas.set_mode("line" if self._rule_type_for_condition(condition) is RuleType.LINE else "polygon")
            self.enabled_check.toggled.connect(lambda *_args: self._refresh_summary())
            self.canvas.set_geometry(initial_geometry)
            self._set_geometry_text(initial_geometry)
            self._on_condition_changed()
            self._sync_action_controls()

        def _default_geometry(self, rule_type: RuleType) -> tuple[tuple[float, float], ...]:
            width = max(1, int(self._path.stream.width))
            height = max(1, int(self._path.stream.height))
            if rule_type is RuleType.LINE:
                return ((0, height / 2), (width, height / 2))
            return ((0, 0), (width, 0), (width, height), (0, height))

        def _rule_type_for_condition(self, condition: str) -> RuleType:
            return CONDITION_SPECS.get(condition, ("区域入侵", RuleType.POLYGON))[1]

        def _current_condition(self) -> str:
            return str(self.condition_combo.currentData() or "intrusion")

        def _set_geometry_text(self, points: tuple[tuple[float, float], ...]) -> None:
            self.geometry_edit.blockSignals(True)
            self.geometry_edit.setPlainText(format_geometry(points))
            self.geometry_edit.blockSignals(False)

        def _on_canvas_geometry_changed(self, points: object) -> None:
            normalized = tuple((float(x), float(y)) for x, y in points)
            self._set_geometry_text(normalized)
            self._refresh_summary()

        def _apply_geometry_text(self) -> None:
            try:
                points = parse_geometry(self.geometry_edit.toPlainText())
                mode = self._rule_type_for_condition(self._current_condition())
                if mode is RuleType.LINE and len(points) != 2:
                    raise ValueError("绊线必须正好包含两个点")
                if mode is RuleType.POLYGON and len(points) < 3:
                    raise ValueError("多边形至少需要三个点")
                self.canvas.set_mode("line" if mode is RuleType.LINE else "polygon")
                self.canvas.set_geometry(points)
            except ValueError as exc:
                QMessageBox.warning(self, "坐标校验失败", str(exc))

        def _on_condition_changed(self) -> None:
            condition = self._current_condition()
            rule_type = self._rule_type_for_condition(condition)
            points = self.canvas.geometry
            valid = len(points) == 2 if rule_type is RuleType.LINE else len(points) >= 3
            if not valid:
                points = self._default_geometry(rule_type)
            self.canvas.set_mode("line" if rule_type is RuleType.LINE else "polygon")
            self.canvas.set_geometry(points)
            self._sync_condition_controls()
            self._refresh_summary()

        def _sync_condition_controls(self) -> None:
            condition = self._current_condition()
            rule_type = self._rule_type_for_condition(condition)
            self.direction_combo.setEnabled(rule_type is RuleType.LINE)
            self.threshold_spin.setEnabled(condition in {"line_count", "occupancy"})
            self.window_spin.setEnabled(condition == "line_count")
            self.dwell_spin.setEnabled(condition == "dwell")
            if rule_type is RuleType.LINE:
                self.geometry_hint.setText("在画面中依次点击两个点绘制绊线；也可直接编辑下方原始视频坐标。")
            else:
                self.geometry_hint.setText("在画面中依次点击至少三个点绘制区域；右键撤销，也可直接编辑原始视频坐标。")
            self._refresh_summary()

        def _sync_action_controls(self) -> None:
            enabled = self.action_checks["http"].isChecked()
            self.http_action_url_edit.setEnabled(enabled)
            self.http_action_timeout_spin.setEnabled(enabled)
            self._refresh_summary()

        def _refresh_summary(self) -> None:
            if not hasattr(self, "summary_label"):
                return
            condition = self._current_condition()
            label = CONDITION_SPECS.get(condition, ("区域入侵", RuleType.POLYGON))[0]
            if condition == "line_count":
                detail = f"{self.window_spin.value():g} 秒内过线数量达到 {self.threshold_spin.value()}"
            elif condition == "occupancy":
                detail = f"区域内人数达到 {self.threshold_spin.value()}"
            elif condition == "dwell":
                detail = f"目标在区域内滞留达到 {self.dwell_spin.value():g} 秒"
            else:
                detail = "目标进入标定区域" if condition == "intrusion" else "目标穿越标定绊线"
            actions = ["生成事件"]
            actions.extend(label_text for key, label_text in ACTION_SPECS if self.action_checks[key].isChecked())
            target = self.labels_edit.text().strip() or "目标"
            state = "启用" if self.enabled_check.isChecked() else "停用"
            self.summary_label.setText(
                f"规则摘要：{state}；检测到 {target} 后，{detail}；动作：{'、'.join(actions)}。"
            )

        def _draft_rule(self) -> Rule:
            name = self.name_edit.text().strip()
            if not name:
                raise ValueError("规则名称不能为空")
            labels = frozenset(item.strip() for item in self.labels_edit.text().split(",") if item.strip())
            if not labels:
                raise ValueError("至少需要一个目标类别")
            condition = self._current_condition()
            rule_type = self._rule_type_for_condition(condition)
            geometry = parse_geometry(self.geometry_edit.toPlainText())
            if rule_type is RuleType.LINE and len(geometry) != 2:
                raise ValueError("绊线必须正好包含两个点")
            if rule_type is RuleType.POLYGON and len(geometry) < 3:
                raise ValueError("多边形至少需要三个点")
            actions = ["event"] + [key for key, _label in ACTION_SPECS if self.action_checks[key].isChecked()]
            if "http" in actions and not self.http_action_url_edit.text().strip():
                raise ValueError("启用外部 HTTP 动作后必须填写 HTTP 地址")
            assignees = tuple(
                item.strip() for item in self.assignees_edit.text().split(",") if item.strip()
            )
            destination_ids = tuple(
                str(self.destination_list.item(row).data(Qt.UserRole))
                for row in range(self.destination_list.count())
                if self.destination_list.item(row).checkState() == Qt.Checked
            )
            condition_nodes = (
                ()
                if self.clear_condition_tree_check.isChecked()
                else self._source_condition_nodes
            )
            fields = {
                "name": name,
                "type": rule_type,
                "geometry": geometry,
                "target_labels": labels,
                "min_confidence": self.confidence_spin.value(),
                "min_box_height": self.box_height_spin.value(),
                "confirm_frames": self.confirm_spin.value(),
                "cooldown_seconds": self.cooldown_spin.value(),
                "direction": str(self.direction_combo.currentData() or "both"),
                "condition": condition,
                "count_threshold": self.threshold_spin.value() if condition in {"line_count", "occupancy"} else 0,
                "window_seconds": self.window_spin.value() if condition == "line_count" else 0.0,
                "dwell_seconds": self.dwell_spin.value() if condition == "dwell" else 0.0,
                "actions": tuple(actions),
                "http_action_url": self.http_action_url_edit.text().strip() if "http" in actions else "",
                "http_action_timeout": self.http_action_timeout_spin.value(),
                "ack_deadline_seconds": self.ack_deadline_spin.value(),
                "escalation_level": str(self.escalation_level_combo.currentData() or ""),
                "destination_ids": destination_ids,
                "assignees": assignees,
                "condition_nodes": condition_nodes,
                "enabled": self.enabled_check.isChecked(),
                "publication_status": str(self.publication_combo.currentData() or "draft"),
                "version": self._source_rule.version + 1 if self._source_rule is not None else 1,
            }
            if self._source_rule is not None:
                return replace(self._source_rule, **fields)
            return Rule(
                id="rule-new",
                stream_id=self._path.stream.id,
                site_id=self._path.site.id,
                area_id=self._path.area.id,
                camera_id=self._path.camera.id,
                **fields,
            )

        def _run_simulation(self) -> None:
            try:
                draft = self._draft_rule()
                report = simulate_rule(
                    draft,
                    width=self._path.stream.width,
                    height=self._path.stream.height,
                )
            except (TypeError, ValueError) as exc:
                self.simulation_label.setText(f"规则自检失败：{exc}")
                return
            self.simulation_label.setText(report.summary)

        def _validate_and_accept(self) -> None:
            try:
                self.rule = self._draft_rule()
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "规则校验失败", str(exc))
                return
            self.accept()

else:

    class RuleEditorDialog:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = ["RuleEditorDialog", "format_geometry", "parse_geometry", "CONDITION_SPECS"]