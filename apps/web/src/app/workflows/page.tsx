"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, unwrap, type WorkflowRunOut } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Badge, Card, copyToClipboard, Empty, ErrorBox, Spinner } from "@/components/ui";

type Tone = "good" | "warn" | "bad" | "accent" | "violet" | "neutral";

const RUN_TONE: Record<string, Tone> = {
  running: "accent",
  waiting_approval: "warn",
  completed: "good",
  failed: "bad",
  cancelled: "neutral",
};
const STEP_TONE: Record<string, Tone> = {
  pending: "neutral",
  running: "accent",
  done: "good",
  skipped: "neutral",
  waiting: "warn",
  rejected: "bad",
  failed: "bad",
};
const KINDS = ["apply", "follow_up"] as const;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function targetHref(run: WorkflowRunOut): string {
  return run.target_type === "application" ? `/pipeline/${run.target_ref}` : `/opportunities/${run.target_ref}`;
}

function RunCard({ run, initiallyOpen }: { run: WorkflowRunOut; initiallyOpen: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(initiallyOpen);
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["workflows"] });
    queryClient.invalidateQueries({ queryKey: ["suggestions"] });
  };
  const decide = useMutation({
    mutationFn: async (decision: "approve" | "reject") =>
      unwrap(
        await api.POST("/api/workflows/{run_id}/decision", {
          params: { path: { run_id: run.id } },
          body: { decision, note: note || null },
        }),
      ),
    onSuccess: invalidate,
  });
  const cancel = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/workflows/{run_id}/cancel", { params: { path: { run_id: run.id } } })),
    onSuccess: invalidate,
  });
  const done = run.steps.filter((s) => s.status === "done" || s.status === "skipped").length;
  const message = typeof run.context.message === "string" ? run.context.message : null;
  const subject = typeof run.context.subject === "string" ? run.context.subject : null;
  const title = typeof run.context.title === "string" ? run.context.title : run.target_ref;

  return (
    <Card
      title={`${run.title} — ${title}`}
      action={
        <div className="flex items-center gap-2">
          <Badge tone={RUN_TONE[run.state] ?? "neutral"}>{run.state.replaceAll("_", " ")}</Badge>
          <span className="text-[11px] text-ink-dim">
            {done}/{run.steps.length} · {timeAgo(run.created_at)}
          </span>
          <button className="btn" onClick={() => setOpen((v) => !v)}>
            {open ? "Hide" : "Steps"}
          </button>
        </div>
      }
    >
      <div className="space-y-3 text-sm">
        <p className="text-xs text-ink-dim">
          {run.target_type}:{" "}
          <Link href={targetHref(run)} className="font-mono text-accent hover:underline">
            {run.target_ref.slice(0, 8)}…
          </Link>
          {run.error && <span className="text-bad"> · {run.error}</span>}
        </p>
        {open && (
          <ol className="space-y-1.5">
            {run.steps.map((s, i) => (
              <li key={s.name} className={`flex items-start gap-2 text-xs ${i === run.current_step ? "font-medium" : ""}`}>
                <Badge tone={STEP_TONE[s.status] ?? "neutral"}>{s.status}</Badge>
                <span className="font-mono">{s.kind === "approval" ? "⏸ " : ""}{s.name}</span>
                <span className="text-ink-dim">{s.summary ?? s.description}</span>
                {s.error && <span className="text-bad">{s.error}</span>}
              </li>
            ))}
          </ol>
        )}
        {run.state === "waiting_approval" && (
          <div className="space-y-2 rounded-lg border border-warn/40 bg-panel-2 p-3">
            <div className="label">Waiting for your approval — nothing has been written yet</div>
            {subject && <p className="text-xs font-medium">{subject}</p>}
            {message && <pre className="whitespace-pre-wrap rounded bg-surface p-2 text-xs">{message}</pre>}
            <input className="input" placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="flex flex-wrap gap-1.5">
              <button className="btn btn-primary" onClick={() => decide.mutate("approve")} disabled={decide.isPending}>
                Approve & continue
              </button>
              <button className="btn" onClick={() => decide.mutate("reject")} disabled={decide.isPending}>
                Reject
              </button>
              {message && (
                <button className="btn" onClick={async () => setCopied(await copyToClipboard(message))}>
                  {copied ? "Copied ✓" : "Copy message"}
                </button>
              )}
            </div>
            <p className="text-[11px] text-ink-dim">Approving records the package in the pipeline; sending is still yours.</p>
            {decide.isError && <ErrorBox error={decide.error} />}
          </div>
        )}
        {run.state === "running" && (
          <button className="btn" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            Cancel
          </button>
        )}
        {run.state === "completed" && message && (
          <details className="text-xs">
            <summary className="cursor-pointer text-ink-dim">Approved message</summary>
            <pre className="mt-1 whitespace-pre-wrap rounded bg-surface p-2">{message}</pre>
          </details>
        )}
      </div>
    </Card>
  );
}

