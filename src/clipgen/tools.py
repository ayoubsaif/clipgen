"""Locate and run ffmpeg. Commands are always argument lists, never shell strings."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


class ToolError(RuntimeError):
    """An external tool failed."""


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


@lru_cache(maxsize=1)
def ffmpeg_dir() -> str:
    """Return a folder holding a standard ffmpeg name, which yt-dlp needs to recognize it."""
    binary = Path(ffmpeg_path())
    if binary.stem.lower() == "ffmpeg":
        return str(binary.parent)
    folder = Path(sys.prefix) / "clipgen-bin"
    folder.mkdir(exist_ok=True)
    target = folder / f"ffmpeg{binary.suffix}"
    if not target.exists() or target.stat().st_size != binary.stat().st_size:
        target.unlink(missing_ok=True)
        try:
            os.link(binary, target)
        except OSError:
            shutil.copy2(binary, target)
    # yt-dlp's partial-download check looks on PATH and ignores ffmpeg_location.
    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
    return str(folder)


@lru_cache(maxsize=1)
def prepare_gpu_libraries() -> tuple[str, ...]:
    """Expose the CUDA DLLs installed in this environment, without changing the system."""
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    folders = tuple(str(p) for p in sorted(root.glob("*/bin"))) if root.exists() else ()
    for folder in folders:
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(folder)
    return folders


def cuda_available() -> bool:
    """Check for the CUDA library on Windows, where a missing DLL is the usual reason for no GPU."""
    if os.name != "nt":
        return True
    prepare_gpu_libraries()
    try:
        ctypes.WinDLL("cublas64_12.dll")
    except OSError:
        return False
    return True


def run_ffmpeg(args: list[str], cwd: Path | None = None, timeout: float | None = None) -> None:
    command = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout
    )
    if result.returncode != 0:
        raise ToolError(f"ffmpeg failed: {result.stderr.strip()[-1500:]}")


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise ToolError(f"Could not read the duration of {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Run a real one-frame encode, because a listed encoder may still lack a usable GPU."""
    try:
        run_ffmpeg(
            ["-f", "lavfi", "-i", "color=black:s=256x256:r=30", "-frames:v", "1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            timeout=30,
        )
    except (ToolError, subprocess.TimeoutExpired):
        return False
    return True


def video_encoder_args() -> list[str]:
    if nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", "21", "-b:v", "0", "-maxrate", "12M", "-bufsize", "24M"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
