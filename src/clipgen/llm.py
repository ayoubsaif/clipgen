"""LLM providers. Every provider gets text in and JSON out, with no tools on the machine."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PROVIDERS = ("claude-cli", "anthropic", "openai", "opencode")
CLAUDE_MODEL = "claude-opus-5-5"
CLI_TIMEOUT = 900
_SAFE_MODEL = re.compile(r"[A-Za-z0-9._:/#\[\]-]+")


class ProviderError(RuntimeError):
    """A model call failed or returned unusable output."""


def _checked_model(model: str | None) -> str | None:
    if model is not None and not _SAFE_MODEL.fullmatch(model):
        raise ValueError(f"Invalid model name: {model!r}")
    return model


def extract_json(text: str, schema: dict) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ProviderError("The model did not return JSON.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise ProviderError(f"The model returned invalid JSON: {error}") from error
    missing = [key for key in schema.get("required", []) if key not in data]
    if missing:
        raise ProviderError(f"The model response is missing: {', '.join(missing)}")
    return data


def _image_blocks(images: list[bytes]) -> list[dict]:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(image).decode()}} for image in images]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic

        self.model = _checked_model(model) or CLAUDE_MODEL
        self.client = anthropic.Anthropic()

    def complete(self, system: str, user: str, schema: dict) -> dict:
        return self._ask(system, user, schema)

    def complete_images(self, system: str, user: str, images: list[bytes], schema: dict) -> dict:
        return self._ask(system, [*_image_blocks(images), {"type": "text", "text": user}], schema)

    def _ask(self, system: str, content, schema: dict) -> dict:
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            raise ProviderError("Claude declined this request.")
        if message.stop_reason == "max_tokens":
            raise ProviderError("Claude's response was cut off at the token limit.")
        text = next((block.text for block in message.content if block.type == "text"), "")
        return extract_json(text, schema)


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.model = _checked_model(model or os.environ.get("OPENAI_MODEL"))
        if not self.model:
            raise ValueError("Choose a ChatGPT model with --model or the OPENAI_MODEL variable.")
        self.client = OpenAI()

    def complete(self, system: str, user: str, schema: dict) -> dict:
        response = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=user,
            text={"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}},
        )
        return extract_json(response.output_text, schema)


def _claude_command() -> list[str]:
    found = shutil.which("claude")
    if not found:
        raise ProviderError("The claude command was not found.")
    if Path(found).suffix.lower() not in {".cmd", ".bat"}:
        return [found]
    # cmd.exe would mangle the JSON schema argument, so call the npm package's script directly.
    script = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    node = shutil.which("node")
    if script.exists() and node:
        return [node, str(script)]
    native = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if native.exists():
        return [str(native)]
    raise ProviderError("Could not find the Claude Code program behind the npm launcher.")


class ClaudeCliProvider:
    """Uses the existing Claude Code login, with all tools and project settings turned off."""

    name = "claude-cli"

    def __init__(self, model: str | None = None):
        self.model = _checked_model(model)
        self.command = _claude_command()

    def complete(self, system: str, user: str, schema: dict) -> dict:
        command = [
            *self.command, "-p", "--output-format", "json", "--tools", "",
            "--strict-mcp-config", "--no-session-persistence", "--setting-sources", "",
            "--system-prompt", system, "--json-schema", json.dumps(schema),
        ]
        if self.model:
            command += ["--model", self.model]
        with tempfile.TemporaryDirectory(prefix="clipgen-llm-") as empty:
            result = subprocess.run(
                command, input=user, cwd=empty, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT,
            )
        if result.returncode != 0:
            raise ProviderError(f"claude failed: {(result.stderr or result.stdout).strip()[-800:]}")
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProviderError("claude returned unreadable output.") from error
        if envelope.get("is_error"):
            raise ProviderError(f"claude failed: {str(envelope.get('result'))[:800]}")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return extract_json(json.dumps(structured), schema)
        return extract_json(str(envelope.get("result", "")), schema)

    def complete_images(self, system: str, user: str, images: list[bytes], schema: dict) -> dict:
        command = [
            *self.command, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--setting-sources", "",
            "--system-prompt", system, "--json-schema", json.dumps(schema),
        ]
        if self.model:
            command += ["--model", self.model]
        content = [*_image_blocks(images), {"type": "text", "text": user}]
        message = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
        with tempfile.TemporaryDirectory(prefix="clipgen-llm-") as empty:
            result = subprocess.run(
                command, input=message + "\n", cwd=empty, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT,
            )
        if result.returncode != 0:
            raise ProviderError(f"claude failed: {(result.stderr or result.stdout).strip()[-800:]}")
        for line in reversed(result.stdout.splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                if event.get("is_error"):
                    raise ProviderError(f"claude failed: {str(event.get('result'))[:800]}")
                structured = event.get("structured_output")
                text = json.dumps(structured) if isinstance(structured, dict) else str(event.get("result", ""))
                return extract_json(text, schema)
        raise ProviderError("claude returned no result.")


class OpenCodeProvider:
    """Runs OpenCode in a private server with every permission denied."""

    name = "opencode"

    def __init__(self, model: str | None = None):
        self.model = _checked_model(model)
        self.executable = shutil.which("opencode")
        if not self.executable:
            raise ProviderError("The opencode command was not found.")

    def complete(self, system: str, user: str, schema: dict) -> dict:
        prompt = (
            f"{system}\n\nDo not use any tools. Reply with only one JSON object matching this "
            f"JSON Schema:\n{json.dumps(schema)}\n\n{user}"
        )
        command = [self.executable, "run", "--standalone", "--format", "json"]
        if self.model:
            command += ["--model", self.model]
        deny = json.dumps({"permission": {"*": "deny"}})
        environment = os.environ | {"OPENCODE_CONFIG_CONTENT": deny, "OPENCODE_PERMISSION": json.dumps({"*": "deny"})}
        with tempfile.TemporaryDirectory(prefix="clipgen-llm-") as empty:
            result = subprocess.run(
                command, input=prompt, cwd=empty, env=environment, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT,
            )
        if result.returncode != 0:
            raise ProviderError(f"opencode failed: {(result.stderr or result.stdout).strip()[-800:]}")
        return extract_json(_opencode_text(result.stdout), schema)


def _opencode_text(output: str) -> str:
    chunks: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict):
            chunks.append(str(part.get("text", "")))
    return "".join(chunks) or output


def make_provider(name: str, model: str | None = None):
    classes = {
        "claude-cli": ClaudeCliProvider,
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "opencode": OpenCodeProvider,
    }
    if name not in classes:
        raise ValueError(f"Unknown provider: {name}")
    return classes[name](model)
