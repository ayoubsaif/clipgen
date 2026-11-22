import { Check, CheckCheck, Settings2, Trash2, X } from "lucide-preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { ChatAction, ChatMessage } from "./api";
import { api } from "./api";
import { type Page, PAGE_NAMES, useApp } from "./context";
import { Button, Notice } from "./ui";

const PROVIDERS = [["claude-cli", "Claude Code login"], ["anthropic", "Claude API key"], ["openai", "ChatGPT API key"], ["opencode", "OpenCode"]];

function ActionCard({ action, busy, working, onApply, onDiscard }: { action: ChatAction; busy: boolean; working: boolean; onApply: () => void; onDiscard: () => void }) {
  if (action.type === "open_page") return null;
  const [heading, ...details] = action.description.split("\n");
  return (
    <div class="mt-2 rounded-md border border-mat-line bg-mat p-2.5 text-sm">
      <p class="font-semibold">{heading}</p>
      {/* After applying, the saved clip already holds the new value, so "Before" would be wrong. */}
      {details.filter((line) => action.status === "pending" || !line.startsWith("Before:")).map((line) => <p key={line} class="mt-0.5 break-words text-paper-dim">{line}</p>)}
      {action.status === "pending" ? (
        <div class="mt-2 flex flex-wrap gap-2">
          <Button kind="primary" icon={Check} busy={working} disabled={action.needs_render && busy} title={action.needs_render && busy ? "Wait for the current job to finish." : undefined} onClick={onApply}>
            {action.needs_render ? "Apply and render" : "Apply"}
          </Button>
          <Button kind="quiet" icon={X} disabled={working} onClick={onDiscard}>Discard</Button>
        </div>
      ) : (
        <p class={`mt-1.5 text-xs font-semibold ${action.status === "applied" ? "text-keep" : "text-paper-dim"}`}>{action.status === "applied" ? "Applied" : "Discarded"}</p>
      )}
    </div>
  );
}

