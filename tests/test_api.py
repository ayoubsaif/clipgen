import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import keyring
import pytest
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend
from PIL import Image

from clipgen import api, assistant, credentials, jobs, postiz, review, webcam
from clipgen.models import Segment, Transcript, Word
from clipgen.pipeline import safe_name

TOKEN = "test-token-" + "x" * 32
SECRET = "super-secret-credential-value"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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


class FakePostiz:
    def __init__(self):
        self.uploads = 0
        self.posts = []

    def __call__(self, request):
        path = request.url.path
        if path.endswith("/integrations"):
            return httpx.Response(200, json=[{"id": "yt1", "name": "YT", "identifier": "youtube", "disabled": False,
                                              "token": "must-not-leak"},
                                             {"id": "ig1", "name": "IG", "identifier": "instagram", "disabled": False}])
        if path.endswith("/upload"):
            self.uploads += 1
            return httpx.Response(201, json={"id": "m1", "path": "https://cdn.test/c.mp4"})
        self.posts.append(json.loads(request.content))
        return httpx.Response(201, json=[{"postId": "p1"}])


@pytest.fixture
def home(tmp_path, monkeypatch):
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setenv("CLIPGEN_HOME", str(tmp_path))
    for name in credentials.ALL_NAMES | {"POSTIZ_URL"}:
        monkeypatch.delenv(name, raising=False)
    yield tmp_path
    keyring.set_keyring(previous)


@pytest.fixture
def client(home):
    with TestClient(api.create_app(TOKEN, PORT), base_url=BASE, headers=AUTH) as test_client:
        yield test_client


@pytest.fixture
def started(monkeypatch):
    specs = []
    monkeypatch.setattr(jobs, "start_job", lambda spec: specs.append(spec) or f"job{len(specs)}")
    return specs


@pytest.fixture
def fake_postiz(monkeypatch, home):
    server = FakePostiz()
    credentials.save("POSTIZ_API_KEY", SECRET)
    monkeypatch.setattr(api, "_postiz_client",
                        lambda: postiz.PostizClient("https://p.test", SECRET, httpx.MockTransport(server)))
    return server


def make_run(root: Path, name="abc123") -> Path:
    run = root / name
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
    file.write_bytes(b"0123456789" * 10)
    source = run / "source.mp4"
    source.write_bytes(b"source")
    (run / "selection.json").write_text(json.dumps([clip]), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"title": "Video", "target": "x", "source": str(source),
                                                   "clips": [clip | {"number": 1, "file": str(file)}]}), encoding="utf-8")
    return run


def add_frame(run: Path) -> None:
    (run / "webcam_frames").mkdir()
    Image.new("RGB", (1920, 1080), "navy").save(run / "webcam_frames" / "frame-1.jpg")


def post_body(**fields):
    return {"mode": "draft", "channels": [{"channel_id": "yt1", "text": "Text", "title": "Title"}]} | fields


