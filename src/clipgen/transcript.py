"""Build a word-timed transcript from platform captions, or local Whisper as the fallback."""

from __future__ import annotations

import gc
import html
import json
import re
import wave
from pathlib import Path

import numpy as np

from .models import Segment, Transcript, Word
from .tools import media_duration, prepare_gpu_libraries, run_ffmpeg

_CUE_TIME = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})"
)
_INLINE_TIME = re.compile(r"<(\d+):(\d{2}):(\d{2})\.(\d{3})>")
_TAG = re.compile(r"</?[^>]+>")

# The default filter drops speech over game audio. Tuned on real Twitch and Kick samples.
VAD_PARAMETERS = {
    "threshold": 0.20,
    "min_speech_duration_ms": 100,
    "min_silence_duration_ms": 1000,
    "speech_pad_ms": 500,
}


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", text)).split())


def _timed_words(line: str, cue_start: float, cue_end: float) -> list[Word]:
    """Parse YouTube's rolling format: first word<00:00:01.200><c> next</c>..."""
    parts = _INLINE_TIME.split(line)
    pieces: list[tuple[float, str]] = [(cue_start, _clean(parts[0]))]
    for index in range(1, len(parts), 5):
        hours, minutes, seconds, millis, text = parts[index : index + 5]
        pieces.append((_seconds(hours, minutes, seconds, millis), _clean(text)))
    pieces = [(start, text) for start, text in pieces if text]
    words = []
    for position, (start, text) in enumerate(pieces):
        end = pieces[position + 1][0] if position + 1 < len(pieces) else cue_end
        words.append(Word(start, max(end, start + 0.01), text))
    return words


def _interpolated_words(text: str, start: float, end: float) -> list[Word]:
    tokens = text.split()
    if not tokens:
        return []
    total = sum(len(token) for token in tokens)
    span = max(end - start, 0.01)
    words, cursor = [], start
    for token in tokens:
        length = span * len(token) / total
        words.append(Word(round(cursor, 3), round(cursor + length, 3), token))
        cursor += length
    return words


def parse_vtt(content: str) -> list[Segment]:
    """Parse WebVTT and remove the repeated lines of rolling automatic captions."""
    segments: list[Segment] = []
    previous_lines: set[str] = set()
    for block in re.split(r"\r?\n\r?\n", content):
        lines = block.strip().splitlines()
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        match = _CUE_TIME.search(lines[time_index])
        if not match:
            continue
        groups = match.groups()
        start, end = _seconds(*groups[:4]), _seconds(*groups[4:])
        body = [line for line in lines[time_index + 1 :] if line.strip()]
        timed = [line for line in body if _INLINE_TIME.search(line)]
        words: list[Word] = []
        if timed:
            for line in timed:
                words.extend(_timed_words(line, start, end))
        else:
            fresh = [c for c in map(_clean, body) if c and c not in previous_lines]
            if fresh:
                words = _interpolated_words(" ".join(fresh), start, end)
        previous_lines = {c for c in map(_clean, body) if c}
        if words:
            text = " ".join(word.text for word in words)
            segments.append(Segment(words[0].start, words[-1].end, text, words))
    return segments


def _read_pcm(path: Path) -> np.ndarray:
    """Load our 16 kHz mono WAV directly, avoiding faster-whisper's PyAV version coupling."""
    with wave.open(str(path), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_whisper(
    media: Path,
    workdir: Path,
    model_name: str = "large-v3-turbo",
    language: str | None = None,
    device: str = "auto",
) -> tuple[list[Segment], str]:
    prepare_gpu_libraries()
    from faster_whisper import WhisperModel

    audio = workdir / "audio.wav"
    run_ffmpeg(["-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)])

    attempts = (
        [("cuda", "float16"), ("cpu", "int8")] if device == "auto"
        else [("cuda", "float16")] if device == "cuda"
        else [("cpu", "int8")]
    )
    last_error: Exception | None = None
    try:
        for attempt_device, compute_type in attempts:
            model = None
            try:
                model = WhisperModel(model_name, device=attempt_device, compute_type=compute_type)
                stream, info = model.transcribe(
                    _read_pcm(audio),
                    language=language,
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters=VAD_PARAMETERS,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                segments = []
                for item in stream:
                    words = [Word(w.start, w.end, w.word.strip()) for w in item.words or [] if w.word.strip()]
                    if words:
                        segments.append(Segment(item.start, item.end, item.text.strip(), words))
                return segments, info.language
            except (RuntimeError, OSError) as error:
                last_error = error
                print(f"Whisper on {attempt_device} failed: {error}")
            finally:
                # Free VRAM before the next attempt and before NVENC needs the GPU.
                del model
                gc.collect()
    finally:
        audio.unlink(missing_ok=True)
    raise RuntimeError(f"Transcription failed: {last_error}")


def load_transcript(
    media: Path,
    workdir: Path,
    captions: Path | None,
    language: str | None,
    whisper_model: str = "large-v3-turbo",
    device: str = "auto",
    force_whisper: bool = False,
) -> Transcript:
    cache = workdir / "transcript.json"
    if cache.exists():
        return Transcript.from_dict(json.loads(cache.read_text(encoding="utf-8")))

    duration = media_duration(media)
    segments: list[Segment] = []
    source = "captions"
    if captions and not force_whisper:
        segments = parse_vtt(captions.read_text(encoding="utf-8", errors="replace"))
    if segments:
        detected = language or "unknown"
    else:
        source = "whisper"
        segments, detected = transcribe_whisper(media, workdir, whisper_model, language, device)
    if not segments:
        raise RuntimeError("No speech was found in this video.")

    transcript = Transcript(detected, source, duration, segments)
    cache.write_text(json.dumps(transcript.to_dict(), ensure_ascii=False), encoding="utf-8")
    return transcript
