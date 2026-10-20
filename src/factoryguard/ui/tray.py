from __future__ import annotations

from collections.abc import Callable

from .qt import HAS_QT

if HAS_QT:
    from .qt import QAction, QApplication, QIcon, QMenu, QStyle, QSystemTrayIcon


if HAS_QT:

    class TrayController:
        """System tray menu shared by normal and tray-only launches."""

        def __init__(
            self,
            *,
            on_show: Callable[[], None],
            on_start: Callable[[], None],
            on_stop: Callable[[], None],
            on_quit: Callable[[], None],
        ) -> None:
            self._on_quit = on_quit
            self.icon = QSystemTrayIcon()
            self.icon.setIcon(QApplication.style().standardIcon(QStyle.SP_ComputerIcon) or QIcon())
            self.icon.setToolTip("FactoryGuard · RTSP 监控")
            menu = QMenu()
            self.show_action = QAction("显示控制台", menu)
            self.start_action = QAction("启动监控", menu)
            self.stop_action = QAction("停止监控", menu)
            menu.addAction(self.show_action)
            menu.addSeparator()
            menu.addAction(self.start_action)
            menu.addAction(self.stop_action)
            menu.addSeparator()
            quit_action = QAction("退出 FactoryGuard", menu)
            menu.addAction(quit_action)
            self.icon.setContextMenu(menu)
            self.show_action.triggered.connect(on_show)
            self.start_action.triggered.connect(on_start)
            self.stop_action.triggered.connect(on_stop)
            quit_action.triggered.connect(on_quit)
            self.icon.activated.connect(self._activated)

        def show(self) -> None:
            self.icon.show()

        def hide(self) -> None:
            self.icon.hide()

        def notify(self, title: str, message: str) -> None:
            if self.icon.isVisible():
                self.icon.showMessage(title, message, QSystemTrayIcon.Information, 4000)

        def set_running(self, running: bool) -> None:
            self.start_action.setEnabled(not running)
            self.stop_action.setEnabled(running)

        def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
            if reason in (
                QSystemTrayIcon.Trigger,
                QSystemTrayIcon.DoubleClick,
            ):
                self.show_action.trigger()

else:

    class TrayController:  # pragma: no cover - only used without Qt
        def __init__(self, **_: object) -> None:
            raise RuntimeError("PySide6 is required for the system tray")


__all__ = ["TrayController"]
