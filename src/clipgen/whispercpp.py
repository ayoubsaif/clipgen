"""Transcribe with a local whisper.cpp build, for NVIDIA GPUs that faster-whisper cannot use (Windows on ARM)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import httpx

from .models import Segment, Word

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
MODELS = {"large-v3-turbo", "medium", "small", "tiny"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PROGRESS = re.compile(r"progress\s*=\s*(\d+)%")
_BACKEND = re.compile(r"using (\S+) backend", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "clipgen" / "whisper.cpp"


def find_binary() -> Path | None:
    configured = os.environ.get("CLIPGEN_WHISPER_CPP")
    binary = Path(configured) if configured else install_dir() / "whisper-cli.exe"
    return binary if binary.is_file() else None


def ensure_model(name: str, progress=print) -> Path:
    """Download the model once, and keep it only if it matches the checksum Hugging Face reports."""
    if name not in MODELS:
        raise RuntimeError(f"whisper.cpp has no model named {name}.")
    target = install_dir() / "models" / f"ggml-{name}.bin"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    progress(f"Downloading the {name} model for whisper.cpp")
    try:
        with httpx.Client(follow_redirects=False, timeout=60) as client:
            first = client.send(client.build_request("GET", MODEL_URL.format(name=name)), stream=True)
            expected = first.headers.get("x-linked-etag", "").strip('"W/').lower()
            location = first.headers.get("location")
            first.close()
            if not location or not _SHA256.fullmatch(expected):
                raise RuntimeError("Could not get the whisper.cpp model's download details.")
        with httpx.stream("GET", httpx.URL(MODEL_URL.format(name=name)).join(location), follow_redirects=True,
                          timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            received = reported = 0
            with partial.open("wb") as file:
                for chunk in response.iter_bytes(1024 * 1024):
                    file.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    tenth = received * 10 // total if total else 0
                    if tenth > reported:
                        reported = tenth
                        progress(f"Downloaded {tenth * 10}% of the model")
        if digest.hexdigest() != expected:
            raise RuntimeError("The downloaded whisper.cpp model failed its checksum.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def parse_output(data: dict) -> tuple[list[Segment], str]:
    """Join whisper.cpp tokens into words. A token starting with a space begins a new word."""
    segments: list[Segment] = []
    for item in data.get("transcription", []):
        words: list[Word] = []
        text, start, end = "", 0.0, 0.0
        for token in item.get("tokens", []):
            piece = token.get("text", "")
            offsets = token.get("offsets")
            if piece.startswith("[_") or not offsets:
                continue
            token_start, token_end = offsets["from"] / 1000, offsets["to"] / 1000
            if piece.startswith(" ") and text.strip():
                words.append(Word(start, max(end, start + 0.01), text.strip()))
                text = ""
            if not text.strip():
                start = token_start
            text += piece
            end = token_end
        if text.strip():
            words.append(Word(start, max(end, start + 0.01), text.strip()))
        if words:
            segments.append(Segment(words[0].start, words[-1].end, " ".join(w.text for w in words), words))
    return segments, data.get("result", {}).get("language", "unknown")


def transcribe(binary: Path, audio: Path, model_name: str, language: str | None, progress=print) -> tuple[list[Segment], str]:
    model = ensure_model(model_name, progress)
    output = audio.with_name("whispercpp")
    result = output.with_suffix(".json")
    code = (language or "auto").split("-")[0].lower()
    command = [str(binary), "-m", str(model), "-f", audio.name, "-of", output.name, "-ojf", "-pp", "-l", code,
               "-t", str(max((os.cpu_count() or 4) // 2, 2))]
    process = subprocess.Popen(command, cwd=audio.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                               creationflags=NO_WINDOW)
    reported = 0
    tail: list[str] = []
    backend = None
    try:
        for line in process.stderr:
            tail = [*tail[-7:], line.strip()]
            if "no GPU found" in line and not backend:
                backend = "CPU"
                progress("whisper.cpp found no GPU, so it is using the CPU")
            found = _BACKEND.search(line)
            if found and not backend:
                backend = found.group(1)
                progress(f"whisper.cpp is using {backend}")
            match = _PROGRESS.search(line)
            if match and int(match.group(1)) >= reported + 10:
                reported = int(match.group(1)) // 10 * 10
                progress(f"Transcribed {reported}%")
        if process.wait() != 0 or not result.is_file():
            raise RuntimeError(f"whisper.cpp failed: {' '.join(tail)[-300:]}")
        return parse_output(json.loads(result.read_text(encoding="utf-8", errors="replace")))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        result.unlink(missing_ok=True)