def test_health_is_open_and_everything_else_needs_token(home):
    bare = TestClient(api.create_app(TOKEN, PORT), base_url=BASE)
    assert bare.get("/api/health").json()["status"] == "ok"
    assert bare.get("/api/runs").status_code == 401
    assert bare.get("/api/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert bare.get("/api/credentials").status_code == 401
    assert bare.get("/openapi.json").status_code == 401
    assert bare.get("/docs").status_code == 401


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1:9999", "127.0.0.1.evil.test:8765"])
def test_rebinding_hosts_are_rejected(client, host):
    assert client.get("/api/runs", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:3000", "null"])
def test_other_websites_are_rejected(client, origin):
    response = client.get("/api/runs", headers={"Origin": origin})
    assert response.status_code == 403 and "access-control-allow-origin" not in response.headers


def test_desktop_origin_gets_cors_and_preflight(client):
    response = client.get("/api/runs", headers={"Origin": "tauri://localhost"})
    assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "tauri://localhost"
    bare = TestClient(client.app, base_url=BASE)
    preflight = bare.options("/api/runs", headers={"Origin": "tauri://localhost", "Access-Control-Request-Method": "GET",
                                                   "Access-Control-Request-Headers": "authorization"})
    assert preflight.status_code == 200


@pytest.mark.parametrize("run_id", ["..%2F..", ".jobs", "a..b", "nope"])
def test_run_ids_cannot_leave_home(client, home, run_id):
    make_run(home)
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_run_view_hides_local_paths(client, home):
    make_run(home)
    data = client.get("/api/runs/abc123").json()
    assert str(home) not in json.dumps(data) and data["clips"][0]["rendered"] is True
    assert client.get("/api/runs").json()[0]["clips"] == 1
    assert len(client.get("/api/runs/abc123/transcript").json()["lines"]) == 6


def test_signed_video_link_supports_seeking_and_expires(client, home):
    make_run(home)
    url = client.get("/api/runs/abc123").json()["clips"][0]["video_url"]
    bare = TestClient(client.app, base_url=BASE)
    response = bare.get(url, headers={"Range": "bytes=10-19"})
    assert response.status_code == 206 and response.content == b"0123456789"
    path = url.split("?")[0]
    assert bare.get(path).status_code == 401
    assert bare.get(url.replace("/clips/1/", "/clips/2/")).status_code == 401
    expired = int(time.time()) - 1
    key = api.hashlib.sha256(b"clipgen-media|" + TOKEN.encode()).digest()
    assert bare.get(f"{path}?exp={expired}&sig={api._sign(key, path, expired)}").status_code == 401


def test_manifest_cannot_point_video_outside_run(client, home):
    run = make_run(home)
    outside = home / "secret.mp4"
    outside.write_bytes(b"private")
    manifest = review.load_manifest(run)
    manifest["clips"][0]["file"] = str(outside)
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert client.get("/api/runs/abc123").json()["clips"][0]["video_url"] is None
    assert client.get("/api/runs/abc123/clips/1/video").status_code == 404


def test_edit_times_and_decision(client, home):
    make_run(home)
    data = client.patch("/api/runs/abc123/clips/1", json={"title": "New", "start": 4.9, "end": 12.4}).json()
    assert data["title"] == "New" and data["end"] == pytest.approx(12.8) and data["video_url"]
    assert client.patch("/api/runs/abc123/clips/1", json={"start": 4}).status_code == 400
    assert client.patch("/api/runs/abc123/clips/1", json={"file": "x"}).status_code == 422
    assert client.put("/api/runs/abc123/clips/1/decision", json={"decision": "approved"}).status_code == 200
    assert client.put("/api/runs/abc123/clips/9/decision", json={"decision": "approved"}).status_code == 404


def test_caption_clip_and_render_start_jobs(client, home, started):
    make_run(home)
    created = client.post("/api/runs/abc123/clips/from-captions", json={"first": 1, "last": 2, "hook": "H"})
    assert created.status_code == 201 and created.json() == {"number": 2, "job_id": "job1"}
    assert started[0]["only"] == [2] and started[0]["layout"] == "blur"
    assert client.post("/api/runs/abc123/render", json={"clips": [7]}).status_code == 400
    assert client.post("/api/runs/abc123/render", json={"clips": [1, 1]}).status_code == 202
    assert started[1]["only"] == [1]


def test_saved_webcam_is_used_when_rendering(client, home, started):
    run = make_run(home)
    webcam.save_position(run, (10, 20, 300, 200), "manual")
    client.post("/api/runs/abc123/render", json={})
    assert started[0]["layout"] == "facecam" and started[0]["cam"] == [10, 20, 300, 200]


@pytest.mark.parametrize("patch,status", [
    ({"target": "https://evil.example/video"}, 400),
    ({"min_seconds": 60, "max_seconds": 20}, 400),
    ({"provider": "shell"}, 422),
    ({"count": 0}, 422),
    ({"language": "../x"}, 422),
    ({"unknown": 1}, 422),
    ({"provider": "anthropic"}, 409),
])
def test_bad_job_requests_start_nothing(client, started, patch, status):
    assert client.post("/api/jobs", json={"target": "https://www.twitch.tv/videos/1"} | patch).status_code == status
    assert started == []


def test_valid_job_request_starts_engine_job(client, started):
    response = client.post("/api/jobs", json={"target": "https://kick.com/x", "layout": "facecam", "count": 2})
    assert response.status_code == 202 and started[0]["kind"] == "run" and started[0]["cam"] is None
    assert started[0]["count"] == 2 and started[0]["whisper_model"] == "large-v3-turbo"


def test_jobs_are_listed_without_paths(client, home):
    job = jobs.jobs_root() / "job-1"
    job.mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"kind": "render", "run_folder": str(home / "abc123")}), encoding="utf-8")
    (job / "status.json").write_text(json.dumps({"state": "done", "percent": 100, "stage": "Finished",
                                                 "run_folder": str(home / "abc123")}), encoding="utf-8")
    (job / "log.txt").write_text("line one\n", encoding="utf-8")
    listed = client.get("/api/jobs").json()
    assert listed[0]["run_id"] == "abc123" and str(home) not in json.dumps(listed)
    assert client.get("/api/jobs/job-1/log").json()["lines"] == ["line one"]
    assert client.get("/api/jobs/..%2Fx").status_code == 404


