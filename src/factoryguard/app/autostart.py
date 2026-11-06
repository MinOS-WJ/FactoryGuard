"""Windows autostart support for the merchant console.

Autostart is implemented with a shortcut in the current user's Startup folder.
That needs no administrator rights, does not touch the registry and is easy to
remove again, which fits small local installations.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

AUTOSTART_NAME = "FactoryGuard 智守"
_SHORTCUT_SUFFIX = ".lnk"


@dataclass(frozen=True, slots=True)
class AutostartStatus:
    """Describe whether the console starts automatically for the current user."""

    supported: bool
    enabled: bool
    location: str
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "enabled": self.enabled,
            "location": self.location,
            "detail": self.detail,
        }


def startup_folder() -> Path:
    """Return the current user's Startup folder."""
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise RuntimeError("APPDATA is not available")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_path() -> Path:
    """Return the shortcut path used to start the console at logon."""
    return startup_folder() / f"{AUTOSTART_NAME}{_SHORTCUT_SUFFIX}"


def is_supported() -> bool:
    """Autostart is only offered on Windows desktops."""
    return sys.platform == "win32"


def describe() -> AutostartStatus:
    """Report the current autostart state without changing anything."""
    if not is_supported():
        return AutostartStatus(
            supported=False,
            enabled=False,
            location="",
            detail="开机自启动仅支持 Windows 系统。",
        )
    try:
        path = autostart_path()
    except RuntimeError as exc:
        return AutostartStatus(False, False, "", str(exc))
    return AutostartStatus(
        supported=True,
        enabled=path.is_file(),
        location=str(path),
        detail="已设置开机自启动。" if path.is_file() else "尚未设置开机自启动。",
    )


def enable(
    *,
    python_executable: str | None = None,
    entrypoint: str | Path | None = None,
    arguments: tuple[str, ...] = ("tray",),
) -> AutostartStatus:
    """Create the Startup shortcut so the console runs in the background."""
    if not is_supported():
        raise RuntimeError("开机自启动仅支持 Windows 系统。")
    executable = python_executable or sys.executable
    if not executable:
        raise RuntimeError("无法确定程序路径。")
    if getattr(sys, "frozen", False):
        # A packaged build is a single executable, so the shortcut runs it
        # directly instead of pointing at a Python script.
        command = " ".join([f'"{executable}"', *[f'"{item}"' for item in arguments]])
    else:
        script = Path(entrypoint) if entrypoint is not None else _default_entrypoint()
        if not script.is_file():
            raise RuntimeError(f"未找到程序入口：{script}")
        command = " ".join([f'"{executable}"', f'"{script}"', *[f'"{item}"' for item in arguments]])
    target = autostart_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A packaged build stores its data next to the executable, so the
    # shortcut must start in that folder rather than wherever it was created.
    working_dir = str(Path(executable).resolve().parent) if getattr(sys, "frozen", False) else None
    if working_dir:
        _write_shortcut(target, command, working_dir=working_dir)
    else:
        _write_shortcut(target, command)
    return describe()


def disable() -> AutostartStatus:
    """Remove the Startup shortcut if it exists."""
    if not is_supported():
        raise RuntimeError("开机自启动仅支持 Windows 系统。")
    try:
        path = autostart_path()
    except RuntimeError:
        return describe()
    if path.exists():
        path.unlink()
    return describe()


def _default_entrypoint() -> Path:
    """Locate ``src/main.py`` relative to the installed package."""
    candidate = Path(__file__).resolve().parents[2] / "main.py"
    if candidate.is_file():
        return candidate
    return Path("src") / "main.py"


def _write_shortcut(target: Path, command: str, *, working_dir: str | None = None) -> None:
    """Create a Windows shortcut using PowerShell when it is available."""
    if shutil.which("powershell") is None:
        raise RuntimeError("需要 Windows PowerShell 才能创建开机自启动项。")
    working_directory = working_dir or "(Get-Location).Path"
    script = "; ".join(
        (
            "$shell = New-Object -ComObject WScript.Shell",
            f"$shortcut = $shell.CreateShortcut('{target}')",
            f"$shortcut.TargetPath = '{command}'",
            f"$shortcut.WorkingDirectory = '{working_directory}'",
            "$shortcut.Description = 'FactoryGuard merchant console'",
            "$shortcut.WindowStyle = 7",
            "$shortcut.Save()",
        )
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        raise RuntimeError(f"开机自启动项创建失败：{detail or '未知错误'}")
    if not target.is_file():
        raise RuntimeError("开机自启动项创建后未找到快捷方式文件。")


__all__ = [
    "AUTOSTART_NAME",
    "AutostartStatus",
    "autostart_path",
    "describe",
    "disable",
    "enable",
    "is_supported",
    "startup_folder",
]
