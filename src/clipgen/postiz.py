"""Client for Postiz's public API. Postiz runs as a separate service and no code is shared."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .jobs import _write_json, home

DEFAULT_URL = "https://api.postiz.com/public/v1"
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
YOUTUBE = {"youtube"}
TIKTOK = {"tiktok", "tiktok-business"}
INSTAGRAM = {"instagram", "instagram-standalone"}
SUPPORTED = YOUTUBE | TIKTOK | INSTAGRAM
MODES = ("draft", "schedule", "now")
YOUTUBE_PRIVACY = ("private", "unlisted", "public")
TIKTOK_PRIVACY = ("inbox", "SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE")


class PostizError(RuntimeError):
    """A Postiz request failed. The message never contains the API key."""


def normalize_url(url: str) -> str:
    """Accept a Postiz API address, requiring TLS unless it is on this computer."""
    value = url.strip().rstrip("/")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a plain Postiz address, such as https://postiz.example.com/api")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and host in LOOPBACK):
        raise ValueError("The Postiz address must use https, unless it runs on this computer.")
    return value if value.endswith("/public/v1") else f"{value}/public/v1"


def _settings_path() -> Path:
    return home() / "settings.json"


def get_url() -> str:
    if os.environ.get("POSTIZ_URL"):
        return normalize_url(os.environ["POSTIZ_URL"])
    path = _settings_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("postiz_url") or DEFAULT_URL
    return DEFAULT_URL


def save_url(url: str) -> str:
    value = normalize_url(url)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    _write_json(path, current | {"postiz_url": value})
    return value


class PostizClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None):
        self.base_url = normalize_url(base_url)
        # Redirects stay off so the key can never be forwarded to another host.
        self.client = httpx.Client(
            base_url=self.base_url + "/",
            headers={"Authorization": api_key},
            timeout=httpx.Timeout(30, read=600, write=600),
            follow_redirects=False,
            transport=transport,
        )

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise PostizError(f"Could not reach Postiz ({type(error).__name__}).") from None
        if response.status_code == 401:
            raise PostizError("Postiz rejected the API key.")
        if not response.is_success:
            try:
                data = response.json()
                detail = data.get("msg") or data.get("message") or data.get("error")
            except (ValueError, AttributeError):
                detail = None
            raise PostizError(f"Postiz returned {response.status_code}: {str(detail or 'request failed')[:400]}")
        try:
            return response.json()
        except ValueError:
            raise PostizError("Postiz returned an unreadable response.") from None

    def channels(self) -> list[dict]:
        data = self._request("GET", "integrations")
        if not isinstance(data, list):
            raise PostizError("Postiz returned an unexpected channel list.")
        return [c for c in data if c.get("identifier") in SUPPORTED and not c.get("disabled")]

    def upload(self, file: Path) -> dict:
        if file.stat().st_size > MAX_UPLOAD_BYTES:
            raise PostizError("This clip is larger than Postiz's 1 GB upload limit.")
        with file.open("rb") as stream:
            data = self._request("POST", "upload", files={"file": (file.name, stream, "video/mp4")})
        if not isinstance(data, dict) or not data.get("id") or not data.get("path"):
            raise PostizError("Postiz did not return the uploaded file.")
        return {"id": data["id"], "path": data["path"]}

    def create_post(self, body: dict):
        return self._request("POST", "posts", json=body)


@dataclass(slots=True)
class ChannelDraft:
    channel_id: str
    identifier: str
    name: str
    text: str
    title: str = ""
    privacy: str = "private"


def channel_settings(draft: ChannelDraft) -> dict:
    """Build settings with the safest visibility the chosen option allows."""
    if draft.identifier in YOUTUBE:
        if draft.privacy not in YOUTUBE_PRIVACY:
            raise ValueError("Choose a YouTube visibility.")
        title = draft.title.strip()
        if not 1 <= len(title) <= 100:
            raise ValueError("YouTube titles must be 1 to 100 characters.")
        return {"title": title, "type": draft.privacy, "selfDeclaredMadeForKids": "no",
                "tags": [], "made_with_ai": False}
    if draft.identifier in TIKTOK:
        if draft.privacy not in TIKTOK_PRIVACY:
            raise ValueError("Choose a TikTok visibility.")
        inbox = draft.privacy == "inbox"
        return {
            "title": draft.title.strip()[:90],
            "privacy_level": "SELF_ONLY" if inbox else draft.privacy,
            "duet": False,
            "stitch": False,
            "comment": True,
            "autoAddMusic": "no",
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "video_made_with_ai": False,
            "content_posting_method": "UPLOAD" if inbox else "DIRECT_POST",
        }
    if draft.identifier in INSTAGRAM:
        return {"post_type": "post", "collaborators": [], "made_with_ai": False}
    raise ValueError(f"Unsupported channel: {draft.identifier}")


def build_post(mode: str, when: datetime, media: dict, drafts: list[ChannelDraft]) -> dict:
    if mode not in MODES:
        raise ValueError(f"Unknown post mode: {mode}")
    if not drafts:
        raise ValueError("Choose at least one channel.")
    if when.tzinfo is None:
        raise ValueError("The post time needs a time zone.")
    if mode == "schedule" and when <= datetime.now(timezone.utc):
        raise ValueError("Choose a time in the future.")
    moment = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "type": mode,
        "date": moment,
        "shortLink": False,
        "tags": [],
        "posts": [
            {
                "integration": {"id": draft.channel_id},
                "group": uuid.uuid4().hex[:10],
                "settings": channel_settings(draft),
                "value": [{
                    "id": uuid.uuid4().hex[:10],
                    "content": draft.text,
                    "delay": 0,
                    "image": [{"id": media["id"], "path": media["path"]}],
                }],
            }
            for draft in drafts
        ],
    }


def load_history(run: Path) -> list[dict]:
    path = run / "posts.json"
    return json.loads(path.read_text(encoding="utf-8")).get("posts", []) if path.exists() else []


def _cached_upload(client: PostizClient, run: Path, file: Path) -> dict:
    """Avoid uploading the same render twice when a post is retried."""
    path = run / "uploads.json"
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    info = file.stat()
    key = f"{client.base_url}|{file.name}|{info.st_size}|{info.st_mtime_ns}"
    if key not in cache:
        cache[key] = client.upload(file)
        _write_json(path, cache)
    return cache[key]


def send_clip(client: PostizClient, run: Path, number: int, file: Path, mode: str,
              when: datetime, drafts: list[ChannelDraft]) -> dict:
    # Validate everything locally before sending the video anywhere.
    build_post(mode, when, {"id": "check", "path": "check"}, drafts)
    media = _cached_upload(client, run, file)
    response = client.create_post(build_post(mode, when, media, drafts))
    record = {
        "clip": number,
        "mode": mode,
        "date": when.astimezone(timezone.utc).isoformat(timespec="minutes"),
        "sent_at": time.time(),
        "channels": [{"id": d.channel_id, "name": d.name, "identifier": d.identifier, "privacy": d.privacy}
                     for d in drafts],
        "response": response if isinstance(response, (list, dict)) else None,
    }
    _write_json(run / "posts.json", {"posts": [*load_history(run), record]})
    return record
