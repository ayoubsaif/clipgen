import { Clapperboard, Sparkles } from "lucide-preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Cam, Frame, Run, WebcamPosition } from "../api";
import { api, media } from "../api";
import { useApp } from "../context";
import { Button, Notice, Segmented } from "../ui";

const MIN_SIDE = 32;
type Corner = "nw" | "ne" | "sw" | "se";
const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
const CORNER_CLASS: Record<Corner, string> = {
  nw: "-left-2 -top-2 cursor-nwse-resize", ne: "-right-2 -top-2 cursor-nesw-resize",
  sw: "-left-2 -bottom-2 cursor-nesw-resize", se: "-right-2 -bottom-2 cursor-nwse-resize",
};

function clampBox([x, y, w, h]: Cam, width: number, height: number): Cam {
  const boxW = Math.min(Math.max(w, MIN_SIDE), width);
  const boxH = Math.min(Math.max(h, MIN_SIDE), height);
  return [Math.min(Math.max(x, 0), width - boxW), Math.min(Math.max(y, 0), height - boxH), boxW, boxH];
}

/** Draws the same crop the renderer uses, so the preview matches the final clip. */
function VerticalPreview({ image, box }: { image: HTMLImageElement | null; box: Cam }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const context = canvas.current?.getContext("2d");
    if (!context || !image?.naturalWidth) return;
    const [x, y, w, h] = box;
    const panel = 1080 / 640;
    const cropW = w / h > panel ? h * panel : w;
    const cropH = w / h > panel ? h : w / panel;
    context.drawImage(image, x + (w - cropW) / 2, y + (h - cropH) / 2, cropW, cropH, 0, 0, 270, 160);
    const gameW = Math.min(image.naturalWidth, image.naturalHeight * 27 / 32);
    const gameH = Math.min(image.naturalHeight, image.naturalWidth * 32 / 27);
    context.drawImage(image, (image.naturalWidth - gameW) / 2, (image.naturalHeight - gameH) / 2, gameW, gameH, 0, 160, 270, 320);
  }, [image, box]);
  return <canvas ref={canvas} width={270} height={480} class="w-full rounded-md bg-black" role="img" aria-label="Preview of the vertical clip" />;
}

