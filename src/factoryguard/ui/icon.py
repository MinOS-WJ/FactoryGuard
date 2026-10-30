"""Application icon selection for window, taskbar and tray surfaces."""

from __future__ import annotations

from pathlib import Path

from .qt import HAS_QT

_ASSET_DIR = Path(__file__).resolve().parent / "assets"

if HAS_QT:
    from .qt import QIcon


def application_icon(theme_name: str = "浅色"):
    """Return a theme-readable application icon, or ``None`` without Qt."""
    if not HAS_QT:
        return None
    filename = "app-icon-dark.svg" if theme_name == "深色" else "app-icon.svg"
    return QIcon(str(_ASSET_DIR / filename))


__all__ = ["application_icon"]