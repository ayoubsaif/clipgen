import type { LucideIcon } from "lucide-preact";
import { Activity, KeyRound, MessageSquare, Plus, Scissors, Send } from "lucide-preact";
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { Job, RunSummary } from "./api";
import { api, watchJobs } from "./api";
import { Assistant } from "./assistant";
import { AppContext, type Page } from "./context";
import { ClipsScreen } from "./screens/clips";
import { CreateScreen } from "./screens/create";
import { JobsScreen } from "./screens/jobs";
import { KeysScreen } from "./screens/keys";
import { PostScreen } from "./screens/post";

const NAV: { page: Page; label: string; icon: LucideIcon; step?: number }[] = [
  { page: "new", label: "New video", icon: Plus },
  { page: "jobs", label: "Progress", icon: Activity },
  { page: "clips", label: "Cut clips", icon: Scissors, step: 1 },
  { page: "post", label: "Post clips", icon: Send, step: 2 },
];

function NavButton({ item, active, badge, onClick }: { item: (typeof NAV)[number]; active: boolean; badge?: boolean; onClick: () => void }) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      title={item.label}
      class={`flex w-full cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-left ${active ? "bg-tape font-semibold text-tape-ink" : "text-paper-dim hover:bg-mat-raised hover:text-paper"}`}
    >
      <Icon size={18} aria-hidden="true" class="shrink-0" />
      <span class="hidden flex-1 md:block">{item.label}</span>
      {item.step && <span class="tabular hidden text-xs opacity-70 md:block">Step {item.step}</span>}
      {badge && <span class="size-2 shrink-0 animate-pulse rounded-full bg-keep" aria-label="Job running" />}
    </button>
  );
}

export function App() {
  const [page, setPage] = useState<Page>("new");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunIdState] = useState<string | null>(null);
  const [clipNumber, setClipNumber] = useState<number | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [version, setVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [chatOpen, setChatOpen] = useState(() => window.innerWidth >= 1024);
  const states = useRef(new Map<string, string>());
  const toastTimer = useRef<number | undefined>(undefined);

  const refresh = useCallback(() => setVersion((value) => value + 1), []);
  const toast = useCallback((text: string) => {
    setMessage(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setMessage(""), 4000);
  }, []);
  const setRunId = useCallback((id: string) => {
    setRunIdState(id);
    setClipNumber(null);
  }, []);

  useEffect(() => watchJobs((list) => {
    let finished = false;
    for (const job of list) {
      const before = states.current.get(job.id);
      if (before && before !== job.state && job.state === "done") finished = true;
      states.current.set(job.id, job.state);
    }
    setJobs(list);
    if (finished) refresh();
  }), [refresh]);

  useEffect(() => {
    api<RunSummary[]>("/api/runs").then((list) => {
      setRuns(list);
      setRunIdState((current) => (current && list.some((run) => run.id === current) ? current : list[0]?.id ?? null));
    }).catch(() => undefined);
  }, [version]);

  const busy = jobs.some((job) => job.state === "queued" || job.state === "running");
  const state = useMemo(() => ({ page, go: setPage, runs, runId, setRunId, clipNumber, setClipNumber, jobs, busy, version, refresh, toast }),
    [page, runs, runId, setRunId, clipNumber, jobs, busy, version, refresh, toast]);

  return (
    <AppContext.Provider value={state}>
      <div class="flex h-full">
        <nav aria-label="Main" class="flex w-16 shrink-0 flex-col gap-1 border-r border-mat-line bg-mat-deep p-2 md:w-56 md:p-3">
          <div class="mb-4 flex items-center gap-2 px-2 pt-1">
            <img src="/favicon.png" alt="" class="size-7" />
            <span class="font-display hidden text-xl font-bold tracking-tight md:block">clipgen</span>
          </div>
          {NAV.map((item) => (
            <NavButton key={item.page} item={item} active={page === item.page} badge={item.page === "jobs" && busy} onClick={() => setPage(item.page)} />
          ))}
          <div class="mt-auto flex flex-col gap-1">
            <NavButton item={{ page: "keys", label: "Credentials", icon: KeyRound }} active={page === "keys"} onClick={() => setPage("keys")} />
            <button
              type="button"
              onClick={() => setChatOpen(!chatOpen)}
              aria-pressed={chatOpen}
              title="Assistant"
              class="flex w-full cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-paper-dim hover:bg-mat-raised hover:text-paper"
            >
              <MessageSquare size={18} aria-hidden="true" class="shrink-0" />
              <span class="hidden md:block">{chatOpen ? "Hide assistant" : "Show assistant"}</span>
            </button>
          </div>
        </nav>
        <main class="mat-grid scroll-thin @container min-w-0 flex-1 overflow-y-auto p-4 md:p-7">
          {page === "new" && <CreateScreen />}
          {page === "jobs" && <JobsScreen />}
          {page === "clips" && <ClipsScreen />}
          {page === "post" && <PostScreen />}
          {page === "keys" && <KeysScreen />}
        </main>
        {chatOpen && <Assistant onClose={() => setChatOpen(false)} />}
      </div>
      {message && (
        <div role="status" class="tape fixed bottom-6 left-1/2 z-50 -translate-x-1/2 px-5 py-2 font-medium shadow-lg">
          {message}
        </div>
      )}
    </AppContext.Provider>
  );
}
