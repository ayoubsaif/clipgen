"""Local Streamlit interface. Start with: clipgen ui"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from clipgen import assistant, credentials, postiz, webcam
from clipgen.cli import parse_time
from clipgen.ingest import validate_url
from clipgen.jobs import active_job, cancel_job, home, list_jobs, start_job
from clipgen.llm import PROVIDERS, ProviderError, make_provider
from clipgen.render import LAYOUTS, parse_cam
from clipgen.review import (
    DECISIONS,
    add_clip_from_captions,
    approved_clips,
    caption_span,
    list_runs,
    load_decisions,
    load_manifest,
    load_transcript,
    set_decision,
    update_clip,
)
from clipgen.tools import ToolError

cam_picker = components.declare_component("cam_picker", path=str(Path(webcam.__file__).with_name("cam_picker")))
CLIPS_PAGE = "Step 1 · Clips"
POST_PAGE = "Step 2 · Post"
PAGES = ("Create", "Progress", CLIPS_PAGE, POST_PAGE, "Credentials")
YOUTUBE_LABELS = {"private": "Private", "unlisted": "Unlisted", "public": "Public"}
TIKTOK_LABELS = {
    "inbox": "Send to TikTok inbox to finish there",
    "SELF_ONLY": "Only me",
    "MUTUAL_FOLLOW_FRIENDS": "Friends",
    "FOLLOWER_OF_CREATOR": "Followers",
    "PUBLIC_TO_EVERYONE": "Public",
}
MODE_LABELS = {"draft": "Save as Postiz draft", "schedule": "Schedule", "now": "Post now"}
PROVIDER_LABELS = {
    "claude-cli": "Claude Code login",
    "anthropic": "Claude API key",
    "openai": "ChatGPT API key",
    "opencode": "OpenCode",
}
LAYOUT_LABELS = {"blur": "Full frame on blur", "center": "Center crop", "facecam": "Webcam over gameplay"}
WHISPER_MODELS = ("large-v3-turbo", "medium", "small", "tiny")
STATE_BADGES = {
    "queued": (":material/schedule:", "gray"), "running": (":material/autorenew:", "blue"),
    "done": (":material/check_circle:", "green"), "failed": (":material/error:", "red"),
    "canceled": (":material/cancel:", "gray"),
}
PAGE_ICONS = {"Create": "add_circle", "Progress": "monitoring", CLIPS_PAGE: "content_cut", POST_PAGE: "send",
              "Credentials": "key"}
DECISION_BADGES = {"pending": (":material/hourglass_empty:", "gray"), "approved": (":material/check:", "green"),
                   "rejected": (":material/close:", "red")}
MODE_ICONS = {"draft": ":material/draft:", "schedule": ":material/event:", "now": ":material/rocket_launch:"}
APP_CSS = """<style>
[data-testid=stSidebar] [data-testid=stRadio], [data-testid=stSidebar] [role=radiogroup] { width: 100%; }
[data-testid=stSidebar] [role=radiogroup] { gap: .2rem; flex-direction: column; }
[data-testid=stSidebar] [role=radiogroup] label { width: 100%; box-sizing: border-box; margin: 0; padding: .55rem .7rem; border-radius: .6rem; cursor: pointer; }
[data-testid=stSidebar] [data-testid=stRadioOption] > div > div:first-child { display: none; }
[data-testid=stSidebar] [data-testid=stRadioOption]:has(input:focus-visible) { outline: 2px solid #fff; }
[data-testid=stSidebar] [role=radiogroup] label:hover { background: rgba(255,255,255,.06); }
[data-testid=stSidebar] [role=radiogroup] label:has(input:checked) { background: rgba(109,74,255,.28); }
[data-testid=stMain] h1 { font-size: 1.9rem; padding-bottom: .1rem; }
@media (min-width: 1000px) { [data-testid=stSidebar][data-collapsed=false] { min-width: 420px !important; } }
[data-testid=stSidebar] [data-testid=stChatMessage] { padding: .5rem; }
[data-testid=stSidebar] [data-testid=stText] { white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; }
</style>"""


def page_header(page: str, title: str, note: str) -> None:
    st.title(f":material/{PAGE_ICONS[page]}: {title}")
    st.caption(note)
DECISION_LABELS = {"pending": "To review", "approved": "Approved", "rejected": "Rejected"}


def go_to(page: str) -> None:
    st.session_state["next_page"] = page
    st.rerun()


def optional_time(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return parse_time(value)
    except Exception as error:
        raise ValueError(f"Invalid time: {value}. Use seconds, MM:SS or HH:MM:SS.") from error


def validated_target(value: str) -> str:
    value = value.strip().strip('"')
    if not value:
        raise ValueError("Enter a video URL or a local file path.")
    return value if Path(value).is_file() else validate_url(value)


def create_page() -> None:
    page_header("Create", "Create clips", "Paste a Twitch, Kick or YouTube link and let AI find the best moments.")
    running = active_job()
    if running:
        st.info("A job is running. You can prepare another after it finishes.")

    with st.form("create"):
        target = st.text_input("Video", placeholder="Twitch, Kick or YouTube URL, or a local file path")
        count_col, minimum_col, maximum_col = st.columns(3)
        count = count_col.number_input("Clips", 1, 60, 5)
        minimum = minimum_col.number_input("Minimum seconds", 5, 180, 20)
        maximum = maximum_col.number_input("Maximum seconds", 5, 180, 60)
        provider_col, model_col = st.columns(2)
        provider = provider_col.selectbox("AI model source", PROVIDERS, format_func=PROVIDER_LABELS.get)
        model = model_col.text_input("Model", placeholder="Default for this source")
        layout_col, cam_col = st.columns(2)
        layout = layout_col.selectbox(
            "Vertical layout", ("blur", "center", "facecam"), format_func=LAYOUT_LABELS.get
        )
        cam = cam_col.text_input("Webcam area", placeholder="Automatic, or x,y,width,height",
                                 help="Claude finds the webcam. You can adjust it on a frame in Step 1.")
        with st.expander("Advanced"):
            first, second, third = st.columns(3)
            start = first.text_input("From", placeholder="1:00:00")
            end = second.text_input("To", placeholder="1:30:00")
            live_minutes = third.number_input("Live recording minutes", 1, 720, 30)
            language = st.text_input("Spoken language code", placeholder="Detect automatically, or es, en...")
            whisper_model = st.selectbox("Whisper model", WHISPER_MODELS)
            force_whisper = st.checkbox("Ignore platform captions and use Whisper")
        submitted = st.form_submit_button("Create clips", type="primary", icon=":material/auto_awesome:", disabled=bool(running))

    if not submitted:
        return
    try:
        if minimum > maximum:
            raise ValueError("Minimum seconds cannot be above maximum seconds.")
        if provider == "openai" and not model.strip():
            raise ValueError("Enter a ChatGPT model name.")
        needed = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
        if needed and credentials.status(needed) == "missing":
            raise ValueError("Add this provider's API key on the Credentials page first.")
        spec = {
            "kind": "run",
            "target": validated_target(target),
            "count": int(count),
            "min_seconds": float(minimum),
            "max_seconds": float(maximum),
            "provider": provider,
            "model": model.strip() or None,
            "layout": layout,
            "cam": list(parse_cam(cam)) if layout == "facecam" and cam.strip() else None,
            "start": optional_time(start),
            "end": optional_time(end),
            "live_minutes": float(live_minutes),
            "language": language.strip() or None,
            "whisper_model": whisper_model,
            "force_whisper": force_whisper,
        }
        start_job(spec)
    except (ValueError, RuntimeError, OSError) as error:
        st.error(str(error))
        return
    go_to("Progress")


@st.fragment(run_every="2s")
def job_list() -> None:
    jobs = list_jobs()
    if not jobs:
        st.info("No jobs yet.")
        return
    for job in jobs[:15]:
        status, spec = job["status"], job["spec"]
        state = status.get("state", "queued")
        name = spec.get("target") or Path(spec.get("run_folder", "")).name
        with st.container(border=True):
            icon, color = STATE_BADGES.get(state, STATE_BADGES["queued"])
            st.badge(state.title(), icon=icon, color=color)
            st.text(name)
            if state in ("queued", "running"):
                st.progress(min(int(status.get("percent", 0)), 100), text=None)
                st.text(status.get("stage", ""))
                if st.button("Cancel", icon=":material/stop_circle:", key=f"cancel-{job['id']}"):
                    cancel_job(job)
                    st.rerun(scope="fragment")
            elif state == "failed":
                st.error(status.get("error", "The job failed."))
            elif state == "done" and st.button("Review clips", icon=":material/content_cut:", key=f"open-{job['id']}"):
                st.session_state["review_run"] = status.get("run_folder")
                st.session_state["next_page"] = CLIPS_PAGE
                st.rerun(scope="app")
            log = job["dir"] / "log.txt"
            if log.exists():
                with st.expander("Log"):
                    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
                    st.code("\n".join(lines[-40:]) or "No output yet.", language=None)


def progress_page() -> None:
    page_header("Progress", "Progress", "Jobs keep running while you use other pages.")
    job_list()


def clip_card(run: Path, clip: dict, decisions: dict[str, str]) -> None:
    number = clip["number"]
    decision = decisions.get(str(number), "pending")
    key = f"{run.name}-{number}"
    with st.container(border=True):
        video_col, detail_col = st.columns([1, 2])
        with video_col:
            file = clip.get("file")
            if file and Path(file).exists():
                st.video(file)
            else:
                st.warning("This clip has not been rendered.")
        with detail_col:
            icon, color = DECISION_BADGES[decision]
            number_col, score_col, length_col, state_col = st.columns(4)
            number_col.badge(f"Clip {number}", icon=":material/movie:", color="violet")
            score_col.badge(f"Score {clip['score']:.0f}", icon=":material/local_fire_department:", color="orange")
            length_col.badge(f"{clip['end'] - clip['start']:.0f}s", icon=":material/timer:", color="gray")
            state_col.badge(DECISION_LABELS[decision], icon=icon, color=color)
            st.text(clip.get("why", ""))
            with st.form(f"edit-{key}"):
                title = st.text_input("YouTube title", clip["title"], max_chars=100)
                hook = st.text_input("On-screen hook", clip["hook"], max_chars=80)
                tiktok = st.text_area("TikTok caption", clip["tiktok_caption"], height=80)
                instagram = st.text_area("Instagram caption", clip["instagram_caption"], height=80)
                saved = st.form_submit_button("Save text", icon=":material/save:")
            if saved:
                update_clip(run, number, {"title": title, "hook": hook, "tiktok_caption": tiktok,
                                          "instagram_caption": instagram})
                st.toast("Saved. Render again to update the hook in the video.")
                st.rerun()
            approve, reject, render = st.columns(3)
            if approve.button("Approve", icon=":material/check:", key=f"approve-{key}", type="primary", disabled=decision == "approved"):
                set_decision(run, number, "approved")
                st.rerun()
            if reject.button("Reject", icon=":material/close:", key=f"reject-{key}", disabled=decision == "rejected"):
                set_decision(run, number, "rejected")
                st.rerun()
            if render.button("Render again", icon=":material/refresh:", key=f"render-{key}"):
                queue_render(run, [number])


def queue_render(run: Path, only: list[int] | None) -> None:
    layout = st.session_state.get("review_layout", "blur")
    saved = webcam.load_position(run) if layout == "facecam" else None
    cam = saved["cam"] if saved else None
    try:
        start_job({"kind": "render", "run_folder": str(run), "layout": layout, "cam": cam, "only": only})
    except (ValueError, RuntimeError, OSError) as error:
        st.error(str(error))
        return
    go_to("Progress")


def clock(seconds: float) -> str:
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def caption_cropper(run: Path) -> None:
    st.write("Select the caption lines you want. The clip runs from the first selected line to the last.")
    try:
        transcript = load_transcript(run)
    except (OSError, ValueError, KeyError):
        st.warning("This video has no saved captions.")
        return
    search = st.text_input("Find in captions", key=f"caption-search-{run.name}").strip().lower()
    rows = [
        {"line": index, "Time": clock(segment.start), "Caption": segment.text}
        for index, segment in enumerate(transcript.segments)
        if not search or search in segment.text.lower()
    ]
    if not rows:
        st.info("No caption lines match.")
        return
    table = pd.DataFrame(rows)
    event = st.dataframe(
        table, hide_index=True, height=320, width="stretch", on_select="rerun",
        selection_mode="multi-row", column_order=("Time", "Caption"),
        key=f"caption-table-{run.name}-{search}",
    )
    selected = event.selection.rows
    if not selected:
        return
    lines = [rows[index]["line"] for index in selected]
    first, last = min(lines), max(lines)
    try:
        start, end, text = caption_span(transcript, first, last)
    except ValueError as error:
        st.error(str(error))
        return
    st.text(f"{clock(start)} to {clock(end)}  ·  {end - start:.1f} seconds  ·  {last - first + 1} lines")
    st.text(text[:600] + ("..." if len(text) > 600 else ""))
    with st.form(f"caption-clip-{run.name}", clear_on_submit=True):
        title = st.text_input("Title", placeholder="Uses the caption text if empty", max_chars=100)
        hook = st.text_input("On-screen hook", placeholder="Optional", max_chars=80)
        create = st.form_submit_button("Create clip from captions", icon=":material/content_cut:", type="primary")
    if create:
        try:
            number = add_clip_from_captions(run, first, last, title, hook)
        except (ValueError, OSError) as error:
            st.error(str(error))
            return
        queue_render(run, [number])


def find_webcam(run: Path, frames: list[Path]) -> None:
    """Ask Claude for the position and show the result on the matching frame."""
    tried = f"cam-generation-{run.name}"
    st.session_state[tried] = st.session_state.get(tried, 0) + 1
    try:
        with st.spinner("Claude is finding the webcam"):
            found = webcam.detect(None, frames)
    except (ProviderError, ToolError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        st.session_state[f"cam-note-{run.name}"] = f"Claude could not find it: {error}. Place the box by hand."
        return
    if not found:
        st.session_state[f"cam-note-{run.name}"] = "Claude saw no webcam overlay. Place the box by hand if there is one."
        return
    st.session_state.pop(f"cam-note-{run.name}", None)
    webcam.save_position(run, found["cam"], "claude", found["frame"])
    st.session_state[f"cam-frame-next-{run.name}"] = found["frame"] + 1


def webcam_panel(run: Path, manifest: dict) -> None:
    source = Path(manifest["source"])
    if not source.exists():
        st.warning("The source video is no longer on this computer, so the webcam position cannot be previewed.")
        return
    middles = [(clip["start"] + clip["end"]) / 2 for clip in manifest["clips"]]
    try:
        frames = webcam.prepare_frames(run, source, middles)
    except (ToolError, OSError) as error:
        st.error(str(error))
        return
    generation_key = f"cam-generation-{run.name}"
    if webcam.load_position(run) is None and generation_key not in st.session_state:
        find_webcam(run, frames)
    saved = webcam.load_position(run)
    generation = st.session_state.get(generation_key, 0)

    with st.container(border=True):
        st.subheader(":material/videocam: Webcam position")
        note = st.session_state.get(f"cam-note-{run.name}")
        if note:
            st.warning(note)
        origin_note = st.empty()
        frame_key = f"cam-frame-{run.name}"
        if f"cam-frame-next-{run.name}" in st.session_state:
            st.session_state[frame_key] = st.session_state.pop(f"cam-frame-next-{run.name}")
        if frame_key not in st.session_state:
            st.session_state[frame_key] = min((saved or {}).get("frame", 0), len(frames) - 1) + 1
        number = st.radio("Frame", range(1, len(frames) + 1), horizontal=True, key=frame_key)
        frame = frames[number - 1]
        width, height = webcam.frame_size(frame)
        start_box = saved["cam"] if saved else [round(width * 0.03), round(height * 0.6),
                                                round(width * 0.25), round(height * 0.3)]
        picker_col, preview_col = st.columns([3, 1])
        with picker_col:
            value = cam_picker(image=webcam.data_url(frame), width=width, height=height, box=start_box,
                               token=generation, key=f"cam-picker-{run.name}-{generation}", default=None)
        box = list(webcam.fit_box(value, width, height)) if value else list(webcam.fit_box(start_box, width, height))
        if value and (not saved or box != saved["cam"]):
            webcam.save_position(run, box, "manual", number - 1)
            st.session_state.pop(f"cam-note-{run.name}", None)
            saved = webcam.load_position(run)
        if saved and not st.session_state.get(f"cam-note-{run.name}"):
            origin_note.caption("Found by Claude. Drag the box to correct it." if saved.get("origin") == "claude"
                                else "Set by hand. Drag the box to adjust it.")
        with preview_col:
            st.image(webcam.preview(frame, box), caption="Vertical preview")
        again_col, render_col = st.columns(2)
        if again_col.button("Find again with Claude", icon=":material/auto_awesome:", key=f"cam-again-{run.name}"):
            find_webcam(run, frames)
            st.rerun()
        if render_col.button("Render all clips with this webcam", icon=":material/movie_edit:", type="primary", key=f"cam-render-{run.name}"):
            webcam.save_position(run, box, (saved or {}).get("origin", "manual"), number - 1)
            queue_render(run, None)


def clips_page() -> None:
    page_header(CLIPS_PAGE, CLIPS_PAGE, "Approve the best clips, or cut a new one from the captions.")
    runs = list_runs(home())
    if not runs:
        st.info("Finished clips will appear here.")
        return
    wanted = st.session_state.pop("review_run", None)
    if wanted and Path(wanted) in runs:
        st.session_state["review_select"] = Path(wanted)
    titles = {run: load_manifest(run).get("title", run.name) for run in runs}
    run = st.selectbox("Video", runs, format_func=lambda p: titles[p], key="review_select")
    st.session_state["assistant_run"] = run
    manifest = load_manifest(run)
    decisions = load_decisions(run)

    filter_col, layout_col = st.columns(2)
    shown = filter_col.selectbox("Show", ("all", *DECISIONS),
                                 format_func=lambda d: "All clips" if d == "all" else DECISION_LABELS[d])
    default_layout = "facecam" if webcam.load_position(run) else "blur"
    layout = layout_col.selectbox("Layout for new renders", LAYOUTS, index=LAYOUTS.index(default_layout),
                                  format_func=LAYOUT_LABELS.get, key="review_layout")
    if layout == "facecam":
        webcam_panel(run, manifest)

    approved = sum(decisions.get(str(c["number"])) == "approved" for c in manifest["clips"])
    summary_col, next_col = st.columns([3, 1])
    summary_col.text(f"{approved} of {len(manifest['clips'])} approved")
    if next_col.button("Continue to Step 2", icon=":material/arrow_forward:", type="primary", disabled=not approved):
        st.session_state["post_run"] = str(run)
        go_to(POST_PAGE)

    review_tab, crop_tab = st.tabs([":material/video_library: Review clips", ":material/subtitles: Crop from captions"])
    with crop_tab:
        caption_cropper(run)
    with review_tab:
        for clip in sorted(manifest["clips"], key=lambda c: c["score"], reverse=True):
            if shown == "all" or decisions.get(str(clip["number"]), "pending") == shown:
                clip_card(run, clip, decisions)


def postiz_client() -> postiz.PostizClient | None:
    key = credentials.postiz_key()
    if not key:
        return None
    return postiz.PostizClient(postiz.get_url(), key)


def channel_composer(clip: dict, channel: dict, key: str) -> postiz.ChannelDraft:
    identifier = channel["identifier"]
    if identifier in postiz.YOUTUBE:
        text = st.text_area("Description", clip["title"], key=f"{key}-text", height=110)
        title = st.text_input("Title", clip["title"][:100], max_chars=100, key=f"{key}-title")
        privacy = st.selectbox("Visibility", postiz.YOUTUBE_PRIVACY, format_func=YOUTUBE_LABELS.get,
                               key=f"{key}-privacy")
    elif identifier in postiz.TIKTOK:
        text = st.text_area("Caption", clip["tiktok_caption"], key=f"{key}-text", height=110)
        title = st.text_input("Title", clip["title"][:90], max_chars=90, key=f"{key}-title")
        privacy = st.selectbox("Visibility", postiz.TIKTOK_PRIVACY, format_func=TIKTOK_LABELS.get,
                               key=f"{key}-privacy")
    else:
        text = st.text_area("Caption", clip["instagram_caption"], key=f"{key}-text", height=110)
        title, privacy = "", "public"
        st.caption("Instagram posts are public once published.")
    return postiz.ChannelDraft(channel["id"], identifier, channel.get("name", identifier), text, title, privacy)


def post_page() -> None:
    page_header(POST_PAGE, POST_PAGE, "Save approved clips as drafts, schedule them or post them through Postiz.")
    runs = [run for run in list_runs(home()) if approved_clips(run)]
    if not runs:
        st.info("Approve a clip in Step 1 to prepare it for posting.")
        return
    client = postiz_client()
    if client is None:
        st.warning("Add your Postiz API key on the Credentials page to connect your channels.")
        return

    wanted = st.session_state.pop("post_run", None)
    if wanted and Path(wanted) in runs:
        st.session_state["post_select"] = Path(wanted)
    titles = {run: load_manifest(run).get("title", run.name) for run in runs}
    run = st.selectbox("Video", runs, format_func=lambda p: titles[p], key="post_select")
    st.session_state["assistant_run"] = run
    clips = approved_clips(run)
    clip = st.selectbox("Approved clip", clips, format_func=lambda c: f"{c['number']}. {c['title']}",
                        key=f"post-clip-{run.name}")

    if "postiz_channels" not in st.session_state or st.session_state.get("postiz_channels_url") != client.base_url:
        try:
            st.session_state["postiz_channels"] = client.channels()
            st.session_state["postiz_channels_url"] = client.base_url
        except postiz.PostizError as error:
            st.error(str(error))
            return
    channels = st.session_state["postiz_channels"]
    if not channels:
        st.warning("No YouTube, TikTok or Instagram channels are connected in Postiz.")
        if st.button("Check again", icon=":material/refresh:"):
            st.session_state.pop("postiz_channels", None)
            st.rerun()
        return

    preview, composer = st.columns([1, 2])
    with preview:
        st.video(clip["file"])
        st.text(f"{clip['end'] - clip['start']:.0f} seconds")
    with composer:
        by_id = {channel["id"]: channel for channel in channels}
        chosen = st.multiselect(
            "Post to", list(by_id),
            format_func=lambda i: f"{by_id[i].get('name', i)} ({by_id[i]['identifier']})",
            key=f"post-channels-{run.name}-{clip['number']}",
        )
        drafts = []
        if chosen:
            tabs = st.tabs([by_id[i].get("name", i) for i in chosen])
            for tab, channel_id in zip(tabs, chosen):
                with tab:
                    drafts.append(channel_composer(clip, by_id[channel_id], f"{run.name}-{clip['number']}-{channel_id}"))

        mode = st.radio("When", postiz.MODES, format_func=MODE_LABELS.get, horizontal=True,
                        key=f"post-mode-{run.name}-{clip['number']}")
        when = datetime.now(timezone.utc)
        if mode == "schedule":
            local = datetime.now().astimezone() + timedelta(hours=1)
            date_col, time_col = st.columns(2)
            day = date_col.date_input("Date", local.date(), key=f"post-date-{clip['number']}")
            moment = time_col.time_input("Time", local.time().replace(second=0, microsecond=0),
                                         key=f"post-time-{clip['number']}")
            when = datetime.combine(day, moment).astimezone()

        history = [item for item in postiz.load_history(run) if item["clip"] == clip["number"]]
        confirmed = True
        if mode != "draft":
            public = [d.name for d in drafts if d.privacy in ("public", "PUBLIC_TO_EVERYONE")]
            warning = f" Publicly visible on: {', '.join(public)}." if public else ""
            action = "will publish immediately" if mode == "now" else "will publish automatically at this time"
            confirmed = st.checkbox(f"I understand this {action}.{warning}",
                                    key=f"post-confirm-{clip['number']}-{mode}")
        if history:
            confirmed = st.checkbox("This clip was already sent. Send it again.",
                                    key=f"post-again-{clip['number']}") and confirmed
        label = {"draft": "Save draft", "schedule": "Schedule post", "now": "Post now"}[mode]
        if st.button(label, icon=MODE_ICONS[mode], type="primary", disabled=not drafts or not confirmed):
            try:
                with st.spinner("Uploading the clip to Postiz"):
                    postiz.send_clip(client, run, clip["number"], Path(clip["file"]), mode, when, drafts)
            except (postiz.PostizError, ValueError, OSError) as error:
                st.error(str(error))
            else:
                st.success("Sent to Postiz. Check final status there.")

    history = postiz.load_history(run)
    if history:
        st.subheader(":material/history: Sent from this video")
        st.dataframe(pd.DataFrame([{
            "Clip": item["clip"],
            "Action": MODE_LABELS.get(item["mode"], item["mode"]),
            "Time (UTC)": item["date"],
            "Channels": ", ".join(c["name"] for c in item["channels"]),
        } for item in reversed(history)]), hide_index=True, width="stretch")


def credentials_page() -> None:
    page_header("Credentials", "Credentials", "Saved in Windows Credential Manager and never shown again. "
                "Use API tokens here, not your account passwords. "
                "Posting uses your Postiz API key, and channels are connected inside Postiz.")
    with st.form("postiz-url"):
        address = st.text_input("Postiz API address", postiz.get_url(),
                                help="Keep the default for Postiz Cloud, or enter your self-hosted address.")
        if st.form_submit_button("Save address", icon=":material/save:"):
            try:
                postiz.save_url(address)
                st.session_state.pop("postiz_channels", None)
                st.toast("Address saved.")
            except (ValueError, OSError) as error:
                st.error(str(error))
    labels = {"stored": ":material/lock: Saved", "environment": ":material/public: From environment",
              "missing": "Not set", "unavailable": ":material/warning: Vault unavailable"}
    generation = st.session_state.get("credential_generation", 0)
    for group, items in credentials.GROUPS.items():
        states = {item.name: credentials.status(item.name) for item in items}
        ready = sum(state != "missing" for state in states.values())
        with st.expander(f"{group}  ({ready}/{len(items)})", icon=":material/check_circle:" if ready else ":material/key:", expanded=False):
            with st.form(f"credentials-{group}", clear_on_submit=True):
                values = {}
                for item in items:
                    values[item.name] = st.text_input(
                        f"{item.label}  ·  {labels[states[item.name]]}", type="password",
                        key=f"input-{item.name}-{generation}", autocomplete="off",
                    )
                submitted = st.form_submit_button("Save", icon=":material/save:")
            if submitted:
                try:
                    changed = [name for name, value in values.items() if value.strip()]
                    for name in changed:
                        credentials.save(name, values[name])
                except Exception as error:  # Vault errors must not echo the submitted value.
                    st.error(f"Could not save to the vault ({type(error).__name__}).")
                else:
                    if changed:
                        # New widget keys drop the submitted secrets from session state.
                        st.session_state["credential_generation"] = generation + 1
                        st.toast("Saved.")
                        st.rerun()
            stored = [item for item in items if states[item.name] == "stored"]
            if stored:
                choice = st.selectbox("Remove a saved credential", stored,
                                      format_func=lambda c: c.label, key=f"remove-{group}")
                if st.button("Remove", icon=":material/delete:", key=f"remove-button-{group}"):
                    credentials.remove(choice.name)
                    st.rerun()


@st.fragment(run_every="3s")
def job_badge() -> None:
    """Show job activity, and refresh the page once when a job finishes."""
    running = bool(active_job())
    if running:
        st.badge("Job running", icon=":material/autorenew:", color="blue")
    finished = st.session_state.get("job_was_running") and not running
    st.session_state["job_was_running"] = running
    if finished:
        st.rerun(scope="app")


def assistant_run() -> Path | None:
    runs = list_runs(home())
    wanted = st.session_state.get("assistant_run")
    return wanted if wanted in runs else (runs[0] if runs else None)


def render_from_assistant(run: Path, numbers: list[int]) -> None:
    saved = webcam.load_position(run)
    layout = st.session_state.get("review_layout") or ("facecam" if saved else "blur")
    cam = saved["cam"] if layout == "facecam" and saved else None
    start_job({"kind": "render", "run_folder": str(run), "layout": layout, "cam": cam, "only": numbers})


def apply_actions(run: Path, messages: list[dict], actions: list[dict]) -> None:
    """Apply proposals in order, then render every changed clip in one job."""
    numbers: set[int] = set()
    try:
        for action in actions:
            number = assistant.apply(run, action)
            action["status"] = "applied"
            if number:
                numbers.add(number)
        if numbers:
            render_from_assistant(run, sorted(numbers))
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration) as error:
        assistant.save_chat(run, messages)
        st.error(str(error))
        return
    assistant.save_chat(run, messages)
    st.toast("Applied. Rendering in Progress." if numbers else "Applied.")
    st.rerun()


def action_card(run: Path, manifest: dict, messages: list[dict], action: dict, busy: bool) -> None:
    with st.container(border=True):
        st.text(assistant.describe(action, manifest))
        if action["status"] != "pending":
            icon, color = (":material/check:", "green") if action["status"] == "applied" else (":material/close:", "gray")
            st.badge(action["status"].title(), icon=icon, color=color)
            return
        renders = assistant.needs_render(action)
        label = "Apply and render" if renders else "Apply"
        if st.button(label, icon=":material/check:", type="primary", key=f"apply-{action['id']}", width="stretch",
                     disabled=renders and busy, help="Wait for the current job to finish." if renders and busy else None):
            apply_actions(run, messages, [action])
        if st.button("Discard", icon=":material/close:", key=f"discard-{action['id']}", width="stretch"):
            action["status"] = "discarded"
            assistant.save_chat(run, messages)
            st.rerun()


def apply_all_button(run: Path, messages: list[dict], message: dict, busy: bool, index: int) -> None:
    pending = [a for a in message.get("actions", []) if a["status"] == "pending" and a["type"] != "open_page"]
    if len(pending) < 2:
        return
    renders = any(assistant.needs_render(action) for action in pending)
    if st.button(f"Apply all {len(pending)}" + (" and render" if renders else ""), icon=":material/done_all:",
                 type="primary", key=f"apply-all-{run.name}-{index}", width="stretch", disabled=renders and busy):
        apply_actions(run, messages, pending)


def assistant_panel(page: str) -> None:
    st.divider()
    title_col, settings_col = st.columns([4, 1])
    title_col.subheader(":material/forum: Assistant")
    with settings_col.popover("", icon=":material/tune:", help="Assistant settings"):
        source = st.selectbox("AI model source", PROVIDERS, format_func=PROVIDER_LABELS.get, key="assistant_provider")
        model = st.text_input("Model", placeholder="Default for this source", key="assistant_model")
    run = assistant_run()
    if run is None:
        st.caption("Create clips first, then ask for changes here.")
        return
    manifest = load_manifest(run)
    messages = assistant.load_chat(run)
    options = [None, *[clip["number"] for clip in manifest["clips"]]]
    titles = {clip["number"]: clip["title"] for clip in manifest["clips"]}
    focus = st.selectbox("Talking about", options, key=f"assistant-focus-{run.name}",
                         format_func=lambda n: "Whole video" if n is None else f"{n}. {titles[n]}")
    busy = bool(active_job())
    with st.container(height=360):
        if not messages:
            st.caption('Try "Start this clip 3 seconds earlier" or "Make the hook stronger".')
        for index, message in enumerate(messages):
            with st.chat_message(message["role"]):
                # Plain text stops model or transcript content from loading remote images or links.
                st.text(message["content"])
                for action in message.get("actions", []):
                    if action["type"] != "open_page":
                        action_card(run, manifest, messages, action, busy)
                apply_all_button(run, messages, message, busy, index)
    prompt = st.chat_input("Ask or request a change", key=f"assistant-input-{run.name}", max_chars=assistant.MAX_MESSAGE)
    if messages and st.button("Clear chat", icon=":material/delete_sweep:", key=f"assistant-clear-{run.name}"):
        assistant.save_chat(run, [])
        st.rerun()
    st.caption("Do not paste passwords or API keys here.")
    if not prompt:
        return
    messages.append({"role": "user", "content": prompt.strip()})
    try:
        credentials.load_ai_credentials()
        provider = make_provider(source, model.strip() or None)
        with st.spinner("Thinking"):
            answer = assistant.ask(provider, run, messages, focus, page)
    except (ProviderError, ValueError) as error:
        answer = {"role": "assistant", "content": f"I could not answer: {error}", "actions": []}
    except Exception as error:  # SDK errors may include request details, so show only the type.
        answer = {"role": "assistant", "content": f"I could not answer ({type(error).__name__}).", "actions": []}
    messages.append(answer)
    assistant.save_chat(run, messages)
    opening = next((action for action in answer["actions"] if action["type"] == "open_page"), None)
    if opening:
        opening["status"] = "applied"
        assistant.save_chat(run, messages)
        st.session_state["next_page"] = opening["value"]
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="clipgen", page_icon=str(Path(webcam.__file__).with_name("assets") / "clipgen.png"),
                       layout="wide")
    st.html(APP_CSS)
    if "next_page" in st.session_state:
        st.session_state["page"] = st.session_state.pop("next_page")
    with st.sidebar:
        st.logo(str(Path(webcam.__file__).with_name("assets") / "clipgen.png"), size="large")
        page = st.radio("Page", PAGES, key="page", label_visibility="collapsed",
                        format_func=lambda name: f":material/{PAGE_ICONS[name]}: {name}")
        job_badge()
    {"Create": create_page, "Progress": progress_page, CLIPS_PAGE: clips_page, POST_PAGE: post_page,
     "Credentials": credentials_page}[page]()
    with st.sidebar:
        assistant_panel(page)


main()
