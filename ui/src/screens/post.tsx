import { CalendarClock, FileText, Rocket } from "lucide-preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import type { Channel, Clip, Run, SentPost } from "../api";
import { api, ApiError, media } from "../api";
import { useApp } from "../context";
import { Button, Empty, Field, Notice, PageTitle, Segmented } from "../ui";

type Mode = "draft" | "schedule" | "now";
interface Draft { text: string; title: string; privacy: string }

const YOUTUBE = [["private", "Private"], ["unlisted", "Unlisted"], ["public", "Public"]];
const TIKTOK = [["inbox", "Send to TikTok inbox"], ["SELF_ONLY", "Only me"], ["MUTUAL_FOLLOW_FRIENDS", "Friends"], ["FOLLOWER_OF_CREATOR", "Followers"], ["PUBLIC_TO_EVERYONE", "Public"]];
const MODE_NAMES: Record<string, string> = { draft: "Draft", schedule: "Scheduled", now: "Posted" };
const isYouTube = (id: string) => id === "youtube";
const isTikTok = (id: string) => id.startsWith("tiktok");

function newDraft(channel: Channel, clip: Clip): Draft {
  if (isYouTube(channel.identifier)) return { text: clip.title, title: clip.title.slice(0, 100), privacy: "private" };
  if (isTikTok(channel.identifier)) return { text: clip.tiktok_caption, title: clip.title.slice(0, 90), privacy: "inbox" };
  return { text: clip.instagram_caption, title: "", privacy: "public" };
}

