"""Command line interface."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import whispercpp
from .credentials import load_ai_credentials
from .desktop import create_shortcut, run_app, run_window, streamlit_command
from .llm import PROVIDERS, ProviderError, make_provider
from .pipeline import RunOptions, rerender, run
from .render import LAYOUTS, parse_cam
from .selection import SelectionOptions
from .tools import ToolError, ffmpeg_path, nvenc_available, prepare_gpu_libraries


def parse_time(value: str) -> float:
    """Accept seconds, MM:SS or HH:MM:SS."""
    try:
        parts = [float(part) for part in value.split(":")]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid time: {value}") from error
    if not 1 <= len(parts) <= 3 or any(part < 0 for part in parts):
        raise argparse.ArgumentTypeError(f"Invalid time: {value}")
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--layout", choices=LAYOUTS, default="blur", help="vertical framing")
    parser.add_argument("--cam", type=parse_cam, help="webcam area for facecam: x,y,width,height (default: Claude finds it)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipgen", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    make = commands.add_parser("run", help="create clips from a URL or local file")
    make.add_argument("target", help="Twitch, Kick or YouTube URL, or a local video")
    make.add_argument("-n", "--count", type=int, default=5, help="number of clips (default 5)")
    make.add_argument("--provider", choices=PROVIDERS, default="claude-cli")
    make.add_argument("--model", help="model name for the chosen provider")
    make.add_argument("--min", dest="min_seconds", type=float, default=20, help="minimum clip seconds")
    make.add_argument("--max", dest="max_seconds", type=float, default=60, help="maximum clip seconds")
    make.add_argument("--language", help="spoken language code, such as es or en")
    make.add_argument("--from", dest="start", type=parse_time, help="only use the video from this time")
    make.add_argument("--to", dest="end", type=parse_time, help="only use the video up to this time")
    make.add_argument("--live-minutes", type=float, default=30, help="minutes to record from a live stream")
    make.add_argument("--whisper-model", default="large-v3-turbo")
    make.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    make.add_argument("--force-whisper", action="store_true", help="ignore platform captions")
    make.add_argument("--no-render", action="store_true", help="pick clips without rendering")
    make.add_argument("-o", "--output", type=Path, default=Path("runs"))
    _add_render_options(make)

    again = commands.add_parser("render", help="render a finished run again")
    again.add_argument("run_folder", type=Path)
    again.add_argument("--only", help="clip numbers, such as 1,3")
    _add_render_options(again)

    commands.add_parser("doctor", help="check this machine")
    interface = commands.add_parser("ui", help="open the local web interface")
    interface.add_argument("--port", type=int, default=8501)
    interface.add_argument("--window", action="store_true", help="open in its own app window")
    local_api = commands.add_parser("api", help="start the local API for the desktop interface")
    local_api.add_argument("--port", type=int, default=8765)
    local_api.add_argument("--export-openapi", type=Path, metavar="FILE", help="write the API schema and exit")
    local_api.add_argument("--ui", action="store_true", help="also serve the built desktop interface")
    commands.add_parser("app", help="open the new desktop interface")
    shortcut = commands.add_parser("shortcut", help="add a clipgen shortcut to the desktop")
    shortcut.add_argument("--folder", type=Path, help="create it in this folder instead")
    return parser


def launch_ui(port: int) -> int:
    return subprocess.call(streamlit_command(port))


def doctor() -> int:
    path = ffmpeg_path()
    filters = subprocess.run([path, "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace").stdout
    nvenc = nvenc_available()
    checks = [
        ("ffmpeg", True, path),
        ("Caption filter (libass)", " subtitles " in filters, ""),
        ("NVENC GPU encoding", nvenc, "" if nvenc else "falls back to CPU encoding"),
    ]
    try:
        prepare_gpu_libraries()
        if os.name == "nt":
            ctypes.WinDLL("cublas64_12.dll")
        checks.append(("Whisper on GPU (CUDA libraries)", True, ""))
    except OSError as error:
        checks.append(("Whisper on GPU (CUDA libraries)", False, f"falls back to CPU: {error}"))
    binary = whispercpp.find_binary()
    checks.append(("whisper.cpp (Windows on ARM GPU)", binary is not None,
                   str(binary) if binary else "optional, see scripts/build-whisper-cpp.ps1"))
    checks += [
        ("Claude Code login path", shutil.which("claude") is not None, "provider claude-cli"),
        ("OpenCode", shutil.which("opencode") is not None, "provider opencode"),
        ("ANTHROPIC_API_KEY", bool(os.environ.get("ANTHROPIC_API_KEY")), "provider anthropic"),
        ("OPENAI_API_KEY", bool(os.environ.get("OPENAI_API_KEY")), "provider openai"),
    ]
    for name, passed, note in checks:
        print(f"{'OK     ' if passed else 'MISSING'} {name}{f' ({note})' if note else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor()
        if args.command == "ui":
            return run_window(args.port) if args.window else launch_ui(args.port)
        if args.command == "api":
            from .api import export_openapi, serve

            if args.export_openapi:
                print(f"API schema written: {export_openapi(args.export_openapi)}")
                return 0
            return serve(args.port, args.ui)
        if args.command == "app":
            return run_app()
        if args.command == "shortcut":
            print(f"Shortcut created: {create_shortcut(args.folder)}")
            return 0
        load_ai_credentials()
        if args.command == "render":
            only = {int(n) for n in args.only.split(",")} if args.only else None
            folder = rerender(args.run_folder, RunOptions(layout=args.layout, cam=args.cam), only)
        else:
            if args.count < 1 or not 0 < args.min_seconds <= args.max_seconds:
                raise ValueError("Check --count, --min and --max.")
            options = RunOptions(
                output_root=args.output, language=args.language, whisper_model=args.whisper_model,
                device=args.device, force_whisper=args.force_whisper, live_minutes=args.live_minutes,
                start=args.start, end=args.end, layout=args.layout, cam=args.cam,
                render=not args.no_render,
                selection=SelectionOptions(args.count, args.min_seconds, args.max_seconds),
            )
            folder = run(args.target, make_provider(args.provider, args.model), options)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        print(f"\nDone: {folder.resolve()}")
        for clip in manifest["clips"]:
            print(f"  {clip['number']:>2}. [{clip['score']:.0f}] {clip['duration'] if 'duration' in clip else clip['end'] - clip['start']:.0f}s {clip['title']}")
        return 0
    except (ValueError, RuntimeError, ProviderError, ToolError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
