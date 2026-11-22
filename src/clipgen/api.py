"""Local HTTP API for the clipgen engine, used by the desktop interface.

It listens on this computer only. Every request needs the per-launch token, except the health check and
short-lived signed media links, which exist because <video> and <img> tags cannot send headers.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware

from . import assistant, credentials, jobs, postiz, review, webcam
from .ingest import validate_url
from .llm import PROVIDERS, ProviderError, make_provider
from .render import LAYOUTS
from .tools import ToolError

API_VERSION = "1"
DEFAULT_PORT = 8765
HOST = "127.0.0.1"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
# Tauri's app origins, plus the Vite dev server used in the next phase.
ALLOWED_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                   "http://localhost:5173", "http://127.0.0.1:5173")
MEDIA_PATH = re.compile(r"^/api/runs/[^/]+/(clips/\d+/video|webcam/frames/\d+)$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MEDIA_SECONDS = 6 * 3600
WHISPER_MODELS = ("large-v3-turbo", "medium", "small", "tiny")
LOG_LINES = 200
UI_DIR = Path(__file__).with_name("webui")
UI_PATH = re.compile(r"^/(?:|favicon\.png|assets/[A-Za-z0-9_-][A-Za-z0-9._-]*)$")
UI_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".woff2": "font/woff2", ".png": "image/png"}
UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
          "media-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; form-action 'none'; "
          "frame-ancestors 'none'; base-uri 'none'")

Provider = Literal["claude-cli", "anthropic", "openai", "opencode"]
Layout = Literal["center", "blur", "facecam"]
Whisper = Literal["large-v3-turbo", "medium", "small", "tiny"]
Page = Literal["Create", "Progress", "Step 1 · Clips", "Step 2 · Post", "Credentials"]

_state_lock = threading.RLock()
_chat_lock = threading.Lock()
_post_lock = threading.Lock()
_chats_running: set[str] = set()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(Strict):
    target: str = Field(min_length=1, max_length=2048)
    count: int = Field(5, ge=1, le=60)
    min_seconds: float = Field(20, ge=5, le=180)
    max_seconds: float = Field(60, ge=5, le=180)
    provider: Provider = "claude-cli"
    model: str | None = Field(None, max_length=120)
    layout: Layout = "blur"
    cam: tuple[int, int, int, int] | None = None
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    live_minutes: float = Field(30, ge=1, le=720)
    language: str | None = Field(None, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    whisper_model: Whisper = "large-v3-turbo"
    force_whisper: bool = False


class RenderRequest(Strict):
    clips: list[int] | None = Field(None, max_length=200)
    layout: Layout | None = None
    cam: tuple[int, int, int, int] | None = None


class ClipPatch(Strict):
    title: str | None = Field(None, min_length=1, max_length=100)
    hook: str | None = Field(None, max_length=80)
    tiktok_caption: str | None = Field(None, max_length=2200)
    instagram_caption: str | None = Field(None, max_length=2200)
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)


class DecisionRequest(Strict):
    decision: Literal["pending", "approved", "rejected"]


class CaptionClipRequest(Strict):
    first: int = Field(ge=0)
    last: int = Field(ge=0)
    title: str = Field("", max_length=100)
    hook: str = Field("", max_length=80)
    render: bool = True
    layout: Layout | None = None


class WebcamRequest(Strict):
    cam: tuple[int, int, int, int]
    frame: int = Field(0, ge=0)


class ChatRequest(Strict):
    content: str = Field(min_length=1, max_length=assistant.MAX_MESSAGE)
    focus_clip: int | None = None
    page: Page = "Step 1 · Clips"
    provider: Provider = "claude-cli"
    model: str | None = Field(None, max_length=120)


class ApplyRequest(Strict):
    action_ids: list[str] = Field(min_length=1, max_length=20)
    layout: Layout | None = None


class SecretRequest(Strict):
    value: SecretStr = Field(min_length=1, max_length=8192)


class PostizSettings(Strict):
    url: str = Field(min_length=1, max_length=512)


class ChannelPost(Strict):
    channel_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=5000)
    title: str = Field("", max_length=100)
    privacy: str | None = Field(None, max_length=40)


class PostRequest(Strict):
    mode: Literal["draft", "schedule", "now"] = "draft"
    when: datetime | None = None
    channels: list[ChannelPost] = Field(min_length=1, max_length=20)
    confirm: bool = False
    resend: bool = False


def _sign(key: bytes, path: str, expires: int) -> str:
    return hmac.new(key, f"{path}|{expires}".encode(), hashlib.sha256).hexdigest()


class Guard:
    """Reject other websites, rebinding attempts and requests without the launch token."""

    def __init__(self, app, token: str, media_key: bytes, port: int | None, ui: bool = False):
        self.app = app
        self.token = token.encode()
        self.media_key = media_key
        self.port = port
        self.ui = ui
        self.origins = {*ALLOWED_ORIGINS, *([f"http://{HOST}:{port}"] if ui and port else [])}

    def _ui_file(self, scope) -> bool:
        """The page and its assets hold no secrets, and the page needs them before it can use the token."""
        return self.ui and scope["method"] in ("GET", "HEAD") and bool(UI_PATH.fullmatch(scope["path"]))

    def _host_allowed(self, value: str) -> bool:
        match = re.fullmatch(r"([^:]+)(?::(\d+))?", value.lower())
        if not match or match.group(1) not in LOCAL_HOSTS:
            return False
        return self.port is None or match.group(2) == str(self.port)

    def _media_allowed(self, scope) -> bool:
        if scope["method"] not in ("GET", "HEAD") or not MEDIA_PATH.fullmatch(scope["path"]):
            return False
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        try:
            expires = int(query["exp"][0])
            signature = query["sig"][0]
        except (KeyError, ValueError, IndexError):
            return False
        return expires >= time.time() and hmac.compare_digest(
            _sign(self.media_key, scope["path"], expires).encode(), signature.encode("utf-8", "ignore"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if not self._host_allowed(headers.get("host", "")) or (origin and origin not in self.origins):
            await JSONResponse({"detail": "This request is not allowed."}, 403)(scope, receive, send)
            return
        if scope["path"] != "/api/health" and not self._ui_file(scope):
            supplied = headers.get("authorization", "").encode("utf-8", "ignore")
            if not hmac.compare_digest(supplied, b"Bearer " + self.token) and not self._media_allowed(scope):
                await JSONResponse({"detail": "Missing or invalid token."}, 401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _run_dir(run_id: str) -> Path:
    if not SAFE_ID.fullmatch(run_id):
        raise HTTPException(404, "Video not found.")
    run = jobs.home() / run_id
    if not (run / "manifest.json").is_file():
        raise HTTPException(404, "Video not found.")
    return run


def _clip(run: Path, number: int) -> dict:
    clip = next((c for c in review.load_manifest(run)["clips"] if c["number"] == number), None)
    if clip is None:
        raise HTTPException(404, "Clip not found.")
    return clip


def _clip_file(run: Path, clip: dict) -> Path | None:
    """Only serve rendered videos from this run's clips folder, whatever the manifest says."""
    if not clip.get("file"):
        return None
    file = Path(clip["file"]).resolve()
    folder = (run / "clips").resolve()
    return file if file.is_relative_to(folder) and file.suffix.lower() == ".mp4" and file.is_file() else None