export function Assistant({ onClose }: { onClose: () => void }) {
  const { runId, runs, clipNumber, page, go, busy, version, refresh, toast } = useApp();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [provider, setProvider] = useState("claude-cli");
  const [model, setModel] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const run = runs.find((item) => item.id === runId);

  const load = () => {
    if (!runId) return setMessages([]);
    api<{ messages: ChatMessage[] }>(`/api/runs/${runId}/assistant`).then((data) => setMessages(data.messages)).catch((e) => setError(e.message));
  };
  useEffect(load, [runId, version]);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages, thinking]);

  const send = async (event: Event) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || !runId || thinking) return;
    setDraft("");
    setError("");
    setThinking(true);
    setMessages((list) => [...list, { role: "user", content }]);
    try {
      const pageNames = Object.entries(PAGE_NAMES) as [Page, string][];
      const answer = await api<{ message: ChatMessage; navigate: string | null }>(`/api/runs/${runId}/assistant/messages`, "POST", {
        content, focus_clip: clipNumber, page: PAGE_NAMES[page], provider, model: model.trim() || null,
      });
      setMessages((list) => [...list, answer.message]);
      const target = pageNames.find(([, name]) => name === answer.navigate);
      if (target) go(target[0]);
    } catch (e) {
      setError((e as Error).message);
      setDraft(content);
      load();
    } finally {
      setThinking(false);
    }
  };

  const apply = async (ids: string[]) => {
    setWorking(true);
    setError("");
    try {
      const result = await api<{ job_id: string | null }>(`/api/runs/${runId}/assistant/actions/apply`, "POST", { action_ids: ids });
      toast(result.job_id ? "Applied. Rendering now." : "Applied.");
      refresh();
    } catch (e) {
      setError((e as Error).message);
      load();
    } finally {
      setWorking(false);
    }
  };

  const discard = async (id: string) => {
    try {
      await api(`/api/runs/${runId}/assistant/actions/${id}/discard`, "POST");
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const clear = async () => {
    await api(`/api/runs/${runId}/assistant`, "DELETE").catch((e) => setError(e.message));
    load();
  };

  return (
    <aside aria-label="Assistant" class="fixed inset-y-0 right-0 z-40 flex w-[min(380px,100vw)] flex-col border-l border-mat-line bg-mat-deep lg:static lg:w-96 lg:shrink-0">
      <header class="flex items-center gap-1 border-b border-mat-line px-3 py-2">
        <div class="min-w-0 flex-1">
          <h2 class="font-display text-lg font-semibold">Assistant</h2>
          <p class="truncate text-xs text-paper-dim">{run ? (clipNumber ? `Clip ${clipNumber} of ${run.title}` : run.title) : "No video selected"}</p>
        </div>
        <Button kind="quiet" icon={Settings2} aria-label="Assistant settings" aria-expanded={showSettings} onClick={() => setShowSettings(!showSettings)} />
        {messages.length > 0 && <Button kind="quiet" icon={Trash2} aria-label="Clear chat" onClick={clear} />}
        <Button kind="quiet" icon={X} aria-label="Hide assistant" onClick={onClose} />
      </header>
      {showSettings && (
        <div class="space-y-2 border-b border-mat-line p-3 text-sm">
          <select aria-label="AI model source" class="field" value={provider} onChange={(e) => setProvider(e.currentTarget.value)}>
            {PROVIDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <input aria-label="Model" class="field" placeholder="Default model" value={model} onInput={(e) => setModel(e.currentTarget.value)} />
        </div>
      )}
      <div class="scroll-thin flex-1 space-y-3 overflow-y-auto p-3" aria-live="polite">
        {!runId && <p class="text-sm text-paper-dim">Create clips first, then ask for changes here.</p>}
        {runId && !messages.length && !thinking && (
          <div class="space-y-1.5">
            <p class="text-sm text-paper-dim">Ask for a change. You approve each one before it happens.</p>
            {["Start this clip 3 seconds earlier", "Make the hook shorter", "Find a stronger moment"].map((idea) => (
              <button key={idea} type="button" onClick={() => setDraft(idea)} class="block w-full cursor-pointer rounded-md border border-mat-line px-3 py-1.5 text-left text-sm hover:border-tape">
                {idea}
              </button>
            ))}
          </div>
        )}
        {messages.map((message, index) => {
          const pending = (message.actions ?? []).filter((action) => action.status === "pending" && action.type !== "open_page");
          return (
            <div key={index} class={message.role === "user" ? "ml-8" : "mr-2"}>
              {/* Rendered as text so model or transcript content cannot inject markup or remote images. */}
              <p class={`rounded-md px-3 py-2 text-sm break-words whitespace-pre-wrap ${message.role === "user" ? "bg-mat-raised" : ""}`}>{message.content}</p>
              {message.actions?.map((action) => (
                <ActionCard key={action.id} action={action} busy={busy} working={working} onApply={() => apply([action.id])} onDiscard={() => discard(action.id)} />
              ))}
              {pending.length > 1 && (
                <Button kind="primary" icon={CheckCheck} class="mt-2 w-full" busy={working} disabled={busy && pending.some((action) => action.needs_render)} onClick={() => apply(pending.map((action) => action.id))}>
                  Apply all {pending.length}
                </Button>
              )}
            </div>
          );
        })}
        {thinking && <p class="animate-pulse px-3 text-sm text-paper-dim">Reading the clip…</p>}
        {error && <Notice kind="error">{error}</Notice>}
        <div ref={end} />
      </div>
      <form onSubmit={send} class="border-t border-mat-line p-3">
        <textarea
          aria-label="Message the assistant"
          class="field resize-none"
          rows={2}
          maxLength={4000}
          disabled={!runId}
          placeholder="Ask or request a change"
          value={draft}
          onInput={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) send(e); }}
        />
        <p class="mt-1 text-xs text-paper-dim">Do not paste passwords or API keys here.</p>
      </form>
    </aside>
  );
}