function WorkflowsInner() {
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<(typeof KINDS)[number]>(params.get("kind") === "follow_up" ? "follow_up" : "apply");
  const [target, setTarget] = useState(params.get("target") ?? "");
  const [useAi, setUseAi] = useState(true);
  const [lastStarted, setLastStarted] = useState<string | null>(null);

  const definitions = useQuery({
    queryKey: ["workflow-definitions"],
    queryFn: async () => unwrap(await api.GET("/api/workflows/definitions")),
  });
  const runs = useQuery({
    queryKey: ["workflows"],
    queryFn: async () => unwrap(await api.GET("/api/workflows", { params: { query: { limit: 50 } } })),
  });
  const sweep = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/workflows/sweep", { params: { query: { limit: 20 } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["workflows"] }),
  });
  const start = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/workflows", { body: { kind, target_id: target, options: { use_ai: useAi } } })),
    onSuccess: (run) => {
      setLastStarted(run.id);
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
  });
  const canStart = UUID_RE.test(target) && !start.isPending;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold">Workflows</h1>
        <p className="text-sm text-ink-dim">
          Multi-step chains that stop at a gate and wait for you. Nothing enters the pipeline, and nothing is ever
          sent, without your approval.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Start" className="lg:col-span-1">
          <div className="space-y-3">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value as (typeof KINDS)[number])}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <input
              className="input font-mono text-xs"
              placeholder={kind === "apply" ? "opportunity id" : "application id"}
              value={target}
              onChange={(e) => setTarget(e.target.value.trim())}
            />
            <label className="flex items-center gap-2 text-xs text-ink-dim">
              <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
              use AI where configured (analysis, tailored CV, drafts)
            </label>
            <button className="btn btn-primary" disabled={!canStart} onClick={() => start.mutate()}>
              {start.isPending ? "Running…" : "Start"}
            </button>
            {start.isError && <ErrorBox error={start.error} />}
            <div className="border-t border-line pt-3">
              <button className="btn" onClick={() => sweep.mutate()} disabled={sweep.isPending} title="one follow_up run per due/overdue follow-up; each waits for your approval">
                {sweep.isPending ? "Sweeping…" : "Sweep due follow-ups"}
              </button>
              {sweep.isSuccess && <p className="pt-1 text-xs text-ink-dim">started {sweep.data.length} run(s)</p>}
              {sweep.isError && <ErrorBox error={sweep.error} />}
            </div>
          </div>
        </Card>

        <Card title="Definitions" className="lg:col-span-2">
          {definitions.isPending ? (
            <Spinner />
          ) : definitions.isError ? (
            <ErrorBox error={definitions.error} />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {(definitions.data ?? []).map((d) => (
                <div key={d.kind} className="rounded-lg border border-line bg-panel-2 p-3 text-xs">
                  <div className="font-medium">{d.title}</div>
                  <p className="text-ink-dim">{d.description}</p>
                  <ol className="mt-1 space-y-0.5">
                    {d.steps.map((s) => (
                      <li key={s.name} className="font-mono">
                        {s.kind === "approval" ? "⏸ " : "· "}
                        {s.name}
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div className="space-y-4 lg:col-span-3">
          {runs.isPending ? (
            <Spinner />
          ) : runs.isError ? (
            <ErrorBox error={runs.error} />
          ) : (runs.data ?? []).length === 0 ? (
            <Card>
              <Empty>No workflow runs yet — start one from an opportunity or an application.</Empty>
            </Card>
          ) : (
            (runs.data ?? []).map((run) => (
              <RunCard key={run.id} run={run} initiallyOpen={run.id === lastStarted || run.state === "waiting_approval"} />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export default function WorkflowsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <WorkflowsInner />
    </Suspense>
  );
}
