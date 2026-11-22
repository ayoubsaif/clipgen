import json
from pathlib import Path

import pytest

from clipgen import assistant, review
from clipgen.models import Segment, Transcript, Word
from clipgen.pipeline import safe_name


def make_run(root: Path) -> Path:
    run = root / "run1"
    (run / "clips").mkdir(parents=True)
    segments = []
    for index in range(6):
        start = index * 5.0
        words = [Word(start + i, start + i + 0.8, f"line{index}word{i}") for i in range(4)]
        segments.append(Segment(start, start + 3.8, " ".join(w.text for w in words), words))
    (run / "transcript.json").write_text(json.dumps(Transcript("en", "whisper", 30.0, segments).to_dict()), encoding="utf-8")
    clip = {"start": 5.0, "end": 15.0, "score": 70.0, "title": "Great moment", "hook": "Hook", "why": "Why",
            "tiktok_caption": "tt", "instagram_caption": "ig"}
    file = run / "clips" / f"{safe_name(1, 'Great moment')}.mp4"
    file.write_bytes(b"video")
    (run / "selection.json").write_text(json.dumps([clip]), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"title": "Video", "target": "x", "source": "x",
                                                   "clips": [clip | {"number": 1, "file": str(file)}]}), encoding="utf-8")
    return run


def raw(kind, **fields):
    return {"type": kind, "clip": None, "field": None, "value": None, "start": None, "end": None, "hook": None} | fields


class FakeProvider:
    def __init__(self, reply):
        self.reply = reply
        self.prompt = ""

    def complete(self, system, user, schema):
        self.prompt = user
        return self.reply


@pytest.mark.parametrize("item", [
    raw("publish", clip=1),
    raw("post_now", clip=1, value="x"),
    raw("edit_text", clip=9, field="title", value="x"),
    raw("edit_text", clip=1, field="file", value="C:/evil.mp4"),
    raw("edit_text", clip=True, field="title", value="x"),
    raw("set_times", clip=1, start=float("nan"), end=4),
    raw("set_decision", clip=1, value="published"),
    raw("open_page", value="Somewhere else"),
    "not an action",
])
def test_unsafe_actions_are_dropped(item):
    assert assistant.clean_action(item, {1}) is None


def test_reply_keeps_valid_actions_and_changes_nothing(tmp_path):
    run = make_run(tmp_path)
    before = (run / "manifest.json").read_text(encoding="utf-8")
    provider = FakeProvider({"reply": "Try this", "actions": [
        raw("edit_text", clip=1, field="hook", value="Better hook"), raw("publish", clip=1)]})
    answer = assistant.ask(provider, run, [{"role": "user", "content": "better hook"}], 1, "Step 1 · Clips")
    assert [a["type"] for a in answer["actions"]] == ["edit_text"] and answer["actions"][0]["status"] == "pending"
    assert (run / "manifest.json").read_text(encoding="utf-8") == before
    assert "line1word0" in provider.prompt and "USER: better hook" in provider.prompt


def test_apply_text_only_renders_when_video_changes(tmp_path):
    run = make_run(tmp_path)
    title = assistant.clean_action(raw("edit_text", clip=1, field="title", value="New title"), {1})
    hook = assistant.clean_action(raw("edit_text", clip=1, field="hook", value="New hook"), {1})
    assert assistant.apply(run, title) is None and assistant.apply(run, hook) == 1
    clip = review.load_manifest(run)["clips"][0]
    assert clip["title"] == "New title" and clip["hook"] == "New hook" and Path(clip["file"]).exists()


def test_times_stay_exact_but_never_cut_a_word(tmp_path):
    run = make_run(tmp_path)
    review.set_clip_times(run, 1, 4.9, 12.4)
    clip = review.load_manifest(run)["clips"][0]
    assert clip["start"] == pytest.approx(4.9) and clip["end"] == pytest.approx(12.8)
    assert json.loads((run / "selection.json").read_text(encoding="utf-8"))[0]["end"] == pytest.approx(12.8)


@pytest.mark.parametrize("start,end", [(10, 10.2), (20, 5), (0, 0)])
def test_bad_times_are_rejected(tmp_path, start, end):
    run = make_run(tmp_path)
    with pytest.raises(ValueError):
        review.set_clip_times(run, 1, start, end)
    assert review.load_manifest(run)["clips"][0]["start"] == 5.0


def test_new_clip_is_added_for_rendering(tmp_path):
    run = make_run(tmp_path)
    action = assistant.clean_action(raw("new_clip", start=15.0, end=24.0, value="Second", hook="Look"), {1})
    assert assistant.apply(run, action) == 2
    clip = review.load_manifest(run)["clips"][1]
    assert clip["title"] == "Second" and clip["hook"] == "Look" and clip["file"] is None


def test_chat_is_saved_and_corrupt_chat_is_ignored(tmp_path):
    run = make_run(tmp_path)
    assistant.save_chat(run, [{"role": "user", "content": "hello"}])
    assert assistant.load_chat(run)[0]["content"] == "hello"
    (run / "assistant.json").write_text("{broken", encoding="utf-8")
    assert assistant.load_chat(run) == []
