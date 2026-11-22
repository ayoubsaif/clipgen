import type { LucideIcon } from "lucide-preact";
import { LoaderCircle } from "lucide-preact";
import type { ButtonHTMLAttributes, ComponentChildren } from "preact";

export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "icon" | "disabled" | "type"> & {
  kind?: "plain" | "primary" | "quiet" | "danger";
  icon?: LucideIcon;
  busy?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
};

const KINDS = {
  primary: "bg-tape text-tape-ink font-semibold hover:brightness-105",
  plain: "border border-mat-line bg-mat-raised text-paper hover:border-paper-dim",
  quiet: "text-paper-dim hover:bg-mat-raised hover:text-paper",
  danger: "border border-cut/60 text-cut hover:bg-cut/10",
};

export function Button({ kind = "plain", icon: Icon, busy, children, class: extra, disabled, type = "button", ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || busy}
      class={`inline-flex min-h-9 cursor-pointer items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${KINDS[kind]} ${extra ?? ""}`}
    >
      {busy ? <LoaderCircle size={16} class="animate-spin" /> : Icon ? <Icon size={16} aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function Field({ label, hint, children, class: extra }: { label: string; hint?: string; children: ComponentChildren; class?: string }) {
  return (
    <label class={`block ${extra ?? ""}`}>
      <span class="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span class="mt-1 block text-xs text-paper-dim">{hint}</span>}
    </label>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "error"; children: ComponentChildren }) {
  return (
    <p role={kind === "error" ? "alert" : "status"} class={`rounded-md border px-3 py-2 text-sm ${kind === "error" ? "border-cut/60 bg-cut/10 text-cut" : "border-mat-line bg-mat-deep text-paper-dim"}`}>
      {children}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ComponentChildren }) {
  return (
    <div class="mx-auto max-w-md py-20 text-center">
      <h2 class="font-display text-2xl font-semibold">{title}</h2>
      <div class="mt-2 text-paper-dim">{children}</div>
    </div>
  );
}

export function PageTitle({ title, note, children }: { title: string; note?: string; children?: ComponentChildren }) {
  return (
    <header class="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="font-display text-3xl leading-tight font-bold tracking-tight">{title}</h1>
        {note && <p class="mt-1 max-w-[65ch] text-paper-dim">{note}</p>}
      </div>
      {children}
    </header>
  );
}

export function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string; icon?: LucideIcon }[]; onChange: (value: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} class="inline-flex flex-wrap gap-1 rounded-md border border-mat-line bg-mat-deep p-1">
      {options.map(({ value: option, label: text, icon: Icon }) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={value === option}
          onClick={() => onChange(option)}
          class={`inline-flex cursor-pointer items-center gap-1.5 rounded px-3 py-1 text-sm ${value === option ? "bg-tape font-semibold text-tape-ink" : "text-paper-dim hover:text-paper"}`}
        >
          {Icon && <Icon size={15} aria-hidden="true" />}
          {text}
        </button>
      ))}
    </div>
  );
}
