from __future__ import annotations

"""Desktop editor for one stream's weekly arming schedule."""

from datetime import UTC, datetime
from typing import Any

from factoryguard.domain.schedules import ArmingSchedule, TimeWindow

from .qt import HAS_QT, require_qt


def parse_windows(text: str) -> tuple[TimeWindow, ...]:
    """Parse comma or semicolon separated ``HH:MM-HH:MM`` windows."""
    windows: list[TimeWindow] = []
    for raw in text.replace("，", ",").replace("；", ",").replace(";", ",").split(","):
        value = raw.strip()
        if not value:
            continue
        if "-" not in value:
            raise ValueError(f"时间段格式错误：{value}，应为 HH:MM-HH:MM")
        start, end = (part.strip() for part in value.split("-", 1))
        windows.append(TimeWindow(_parse_minute(start), _parse_minute(end)))
    return tuple(windows)


def _parse_minute(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"时间格式错误：{value}") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"时间超出范围：{value}")
    return hour * 60 + minute


def format_windows(windows: tuple[TimeWindow, ...]) -> str:
    def format_minute(value: int) -> str:
        return f"{value // 60:02d}:{value % 60:02d}"

    return ", ".join(f"{format_minute(item.start_minute)}-{format_minute(item.end_minute)}" for item in windows)


if HAS_QT:
    from .qt import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QVBoxLayout,
    )

    class ScheduleEditorDialog(QDialog):
        def __init__(self, *, stream_id: str, schedule: ArmingSchedule | None = None, parent: Any = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("布防时间表")
            self.setModal(True)
            self.resize(520, 600)
            self.stream_id = stream_id
            self.schedule: ArmingSchedule | None = None
            source = schedule or ArmingSchedule(stream_id)
            layout = QVBoxLayout(self)
            form = QFormLayout()
            self.enabled_check = QCheckBox("启用时间表")
            self.enabled_check.setChecked(source.enabled)
            self.default_check = QCheckBox("无匹配时默认布防")
            self.default_check.setChecked(source.default_armed)
            form.addRow("状态", self.enabled_check)
            form.addRow("默认策略", self.default_check)
            self.day_edits: list[QLineEdit] = []
            labels = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
            for label, windows in zip(labels, source.weekly):
                edit = QLineEdit(format_windows(windows))
                edit.setPlaceholderText("08:00-18:00，支持跨午夜")
                self.day_edits.append(edit)
                form.addRow(label, edit)
            self.holidays_edit = QLineEdit(", ".join(sorted(source.holidays)))
            self.holidays_edit.setPlaceholderText("2026-01-01, 2026-10-01")
            form.addRow("节假日", self.holidays_edit)
            self.timezone_combo = QComboBox()
            for label, value in (
                ("本机时区", "local"),
                ("UTC", "UTC"),
                ("中国标准时间", "Asia/Shanghai"),
                ("日本标准时间", "Asia/Tokyo"),
                ("美国太平洋时间", "America/Los_Angeles"),
            ):
                self.timezone_combo.addItem(label, value)
            timezone_index = self.timezone_combo.findData(source.timezone)
            self.timezone_combo.setCurrentIndex(timezone_index if timezone_index >= 0 else 0)
            form.addRow("时区", self.timezone_combo)
            self.override_combo = QComboBox()
            self.override_combo.addItem("无临时覆盖", None)
            self.override_combo.addItem("临时布防", True)
            self.override_combo.addItem("临时撤防", False)
            self.override_combo.setCurrentIndex(
                self.override_combo.findData(source.override_armed) if source.override_armed is not None else 0
            )
            self.override_until_edit = QLineEdit(source.override_until.isoformat() if source.override_until else "")
            self.override_until_edit.setPlaceholderText("2026-10-20T18:00:00+08:00，可留空表示长期")
            override_row = QHBoxLayout()
            override_row.addWidget(self.override_combo)
            override_row.addWidget(self.override_until_edit, 1)
            form.addRow("临时覆盖", override_row)
            layout.addLayout(form)
            hint = QLabel("时间段按所选时区解释；结束时间早于开始时间时表示跨午夜。节假日当天默认撤防。")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
            buttons.accepted.connect(self._validate_and_accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _validate_and_accept(self) -> None:
            try:
                weekly = tuple(parse_windows(edit.text()) for edit in self.day_edits)
                holidays = frozenset(item.strip() for item in self.holidays_edit.text().split(",") if item.strip())
                for holiday in holidays:
                    datetime.strptime(holiday, "%Y-%m-%d")
                override_armed = self.override_combo.currentData()
                override_until_text = self.override_until_edit.text().strip()
                override_until = datetime.fromisoformat(override_until_text) if override_until_text else None
                if override_until is not None and override_until.tzinfo is None:
                    override_until = override_until.replace(tzinfo=UTC)
                self.schedule = ArmingSchedule(
                    self.stream_id,
                    weekly=weekly,
                    holidays=holidays,
                    enabled=self.enabled_check.isChecked(),
                    default_armed=self.default_check.isChecked(),
                    override_armed=override_armed,
                    override_until=override_until,
                    timezone=str(self.timezone_combo.currentData()),
                )
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, "时间表校验失败", str(exc))
                return
            self.accept()

else:

    class ScheduleEditorDialog:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = ["ScheduleEditorDialog", "format_windows", "parse_windows"]
