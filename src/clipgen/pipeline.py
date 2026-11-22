"""Run the stages in order, caching each result inside one folder per video."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .ingest import fetch, is_fresh_live
from .models import Clip
from .render import render_clip
from .selection import SelectionOptions, select_clips
from .signals import loudness_per_second
from .transcript import load_transcript
from .webcam import resolve_layout


@dataclass(slots=True)
class RunOptions:
    output_root: Path = Path("runs")
    language: str | None = None
    whisper_model: str = "large-v3-turbo"
    device: str = "auto"
    force_whisper: bool = False
    live_minutes: float = 30
    start: float | None = None
    end: float | None = None
    layout: str = "blur"
    cam: tuple[int, int, int, int] | None = None
    render: bool = True
    selection: SelectionOptions = field(default_factory=SelectionOptions)


def run_folder(target: str, options: RunOptions, capture_id: str = "") -> Path:
    key = f"{target}|{options.start}|{options.end}|{capture_id}" if capture_id else f"{target}|{options.start}|{options.end}"
    return options.output_root / hashlib.sha256(key.encode()).hexdigest()[:12]


def safe_name(index: int, title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_title).strip("-").lower()[:50].strip("-")
    return f"{index:02}-{slug or 'clip'}"


def _write_manifest(workdir: Path, title: str, target: str, source: Path, clips: list[dict]) -> None:
    manifest = {"title": title, "target": target, "source": str(source.resolve()), "clips": clips}
    (workdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def render_all(workdir: Path, source: Path, clips: list[Clip], transcript, options: RunOptions,
               only: set[int] | None = None, progress=print) -> list[dict]:
    words = transcript.words
    entries = []
    for number, clip in enumerate(clips, 1):
        entry = clip.to_dict() | {"number": number, "file": None}
        existing = workdir / "clips" / f"{safe_name(number, clip.title)}.mp4"
        if only is None or number in only:
            progress(f"Rendering clip {number}/{len(clips)}: {clip.title}")
            render_clip(source, clip, words, existing, options.layout, options.cam)
        if existing.exists():
            entry["file"] = str(existing.resolve())
        entries.append(entry)
    return entries


def _clip_middles(clips: list[Clip]) -> list[float]:
    return [(clip.start + clip.end) / 2 for clip in clips]


def run(target: str, provider, options: RunOptions, progress=print) -> Path:
    progress("Fetching video")
    capture_id = time.strftime("%Y%m%d-%H%M%S") if is_fresh_live(target) else ""
    workdir = run_folder(target, options, capture_id)
    workdir.mkdir(parents=True, exist_ok=True)

    source = fetch(target, workdir, options.language, options.live_minutes, options.start, options.end)

    progress("Loading transcript")
    transcript = load_transcript(
        source.path, workdir, source.captions, source.caption_language,
        options.whisper_model, options.device, options.force_whisper,
    )
    progress(f"Transcript from {transcript.source}: {len(transcript.words)} words")

    progress("Measuring audio energy")
    loudness = loudness_per_second(source.path)
    clips = select_clips(transcript, provider, options.selection, loudness, progress)
    if not clips:
        raise RuntimeError("The model proposed no clips that passed validation.")

    (workdir / "selection.json").write_text(
        json.dumps([clip.to_dict() for clip in clips], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if options.render:
        options = resolve_layout(workdir, source.path, _clip_middles(clips), options, provider, progress)
    entries = (
        render_all(workdir, source.path, clips, transcript, options, progress=progress)
        if options.render
        else [clip.to_dict() | {"number": i, "file": None} for i, clip in enumerate(clips, 1)]
    )
    _write_manifest(workdir, source.title, target, source.path, entries)
    return workdir


def rerender(workdir: Path, options: RunOptions, only: set[int] | None = None, progress=print) -> Path:
    """Render again from saved results, with no download or model call."""
    from .models import Transcript

    manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
    transcript = Transcript.from_dict(json.loads((workdir / "transcript.json").read_text(encoding="utf-8")))
    saved = json.loads((workdir / "selection.json").read_text(encoding="utf-8"))
    clips = [Clip(**item) for item in saved]
    source = Path(manifest["source"])
    options = resolve_layout(workdir, source, _clip_middles(clips), options, progress=progress)
    entries = render_all(workdir, source, clips, transcript, options, only, progress)
    _write_manifest(workdir, manifest["title"], manifest["target"], source, entries)
    return workdir
