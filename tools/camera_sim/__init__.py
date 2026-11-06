"""FactoryGuard multi-camera simulator for ONVIF, GB/T 28181 and RTSP tests."""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from .service import CameraSimulator, SimulatedCamera

__all__ = ["CameraSimulator", "SimulatedCamera"]
