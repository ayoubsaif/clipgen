"""Store API credentials in the operating system's vault, never in project files."""

from __future__ import annotations

import os
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "clipgen"


@dataclass(frozen=True, slots=True)
class Credential:
    name: str
    label: str


GROUPS: dict[str, tuple[Credential, ...]] = {
    "Postiz (used for posting)": (
        Credential("POSTIZ_API_KEY", "Postiz API key"),
    ),
    "YouTube Shorts": (
        Credential("YOUTUBE_CLIENT_ID", "OAuth client ID"),
        Credential("YOUTUBE_CLIENT_SECRET", "OAuth client secret"),
        Credential("YOUTUBE_REFRESH_TOKEN", "Refresh token"),
    ),
    "TikTok": (
        Credential("TIKTOK_CLIENT_KEY", "Client key"),
        Credential("TIKTOK_CLIENT_SECRET", "Client secret"),
        Credential("TIKTOK_ACCESS_TOKEN", "Access token"),
        Credential("TIKTOK_REFRESH_TOKEN", "Refresh token"),
    ),
    "Instagram Reels": (
        Credential("INSTAGRAM_USER_ID", "Instagram professional account ID"),
        Credential("INSTAGRAM_ACCESS_TOKEN", "Access token"),
    ),
    "AI providers": (
        Credential("ANTHROPIC_API_KEY", "Anthropic API key"),
        Credential("OPENAI_API_KEY", "OpenAI API key"),
    ),
}
ALL_NAMES = frozenset(item.name for group in GROUPS.values() for item in group)
AI_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _check(name: str) -> None:
    if name not in ALL_NAMES:
        raise ValueError(f"Unknown credential: {name}")


def save(name: str, value: str) -> None:
    _check(name)
    value = value.strip()
    if not value:
        raise ValueError("The credential is empty.")
    keyring.set_password(SERVICE, name, value)


def remove(name: str) -> None:
    _check(name)
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass


def status(name: str) -> str:
    """Return where a credential is available, without returning its value."""
    _check(name)
    try:
        if keyring.get_password(SERVICE, name):
            return "stored"
    except KeyringError:
        return "unavailable"
    return "environment" if os.environ.get(name) else "missing"


def postiz_key() -> str | None:
    """Return the Postiz key for an API call. Never show or log this value."""
    try:
        stored = keyring.get_password(SERVICE, "POSTIZ_API_KEY")
    except KeyringError:
        stored = None
    return stored or os.environ.get("POSTIZ_API_KEY") or None


def load_ai_credentials() -> None:
    """Give this process its model keys. Social tokens are not exposed to model calls."""
    for name in AI_NAMES:
        if os.environ.get(name):
            continue
        try:
            value = keyring.get_password(SERVICE, name)
        except KeyringError:
            value = None
        if value:
            os.environ[name] = value
