import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import keyring
import pytest
from keyring.backend import KeyringBackend
from streamlit.testing.v1 import AppTest

from clipgen import credentials, postiz, review
from clipgen.models import Segment, Transcript, Word
from clipgen.postiz import ChannelDraft, PostizClient, PostizError, build_post, channel_settings, normalize_url

APP = str(Path(__file__).parents[1] / "src" / "clipgen" / "app.py")
KEY = "test-postiz-key"
FUTURE = datetime.now(timezone.utc) + timedelta(days=1)


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setenv("CLIPGEN_HOME", str(tmp_path))
    monkeypatch.delenv("POSTIZ_URL", raising=False)
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    yield
    keyring.set_keyring(previous)


def make_run(root: Path, approved=True) -> Path:
    run = root / "run1"
    (run / "clips").mkdir(parents=True)
    segments = []
    for index in range(6):
        start = index * 5.0
        words = [Word(start + i, start + i + 0.8, f"line{index}word{i}") for i in range(4)]
        segments.append(Segment(start, start + 3.8, " ".join(w.text for w in words), words))
    (run / "transcript.json").write_text(json.dumps(Transcript("en", "whisper", 30.0, segments).to_dict()), encoding="utf-8")
    clip = {"start": 0.0, "end": 8.0, "score": 70.0, "title": "Great moment", "hook": "Hook", "why": "Why",
            "tiktok_caption": "tt caption", "instagram_caption": "ig caption"}
    file = run / "clips" / "01-great-moment.mp4"
    file.write_bytes(b"video-bytes")
    (run / "selection.json").write_text(json.dumps([clip]), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"title": "Video", "target": "x", "source": "x",
                                                   "clips": [clip | {"number": 1, "file": str(file)}]}), encoding="utf-8")
    if approved:
        review.set_decision(run, 1, "approved")
    return run


def drafts():
    return [
        ChannelDraft("yt1", "youtube", "My YT", "desc", "Title", "private"),
        ChannelDraft("tt1", "tiktok", "My TT", "caption", "Title", "inbox"),
        ChannelDraft("ig1", "instagram", "My IG", "caption", "", "public"),
    ]


class FakePostiz:
    def __init__(self):
        self.uploads = 0
        self.posts = []
        self.auth = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("authorization"))
        path = request.url.path
        if path.endswith("/integrations"):
            return httpx.Response(200, json=[
                {"id": "yt1", "name": "My YT", "identifier": "youtube", "disabled": False},
                {"id": "x1", "name": "X", "identifier": "x", "disabled": False},
                {"id": "off", "name": "Off", "identifier": "tiktok", "disabled": True},
            ])
        if path.endswith("/upload"):
            self.uploads += 1
            return httpx.Response(201, json={"id": "media1", "path": "https://cdn.test/clip.mp4"})
        if path.endswith("/posts"):
            self.posts.append(json.loads(request.content))
            return httpx.Response(201, json=[{"postId": "p1", "integration": "yt1"}])
        return httpx.Response(404, json={"msg": "missing"})


@pytest.mark.parametrize("url", ["http://postiz.example.com", "ftp://x", "https://user:pw@x.com",
                                 "https://x.com?y=1", "not a url"])
