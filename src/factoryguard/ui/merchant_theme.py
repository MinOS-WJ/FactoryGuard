"""Commercial visual language for the merchant-facing FactoryGuard console."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_ASSET_DIR = Path(__file__).resolve().parent / "assets"


PALETTE = {
    "navy": "#17261F",
    "navy_soft": "#1B2D25",
    "teal": "#176B5B",
    "teal_dark": "#125448",
    "orange": "#A7643D",
    "red": "#AD3F43",
    "amber": "#A36B1F",
    "green": "#2D7864",
    "ink": "#1F2C27",
    "muted": "#68756F",
    "background": "#F3F1EC",
    "card": "#FFFFFF",
    "border": "#E3E4DF",
}


MERCHANT_STYLESHEET = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    outline: none;
}
QMainWindow, QWidget#appRoot, QWidget#pageCanvas {
    background: #F3F1EC;
    color: #1F2C27;
}
QWidget#pageCanvas {
    background: transparent;
}
QFrame#sidebar {
    background: #F8F6F1;
    border: none;
    border-right: 1px solid #E4E1D9;
}
QLabel#sidebarOverline {
    color: #8B958E;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#sidebarTitle {
    color: #24322C;
    font-size: 18px;
    font-weight: 700;
}
QLabel#brandMark {
    background: #1D4339;
    color: white;
    border-radius: 10px;
    font-size: 15px;
    font-weight: 800;
    padding: 8px;
}
QLabel#brandName {
    color: #213029;
    font-size: 17px;
    font-weight: 700;
}
QLabel#brandTagline {
    color: #8A948E;
    font-size: 10px;
}
QLabel#navSection {
    color: #929B95;
    font-size: 10px;
    font-weight: 700;
    padding: 13px 12px 5px;
}
QPushButton#navItem {
    min-height: 42px;
    text-align: left;
    padding: 0 14px;
    border: none;
    border-radius: 9px;
    color: #4F5F57;
    background: transparent;
    font-size: 13px;
    font-weight: 550;
}
QPushButton#navItem:hover {
    color: #1D392F;
    background: #EBEEE9;
}
QPushButton#navItem:checked {
    color: #FFFFFF;
    background: #1D4339;
    border: none;
    font-weight: 650;
}
QWidget#sidebarStatusCard {
    background: #EDF1EC;
    border: 1px solid #DFE5DE;
    border-radius: 12px;
}
QFrame#sidebarAssistant {
    background: #EDF1EC;
    border: 1px solid #DFE5DE;
    border-radius: 12px;
}
QLabel#sidebarAssistantTitle {
    color: #2B3932;
    font-weight: 700;
}
QLabel#sidebarAssistantText {
    color: #718078;
    font-size: 11px;
}
QLabel#sidebarAssistantState {
    color: #26745F;
    font-size: 11px;
    font-weight: 650;
}
QFrame#topBar {
    background: transparent;
    border: none;
}
QLabel#pageTitle {
    color: #1B2923;
    font-size: 25px;
    font-weight: 750;
}
QLabel#pageSubtitle {
    color: #728078;
    font-size: 12px;
}
QLabel#todayLabel {
    color: #596961;
    background: #FBFAF7;
    border: 1px solid #E2E1DA;
    border-radius: 15px;
    min-height: 30px;
    padding: 0 12px;
    font-size: 11px;
}
QFrame#card, QFrame#metricCard, QFrame#sectionCard, QFrame#heroCard,
QFrame#cameraCard, QFrame#incidentCard, QFrame#contextCard, QFrame#areaCard {
    background: #FFFFFF;
    border: 1px solid #E2E3DD;
    border-radius: 12px;
}
QFrame#liveTile {
    background: #FFFFFF;
    border: 1px solid #DDE1DB;
    border-radius: 11px;
}
QFrame#heroCard {
    background: #E7EDE8;
    border: 1px solid #DCE5DE;
}
QLabel#heroEyebrow {
    color: #286956;
    font-size: 10px;
    font-weight: 700;
}
QLabel#heroTitle {
    color: #1A3027;
    font-size: 23px;
    font-weight: 750;
}
QLabel#heroText {
    color: #617068;
    font-size: 12px;
}
QLabel#heroMetric {
    color: #172D25;
    font-size: 20px;
    font-weight: 700;
}
QLabel#heroMetricCaption {
    color: #74827A;
    font-size: 11px;
}
QLabel#sectionTitle {
    color: #1F2E27;
    font-size: 15px;
    font-weight: 700;
}
QLabel#sectionCaption, QLabel#muted, QLabel#cardMuted {
    color: #75827A;
    font-size: 11px;
}
QLabel#errorHint {
    color: #A33E3E;
    background: #FFF7F5;
    border: 1px solid #F1D5D1;
    border-radius: 7px;
    padding: 7px 9px;
    font-size: 10px;
}
QLabel#cardWarning {
    color: #9A5B22;
    font-size: 10px;
    font-weight: 600;
}
QLabel#metricValue {
    color: #1A2923;
    font-size: 27px;
    font-weight: 750;
}
QLabel#metricLabel {
    color: #617068;
    font-size: 11px;
    font-weight: 650;
}
QLabel#metricHint {
    color: #8B958F;
    font-size: 10px;
}
QLabel#metricGlyph {
    font-size: 16px;
    font-weight: 700;
    border-radius: 10px;
    padding: 7px;
}
QLabel#metricGlyph[tone="teal"] { color: #2E715E; background: #E7F0EB; }
QLabel#metricGlyph[tone="blue"] { color: #496D78; background: #E9F0F2; }
QLabel#metricGlyph[tone="red"] { color: #A34843; background: #F6E9E7; }
QLabel#metricGlyph[tone="orange"] { color: #96613B; background: #F5ECE3; }
QLabel#metricGlyph[tone="amber"] { color: #92702B; background: #F4EFDF; }
QPushButton#primary {
    min-height: 36px;
    padding: 0 17px;
    border-radius: 8px;
    border: 1px solid #176B5B;
    background: #176B5B;
    color: #FFFFFF;
    font-weight: 650;
}
QPushButton#primary:hover { background: #125448; border-color: #125448; }
QPushButton#primary:pressed { background: #0F463E; }
QPushButton#primary:disabled {
    background: #E5E6E1;
    border-color: #E5E6E1;
    color: #9BA39E;
}
QPushButton#aiButton {
    min-height: 38px;
    padding: 0 18px;
    border-radius: 8px;
    border: 1px solid #30473D;
    background: #20362D;
    color: #F8FAF9;
    font-weight: 650;
}
QPushButton#aiButton:hover { background: #172D25; border-color: #172D25; }
QPushButton#secondary, QPushButton#toolButton {
    min-height: 34px;
    padding: 0 14px;
    border-radius: 8px;
    border: 1px solid #D9DDD7;
    background: #FFFFFF;
    color: #46564E;
    font-weight: 550;
}
QPushButton#secondary:hover, QPushButton#toolButton:hover {
    background: #F8F8F5;
    border-color: #B7C1BA;
    color: #253A31;
}
QPushButton#secondary:disabled, QPushButton#toolButton:disabled {
    background: #F4F4F1;
    border-color: #E5E6E1;
    color: #A8AFAA;
}
QPushButton#danger {
    min-height: 34px;
    padding: 0 14px;
    border-radius: 8px;
    border: 1px solid #EDCAC7;
    background: #FFF7F5;
    color: #A33E3E;
    font-weight: 600;
}
QPushButton#danger:hover { background: #FCECE9; }
QPushButton#linkButton {
    border: none;
    background: transparent;
    color: #176B5B;
    padding: 4px 2px;
    font-weight: 650;
}
QPushButton#linkButton:hover { color: #0F4D43; }
QPushButton#suggestionChip {
    text-align: left;
    padding: 9px 12px;
    border-radius: 8px;
    border: 1px solid #E1E4DF;
    background: #FAFAF7;
    color: #46564E;
}
QPushButton#suggestionChip:hover {
    background: #F1F4EF;
    border-color: #BFCAC2;
    color: #263C32;
}
QLabel#statusPill {
    border-radius: 8px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 650;
}
QLabel#statusPill[state="online"] { color: #236A56; background: #E8F2EC; }
QLabel#statusPill[state="offline"] { color: #A33E3E; background: #F8EAE8; }
QLabel#statusPill[state="unknown"] { color: #68756F; background: #EFF1ED; }
QLabel#statusPill[state="open"] { color: #A33E3E; background: #F8EAE8; }
QLabel#statusPill[state="acknowledged"] { color: #93651F; background: #F7F0DF; }
QLabel#statusPill[state="resolved"] { color: #236A56; background: #E8F2EC; }
QLabel#statusPill[state="false_alarm"] { color: #68756F; background: #EFF1ED; }
QLabel#severityPill {
    border-radius: 7px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}
QLabel#severityPill[severity="critical"] { color: #FFFFFF; background: #AD3F43; }
QLabel#severityPill[severity="high"] { color: #9E4D2D; background: #F7E7DE; }
QLabel#severityPill[severity="medium"] { color: #88631C; background: #F5EFD9; }
QLabel#severityPill[severity="low"] { color: #426B7B; background: #EAF1F4; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    min-height: 34px;
    padding: 0 10px;
    border: 1px solid #D9DEDA;
    border-radius: 8px;
    background: #FFFFFF;
    color: #203029;
    selection-background-color: #2B7562;
    selection-color: white;
}
QPlainTextEdit, QTextEdit { padding: 9px 10px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border: 1px solid #2B7562; }
QLineEdit::placeholder { color: #A0A8A3; }
QSpinBox, QDoubleSpinBox { padding-right: 30px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border: none;
    border-left: 1px solid #E5E8E3;
    border-bottom: 1px solid #E5E8E3;
    border-top-right-radius: 8px;
    background: #F7F8F4;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border: none;
    border-left: 1px solid #E5E8E3;
    border-bottom-right-radius: 8px;
    background: #F7F8F4;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: #EDF2EC;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url("__CARET_UP__"); width: 8px; height: 5px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url("__CARET_DOWN__"); width: 8px; height: 5px;
}
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url("__CARET_DOWN__"); width: 10px; height: 6px; }
QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid #D9DEDA;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #E8F1EC;
    selection-color: #175B4D;
}
QComboBox QAbstractItemView::item { min-height: 28px; padding: 3px 8px; }
QCheckBox { color: #46564E; spacing: 8px; }
QCheckBox::indicator {
    width: 17px; height: 17px; border-radius: 4px;
    border: 1px solid #BAC3BD; background: #FFFFFF;
}
QCheckBox::indicator:checked {
    background: #2B7562;
    border-color: #2B7562;
    image: url("__CHECK_WHITE__");
}
QTableWidget, QTreeWidget {
    background: #FFFFFF;
    border: 1px solid #E1E3DE;
    border-radius: 10px;
    gridline-color: #EEF0EC;
    alternate-background-color: #FBFBF8;
    selection-background-color: #E8F1EC;
    selection-color: #175B4D;
}
QListWidget#selectionList {
    background: #FFFFFF;
    border: 1px solid #E1E3DE;
    border-radius: 10px;
    padding: 5px;
    outline: none;
}
QListWidget#selectionList::item {
    min-height: 38px;
    padding: 5px 8px;
    border-bottom: 1px solid #EEF0EC;
    color: #26372F;
}
QListWidget#selectionList::item:hover { background: #F4F7F2; }
QListWidget#selectionList::item:last { border-bottom: none; }
QHeaderView::section {
    background: #F6F7F3;
    color: #66736C;
    border: none;
    border-bottom: 1px solid #E1E3DE;
    padding: 8px;
    font-weight: 650;
}
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { min-height: 32px; border-radius: 4px; background: #C7CEC8; }
QScrollBar::handle:vertical:hover { background: #AAB6AE; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 10px; background: transparent; }
QScrollBar::handle:horizontal { min-width: 32px; border-radius: 4px; background: #C7CEC8; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QFrame#cameraThumb {
    background: #17231F;
    border: none;
    border-radius: 10px;
}
QLabel#cameraName { color: #203029; font-size: 14px; font-weight: 700; }
QLabel#cameraMeta { color: #7A8780; font-size: 10px; }
QLabel#summaryStrip {
    color: #63716A;
    background: #F6F7F3;
    border: 1px solid #E8EAE5;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 10px;
}
QLabel#cameraLiveTag {
    color: #F1F8F5; background: rgba(45, 120, 100, 218);
    border-radius: 6px; padding: 3px 7px; font-size: 10px; font-weight: 700;
}
QLabel#cameraEmptyTag {
    color: #A9B5AE; background: rgba(255, 255, 255, 18);
    border-radius: 6px; padding: 3px 7px; font-size: 10px;
}
QFrame#incidentRow {
    background: #FFFFFF;
    border: 1px solid #E7E8E3;
    border-radius: 10px;
}
QFrame#incidentRow:hover { background: #FBFBF8; border-color: #C9D2CB; }
QFrame#incidentRow[selected="true"] { background: #F3F7F3; border-color: #A9C5B5; }
QLabel#incidentTitle { color: #203029; font-size: 12px; font-weight: 650; }
QLabel#incidentMeta { color: #7A8780; font-size: 10px; }
QFrame#chatUser {
    background: #1D5A4C;
    border: none;
    border-radius: 12px;
}
QFrame#chatAssistant {
    background: #FFFFFF;
    border: 1px solid #E1E4DF;
    border-radius: 12px;
}
QFrame#welcomeCard {
    background: #F4F7F3;
    border: 1px solid #DDE6DF;
    border-radius: 12px;
}
QFrame#settingsInfo {
    background: #F6F7F3;
    border: 1px solid #E2E5DF;
    border-radius: 10px;
}
QLabel#chatText { color: #24342C; font-size: 12px; }
QLabel#chatTextOnColor { color: #FFFFFF; font-size: 12px; }
QFrame#assistantComposer {
    background: #FFFFFF;
    border: 1px solid #D9DEDA;
    border-radius: 12px;
}
QLabel#smallLabel { color: #78857E; font-size: 10px; }
QFrame#line { background: #E8EAE5; border: none; max-height: 1px; min-height: 1px; }
QFrame#emptyState {
    background: #FAFAF7;
    border: 1px dashed #CDD4CE;
    border-radius: 12px;
}
QDialog { background: #F5F3EE; color: #1F2C27; }
QDialog QLabel#dialogTitle { color: #1F2C27; font-size: 20px; font-weight: 700; }
QDialog QLabel#dialogHint { color: #75827A; font-size: 12px; }
QDialogButtonBox QPushButton { min-height: 34px; min-width: 78px; }
QStatusBar {
    background: #EFEEE9;
    color: #747F79;
    border-top: 1px solid #DFE0DA;
}
QToolTip {
    color: #EFF5F2; background: #20362D; border: 1px solid #355247;
    border-radius: 6px; padding: 5px 7px;
}
"""


