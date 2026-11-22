"""Render 1080x1920 clips in a single encode, with burned-in word-highlight captions."""

from __future__ import annotations

import re
from pathlib import Path

from .models import Clip, Word
from .tools import run_ffmpeg, video_encoder_args

LAYOUTS = ("center", "blur", "facecam")
MAX_GROUP_WORDS = 4
MAX_GROUP_CHARS = 24
HOOK_SECONDS = 3.0
FACECAM_HOOK_MARGIN = 672  # Just below the 640-pixel webcam panel, clear of the face.

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,430,1
Style: Hook,Arial,68,&H00000000,&H00000000,&H0000E5FF,&H0000E5FF,-1,0,0,0,100,100,0,0,3,18,0,8,70,70,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(seconds: float) -> str:
    centis = max(round(seconds * 100), 0)
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    whole, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02}:{whole:02}.{centis:02}"


def ass_escape(text: str) -> str:
    """Stop transcript or model text from acting as ASS override commands."""
    text = " ".join(text.split())
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")")


def group_words(words: list[Word]) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        length = sum(len(w.text) + 1 for w in current) + len(word.text)
        gap = bool(current) and word.start - current[-1].end > 0.6
        if current and (len(current) >= MAX_GROUP_WORDS or length > MAX_GROUP_CHARS or gap):
            groups.append(current)
            current = []
        current.append(word)
        if word.text.endswith((".", "!", "?", ",", "…")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def build_ass(clip: Clip, words: list[Word], layout: str = "blur") -> str:
    inside = [w for w in words if w.end > clip.start and w.start < clip.end]
    lines = [_ASS_HEADER]
    if clip.hook:
        end = min(HOOK_SECONDS, clip.duration)
        # A margin of 0 keeps the style's default position.
        margin = FACECAM_HOOK_MARGIN if layout == "facecam" else 0
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Hook,,0,0,{margin},,{ass_escape(clip.hook)}\n")
    for group in group_words(inside):
        for index, word in enumerate(group):
            start = max(word.start - clip.start, 0)
            following = group[index + 1].start if index + 1 < len(group) else word.end
            end = min(max(following, word.end) - clip.start, clip.duration)
            if end <= start:
                continue
            parts = [
                f"{{\\c&H00E5FF&}}{ass_escape(w.text)}{{\\c&HFFFFFF&}}" if i == index else ass_escape(w.text)
                for i, w in enumerate(group)
            ]
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}\n")
    return "".join(lines)


def parse_cam(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"(\d+),(\d+),(\d+),(\d+)", value.strip())
    if not match:
        raise ValueError("The webcam area must be x,y,width,height in source pixels.")
    x, y, width, height = map(int, match.groups())
    if width < 2 or height < 2:
        raise ValueError("The webcam area is too small.")
    return x, y, width, height


def video_filter(layout: str, cam: tuple[int, int, int, int] | None, ass_name: str) -> str:
    finish = "setsar=1,fps=30,format=yuv420p"
    if layout == "center":
        base = (f"[0:v]crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),"
                f"scale=1080:1920:flags=lanczos,{finish}[base]")
    elif layout == "blur":
        base = ("[0:v]split=2[bgsrc][fgsrc];"
                "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=30[bg];"
                "[fgsrc]scale=1080:-2:flags=lanczos[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{finish}[base]")
    elif layout == "facecam":
        if cam is None:
            raise ValueError("The facecam layout needs the webcam area, for example --cam 1400,40,480,270.")
        x, y, width, height = cam
        base = ("[0:v]split=2[camsrc][gamesrc];"
                f"[camsrc]crop={width}:{height}:{x}:{y},"
                "scale=1080:640:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:640[cam];"
                "[gamesrc]crop=min(iw\\,ih*27/32):min(ih\\,iw*32/27),scale=1080:1280:flags=lanczos[game];"
                f"[cam][game]vstack,{finish}[base]")
    else:
        raise ValueError(f"Unknown layout: {layout}")
    return f"{base};[base]subtitles={ass_name}[v]"


def render_clip(
    source: Path,
    clip: Clip,
    words: list[Word],
    output: Path,
    layout: str = "blur",
    cam: tuple[int, int, int, int] | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    ass_name = f"{output.stem}.ass"
    ass_path = output.parent / ass_name
    ass_path.write_text(build_ass(clip, words, layout), encoding="utf-8")
    try:
        # Running inside the output folder keeps Windows drive colons out of the filter string.
        run_ffmpeg(
            ["-ss", f"{clip.start:.3f}", "-i", str(source.resolve()), "-t", f"{clip.duration:.3f}",
             "-filter_complex", video_filter(layout, cam, ass_name),
             "-map", "[v]", "-map", "0:a:0?",
             *video_encoder_args(),
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", output.name],
            cwd=output.parent,
        )
    finally:
        ass_path.unlink(missing_ok=True)
    return output
