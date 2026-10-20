"""Optional Qt desktop control panel.

The core and command-line capture paths do not import Qt.  Install the GUI
extra from ``requirements.txt`` (or ``pip install .[gui]``) when the desktop
panel is needed.
"""

from .qt import qt_available

__all__ = ["qt_available"]
