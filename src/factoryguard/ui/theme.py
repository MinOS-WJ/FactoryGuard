"""Centralized FactoryGuard desktop themes and control styling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_ASSET_DIR = Path(__file__).resolve().parent / "assets"

THEMES: dict[str, str] = {
    "浅色": """
        QMainWindow, QWidget { background: #f2f3f1; color: #1f2622; }
        QGroupBox { border: 1px solid #d6dbd7; border-radius: 0px; margin-top: 9px; padding: 12px; background: #ffffff; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #56625b; }
        QLabel#title { font-size: 23px; font-weight: 700; color: #18201c; }
        QLabel#subtitle, QLabel#muted { color: #69736d; }
        QLabel#status { padding: 5px 10px; border-radius: 0px; background: #e5e9e6; color: #4f5b54; }
        QLabel#channelStatus { color: #56625b; padding: 4px 7px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { padding: 6px 9px; border: 1px solid #cfd5d1; border-radius: 0px; background: #ffffff; color: #1f2622; }
        QPushButton { padding: 7px 12px; border: 1px solid #cbd2cd; border-radius: 0px; background: #ffffff; color: #26302a; }
        QPushButton:hover { border-color: #6e8c7d; background: #f5f7f5; }
        QPushButton#primary { background: #3f6f5d; color: #ffffff; border-color: #3f6f5d; }
        QPushButton#secondary { color: #3f6f5d; }
        QPushButton:disabled { color: #9aa39d; background: #f1f2f0; }
        QTableWidget { border: 1px solid #d6dbd7; gridline-color: #e9ecea; background: #ffffff; alternate-background-color: #f7f8f7; }
        QHeaderView::section { padding: 7px; background: #e8ece9; color: #4d5952; border: 0; border-right: 1px solid #d6dbd7; border-bottom: 1px solid #d6dbd7; }
        QSplitter::handle { background: #d9dedb; }
        QWidget#sidebar { background: #f7f8f6; border-right: 1px solid #d6dbd7; }
        QLabel#brand { font-size: 18px; font-weight: 700; color: #18201c; }
        QLabel#navSection { color: #7a857e; font-size: 11px; font-weight: 700; padding: 11px 9px 4px; }
        QPushButton#navButton { text-align: left; padding: 8px 10px; border: 1px solid transparent; border-radius: 0px; color: #4f5b54; background: transparent; }
        QPushButton#navButton:hover { background: #ecefec; }
        QPushButton#navButton:checked { color: #2f5f4d; background: #ffffff; border-color: #cfd5d1; font-weight: 600; }
    """,
    "深色": """
        QMainWindow, QWidget { background: #171b1a; color: #e6ebe8; }
        QGroupBox { border: 1px solid #353d39; border-radius: 0px; margin-top: 9px; padding: 12px; background: #202624; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #b2bdb6; }
        QLabel#title { font-size: 23px; font-weight: 700; color: #f2f5f3; }
        QLabel#subtitle, QLabel#muted { color: #98a49d; }
        QLabel#status { padding: 5px 10px; border-radius: 0px; background: #2a322e; color: #c4cec8; }
        QLabel#channelStatus { color: #b2bdb6; padding: 4px 7px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { padding: 6px 9px; border: 1px solid #3d4741; border-radius: 0px; background: #232a27; color: #e6ebe8; }
        QPushButton { padding: 7px 12px; border: 1px solid #414b45; border-radius: 0px; background: #262e2a; color: #e6ebe8; }
        QPushButton:hover { border-color: #7da08f; background: #2d3732; }
        QPushButton#primary { background: #4b806b; color: #ffffff; border-color: #4b806b; }
        QPushButton#secondary { color: #9fc2b1; }
        QPushButton:disabled { color: #6d7872; background: #202522; }
        QTableWidget { border: 1px solid #353d39; gridline-color: #303733; background: #202624; alternate-background-color: #242b27; }
        QHeaderView::section { padding: 7px; background: #2b332f; color: #c3ccc7; border: 0; border-right: 1px solid #3a433e; border-bottom: 1px solid #3a433e; }
        QSplitter::handle { background: #353d39; }
        QWidget#sidebar { background: #1c211f; border-right: 1px solid #353d39; }
        QLabel#brand { font-size: 18px; font-weight: 700; color: #f2f5f3; }
        QLabel#navSection { color: #849088; font-size: 11px; font-weight: 700; padding: 11px 9px 4px; }
        QPushButton#navButton { text-align: left; padding: 8px 10px; border: 1px solid transparent; border-radius: 0px; color: #b2bdb6; background: transparent; }
        QPushButton#navButton:hover { background: #29312d; }
        QPushButton#navButton:checked { color: #c5ddcf; background: #2d3732; border-color: #414b45; font-weight: 600; }
    """,
    "海蓝": """
        QMainWindow, QWidget { background: #eef3f2; color: #19312b; }
        QGroupBox { border: 1px solid #c9d8d4; border-radius: 0px; margin-top: 9px; padding: 12px; background: #ffffff; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #496c63; }
        QLabel#title { font-size: 23px; font-weight: 700; color: #143c32; }
        QLabel#subtitle, QLabel#muted { color: #607d75; }
        QLabel#status { padding: 5px 10px; border-radius: 0px; background: #dce9e5; color: #45675e; }
        QLabel#channelStatus { color: #496c63; padding: 4px 7px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { padding: 6px 9px; border: 1px solid #c3d5d0; border-radius: 0px; background: #ffffff; color: #19312b; }
        QPushButton { padding: 7px 12px; border: 1px solid #bfcec9; border-radius: 0px; background: #ffffff; color: #24473e; }
        QPushButton:hover { border-color: #4f8a79; background: #f2f7f5; }
        QPushButton#primary { background: #397562; color: #ffffff; border-color: #397562; }
        QPushButton#secondary { color: #397562; }
        QPushButton:disabled { color: #92a39d; background: #edf1ef; }
        QTableWidget { border: 1px solid #c9d8d4; gridline-color: #e3ebe8; background: #ffffff; alternate-background-color: #f5f8f7; }
        QHeaderView::section { padding: 7px; background: #e2ece9; color: #496c63; border: 0; border-right: 1px solid #c9d8d4; border-bottom: 1px solid #c9d8d4; }
        QSplitter::handle { background: #ccdcd7; }
        QWidget#sidebar { background: #f6f9f8; border-right: 1px solid #c9d8d4; }
        QLabel#brand { font-size: 18px; font-weight: 700; color: #143c32; }
        QLabel#navSection { color: #729087; font-size: 11px; font-weight: 700; padding: 11px 9px 4px; }
        QPushButton#navButton { text-align: left; padding: 8px 10px; border: 1px solid transparent; border-radius: 0px; color: #496c63; background: transparent; }
        QPushButton#navButton:hover { background: #e8f1ee; }
        QPushButton#navButton:checked { color: #2d6655; background: #ffffff; border-color: #c3d5d0; font-weight: 600; }
    """,
}


THEME_PALETTES: dict[str, dict[str, str]] = {
    "浅色": {
        "window": "#f2f3f1",
        "base": "#ffffff",
        "alternate": "#f7f8f7",
        "button": "#ffffff",
        "text": "#1f2622",
        "mid": "#cfd5d1",
        "midlight": "#dce5df",
        "highlight": "#3f6f5d",
        "highlighted_text": "#ffffff",
    },
    "深色": {
        "window": "#171b1a",
        "base": "#232a27",
        "alternate": "#242b27",
        "button": "#262e2a",
        "text": "#e6ebe8",
        "mid": "#3d4741",
        "midlight": "#313b36",
        "highlight": "#4b806b",
        "highlighted_text": "#ffffff",
    },
    "海蓝": {
        "window": "#eef3f2",
        "base": "#ffffff",
        "alternate": "#f5f8f7",
        "button": "#ffffff",
        "text": "#19312b",
        "mid": "#c3d5d0",
        "midlight": "#dce9e5",
        "highlight": "#397562",
        "highlighted_text": "#ffffff",
    },
}

def apply_theme(application: Any, name: str) -> None:
    """Apply one named theme plus shared control rules to the application."""
    if application is None or name not in THEMES:
        return
    from .qt import QColor, QPalette

    colors = THEME_PALETTES[name]
    palette = application.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alternate"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor(colors["mid"]))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(colors["midlight"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["highlight"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["highlighted_text"]))
    application.setPalette(palette)
    caret_down = (_ASSET_DIR / "caret-down.svg").as_posix()
    caret_up = (_ASSET_DIR / "caret-up.svg").as_posix()
    style = (THEMES[name] + """
        QFrame#metricCard {
            background: palette(base);
            border: 1px solid palette(mid);
            border-left: 3px solid palette(highlight);
            border-radius: 0px;
        }
        QLabel#metricCaption { color: palette(mid); font-size: 12px; }
        QLabel#metricValue { color: palette(text); font-size: 24px; font-weight: 600; }
        QLabel#metricDetail { color: palette(mid); font-size: 11px; }
        QScrollArea#pageScroll { background: transparent; border: 0; }
        QLabel#subtitle { font-size: 13px; }
        QLabel#muted { font-size: 12px; }
        QGroupBox { border-radius: 0px; font-weight: 600; }
        QGroupBox::title { font-size: 13px; font-weight: 600; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
            min-height: 33px;
            padding: 2px 9px;
            border: 1px solid palette(mid);
            border-radius: 0px;
            background: palette(base);
            selection-background-color: palette(highlight);
            selection-color: palette(highlighted-text);
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
            border: 1px solid palette(highlight);
        }
        QComboBox { combobox-popup: 0; padding-right: 34px; }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 30px;
            border: 0;
            border-left: 1px solid palette(mid);
            border-top-right-radius: 0px;
            border-bottom-right-radius: 0px;
            background: transparent;
        }
        QComboBox::down-arrow { image: url("__CARET_DOWN__"); width: 8px; height: 5px; }
        QSpinBox, QDoubleSpinBox { padding-right: 32px; }
        QSpinBox::up-button, QDoubleSpinBox::up-button {
            subcontrol-origin: border; subcontrol-position: top right;
            width: 26px; border: 0; border-left: 1px solid palette(mid);
            background: transparent;
        }
        QSpinBox::down-button, QDoubleSpinBox::down-button {
            subcontrol-origin: border; subcontrol-position: bottom right;
            width: 26px; border: 0; border-left: 1px solid palette(mid);
            background: transparent;
        }
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("__CARET_UP__"); width: 7px; height: 4px; }
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("__CARET_DOWN__"); width: 7px; height: 4px; }
        QComboBox QAbstractItemView, QComboBox QListView {
            show-decoration-selected: 1;
            spacing: 0;
            background: palette(base);
            border: 1px solid palette(mid);
            border-radius: 0px;
            padding: 2px;
            outline: 0;
            selection-background-color: palette(midlight);
            selection-color: palette(text);
        }
        QComboBox QAbstractItemView::item { min-height: 26px; padding: 3px 8px; border: 0; }
        QPushButton { min-height: 32px; padding: 0 12px; border-radius: 0px; font-weight: 500; }
        QPushButton:focus { border-color: palette(highlight); }
        QPushButton#navButton { border-radius: 0px; padding: 8px 10px; }
        QMenu { background: palette(base); border: 1px solid palette(mid); border-radius: 0px; padding: 4px; }
        QMenu::item { padding: 7px 24px 7px 10px; border-radius: 0px; }
        QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
        QToolTip { padding: 5px 8px; border: 1px solid palette(mid); border-radius: 0px; background: palette(base); color: palette(text); }
        QTableWidget { font-size: 12px; border-radius: 0px; selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }
        QHeaderView::section { min-height: 31px; padding: 6px 7px; font-weight: 600; }
        QTreeWidget { font-size: 12px; border-radius: 0px; }
        QTreeWidget::item { min-height: 27px; padding: 3px 5px; border-radius: 0px; }
        QStatusBar { min-height: 28px; font-size: 12px; }
        QScrollBar:vertical { width: 10px; margin: 2px; background: transparent; }
        QScrollBar::handle:vertical { min-height: 32px; border-radius: 0px; background: palette(mid); }
        QScrollBar:horizontal { height: 10px; margin: 2px; background: transparent; }
        QScrollBar::handle:horizontal { min-width: 32px; border-radius: 0px; background: palette(mid); }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    """).replace("__CARET_DOWN__", caret_down).replace("__CARET_UP__", caret_up)
    application.setStyleSheet(style)


__all__ = ["THEMES", "apply_theme"]
