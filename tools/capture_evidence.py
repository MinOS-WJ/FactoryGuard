"""Capture delivery evidence: page screenshots and a screen recording.

Delivery reviews need visual proof, not just a green test run.  This tool
boots the real merchant console against a seeded demo database, walks every
page, saves a PNG for each one and records the session into a video file a
reviewer can open without any extra tooling.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FACTORYGUARD_DISABLE_TRAY", "1")

PAGES: tuple[tuple[str, str], ...] = (
    ("dashboard", "工作台"),
    ("live", "实时画面"),
    ("incidents", "险情中心"),
    ("devices", "区域与设备"),
    ("assistant", "现场问询"),
    ("settings", "系统设置"),
)


@dataclass(slots=True)
class EvidenceResult:
    page: str
    title: str
    screenshot: str
    width: int
    height: int


def _find_ffmpeg() -> str | None:
    """Locate the bundled FFmpeg binary used by the product."""
    from factoryguard.media.ffmpeg import find_ffmpeg

    installation = find_ffmpeg()
    return str(installation.executable) if installation is not None else None


def _seed_demo(database: Path) -> None:
    """Fill the demo database so the screenshots show real business content."""
    from factoryguard.storage.sqlite import SQLiteStore
    from tools.seed_merchant_demo import seed

    store = SQLiteStore(database)
    try:
        seed(store)
    finally:
        store.close()


def _capture_pages(
    database: Path,
    config_path: Path,
    output_dir: Path,
    size: tuple[int, int],
    *,
    settle_ms: int = 900,
) -> list[EvidenceResult]:
    """Open the real console and save one screenshot per page."""
    from PySide6.QtWidgets import QApplication

    from factoryguard.config import MerchantConfig
    from factoryguard.ui.merchant_console import MerchantMainWindow

    application = QApplication.instance() or QApplication([])
    window = MerchantMainWindow(
        config=MerchantConfig(db=str(database), config_path=str(config_path)),
    )
    window.resize(*size)
    window.show()
    application.processEvents()
    results: list[EvidenceResult] = []
    for key, title in PAGES:
        window._switch_page(key)
        application.processEvents()
        time.sleep(settle_ms / 1000.0)
        application.processEvents()
        image = window.grab()
        target = output_dir / f"page-{key}.png"
        image.save(str(target))
        results.append(
            EvidenceResult(
                page=key,
                title=title,
                screenshot=str(target),
                width=image.width(),
                height=image.height(),
            )
        )
    window.quit_application()
    application.processEvents()
    return results


def _record_frames(frames_dir: Path, video: Path, ffmpeg: str, fps: int) -> bool:
    """Turn the captured PNG frames into a reviewable MP4."""
    pattern = frames_dir / "frame-%04d.png"
    if not any(frames_dir.glob("frame-*.png")):
        return False
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(pattern),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        str(video),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode == 0 and video.exists()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="capture delivery evidence")
    parser.add_argument("--db", default="data/evidence.db")
    parser.add_argument("--config", default="config/evidence.json")
    parser.add_argument("--out", default="docs/testing/evidence")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--fps", type=int, default=4)
    args = parser.parse_args(argv)

    database = Path(args.db)
    config_path = Path(args.config)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not database.exists():
        _seed_demo(database)

    results = _capture_pages(database, config_path, output_dir, (args.width, args.height))

    video_note = ""
    ffmpeg = _find_ffmpeg()
    if ffmpeg is not None:
        # Build the recording from the still frames so the deliverable works
        # on machines without a desktop capture device.
        frames_dir = output_dir / "frames"
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        frames_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(results, start=1):
            source = Path(item.screenshot)
            if source.exists():
                shutil.copyfile(source, frames_dir / f"frame-{index:04d}.png")
        video = output_dir / "console-walkthrough.mp4"
        if _record_frames(frames_dir, video, ffmpeg, args.fps):
            video_note = video.name
            shutil.rmtree(frames_dir, ignore_errors=True)

    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "size": {"width": args.width, "height": args.height},
        "video": video_note,
        "pages": [
            {
                "page": item.page,
                "title": item.title,
                "screenshot": Path(item.screenshot).name,
                "width": item.width,
                "height": item.height,
            }
            for item in results
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EvidenceResult", "main"]
