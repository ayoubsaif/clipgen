# clipgen

Turn a Twitch, Kick or YouTube video, clip or live stream into vertical clips for YouTube Shorts, TikTok and Instagram Reels.

1. Downloads the video with `yt-dlp`, or records a set number of minutes from a live stream.
2. Uses platform captions when available, otherwise runs Whisper locally on the GPU.
3. Asks Claude, ChatGPT or an OpenCode model to score the whole video, then choose exact clips.
4. Checks every timestamp in code and snaps it to spoken word boundaries.
5. Renders 1080x1920 MP4s with word-highlight captions and a hook, using NVENC when available.

Then work in two steps:

- **Step 1, Clips:** review, edit and approve generated clips, or create a new clip by selecting a range of caption lines.
- **Step 2, Post:** send approved clips through [Postiz](https://github.com/gitroomhq/postiz-app) as a draft, a scheduled post or an immediate post, with separate text and settings per channel.

Nothing is sent unless you press the button in Step 2.

## Setup

```powershell
uv sync --extra gpu
```

The `gpu` extra adds NVIDIA's CUDA library (about 550 MB) inside this project's environment. Without it, Whisper runs on the CPU. The first transcription also downloads the Whisper model.

```powershell
uv run clipgen doctor
```

## Open the app

Double-click `Start clipgen.cmd`. It sets up clipgen the first time, then opens it in its own window with no console. Closing the window stops the app. A job already running keeps going and appears under Progress next time.

To add a desktop icon:

```powershell
uv run --extra gpu clipgen shortcut
```

To run it in a normal browser tab at http://127.0.0.1:8501 instead:

```powershell
uv run --extra gpu clipgen ui
```

The app creates clips, follows progress, edits text, approves clips and manages credentials. It only listens on this computer and needs Microsoft Edge or Google Chrome for its window.

Credentials are saved in Windows Credential Manager, not in project files, and are never displayed after saving. Use official API tokens rather than account passwords. Posting uses only the Postiz API key. Social tokens saved on the Credentials page are stored but not used.

## Assistant

The sidebar has a chat for the selected video. Ask it to change a clip's timing, rewrite its title, hook or captions, find another moment, approve or reject a clip, or explain a score. Each change appears as a card with the before and after, and nothing changes until you press Apply. Changes to timing or the hook render the clip again in one job. The assistant cannot publish, schedule or read credentials.

## Webcam over gameplay

Choose this layout and leave the webcam area empty. Claude looks at frames from the clips and finds the overlay. In Step 1, the position appears on a frame with a vertical preview, so you can drag the box, resize it from the corners or use the arrow keys, then render all clips. Frames go to Claude through your Claude Code login, or your Claude API key if that is the selected provider. If no webcam is found, the run uses the full-frame layout and you can place the box by hand in Step 1.

## Posting with Postiz

1. Create a Postiz account, or self-host Postiz. Self-hosting needs Docker, Postgres, Redis and Temporal.
2. Connect your YouTube, TikTok and Instagram channels inside Postiz.
3. Copy your Postiz API key into the Credentials page. For a self-hosted instance, also set its API address there. Addresses must use https, except on this computer.
4. Approve a clip in Step 1, then open Step 2.

Safe defaults: the mode starts as a Postiz draft, YouTube starts as private, and TikTok goes to your inbox as a private upload. Scheduling or posting now requires a confirmation tick, and so does sending the same clip again. Instagram posts are public, so the confirmation names them.

Postiz is licensed under AGPL-3.0. clipgen contains no Postiz code and talks to it only through its public API, as a separate service.

## New desktop interface

The new Preact and Tailwind interface has the same features as the Streamlit app. Open it with:

```powershell
uv run --extra gpu clipgen app
```

It opens in its own window and uses a new private token each launch. Closing the window stops its server. A job already running keeps going. The desktop icon and `Start clipgen.cmd` still open the Streamlit app.

To change the interface, install Node.js, then rebuild it into `src/clipgen/webui`:

```powershell
npm --prefix ui install
```

```powershell
npm --prefix ui run build
```

## Local API

The desktop interface talks to the engine through a local API. See [docs/api.md](docs/api.md).

## Command line

Five clips with your existing Claude Code login:

```powershell
uv run clipgen run "https://www.twitch.tv/videos/123456789" -n 5
```

One clip from part of a long VOD:

```powershell
uv run clipgen run "https://www.youtube.com/watch?v=VIDEO_ID" -n 1 --from 1:00:00 --to 1:30:00
```

Record 20 minutes of a live stream, with Claude finding the webcam. Add `--cam x,y,width,height` to set it yourself:

```powershell
uv run clipgen run "https://kick.com/CHANNEL" --live-minutes 20 --layout facecam
```

Other providers:

```powershell
uv run clipgen run VIDEO_URL --provider anthropic
```

```powershell
uv run clipgen run VIDEO_URL --provider openai --model MODEL_NAME
```

```powershell
uv run clipgen run VIDEO_URL --provider opencode --model provider/model
```

Render a finished run again with another layout, with no download or model cost:

```powershell
uv run clipgen render runs/RUN_ID --layout blur --only 1,3
```

Each run folder contains `clips/`, `manifest.json` (titles, hooks, platform captions, scores and reasons), `selection.json` and `transcript.json`.

## Limits

- The virality score is a model's judgment, not a prediction from real performance data.
- Live capture records a fixed length and does not yet watch a stream continuously.
- The default `blur` layout keeps the full frame. `center` crops the middle and can cut people out, because there is no automatic face tracking yet.
- Only download and repost content you own or have permission to use.

## Design credits

The approach was informed by studying [OpenShorts](https://github.com/mutonby/openshorts) and [AgentCut](https://github.com/0xPasho/agentcut). No code from either project is included.
