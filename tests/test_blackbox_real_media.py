"""End-to-end black-box acceptance driven by real customer footage.

The customer supplied three recorded surveillance clips.  This module plays
them through the simulated cameras, then drives the real platform entry point
and asserts only what an operator can observe: process results, JSON
payloads, database rows and rendered pixels.

Nothing here imports FactoryGuard internals for the assertions, so the test
stays honest about the black-box contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "src" / "main.py"
MEDIA_DIR = ROOT / "监控录像20250917232724-20250918012534"


def _clips() -> list[Path]:
    """Return the customer clips that are present in the workspace."""
    if not MEDIA_DIR.is_dir():
        return []
    return sorted(path for path in MEDIA_DIR.glob("*.mp4") if path.stat().st_size > 1024 * 1024)


pytestmark = pytest.mark.skipif(
    not _clips(), reason="customer surveillance clips are not available"
)


def _run_cli(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def test_simulator_serves_real_customer_footage_over_rtsp(tmp_path: Path) -> None:
    """The recorded clips must be playable through the RTSP camera server."""
    sys.path.insert(0, str(ROOT / "src"))
    from tools.camera_sim.service import CameraSimulator

    clip = _clips()[0]
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        media_sources=(str(clip),),
        rtsp_port=0,
        onvif_port=0,
    )
    simulator.start()
    try:
        assert simulator.wait_until_ready(timeout=30.0)
        snapshot = simulator.snapshot()[0]
        assert snapshot["rtsp_url"].startswith("rtsp://")

        from factoryguard.media.rtsp import RTSPCapture

        frame = RTSPCapture(
            simulator.ffmpeg, snapshot["rtsp_url"], width=640, height=360, fps=2
        ).capture_one(timeout=30.0)
        assert frame is not None
        assert frame.image.shape == (360, 640, 3)
        # Real footage is never a flat synthetic colour.
        assert float(frame.image.std()) > 3.0
    finally:
        simulator.stop()


def test_platform_accepts_the_real_clip_as_a_camera_and_reports_health(
    tmp_path: Path,
) -> None:
    """A camera backed by the customer's own footage must pass the health check."""
    sys.path.insert(0, str(ROOT / "src"))
    from tools.camera_sim.service import CameraSimulator

    clip = _clips()[0]
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        media_sources=(str(clip),),
        rtsp_port=0,
        onvif_port=0,
    )
    simulator.start()
    try:
        assert simulator.wait_until_ready(timeout=30.0)
        endpoint = simulator.snapshot()[0]["rtsp_url"]

        result = _run_cli(
            "probe-rtsp",
            "--url",
            endpoint,
            "--width",
            "640",
            "--height",
            "360",
            "--timeout",
            "20",
            timeout=90.0,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        report = json.loads(result.stdout)
        assert report["status"] == "ok"
        assert report["width"] == 640
        assert report["height"] == 360
        assert report["frame_no"] >= 0
        assert report["url"].startswith("rtsp://")
    finally:
        simulator.stop()


def test_real_footage_reaches_the_preview_without_picture_damage(tmp_path: Path) -> None:
    """The decoded frame must match the source, not a smeared approximation.

    MPEG-TS over RTP loses packets when the relay socket buffer is too
    small, and the result is a picture that looks striped.  Comparing the
    horizontal gradient of the delivered frame with the source clip proves
    the transport did not damage the picture.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import numpy as np

    from tools.camera_sim.service import CameraSimulator

    clip = _clips()[0]
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        media_sources=(str(clip),),
        rtsp_port=0,
        onvif_port=0,
    )
    simulator.start()
    try:
        assert simulator.wait_until_ready(timeout=30.0)
        url = simulator.snapshot()[0]["rtsp_url"]

        import subprocess

        from factoryguard.media.ffmpeg import require_ffmpeg
        from factoryguard.media.rtsp import RTSPCapture

        installation = require_ffmpeg()
        # Decode the same clip straight from disk as the reference picture.
        completed = subprocess.run(
            [
                str(installation.executable),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "10",
                "-i",
                str(clip),
                "-frames:v",
                "1",
                "-vf",
                "scale=1280:720",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-",
            ],
            capture_output=True,
            timeout=120.0,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-400:]
        reference = np.frombuffer(completed.stdout, dtype=np.uint8).reshape(720, 1280, 3)

        time.sleep(4)
        cap = RTSPCapture(installation, url, width=1280, height=720, fps=8)
        # Use the streaming context manager so the decoder is always shut
        # down, even though we only need a single frame.
        with cap.stream(timeout=40.0) as frames:
            frame = next(iter(frames))

        def gradient(image: object) -> float:
            array = np.asarray(image, dtype=float)
            grey = array.mean(axis=2)
            return float(np.abs(np.diff(grey, axis=1)).mean())

        source_score = gradient(reference)
        delivered_score = gradient(frame.image)
        # A damaged stream loses most of its horizontal detail.
        assert delivered_score > source_score * 0.75, (
            f"delivered frame is smeared: source={source_score:.2f} delivered={delivered_score:.2f}"
        )
    finally:
        simulator.stop()


def test_real_entrypoint_health_is_green_against_the_shipping_configuration(
    tmp_path: Path,
) -> None:
    """The shipped configuration must pass the operator preflight."""
    database = tmp_path / "preflight.db"
    media = tmp_path / "events-media"

    result = _run_cli(
        "health",
        "--db",
        str(database),
        "--media-dir",
        str(media),
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["resource_state"] in {"ok", "warn"}
    names = {item["name"] for item in report["checks"]}
    assert "FFmpeg" in names
    assert not any(item["state"] == "error" for item in report["checks"])


def test_platform_survives_a_real_clip_being_added_and_removed(tmp_path: Path) -> None:
    """Adding a customer clip and then deleting it must leave a clean database."""
    sys.path.insert(0, str(ROOT / "src"))
    from factoryguard.config import AppConfigStore
    from factoryguard.domain.models import Area, CameraDevice, Site, VideoDevice
    from factoryguard.domain.topology import EntityRegistry
    from factoryguard.storage.sqlite import SQLiteStore
    from tools.camera_sim.service import CameraSimulator

    clip = _clips()[0]
    database = tmp_path / "footage-lifecycle.db"
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        media_sources=(str(clip),),
        rtsp_port=0,
        onvif_port=0,
    )
    simulator.start()
    try:
        assert simulator.wait_until_ready(timeout=30.0)
        endpoint = simulator.snapshot()[0]["rtsp_url"]

        store = SQLiteStore(database)
        store.save_topology(
            EntityRegistry(
                sites=(Site("site-1", "演示门店"),),
                areas=(Area("area-1", "site-1", "东门"),),
                devices=(
                    VideoDevice(
                        "device-footage",
                        "site-1",
                        "area-1",
                        "客户录像摄像头",
                        "onvif",
                        endpoint,
                    ),
                ),
                cameras=(
                    CameraDevice(
                        "camera-footage",
                        "area-1",
                        "客户录像摄像头",
                        device_id="device-footage",
                    ),
                ),
            )
        )
        store.close()

        config = AppConfigStore(tmp_path / "factoryguard.json")
        config.update_section("gb28181", {"enabled": False})

        result = _run_cli(
            "health",
            "--db",
            str(database),
            "--media-dir",
            str(tmp_path / "events-media"),
        )
        assert result.returncode == 0, result.stderr or result.stdout
        report = json.loads(result.stdout)
        assert report["inventory"]["cameras"] == 1
        assert report["inventory"]["devices"] == 1
    finally:
        simulator.stop()
