"""Background jobs that keep running and reporting when the browser page reloads."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

HEARTBEAT_SECONDS = 3
STALE_SECONDS = 30
ACTIVE = {"queued", "running"}


def home() -> Path:
    return Path(os.environ.get("CLIPGEN_HOME", "runs")).resolve()


def jobs_root() -> Path:
    return home() / ".jobs"


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:  # Windows blocks replacement while a reader has the file open.
            time.sleep(0.05 * (attempt + 1))
    temporary.unlink(missing_ok=True)
    raise OSError(f"Could not update {path}")


def _read_json(path: Path) -> dict | None:
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (PermissionError, json.JSONDecodeError):
            time.sleep(0.05 * (attempt + 1))
    return None


def progress_percent(message: str, current: int) -> int:
    match = re.match(r"Rendering clip (\d+)/(\d+)", message)
    if match:
        number, total = map(int, match.groups())
        return 75 + int(24 * (number - 1) / max(total, 1))
    for prefix, percent in (("Fetching", 5), ("Loading transcript", 20), ("Transcript from", 40),
                            ("Measuring", 45), ("Scoring", 55), ("Choosing", 68)):
        if message.startswith(prefix):
            return percent
    return current


def read_status(job_dir: Path) -> dict:
    status = _read_json(job_dir / "status.json") or {"state": "queued", "percent": 0, "stage": "Waiting"}
    if status.get("state") in ACTIVE:
        try:
            age = time.time() - (job_dir / "heartbeat").stat().st_mtime
        except FileNotFoundError:
            age = time.time() - job_dir.stat().st_mtime
        if age > STALE_SECONDS:
            status |= {"state": "failed", "error": "The job stopped unexpectedly."}
    return status


def list_jobs() -> list[dict]:
    root = jobs_root()
    if not root.exists():
        return []
    jobs = []
    for job_dir in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        spec = _read_json(job_dir / "job.json")
        if spec:
            jobs.append({"id": job_dir.name, "dir": job_dir, "spec": spec, "status": read_status(job_dir)})
    return jobs


def active_job() -> dict | None:
    return next((job for job in list_jobs() if job["status"]["state"] in ACTIVE), None)


def start_job(spec: dict) -> str:
    """Start one job. Only one runs at a time because they share the GPU's memory."""
    if active_job():
        raise RuntimeError("Another job is running. Wait for it or cancel it first.")
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    job_dir = jobs_root() / job_id
    job_dir.mkdir(parents=True)
    _write_json(job_dir / "job.json", spec)
    _write_json(job_dir / "status.json", {"state": "queued", "percent": 0, "stage": "Starting"})
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    environment = os.environ | {"CLIPGEN_HOME": str(home()), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    with (job_dir / "log.txt").open("ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "clipgen.jobs", str(job_dir)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags, env=environment,
        )
    return job_id


def cancel_job(job: dict) -> None:
    status = read_status(job["dir"])
    pid = status.get("pid")
    if status.get("state") not in ACTIVE or not isinstance(pid, int):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, 15)
    _write_json(job["dir"] / "status.json", status | {"state": "canceled", "stage": "Canceled"})


def _execute(spec: dict, report) -> Path:
    from .credentials import load_ai_credentials
    from .pipeline import RunOptions, rerender, run
    from .selection import SelectionOptions

    cam = tuple(spec["cam"]) if spec.get("cam") else None
    if spec["kind"] == "render":
        options = RunOptions(layout=spec["layout"], cam=cam)
        only = set(spec["only"]) if spec.get("only") else None
        return rerender(Path(spec["run_folder"]), options, only, report)

    from .llm import make_provider

    load_ai_credentials()
    options = RunOptions(
        output_root=home(), language=spec.get("language"), whisper_model=spec["whisper_model"],
        device=spec.get("device", "auto"), force_whisper=spec.get("force_whisper", False),
        live_minutes=spec.get("live_minutes", 30), start=spec.get("start"), end=spec.get("end"),
        layout=spec["layout"], cam=cam,
        selection=SelectionOptions(spec["count"], spec["min_seconds"], spec["max_seconds"]),
    )
    return run(spec["target"], make_provider(spec["provider"], spec.get("model")), options, report)


def worker(job_dir: Path) -> int:
    spec = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    state = {"state": "running", "percent": 1, "stage": "Starting", "pid": os.getpid()}
    _write_json(job_dir / "status.json", state)
    beat = job_dir / "heartbeat"
    beat.touch()

    def heartbeat() -> None:
        while True:
            beat.touch()
            time.sleep(HEARTBEAT_SECONDS)

    threading.Thread(target=heartbeat, daemon=True).start()

    def report(message: str) -> None:
        print(message, flush=True)
        state.update(stage=message, percent=progress_percent(message, state["percent"]))
        _write_json(job_dir / "status.json", state)

    try:
        folder = _execute(spec, report)
    except Exception as error:  # A job must always end with a visible state.
        traceback.print_exc()
        state.update(state="failed", error=str(error)[:1000])
        _write_json(job_dir / "status.json", state)
        return 1
    state.update(state="done", percent=100, stage="Finished", run_folder=str(Path(folder).resolve()))
    _write_json(job_dir / "status.json", state)
    return 0


if __name__ == "__main__":
    raise SystemExit(worker(Path(sys.argv[1])))