function localDefault(): string {
  const date = new Date(Date.now() + 3600_000);
  date.setSeconds(0, 0);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function PostScreen() {
  const { runs, runId, setRunId, version, go, toast, refresh } = useApp();
  const [run, setRun] = useState<Run | null>(null);
  const [number, setNumber] = useState<number | null>(null);
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [channelError, setChannelError] = useState("");
  const [needsKey, setNeedsKey] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [active, setActive] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("draft");
  const [when, setWhen] = useState(localDefault);
  const [confirm, setConfirm] = useState(false);
  const [resend, setResend] = useState(false);
  const [history, setHistory] = useState<SentPost[]>([]);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  const loadChannels = () => {
    setChannelError("");
    setNeedsKey(false);
    api<Channel[]>("/api/postiz/channels").then(setChannels).catch((e: ApiError) => {
      setChannels([]);
      if (e.status === 409) setNeedsKey(true);
      else setChannelError(e.message);
    });
  };
  useEffect(loadChannels, []);

  useEffect(() => {
    if (!runId) return;
    api<Run>(`/api/runs/${runId}`).then(setRun).catch((e) => setError(e.message));
    api<SentPost[]>(`/api/runs/${runId}/posts`).then(setHistory).catch(() => setHistory([]));
  }, [runId, version]);

  const approved = useMemo(() => (run?.clips ?? []).filter((clip) => clip.decision === "approved" && clip.rendered), [run]);
  const clip = approved.find((item) => item.number === number) ?? approved[0] ?? null;

  useEffect(() => { setDrafts({}); setActive(null); setConfirm(false); setResend(false); }, [clip?.number, runId]);
  useEffect(() => setConfirm(false), [mode]);

  const postable = runs.filter((item) => item.approved > 0);
  if (!postable.length) return <Empty title="Nothing approved yet"><p class="mb-4">Approve a clip and it will appear here.</p><Button kind="primary" onClick={() => go("clips")}>Cut clips</Button></Empty>;

  const toggle = (channel: Channel) => {
    if (!clip) return;
    setDrafts((current) => {
      const next = { ...current };
      if (next[channel.id]) delete next[channel.id];
      else next[channel.id] = newDraft(channel, clip);
      const ids = Object.keys(next);
      setActive((value) => (value && next[value] ? value : ids[0] ?? null));
      return next;
    });
  };
  const edit = (id: string, change: Partial<Draft>) => setDrafts((current) => ({ ...current, [id]: { ...current[id], ...change } }));
  const chosen = (channels ?? []).filter((channel) => drafts[channel.id]);
  const activeChannel = chosen.find((channel) => channel.id === active) ?? chosen[0];
  const sent = clip ? history.some((item) => item.clip === clip.number) : false;
  const publicNames = chosen.filter((channel) => ["public", "PUBLIC_TO_EVERYONE"].includes(drafts[channel.id].privacy)).map((channel) => channel.name);
  const ready = chosen.length > 0 && (mode === "draft" || confirm) && (!sent || resend);
  const LABEL = { draft: "Save draft", schedule: "Schedule post", now: "Post now" }[mode];

  const send = async () => {
    if (!clip) return;
    setSending(true);
    setError("");
    try {
      await api(`/api/runs/${run!.id}/clips/${clip.number}/post`, "POST", {
        mode, when: mode === "schedule" ? new Date(when).toISOString() : null, confirm: mode !== "draft" && confirm, resend,
        channels: chosen.map((channel) => ({ channel_id: channel.id, text: drafts[channel.id].text, title: drafts[channel.id].title, privacy: drafts[channel.id].privacy })),
      });
      toast({ draft: "Draft saved in Postiz.", schedule: "Post scheduled in Postiz.", now: "Sent to Postiz to post." }[mode]);
      setConfirm(false);
      setResend(false);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div class="mx-auto max-w-5xl">
      <PageTitle title="Post clips" note="Save a draft, schedule it, or post it through Postiz.">
        <select aria-label="Video" class="field !w-72 max-w-full" value={runId ?? ""} onChange={(e) => setRunId(e.currentTarget.value)}>
          {postable.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </PageTitle>
      {!clip ? <Notice>This video has no approved, rendered clips yet.</Notice> : (
        <div class="grid items-start gap-5 md:grid-cols-[15rem_1fr]">
          <div>
            <select aria-label="Approved clip" class="field mb-2" value={clip.number} onChange={(e) => setNumber(+e.currentTarget.value)}>
              {approved.map((item) => <option key={item.number} value={item.number}>{item.number}. {item.title}</option>)}
            </select>
            <video key={clip.video_url} src={media(clip.video_url)} controls preload="metadata" class="aspect-[9/16] w-full rounded-md bg-black" />
          </div>
          <section class="space-y-4 rounded-md border border-mat-line bg-mat-deep p-4">
            {needsKey && <Notice>Add your Postiz API key to connect your channels. <button type="button" class="cursor-pointer font-semibold text-tape underline" onClick={() => go("keys")}>Open Credentials</button></Notice>}
            {channelError && <Notice kind="error">{channelError}</Notice>}
            {channels && !channels.length && !needsKey && !channelError && <Notice>Connect a YouTube, TikTok or Instagram channel in Postiz, then check again. <button type="button" class="cursor-pointer font-semibold text-tape underline" onClick={loadChannels}>Check again</button></Notice>}
            {!!channels?.length && (
              <fieldset>
                <legend class="mb-1.5 text-sm font-medium">Post to</legend>
                <div class="flex flex-wrap gap-2">
                  {channels.map((channel) => (
                    <label key={channel.id} class={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-tape ${drafts[channel.id] ? "border-tape bg-mat-raised" : "border-mat-line"}`}>
                      <input type="checkbox" class="accent-tape" checked={!!drafts[channel.id]} onChange={() => toggle(channel)} />
                      {channel.name}
                      <span class="text-xs text-paper-dim">{channel.identifier}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            {activeChannel && (
              <div>
                {chosen.length > 1 && <div class="mb-3"><Segmented<string> label="Channel to edit" value={activeChannel.id} onChange={setActive} options={chosen.map((channel) => ({ value: channel.id, label: channel.name }))} /></div>}
                <div class="space-y-3">
                  <Field label={isYouTube(activeChannel.identifier) ? "Description" : "Caption"}>
                    <textarea class="field" rows={4} maxLength={5000} value={drafts[activeChannel.id].text} onInput={(e) => edit(activeChannel.id, { text: e.currentTarget.value })} />
                  </Field>
                  {(isYouTube(activeChannel.identifier) || isTikTok(activeChannel.identifier)) && (
                    <div class="grid gap-3 sm:grid-cols-2">
                      <Field label="Title"><input class="field" maxLength={isYouTube(activeChannel.identifier) ? 100 : 90} value={drafts[activeChannel.id].title} onInput={(e) => edit(activeChannel.id, { title: e.currentTarget.value })} /></Field>
                      <Field label="Visibility">
                        <select class="field" value={drafts[activeChannel.id].privacy} onChange={(e) => edit(activeChannel.id, { privacy: e.currentTarget.value })}>
                          {(isYouTube(activeChannel.identifier) ? YOUTUBE : TIKTOK).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                        </select>
                      </Field>
                    </div>
                  )}
                  {!isYouTube(activeChannel.identifier) && !isTikTok(activeChannel.identifier) && <p class="text-sm text-paper-dim">Instagram posts are public once published.</p>}
                </div>
              </div>
            )}
            <div class="space-y-3 border-t border-mat-line pt-4">
              <Segmented<Mode> label="When" value={mode} onChange={setMode} options={[{ value: "draft", label: "Draft", icon: FileText }, { value: "schedule", label: "Schedule", icon: CalendarClock }, { value: "now", label: "Post now", icon: Rocket }]} />
              {mode === "schedule" && <Field label="Date and time" class="max-w-xs"><input class="field" type="datetime-local" value={when} onInput={(e) => setWhen(e.currentTarget.value)} /></Field>}
              {mode !== "draft" && (
                <label class="flex items-start gap-2 text-sm"><input type="checkbox" class="mt-1 accent-tape" checked={confirm} onChange={(e) => setConfirm(e.currentTarget.checked)} />
                  <span>I understand this {mode === "now" ? "publishes immediately" : "publishes automatically at this time"}.{publicNames.length ? ` Public on ${publicNames.join(", ")}.` : ""}</span>
                </label>
              )}
              {sent && <label class="flex items-start gap-2 text-sm"><input type="checkbox" class="mt-1 accent-tape" checked={resend} onChange={(e) => setResend(e.currentTarget.checked)} /><span>This clip was already sent. Send it again.</span></label>}
              {error && <Notice kind="error">{error}</Notice>}
              <Button kind="primary" icon={mode === "draft" ? FileText : mode === "schedule" ? CalendarClock : Rocket} busy={sending} disabled={!ready} onClick={send}>{LABEL}</Button>
            </div>
          </section>
        </div>
      )}
      {history.length > 0 && (
        <section class="mt-6">
          <h2 class="font-display mb-2 text-xl font-semibold">Sent from this video</h2>
          <div class="overflow-x-auto rounded-md border border-mat-line bg-mat-deep">
            <table class="w-full text-left text-sm">
              <thead class="text-paper-dim"><tr><th class="px-3 py-2 font-medium">Clip</th><th class="px-3 py-2 font-medium">Action</th><th class="px-3 py-2 font-medium">Time</th><th class="px-3 py-2 font-medium">Channels</th></tr></thead>
              <tbody>
                {[...history].reverse().map((item, index) => (
                  <tr key={index} class="border-t border-mat-line">
                    <td class="tabular px-3 py-2">{item.clip}</td><td class="px-3 py-2">{MODE_NAMES[item.mode] ?? item.mode}</td>
                    <td class="tabular px-3 py-2">{new Date(item.date).toLocaleString()}</td><td class="px-3 py-2">{item.channels.map((channel) => channel.name).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
