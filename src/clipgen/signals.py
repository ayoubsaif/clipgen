"""Cheap audio evidence for moments the transcript alone would miss, such as shouting."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .tools import ffmpeg_path

SAMPLE_RATE = 8000


def loudness_per_second(media: Path) -> np.ndarray:
    command = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(media),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    values: list[float] = []
    assert process.stdout is not None
    while chunk := process.stdout.read(SAMPLE_RATE * 2):
        samples = np.frombuffer(chunk[: len(chunk) - len(chunk) % 2], dtype=np.int16).astype(np.float64)
        rms = np.sqrt(np.mean(samples**2)) if samples.size else 0.0
        values.append(20 * np.log10(max(rms, 1.0) / 32768))
    process.wait()
    return np.array(values)


def energy_bonus(loudness: np.ndarray, start: float, end: float, cap: float = 10.0) -> float:
    """Return up to `cap` points for a window whose peak is well above the median level."""
    if loudness.size == 0:
        return 0.0
    first, last = max(int(start), 0), min(int(end) + 1, loudness.size)
    if first >= last:
        return 0.0
    over_median = float(loudness[first:last].max() - np.median(loudness))
    return float(np.clip(over_median - 6.0, 0.0, cap))
