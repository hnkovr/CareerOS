"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, unwrap } from "@/lib/api";
import { recommendationLabel, timeAgo, truncate } from "@/lib/format";
import { Badge, Card, Empty, ScoreRing, Spinner, severityTone } from "@/components/ui";

export default function DashboardPage() {
  const vault = useQuery({
    queryKey: ["vault-status"],
    queryFn: async () => unwrap(await api.GET("/api/vault/status")),
  });
  const health = useQuery({
    queryKey: ["profiles-health"],
    queryFn: async () => unwrap(await api.GET("/api/profiles/health")),
  });
  const opportunities = useQuery({
    queryKey: ["opportunities", "top"],
    queryFn: async () => unwrap(await api.GET("/api/opportunities", { params: { query: { limit: 50 } } })),
  });
  const artifacts = useQuery({
    queryKey: ["cv-artifacts", "recent"],
    queryFn: async () => unwrap(await api.GET("/api/cv/artifacts", { params: { query: { limit: 5 } } })),
  });
  const inboxStats = useQuery({
    queryKey: ["inbox", "stats"],
    queryFn: async () => unwrap(await api.GET("/api/inbox/stats")),
  });
  const followUps = useQuery({
    queryKey: ["pipeline-follow-ups"],
    queryFn: async () =>
      unwrap(await api.GET("/api/pipeline/follow-ups", { params: { query: { within_days: 7 } } })),
  });
  const pendingSuggestions = useQuery({
    queryKey: ["suggestions", "pending"],
    queryFn: async () =>
      unwrap(await api.GET("/api/ai/suggestions", { params: { query: { state: "suggested", limit: 5 } } })),
  });
  const runs = useQuery({
    queryKey: ["ai-runs", "recent"],
    queryFn: async () => unwrap(await api.GET("/api/ai/runs", { params: { query: { limit: 5 } } })),
  });

  const newOpps = (opportunities.data ?? []).filter((o) => o.status === "new");
  const top = [...(opportunities.data ?? [])]
    .filter((o) => o.score)
    .sort((a, b) => (b.score?.overall ?? 0) - (a.score?.overall ?? 0))
    .slice(0, 5);

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card
        title="Career Vault"
        action={<Link className="btn" href="/vault">Open</Link>}
      >
        {vault.isPending ? (
          <Spinner />
        ) : vault.isError ? (
          <Empty>API unreachable — is the backend running?</Empty>
        ) : (
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2">
              {vault.data.valid ? <Badge tone="good">valid</Badge> : <Badge tone="bad">{vault.data.errors} errors</Badge>}
              {vault.data.warnings > 0 && <Badge tone="warn">{vault.data.warnings} warnings</Badge>}
              {vault.data.dirty && <Badge tone="warn">uncommitted changes</Badge>}
            </div>
            <p className="text-ink-dim">
              {vault.data.owner ?? "no owner"} · {Object.values(vault.data.counts).reduce((a, b) => a + b, 0)} items ·{" "}
              {vault.data.head_sha ? `HEAD ${vault.data.head_sha.slice(0, 8)}` : "not a git repo"}
            </p>
            <p className="text-xs text-ink-dim">
              positioning: {vault.data.default_positioning} · CV: {vault.data.default_cv_variant}
            </p>
          </div>
        )}
      </Card>

      <Card title="Profile Health" action={<Link className="btn" href="/profiles">Audit</Link>}>
        {health.isPending ? (
          <Spinner />
        ) : health.isError || !health.data ? (
          <Empty>—</Empty>
        ) : (
          <ul className="space-y-2 text-sm">
            {health.data.map((h) => (
              <li key={h.platform} className="flex items-center justify-between">
                <span className="capitalize">{h.platform}</span>
                <span className="flex items-center gap-2">
                  {h.top_severity && <Badge tone={severityTone(h.top_severity)}>{h.open_findings} open</Badge>}
                  {h.health_score == null ? (
                    <span className="text-xs text-ink-dim">no snapshot</span>
                  ) : (
                    <ScoreRing score={h.health_score} size="text-base" />
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="New Opportunities" action={<Link className="btn" href="/opportunities?new=1">Add</Link>}>
        {opportunities.isPending ? (
          <Spinner />
        ) : newOpps.length === 0 ? (
          <Empty>Nothing new. Paste a JD to triage it.</Empty>
        ) : (
          <ul className="space-y-2 text-sm">
            {newOpps.slice(0, 5).map((o) => (
              <li key={o.id} className="flex items-center justify-between gap-2">
                <Link href={`/opportunities/${o.id}`} className="truncate hover:text-accent">
                  {truncate(o.title, 46)}
                </Link>
                <ScoreRing score={o.score?.overall} size="text-sm" />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Top Matches" className="xl:col-span-2">
        {top.length === 0 ? (
          <Empty>No scored opportunities yet.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-dim">
                <th className="pb-2">Role</th>
                <th className="pb-2">Company</th>
                <th className="pb-2">Recommendation</th>
                <th className="pb-2 text-right">Score</th>
              </tr>
            </thead>
            <tbody>
              {top.map((o) => (
                <tr key={o.id} className="border-t border-line/60">
                  <td className="py-2 pr-2">
                    <Link href={`/opportunities/${o.id}`} className="hover:text-accent">
                      {truncate(o.title, 48)}
                    </Link>
                  </td>
                  <td className="py-2 pr-2 text-ink-dim">{o.company_name ?? "—"}</td>
                  <td className="py-2 pr-2">
                    <Badge tone={(o.score?.overall ?? 0) >= 80 ? "good" : (o.score?.overall ?? 0) >= 65 ? "accent" : "neutral"}>
                      {recommendationLabel(o.score?.recommendation)}
                    </Badge>
                  </td>
                  <td className="py-2 text-right">
                    <ScoreRing score={o.score?.overall} size="text-base" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="CV Versions" action={<Link className="btn" href="/cv">Generate</Link>}>
        {artifacts.isPending ? (
          <Spinner />
        ) : (artifacts.data ?? []).length === 0 ? (
          <Empty>No CVs generated yet.</Empty>
        ) : (
          <ul className="space-y-2 text-sm">
            {(artifacts.data ?? []).map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-2">
                <Link href={`/cv/${a.id}`} className="hover:text-accent">
                  {a.variant_id}
                </Link>
                <span className="flex items-center gap-2 text-xs text-ink-dim">
                  {a.ai_used && <Badge tone="violet">AI</Badge>}
                  {timeAgo(a.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Career Inbox" action={<Link className="btn" href="/inbox">Open</Link>}>
        {inboxStats.isPending ? (
          <Spinner />
        ) : inboxStats.isError || !inboxStats.data ? (
          <Empty>—</Empty>
        ) : inboxStats.data.total === 0 ? (
          <Empty>No messages yet — paste an email to triage it.</Empty>
        ) : (
          <div className="space-y-2 text-sm">
            <div className="flex gap-2">
              <Badge tone={inboxStats.data.needs_attention > 0 ? "warn" : "good"}>
                {inboxStats.data.needs_attention} need attention
              </Badge>
              <Badge>{inboxStats.data.unread} unread</Badge>
            </div>
            <ul className="space-y-1 text-xs text-ink-dim">
              {Object.entries(inboxStats.data.by_class)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([cls, n]) => (
                  <li key={cls} className="flex justify-between">
                    <span>{cls.replaceAll("_", " ")}</span>
                    <span className="tabular-nums">{n}</span>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </Card>

      <Card title="Follow-ups" action={<Link className="btn" href="/pipeline">Pipeline</Link>}>
        {followUps.isPending ? (
          <Spinner />
        ) : (followUps.data ?? []).length === 0 ? (
          <Empty>Nothing due this week.</Empty>
        ) : (
          <ul className="space-y-2 text-sm">
            {(followUps.data ?? []).map((f) => (
              <li key={f.application.id} className="flex items-center justify-between gap-2">
                <Link href={`/pipeline/${f.application.id}`} className="truncate hover:text-accent">
                  {truncate(f.application.opportunity_title, 40)}
                </Link>
                <Badge tone={f.overdue ? "bad" : "warn"}>{f.overdue ? "overdue" : timeAgo(f.due_at).replace(" ago", "")}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="AI Suggestions" action={<Link className="btn" href="/suggestions">Review</Link>}>
        {pendingSuggestions.isPending ? (
          <Spinner />
        ) : (pendingSuggestions.data ?? []).length === 0 ? (
          <Empty>Nothing awaiting approval.</Empty>
        ) : (
          <ul className="space-y-2 text-sm">
            {(pendingSuggestions.data ?? []).map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-2">
                <Link href="/suggestions" className="truncate hover:text-accent">
                  {truncate(s.title, 42)}
                </Link>
                <Badge tone="accent">{s.target_type}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Recent AI Runs" className="md:col-span-2 xl:col-span-1">
        {runs.isPending ? (
          <Spinner />
        ) : (runs.data ?? []).length === 0 ? (
          <Empty>No AI runs yet.</Empty>
        ) : (
          <ul className="space-y-1.5 text-xs">
            {(runs.data ?? []).map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2">
                <span className="truncate text-ink-dim">
                  <span className="text-ink">{r.prompt_id}</span> · {r.provider}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {r.valid ? <Badge tone="good">ok</Badge> : <Badge tone="bad">{r.status}</Badge>}
                  <span className="text-ink-dim">{timeAgo(r.created_at)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