def test_webcam_position_is_kept_inside_frame(client, home):
    run = make_run(home)
    add_frame(run)
    view = client.get("/api/runs/abc123/webcam").json()
    assert view["frames"][0]["width"] == 1920 and view["frames"][0]["url"].count("sig=") == 1
    saved = client.put("/api/runs/abc123/webcam", json={"cam": [1900, 1000, 401, 301], "frame": 0}).json()
    x, y, width, height = saved["cam"]
    assert x + width <= 1920 and y + height <= 1080 and saved["origin"] == "manual"
    assert client.put("/api/runs/abc123/webcam", json={"cam": [0, 0, 100, 100], "frame": 5}).status_code == 400


def test_assistant_proposes_then_applies_once(client, home, started, monkeypatch):
    make_run(home)

    class Fake:
        def complete(self, system, user, schema):
            return {"reply": "Try this", "actions": [
                {"type": "edit_text", "clip": 1, "field": "hook", "value": "Better", "start": None, "end": None, "hook": None},
                {"type": "publish", "clip": 1, "field": None, "value": None, "start": None, "end": None, "hook": None}]}

    monkeypatch.setattr(api, "make_provider", lambda *_: Fake())
    answer = client.post("/api/runs/abc123/assistant/messages", json={"content": "better hook"}).json()
    actions = answer["message"]["actions"]
    assert [a["type"] for a in actions] == ["edit_text"] and actions[0]["needs_render"] is True
    assert client.get("/api/runs/abc123").json()["clips"][0]["hook"] == "Hook"
    body = {"action_ids": [actions[0]["id"]]}
    assert client.post("/api/runs/abc123/assistant/actions/apply", json=body).json()["job_id"] == "job1"
    assert client.get("/api/runs/abc123").json()["clips"][0]["hook"] == "Better"
    assert client.post("/api/runs/abc123/assistant/actions/apply", json=body).status_code == 409
    assert client.post("/api/runs/abc123/assistant/actions/forged/discard").status_code == 404
    assert len(client.get("/api/runs/abc123/assistant").json()["messages"]) == 2
    assert client.delete("/api/runs/abc123/assistant").status_code == 204


def test_credentials_are_write_only(client):
    saved = client.put("/api/credentials/TIKTOK_ACCESS_TOKEN", json={"value": SECRET})
    assert saved.json()["status"] == "stored" and SECRET not in saved.text
    assert SECRET not in client.get("/api/credentials").text
    assert client.put("/api/credentials/PATH", json={"value": "x"}).status_code == 404
    assert client.delete("/api/credentials/TIKTOK_ACCESS_TOKEN").json()["status"] == "missing"


def test_validation_errors_never_repeat_secrets(client):
    response = client.put("/api/credentials/TIKTOK_ACCESS_TOKEN", json={"value": SECRET, "extra": SECRET})
    assert response.status_code == 422 and SECRET not in response.text
    too_long = SECRET * 400
    response = client.put("/api/credentials/TIKTOK_ACCESS_TOKEN", json={"value": too_long})
    assert response.status_code == 422 and SECRET not in response.text


def test_post_needs_approved_clip(client, home, fake_postiz):
    make_run(home)
    assert client.post("/api/runs/abc123/clips/1/post", json=post_body()).status_code == 409
    assert fake_postiz.uploads == 0


