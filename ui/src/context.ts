import { createContext } from "preact";
import { useContext } from "preact/hooks";
import type { Job, RunSummary } from "./api";

export type Page = "new" | "jobs" | "clips" | "post" | "keys";

export const PAGE_NAMES: Record<Page, string> = {
  new: "Create",
  jobs: "Progress",
  clips: "Step 1 · Clips",
  post: "Step 2 · Post",
  keys: "Credentials",
};

export interface AppState {
  page: Page;
  go: (page: Page) => void;
  runs: RunSummary[];
  runId: string | null;
  setRunId: (id: string) => void;
  clipNumber: number | null;
  setClipNumber: (number: number | null) => void;
  jobs: Job[];
  busy: boolean;
  version: number;
  refresh: () => void;
  toast: (message: string) => void;
}

export const AppContext = createContext<AppState>(null as unknown as AppState);
export const useApp = () => useContext(AppContext);
