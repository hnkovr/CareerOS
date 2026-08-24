"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Card, Empty, ErrorBox, ScoreRing, Spinner } from "@/components/ui";

type Kind = "employment" | "freelance";

export default function PipelinePage() {
  const [kind, setKind] = useState<Kind>("employment");
  const queryClient = useQueryClient();

  const board = useQuery({
    queryKey: ["pipeline-board", kind],
    queryFn: async () => unwrap(await api.GET("/api/pipeline/board", { params: { query: { kind } } })),
  });

  const move = useMutation({
    mutationFn: async ({ id, stage }: { id: string; stage: string }) => {
      const body: Schemas["ApplicationUpdate"] = {
        stage: stage as Schemas["ApplicationUpdate"]["stage"],
        clear_follow_up: false,
      };
      return unwrap(
        await api.PATCH("/api/pipeline/applications/{application_id}", {
          params: { path: { application_id: id } },
          body,
        }),
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pipeline-board"] }),
  });

  const stages = board.data?.stages ?? [];
  const columns = (board.data?.columns ?? []).filter(
    (c) => (c.applications ?? []).length > 0 || !["rejected", "archived", "lost"].includes(c.stage),
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Pipeline</h1>
        <div className="flex gap-1.5">
          {(["employment", "freelance"] as Kind[]).map((k) => (
            <button key={k} className={`btn ${k === kind ? "btn-primary" : ""}`} onClick={() => setKind(k)}>
              {k}
            </button>
          ))}
        </div>
      </div>
      {move.isError && <ErrorBox error={move.error} />}

      {board.isPending ? (
        <Spinner />
      ) : board.isError ? (
        <ErrorBox error={board.error} />
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4">
          {columns.map((col) => (
            <div key={col.stage} className="w-64 shrink-0">
              <div className="mb-2 flex items-center justify-between px-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-ink-dim">
                  {col.stage.replaceAll("_", " ")}
                </span>
                <span className="text-xs tabular-nums text-ink-dim">{(col.applications ?? []).length}</span>
              </div>
              <div className="space-y-2">
                {(col.applications ?? []).map((a) => (
                  <div key={a.id} className="card p-3">
                    <Link href={`/pipeline/${a.id}`} className="block text-sm font-medium hover:text-accent">
                      {a.opportunity_title}
                    </Link>
                    <p className="pb-1.5 text-xs text-ink-dim">{a.company_name ?? "—"}</p>
                    <div className="flex items-center justify-between gap-2">
                      <ScoreRing score={a.score_overall} size="text-sm" />
                      <select
                        className="input w-auto px-1.5 py-0.5 text-[11px]"
                        value={a.stage}
                        onChange={(e) => move.mutate({ id: a.id, stage: e.target.value })}
                      >
                        {stages.map((s) => (
                          <option key={s} value={s}>
                            {s.replaceAll("_", " ")}
                          </option>
                        ))}
                      </select>
                    </div>
                    {a.next_follow_up_at && (
                      <p className="pt-1.5 text-[11px] text-warn">follow up {timeAgo(a.next_follow_up_at).replace(" ago", "")}</p>
                    )}
                  </div>
                ))}
                {(col.applications ?? []).length === 0 && (
                  <div className="rounded-xl border border-dashed border-line p-3 text-center text-xs text-ink-dim">—</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {!board.isPending && (board.data?.columns ?? []).every((c) => (c.applications ?? []).length === 0) && (
        <Card>
          <Empty>
            Nothing in the {kind} pipeline. Open an{" "}
            <Link href="/opportunities" className="text-accent">
              opportunity
            </Link>{" "}
            and press “Add to pipeline”.
          </Empty>
        </Card>
      )}
    </div>
  );
}
