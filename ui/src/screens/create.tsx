import { Sparkles } from "lucide-preact";
import { useState } from "preact/hooks";
import type { Layout } from "../api";
import { api } from "../api";
import { useApp } from "../context";
import { Button, Field, Notice, PageTitle } from "../ui";

const LAYOUTS: { value: Layout; label: string; note: string }[] = [
  { value: "blur", label: "Full frame", note: "Keeps everyone in shot" },
  { value: "center", label: "Center crop", note: "Fills the screen" },
  { value: "facecam", label: "Webcam on top", note: "For gameplay streams" },
];

function LayoutDiagram({ layout }: { layout: Layout }) {
  return (
    <svg viewBox="0 0 36 64" class="h-20 w-auto" aria-hidden="true">
      <rect width="36" height="64" rx="3" fill="#0a2c26" stroke="currentColor" />
      {layout === "blur" && <><rect x="1" y="1" width="34" height="62" rx="2" fill="currentColor" opacity=".18" /><rect x="1" y="23" width="34" height="18" fill="currentColor" opacity=".8" /></>}
      {layout === "center" && <rect x="1" y="1" width="34" height="62" rx="2" fill="currentColor" opacity=".8" />}
      {layout === "facecam" && <><rect x="1" y="1" width="34" height="20" rx="2" fill="#ffd21f" /><rect x="1" y="22" width="34" height="41" rx="2" fill="currentColor" opacity=".7" /></>}
    </svg>
  );
}

export function CreateScreen() {
  const { go, busy, toast } = useApp();
  const [target, setTarget] = useState("");
  const [count, setCount] = useState(5);
  const [minimum, setMinimum] = useState(20);
  const [maximum, setMaximum] = useState(60);
  const [layout, setLayout] = useState<Layout>("blur");
  const [provider, setProvider] = useState("claude-cli");
  const [model, setModel] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [liveMinutes, setLiveMinutes] = useState(30);
  const [language, setLanguage] = useState("");
  const [whisper, setWhisper] = useState("large-v3-turbo");
  const [forceWhisper, setForceWhisper] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  const seconds = (value: string): number | null => {
    const text = value.trim();
    if (!text) return null;
    const parts = text.split(":").map(Number);
    if (parts.length > 3 || parts.some((part) => !Number.isFinite(part) || part < 0)) throw new Error(`Use seconds, MM:SS or HH:MM:SS instead of "${text}".`);
    return parts.reduce((total, part) => total * 60 + part, 0);
  };

  const submit = async (event: Event) => {
    event.preventDefault();
    setError("");
    setSending(true);
    try {
      await api("/api/jobs", "POST", {
        target, count, min_seconds: minimum, max_seconds: maximum, provider, model: model.trim() || null, layout,
        start: seconds(start), end: seconds(end), live_minutes: liveMinutes, language: language.trim() || null,
        whisper_model: whisper, force_whisper: forceWhisper,
      });
      toast("Started finding clips.");
      go("jobs");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <form onSubmit={submit} class="mx-auto max-w-3xl">
      <PageTitle title="Turn a video into clips" note="Paste a Twitch, Kick or YouTube link, a live channel, or a file on this computer." />
      <input
        aria-label="Video link or file path"
        class="field font-display !py-3.5 !text-lg"
        placeholder="https://www.twitch.tv/videos/…"
        value={target}
        onInput={(e) => setTarget(e.currentTarget.value)}
        required
      />
      <div class="mt-6 grid gap-4 sm:grid-cols-3">
        <Field label="Clips"><input class="field tabular" type="number" min={1} max={60} value={count} onInput={(e) => setCount(+e.currentTarget.value)} /></Field>
        <Field label="Shortest clip (seconds)"><input class="field tabular" type="number" min={5} max={180} value={minimum} onInput={(e) => setMinimum(+e.currentTarget.value)} /></Field>
        <Field label="Longest clip (seconds)"><input class="field tabular" type="number" min={5} max={180} value={maximum} onInput={(e) => setMaximum(+e.currentTarget.value)} /></Field>
      </div>
      <fieldset class="mt-6">
        <legend class="mb-2 text-sm font-medium">Vertical layout</legend>
        <div class="grid grid-cols-3 gap-3">
          {LAYOUTS.map((item) => (
            <label key={item.value} class={`flex cursor-pointer flex-col items-center gap-1.5 rounded-md border p-3 text-center has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-tape ${layout === item.value ? "border-tape bg-mat-raised text-paper" : "border-mat-line bg-mat-deep text-paper-dim"}`}>
              <input type="radio" name="layout" class="sr-only" checked={layout === item.value} onChange={() => setLayout(item.value)} />
              <LayoutDiagram layout={item.value} />
              <span class="text-sm font-semibold">{item.label}</span>
              <span class="text-xs">{item.note}</span>
            </label>
          ))}
        </div>
        {layout === "facecam" && <p class="mt-2 text-sm text-paper-dim">Claude finds the webcam. You can move it in Cut clips.</p>}
      </fieldset>
      <div class="mt-6 grid gap-4 sm:grid-cols-2">
        <Field label="AI model source">
          <select class="field" value={provider} onChange={(e) => setProvider(e.currentTarget.value)}>
            <option value="claude-cli">Claude Code login</option>
            <option value="anthropic">Claude API key</option>
            <option value="openai">ChatGPT API key</option>
            <option value="opencode">OpenCode</option>
          </select>
        </Field>
        <Field label="Model"><input class="field" placeholder="Default for this source" value={model} onInput={(e) => setModel(e.currentTarget.value)} /></Field>
      </div>
      <details class="mt-5 rounded-md border border-mat-line bg-mat-deep/70 p-3">
        <summary class="cursor-pointer text-sm font-medium">More options</summary>
        <div class="mt-3 grid gap-4 sm:grid-cols-3">
          <Field label="Start from"><input class="field tabular" placeholder="1:00:00" value={start} onInput={(e) => setStart(e.currentTarget.value)} /></Field>
          <Field label="Stop at"><input class="field tabular" placeholder="1:30:00" value={end} onInput={(e) => setEnd(e.currentTarget.value)} /></Field>
          <Field label="Live recording (minutes)"><input class="field tabular" type="number" min={1} max={720} value={liveMinutes} onInput={(e) => setLiveMinutes(+e.currentTarget.value)} /></Field>
          <Field label="Spoken language" hint="Leave empty to detect it"><input class="field" placeholder="es, en…" value={language} onInput={(e) => setLanguage(e.currentTarget.value)} /></Field>
          <Field label="Whisper model">
            <select class="field" value={whisper} onChange={(e) => setWhisper(e.currentTarget.value)}>
              {["large-v3-turbo", "medium", "small", "tiny"].map((name) => <option key={name}>{name}</option>)}
            </select>
          </Field>
          <label class="flex items-center gap-2 self-center text-sm"><input type="checkbox" checked={forceWhisper} onChange={(e) => setForceWhisper(e.currentTarget.checked)} />Ignore platform captions</label>
        </div>
      </details>
      {error && <div class="mt-4"><Notice kind="error">{error}</Notice></div>}
      {busy && <div class="mt-4"><Notice>A job is running. You can start this one when it finishes.</Notice></div>}
      <Button kind="primary" type="submit" icon={Sparkles} busy={sending} disabled={busy} class="mt-5 !px-5 !py-2.5 !text-base">Find clips</Button>
    </form>
  );
}
