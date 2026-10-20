from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Iterable

import numpy as np


@dataclass(frozen=True, slots=True)
class FFmpegInstallation:
    """An FFmpeg toolchain discovered without invoking a shell."""

    executable: Path
    version: str | None = None
    ffprobe: Path | None = None

    def command_for_rtsp(
        self,
        url: str,
        *,
        fps: float = 2.0,
        width: int | None = None,
        height: int | None = None,
        frames: int | None = None,
    ) -> list[str]:
        """Build an isolated RTSP-over-TCP raw-frame command.

        Pass the returned list to ``subprocess.Popen`` with ``shell=False``.
        Keeping every argument separate prevents a camera URL or credential
        from becoming a command injection vector.
        """
        if not url:
            raise ValueError("RTSP URL is required")
        if not isfinite(fps) or fps <= 0:
            raise ValueError("fps must be positive")
        if frames is not None and frames < 1:
            raise ValueError("frames must be positive when provided")
        filters = [f"fps={fps:g}"]
        if width is not None or height is not None:
            if not width or not height or width <= 0 or height <= 0:
                raise ValueError("width and height must both be positive")
            filters.insert(0, f"scale={width}:{height}")
        command = [
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-an",
            "-vf",
            ",".join(filters),
        ]
        if frames is not None:
            command.extend(["-frames:v", str(frames)])
        command.extend([
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ])
        return command

    def command_for_rawvideo(
        self,
        output: str | os.PathLike[str],
        *,
        width: int,
        height: int,
        fps: float,
        frames: int | None = None,
    ) -> list[str]:
        """Build a safe raw BGR frame-to-MP4 command for event clips."""
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        if not isfinite(fps) or fps <= 0:
            raise ValueError("fps must be positive")
        if frames is not None and frames < 1:
            raise ValueError("frames must be positive when provided")
        destination = Path(output).expanduser()
        if not destination.name:
            raise ValueError("clip output path is required")
        command = [
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            f"{fps:g}",
            "-i",
            "pipe:0",
            "-an",
        ]
        if frames is not None:
            command.extend(["-frames:v", str(frames)])
        command.extend([
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ])
        return command


def encode_raw_clip(
    installation: FFmpegInstallation,
    frames: Iterable[np.ndarray],
    output: str | os.PathLike[str],
    *,
    width: int,
    height: int,
    fps: float,
    timeout: float = 30.0,
) -> str:
    """Encode BGR frames into a local MP4 clip and return its absolute path."""
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive")
    packed: list[bytes] = []
    for frame in frames:
        if not isinstance(frame, np.ndarray) or frame.shape != (height, width, 3):
            raise ValueError("clip frames must all be HxWx3 arrays matching the configured size")
        packed.append(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
    if not packed:
        raise ValueError("at least one frame is required for a clip")
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = installation.command_for_rawvideo(
        destination,
        width=width,
        height=height,
        fps=fps,
        frames=len(packed),
    )
    try:
        completed = subprocess.run(
            command,
            input=b"".join(packed),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg clip encoding timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RuntimeError(f"FFmpeg clip encoding could not start: {exc}") from exc
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"FFmpeg clip encoding failed ({completed.returncode}): {detail}")
    return str(destination)


def find_ffmpeg(explicit: str | os.PathLike[str] | None = None) -> FFmpegInstallation | None:
    """Find the supplied bundle, an explicit path, or a PATH executable."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.environ.get("FACTORYGUARD_FFMPEG")
    if configured:
        candidates.append(Path(configured))
    repository_root = Path(__file__).resolve().parents[3]
    # The bundled toolchain lives under ``src`` so a source checkout and a
    # packaged application use the same relative layout.  Keep the old root
    # location as a compatibility fallback for existing installations.
    candidates.extend(sorted((repository_root / "src" / "third_party").glob("ffmpeg-*/bin/ffmpeg.exe")))
    candidates.extend(sorted(repository_root.glob("ffmpeg-*/bin/ffmpeg.exe")))
    path_candidate = shutil.which("ffmpeg")
    if path_candidate:
        candidates.append(Path(path_candidate))
    for candidate in candidates:
        executable = _resolve_executable(candidate)
        if executable is None:
            continue
        ffprobe = executable.with_name("ffprobe.exe")
        return FFmpegInstallation(executable, _version(executable), ffprobe if ffprobe.exists() else None)
    return None


def require_ffmpeg(explicit: str | os.PathLike[str] | None = None) -> FFmpegInstallation:
    installation = find_ffmpeg(explicit)
    if installation is None:
        raise FileNotFoundError(
            "FFmpeg was not found; set FACTORYGUARD_FFMPEG or place a bundle under src/third_party/ffmpeg-*/bin"
        )
    return installation


def _resolve_executable(candidate: Path) -> Path | None:
    if candidate.is_file():
        return candidate.resolve()
    resolved = shutil.which(str(candidate))
    return Path(resolved).resolve() if resolved else None


def _version(executable: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = (completed.stdout or completed.stderr).splitlines()
    return first_line[0] if first_line else None
