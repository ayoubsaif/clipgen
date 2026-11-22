import re
import subprocess
from pathlib import Path

import pytest

from clipgen.ingest import _capture_live, _existing_media, _is_true_live, validate_url
from clipgen.models import Clip, Segment, Transcript, Word
from clipgen.pipeline import RunOptions, rerender, run, run_folder, safe_name
from clipgen.render import ass_escape, build_ass
from clipgen.selection import SelectionOptions, build_windows, finalize_clips, snap_to_words
from clipgen.tools import ffmpeg_path
from clipgen.transcript import parse_vtt


def make_transcript(seconds=300):
    segments = []
    for t in range(0, seconds, 5):
        words = [
            Word(t + i, t + i + 0.8, f"word{t}_{i}" + ("." if i == 4 else ""))
            for i in range(5)
        ]
        segments.append(Segment(t, t + 4.8, " ".join(w.text for w in words), words))
    return Transcript("en", "captions", float(seconds), segments)


@pytest.mark.parametrize("url", [
    "https://www.twitch.tv/videos/1",
    "https://clips.twitch.tv/Abc",
    "https://kick.com/someone",
    "https://www.youtube.com/watch?v=x",
    "https://youtu.be/x",
])
def test_accepts_supported_urls(url):
    assert validate_url(url) == url


@pytest.mark.parametrize("url", [
    "http://twitch.tv/x",
    "https://twitch.tv.evil.com/x",
    "https://eviltwitch.tv/x",
    "file:///C:/secret",
    "--exec=calc",
    "https://127.0.0.1/x",
])
def test_rejects_other_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_kick_ongoing_vod_is_not_a_true_live():
    assert not _is_true_live({"is_live": True, "extractor_key": "KickVOD"})
    assert _is_true_live({"is_live": True, "extractor_key": "KickLive"})
    assert _is_true_live({"is_live": True, "extractor_key": "TwitchStream"})
    assert not _is_true_live({"is_live": False, "extractor_key": "TwitchVod"})


def test_capture_honors_range_and_only_publishes_finished_file(tmp_path, monkeypatch):
    calls = []

    def fake_ffmpeg(args):
        calls.append(args)
        Path(args[-1]).write_bytes(b"video")

    monkeypatch.setattr("clipgen.ingest.run_ffmpeg", fake_ffmpeg)
    output = _capture_live({"url": "https://example.test/a.m3u8"}, tmp_path, 30, 120, 135)
    args = calls[0]
    assert args[args.index("-ss") + 1] == "120.000"
    assert args[args.index("-t") + 1] == "15.000"
    assert args.index("-ss") < args.index("-i")
    assert output.name == "source.mkv" and output.exists()
    assert not (tmp_path / "capture.mkv").exists()


def test_failed_capture_leaves_no_source(tmp_path, monkeypatch):
    def failing_ffmpeg(args):
        Path(args[-1]).write_bytes(b"partial")
        raise RuntimeError("stream ended")

    monkeypatch.setattr("clipgen.ingest.run_ffmpeg", failing_ffmpeg)
    with pytest.raises(RuntimeError):
        _capture_live({"url": "https://example.test/a.m3u8"}, tmp_path, 1)
    assert _existing_media(tmp_path) is None


def test_live_captures_get_separate_folders():
    options = RunOptions()
    url = "https://www.twitch.tv/channel"
    assert run_folder(url, options, "20261122-100000") != run_folder(url, options, "20261122-110000")
    assert run_folder(url, options) == run_folder(url, options)


def test_vtt_rolling_captions_are_not_duplicated():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
hello<00:00:01.500><c> big</c><00:00:02.000><c> world</c>

00:00:03.000 --> 00:00:03.010
hello big world

