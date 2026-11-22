import { CircleCheck, CircleStop, CircleX, Clock, LoaderCircle, Scissors } from "lucide-preact";
import { useState } from "preact/hooks";
import type { Job } from "../api";
import { api } from "../api";
import { useApp } from "../context";
import { Button, Empty, Notice, PageTitle } from "../ui";

const STATES = {
  queued: { icon: Clock, label: "Waiting", color: "text-paper-dim" },
  running: { icon: LoaderCircle, label: "Running", color: "text-tape" },
  done: { icon: CircleCheck, label: "Finished", color: "text-keep" },
  failed: { icon: CircleX, label: "Failed", color: "text-cut" },
  canceled: { icon: CircleStop, label: "Canceled", color: "text-paper-dim" },
};

function JobRow({ job }: { job: Job }) {
  const { go, setRunId, runs, refresh } = useApp();
  const [log, setLog] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const state = STATES[job.state];
  const Icon = state.icon;
  const active = job.state === "queued" || job.state === "running";
  const title = runs.find((run) => run.id === job.run_id)?.title ?? job.target ?? "Video";

  const toggleLog = async () => {
    if (log) return setLog(null);
    try {
      setLog((await api<{ lines: string[] }>(`/api/jobs/${job.id}/log`)).lines);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const cancel = async () => {
    try {
      await api(`/api/jobs/${job.id}/cancel`, "POST");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <li class="border-b border-mat-line bg-mat-deep/80 p-4 first:rounded-t-md last:rounded-b-md last:border-b-0">
      <div class="flex flex-wrap items-center gap-3">
        <Icon size={20} aria-hidden="true" class={`${state.color} ${job.state === "running" ? "animate-spin" : ""}`} />
        <div class="min-w-0 flex-1">
          <p class="truncate font-semibold">{title}</p>
          <p class="text-sm text-paper-dim">{job.kind === "render" ? "Rendering clips" : "Finding clips"}: {state.label}{active && job.stage ? `, ${job.stage}` : ""}</p>
        </div>
        <Button kind="quiet" onClick={toggleLog}>{log ? "Hide log" : "Show log"}</Button>
        {active && <Button kind="danger" icon={CircleStop} onClick={cancel}>Cancel</Button>}
        {job.state === "done" && job.run_id && <Button icon={Scissors} onClick={() => { setRunId(job.run_id!); go("clips"); }}>Review clips</Button>}
      </div>
      {active && (
        <div class="mt-3 h-3 overflow-hidden rounded-sm bg-mat" role="progressbar" aria-valuenow={job.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Progress">
          <div class="tape h-full transition-[width] duration-500" style={{ width: `${Math.max(job.percent, 2)}%` }} />
        </div>
      )}
      {job.error && <div class="mt-3"><Notice kind="error">{job.error}</Notice></div>}
      {error && <div class="mt-3"><Notice kind="error">{error}</Notice></div>}
      {log && <pre class="scroll-thin mt-3 max-h-64 overflow-auto rounded-md bg-mat p-3 text-xs whitespace-pre-wrap text-paper-dim">{log.join("\n") || "No output yet."}</pre>}
    </li>
  );
}

export function JobsScreen() {
  const { jobs, go } = useApp();
  return (
    <div class="mx-auto max-w-4xl">
      <PageTitle title="Progress" note="Jobs keep running while you use other screens." />
      {jobs.length ? (
        <ul class="rounded-md border border-mat-line">{jobs.slice(0, 20).map((job) => <JobRow key={job.id} job={job} />)}</ul>
      ) : (
        <Empty title="Nothing running"><p class="mb-4">Start with a video link.</p><Button kind="primary" onClick={() => go("new")}>New video</Button></Empty>
      )}
    </div>
  );
}
