from __future__ import annotations

import subprocess
from dataclasses import dataclass
from math import isfinite
from typing import Iterator

import numpy as np

from .ffmpeg import FFmpegInstallation


@dataclass(frozen=True, slots=True)
class RawFrame:
    """A BGR frame emitted by FFmpeg's rawvideo pipe."""

    image: np.ndarray
    frame_no: int


class RTSPCapture:
    """Small bounded FFmpeg capture adapter for RTSP smoke tests."""

    def __init__(
        self,
        installation: FFmpegInstallation,
        url: str,
        *,
        width: int,
        height: int,
        fps: float = 2.0,
    ) -> None:
        if not url:
            raise ValueError("RTSP URL is required")
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        if not isfinite(fps) or fps <= 0:
            raise ValueError("fps must be positive")
        self.installation = installation
        self.url = url
        self.width = width
        self.height = height
        self.fps = fps

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3

    def capture_one(self, *, timeout: float = 20.0) -> RawFrame:
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        command = self.installation.command_for_rtsp(
            self.url,
            fps=self.fps,
            width=self.width,
            height=self.height,
            frames=1,
        )
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"FFmpeg RTSP capture timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise RuntimeError(f"FFmpeg could not be started: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"FFmpeg RTSP capture failed ({completed.returncode}): {detail}")
        if len(completed.stdout) < self.frame_bytes:
            raise RuntimeError(
                f"FFmpeg returned {len(completed.stdout)} bytes; expected {self.frame_bytes}"
            )
        image = np.frombuffer(completed.stdout[: self.frame_bytes], dtype=np.uint8).reshape(
            (self.height, self.width, 3)
        )
        return RawFrame(image=image, frame_no=0)

    def capture(self, frames: int, *, timeout: float = 30.0) -> Iterator[RawFrame]:
        """Capture a finite sequence in one FFmpeg process."""
        if frames < 1:
            raise ValueError("frames must be positive")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        command = self.installation.command_for_rtsp(
            self.url,
            fps=self.fps,
            width=self.width,
            height=self.height,
            frames=frames,
        )
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"FFmpeg RTSP capture timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise RuntimeError(f"FFmpeg could not be started: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"FFmpeg RTSP capture failed ({completed.returncode}): {detail}")
        received = 0
        for frame_no in range(frames):
            start = frame_no * self.frame_bytes
            chunk = completed.stdout[start : start + self.frame_bytes]
            if len(chunk) < self.frame_bytes:
                break
            received += 1
            yield RawFrame(
                image=np.frombuffer(chunk, dtype=np.uint8).reshape((self.height, self.width, 3)),
                frame_no=frame_no,
            )
        if received != frames:
            raise RuntimeError(
                f"FFmpeg returned {received} frames; expected {frames}"
            )
