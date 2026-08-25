"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "accent" | "violet" | "neutral"> = {
  evidenced: "good",
  claimed: "warn",
  known: "neutral",
  missing: "bad",
  worth_learning: "violet",
};

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export default function InsightsPage() {
  const [windowDays, setWindowDays] = useState(90);
  const market = useQuery({
    queryKey: ["insights-market", windowDays],
    queryFn: async () => unwrap(await api.GET("/api/insights/market", { params: { query: { window_days: windowDays } } })),
  });
  const gap = useQuery({
    queryKey: ["insights-skills-gap"],
    queryFn: async () => unwrap(await api.GET("/api/insights/skills-gap")),
  });
  const funnel = useQuery({
    queryKey: ["insights-funnel"],
    queryFn: async () => unwrap(await api.GET("/api/insights/funnel")),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Career insights</h1>
        <label className="flex items-center gap-2 text-xs text-ink-dim">
          window
          <select className="input w-auto" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
            {[30, 90, 180, 365].map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </label>
      </div>

      <Card title="Funnel">
        {funnel.isPending ? (
          <Spinner />
        ) : funnel.isError ? (
          <ErrorBox error={funnel.error} />
        ) : funnel.data.applications_total === 0 ? (
          <Empty>No applications yet — add one from an opportunity.</Empty>
        ) : (
          <div className="grid gap-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
            {[
              ["Applications", String(funnel.data.applications_total)],
              ["Active", String(funnel.data.active)],
              ["Response rate", pct(funnel.data.response_rate)],
              ["Interview rate", pct(funnel.data.interview_rate)],
              ["Offer rate", pct(funnel.data.offer_rate)],
              ["Days to reply (median)", funnel.data.median_days_to_first_response == null ? "—" : String(funnel.data.median_days_to_first_response)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line p-2.5">
                <div className="text-[11px] uppercase tracking-wide text-ink-dim">{k}</div>
                <div className="text-xl font-bold tabular-nums">{v}</div>
              </div>
            ))}
            <div className="col-span-full flex flex-wrap gap-1.5">
              {Object.entries(funnel.data.by_stage).map(([stage, n]) => (
                <Badge key={stage}>
                  {stage.replaceAll("_", " ")}: {n}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={market.data ? `Market — ${market.data.sample_size} opportunities, last ${market.data.window_days}d` : "Market"}>
          {market.isPending ? (
            <Spinner />
          ) : market.isError ? (
            <ErrorBox error={market.error} />
          ) : market.data.sample_size === 0 ? (
            <Empty>No opportunities in this window.</Empty>
          ) : (
            <div className="space-y-3 text-sm">
              <p className="text-[11px] text-ink-dim">{market.data.disclaimer}</p>
              <div>
                <div className="label">Technology demand</div>
                <ul className="space-y-1">
                  {market.data.technologies.slice(0, 12).map((t) => (
                    <li key={t.technology} className="flex items-center gap-2">
                      <span className="w-28 truncate">{t.technology}</span>
                      <div className="h-1.5 flex-1 rounded bg-panel-2">
                        <div className="h-1.5 rounded bg-accent" style={{ width: `${Math.max(3, t.share * 100)}%` }} />
                      </div>
                      <span className="w-20 text-right text-xs tabular-nums text-ink-dim">
                        {t.count} · {Math.round(t.share * 100)}%
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              {market.data.combos.length > 0 && (
                <div>
                  <div className="label">Common combinations</div>
                  <div className="flex flex-wrap gap-1.5">
                    {market.data.combos.map((c) => (
                      <Badge key={c.technologies.join("+")}>
                        {c.technologies.join(" + ")} ×{c.count}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
              <div className="grid gap-2 text-xs sm:grid-cols-2">
                <div>
                  <div className="label">Remote policy</div>
                  {Object.entries(market.data.remote_policy).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span>{k.replaceAll("_", " ")}</span>
                      <span className="tabular-nums">{v}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="label">Compensation (observed)</div>
                  {market.data.compensation.length === 0 ? (
                    <p className="text-ink-dim">too few stated</p>
                  ) : (
                    market.data.compensation.map((c) => (
                      <div key={`${c.kind}-${c.currency}`} className="flex justify-between">
                        <span>
                          {c.kind} {c.currency} (n={c.n})
                        </span>
                        <span className="tabular-nums">
                          {c.p25.toLocaleString()} / {c.median.toLocaleString()} / {c.p75.toLocaleString()}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
        </Card>

        <Card title="Skills gap — I know it vs I can prove it">
          {gap.isPending ? (
            <Spinner />
          ) : gap.isError ? (
            <ErrorBox error={gap.error} />
          ) : (
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(gap.data.counts).map(([k, v]) => (
                  <Badge key={k} tone={STATUS_TONE[k] ?? "neutral"}>
                    {k.replaceAll("_", " ")}: {v}
                  </Badge>
                ))}
              </div>
              <ul className="max-h-80 space-y-1 overflow-y-auto">
                {gap.data.items
                  .filter((i) => i.demand > 0 || i.status === "claimed")
                  .slice(0, 30)
                  .map((i) => (
                    <li key={i.technology} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1 hover:bg-panel-2">
                      <span className="flex items-center gap-2">
                        <Badge tone={STATUS_TONE[i.status] ?? "neutral"}>{i.status.replaceAll("_", " ")}</Badge>
                        <span>{i.technology}</span>
                        {(i.evidence ?? []).length > 0 && (
                          <span className="font-mono text-[10px] text-accent-2">{(i.evidence ?? []).slice(0, 2).join(", ")}</span>
                        )}
                      </span>
                      <span className="text-xs tabular-nums text-ink-dim">demand {i.demand}</span>
                    </li>
                  ))}
              </ul>
              {gap.data.portfolio.length > 0 && (
                <div>
                  <div className="label">Portfolio planner — proof with the best ROI</div>
                  <ol className="list-inside list-decimal space-y-1.5">
                    {gap.data.portfolio.map((p) => (
                      <li key={p.technology}>
                        <span className="font-medium">{p.suggested_proof}</span>{" "}
                        <Badge tone={p.estimated_roi === "high" ? "good" : p.estimated_roi === "medium" ? "warn" : "neutral"}>
                          ROI {p.estimated_roi}
                        </Badge>
                        <span className="block text-xs text-ink-dim">
                          {p.why}
                          {p.project_id && (
                            <>
                              {" · "}
                              <Link href={`/vault/projects/${p.project_id}`} className="text-accent">
                                {p.project_id}
                              </Link>
                            </>
                          )}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
