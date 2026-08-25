"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, unwrap, type AuditOut } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, ScoreRing, severityTone, Spinner } from "@/components/ui";

const PLATFORMS = ["linkedin", "wellfound", "upwork", "toptal", "hh", "indeed", "getmatch"] as const;

function SnapshotForm({ onDone }: { onDone: () => void }) {
  const [platform, setPlatform] = useState<(typeof PLATFORMS)[number]>("linkedin");
  const [headline, setHeadline] = useState("");
  const [about, setAbout] = useState("");
  const [skills, setSkills] = useState("");
  const [rawText, setRawText] = useState("");
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/profiles/snapshots", {
          body: {
            platform,
            capture_method: "paste",
            headline: headline || null,
            about: about || null,
            skills: skills ? skills.split(",").map((s) => s.trim()).filter(Boolean) : [],
            raw_text: rawText || null,
          },
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries();
      onDone();
    },
  });

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
        <div>
          <label className="label">Platform</label>
          <select className="input" value={platform} onChange={(e) => setPlatform(e.target.value as (typeof PLATFORMS)[number])}>
            {PLATFORMS.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Headline</label>
          <input className="input" value={headline} onChange={(e) => setHeadline(e.target.value)} placeholder="Copy the profile headline…" />
        </div>
      </div>
      <div>
        <label className="label">About</label>
        <textarea className="input h-28" value={about} onChange={(e) => setAbout(e.target.value)} placeholder="Copy the about/summary section…" />
      </div>
      <div>
        <label className="label">Skills (comma-separated)</label>
        <input className="input" value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="Python, SQL, dbt…" />
      </div>
      <div>
        <label className="label">Everything else (experience, projects — raw paste)</label>
        <textarea className="input h-28 font-mono text-xs" value={rawText} onChange={(e) => setRawText(e.target.value)} />
      </div>
      <button className="btn btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
        Save snapshot
      </button>
      {create.isError && <ErrorBox error={create.error} />}
    </div>
  );
}

