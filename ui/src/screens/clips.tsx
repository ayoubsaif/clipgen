import { Check, Captions, FileText, RefreshCw, Send, Video, X } from "lucide-preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import type { Clip, Decision, Layout, Run, TranscriptView } from "../api";
import { api, media } from "../api";
import { useApp } from "../context";
import { Button, clock, Empty, Field, Notice, PageTitle, Segmented } from "../ui";
import { CaptionCutter } from "./captions";
import { Ruler } from "./ruler";
import { WebcamEditor } from "./webcam";

type Tab = "text" | "captions" | "webcam";

const LAYOUT_OPTIONS: { value: Layout; label: string }[] = [
  { value: "blur", label: "Full frame" }, { value: "center", label: "Center crop" }, { value: "facecam", label: "Webcam on top" },
];

function TextForm({ run, clip, onSaved }: { run: Run; clip: Clip; onSaved: () => void }) {
  const { toast } = useApp();
  const [title, setTitle] = useState(clip.title);
  const [hook, setHook] = useState(clip.hook);
  const [tiktok, setTiktok] = useState(clip.tiktok_caption);
  const [instagram, setInstagram] = useState(clip.instagram_caption);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { setTitle(clip.title); setHook(clip.hook); setTiktok(clip.tiktok_caption); setInstagram(clip.instagram_caption); }, [clip]);
  const changed = title !== clip.title || hook !== clip.hook || tiktok !== clip.tiktok_caption || instagram !== clip.instagram_caption;

  const save = async (event: Event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await api(`/api/runs/${run.id}/clips/${clip.number}`, "PATCH", { title, hook, tiktok_caption: tiktok, instagram_caption: instagram });
      toast(hook !== clip.hook ? "Text saved. Render the clip to update its hook." : "Text saved.");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <form onSubmit={save} class="space-y-3">
      <Field label="YouTube title"><input class="field" maxLength={100} value={title} onInput={(e) => setTitle(e.currentTarget.value)} /></Field>
      <Field label="On-screen hook"><input class="field" maxLength={80} value={hook} onInput={(e) => setHook(e.currentTarget.value)} /></Field>
      <Field label="TikTok caption"><textarea class="field" rows={3} maxLength={2200} value={tiktok} onInput={(e) => setTiktok(e.currentTarget.value)} /></Field>
      <Field label="Instagram caption"><textarea class="field" rows={3} maxLength={2200} value={instagram} onInput={(e) => setInstagram(e.currentTarget.value)} /></Field>
      {error && <Notice kind="error">{error}</Notice>}
      <Button type="submit" busy={saving} disabled={!changed}>Save text</Button>
    </form>
  );
}

