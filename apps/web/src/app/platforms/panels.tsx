"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api";
import { formatDate, timeAgo, truncate } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

type Capabilities = Schemas["Capabilities"];
type Platform = Schemas["Platform"];
type SyncKind = Schemas["SyncKind"];
type SyncResult = Schemas["SyncResult"];
type Tone = "good" | "warn" | "bad" | "accent" | "violet" | "neutral";

export const SYNC_STATUS_TONE: Record<string, Tone> = {
  ok: "good",
  partial: "warn",
  failed: "bad",
  skipped: "neutral",
};

const APPLICATION_TONE: Record<string, Tone> = {
  applied: "accent",
  viewed: "neutral",
  invited: "good",
  interview: "good",
  offer: "good",
  rejected: "bad",
  withdrawn: "neutral",
  unknown: "neutral",
};

const PASTE_KINDS: SyncKind[] = ["profile", "jobs", "applications"];
const PLACEHOLDER: Record<string, string> = {
  profile: "Open your profile page, select all, paste it here.",
  jobs: "Paste a search-results page (or a single job posting).",
  applications: "Paste your applications / proposals page.",
};

function itemLabel(item: Record<string, unknown>): string {
  for (const key of ["title", "job_title", "headline", "name", "url"]) {
    const value = item[key];
    if (typeof value === "string" && value) return truncate(value, 80);
  }
  return truncate(JSON.stringify(item), 80);
}

