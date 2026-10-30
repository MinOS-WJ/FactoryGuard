"""Read-only rule history dialogs with explicit restore selection."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .qt import HAS_QT, require_qt

if HAS_QT:
    from .qt import (
        QAbstractItemView,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        Qt,
    )


def _format_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


if HAS_QT:

    class RuleReviewHistoryDialog(QDialog):
        """Show review decisions and notes for one rule."""

        def __init__(
            self,
            *,
            rule_name: str,
            rows: Sequence[Mapping[str, Any]],
            approvers: Sequence[str] = (),
            parent: Any = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("规则审核记录")
            self.resize(820, 440)
            layout = QVBoxLayout(self)
            summary = QLabel(
                f"规则：{rule_name}\n已通过审核人：{'、'.join(approvers) if approvers else '暂无'}"
            )
            summary.setWordWrap(True)
            summary.setObjectName("muted")
            layout.addWidget(summary)
            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(["时间", "版本", "审核人", "结论", "说明"])
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setAlternatingRowColors(True)
            self.table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.table, 1)
            decisions = {"approve": "通过", "reject": "驳回"}
            for row in rows:
                index = self.table.rowCount()
                self.table.insertRow(index)
                values = (
                    _format_timestamp(row["ts"]),
                    f"v{row['version']}",
                    str(row["actor"]),
                    decisions.get(str(row["decision"]), str(row["decision"])),
                    str(row["note"] or "—"),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    self.table.setItem(index, column, item)
            if not rows:
                self.table.setRowCount(1)
                empty = QTableWidgetItem("暂无审核记录")
                self.table.setItem(0, 0, empty)
                self.table.setSpan(0, 0, 1, 5)
            actions = QHBoxLayout()
            actions.addStretch(1)
            close_button = QPushButton("关闭")
            close_button.clicked.connect(self.reject)
            actions.addWidget(close_button)
            layout.addLayout(actions)

    class RuleVersionHistoryDialog(QDialog):
        """Show saved versions and return the version selected for restore."""

        def __init__(
            self,
            *,
            rule_name: str,
            rows: Sequence[Mapping[str, Any]],
            allow_restore: bool = True,
            parent: Any = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("规则版本历史")
            self.resize(760, 420)
            self.selected_version: int | None = None
            self._allow_restore = allow_restore
            layout = QVBoxLayout(self)
            hint = QLabel(f"规则：{rule_name}\n恢复旧版本不会覆盖历史记录，而是生成一个新版本。")
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            layout.addWidget(hint)
            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(["版本", "保存时间", "操作人", "说明"])
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setAlternatingRowColors(True)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self._update_restore_button)
            self.table.cellDoubleClicked.connect(lambda _row, _column: self._accept_restore())
            layout.addWidget(self.table, 1)
            for row in rows:
                index = self.table.rowCount()
                self.table.insertRow(index)
                version = int(row["version"])
                values = (f"v{version}", _format_timestamp(row["created_at"]), str(row["actor"]), str(row["note"] or "—"))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.UserRole, version)
                    self.table.setItem(index, column, item)
            if not rows:
                self.table.setRowCount(1)
                empty = QTableWidgetItem("暂无版本记录")
                self.table.setItem(0, 0, empty)
                self.table.setSpan(0, 0, 1, 4)
            actions = QHBoxLayout()
            actions.addStretch(1)
            self.restore_button = QPushButton("恢复选中版本")
            self.restore_button.setEnabled(False)
            self.restore_button.setVisible(allow_restore)
            self.restore_button.clicked.connect(self._accept_restore)
            close_button = QPushButton("关闭")
            close_button.clicked.connect(self.reject)
            actions.addWidget(self.restore_button)
            actions.addWidget(close_button)
            layout.addLayout(actions)

        def _selected_version(self) -> int | None:
            selected = self.table.selectedItems()
            if not selected:
                return None
            value = selected[0].data(Qt.UserRole)
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def _update_restore_button(self) -> None:
            self.restore_button.setEnabled(self._allow_restore and self._selected_version() is not None)

        def _accept_restore(self) -> None:
            if not self._allow_restore:
                return
            version = self._selected_version()
            if version is None:
                return
            self.selected_version = version
            self.accept()

else:

    class RuleReviewHistoryDialog:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()

    class RuleVersionHistoryDialog:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            require_qt()


__all__ = ["RuleReviewHistoryDialog", "RuleVersionHistoryDialog"]