def _job_view(job: dict) -> dict:
    status, spec = job["status"], job["spec"]
    folder = status.get("run_folder") or spec.get("run_folder")
    return {"id": job["id"], "kind": spec.get("kind"), "target": spec.get("target"),
            "run_id": Path(folder).name if folder else None, "state": status.get("state", "queued"),
            "percent": min(int(status.get("percent", 0)), 100), "stage": status.get("stage", ""),
            "error": status.get("error")}


def _job(job_id: str) -> dict:
    if not SAFE_ID.fullmatch(job_id):
        raise HTTPException(404, "Job not found.")
    job = next((item for item in jobs.list_jobs() if item["id"] == job_id), None)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job


def _start_render(run: Path, numbers: list[int] | None, layout: str | None = None, cam=None) -> str:
    saved = webcam.load_position(run)
    chosen = layout or ("facecam" if saved else "blur")
    if chosen == "facecam" and cam is None and saved:
        cam = saved["cam"]
    return jobs.start_job({"kind": "render", "run_folder": str(run), "layout": chosen,
                           "cam": list(cam) if chosen == "facecam" and cam else None, "only": numbers})


def _require_idle() -> None:
    if jobs.active_job():
        raise HTTPException(409, "Another job is running. Wait for it or cancel it first.")


def _chat_view(run: Path, messages: list[dict]) -> list[dict]:
    manifest = review.load_manifest(run)
    view = []
    for message in messages:
        actions = []
        for action in message.get("actions", []):
            try:
                description = assistant.describe(action, manifest)
            except (KeyError, TypeError, ValueError):
                description = "This change is no longer available."
            actions.append(action | {"description": description, "needs_render": assistant.needs_render(action)})
        view.append({"role": message["role"], "content": message["content"], "actions": actions})
    return view


