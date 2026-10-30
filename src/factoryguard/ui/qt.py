from __future__ import annotations

"""Small import boundary for the optional PySide6 dependency."""

try:  # Keep non-GUI commands usable without Qt installed.
    from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPalette, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QGridLayout,
        QHeaderView,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QComboBox,
        QSpinBox,
        QStatusBar,
        QStyle,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
        QAbstractItemView,
        QDateTimeEdit,
        QDialog,
        QDialogButtonBox,
        QCheckBox,
        QFileDialog,
        QPlainTextEdit,
        QSplitter,
        QStackedWidget,
        QScrollArea,
        QSizePolicy,
        QFrame,
    )
    from PySide6.QtCore import QRectF

    HAS_QT = True
    QT_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001 - keep non-GUI commands usable without Qt
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
    "QAction",
    "QAbstractItemView",
    "QApplication",
    "QCloseEvent",
    "QColor",
    "QComboBox",
    "QDesktopServices",
    "QDoubleSpinBox",
    "QDateTimeEdit",
    "QDialog",
    "QDialogButtonBox",
    "QCheckBox",
    "QFileDialog",
    "QFormLayout",
    "QGridLayout",
    "QGroupBox",
    "QHeaderView",
    "QHBoxLayout",
    "QInputDialog",
    "QIcon",
    "QImage",
    "QLabel",
    "QLineEdit",
    "QListWidget",
    "QListWidgetItem",
    "QMainWindow",
    "QMenu",
    "QMessageBox",
    "QObject",
    "QPushButton",
    "QPainter",
    "QPalette",
    "QPen",
    "QPlainTextEdit",
    "QFont",
    "QRectF",
    "QSettings",
    "QSpinBox",
    "QStatusBar",
    "QStyle",
    "QSystemTrayIcon",
    "QTableWidget",
    "QTableWidgetItem",
    "QTreeWidget",
    "QTreeWidgetItem",
    "QThread",
    "QTimer",
    "QUrl",
    "QVBoxLayout",
    "QSplitter",
    "QStackedWidget",
    "QScrollArea",
    "QSizePolicy",
    "QFrame",
    "QWidget",
    "Qt",
    "Signal",
    "Slot",
    "qt_available",
    "require_qt",
]
