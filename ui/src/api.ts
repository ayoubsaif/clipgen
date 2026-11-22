const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
const TOKEN_KEY = "clipgen-token";

function readToken(): string {
  const match = location.hash.match(/token=([\w-]+)/);
  try {
    if (match) sessionStorage.setItem(TOKEN_KEY, match[1]);
    if (match) history.replaceState(null, "", location.pathname);
    return match?.[1] ?? sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return match?.[1] ?? "";
  }
}

const token = readToken();

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("clipgen's engine is not responding. Close this window and open clipgen again.", 0);
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const fields = data?.errors?.map((item: { field: string; message: string }) => `${item.field}: ${item.message}`).join(", ");
    throw new ApiError(fields || data?.detail || `Request failed (${response.status}).`, response.status);
  }
  return data as T;
}

export const media = (url: string | null) => (url ? BASE + url : "");

export function watchJobs(onJobs: (jobs: Job[]) => void): () => void {
  const controller = new AbortController();
  (async () => {
    while (!controller.signal.aborted) {
      try {
        const response = await fetch(`${BASE}/api/events`, { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
        const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          const events = buffer.split("\n\n");
          buffer = events.pop() ?? "";
          for (const event of events) {
            const line = event.split("\n").find((item) => item.startsWith("data: "));
            if (line) onJobs(JSON.parse(line.slice(6)));
          }
        }
      } catch {
        /* The loop below reconnects. */
      }
      if (!controller.signal.aborted) await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  })();
  return () => controller.abort();
}

export type Layout = "blur" | "center" | "facecam";
export type Decision = "pending" | "approved" | "rejected";

export interface Job {
  id: string;
  kind: "run" | "render";
  target: string | null;
  run_id: string | null;
  state: "queued" | "running" | "done" | "failed" | "canceled";
  percent: number;
  stage: string;
  error: string | null;
}

export interface RunSummary {
  id: string;
  title: string;
  target: string;
  clips: number;
  approved: number;
  updated_at: number;
}

export interface Clip {
  number: number;
  start: number;
  end: number;
  duration: number;
  score: number;
  title: string;
  hook: string;
  why: string;
  tiktok_caption: string;
  instagram_caption: string;
  decision: Decision;
  rendered: boolean;
  video_url: string | null;
}

export type Cam = [number, number, number, number];
export interface WebcamPosition {
  cam: Cam;
  origin: "claude" | "manual";
  frame: number;
}

export interface Run {
  id: string;
  title: string;
  target: string;
  clips: Clip[];
  webcam: WebcamPosition | null;
}

export interface CaptionLine {
  index: number;
  start: number;
  end: number;
  text: string;
}

export interface TranscriptView {
  language: string;
  source: string;
  duration: number;
  lines: CaptionLine[];
}

export interface Frame {
  index: number;
  width: number;
  height: number;
  url: string;
}

export interface ChatAction {
  id: string;
  type: string;
  status: "pending" | "applied" | "discarded";
  description: string;
  needs_render: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
}

export interface Channel {
  id: string;
  name: string;
  identifier: string;
}

export interface SentPost {
  clip: number;
  mode: string;
  date: string;
  channels: { name: string }[];
}

export interface CredentialGroup {
  group: string;
  items: { name: string; label: string; status: string }[];
}
