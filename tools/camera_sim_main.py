"""Standalone entry point for the packaged camera simulator."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _locate_packaged_ffmpeg() -> str:
    """Find FFmpeg inside the delivered test package.

    The simulator ships without its own FFmpeg copy; the launchers point it
    at the copy bundled with the application under test.
    """
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
    else:
        here = Path(__file__).resolve().parent
    package_root = here.parent
    candidates: list[Path] = []
    candidates.extend(
        package_root.glob("main-software/_internal/src/third_party/ffmpeg-*/bin/ffmpeg.exe")
    )
    candidates.extend(package_root.glob("**/ffmpeg.exe"))
    candidates.extend(here.glob("**/ffmpeg.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def main(argv: list[str] | None = None) -> int:
    if not os.environ.get("FACTORYGUARD_FFMPEG"):
        found = _locate_packaged_ffmpeg()
        if found:
            os.environ["FACTORYGUARD_FFMPEG"] = found
    from tools.camera_sim.cli import main as simulator_main

    return simulator_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
