"""Record the platform decoding the customer's own surveillance footage.

This produces the strongest delivery evidence available: the real merchant
console, the real FFmpeg decoder and the real customer clip, captured as a
video file.  The recording shows the live view filling in with the actual
footage, which proves the whole access chain end to end.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FACTORYGUARD_DISABLE_TRAY", "1")

MEDIA_DIR = ROOT / "监控录像20250917232724-20250918012534"


def _clips() -> list[Path]:
    if not MEDIA_DIR.is_dir():
        return []
    return sorted(path for path in MEDIA_DIR.glob("*.mp4") if path.stat().st_size > 1024 * 1024)


def _ffmpeg() -> str | None:
    from factoryguard.media.ffmpeg import find_ffmpeg

    installation = find_ffmpeg()
    return str(installation.executable) if installation is not None else None


def _capture_live_frames(
    clip: Path,
    frames_dir: Path,
    *,
    fps: int,
    seconds: int,
) -> int:
    """Open the real preview dialog on the real clip and grab every frame."""
    from PySide6.QtWidgets import QApplication

    from factoryguard.ui.merchant_dialogs import LivePreviewDialog
    from factoryguard.ui.merchant_theme import apply_merchant_theme
    from tools.camera_sim.service import CameraSimulator

    application = QApplication.instance() or QApplication([])
    apply_merchant_theme(application)

    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        media_sources=(str(clip),),
        rtsp_port=0,
        onvif_port=0,
    )
    simulator.start()
    captured = 0
    try:
        if not simulator.wait_until_ready(timeout=30.0):
            raise RuntimeError("the camera simulator did not become ready")
        url = simulator.snapshot()[0]["rtsp_url"]

        dialog = LivePreviewDialog(title=clip.stem, url=url)
        dialog.resize(1120, 720)
        dialog.show()
        application.processEvents()

        deadline = time.monotonic() + seconds
        index = 0
        while time.monotonic() < deadline:
            application.processEvents()
            image = dialog.grab()
            index += 1
            image.save(str(frames_dir / f"frame-{index:04d}.png"))
            captured += 1
            time.sleep(1.0 / fps)

        dialog.close()
        application.processEvents()
    finally:
        simulator.stop()
    return captured


def _encode(frames_dir: Path, video: Path, ffmpeg: str, fps: int) -> bool:
    pattern = frames_dir / "frame-%04d.png"
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
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        str(video),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode == 0 and video.exists()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="record the live view on real footage")
    parser.add_argument("--out", default="docs/testing/evidence")
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--seconds", type=int, default=12)
    args = parser.parse_args(argv)

    clips = _clips()
    if not clips:
        print("no customer clips found")
        return 2

    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "live-frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    clip = clips[0]
    captured = _capture_live_frames(clip, frames_dir, fps=args.fps, seconds=args.seconds)

    ffmpeg = _ffmpeg()
    video = output_dir / "live-view-real-footage.mp4"
    ok = False
    if ffmpeg is not None:
        ok = _encode(frames_dir, video, ffmpeg, args.fps)
    shutil.rmtree(frames_dir, ignore_errors=True)

    summary = {
        "clip": clip.name,
        "frames": captured,
        "video": video.name if ok else "",
        "fps": args.fps,
        "seconds": args.seconds,
    }
    (output_dir / "footage-recording.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