export function WebcamEditor({ run, onChanged }: { run: Run; onChanged: () => void }) {
  const { busy, toast } = useApp();
  const [frames, setFrames] = useState<Frame[]>([]);
  const [frameIndex, setFrameIndex] = useState(0);
  const [box, setBox] = useState<Cam | null>(null);
  const [origin, setOrigin] = useState<"claude" | "manual" | null>(null);
  const [dirty, setDirty] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const stage = useRef<HTMLImageElement>(null);
  const drag = useRef<{ corner: Corner | null; x: number; y: number; box: Cam } | null>(null);
  const frame = frames[frameIndex];

  const detect = async (available: Frame[]) => {
    setDetecting(true);
    setError("");
    setNote("");
    try {
      const result = await api<{ found: boolean; position: WebcamPosition | null }>(`/api/runs/${run.id}/webcam/detect`, "POST");
      if (result.found && result.position) {
        setBox(result.position.cam);
        setOrigin("claude");
        setFrameIndex(Math.min(result.position.frame, available.length - 1));
        setDirty(false);
        onChanged();
      } else {
        setNote("Claude saw no webcam overlay. Place the box by hand if there is one.");
        setBox((current) => current ?? clampBox([Math.round(available[0].width * 0.03), Math.round(available[0].height * 0.6), Math.round(available[0].width * 0.25), Math.round(available[0].height * 0.3)], available[0].width, available[0].height));
      }
    } catch (e) {
      setError((e as Error).message);
      setBox((current) => current ?? clampBox([40, 500, 400, 300], available[0].width, available[0].height));
    } finally {
      setDetecting(false);
    }
  };

  useEffect(() => {
    let current = true;
    setFrames([]);
    setBox(null);
    setDirty(false);
    api<{ position: WebcamPosition | null; frames: Frame[] }>(`/api/runs/${run.id}/webcam`).then((data) => {
      if (!current) return;
      setFrames(data.frames);
      if (data.position) {
        setBox(data.position.cam);
        setOrigin(data.position.origin);
        setFrameIndex(Math.min(data.position.frame, data.frames.length - 1));
      } else {
        // Auto first: Claude places the box, then the person can correct it.
        detect(data.frames);
      }
    }).catch((e) => current && setError(e.message));
    return () => { current = false; };
  }, [run.id]);

  if (error && !frames.length) return <Notice kind="error">{error}</Notice>;
  if (!frame) return <p class="text-sm text-paper-dim">Loading frames…</p>;

  const point = (event: PointerEvent): [number, number] => {
    const rect = stage.current!.getBoundingClientRect();
    return [(event.clientX - rect.left) * frame.width / rect.width, (event.clientY - rect.top) * frame.height / rect.height];
  };
  const start = (event: PointerEvent, corner: Corner | null) => {
    if (!box) return;
    event.preventDefault();
    event.stopPropagation();
    const [x, y] = point(event);
    drag.current = { corner, x, y, box: [...box] as Cam };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent) => {
    if (!drag.current) return;
    const [px, py] = point(event);
    const dx = px - drag.current.x;
    const dy = py - drag.current.y;
    let [x, y, w, h] = drag.current.box;
    const corner = drag.current.corner;
    if (!corner) {
      x += dx; y += dy;
    } else {
      if (corner.includes("w")) { const next = Math.max(MIN_SIDE, Math.min(w - dx, x + w)); x += w - next; w = next; } else w = Math.min(w + dx, frame.width - x);
      if (corner.includes("n")) { const next = Math.max(MIN_SIDE, Math.min(h - dy, y + h)); y += h - next; h = next; } else h = Math.min(h + dy, frame.height - y);
    }
    setBox(clampBox([Math.round(x), Math.round(y), Math.round(w), Math.round(h)], frame.width, frame.height));
    setDirty(true);
  };
  const end = () => { drag.current = null; };
  const key = (event: KeyboardEvent) => {
    const delta = ({ ArrowLeft: [-4, 0], ArrowRight: [4, 0], ArrowUp: [0, -4], ArrowDown: [0, 4] } as Record<string, number[]>)[event.key];
    if (!delta || !box) return;
    event.preventDefault();
    const [x, y, w, h] = box;
    setBox(clampBox(event.shiftKey ? [x, y, w + delta[0], h + delta[1]] : [x + delta[0], y + delta[1], w, h], frame.width, frame.height));
    setDirty(true);
  };

  const save = async () => {
    if (!box) return null;
    const saved = await api<WebcamPosition>(`/api/runs/${run.id}/webcam`, "PUT", { cam: box, frame: frameIndex });
    setBox(saved.cam);
    setOrigin(saved.origin);
    setDirty(false);
    return saved;
  };
  const saveAndRender = async () => {
    setError("");
    try {
      if (dirty || !origin) await save();
      await api(`/api/runs/${run.id}/render`, "POST", { layout: "facecam" });
      toast("Rendering all clips with this webcam.");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div>
      <div class="mb-3 flex flex-wrap items-center gap-2">
        {frames.length > 1 && (
          <Segmented<string> label="Frame" value={String(frameIndex)} onChange={(value) => setFrameIndex(+value)} options={frames.map((item) => ({ value: String(item.index), label: `Frame ${item.index + 1}` }))} />
        )}
        <Button kind="quiet" icon={Sparkles} busy={detecting} onClick={() => detect(frames)}>Find with Claude</Button>
      </div>
      <div class="grid gap-3 xl:grid-cols-[1fr_9rem]">
        <div>
          <div class="relative overflow-hidden rounded-md select-none" style={{ touchAction: "none" }}>
            <img ref={stage} src={media(frame.url)} alt="Video frame" class="block w-full" draggable={false} onLoad={(e) => setImage(e.currentTarget)} />
            {box && (
              <div
                role="slider"
                tabIndex={0}
                aria-label="Webcam area. Arrow keys move it, Shift and arrow keys resize it."
                aria-valuetext={`Position ${box[0]}, ${box[1]}. Size ${box[2]} by ${box[3]}.`}
                class="absolute cursor-move border-2 border-tape shadow-[0_0_0_9999px_rgba(0,0,0,.55)]"
                style={{ left: `${box[0] / frame.width * 100}%`, top: `${box[1] / frame.height * 100}%`, width: `${box[2] / frame.width * 100}%`, height: `${box[3] / frame.height * 100}%` }}
                onPointerDown={(e) => start(e, null)}
                onPointerMove={move}
                onPointerUp={end}
                onPointerCancel={end}
                onKeyDown={key}
              >
                {CORNERS.map((corner) => (
                  <span key={corner} class={`absolute size-4 border-2 border-tape-ink bg-tape ${CORNER_CLASS[corner]}`} onPointerDown={(e) => start(e, corner)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} />
                ))}
              </div>
            )}
          </div>
          <p class="tabular mt-1.5 text-xs text-paper-dim">
            {detecting ? "Claude is looking for the webcam…" : box ? `${origin === "claude" && !dirty ? "Found by Claude. " : ""}Drag to move, corners to resize. ${box[2]} × ${box[3]} px` : ""}
          </p>
        </div>
        {box && <VerticalPreview image={image} box={box} />}
      </div>
      {note && <div class="mt-3"><Notice>{note}</Notice></div>}
      {error && <div class="mt-3"><Notice kind="error">{error}</Notice></div>}
      <Button kind="primary" icon={Clapperboard} class="mt-3" disabled={!box || busy || detecting} title={busy ? "Wait for the current job to finish." : undefined} onClick={saveAndRender}>Render all clips</Button>
    </div>
  );
}
