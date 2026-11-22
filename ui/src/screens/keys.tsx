import { CircleAlert, Globe, Lock, Save, Trash2 } from "lucide-preact";
import { useEffect, useState } from "preact/hooks";
import type { CredentialGroup } from "../api";
import { api } from "../api";
import { useApp } from "../context";
import { Button, Field, Notice, PageTitle } from "../ui";

const STATUS = {
  stored: { icon: Lock, label: "Saved", color: "text-keep" },
  environment: { icon: Globe, label: "From environment", color: "text-keep" },
  unavailable: { icon: CircleAlert, label: "Vault unavailable", color: "text-cut" },
  missing: { icon: null, label: "Not set", color: "text-paper-dim" },
} as const;

function CredentialRow({ item, onChange }: { item: CredentialGroup["items"][number]; onChange: () => void }) {
  const { toast } = useApp();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const status = STATUS[item.status as keyof typeof STATUS] ?? STATUS.missing;
  const Icon = status.icon;

  const save = async (event: Event) => {
    event.preventDefault();
    setError("");
    try {
      await api(`/api/credentials/${item.name}`, "PUT", { value });
      setValue("");
      toast(`${item.label} saved.`);
      onChange();
    } catch (e) {
      setValue("");
      setError((e as Error).message);
    }
  };
  const remove = async () => {
    try {
      await api(`/api/credentials/${item.name}`, "DELETE");
      toast(`${item.label} removed.`);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <form onSubmit={save} class="py-2.5">
      <div class="mb-1 flex items-center justify-between gap-2 text-sm">
        <label for={item.name} class="font-medium">{item.label}</label>
        <span class={`inline-flex items-center gap-1 text-xs ${status.color}`}>{Icon && <Icon size={13} aria-hidden="true" />}{status.label}</span>
      </div>
      <div class="flex gap-2">
        <input id={item.name} class="field" type="password" autoComplete="off" spellcheck={false} placeholder={item.status === "stored" ? "Enter a new value to replace it" : "Paste value"} value={value} onInput={(e) => setValue(e.currentTarget.value)} />
        <Button type="submit" icon={Save} disabled={!value.trim()}>Save</Button>
        {item.status === "stored" && <Button kind="danger" icon={Trash2} aria-label={`Remove ${item.label}`} onClick={remove} />}
      </div>
      {error && <div class="mt-2"><Notice kind="error">{error}</Notice></div>}
    </form>
  );
}

export function KeysScreen() {
  const { toast } = useApp();
  const [groups, setGroups] = useState<CredentialGroup[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const load = () => api<CredentialGroup[]>("/api/credentials").then(setGroups).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    api<{ url: string }>("/api/postiz/settings").then((data) => setUrl(data.url)).catch((e) => setError(e.message));
  }, []);

  const saveUrl = async (event: Event) => {
    event.preventDefault();
    setError("");
    try {
      const saved = await api<{ url: string }>("/api/postiz/settings", "PUT", { url });
      setUrl(saved.url);
      toast("Postiz address saved.");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div class="mx-auto max-w-2xl">
      <PageTitle title="Credentials" note="Saved in Windows Credential Manager and never shown again. Use API tokens, not account passwords." />
      {error && <div class="mb-4"><Notice kind="error">{error}</Notice></div>}
      <form onSubmit={saveUrl} class="mb-4 rounded-md border border-mat-line bg-mat-deep p-4">
        <Field label="Postiz API address" hint="Keep the default for Postiz Cloud, or enter your self-hosted address.">
          <div class="flex gap-2"><input class="field" value={url} onInput={(e) => setUrl(e.currentTarget.value)} /><Button type="submit" icon={Save}>Save</Button></div>
        </Field>
      </form>
      <div class="space-y-3">
        {groups.map((group, index) => {
          const ready = group.items.filter((item) => item.status !== "missing").length;
          return (
            <details key={group.group} open={index === 0} class="rounded-md border border-mat-line bg-mat-deep px-4 py-3">
              <summary class="flex cursor-pointer items-center justify-between font-semibold">
                <span>{group.group}</span><span class="tabular text-sm font-normal text-paper-dim">{ready} of {group.items.length} set</span>
              </summary>
              <div class="mt-1 divide-y divide-mat-line">{group.items.map((item) => <CredentialRow key={item.name} item={item} onChange={load} />)}</div>
            </details>
          );
        })}
      </div>
    </div>
  );
}
