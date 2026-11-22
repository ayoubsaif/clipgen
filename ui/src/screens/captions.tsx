import { Scissors } from "lucide-preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import type { Layout, Run, TranscriptView } from "../api";
import { api } from "../api";
import { useApp } from "../context";
import { Button, clock, Field, Notice } from "../ui";

export function CaptionCutter({ run, transcript, layout, onRange, onCreated }: { run: Run; transcript: TranscriptView | null; layout: Layout; onRange: (range: [number, number] | null) => void; onCreated: (number: number) => void }) {
  const { busy, toast } = useApp();
  const [search, setSearch] = useState("");
  const [first, setFirst] = useState<number | null>(null);
  const [last, setLast] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [hook, setHook] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const lines = transcript?.lines ?? [];
  const shown = useMemo(() => lines.filter((line) => line.text.toLowerCase().includes(search.trim().toLowerCase())), [lines, search]);
  const from = first === null ? null : lines[first];
  const to = last === null ? from : lines[last];

  useEffect(() => {
    onRange(from && to ? [from.start, to.end] : null);
    return () => onRange(null);
  }, [from, to]);

  if (!transcript) return <Notice>This video has no saved captions.</Notice>;

  const choose = (index: number, extend: boolean) => {
    if (first === null || (last !== null && !extend)) {
      setFirst(index);
      setLast(null);
    } else if (index < first) {
      setLast(first);
      setFirst(index);
    } else {
      setLast(index);
    }
  };

  const create = async (event: Event) => {
    event.preventDefault();
    if (first === null) return;
    setSaving(true);
    setError("");
    try {
      const result = await api<{ number: number }>(`/api/runs/${run.id}/clips/from-captions`, "POST", { first, last: last ?? first, title, hook, layout });
      toast(`Clip ${result.number} created. Rendering now.`);
      setFirst(null); setLast(null); setTitle(""); setHook("");
      onCreated(result.number);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const seconds = from && to ? to.end - from.start : 0;
  return (
    <div>
      <p class="mb-2 text-sm text-paper-dim">Click the first line, then the last line of your clip.</p>
      <input aria-label="Find in captions" class="field mb-2" placeholder="Find in captions" value={search} onInput={(e) => setSearch(e.currentTarget.value)} />
      <ol class="scroll-thin max-h-72 space-y-0.5 overflow-y-auto rounded-md border border-mat-line bg-mat p-1">
        {shown.map((line) => {
          const inside = first !== null && line.index >= first && line.index <= (last ?? first);
          return (
            <li key={line.index}>
              <button
                type="button"
                aria-pressed={inside}
                onClick={(e) => choose(line.index, e.shiftKey || last === null)}
                class={`flex w-full cursor-pointer gap-3 rounded px-2 py-1 text-left text-sm ${inside ? "bg-tape font-medium text-tape-ink" : "hover:bg-mat-raised"}`}
              >
                <span class={`tabular w-14 shrink-0 ${inside ? "" : "text-paper-dim"}`}>{clock(line.start)}</span>
                <span>{line.text}</span>
              </button>
            </li>
          );
        })}
        {!shown.length && <li class="p-2 text-sm text-paper-dim">No captions match.</li>}
      </ol>
      {from && to && (
        <form onSubmit={create} class="mt-3 space-y-3">
          <p class="tabular text-sm font-medium">{clock(from.start)} to {clock(to.end)}, {seconds.toFixed(1)} seconds</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <Field label="Title"><input class="field" maxLength={100} placeholder="Uses the caption text" value={title} onInput={(e) => setTitle(e.currentTarget.value)} /></Field>
            <Field label="On-screen hook"><input class="field" maxLength={80} placeholder="Optional" value={hook} onInput={(e) => setHook(e.currentTarget.value)} /></Field>
          </div>
          {error && <Notice kind="error">{error}</Notice>}
          <Button kind="primary" type="submit" icon={Scissors} busy={saving} disabled={busy} title={busy ? "Wait for the current job to finish." : undefined}>Create clip</Button>
        </form>
      )}
    </div>
  );
}
