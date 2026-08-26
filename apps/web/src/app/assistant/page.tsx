"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, unwrap, type AskResponse } from "@/lib/api";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const EXAMPLES = [
  "Which of my achievements best prove dbt and Dagster, and what is missing?",
  "What are my three highest-scoring new opportunities and why?",
  "Which applications need a follow-up, and what should I say?",
  "Which profile needs attention first, and what exactly is out of sync?",
];

function Ref({ id }: { id: string }) {
  const isEntity = UUID_RE.test(id);
  return isEntity ? (
    <Link href={`/opportunities/${id}`} className="rounded bg-panel-2 px-1 font-mono text-[10px] text-accent hover:underline">
      {id.slice(0, 8)}…
    </Link>
  ) : (
    <span className="rounded bg-panel-2 px-1 font-mono text-[10px] text-accent">{id}</span>
  );
}

function AnswerCard({ q, r }: { q: string; r: AskResponse }) {
  return (
    <Card
      title={q}
      action={
        <span className="text-[11px] text-ink-dim">
          {r.provider}/{r.model} · {r.turns} turn{r.turns === 1 ? "" : "s"}
        </span>
      }
    >
      <div className="space-y-3 text-sm">
        {r.guarded ? (
          <div className="rounded-lg border border-bad/40 bg-panel-2 p-3">
            <div className="flex items-center gap-2">
              <Badge tone="bad">withheld</Badge>
              <span className="text-xs text-ink-dim">the provenance guard rejected the model&apos;s answer</span>
            </div>
            <ul className="list-inside list-disc pt-1 text-xs text-ink-dim">
              {(r.provenance_problems ?? []).map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="whitespace-pre-wrap">{r.answer}</p>
        )}
        {r.suggested_next_action && (
          <p className="text-xs">
            <span className="label">Suggested next step (yours to take)</span>
            {r.suggested_next_action}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-1">
          <Badge tone={r.confidence === "high" ? "good" : r.confidence === "low" ? "warn" : "neutral"}>
            {r.confidence}
          </Badge>
          {(r.derived_from ?? []).map((id) => (
            <Ref key={id} id={id} />
          ))}
        </div>
        {(r.tools_used ?? []).length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer text-ink-dim">
              What it looked at — {(r.tools_used ?? []).length} tool call{(r.tools_used ?? []).length === 1 ? "" : "s"}
            </summary>
            <table className="mt-1 w-full">
              <tbody>
                {(r.tools_used ?? []).map((s) => (
                  <tr key={s.step} className="border-t border-line/60 align-top">
                    <td className="py-1 pr-2 font-mono text-ink-dim">{s.step}</td>
                    <td className="py-1 pr-2 font-mono">{s.tool}</td>
                    <td className="py-1 pr-2 font-mono text-ink-dim">{JSON.stringify(s.arguments)}</td>
                    <td className="py-1 pr-2">
                      <Badge tone={s.ok ? "good" : "bad"}>{s.ok ? "ok" : "error"}</Badge>
                    </td>
                    <td className="max-w-md truncate py-1 text-ink-dim">{s.result_preview}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </div>
    </Card>
  );
}

function AssistantInner() {
  const params = useSearchParams();
  const [question, setQuestion] = useState("");
  const [opportunityId, setOpportunityId] = useState(params.get("opportunity") ?? "");
  const [history, setHistory] = useState<Array<{ q: string; r: AskResponse }>>([]);

  const tools = useQuery({
    queryKey: ["assistant-tools"],
    queryFn: async () => unwrap(await api.GET("/api/assistant/tools")),
  });
  const ask = useMutation({
    mutationFn: async (q: string) =>
      unwrap(
        await api.POST("/api/assistant/ask", {
          body: {
            question: q,
            opportunity_id: UUID_RE.test(opportunityId) ? opportunityId : null,
            application_id: null,
            provider: null,
            max_steps: 8,
          },
        }),
      ),
    onSuccess: (r, q) => {
      setHistory((h) => [{ q, r }, ...h]);
      setQuestion("");
    },
  });
  const canAsk = question.trim().length >= 3 && !ask.isPending;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold">Assistant</h1>
        <p className="text-sm text-ink-dim">
          Asks your vault and your pipeline through read-only tools, cites the facts it used, and is silenced when it
          states something it never saw. It never applies, sends or edits.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Ask" className="lg:col-span-2">
          <div className="space-y-3">
            <textarea
              className="input h-28"
              placeholder="e.g. Which of my achievements prove ClickHouse at scale?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canAsk) ask.mutate(question.trim());
              }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="input w-80 font-mono text-xs"
                placeholder="opportunity id as context (optional)"
                value={opportunityId}
                onChange={(e) => setOpportunityId(e.target.value.trim())}
              />
              <button className="btn btn-primary" disabled={!canAsk} onClick={() => ask.mutate(question.trim())}>
                {ask.isPending ? "Thinking…" : "Ask (⌘↵)"}
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="btn text-xs" onClick={() => setQuestion(ex)} disabled={ask.isPending}>
                  {ex}
                </button>
              ))}
            </div>
            {ask.isError && <ErrorBox error={ask.error} />}
          </div>
        </Card>

        <Card title="Tools it may call">
          {tools.isPending ? (
            <Spinner />
          ) : tools.isError ? (
            <ErrorBox error={tools.error} />
          ) : (
            <ul className="space-y-1.5 text-xs">
              {(tools.data ?? []).map((t) => (
                <li key={t.name}>
                  <span className="font-mono text-accent">{t.name}</span>
                  <Badge>{t.read_only === false ? "writes" : "read-only"}</Badge>
                  <div className="text-ink-dim">{t.description}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-4 lg:col-span-3">
          {history.length === 0 ? (
            <Card>
              <Empty>No questions yet. Every answer shows what it looked at and which facts it rests on.</Empty>
            </Card>
          ) : (
            history.map((h, i) => <AnswerCard key={`${i}-${h.r.ai_run_id ?? ""}`} q={h.q} r={h.r} />)
          )}
        </div>
      </div>
    </div>
  );
}

export default function AssistantPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <AssistantInner />
    </Suspense>
  );
}
