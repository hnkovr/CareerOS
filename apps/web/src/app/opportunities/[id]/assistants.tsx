"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type InterviewPrepOut, type NegotiationOut } from "@/lib/api";
import { Badge, Card, Empty, ErrorBox, KeyValue } from "@/components/ui";

type Tone = "good" | "warn" | "bad" | "accent" | "neutral";

function TechList({ label, items, tone }: { label: string; items: string[]; tone: Tone }) {
  return (
    <div>
      <div className="label">{label}</div>
      {items.length === 0 ? (
        <span className="text-xs text-ink-dim">none</span>
      ) : (
        <div className="flex flex-wrap gap-1">
          {items.map((t) => (
            <Badge key={t} tone={tone}>
              {t}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

function List({ label, items, tone = "text-ink", ordered = false }: { label: string; items: string[]; tone?: string; ordered?: boolean }) {
  if (items.length === 0) return null;
  const Tag = ordered ? "ol" : "ul";
  return (
    <div>
      <div className="label">{label}</div>
      <Tag className={`list-inside ${ordered ? "list-decimal" : "list-disc"} space-y-0.5 text-xs ${tone}`}>
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </Tag>
    </div>
  );
}

function Provenance({ ids }: { ids: string[] }) {
  if (ids.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 pt-0.5">
      {ids.map((id) => (
        <span key={id} className="rounded bg-panel-2 px-1 font-mono text-[10px] text-accent">
          {id}
        </span>
      ))}
    </div>
  );
}

function Rejected({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-lg border border-bad/40 bg-panel-2 p-2">
      <div className="label">Rejected by the provenance guard ({items.length})</div>
      <ul className="list-inside list-disc space-y-0.5 text-[11px] text-ink-dim">
        {items.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </div>
  );
}

export function InterviewPrepCard({ id }: { id: string }) {
  const [result, setResult] = useState<InterviewPrepOut | null>(null);
  const run = useMutation({
    mutationFn: async (useAi: boolean) =>
      unwrap(
        await api.POST("/api/opportunities/{opportunity_id}/interview-prep", {
          params: { path: { opportunity_id: id } },
          body: { use_ai: useAi, provider: null },
        }),
      ),
    onSuccess: setResult,
  });
  const frame = result?.frame;
  const plan = result?.plan ?? null;

  return (
    <Card
      title="Interview prep"
      className="lg:col-span-3"
      action={
        <div className="flex gap-1.5">
          <button className="btn" onClick={() => run.mutate(false)} disabled={run.isPending}>
            Evidence map
          </button>
          <button className="btn btn-primary" onClick={() => run.mutate(true)} disabled={run.isPending}>
            {run.isPending ? "Preparing…" : "Prepare with AI"}
          </button>
        </div>
      }
    >
      {run.isError && <ErrorBox error={run.error} />}
      {!frame ? (
        <Empty>
          First what the vault can <em>prove</em> for this posting (stories with fact ids, matched / claimed / missing
          tech), then an AI plan — every story must cite a fact id or it is dropped.
        </Empty>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <Badge tone="accent">{frame.track}</Badge>
            <span className="text-ink-dim">likely stages:</span>
            {frame.stages.map((s) => (
              <Badge key={s}>{s.replaceAll("_", " ")}</Badge>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <TechList label="Provable from the vault" items={frame.matched} tone="good" />
            <TechList label="Claimed only (no story)" items={frame.claimed_only} tone="warn" />
            <TechList label="Missing" items={frame.missing} tone="bad" />
          </div>
          <List label="Expect probing on" items={frame.weak_dimensions ?? []} tone="text-warn" />
          {frame.materials.length > 0 && (
            <div>
              <div className="label">Story materials</div>
              <ul className="space-y-1 text-xs">
                {frame.materials.map((m) => (
                  <li key={m.fact_id}>
                    <span className="font-mono text-accent">{m.fact_id}</span> · {m.title}
                    {m.company ? ` @ ${m.company}` : ""} — <span className="text-ink-dim">{(m.technologies ?? []).join(", ")}</span>
                    {(m.metrics ?? []).length > 0 && <span className="text-ink-dim"> · {(m.metrics ?? []).join(" · ")}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <List label="Ask them" items={frame.questions_to_ask} />
          {plan && result && (
            <div className="space-y-3 border-t border-line pt-3">
              <List label="Focus areas" items={plan.focus_areas ?? []} />
              {(plan.expected_questions ?? []).length > 0 && (
                <div>
                  <div className="label">Expected questions</div>
                  <ul className="space-y-1.5 text-xs">
                    {(plan.expected_questions ?? []).map((q, i) => (
                      <li key={i}>
                        <span className="font-medium">{q.question}</span> <span className="text-ink-dim">— {q.why}</span>
                        <div>{q.answer_outline}</div>
                        <Provenance ids={q.derived_from ?? []} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(plan.stories ?? []).length > 0 && (
                <div>
                  <div className="label">Stories</div>
                  <ul className="space-y-2 text-xs">
                    {(plan.stories ?? []).map((s, i) => (
                      <li key={i} className="rounded-lg border border-line bg-panel-2 p-2">
                        <div className="font-medium">{s.title}</div>
                        <div>
                          <span className="text-ink-dim">Situation:</span> {s.situation}
                        </div>
                        <div>
                          <span className="text-ink-dim">Action:</span> {s.action}
                        </div>
                        <div>
                          <span className="text-ink-dim">Result:</span> {s.result}
                        </div>
                        <Provenance ids={s.derived_from} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <List label="Gaps to prepare" items={plan.gaps_to_prepare ?? []} tone="text-warn" />
              <List label="Questions to ask" items={plan.questions_to_ask ?? []} />
              <List label="Plan" items={plan.plan ?? []} ordered />
              <Rejected items={result.provenance_rejected ?? []} />
              <p className="text-[11px] text-ink-dim">
                {result.provider}/{result.model} · saved to AI suggestions for review
              </p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const POSITION_TONE: Record<string, Tone> = {
  below_floor: "bad",
  below_target: "warn",
  at_target: "good",
  above_target: "good",
  unknown: "neutral",
};

function money(v: number | null | undefined, currency: string): string {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v).toLocaleString("en-US")} ${currency}`;
}

export function NegotiationCard({ id }: { id: string }) {
  const [result, setResult] = useState<NegotiationOut | null>(null);
  const run = useMutation({
    mutationFn: async (useAi: boolean) =>
      unwrap(
        await api.POST("/api/opportunities/{opportunity_id}/negotiation", {
          params: { path: { opportunity_id: id } },
          body: { use_ai: useAi, provider: null },
        }),
      ),
    onSuccess: setResult,
  });
  const frame = result?.frame;
  const plan = result?.plan ?? null;

  return (
    <Card
      title="Negotiation"
      className="lg:col-span-3"
      action={
        <div className="flex gap-1.5">
          <button className="btn" onClick={() => run.mutate(false)} disabled={run.isPending}>
            Position only
          </button>
          <button className="btn btn-primary" onClick={() => run.mutate(true)} disabled={run.isPending}>
            {run.isPending ? "Planning…" : "Plan with AI"}
          </button>
        </div>
      }
    >
      {run.isError && <ErrorBox error={run.error} />}
      {!frame ? (
        <Empty>
          The offer against your floor / target (scoring/model.yaml) and the compensation you have actually observed in
          your stream; then an AI script that may only use those numbers or cited facts.
        </Empty>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={POSITION_TONE[frame.position] ?? "neutral"}>{frame.position.replaceAll("_", " ")}</Badge>
            {frame.gap_to_target_pct !== null && frame.gap_to_target_pct !== undefined && (
              <span className="text-xs text-ink-dim">
                gap to target {frame.gap_to_target_pct > 0 ? "−" : "+"}
                {Math.abs(frame.gap_to_target_pct)}%
              </span>
            )}
            <span className="text-xs text-ink-dim">
              {frame.basis} · {frame.currency}
            </span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <KeyValue
              items={[
                ["Offered", frame.offered_raw ?? `${money(frame.offered_min, frame.currency)} – ${money(frame.offered_max, frame.currency)}`],
                ["Floor", money(frame.floor, frame.currency)],
                ["Target", money(frame.target, frame.currency)],
                ["Anchor", money(frame.anchor, frame.currency)],
              ]}
            />
            <KeyValue
              items={[
                ["Observed postings", String(frame.observed.n)],
                ["p25 / median / p75", frame.observed.n === 0 ? "—" : `${money(frame.observed.p25, frame.currency)} / ${money(frame.observed.median, frame.currency)} / ${money(frame.observed.p75, frame.currency)}`],
                ["Unknowns", (frame.unknowns ?? []).join("; ") || "none"],
                ["Notes", (frame.notes ?? []).join("; ") || "—"],
              ]}
            />
          </div>
          {(frame.leverage ?? []).length > 0 && (
            <div>
              <div className="label">Leverage facts</div>
              <ul className="space-y-0.5 text-xs">
                {(frame.leverage ?? []).map((l) => (
                  <li key={l.fact_id}>
                    <span className="font-mono text-accent">{l.fact_id}</span> · {l.title} —{" "}
                    <span className="text-ink-dim">{(l.technologies ?? []).join(", ")}</span>
                    {(l.metrics ?? []).length > 0 && <span className="text-ink-dim"> · {(l.metrics ?? []).join(" · ")}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {plan && result && (
            <div className="space-y-3 border-t border-line pt-3">
              <div className="flex items-center gap-2">
                <Badge tone={plan.stance === "walk_away" ? "bad" : plan.stance === "accept" ? "good" : "accent"}>
                  {plan.stance.replaceAll("_", " ").toUpperCase()}
                </Badge>
                {plan.counter_ask && <span className="font-medium">{plan.counter_ask}</span>}
              </div>
              <p>{plan.rationale}</p>
              {(plan.leverage ?? []).length > 0 && (
                <div>
                  <div className="label">Leverage</div>
                  <ul className="space-y-1 text-xs">
                    {(plan.leverage ?? []).map((lp, i) => (
                      <li key={i}>
                        {lp.point}
                        <Provenance ids={lp.derived_from ?? []} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <List label="Script (say it yourself — nothing is sent)" items={plan.script ?? []} ordered />
              <div className="grid gap-3 sm:grid-cols-3">
                <List label="Concessions" items={plan.concessions ?? []} />
                <List label="Questions" items={plan.questions ?? []} />
                <List label="Risks" items={plan.risks ?? []} tone="text-bad" />
              </div>
              <Rejected items={result.provenance_rejected ?? []} />
              <p className="text-[11px] text-ink-dim">
                {result.provider}/{result.model} · saved to AI suggestions for review
              </p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
