"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { recommendationLabel, timeAgo, truncate } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, ScoreRing, Spinner } from "@/components/ui";
import { ComparePanel } from "./compare-panel";

const SOURCES = ["manual", "linkedin", "wellfound", "upwork", "toptal", "hh", "indeed", "getmatch", "recruiter", "email", "direct", "website", "url"] as const;

function IngestForm({ onDone }: { onDone: (id: string) => void }) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [source, setSource] = useState<(typeof SOURCES)[number]>("manual");
  const [useAi, setUseAi] = useState(false);
  const queryClient = useQueryClient();

  const ingest = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/opportunities/ingest", {
          body: { source, text: text || null, url: url || null, use_ai: useAi },
        }),
      ),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      onDone(data.id);
    },
  });

  return (
    <div className="space-y-3">
      <div>
        <label className="label">Job description / message</label>
        <textarea
          className="input h-48 font-mono text-xs"
          placeholder="Paste the JD, recruiter message or project brief…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <label className="label">URL (optional)</label>
          <input className="input" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <div>
          <label className="label">Source</label>
          <select className="input" value={source} onChange={(e) => setSource(e.target.value as (typeof SOURCES)[number])}>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm text-ink-dim">
        <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
        Use AI extraction to fill gaps (needs a configured provider)
      </label>
      <button
        className="btn btn-primary"
        disabled={ingest.isPending || (!text.trim() && !url.trim())}
        onClick={() => ingest.mutate()}
      >
        {ingest.isPending ? "Scoring…" : "Ingest & score"}
      </button>
      {ingest.isError && <ErrorBox error={ingest.error} />}
    </div>
  );
}

function OpportunitiesPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [showNew, setShowNew] = useState(searchParams.get("new") === "1");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [selected, setSelected] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= 5 ? cur : [...cur, id]));

  const opportunities = useQuery({
    queryKey: ["opportunities", statusFilter],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/opportunities", {
          params: { query: statusFilter ? { status: statusFilter as never, limit: 100 } : { limit: 100 } },
        }),
      ),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-bold">Opportunities</h1>
        <div className="flex items-center gap-2">
          <select className="input w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">all statuses</option>
            {["new", "watching", "applied", "ignored", "archived"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button
            className="btn"
            onClick={() => setCompareOpen((v) => !v)}
            disabled={selected.length < 2 && !compareOpen}
            title="Tick 2–5 rows to compare them side by side"
          >
            Compare{selected.length > 0 ? ` (${selected.length})` : ""}
          </button>
          <button className="btn btn-primary" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Close" : "Add opportunity"}
          </button>
        </div>
      </div>

      {showNew && (
        <Card title="Paste a JD, recruiter message or project brief">
          <IngestForm onDone={(id) => router.push(`/opportunities/${id}`)} />
        </Card>
      )}

      {compareOpen && <ComparePanel ids={selected} onClose={() => setCompareOpen(false)} />}

      <Card>
        {opportunities.isPending ? (
          <Spinner />
        ) : opportunities.isError ? (
          <ErrorBox error={opportunities.error} />
        ) : (opportunities.data ?? []).length === 0 ? (
          <Empty>No opportunities yet — paste your first JD.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-dim">
                <th className="w-6 pb-2" title="select for comparison" />
                <th className="pb-2">Role</th>
                <th className="pb-2">Company</th>
                <th className="pb-2">Remote</th>
                <th className="pb-2">Recommendation</th>
                <th className="pb-2 text-right">Score</th>
                <th className="pb-2 text-right">Received</th>
              </tr>
            </thead>
            <tbody>
              {(opportunities.data ?? []).map((o) => (
                <tr key={o.id} className="border-t border-line/60">
                  <td className="py-2 pr-2">
                    <input
                      type="checkbox"
                      checked={selected.includes(o.id)}
                      disabled={!selected.includes(o.id) && selected.length >= 5}
                      onChange={() => toggle(o.id)}
                      aria-label={`compare ${o.title}`}
                    />
                  </td>
                  <td className="max-w-64 py-2 pr-2">
                    <Link href={`/opportunities/${o.id}`} className="hover:text-accent">
                      {truncate(o.title, 52)}
                    </Link>
                    <div className="flex gap-1 pt-0.5">
                      <Badge>{o.source}</Badge>
                      {o.status !== "new" && <Badge tone="accent">{o.status}</Badge>}
                      {o.possible_duplicate_of && <Badge tone="warn">possible dup</Badge>}
                    </div>
                  </td>
                  <td className="py-2 pr-2 text-ink-dim">{o.company_name ?? "—"}</td>
                  <td className="py-2 pr-2 text-xs text-ink-dim">{o.remote_policy.replaceAll("_", " ")}</td>
                  <td className="py-2 pr-2">
                    <Badge tone={(o.score?.overall ?? 0) >= 80 ? "good" : (o.score?.overall ?? 0) >= 65 ? "accent" : "neutral"}>
                      {recommendationLabel(o.score?.recommendation)}
                    </Badge>
                  </td>
                  <td className="py-2 text-right">
                    <ScoreRing score={o.score?.overall} size="text-base" />
                  </td>
                  <td className="py-2 text-right text-xs text-ink-dim">{timeAgo(o.received_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export default function OpportunitiesPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <OpportunitiesPageInner />
    </Suspense>
  );
}