@pytest.mark.parametrize("mode", ["now", "schedule"])
def test_publishing_requires_server_side_confirmation(client, home, fake_postiz, mode):
    run = make_run(home)
    review.set_decision(run, 1, "approved")
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = client.post("/api/runs/abc123/clips/1/post", json=post_body(mode=mode, when=when))
    assert response.status_code == 428 and fake_postiz.uploads == 0 and fake_postiz.posts == []


def test_draft_defaults_private_and_resend_needs_confirmation(client, home, fake_postiz):
    run = make_run(home)
    review.set_decision(run, 1, "approved")
    first = client.post("/api/runs/abc123/clips/1/post", json=post_body())
    assert first.status_code == 201 and SECRET not in first.text
    assert fake_postiz.posts[0]["type"] == "draft" and fake_postiz.posts[0]["posts"][0]["settings"]["type"] == "private"
    assert client.post("/api/runs/abc123/clips/1/post", json=post_body()).status_code == 428
    assert client.post("/api/runs/abc123/clips/1/post", json=post_body(resend=True)).status_code == 201
    assert fake_postiz.uploads == 1 and len(client.get("/api/runs/abc123/posts").json()) == 2


def test_unknown_channel_uploads_nothing(client, home, fake_postiz):
    run = make_run(home)
    review.set_decision(run, 1, "approved")
    body = post_body(channels=[{"channel_id": "other", "text": "x", "title": "t"}])
    assert client.post("/api/runs/abc123/clips/1/post", json=body).status_code == 400
    assert fake_postiz.uploads == 0


def test_channels_expose_only_public_fields(client, fake_postiz):
    data = client.get("/api/postiz/channels")
    assert "must-not-leak" not in data.text and set(data.json()[0]) == {"id", "name", "identifier"}


def test_postiz_address_is_validated(client):
    assert client.put("/api/postiz/settings", json={"url": "http://postiz.example.com"}).status_code == 400
    assert client.put("/api/postiz/settings", json={"url": "https://postiz.example.com"}).json()["url"].endswith("/public/v1")


def test_short_token_is_refused():
    with pytest.raises(ValueError):
        api.create_app("short")


def test_schema_can_be_exported(tmp_path):
    schema = json.loads(api.export_openapi(tmp_path / "openapi.json").read_text(encoding="utf-8"))
    assert "/api/runs/{run_id}/clips/{number}/post" in schema["paths"]


@pytest.fixture
def ui_files(tmp_path, monkeypatch):
    root = tmp_path / "webui"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>clipgen</title>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "assets" / "notes.txt").write_text("not served", encoding="utf-8")
    (tmp_path / "secret.js").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(api, "UI_DIR", root)
    return root


def test_ui_is_public_with_strict_headers_but_api_is_not(home, ui_files):
    bare = TestClient(api.create_app(TOKEN, PORT, ui=True), base_url=BASE)
    page = bare.get("/")
    assert page.status_code == 200
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert bare.get("/assets/app.js").status_code == 200
    assert bare.get("/api/runs").status_code == 401
    assert bare.post("/").status_code == 401
    assert bare.get("/docs").status_code == 401


@pytest.mark.parametrize("path", ["/assets/notes.txt", "/assets/..%2fsecret.js", "/assets/%2e%2e%2fsecret.js"])
def test_ui_cannot_read_outside_its_files(home, ui_files, path):
    bare = TestClient(api.create_app(TOKEN, PORT, ui=True), base_url=BASE)
    assert bare.get(path).status_code in (401, 404)


def test_ui_is_off_by_default_and_own_origin_needs_ui_mode(home, ui_files):
    bare = TestClient(api.create_app(TOKEN, PORT), base_url=BASE)
    assert bare.get("/").status_code == 401
    assert bare.get("/api/health", headers={"Origin": BASE}).status_code == 403
    served = TestClient(api.create_app(TOKEN, PORT, ui=True), base_url=BASE, headers=AUTH)
    assert served.get("/api/runs", headers={"Origin": BASE}).status_code == 200
    assert served.get("/api/runs", headers={"Origin": "https://evil.example"}).status_code == 403