def apply_merchant_theme(application: Any) -> None:
    style = (
        MERCHANT_STYLESHEET.replace("__CARET_DOWN__", (_ASSET_DIR / "caret-down.svg").as_posix())
        .replace("__CARET_UP__", (_ASSET_DIR / "caret-up.svg").as_posix())
        .replace("__CHECK_WHITE__", (_ASSET_DIR / "check-white.svg").as_posix())
    )
    application.setStyleSheet(style)
    ensure_chinese_font(application)


# Chinese font candidates in order of preference.  The console is always used
# with Chinese copy, so the font has to be present even when the platform
# reports no installed families at all.
CJK_FONT_CANDIDATES: tuple[str, ...] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "PingFang SC",
    "SimHei",
    "WenQuanYi Zen Hei",
)

_FONT_FILES: tuple[str, ...] = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)


def ensure_chinese_font(application: Any) -> str:
    """Make sure a Chinese font is usable and return its family name.

    Qt's offscreen platform often reports an empty font database, which turns
    every Chinese label into empty boxes.  Registering the system font files
    directly fixes the rendering on those machines and on stripped-down
    Windows installations where the family is not indexed.
    """
    try:
        from PySide6.QtGui import QFont, QFontDatabase
    except Exception:  # pragma: no cover - PySide6 is required in practice
        return ""

    try:
        families = set(QFontDatabase.families())
    except Exception:  # pragma: no cover - defensive
        families = set()

    for family in CJK_FONT_CANDIDATES:
        if family in families:
            application.setFont(QFont(family, 10))
            return family

    for path in _FONT_FILES:
        font_file = Path(path)
        if not font_file.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        if font_id < 0:
            continue
        loaded = QFontDatabase.applicationFontFamilies(font_id)
        if loaded:
            family = loaded[0]
            application.setFont(QFont(family, 10))
            return family
    return ""


__all__ = [
    "CJK_FONT_CANDIDATES",
    "MERCHANT_STYLESHEET",
    "PALETTE",
    "apply_merchant_theme",
    "ensure_chinese_font",
]