/** Paste path (ADR-013): every platform accepts pasted page text, with a parse-only preview. */
export function PastePanel({ capabilities }: { capabilities: Capabilities[] }) {
  const queryClient = useQueryClient();
  // Offered per kind: an account-less reader exposes only `jobs` paste (a job page), never a
  // profile or applications page — the kind filter below derives that from the capabilities.
  const pasteable = capabilities.filter(
    (c) =>
      (c.profile ?? []).includes("paste") ||
      (c.jobs ?? []).includes("paste") ||
      (c.applications ?? []).includes("paste"),
  );
  const [platform, setPlatform] = useState<Platform | "">("");
  const [kind, setKind] = useState<SyncKind>("jobs");
  const [text, setText] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [result, setResult] = useState<SyncResult | null>(null);

  const chosen = pasteable.find((c) => c.platform === platform);
  const kinds = PASTE_KINDS.filter((k) => {
    if (!chosen) return true;
    const methods =
      k === "profile" ? chosen.profile : k === "jobs" ? chosen.jobs : chosen.applications;
    return (methods ?? []).includes("paste");
  });

  const sync = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/platform/{platform}/sync/{kind}", {
          params: { path: { platform: platform as Platform, kind } },
          body: {
            method: "paste",
            text,
            file_path: null,
            query: null,
            use_ai: false,
            provider: null,
            dry_run: dryRun,
          },
        }),
      ),
    onSuccess: (data) => {
      setResult(data);
      if (!dryRun) {
        queryClient.invalidateQueries({ queryKey: ["platform-runs"] });
        queryClient.invalidateQueries({ queryKey: ["platform-observations"] });
        queryClient.invalidateQueries({ queryKey: ["platform-connections"] });
        queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      }
    },
  });
  const canSync = platform !== "" && text.trim().length > 20 && !sync.isPending;

  return (
    <Card title="Paste from a platform" className="lg:col-span-2">
      <div className="space-y-3">
        <p className="text-xs text-ink-dim">
          Nothing is scraped and no password is ever stored: you open the page yourself and paste what you see. The
          parser is deterministic; preview first, import when it looks right.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <select
            className="input"
            value={platform}
            onChange={(e) => {
              const next = e.target.value as Platform | "";
              setPlatform(next);
              setResult(null);
            }}
          >
            <option value="">choose a platform…</option>
            {pasteable.map((c) => (
              <option key={c.platform} value={c.platform}>
                {c.platform}
              </option>
            ))}
          </select>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as SyncKind)}>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
        <textarea
          className="input h-40 font-mono text-xs"
          placeholder={PLACEHOLDER[kind] ?? "Paste the page text…"}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-ink-dim">
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            preview only (parse, store nothing)
          </label>
          <button className="btn btn-primary" disabled={!canSync} onClick={() => sync.mutate()}>
            {sync.isPending ? "Parsing…" : dryRun ? "Preview" : "Import"}
          </button>
          {result && !dryRun && (
            <span className="text-xs text-ink-dim">
              {result.items_created} created · {result.items_updated} updated · {result.items_skipped} skipped
            </span>
          )}
        </div>
        {sync.isError && <ErrorBox error={sync.error} />}
        {result && (
          <div className="space-y-2 rounded-lg border border-line bg-panel-2 p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={SYNC_STATUS_TONE[result.status] ?? "neutral"}>{result.status}</Badge>
              <span className="text-ink-dim">
                {result.platform} · {result.kind} · {result.method ?? "—"} · {result.items_seen} item(s) seen
              </span>
            </div>
            {result.message && <p>{result.message}</p>}
            {(result.warnings ?? []).length > 0 && (
              <ul className="list-inside list-disc text-warn">
                {(result.warnings ?? []).map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
            {(result.preview ?? []).length > 0 && (
              <ol className="list-inside list-decimal space-y-0.5">
                {(result.preview ?? []).map((item, i) => (
                  <li key={i}>{itemLabel(item as Record<string, unknown>)}</li>
                ))}
              </ol>
            )}
            {(result.duplicates ?? []).length > 0 && (
              <p className="text-ink-dim">{(result.duplicates ?? []).length} already known (deduped)</p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

export function SyncRunsPanel() {
  const runs = useQuery({
    queryKey: ["platform-runs"],
    queryFn: async () => unwrap(await api.GET("/api/platform/sync-runs")),
  });

  return (
    <Card title="Sync runs" className="lg:col-span-3">
      {runs.isPending ? (
        <Spinner />
      ) : runs.isError ? (
        <ErrorBox error={runs.error} />
      ) : (runs.data ?? []).length === 0 ? (
        <Empty>No syncs yet — connect a platform or paste a page above.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left uppercase tracking-wide text-ink-dim">
                <th className="pb-2 pr-2">When</th>
                <th className="pb-2 pr-2">Platform</th>
                <th className="pb-2 pr-2">Kind</th>
                <th className="pb-2 pr-2">Method</th>
                <th className="pb-2 pr-2">Status</th>
                <th className="pb-2 pr-2 text-right">Seen</th>
                <th className="pb-2 pr-2 text-right">New</th>
                <th className="pb-2 pr-2 text-right">Updated</th>
                <th className="pb-2">Error</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data ?? []).map((r) => (
                <tr key={r.id} className="border-t border-line/60">
                  <td className="py-1.5 pr-2 text-ink-dim">{timeAgo(r.started_at)}</td>
                  <td className="py-1.5 pr-2">{r.platform}</td>
                  <td className="py-1.5 pr-2">{r.kind}</td>
                  <td className="py-1.5 pr-2 text-ink-dim">{r.method}</td>
                  <td className="py-1.5 pr-2">
                    <Badge tone={SYNC_STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
                  </td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.items_seen}</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.items_created}</td>
                  <td className="py-1.5 pr-2 text-right font-mono">{r.items_updated}</td>
                  <td className="max-w-sm truncate py-1.5 text-bad">{r.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function ObservationsPanel() {
  const observations = useQuery({
    queryKey: ["platform-observations"],
    queryFn: async () => unwrap(await api.GET("/api/platform/applications")),
  });

  return (
    <Card title="Application statuses observed on platforms" className="lg:col-span-3">
      {observations.isPending ? (
        <Spinner />
      ) : observations.isError ? (
        <ErrorBox error={observations.error} />
      ) : (observations.data ?? []).length === 0 ? (
        <Empty>Nothing observed yet — sync or paste an applications page.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left uppercase tracking-wide text-ink-dim">
                <th className="pb-2 pr-2">Role</th>
                <th className="pb-2 pr-2">Company</th>
                <th className="pb-2 pr-2">Platform</th>
                <th className="pb-2 pr-2">Status</th>
                <th className="pb-2 pr-2">Observed</th>
                <th className="pb-2">Raw</th>
              </tr>
            </thead>
            <tbody>
              {(observations.data ?? []).map((o) => (
                <tr key={o.id} className="border-t border-line/60">
                  <td className="max-w-64 py-1.5 pr-2">
                    {o.job_url ? (
                      <a className="text-accent hover:underline" href={o.job_url} target="_blank" rel="noreferrer">
                        {truncate(o.job_title, 60)}
                      </a>
                    ) : (
                      truncate(o.job_title, 60)
                    )}
                  </td>
                  <td className="py-1.5 pr-2 text-ink-dim">{o.company ?? "—"}</td>
                  <td className="py-1.5 pr-2">{o.platform}</td>
                  <td className="py-1.5 pr-2">
                    <Badge tone={APPLICATION_TONE[o.status] ?? "neutral"}>{o.status}</Badge>
                  </td>
                  <td className="py-1.5 pr-2 text-ink-dim">{formatDate(o.observed_at)}</td>
                  <td className="max-w-sm truncate py-1.5 text-ink-dim">{o.status_raw}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
