"""Fetch a Twitch, Kick or YouTube video, clip or live stream, or accept a local file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .tools import ffmpeg_dir, run_ffmpeg

ALLOWED_DOMAINS = ("twitch.tv", "kick.com", "youtube.com", "youtu.be")
FORMAT = (
    "bv*[height<=1080][vcodec^=avc1]+ba/bv*[height<=1080]+ba/"
    "b[height<=1080]/b"
)
SKIP_SUFFIXES = {".part", ".ytdl", ".vtt", ".json", ".wav", ".ass"}


@dataclass(slots=True)
class Source:
    path: Path
    title: str
    url: str | None
    captions: Path | None
    caption_language: str | None


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        host == domain or host.endswith(f".{domain}") for domain in ALLOWED_DOMAINS
    ):
        raise ValueError("Only https Twitch, Kick and YouTube URLs are supported.")
    return url


def _base_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_dir(),
        # YouTube extraction needs a JavaScript runtime, and Node is the one installed here.
        "js_runtimes": {"node": {}},
    }


def _existing_media(workdir: Path) -> Path | None:
    for path in sorted(workdir.glob("source.*")):
        if path.suffix.lower() not in SKIP_SUFFIXES:
            return path
    return None


def _pick_caption_language(info: dict, wanted: str | None) -> str | None:
    """Prefer creator-written captions over automatic ones, in the wanted language."""
    original = info.get("language")
    for tracks in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        if not tracks:
            continue
        preferred = [wanted] if wanted else [original, f"{original}-orig", "en"]
        for language in preferred:
            if language and language in tracks:
                return language
        if wanted:
            for language in tracks:
                if language.split("-")[0] == wanted:
                    return language
        elif tracks is (info.get("subtitles") or {}) and len(tracks) == 1:
            return next(iter(tracks))
    return None


def _download_captions(url: str, workdir: Path, language: str) -> Path | None:
    import yt_dlp

    options = _base_options() | {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [language],
        "subtitlesformat": "vtt",
        "outtmpl": str(workdir / "captions.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError:
        return None
    return next(iter(sorted(workdir.glob("captions*.vtt"))), None)


def _is_true_live(info: dict) -> bool:
    """Kick labels the replay of an ongoing stream as live, but it can be seeked like a VOD."""
    return bool(info.get("is_live")) and info.get("extractor_key") != "KickVOD"


def is_fresh_live(target: str) -> bool:
    """True when each run must record new footage rather than reuse an earlier capture."""
    if Path(target).is_file():
        return False
    import yt_dlp

    url = validate_url(target)
    try:
        with yt_dlp.YoutubeDL(_base_options() | {"format": "b[protocol*=m3u8]/b"}) as ydl:
            return _is_true_live(ydl.extract_info(url, download=False))
    except yt_dlp.utils.DownloadError:
        return False  # fetch() reports the real error to the user.


def _capture_live(
    info: dict, workdir: Path, minutes: float, start: float | None = None, end: float | None = None
) -> Path:
    stream_url = info.get("url")
    if not stream_url:
        requested = info.get("requested_formats") or []
        stream_url = requested[0].get("url") if len(requested) == 1 else None
    if not stream_url:
        raise RuntimeError("The live stream did not expose a single capturable URL.")
    headers = "".join(f"{k}: {v}\r\n" for k, v in (info.get("http_headers") or {}).items())
    output = workdir / "source.mkv"
    partial = workdir / "capture.mkv"  # An interrupted capture must never look finished.
    seconds = end - (start or 0) if end is not None else minutes * 60
    args = ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,hls"]
    if headers:
        args += ["-headers", headers]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", stream_url, "-t", f"{seconds:.3f}", "-c", "copy", str(partial)]
    run_ffmpeg(args)
    partial.replace(output)
    return output


def fetch(
    target: str,
    workdir: Path,
    language: str | None = None,
    live_minutes: float = 30,
    start: float | None = None,
    end: float | None = None,
) -> Source:
    workdir.mkdir(parents=True, exist_ok=True)
    local = Path(target)
    if local.is_file():
        return Source(local.resolve(), local.stem, None, None, language)

    import yt_dlp

    url = validate_url(target)
    probe_options = _base_options() | {"format": "b[protocol*=m3u8]/b"}
    with yt_dlp.YoutubeDL(probe_options) as ydl:
        info = ydl.extract_info(url, download=False)
    title = info.get("title") or info.get("id") or "video"
    is_live = bool(info.get("is_live"))
    if _is_true_live(info) and (start is not None or end is not None):
        raise ValueError("From and To do not apply to a live stream. Use the live recording minutes.")

    media = _existing_media(workdir)
    if media is None:
        if is_live:
            media = _capture_live(info, workdir, live_minutes, start, end)
        else:
            options = _base_options() | {
                "format": FORMAT,
                "merge_output_format": "mp4",
                "outtmpl": str(workdir / "source.%(ext)s"),
            }
            if start is not None or end is not None:
                options["download_ranges"] = yt_dlp.utils.download_range_func(
                    [], [(start or 0, end if end is not None else float("inf"))]
                )
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
            media = _existing_media(workdir)
            if media is None:
                raise RuntimeError("The download finished without producing a video file.")

    # Captions use full-video timestamps, so they do not fit a partial or live capture.
    captions, caption_language = None, language
    if not is_live and start is None and end is None:
        picked = _pick_caption_language(info, language)
        if picked:
            captions = _download_captions(url, workdir, picked)
            caption_language = picked if captions else language
    return Source(media, title, url, captions, caption_language)