00:00:03.010 --> 00:00:05.000
hello big world
next<00:00:03.500><c> line</c>
"""
    words = [w.text for s in parse_vtt(vtt) for w in s.words]
    assert words == ["hello", "big", "world", "next", "line"]


def test_vtt_plain_cues_get_ordered_word_times():
    segments = parse_vtt("WEBVTT\n\n00:01.000 --> 00:03.000\nHola &amp; adios\n")
    assert [w.text for w in segments[0].words] == ["Hola", "&", "adios"]
    assert segments[0].words[0].start == 1.0
    assert segments[0].words[-1].end == pytest.approx(3.0, abs=0.01)


def test_windows_cover_transcript_and_advance():
    transcript = make_transcript(600)
    windows = build_windows(transcript, 90, 30)
    assert windows[0].start == 0
    assert windows[-1].end == transcript.segments[-1].end
    assert all(b.start > a.start for a, b in zip(windows, windows[1:]))
    assert all(b.start <= a.end for a, b in zip(windows, windows[1:]))


@pytest.mark.parametrize("start,end", [
    (float("nan"), 30), (10, float("inf")), (50, 40), (400, 500), (-50, -10), ("1", 30), (None, 30),
])
def test_snap_rejects_bad_times(start, end):
    transcript = make_transcript()
    assert snap_to_words(start, end, transcript.words, transcript.duration, 20, 60) is None


def test_snap_clamps_and_respects_limits():
    transcript = make_transcript()
    for start, end in [(-20, 40), (12.3, 200), (280, 900), (100, 103)]:
        bounds = snap_to_words(start, end, transcript.words, transcript.duration, 20, 60)
        assert bounds is not None
        assert 0 <= bounds[0] < bounds[1] <= transcript.duration
        assert 20 <= bounds[1] - bounds[0] <= 60 + 1e-6


def test_snap_does_not_cut_words():
    transcript = make_transcript()
    words = transcript.words
    start, end = snap_to_words(12.4, 47.4, words, transcript.duration, 20, 60)
    for word in words:
        assert not (word.start < start - 1e-6 < word.end - 0.11)
        assert not (word.start + 0.26 < end < word.end - 1e-6)


def test_finalize_removes_overlap_and_honors_count():
    transcript = make_transcript()
    base = {"title": "t", "hook": "h", "why": "w", "tiktok_caption": "a", "instagram_caption": "b"}
    raw = [
        base | {"start": 10, "end": 40, "score": 90},
        base | {"start": 12, "end": 42, "score": 80},
        base | {"start": 100, "end": 130, "score": 70},
        base | {"start": 200, "end": 230, "score": 60},
        base | {"start": 900, "end": 930, "score": 100},
    ]
    clips = finalize_clips(raw, transcript, SelectionOptions(count=2))
    assert [c.score for c in clips] == [90, 70]


def test_ass_text_cannot_inject_commands():
    assert "{" not in ass_escape(r"{\pos(1,1)}hi")
    assert "\\" not in ass_escape(r"{\pos(1,1)}hi")
    assert "\n" not in ass_escape("a\nDialogue: 0,evil")


def test_build_ass_uses_clip_relative_times():
    words = [Word(100.0, 100.5, "hello"), Word(100.6, 101.0, "there")]
    clip = Clip(100.0, 110.0, 50, "t", "Hook", "w", "a", "b")
    ass = build_ass(clip, words)
    assert "0:00:00.00,0:00:03.00,Hook" in ass
    assert "0:00:00.60,0:00:01.00,Caption" in ass


def test_safe_name_blocks_path_tricks():
    name = safe_name(1, "..\\..\\CON: ¿Qué pasó?")
    assert re.fullmatch(r"01-[a-z0-9-]+", name)


class FakeProvider:
    name = "fake"

    def complete(self, system, user, schema):
        if "scores" in schema["properties"]:
            ids = [int(i) for i in re.findall(r'<excerpt id="(\d+)"', user)]
            return {"scores": [{"id": i, "score": 90 - i} for i in ids]}
        return {"clips": [
            {"start": 5, "end": 28, "score": 88, "title": "Primer clip: ¡qué locura!",
             "hook": "Nobody expected {this}", "why": "test", "tiktok_caption": "a", "instagram_caption": "b"},
            {"start": 40, "end": 65, "score": 75, "title": "Second clip",
             "hook": "Second", "why": "test", "tiktok_caption": "a", "instagram_caption": "b"},
        ]}


def probe(path):
    return subprocess.run([ffmpeg_path(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True, errors="replace").stderr


@pytest.mark.parametrize("layout,cam", [("center", None), ("blur", None), ("facecam", (1400, 40, 480, 270))])
def test_end_to_end_render(tmp_path, layout, cam):
    # The space and apostrophe check path handling in ffmpeg's filter graph.
    folder = tmp_path / "my user's videos"
    folder.mkdir()
    source = folder / "input video.mp4"
    subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=80",
         "-f", "lavfi", "-i", "sine=f=440:d=80", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        check=True,
    )
    options = RunOptions(output_root=folder / "runs", layout=layout, cam=cam,
                         selection=SelectionOptions(count=2, min_seconds=15, max_seconds=30))
    workdir = run_folder(str(source), options)
    workdir.mkdir(parents=True)
    import json
    (workdir / "transcript.json").write_text(json.dumps(make_transcript(80).to_dict()), encoding="utf-8")

    result = run(str(source), FakeProvider(), options, progress=lambda _: None)
    manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["clips"]) == 2
    for entry in manifest["clips"]:
        info = probe(entry["file"])
        assert "1080x1920" in info
        assert "Audio: aac" in info
        hours, minutes, seconds = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info).groups()
        actual = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        assert actual == pytest.approx(entry["end"] - entry["start"], abs=0.2)

    rerender(result, RunOptions(layout="center"), only={1}, progress=lambda _: None)


def _short_transcript():
    from clipgen.models import Segment, Transcript, Word

    first = [Word(i * 0.5, i * 0.5 + 0.4, f"a{i}") for i in range(6)]
    second = [Word(6 + i * 0.5, 6.4 + i * 0.5, f"b{i}") for i in range(6)]
    return Transcript("es", "whisper", 17.5, [Segment(0, 2.9, "a", first), Segment(6, 8.9, "b", second)])


class _ShortClipProvider:
    name = "stub"

    def __init__(self, start, end):
        self.start, self.end = start, end

    def complete(self, system, user, schema):
        if "scores" in schema.get("properties", {}):
            return {"scores": [{"id": 0, "score": 60}]}
        return {"clips": [{"start": self.start, "end": self.end, "score": 60, "title": "t", "hook": "h", "why": "w",
                           "tiktok_caption": "a", "instagram_caption": "b"}]}


@pytest.mark.parametrize("start,end", [(0.0, 2.0), (3.5, 5.0), (10.0, 15.0), (6.5, 7.0)])
def test_short_video_keeps_all_speech_wherever_the_model_points(start, end):
    from clipgen.selection import SelectionOptions, select_clips

    clips = select_clips(_short_transcript(), _ShortClipProvider(start, end),
                         SelectionOptions(count=1, min_seconds=20, max_seconds=60), None, lambda _: None)
    assert len(clips) == 1
    assert clips[0].start == 0
    assert clips[0].end >= 8.9


def test_video_without_speech_gets_a_clear_error():
    from clipgen.models import Transcript
    from clipgen.selection import SelectionOptions, select_clips

    with pytest.raises(RuntimeError, match="No speech"):
        select_clips(Transcript("es", "whisper", 12.0, []), _ShortClipProvider(0, 5),
                     SelectionOptions(count=1), None, lambda _: None)
