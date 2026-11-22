import json
import time
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from streamlit.testing.v1 import AppTest

from clipgen import credentials, jobs, review
from clipgen.pipeline import safe_name

APP = str(Path(__file__).parents[1] / "src" / "clipgen" / "app.py")
SECRET = "test-secret-value-12345"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise PasswordDeleteError()
        del self.items[(service, username)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep tests away from the real credential vault and real runs."""
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.setenv("CLIPGEN_HOME", str(tmp_path))
    for name in credentials.ALL_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield backend
    keyring.set_keyring(previous)


def make_run(root: Path) -> Path:
    run = root / "abc123"
    (run / "clips").mkdir(parents=True)
    clip = {"start": 1.0, "end": 21.0, "score": 80.0, "title": "First title", "hook": "Hook",
            "why": "Reason", "tiktok_caption": "tt", "instagram_caption": "ig"}
    file = run / "clips" / f"{safe_name(1, 'First title')}.mp4"
    file.write_bytes(b"video")
    (run / "selection.json").write_text(json.dumps([clip]), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({
        "title": "Video", "target": "x", "source": "x",
        "clips": [clip | {"number": 1, "file": str(file)}],
    }), encoding="utf-8")
    return run


def test_credentials_status_never_returns_value(isolated):
    assert credentials.status("TIKTOK_ACCESS_TOKEN") == "missing"
    credentials.save("TIKTOK_ACCESS_TOKEN", f"  {SECRET} ")
    assert isolated.items[("clipgen", "TIKTOK_ACCESS_TOKEN")] == SECRET
    assert credentials.status("TIKTOK_ACCESS_TOKEN") == "stored"
    credentials.remove("TIKTOK_ACCESS_TOKEN")
    assert credentials.status("TIKTOK_ACCESS_TOKEN") == "missing"


def test_unknown_credential_is_rejected():
    with pytest.raises(ValueError):
        credentials.save("PATH", "x")


def test_only_ai_keys_reach_the_process_environment(monkeypatch):
    import os

    credentials.save("OPENAI_API_KEY", SECRET)
    credentials.save("TIKTOK_ACCESS_TOKEN", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    credentials.load_ai_credentials()
    assert os.environ["OPENAI_API_KEY"] == SECRET
    assert "TIKTOK_ACCESS_TOKEN" not in os.environ


def test_credentials_page_saves_without_showing_secret(isolated):
    app = AppTest.from_file(APP, default_timeout=30).run()
    app.sidebar.radio[0].set_value("Credentials").run()
    assert not app.exception
    field = next(t for t in app.text_input if (t.key or "").startswith("input-TIKTOK_ACCESS_TOKEN"))
    field.set_value(SECRET)
    form_button = next(b for b in app.button if "credentials-TikTok" in b.proto.id or "credentials-TikTok" in str(b.proto.form_id))
    form_button.click().run()
    assert not app.exception
    assert isolated.items[("clipgen", "TIKTOK_ACCESS_TOKEN")] == SECRET
    assert all(t.value != SECRET for t in app.text_input)
    assert SECRET not in str(app.main)


def test_create_page_rejects_unsupported_url():
    app = AppTest.from_file(APP, default_timeout=30).run()
    next(t for t in app.text_input if t.label == "Video").set_value("https://evil.example/video")
    next(b for b in app.button if b.label == "Create clips").click().run()
    assert any("Only https" in e.value for e in app.error)
    assert jobs.list_jobs() == []


def test_review_edit_keeps_files_and_manifest_in_step(tmp_path):
    run = make_run(tmp_path)
    review.update_clip(run, 1, {"title": "New title", "hook": "New hook"})
    manifest = review.load_manifest(run)
    clip = manifest["clips"][0]
    assert clip["title"] == "New title" and Path(clip["file"]).name == "01-new-title.mp4"
    assert Path(clip["file"]).exists()
    assert json.loads((run / "selection.json").read_text(encoding="utf-8"))[0]["hook"] == "New hook"
    review.set_decision(run, 1, "approved")
    assert review.load_decisions(run) == {"1": "approved"}


def test_stale_job_is_reported_failed(tmp_path):
    job = jobs.jobs_root() / "old"
    job.mkdir(parents=True)
    (job / "job.json").write_text('{"kind":"run"}', encoding="utf-8")
    (job / "status.json").write_text('{"state":"running","percent":50}', encoding="utf-8")
    beat = job / "heartbeat"
    beat.touch()
    past = time.time() - 120
    import os
    os.utime(beat, (past, past))
    assert jobs.read_status(job)["state"] == "failed"
    assert jobs.active_job() is None


def test_real_background_job_reports_failure(tmp_path):
    job_id = jobs.start_job({"kind": "render", "run_folder": str(tmp_path / "missing"),
                             "layout": "blur", "cam": None, "only": None})
    with pytest.raises(RuntimeError):
        jobs.start_job({"kind": "render", "run_folder": "x", "layout": "blur"})
    job_dir = jobs.jobs_root() / job_id
    deadline = time.time() + 60
    while jobs.read_status(job_dir)["state"] in jobs.ACTIVE and time.time() < deadline:
        time.sleep(0.2)
    status = jobs.read_status(job_dir)
    assert status["state"] == "failed" and status["error"]