def test_rejects_unsafe_addresses(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_accepts_https_and_local_addresses():
    assert normalize_url("https://postiz.example.com/api/") == "https://postiz.example.com/api/public/v1"
    assert normalize_url(postiz.DEFAULT_URL) == postiz.DEFAULT_URL
    assert normalize_url("http://localhost:4007") == "http://localhost:4007/public/v1"


def test_settings_match_postiz_required_fields():
    youtube, tiktok, instagram = (channel_settings(d) for d in drafts())
    assert set(youtube) >= {"title", "type", "selfDeclaredMadeForKids", "tags"} and youtube["type"] == "private"
    assert tiktok["content_posting_method"] == "UPLOAD" and tiktok["privacy_level"] == "SELF_ONLY"
    assert set(tiktok) >= {"title", "privacy_level", "duet", "stitch", "comment", "autoAddMusic",
                           "brand_content_toggle", "brand_organic_toggle", "video_made_with_ai"}
    assert instagram["post_type"] == "post" and instagram["collaborators"] == []


def test_build_post_shape_and_utc_date():
    local = datetime(2030, 1, 2, 12, 0, tzinfo=timezone(timedelta(hours=1)))
    body = build_post("schedule", local, {"id": "m", "path": "https://c/x.mp4"}, drafts())
    assert body["date"] == "2030-01-02T11:00:00.000Z"
    assert body["type"] == "schedule" and body["shortLink"] is False and body["tags"] == []
    assert len({post["group"] for post in body["posts"]}) == 3
    value = body["posts"][0]["value"][0]
    assert value["delay"] == 0 and value["image"] == [{"id": "m", "path": "https://c/x.mp4"}]


def test_past_schedule_and_no_channels_are_rejected():
    media = {"id": "m", "path": "p"}
    with pytest.raises(ValueError):
        build_post("schedule", datetime.now(timezone.utc) - timedelta(minutes=1), media, drafts())
    with pytest.raises(ValueError):
        build_post("draft", FUTURE, media, [])


def test_channels_keeps_only_active_supported():
    client = PostizClient("https://p.test", KEY, httpx.MockTransport(FakePostiz()))
    assert [c["id"] for c in client.channels()] == ["yt1"]


def test_errors_never_expose_key_or_follow_redirects():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.test/steal"})

    client = PostizClient("https://p.test", KEY, httpx.MockTransport(handler))
    with pytest.raises(PostizError) as error:
        client.channels()
    assert KEY not in str(error.value) and len(calls) == 1


def test_send_clip_records_and_reuses_upload(tmp_path):
    run = make_run(tmp_path)
    server = FakePostiz()
    client = PostizClient("https://p.test", KEY, httpx.MockTransport(server))
    file = Path(review.load_manifest(run)["clips"][0]["file"])
    postiz.send_clip(client, run, 1, file, "draft", FUTURE, drafts())
    postiz.send_clip(client, run, 1, file, "draft", FUTURE, drafts())
    assert server.uploads == 1 and len(server.posts) == 2 and server.auth == {KEY}
    assert server.posts[0]["type"] == "draft"
    history = postiz.load_history(run)
    assert len(history) == 2 and KEY not in json.dumps(history)
    assert KEY not in (run / "uploads.json").read_text(encoding="utf-8")


def test_invalid_draft_uploads_nothing(tmp_path):
    run = make_run(tmp_path)
    server = FakePostiz()
    client = PostizClient("https://p.test", KEY, httpx.MockTransport(server))
    bad = [ChannelDraft("yt1", "youtube", "YT", "desc", "", "private")]
    file = Path(review.load_manifest(run)["clips"][0]["file"])
    with pytest.raises(ValueError):
        postiz.send_clip(client, run, 1, file, "draft", FUTURE, bad)
    assert server.uploads == 0


def test_caption_crop_adds_clip_on_line_boundaries(tmp_path):
    run = make_run(tmp_path)
    number = review.add_clip_from_captions(run, 1, 3, hook="My hook")
    assert number == 2
    clip = review.load_manifest(run)["clips"][1]
    assert clip["start"] == pytest.approx(4.9) and clip["end"] == pytest.approx(19.05)
    assert clip["hook"] == "My hook" and clip["file"] is None
    assert clip["title"].startswith("line1word0")
    selection = json.loads((run / "selection.json").read_text(encoding="utf-8"))
    assert len(selection) == 2 and selection[1]["start"] == clip["start"]


@pytest.mark.parametrize("first,last", [(-1, 2), (3, 1), (0, 99)])
def test_caption_crop_rejects_bad_lines(tmp_path, first, last):
    run = make_run(tmp_path)
    with pytest.raises(ValueError):
        review.add_clip_from_captions(run, first, last)
    assert len(review.load_manifest(run)["clips"]) == 1


def test_only_approved_rendered_clips_reach_step_two(tmp_path):
    run = make_run(tmp_path, approved=False)
    assert review.approved_clips(run) == []
    review.set_decision(run, 1, "approved")
    assert [c["number"] for c in review.approved_clips(run)] == [1]
    review.add_clip_from_captions(run, 1, 2)
    review.set_decision(run, 2, "approved")
    assert [c["number"] for c in review.approved_clips(run)] == [1]


def test_postiz_key_is_stored_in_vault():
    credentials.save("POSTIZ_API_KEY", KEY)
    assert credentials.postiz_key() == KEY and credentials.status("POSTIZ_API_KEY") == "stored"


def test_step_pages_render_without_key(tmp_path):
    make_run(tmp_path)
    app = AppTest.from_file(APP, default_timeout=30).run()
    app.sidebar.radio[0].set_value("Step 1 · Clips").run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Step 2 · Post").run()
    assert not app.exception
    assert any("Postiz API key" in w.value for w in app.warning)
