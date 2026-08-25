"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap, type CompareOut } from "@/lib/api";
import { recommendationLabel } from "@/lib/format";
import { Card, ErrorBox, ScoreRing } from "@/components/ui";

/** §31 comparison mode: deterministic rows side by side, optionally with an AI-ranked recommendation. */
export function ComparePanel({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [result, setResult] = useState<CompareOut | null>(null);
  const compare = useMutation({
    mutationFn: async (useAi: boolean) =>
      unwrap(await api.POST("/api/opportunities/compare", { body: { ids, use_ai: useAi, provider: null } })),
    onSuccess: setResult,
  });
  const dims = result ? result.dimension_names.filter((d) => d !== "overall_fit") : [];
  const rows = result ? [...result.rows].sort((a, b) => result.ranked.indexOf(a.id) - result.ranked.indexOf(b.id)) : [];
  const rankOf = (id: string) => result?.ranking?.find((r) => r.opportunity_id === id)?.rank;
  const tooFew = ids.length < 2;

  return (
    <Card
      title={`Compare ${ids.length} opportunities`}
      action={
        <div className="flex gap-1.5">
          <button className="btn" onClick={() => compare.mutate(false)} disabled={compare.isPending || tooFew}>
            Compare
          </button>
          <button className="btn btn-primary" onClick={() => compare.mutate(true)} disabled={compare.isPending || tooFew}>
            {compare.isPending ? "Ranking…" : "Rank with AI"}
          </button>
          <button className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      }
    >
      {tooFew && <p className="text-xs text-ink-dim">Tick 2–5 opportunities in the list, then Compare.</p>}
      {compare.isError && <ErrorBox error={compare.error} />}
      {result && (
        <div className="space-y-3">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left uppercase tracking-wide text-ink-dim">
                  <th className="pb-2 pr-2">Opportunity</th>
                  <th className="pb-2 pr-2 text-right">Score</th>
                  {result.ranking && <th className="pb-2 pr-2 text-right">AI rank</th>}
                  {dims.map((d) => (
                    <th key={d} className="pb-2 pr-2 text-right">
                      {d.replaceAll("_", " ")}
                    </th>
                  ))}
                  <th className="pb-2 pr-2">Comp</th>
                  <th className="pb-2">Remote</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-line/60">
                    <td className="py-1.5 pr-2">
                      <Link href={`/opportunities/${r.id}`} className="hover:text-accent">
                        {r.title}
                      </Link>
                      <div className="text-ink-dim">
                        {r.company_name ?? "—"} · {recommendationLabel(r.recommendation)}
                      </div>
                    </td>
                    <td className="py-1.5 pr-2 text-right">
                      <ScoreRing score={r.overall} size="text-base" />
                    </td>
                    {result.ranking && <td className="py-1.5 pr-2 text-right font-mono">#{rankOf(r.id) ?? "—"}</td>}
                    {dims.map((d) => (
                      <td key={d} className="py-1.5 pr-2 text-right font-mono">
                        {r.dimensions[d] ?? "—"}
                      </td>
                    ))}
                    <td className="py-1.5 pr-2">{r.compensation ?? "—"}</td>
                    <td className="py-1.5">{r.remote_policy.replaceAll("_", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.ranking_note && <p className="text-xs text-warn">{result.ranking_note}</p>}
          {result.ranking && (
            <div className="space-y-2 border-t border-line pt-3 text-sm">
              <p className="font-medium">{result.recommendation}</p>
              <ol className="list-inside list-decimal space-y-1 text-xs">
                {[...result.ranking]
                  .sort((a, b) => a.rank - b.rank)
                  .map((r) => {
                    const row = result.rows.find((x) => x.id === r.opportunity_id);
                    return (
                      <li key={r.opportunity_id}>
                        <span className="font-medium">{row?.title ?? r.opportunity_id}</span> —{" "}
                        <span className="text-ink-dim">{r.rationale}</span>
                      </li>
                    );
                  })}
              </ol>
              {(result.tradeoffs ?? []).length > 0 && (
                <ul className="list-inside list-disc text-xs text-ink-dim">
                  {(result.tradeoffs ?? []).map((t, i) => (
                    <li key={i}>{t}</li>
                  ))}
                </ul>
              )}
              <p className="text-[11px] text-ink-dim">The AI ranks the deterministic rows; it never changes the scores.</p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
