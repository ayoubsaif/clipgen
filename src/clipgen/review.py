"""Read finished runs and save a reviewer's decisions and text edits."""

from __future__ import annotations

import json
from pathlib import Path

from .jobs import _write_json
from .models import Clip, Transcript
from .pipeline import safe_name

EDITABLE = ("title", "hook", "tiktok_caption", "instagram_caption")
DECISIONS = ("pending", "approved", "rejected")
MIN_CAPTION_CLIP = 1.0
MAX_CAPTION_CLIP = 180.0


def load_transcript(run: Path) -> Transcript:
    return Transcript.from_dict(json.loads((run / "transcript.json").read_text(encoding="utf-8")))


def caption_span(transcript: Transcript, first: int, last: int) -> tuple[float, float, str]:
    """Return safe clip bounds and text for a range of caption lines."""
    segments = transcript.segments
    if not 0 <= first <= last < len(segments):
        raise ValueError("Select caption lines from this video.")
    chosen = segments[first : last + 1]
    words = [word for segment in chosen for word in segment.words]
    if not words:
        raise ValueError("The selected lines contain no timed words.")
    floor = segments[first - 1].end if first else 0.0
    ceiling = segments[last + 1].start if last + 1 < len(segments) else transcript.duration
    start = max(words[0].start - 0.1, min(floor, words[0].start), 0.0)
    end = min(words[-1].end + 0.25, max(ceiling, words[-1].end), transcript.duration)
    text = " ".join(segment.text.strip() for segment in chosen)
    return round(start, 3), round(end, 3), text


def add_clip_from_captions(run: Path, first: int, last: int, title: str = "", hook: str = "") -> int:
    """Add a clip covering the selected caption lines and return its number."""
    start, end, text = caption_span(load_transcript(run), first, last)
    if not MIN_CAPTION_CLIP <= end - start <= MAX_CAPTION_CLIP:
        raise ValueError(f"Select between {MIN_CAPTION_CLIP:.0f} and {MAX_CAPTION_CLIP:.0f} seconds of captions.")
    manifest = load_manifest(run)
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    clip_title = title.strip() or text[:70].strip()
    clip = Clip(start, end, 0.0, clip_title[:100], hook.strip()[:80], "Selected by hand from the captions.",
                text[:150].strip(), text[:150].strip())
    number = len(selection) + 1
    selection.append(clip.to_dict())
    manifest["clips"].append(clip.to_dict() | {"number": number, "file": None})
    _write_json(run / "selection.json", selection)
    _write_json(run / "manifest.json", manifest)
    return number


def word_safe_bounds(transcript: Transcript, start: float, end: float) -> tuple[float, float]:
    """Keep exact requested times, widening only when they would cut through a word."""
    start, end = max(start, 0.0), min(end, transcript.duration)
    for word in transcript.words:
        if word.start < start < word.end:
            start = word.start
        if word.start < end < word.end:
            end = word.end
    if not MIN_CAPTION_CLIP <= end - start <= MAX_CAPTION_CLIP:
        raise ValueError(f"Clips must last between {MIN_CAPTION_CLIP:.0f} and {MAX_CAPTION_CLIP:.0f} seconds.")
    return round(start, 3), round(end, 3)


def set_clip_times(run: Path, number: int, start: float, end: float) -> None:
    start, end = word_safe_bounds(load_transcript(run), start, end)
    manifest = load_manifest(run)
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    if not 1 <= number <= len(selection):
        raise ValueError(f"Clip {number} does not exist.")
    selection[number - 1].update(start=start, end=end)
    next(clip for clip in manifest["clips"] if clip["number"] == number).update(start=start, end=end)
    _write_json(run / "selection.json", selection)
    _write_json(run / "manifest.json", manifest)


def add_clip_at(run: Path, start: float, end: float, title: str = "", hook: str = "") -> int:
    """Add a clip at chosen times and return its number."""
    transcript = load_transcript(run)
    start, end = word_safe_bounds(transcript, start, end)
    spoken = " ".join(word.text for word in transcript.words if word.end > start and word.start < end).strip()
    manifest = load_manifest(run)
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    clip_title = title.strip() or spoken[:70].strip() or "New clip"
    clip = Clip(start, end, 0.0, clip_title[:100], hook.strip()[:80], "Created with the assistant.",
                spoken[:150].strip(), spoken[:150].strip())
    number = len(selection) + 1
    selection.append(clip.to_dict())
    manifest["clips"].append(clip.to_dict() | {"number": number, "file": None})
    _write_json(run / "selection.json", selection)
    _write_json(run / "manifest.json", manifest)
    return number


def approved_clips(run: Path) -> list[dict]:
    decisions = load_decisions(run)
    return [clip for clip in load_manifest(run)["clips"]
            if decisions.get(str(clip["number"])) == "approved" and clip.get("file") and Path(clip["file"]).exists()]


def list_runs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    runs = [p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()]
    return sorted(runs, key=lambda p: (p / "manifest.json").stat().st_mtime, reverse=True)


def load_manifest(run: Path) -> dict:
    return json.loads((run / "manifest.json").read_text(encoding="utf-8"))


def load_decisions(run: Path) -> dict[str, str]:
    path = run / "review.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def set_decision(run: Path, number: int, decision: str) -> None:
    if decision not in DECISIONS:
        raise ValueError(f"Unknown decision: {decision}")
    decisions = load_decisions(run)
    decisions[str(number)] = decision
    _write_json(run / "review.json", decisions)


def update_clip(run: Path, number: int, fields: dict[str, str]) -> None:
    """Save text edits and keep the file name, manifest and selection in step."""
    changes = {key: str(fields[key]).strip() for key in EDITABLE if key in fields}
    manifest = load_manifest(run)
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    if not 1 <= number <= len(selection):
        raise ValueError(f"Clip {number} does not exist.")
    entry = next(clip for clip in manifest["clips"] if clip["number"] == number)
    old_name = safe_name(number, selection[number - 1]["title"])
    selection[number - 1].update(changes)
    entry.update(changes)
    new_name = safe_name(number, selection[number - 1]["title"])
    if new_name != old_name:
        old_file = run / "clips" / f"{old_name}.mp4"
        new_file = run / "clips" / f"{new_name}.mp4"
        if old_file.exists():
            old_file.replace(new_file)
            entry["file"] = str(new_file.resolve())
    _write_json(run / "selection.json", selection)
    _write_json(run / "manifest.json", manifest)