def _default_privacy(identifier: str) -> str:
    if identifier in postiz.YOUTUBE:
        return "private"
    return "inbox" if identifier in postiz.TIKTOK else "public"


def _postiz_client() -> postiz.PostizClient:
    key = credentials.postiz_key()
    if not key:
        raise HTTPException(409, "Add your Postiz API key first.")
    return postiz.PostizClient(postiz.get_url(), key)


def _ui_response(relative: str) -> FileResponse:
    root = UI_DIR.resolve()
    file = (root / relative).resolve()
    if not file.is_relative_to(root) or not file.is_file() or file.suffix not in UI_TYPES:
        raise HTTPException(404, "Not found.")
    cache = "public, max-age=31536000, immutable" if relative.startswith("assets/") else "no-store"
    return FileResponse(file, media_type=UI_TYPES[file.suffix], headers={
        "Content-Security-Policy": UI_CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY", "Cache-Control": cache})


def create_app(token: str, port: int | None = None, ui: bool = False) -> FastAPI:
    if len(token) < 32:
        raise ValueError("The API token is too short.")
    media_key = hashlib.sha256(b"clipgen-media|" + token.encode()).digest()
    app = FastAPI(title="clipgen", version=API_VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, token=token, media_key=media_key, port=port, ui=ui)
    app.add_middleware(CORSMiddleware, allow_origins=list(ALLOWED_ORIGINS),
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Range"],
                       expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"])

    def media_url(path: str, version: int = 0) -> str:
        expires = int(time.time()) + MEDIA_SECONDS
        return f"{path}?exp={expires}&sig={_sign(media_key, path, expires)}&v={version}"

    def clip_view(run: Path, clip: dict, decisions: dict[str, str]) -> dict:
        file = _clip_file(run, clip)
        fields = ("number", "start", "end", "score", "title", "hook", "why", "tiktok_caption", "instagram_caption")
        view = {name: clip.get(name) for name in fields}
        view |= {"duration": round(clip["end"] - clip["start"], 3),
                 "decision": decisions.get(str(clip["number"]), "pending"), "rendered": bool(file), "video_url": None}
        if file:
            view["video_url"] = media_url(f"/api/runs/{run.name}/clips/{clip['number']}/video", file.stat().st_mtime_ns)
        return view

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError):
        # FastAPI's default response repeats the submitted input, which could be a credential.
        details = [{"field": ".".join(str(part) for part in item["loc"][1:]), "message": item["msg"]}
                   for item in error.errors()]
        return JSONResponse({"detail": "Invalid request.", "errors": details}, 422)

    @app.exception_handler(ValueError)
    async def bad_value(_: Request, error: ValueError):
        return JSONResponse({"detail": str(error)[:500]}, 400)

    @app.exception_handler(postiz.PostizError)
    async def postiz_failed(_: Request, error: postiz.PostizError):
        return JSONResponse({"detail": str(error)}, 502)

    @app.exception_handler(ProviderError)
    async def provider_failed(_: Request, error: ProviderError):
        return JSONResponse({"detail": str(error)[:800]}, 502)

    @app.exception_handler(ToolError)
    async def tool_failed(_: Request, error: ToolError):
        return JSONResponse({"detail": str(error)[:800]}, 500)

    @app.exception_handler(RuntimeError)
    async def conflict(_: Request, error: RuntimeError):
        return JSONResponse({"detail": str(error)[:500]}, 409)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "api_version": API_VERSION}

    @app.get("/api/meta")
    def meta() -> dict:
        return {"providers": list(PROVIDERS), "layouts": list(LAYOUTS), "whisper_models": list(WHISPER_MODELS),
                "post_modes": list(postiz.MODES), "youtube_privacy": list(postiz.YOUTUBE_PRIVACY),
                "tiktok_privacy": list(postiz.TIKTOK_PRIVACY), "pages": list(assistant.PAGES)}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        result = []
        for run in review.list_runs(jobs.home()):
            manifest = review.load_manifest(run)
            decisions = review.load_decisions(run)
            clips = manifest["clips"]
            result.append({"id": run.name, "title": manifest.get("title", run.name), "target": manifest.get("target"),
                           "clips": len(clips), "updated_at": (run / "manifest.json").stat().st_mtime,
                           "approved": sum(decisions.get(str(c["number"])) == "approved" for c in clips)})
        return result

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = _run_dir(run_id)
        manifest = review.load_manifest(run)
        decisions = review.load_decisions(run)
        return {"id": run.name, "title": manifest.get("title", run.name), "target": manifest.get("target"),
                "clips": [clip_view(run, clip, decisions) for clip in manifest["clips"]],
                "webcam": webcam.load_position(run)}

    @app.get("/api/runs/{run_id}/transcript")
    def get_transcript(run_id: str) -> dict:
        run = _run_dir(run_id)
        try:
            transcript = review.load_transcript(run)
        except (OSError, KeyError, json.JSONDecodeError):
            raise HTTPException(404, "This video has no saved captions.") from None
        return {"language": transcript.language, "source": transcript.source, "duration": transcript.duration,
                "lines": [{"index": index, "start": segment.start, "end": segment.end, "text": segment.text.strip()}
                          for index, segment in enumerate(transcript.segments)]}

    @app.patch("/api/runs/{run_id}/clips/{number}")
    def patch_clip(run_id: str, number: int, body: ClipPatch) -> dict:
        run = _run_dir(run_id)
        _clip(run, number)
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(400, "Fields cannot be empty.")
        if ("start" in changes) != ("end" in changes):
            raise HTTPException(400, "Send both start and end.")
        with _state_lock:
            if "start" in changes:
                review.set_clip_times(run, number, changes.pop("start"), changes.pop("end"))
            if changes:
                review.update_clip(run, number, changes)
            return clip_view(run, _clip(run, number), review.load_decisions(run))

    @app.put("/api/runs/{run_id}/clips/{number}/decision")
    def put_decision(run_id: str, number: int, body: DecisionRequest) -> dict:
        run = _run_dir(run_id)
        _clip(run, number)
        with _state_lock:
            review.set_decision(run, number, body.decision)
        return {"number": number, "decision": body.decision}

    @app.post("/api/runs/{run_id}/clips/from-captions", status_code=201)
    def clip_from_captions(run_id: str, body: CaptionClipRequest) -> dict:
        run = _run_dir(run_id)
        if body.render:
            _require_idle()
        with _state_lock:
            number = review.add_clip_from_captions(run, body.first, body.last, body.title, body.hook)
            job_id = _start_render(run, [number], body.layout) if body.render else None
        return {"number": number, "job_id": job_id}

    @app.get("/api/runs/{run_id}/clips/{number}/video")
    def clip_video(run_id: str, number: int):
        run = _run_dir(run_id)
        file = _clip_file(run, _clip(run, number))
        if file is None:
            raise HTTPException(404, "This clip has not been rendered.")
        return FileResponse(file, media_type="video/mp4")

    @app.post("/api/runs/{run_id}/render", status_code=202)
    def render(run_id: str, body: RenderRequest) -> dict:
        run = _run_dir(run_id)
        known = {clip["number"] for clip in review.load_manifest(run)["clips"]}
        if body.clips is not None and (not body.clips or not set(body.clips) <= known):
            raise HTTPException(400, "Choose clips from this video.")
        _require_idle()
        with _state_lock:
            return {"job_id": _start_render(run, sorted(set(body.clips)) if body.clips else None, body.layout, body.cam)}

    @app.get("/api/jobs")
    def list_all_jobs() -> list[dict]:
        return [_job_view(job) for job in jobs.list_jobs()[:50]]

    @app.post("/api/jobs", status_code=202)
    def create_job(body: RunRequest) -> dict:
        if body.min_seconds > body.max_seconds:
            raise HTTPException(400, "Minimum seconds cannot be above maximum seconds.")
        if body.start is not None and body.end is not None and body.start >= body.end:
            raise HTTPException(400, "The start time must be before the end time.")
        if body.provider == "openai" and not (body.model or os.environ.get("OPENAI_MODEL")):
            raise HTTPException(400, "Enter a ChatGPT model name.")
        needed = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(body.provider)
        if needed and credentials.status(needed) == "missing":
            raise HTTPException(409, "Add this provider's API key first.")
        target = body.target.strip().strip('"')
        spec = body.model_dump() | {"kind": "run", "target": target if Path(target).is_file() else validate_url(target),
                                    "cam": list(body.cam) if body.layout == "facecam" and body.cam else None}
        with _state_lock:
            return {"job_id": jobs.start_job(spec)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return _job_view(_job(job_id))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict:
        job = _job(job_id)
        jobs.cancel_job(job)
        return _job_view(_job(job_id))

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str) -> dict:
        log = _job(job_id)["dir"] / "log.txt"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
        return {"lines": lines[-LOG_LINES:]}

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            last, quiet = None, 0
            while not await request.is_disconnected():
                views = await run_in_threadpool(lambda: [_job_view(job) for job in jobs.list_jobs()[:50]])
                text = json.dumps(views, ensure_ascii=False)
                if text != last:
                    last, quiet = text, 0
                    yield f"event: jobs\ndata: {text}\n\n"
                else:
                    quiet += 1
                    if quiet >= 15:
                        quiet = 0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def webcam_frames(run: Path) -> list[Path]:
        manifest = review.load_manifest(run)
        source = Path(manifest["source"])
        if not source.exists():
            raise HTTPException(409, "The source video is no longer on this computer.")
        return webcam.prepare_frames(run, source, [(c["start"] + c["end"]) / 2 for c in manifest["clips"]])

    @app.get("/api/runs/{run_id}/webcam")
    def get_webcam(run_id: str) -> dict:
        run = _run_dir(run_id)
        frames = webcam_frames(run)
        views = []
        for index, frame in enumerate(frames):
            width, height = webcam.frame_size(frame)
            views.append({"index": index, "width": width, "height": height,
                          "url": media_url(f"/api/runs/{run.name}/webcam/frames/{index}")})
        return {"position": webcam.load_position(run), "frames": views}

    @app.get("/api/runs/{run_id}/webcam/frames/{index}")
    def webcam_frame(run_id: str, index: int):
        frames = sorted((_run_dir(run_id) / "webcam_frames").glob("frame-*.jpg"))
        if not 0 <= index < len(frames):
            raise HTTPException(404, "Frame not found.")
        return FileResponse(frames[index], media_type="image/jpeg")

    @app.put("/api/runs/{run_id}/webcam")
    def put_webcam(run_id: str, body: WebcamRequest) -> dict:
        run = _run_dir(run_id)
        frames = webcam_frames(run)
        if body.frame >= len(frames):
            raise HTTPException(400, "Frame not found.")
        cam = webcam.fit_box(body.cam, *webcam.frame_size(frames[body.frame]))
        with _state_lock:
            webcam.save_position(run, cam, "manual", body.frame)
        return webcam.load_position(run)

    @app.post("/api/runs/{run_id}/webcam/detect")
    def detect_webcam(run_id: str) -> dict:
        run = _run_dir(run_id)
        try:
            found = webcam.detect(None, webcam_frames(run))
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Claude took too long to answer.") from None
        if not found:
            return {"found": False, "position": webcam.load_position(run)}
        with _state_lock:
            webcam.save_position(run, found["cam"], "claude", found["frame"])
        return {"found": True, "position": webcam.load_position(run)}

    @app.get("/api/runs/{run_id}/assistant")
    def get_chat(run_id: str) -> dict:
        run = _run_dir(run_id)
        return {"messages": _chat_view(run, assistant.load_chat(run))}

    @app.post("/api/runs/{run_id}/assistant/messages")
    def send_message(run_id: str, body: ChatRequest) -> dict:
        run = _run_dir(run_id)
        with _chat_lock:
            if run.name in _chats_running:
                raise HTTPException(409, "The assistant is still answering.")
            _chats_running.add(run.name)
        try:
            messages = assistant.load_chat(run)
            messages.append({"role": "user", "content": body.content.strip()})
            credentials.load_ai_credentials()
            try:
                answer = assistant.ask(make_provider(body.provider, body.model), run, messages, body.focus_clip, body.page)
            except subprocess.TimeoutExpired:
                raise HTTPException(504, "The assistant took too long to answer.") from None
            except (ProviderError, ValueError, HTTPException):
                raise
            except Exception as error:  # SDK errors may include request details, so return only the type.
                raise HTTPException(502, f"The assistant failed ({type(error).__name__}).") from None
            navigate = None
            for action in answer["actions"]:
                if action["type"] == "open_page":
                    action["status"] = "applied"
                    navigate = navigate or action["value"]
            with _state_lock:
                current = assistant.load_chat(run)
                assistant.save_chat(run, [*current, messages[-1], answer])
            return {"message": _chat_view(run, [answer])[0], "navigate": navigate}
        finally:
            with _chat_lock:
                _chats_running.discard(run.name)

    @app.post("/api/runs/{run_id}/assistant/actions/apply")
    def apply_actions(run_id: str, body: ApplyRequest) -> dict:
        run = _run_dir(run_id)
        with _state_lock:
            messages = assistant.load_chat(run)
            by_id = {action["id"]: action for message in messages for action in message.get("actions", [])}
            chosen = [by_id.get(action_id) for action_id in dict.fromkeys(body.action_ids)]
            if None in chosen:
                raise HTTPException(404, "Change not found.")
            if any(action["status"] != "pending" or action["type"] == "open_page" for action in chosen):
                raise HTTPException(409, "This change was already handled.")
            if any(assistant.needs_render(action) for action in chosen):
                _require_idle()
            numbers: set[int] = set()
            try:
                for action in chosen:
                    number = assistant.apply(run, action)
                    action["status"] = "applied"
                    if number:
                        numbers.add(number)
            finally:
                assistant.save_chat(run, messages)
            job_id = _start_render(run, sorted(numbers), body.layout) if numbers else None
        return {"applied": [action["id"] for action in chosen], "job_id": job_id}

    @app.post("/api/runs/{run_id}/assistant/actions/{action_id}/discard")
    def discard_action(run_id: str, action_id: str) -> dict:
        run = _run_dir(run_id)
        with _state_lock:
            messages = assistant.load_chat(run)
            action = next((a for m in messages for a in m.get("actions", []) if a["id"] == action_id), None)
            if action is None:
                raise HTTPException(404, "Change not found.")
            if action["status"] != "pending":
                raise HTTPException(409, "This change was already handled.")
            action["status"] = "discarded"
            assistant.save_chat(run, messages)
        return {"discarded": action_id}

    @app.delete("/api/runs/{run_id}/assistant", status_code=204)
    def clear_chat(run_id: str) -> None:
        run = _run_dir(run_id)
        with _state_lock:
            assistant.save_chat(run, [])

    @app.get("/api/credentials")
    def list_credentials() -> list[dict]:
        return [{"group": group, "items": [{"name": item.name, "label": item.label,
                                            "status": credentials.status(item.name)} for item in items]}
                for group, items in credentials.GROUPS.items()]

    def known_credential(name: str) -> None:
        if name not in credentials.ALL_NAMES:
            raise HTTPException(404, "Unknown credential.")

    @app.put("/api/credentials/{name}")
    def save_credential(name: str, body: SecretRequest) -> dict:
        known_credential(name)
        try:
            credentials.save(name, body.value.get_secret_value())
        except Exception as error:  # Vault errors must not echo the submitted value.
            raise HTTPException(500, f"Could not save to the vault ({type(error).__name__}).") from None
        return {"name": name, "status": credentials.status(name)}

    @app.delete("/api/credentials/{name}")
    def delete_credential(name: str) -> dict:
        known_credential(name)
        try:
            credentials.remove(name)
        except Exception as error:
            raise HTTPException(500, f"Could not update the vault ({type(error).__name__}).") from None
        return {"name": name, "status": credentials.status(name)}

    @app.get("/api/postiz/settings")
    def postiz_settings() -> dict:
        return {"url": postiz.get_url(), "key_status": credentials.status("POSTIZ_API_KEY")}

    @app.put("/api/postiz/settings")
    def save_postiz_settings(body: PostizSettings) -> dict:
        return {"url": postiz.save_url(body.url), "key_status": credentials.status("POSTIZ_API_KEY")}

    @app.get("/api/postiz/channels")
    def postiz_channels() -> list[dict]:
        return [{"id": c["id"], "name": c.get("name", c["identifier"]), "identifier": c["identifier"]}
                for c in _postiz_client().channels()]

    @app.get("/api/runs/{run_id}/posts")
    def post_history(run_id: str) -> list[dict]:
        fields = ("clip", "mode", "date", "sent_at", "channels")
        return [{name: item.get(name) for name in fields} for item in postiz.load_history(_run_dir(run_id))]

    @app.post("/api/runs/{run_id}/clips/{number}/post", status_code=201)
    def post_clip(run_id: str, number: int, body: PostRequest = Body(...)) -> dict:
        run = _run_dir(run_id)
        # One send at a time, so a double click cannot pass the "already sent" check twice.
        if not _post_lock.acquire(blocking=False):
            raise HTTPException(409, "Another post is being sent.")
        try:
            clip = next((c for c in review.approved_clips(run) if c["number"] == number), None)
            file = _clip_file(run, clip) if clip else None
            if file is None:
                raise HTTPException(409, "Approve and render this clip first.")
            # These checks live here so no interface can skip them.
            if body.mode != "draft" and not body.confirm:
                raise HTTPException(428, "Confirm before scheduling or publishing.")
            if any(item["clip"] == number for item in postiz.load_history(run)) and not body.resend:
                raise HTTPException(428, "This clip was already sent. Confirm to send it again.")
            when = datetime.now(timezone.utc)
            if body.mode == "schedule":
                if body.when is None or body.when.tzinfo is None:
                    raise HTTPException(400, "Choose a date and time with a time zone.")
                when = body.when
            client = _postiz_client()
            channels = {channel["id"]: channel for channel in client.channels()}
            drafts = []
            for item in body.channels:
                channel = channels.get(item.channel_id)
                if channel is None:
                    raise HTTPException(400, "Choose channels connected in Postiz.")
                identifier = channel["identifier"]
                privacy = "public" if identifier in postiz.INSTAGRAM else item.privacy or _default_privacy(identifier)
                drafts.append(postiz.ChannelDraft(channel["id"], identifier, channel.get("name", identifier), item.text,
                                                  item.title, privacy))
            record = postiz.send_clip(client, run, number, file, body.mode, when, drafts)
        finally:
            _post_lock.release()
        return {name: record[name] for name in ("clip", "mode", "date", "sent_at", "channels")}

    if ui:
        @app.get("/", include_in_schema=False)
        def ui_page():
            return _ui_response("index.html")

        @app.get("/favicon.png", include_in_schema=False)
        def ui_icon():
            return _ui_response("favicon.png")

        @app.get("/assets/{name}", include_in_schema=False)
        def ui_asset(name: str):
            return _ui_response(f"assets/{name}")

    return app


def export_openapi(path: Path) -> Path:
    app = create_app(secrets.token_urlsafe(32))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def serve(port: int = DEFAULT_PORT, ui: bool = False) -> int:
    """Start the API and print one JSON line that the parent app reads to connect."""
    import uvicorn

    # The token leaves the environment so background jobs and model tools never inherit it.
    token = os.environ.pop("CLIPGEN_API_TOKEN", "") or secrets.token_urlsafe(32)
    app = create_app(token, port, ui)
    print(json.dumps({"url": f"http://{HOST}:{port}", "token": token}), flush=True)
    uvicorn.run(app, host=HOST, port=port, access_log=False, log_level="warning", timeout_graceful_shutdown=3)
    return 0
