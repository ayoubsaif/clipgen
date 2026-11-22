"""Run clipgen like a desktop app: its own window, no console, and a shortcut with an icon."""

from __future__ import annotations

import ctypes
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOST = "127.0.0.1"
START_TIMEOUT = 90
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ICON = Path(__file__).with_name("assets") / "clipgen.ico"
THEME = {
    "base": "dark",
    "primaryColor": "#6D4AFF",
    "backgroundColor": "#0F1117",
    "secondaryBackgroundColor": "#1A1D27",
    "textColor": "#F2F3F7",
    "baseRadius": "0.6rem",
}


def streamlit_command(port: int) -> list[str]:
    """Serve on this computer only, because the app can reach stored credentials."""
    app = Path(__file__).with_name("app.py")
    theme = [argument for name, value in THEME.items() for argument in (f"--theme.{name}", value)]
    return [
        _console_python(), "-m", "streamlit", "run", str(app),
        "--server.address", HOST, "--server.port", str(port),
        "--server.headless", "true", "--server.enableCORS", "true",
        "--server.enableXsrfProtection", "true", "--server.fileWatcherType", "none",
        "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal", *theme,
    ]


def _console_python() -> str:
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    return str(console) if executable.name.lower() == "pythonw.exe" and console.exists() else str(executable)


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((HOST, port)) == 0


def find_browser() -> str | None:
    """Find a browser that supports a chromeless app window."""
    roots = [os.environ.get(name) for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    tails = [Path("Microsoft/Edge/Application/msedge.exe"), Path("Google/Chrome/Application/chrome.exe")]
    for tail in tails:
        for root in roots:
            if root and (Path(root) / tail).exists():
                return str(Path(root) / tail)
    return None


def window_command(browser: str, port: int) -> list[str]:
    # A private profile keeps this process alive until the window closes, and stays apart from personal browsing.
    profile = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "clipgen" / "window"
    return [browser, f"--app=http://{HOST}:{port}", f"--user-data-dir={profile}", "--window-size=1440,920",
            "--no-first-run", "--no-default-browser-check"]


def show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, message, "clipgen", 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def run_window(port: int) -> int:
    """Open the app window, and stop the server this call started once the window closes."""
    browser = find_browser()
    if not browser:
        show_error("Microsoft Edge or Google Chrome is needed to open the clipgen window.")
        return 1
    if port_open(port):
        subprocess.Popen(window_command(browser, port))
        return 0
    server = subprocess.Popen(streamlit_command(port), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while not port_open(port):
            if server.poll() is not None or time.monotonic() > deadline:
                show_error("clipgen could not start. Run: uv run --extra gpu clipgen ui")
                return 1
            time.sleep(0.25)
        subprocess.Popen(window_command(browser, port)).wait()
        return 0
    finally:
        if server.poll() is None:
            server.terminate()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def app_window_command(browser: str, port: int, token: str, profile: Path) -> list[str]:
    # The token sits in the URL fragment, which browsers never send to a server.
    return [browser, f"--app=http://{HOST}:{port}/#token={token}", f"--user-data-dir={profile}",
            "--window-size=1440,920", "--no-first-run", "--no-default-browser-check"]


def run_app() -> int:
    """Open the Preact interface on its own port and token, and stop its server when the window closes."""
    browser = find_browser()
    if not browser:
        show_error("Microsoft Edge or Google Chrome is needed to open the clipgen window.")
        return 1
    if not (Path(__file__).with_name("webui") / "index.html").is_file():
        show_error("The clipgen interface is not built. Run: npm run build, in the ui folder.")
        return 1
    port, token = free_port(), secrets.token_urlsafe(32)
    server = subprocess.Popen([_console_python(), "-m", "clipgen.cli", "api", "--port", str(port), "--ui"],
                              env=os.environ | {"CLIPGEN_API_TOKEN": token}, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while not port_open(port):
            if server.poll() is not None or time.monotonic() > deadline:
                show_error("clipgen could not start. Run: uv run --extra gpu clipgen api --ui")
                return 1
            time.sleep(0.2)
        # A new profile per launch keeps this browser process alive until its own window closes.
        with tempfile.TemporaryDirectory(prefix="clipgen-window-", ignore_cleanup_errors=True) as profile:
            subprocess.Popen(app_window_command(browser, port, token, Path(profile))).wait()
        return 0
    finally:
        if server.poll() is None:
            server.terminate()


def create_shortcut(folder: Path | None = None) -> Path:
    """Create a Windows shortcut. Paths travel as environment variables, never inside the script text."""
    if os.name != "nt":
        raise RuntimeError("Desktop shortcuts are only available on Windows.")
    python = Path(sys.executable).with_name("pythonw.exe")
    if not python.exists():
        raise RuntimeError("Could not find pythonw.exe in this environment.")
    script = (
        "$ErrorActionPreference='Stop';"
        "$folder=$env:CLIPGEN_LINK_DIR; if(-not $folder){$folder=[Environment]::GetFolderPath('Desktop')};"
        "$path=Join-Path $folder 'clipgen.lnk';"
        "$link=(New-Object -ComObject WScript.Shell).CreateShortcut($path);"
        "$link.TargetPath=$env:CLIPGEN_LINK_TARGET;"
        "$link.Arguments='-m clipgen.cli ui --window';"
        "$link.WorkingDirectory=$env:CLIPGEN_LINK_CWD;"
        "$link.IconLocation=$env:CLIPGEN_LINK_ICON;"
        "$link.Description='Create and post vertical clips';"
        "$link.Save();Write-Output $path"
    )
    environment = os.environ | {"CLIPGEN_LINK_DIR": str(folder.resolve()) if folder else "",
                                "CLIPGEN_LINK_TARGET": str(python), "CLIPGEN_LINK_CWD": str(Path.cwd()),
                                "CLIPGEN_LINK_ICON": str(ICON)}
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=environment,
                            capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"Could not create the shortcut: {result.stderr.strip()[-400:]}")
    return Path(result.stdout.strip())
