from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import numpy as np


def _creation_flags() -> int:
    """Return the Windows creation flags used for every FFmpeg child."""
    if os.name != "nt":
        return 0
    # A dedicated process group lets the console stop FFmpeg together with
    # the helper processes FFmpeg starts.
    return int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


@dataclass(frozen=True, slots=True)
class FFmpegInstallation:
    """An FFmpeg toolchain discovered without invoking a shell."""

    executable: Path
    version: str | None = None
    ffprobe: Path | None = None

    def command_for_stream(
        self,
        url: str,
        *,
        fps: float = 2.0,
        width: int | None = None,
        height: int | None = None,
        frames: int | None = None,
        read_timeout: float = 15.0,
    ) -> list[str]:
        """Build an isolated RTSP-over-TCP raw-frame command.

        Pass the returned list to ``subprocess.Popen`` with ``shell=False``.
        Keeping every argument separate prevents a camera URL or credential
        from becoming a command injection vector.
        """
        if not url.lower().startswith(("rtsp://", "rtsps://", "rtp://")):
            raise ValueError("stream URL must use rtsp://, rtsps:// or rtp://")
        if not isfinite(fps) or fps <= 0:
            raise ValueError("fps must be positive")
        if frames is not None and frames < 1:
            raise ValueError("frames must be positive when provided")
        if not isfinite(read_timeout) or read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        filters = [f"fps={fps:g}"]
        if width is not None or height is not None:
            if not width or not height or width <= 0 or height <= 0:
                raise ValueError("width and height must both be positive")
            filters.insert(0, f"scale={width}:{height}")
        command = [str(self.executable), "-hide_banner", "-loglevel", "warning"]
        if url.lower().startswith(("rtsp://", "rtsps://")):
            command.extend(
                [
                    "-rtsp_transport",
                    "tcp",
                    "-timeout",
                    str(max(1, round(read_timeout * 1_000_000))),
                ]
            )
        command.extend(
            [
                "-i",
                url,
                "-an",
                "-vf",
                ",".join(filters),
            ]
        )
        if frames is not None:
            command.extend(["-frames:v", str(frames)])
        command.extend(
            [
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "pipe:1",
            ]
        )
        return command

    def command_for_rtsp(
        self,
        url: str,
        *,
        fps: float = 2.0,
        width: int | None = None,
        height: int | None = None,
        frames: int | None = None,
        read_timeout: float = 15.0,
    ) -> list[str]:
        """Backward-compatible name for RTSP and RTP stream commands."""
        return self.command_for_stream(
            url,
            fps=fps,
            width=width,
            height=height,
            frames=frames,
            read_timeout=read_timeout,
        )

    def probe_resolution(self, url: str, *, timeout: float = 10.0) -> tuple[int, int] | None:
        """Return the source video resolution when FFprobe is available."""

        if not self.ffprobe:
            return None
        if not url:
            raise ValueError("RTSP URL is required")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        command = [
            str(self.ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        try:
            payload = json.loads(completed.stdout.decode("utf-8", errors="replace"))
            stream = payload["streams"][0]
            width = int(stream["width"])
            height = int(stream["height"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return (width, height) if width > 0 and height > 0 else None

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
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
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
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg clip encoding timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RuntimeError(f"FFmpeg clip encoding could not start: {exc}") from exc
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"FFmpeg clip encoding failed ({completed.returncode}): {detail}")
    return str(destination)


def encode_raw_image(
    installation: FFmpegInstallation,
    image: np.ndarray,
    output: str | os.PathLike[str],
    *,
    timeout: float = 10.0,
) -> str:
    """Encode one BGR frame as a local PNG without requiring Qt."""

    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive")
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be an HxWx3 BGR array")
    height, width = image.shape[:2]
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(installation.executable),
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
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-vcodec",
        "png",
        str(destination),
    ]
    payload = np.ascontiguousarray(image, dtype=np.uint8).tobytes()
    try:
        completed = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg image encoding timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RuntimeError(f"FFmpeg image encoding could not start: {exc}") from exc
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"FFmpeg image encoding failed ({completed.returncode}): {detail}")
    return str(destination)


def _runtime_roots() -> list[Path]:
    """Return every directory that may hold the bundled FFmpeg tree.

    In a normal checkout the toolchain sits under ``src/third_party``.  A
    PyInstaller one-directory build copies it into ``sys._MEIPASS`` (the
    ``_internal`` folder), and an operator may also drop a bundle next to the
    executable, so all three locations are checked.
    """
    roots: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    if getattr(sys, "frozen", False):
        try:
            roots.append(Path(sys.executable).resolve().parent)
        except (OSError, TypeError, ValueError):
            pass
    roots.append(Path(__file__).resolve().parents[3])
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def find_ffmpeg(explicit: str | os.PathLike[str] | None = None) -> FFmpegInstallation | None:
    """Find the supplied bundle, an explicit path, or a PATH executable."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.environ.get("FACTORYGUARD_FFMPEG")
    if configured:
        candidates.append(Path(configured))
    # The bundled toolchain lives under ``src/third_party`` in a source
    # checkout.  A PyInstaller bundle places the same tree next to the module
    # inside ``sys._MEIPASS``, so every runtime root is searched.
    for root in _runtime_roots():
        candidates.extend(sorted((root / "src" / "third_party").glob("ffmpeg-*/bin/ffmpeg.exe")))
        candidates.extend(sorted((root / "third_party").glob("ffmpeg-*/bin/ffmpeg.exe")))
        candidates.extend(sorted(root.glob("ffmpeg-*/bin/ffmpeg.exe")))
    path_candidate = shutil.which("ffmpeg")
    if path_candidate:
        candidates.append(Path(path_candidate))
    for candidate in candidates:
        executable = _resolve_executable(candidate)
        if executable is None:
            continue
        ffprobe = executable.with_name("ffprobe.exe")
        return FFmpegInstallation(
            executable, _version(executable), ffprobe if ffprobe.exists() else None
        )
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
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = (completed.stdout or completed.stderr).splitlines()
    return first_line[0] if first_line else None
