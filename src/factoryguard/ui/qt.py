"""Small import boundary for the optional PySide6 dependency."""

from __future__ import annotations

try:  # Keep non-GUI commands usable without Qt installed.
    from PySide6.QtCore import QObject, QRectF, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import (
        QAction,
        QCloseEvent,
        QColor,
        QDesktopServices,
        QFont,
        QIcon,
        QImage,
        QPainter,
        QPalette,
        QPen,
    )
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QDateTimeEdit,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QStatusBar,
        QStyle,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    HAS_QT = True
    QT_IMPORT_ERROR: Exception | None = None
except Exception as exc:
    HAS_QT = False
    QT_IMPORT_ERROR = exc


def require_qt() -> None:
    if not HAS_QT:
        detail = str(QT_IMPORT_ERROR) if QT_IMPORT_ERROR else "PySide6 is not installed"
        raise RuntimeError(
            "Qt GUI is unavailable. Install PySide6 with `python -m pip install -r requirements.txt`. "
            f"Import detail: {detail}"
        )


def qt_available() -> bool:
    return HAS_QT


__all__ = [
    "HAS_QT",
    "QT_IMPORT_ERROR",
    "QAbstractItemView",
    "QAction",
    "QApplication",
    "QCheckBox",
    "QCloseEvent",
    "QColor",
    "QComboBox",
    "QDateTimeEdit",
    "QDesktopServices",
    "QDialog",
    "QDialogButtonBox",
    "QDoubleSpinBox",
    "QFileDialog",
    "QFont",
    "QFormLayout",
    "QFrame",
    "QGridLayout",
    "QGroupBox",
    "QHBoxLayout",
    "QHeaderView",
    "QIcon",
    "QImage",
    "QInputDialog",
    "QLabel",
    "QLineEdit",
    "QListWidget",
    "QListWidgetItem",
    "QMainWindow",
    "QMenu",
    "QMessageBox",
    "QObject",
    "QPainter",
    "QPalette",
    "QPen",
    "QPlainTextEdit",
    "QPushButton",
    "QRectF",
    "QScrollArea",
    "QSettings",
    "QSizePolicy",
    "QSpinBox",
    "QSplitter",
    "QStackedWidget",
    "QStatusBar",
    "QStyle",
    "QSystemTrayIcon",
    "QTableWidget",
    "QTableWidgetItem",
    "QThread",
    "QTimer",
    "QTreeWidget",
    "QTreeWidgetItem",
    "QUrl",
    "QVBoxLayout",
    "QWidget",
    "Qt",
    "Signal",
    "Slot",
    "qt_available",
    "require_qt",
]
