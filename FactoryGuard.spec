# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller one-directory build for the FactoryGuard merchant console.

Everything the shipped product needs is copied into the bundle: the Qt UI
assets, the bundled FFmpeg toolchain, the shipping configuration example and
the delivery documentation.  Nothing is trimmed away.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()


def _data(source: Path, destination: str) -> tuple[str, str]:
    """Return one ``--add-data`` pair in PyInstaller tuple form."""
    if not source.exists():
        raise SystemExit(f"missing build input: {source}")
    return (str(source), destination)


FFMPEG_DIR = ROOT / "src" / "third_party" / "ffmpeg-9.0.2-full_build"

datas = [
    _data(ROOT / "src" / "factoryguard" / "ui" / "assets", "factoryguard/ui/assets"),
    _data(FFMPEG_DIR, "src/third_party/ffmpeg-9.0.2-full_build"),
    _data(ROOT / "config" / "factoryguard.example.json", "config"),
    _data(ROOT / "docs", "docs"),
    _data(ROOT / "README.md", "."),
    _data(ROOT / "LICENSE", "."),
    _data(ROOT / "requirements.txt", "."),
]

hiddenimports = [
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "factoryguard.ui.merchant_console",
    "factoryguard.ui.merchant_dialogs",
    "factoryguard.ui.merchant_theme",
    "factoryguard.ui.merchant_widgets",
    "factoryguard.ui.tray",
]

a = Analysis(
    [str(ROOT / "src" / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FactoryGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "src" / "factoryguard" / "ui" / "assets" / "app-icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FactoryGuard",
)
