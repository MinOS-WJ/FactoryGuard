from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import tempfile
from contextlib import contextmanager
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


def _redact_credentials(value: str) -> str:
    return re.sub(r"((?:rtsp|rtsps|https?)://)[^/@\s]+@", r"\1***:***@", value, flags=re.IGNORECASE)

def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _read_exact(stream: object, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _terminate_process(process: object) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

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

    @contextmanager
    def stream(self, *, timeout: float = 20.0) -> Iterator[Iterator[RawFrame]]:
        """Keep one FFmpeg process open and yield frames until it exits."""
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        command = self.installation.command_for_rtsp(
            self.url,
            fps=self.fps,
            width=self.width,
            height=self.height,
            read_timeout=timeout,
        )
        stderr_file = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                bufsize=0,
                shell=False,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            stderr_file.close()
            raise RuntimeError(f"FFmpeg could not be started: {exc}") from exc
        if process.stdout is None:
            _terminate_process(process)
            raise RuntimeError("FFmpeg did not expose a stdout pipe")

        frame_queue: queue.Queue[tuple[bytes | None, Exception | None]] = queue.Queue(maxsize=2)
        stop_reader = threading.Event()

        def post(item: tuple[bytes | None, Exception | None]) -> bool:
            while not stop_reader.is_set():
                try:
                    frame_queue.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def read_frames() -> None:
            try:
                while not stop_reader.is_set():
                    payload = _read_exact(process.stdout, self.frame_bytes)
                    if payload is None:
                        post((None, None))
                        return
                    if not post((payload, None)):
                        return
            except Exception as exc:  # noqa: BLE001 - forward reader failures to the consumer
                post((None, exc))

        reader = threading.Thread(target=read_frames, name="rtsp-frame-reader", daemon=True)
        reader.start()

        def frames() -> Iterator[RawFrame]:
            frame_no = 0
            while True:
                try:
                    payload, read_error = frame_queue.get(timeout=timeout)
                except queue.Empty as exc:
                    raise RuntimeError(f"FFmpeg RTSP read timed out after {timeout:g}s") from exc
                if read_error is not None:
                    raise read_error
                if payload is None:
                    if process.poll() not in (None, 0):
                        stderr_file.seek(0)
                        detail = stderr_file.read().decode("utf-8", errors="replace")[-1000:]
                        raise RuntimeError(f"FFmpeg RTSP stream ended ({process.returncode}): {_redact_credentials(detail)}")
                    break
                yield RawFrame(
                    image=np.frombuffer(payload, dtype=np.uint8).reshape((self.height, self.width, 3)),
                    frame_no=frame_no,
                )
                frame_no += 1

        try:
            yield frames()
        finally:
            stop_reader.set()
            _terminate_process(process)
            try:
                process.stdout.close()
            except (OSError, ValueError):
                pass
            reader.join(timeout=2.0)
            stderr_file.close()
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
                creationflags=_creation_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"FFmpeg RTSP capture timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise RuntimeError(f"FFmpeg could not be started: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"FFmpeg RTSP capture failed ({completed.returncode}): {_redact_credentials(detail)}")
        if len(completed.stdout) < self.frame_bytes:
            raise RuntimeError(
                f"FFmpeg returned {len(completed.stdout)} bytes; expected {self.frame_bytes}"
            )
        image = np.frombuffer(completed.stdout[: self.frame_bytes], dtype=np.uint8).reshape(
            (self.height, self.width, 3)
        )
        return RawFrame(image=image, frame_no=0)

    def capture(self, frames: int, *, timeout: float = 30.0) -> Iterator[RawFrame]:
        """Capture a finite sequence incrementally from one long-lived process."""

        if frames < 1:
            raise ValueError("frames must be positive")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        received = 0
        with self.stream(timeout=timeout) as raw_frames:
            for raw_frame in raw_frames:
                yield raw_frame
                received += 1
                if received >= frames:
                    break
        if received != frames:
            raise RuntimeError(f"FFmpeg returned {received} frames; expected {frames}")