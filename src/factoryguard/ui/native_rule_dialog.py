from __future__ import annotations

from dataclasses import replace
from typing import Any

from factoryguard.domain.models import Rule, RuleMode
from factoryguard.domain.topology import EntityPath
from factoryguard.notifications.destinations import NotificationDestination

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
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
        Qt,
    )


if HAS_QT:

    class NativeRuleDialog(QDialog):
        """Map device event codes to the existing event and notification workflow."""

        def __init__(
            self,
            *,
            path: EntityPath,
            rule: Rule | None = None,
            destinations: tuple[NotificationDestination, ...] = (),
            allow_publish: bool = True,
            parent: Any = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("编辑设备事件规则" if rule else "新增设备事件规则")
            self.setModal(True)
            self.resize(620, 560)
            self._path = path
            self._source_rule = rule
            self.rule: Rule | None = None

            layout = QVBoxLayout(self)
            form = QFormLayout()
            self.name_edit = QLineEdit(rule.name if rule else "设备报警")
            self.codes_edit = QLineEdit(", ".join(rule.native_event_codes) if rule else "alarm, motiondetect")
            self.codes_edit.setPlaceholderText("多个事件代码用逗号分隔")
            self.dedup_spin = QDoubleSpinBox()
            self.dedup_spin.setRange(0.0, 3600.0)
            self.dedup_spin.setValue(rule.dedup_window_seconds if rule else 5.0)
            self.level_combo = QComboBox()
            for label, value in (("按设备级别", ""), ("低", "low"), ("中", "medium"), ("高", "high"), ("紧急", "critical")):
                self.level_combo.addItem(label, value)
            if rule:
                self.level_combo.setCurrentIndex(max(0, self.level_combo.findData(rule.escalation_level)))
            form.addRow("规则名称", self.name_edit)
            form.addRow("设备事件代码", self.codes_edit)
            form.addRow("重复合并窗口", self.dedup_spin)
            form.addRow("升级级别", self.level_combo)
            layout.addLayout(form)

            action_group = QGroupBox("动作")
            action_layout = QVBoxLayout(action_group)
            self.action_checks: dict[str, QCheckBox] = {}
            for key, label in (("snapshot", "保存截图"), ("clip", "保存事件短视频"), ("notify", "发送通知"), ("audit", "写入审计"), ("http", "调用外部 HTTP")):
                check = QCheckBox(label)
                check.setChecked(key in (rule.actions if rule else ("notify",)))
                self.action_checks[key] = check
                action_layout.addWidget(check)
            self.http_edit = QLineEdit(rule.http_action_url if rule else "")
            self.http_edit.setPlaceholderText("启用 HTTP 动作时填写 HTTPS 地址")
            action_layout.addWidget(self.http_edit)
            layout.addWidget(action_group)

            destination_group = QGroupBox("通知目标")
            destination_layout = QVBoxLayout(destination_group)
            self.destination_list = QListWidget()
            for destination in destinations:
                item = QListWidgetItem(destination.name)
                item.setData(Qt.UserRole, destination.id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if rule and destination.id in rule.destination_ids else Qt.Unchecked)
                self.destination_list.addItem(item)
            destination_layout.addWidget(self.destination_list)
            self.assignees_edit = QLineEdit(", ".join(rule.assignees) if rule else "")
            destination_layout.addWidget(QLabel("值班人员"))
            destination_layout.addWidget(self.assignees_edit)
            layout.addWidget(destination_group)

            self.enabled_check = QCheckBox("启用规则")
            self.enabled_check.setChecked(rule.enabled if rule else True)
            layout.addWidget(self.enabled_check)
            self.summary_label = QLabel("设备原生事件直接进入事件与通知闭环")
            self.summary_label.setObjectName("muted")
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)

            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.accepted.connect(self._validate_and_accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _draft_rule(self) -> Rule:
            codes = tuple(dict.fromkeys(item.strip().lower() for item in self.codes_edit.text().split(",") if item.strip()))
            if not codes:
                raise ValueError("至少填写一个设备事件代码")
            actions = ["event"] + [key for key, check in self.action_checks.items() if check.isChecked()]
            if "http" in actions and not self.http_edit.text().strip():
                raise ValueError("启用 HTTP 动作后必须填写地址")
            destinations = tuple(
                str(self.destination_list.item(row).data(Qt.UserRole))
                for row in range(self.destination_list.count())
                if self.destination_list.item(row).checkState() == Qt.Checked
            )
            fields = {
                "name": self.name_edit.text().strip() or "设备报警",
                "type": None,
                "geometry": (),
                "target_labels": frozenset({"device_event"}),
                "trigger_mode": RuleMode.NATIVE_EVENT,
                "native_event_codes": codes,
                "dedup_window_seconds": self.dedup_spin.value(),
                "actions": tuple(actions),
                "http_action_url": self.http_edit.text().strip() if "http" in actions else "",
                "escalation_level": str(self.level_combo.currentData() or ""),
                "destination_ids": destinations,
                "assignees": tuple(item.strip() for item in self.assignees_edit.text().split(",") if item.strip()),
                "enabled": self.enabled_check.isChecked(),
                "publication_status": "published" if self._source_rule is None else self._source_rule.publication_status,
                "version": self._source_rule.version + 1 if self._source_rule else 1,
            }
            if self._source_rule is not None:
                return replace(self._source_rule, **fields)
            return Rule(
                id="rule-native-new",
                stream_id=self._path.stream.id,
                site_id=self._path.site.id,
                area_id=self._path.area.id,
                camera_id=self._path.camera.id,
                **fields,
            )

        def _validate_and_accept(self) -> None:
            try:
                self.rule = self._draft_rule()
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "规则校验失败", str(exc))
                return
            self.accept()


else:

    class NativeRuleDialog:
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = ["NativeRuleDialog"]