function AuditView({ audit }: { audit: AuditOut }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <ScoreRing score={audit.health_score} size="text-4xl" />
        <div className="text-xs text-ink-dim">
          <p>
            Profile Health Score · {audit.engine_version}
            {audit.ai_used && (
              <>
                {" "}
                <Badge tone="violet">+ AI</Badge>
              </>
            )}
          </p>
          <p>{audit.findings.length} findings</p>
        </div>
      </div>
      {audit.headline_suggestion && (
        <div>
          <div className="label">Suggested headline</div>
          <blockquote className="rounded-lg border border-line bg-panel-2 p-2 text-sm">{audit.headline_suggestion}</blockquote>
        </div>
      )}
      <ul className="space-y-2">
        {audit.findings.map((f, i) => (
          <li key={f.id ?? i} className="rounded-lg border border-line p-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-1.5 pb-1">
              <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
              <Badge>{f.category.replaceAll("_", " ")}</Badge>
              {f.origin === "ai" && <Badge tone="violet">AI · conf {f.confidence}</Badge>}
              {f.resolution !== "open" && <Badge tone="good">{f.resolution}</Badge>}
            </div>
            <p className="font-medium">{f.problem}</p>
            <p className="text-xs text-ink-dim">{f.why_it_matters}</p>
            {f.suggested_change && <p className="pt-1 text-xs text-accent">→ {f.suggested_change}</p>}
            {(f.source_fact_ids ?? []).length > 0 && (
              <p className="pt-1 font-mono text-[10px] text-accent-2">{(f.source_fact_ids ?? []).join(", ")}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DriftPanel() {
  const queryClient = useQueryClient();
  const drift = useQuery({
    queryKey: ["drift"],
    queryFn: async () => unwrap(await api.GET("/api/profiles/drift", { params: { query: { open_only: false } } })),
  });
  const recompute = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/profiles/drift/recompute")),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["drift"] });
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
      queryClient.invalidateQueries({ queryKey: ["brief"] });
    },
  });
  const resolve = useMutation({
    mutationFn: async ({ id, resolution }: { id: string; resolution: "resolved" | "dismissed" | "open" }) =>
      unwrap(
        await api.PATCH("/api/profiles/drift/{finding_id}", {
          params: { path: { finding_id: id } },
          body: { resolution },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["drift"] }),
  });
  const open = (drift.data?.findings ?? []).filter((f) => f.resolution === "open");
  const decided = (drift.data?.findings ?? []).filter((f) => f.resolution !== "open");
  return (
    <Card
      title={`Drift — ${drift.data?.open ?? 0} out of sync`}
      action={
        <button className="btn" onClick={() => recompute.mutate()} disabled={recompute.isPending}>
          {recompute.isPending ? "Checking…" : "Recompute"}
        </button>
      }
    >
      {recompute.isError && <ErrorBox error={recompute.error} />}
      {drift.isPending ? (
        <Spinner />
      ) : open.length === 0 ? (
        <Empty>No open drift between platforms and the vault. Recompute after new snapshots.</Empty>
      ) : (
        <ul className="space-y-2">
          {open.map((f) => (
            <li key={f.id} className="rounded-lg border border-line p-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-1.5 pb-1">
                <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
                <Badge>{f.field.replaceAll("_", " ")}</Badge>
                <span className="text-xs text-ink-dim">
                  {f.platform_a} ↔ {f.platform_b}
                </span>
              </div>
              <p>{f.message}</p>
              <p className="text-xs text-ink-dim">
                {f.platform_a}: <span className="text-ink">{f.value_a}</span> · {f.platform_b}:{" "}
                <span className="text-ink">{f.value_b}</span>
              </p>
              <div className="flex gap-1.5 pt-1.5">
                <button className="btn px-2 py-0.5 text-[11px]" onClick={() => resolve.mutate({ id: f.id, resolution: "resolved" })}>
                  fixed
                </button>
                <button className="btn px-2 py-0.5 text-[11px]" onClick={() => resolve.mutate({ id: f.id, resolution: "dismissed" })}>
                  dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {decided.length > 0 && (
        <p className="pt-2 text-xs text-ink-dim">{decided.length} resolved/dismissed (kept across recomputes)</p>
      )}
    </Card>
  );
}

function ProfilesPageInner() {
  const searchParams = useSearchParams();
  const [showNew, setShowNew] = useState(searchParams.get("new") === "1");
  const [audit, setAudit] = useState<AuditOut | null>(null);
  const [useAi, setUseAi] = useState(false);
  const queryClient = useQueryClient();

  const health = useQuery({
    queryKey: ["profiles-health"],
    queryFn: async () => unwrap(await api.GET("/api/profiles/health")),
  });
  const snapshots = useQuery({
    queryKey: ["profiles-snapshots"],
    queryFn: async () => unwrap(await api.GET("/api/profiles/snapshots", { params: { query: { limit: 30 } } })),
  });

  const runAudit = useMutation({
    mutationFn: async (snapshotId: string) =>
      unwrap(
        await api.POST("/api/profiles/snapshots/{snapshot_id}/audit", {
          params: { path: { snapshot_id: snapshotId } },
          body: { use_ai: useAi },
        }),
      ),
    onSuccess: (data) => {
      setAudit(data);
      queryClient.invalidateQueries({ queryKey: ["profiles-health"] });
      queryClient.invalidateQueries({ queryKey: ["profiles-snapshots"] });
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Platform profiles</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-ink-dim">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
            AI audit
          </label>
          <button className="btn btn-primary" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Close" : "Add snapshot"}
          </button>
        </div>
      </div>

      {showNew && (
        <Card title="Capture a profile snapshot (copy/paste — no scraping, ever)">
          <SnapshotForm onDone={() => setShowNew(false)} />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <Card title="Health by platform">
            {health.isPending ? (
              <Spinner />
            ) : (
              <ul className="space-y-2 text-sm">
                {(health.data ?? []).map((h) => (
                  <li key={h.platform} className="flex items-center justify-between">
                    <span className="capitalize">{h.platform}</span>
                    <span className="flex items-center gap-2">
                      {h.open_findings > 0 && h.top_severity && (
                        <Badge tone={severityTone(h.top_severity)}>{h.open_findings}</Badge>
                      )}
                      {h.health_score == null ? <span className="text-xs text-ink-dim">—</span> : <ScoreRing score={h.health_score} size="text-base" />}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Snapshots">
            {snapshots.isPending ? (
              <Spinner />
            ) : (snapshots.data ?? []).length === 0 ? (
              <Empty>No snapshots yet.</Empty>
            ) : (
              <ul className="space-y-2 text-sm">
                {(snapshots.data ?? []).map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-2">
                    <span>
                      <span className="capitalize">{s.platform}</span>
                      <span className="block text-[11px] text-ink-dim">{timeAgo(s.captured_at)}</span>
                    </span>
                    <span className="flex items-center gap-1.5">
                      {s.latest_health_score != null && <ScoreRing score={s.latest_health_score} size="text-sm" />}
                      <button className="btn" onClick={() => runAudit.mutate(s.id)} disabled={runAudit.isPending}>
                        {runAudit.isPending ? "…" : "Audit"}
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-4">
        <Card title="Audit result">
          {runAudit.isError ? (
            <ErrorBox error={runAudit.error} />
          ) : !audit ? (
            <Empty>Run an audit to see findings: problem → why it matters → suggested change → source facts.</Empty>
          ) : (
            <AuditView audit={audit} />
          )}
        </Card>
        <DriftPanel />
        </div>
      </div>
    </div>
  );
}

export default function ProfilesPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <ProfilesPageInner />
    </Suspense>
  );
}
