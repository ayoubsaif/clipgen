import type { Clip } from "../api";
import { clock } from "../ui";

const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];

export function Ruler({ duration, clips, selected, range, onSelect }: { duration: number; clips: Clip[]; selected: number | null; range?: [number, number] | null; onSelect: (number: number) => void }) {
  const total = Math.max(duration, 1);
  const step = STEPS.find((value) => total / value <= 12) ?? 14400;
  const ticks = Array.from({ length: Math.floor(total / step) + 1 }, (_, index) => index * step);
  const percent = (value: number) => `${Math.min(value / total, 1) * 100}%`;
  return (
    <div class="rounded-md border border-mat-line bg-mat-deep px-4 pt-2 pb-3">
      <div class="relative h-6" aria-hidden="true">
        {ticks.map((tick) => (
          <span key={tick} class="tabular absolute top-0 -translate-x-1/2 text-[11px] text-paper-dim first:translate-x-0" style={{ left: percent(tick) }}>{clock(tick)}</span>
        ))}
      </div>
      <div class="relative h-3" aria-hidden="true">
        {ticks.map((tick) => <span key={tick} class="absolute top-0 h-3 w-px bg-paper-dim" style={{ left: percent(tick) }} />)}
        {ticks.flatMap((tick) => [1, 2, 3, 4].map((part) => tick + (step * part) / 5).filter((value) => value < total).map((value) => (
          <span key={value} class="absolute top-0 h-1.5 w-px bg-mat-line" style={{ left: percent(value) }} />
        )))}
      </div>
      <div class="relative mt-1 h-9 rounded-sm bg-mat">
        {range && <div class="absolute inset-y-0 border-x-2 border-keep bg-keep/30" style={{ left: percent(range[0]), width: percent(range[1] - range[0]) }} />}
        {/* Shorter clips sit on top, so a clip inside a longer one stays clickable. */}
        {[...clips].sort((a, b) => b.duration - a.duration).map((clip) => (
          <button
            key={clip.number}
            type="button"
            onClick={() => onSelect(clip.number)}
            aria-label={`Clip ${clip.number}: ${clip.title}, ${clock(clip.start)} to ${clock(clip.end)}`}
            aria-pressed={selected === clip.number}
            title={clip.title}
            class={`tape tabular absolute top-1 bottom-1 min-w-5 cursor-pointer overflow-hidden text-center text-xs font-bold ${selected === clip.number ? "outline-2 outline-offset-1 outline-paper" : clip.decision === "rejected" ? "opacity-30" : "opacity-70 hover:opacity-100"}`}
            style={{ left: percent(clip.start), width: percent(clip.end - clip.start) }}
          >
            {clip.number}
          </button>
        ))}
      </div>
    </div>
  );
}