export function ClipsScreen() {
  const { runs, runId, setRunId, clipNumber, setClipNumber, version, refresh, busy, go, toast } = useApp();
  const [run, setRun] = useState<Run | null>(null);
  const [transcript, setTranscript] = useState<TranscriptView | null>(null);
  const [tab, setTab] = useState<Tab>("text");
  const [layout, setLayout] = useState<Layout>("blur");
  const [range, setRange] = useState<[number, number] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!runId) return setRun(null);
    let current = true;
    api<Run>(`/api/runs/${runId}`).then((data) => {
      if (!current) return;
      setRun(data);
      if (data.webcam) setLayout("facecam");
    }).catch((e) => setError(e.message));
    return () => { current = false; };
  }, [runId, version]);

  useEffect(() => {
    setTranscript(null);
    setRange(null);
    if (runId) api<TranscriptView>(`/api/runs/${runId}/transcript`).then(setTranscript).catch(() => setTranscript(null));
  }, [runId]);

  const ordered = useMemo(() => [...(run?.clips ?? [])].sort((a, b) => b.score - a.score), [run]);
  const clip = run?.clips.find((item) => item.number === clipNumber) ?? ordered[0] ?? null;
  useEffect(() => { if (clip && clip.number !== clipNumber) setClipNumber(clip.number); }, [clip?.number, clipNumber]);

  if (!runs.length) return <Empty title="No clips yet"><p class="mb-4">Add a video and clipgen will find the best moments.</p><Button kind="primary" onClick={() => go("new")}>New video</Button></Empty>;
  if (!run || run.id !== runId) return error ? <Notice kind="error">{error}</Notice> : <p class="text-paper-dim">Loading clips…</p>;

  const duration = transcript?.duration ?? Math.max(...run.clips.map((item) => item.end), 1);
  const approved = run.clips.filter((item) => item.decision === "approved").length;

  const decide = async (decision: Decision) => {
    if (!clip) return;
    try {
      await api(`/api/runs/${run.id}/clips/${clip.number}/decision`, "PUT", { decision });
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const render = async () => {
    if (!clip) return;
    setError("");
    try {
      await api(`/api/runs/${run.id}/render`, "POST", { clips: [clip.number], layout });
      toast(`Rendering clip ${clip.number}.`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div class="mx-auto max-w-6xl">
      <PageTitle title="Cut clips">
        <div class="flex flex-wrap items-center gap-2">
          <select aria-label="Video" class="field !w-72 max-w-full" value={run.id} onChange={(e) => setRunId(e.currentTarget.value)}>
            {runs.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select>
          <Button kind="primary" icon={Send} disabled={!approved} title={approved ? undefined : "Approve a clip first"} onClick={() => go("post")}>
            Post {approved} approved
          </Button>
        </div>
      </PageTitle>
      <Ruler duration={duration} clips={run.clips} selected={clip?.number ?? null} range={tab === "captions" ? range : null} onSelect={setClipNumber} />
      {error && <div class="mt-3"><Notice kind="error">{error}</Notice></div>}
      <div class="mt-4 grid items-start gap-4 @2xl:grid-cols-[15rem_minmax(0,1fr)] @5xl:grid-cols-[14rem_17rem_minmax(0,1fr)]">
        <div class="space-y-3 @5xl:contents">
        <ul class="scroll-thin max-h-44 space-y-1 overflow-y-auto rounded-md border border-mat-line bg-mat-deep p-1.5 @5xl:max-h-[34rem]" aria-label="Clips">
          {ordered.map((item) => (
            <li key={item.number}>
              <button
                type="button"
                aria-current={item.number === clip?.number ? "true" : undefined}
                onClick={() => setClipNumber(item.number)}
                class={`w-full cursor-pointer rounded px-2.5 py-2 text-left ${item.number === clip?.number ? "bg-mat-raised" : "hover:bg-mat-raised/60"}`}
              >
                <span class="line-clamp-2 text-sm font-medium">{item.title}</span>
                <span class="tabular mt-0.5 flex items-center gap-2 text-xs text-paper-dim">
                  <span>Score {Math.round(item.score)}</span>
                  <span>{Math.round(item.duration)}s</span>
                  {item.decision === "approved" && <Check size={13} class="text-keep" aria-label="Approved" />}
                  {item.decision === "rejected" && <X size={13} class="text-cut" aria-label="Rejected" />}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div>
          {clip?.video_url ? (
            <video key={clip.video_url} src={media(clip.video_url)} controls preload="metadata" class="aspect-[9/16] w-full rounded-md bg-black" />
          ) : (
            <div class="flex aspect-[9/16] w-full items-center justify-center rounded-md border border-dashed border-mat-line bg-mat-deep p-4 text-center text-sm text-paper-dim">Render this clip to watch it.</div>
          )}
          {clip && (
            <>
              <div class="mt-2 grid grid-cols-2 gap-2">
                <Button kind={clip.decision === "approved" ? "primary" : "plain"} icon={Check} aria-pressed={clip.decision === "approved"} onClick={() => decide(clip.decision === "approved" ? "pending" : "approved")}>{clip.decision === "approved" ? "Approved" : "Approve"}</Button>
                <Button kind="danger" icon={X} aria-pressed={clip.decision === "rejected"} onClick={() => decide(clip.decision === "rejected" ? "pending" : "rejected")}>{clip.decision === "rejected" ? "Rejected" : "Reject"}</Button>
              </div>
              <p class="tabular mt-2 text-center text-xs text-paper-dim">{clock(clip.start)} to {clock(clip.end)}</p>
            </>
          )}
        </div>
        </div>
        <section class="min-w-0 rounded-md border border-mat-line bg-mat-deep p-4">
          <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
            <Segmented<Tab> label="Editing" value={tab} onChange={setTab} options={[
              { value: "text", label: "Text", icon: FileText }, { value: "captions", label: "Cut from captions", icon: Captions }, { value: "webcam", label: "Webcam", icon: Video },
            ]} />
          </div>
          {tab === "text" && clip && (
            <>
              {clip.why && <p class="mb-3 text-sm text-paper-dim">{clip.why}</p>}
              <TextForm run={run} clip={clip} onSaved={refresh} />
              <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-mat-line pt-4">
                <Segmented<Layout> label="Layout" value={layout} onChange={setLayout} options={LAYOUT_OPTIONS} />
                <Button icon={RefreshCw} disabled={busy} title={busy ? "Wait for the current job to finish." : undefined} onClick={render}>Render clip</Button>
              </div>
            </>
          )}
          {tab === "captions" && <CaptionCutter run={run} transcript={transcript} layout={layout} onRange={setRange} onCreated={async (number) => { setRun(await api<Run>(`/api/runs/${run.id}`)); setClipNumber(number); setTab("text"); refresh(); }} />}
          {tab === "webcam" && <WebcamEditor run={run} onChanged={() => { setLayout("facecam"); refresh(); }} />}
        </section>
      </div>
    </div>
  );
}
