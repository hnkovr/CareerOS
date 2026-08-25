"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api, unwrap, type BundleOut } from "@/lib/api";
import { formatDate, recommendationLabel, truncate } from "@/lib/format";
import { Badge, Card, copyToClipboard, Empty, ErrorBox, KeyValue, ScoreBar, ScoreRing, Spinner } from "@/components/ui";
import { InterviewPrepCard, NegotiationCard } from "./assistants";

const EXTERNAL_TARGETS = ["chatgpt", "claude", "gemini", "grok", "perplexity"] as const;

export default function OpportunityDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const id = params.id;
  const [bundle, setBundle] = useState<BundleOut | null>(null);
  const [copied, setCopied] = useState(false);

  const opp = useQuery({
    queryKey: ["opportunity", id],
    queryFn: async () =>
      unwrap(await api.GET("/api/opportunities/{opportunity_id}", { params: { path: { opportunity_id: id } } })),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["opportunity", id] });
    queryClient.invalidateQueries({ queryKey: ["opportunities"] });
  };

  const analyze = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/opportunities/{opportunity_id}/analyze", { params: { path: { opportunity_id: id } }, body: {} })),
    onSuccess: invalidate,
  });
  const rescore = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/opportunities/{opportunity_id}/rescore", { params: { path: { opportunity_id: id } } })),
    onSuccess: invalidate,
  });
  const setStatus = useMutation({
    mutationFn: async (status: string) =>
      unwrap(
        await api.PATCH("/api/opportunities/{opportunity_id}/status", {
          params: { path: { opportunity_id: id } },
          body: { status: status as never },
        }),
      ),
    onSuccess: invalidate,
  });
  const externalPrompt = useMutation({
    mutationFn: async (target: (typeof EXTERNAL_TARGETS)[number]) =>
      unwrap(
        await api.POST("/api/opportunities/{opportunity_id}/external-prompt", {
          params: { path: { opportunity_id: id } },
          body: { target },
        }),
      ),
    onSuccess: (data) => {
      setBundle(data);
      setCopied(false);
    },
  });
  const addToPipeline = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/pipeline/applications", { body: { opportunity_id: id } })),
    onSuccess: (a) => router.push(`/pipeline/${a.id}`),
  });
  const generateCv = useMutation({
    mutationFn: async (variantId: string) =>
      unwrap(await api.POST("/api/cv/generate", { body: { variant_id: variantId, opportunity_id: id, use_ai: true } })),
    onSuccess: (artifact) => router.push(`/cv/${artifact.id}`),
  });

  if (opp.isPending) return <Spinner />;
  if (opp.isError) return <ErrorBox error={opp.error} />;
  const o = opp.data;
  const score = o.score;
  const analysis = o.analysis;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">{o.title}</h1>
          <p className="text-sm text-ink-dim">
            {o.company_name ?? "unknown company"} · {o.source} · {o.remote_policy.replaceAll("_", " ")}
            {o.remote_regions.length > 0 && ` (${o.remote_regions.join(", ")})`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input w-auto"
            value={o.status}
            onChange={(e) => setStatus.mutate(e.target.value)}
            title="Status"
          >
            {["new", "watching", "applied", "ignored", "archived"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => rescore.mutate()} disabled={rescore.isPending}>
            Rescore
          </button>
          <button className="btn" onClick={() => addToPipeline.mutate()} disabled={addToPipeline.isPending}>
            Add to pipeline
          </button>
          <button className="btn btn-primary" onClick={() => analyze.mutate()} disabled={analyze.isPending}>
            {analyze.isPending ? "Analyzing…" : "Analyze with AI"}
          </button>
        </div>
      </div>
      {(analyze.isError || setStatus.isError || addToPipeline.isError) && (
        <ErrorBox error={analyze.error ?? setStatus.error ?? addToPipeline.error} />
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Transparent score" className="lg:row-span-2">
          {!score ? (
            <Empty>Not scored yet</Empty>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <ScoreRing score={score.overall} size="text-4xl" />
                <div>
                  <Badge tone={score.overall >= 80 ? "good" : score.overall >= 65 ? "accent" : "neutral"}>
                    {recommendationLabel(score.recommendation)}
                  </Badge>
                  <p className="pt-1 text-[11px] text-ink-dim">
                    scoring v{score.scoring_version}
                    {score.vault_sha ? ` @ ${score.vault_sha.slice(0, 8)}` : ""}
                  </p>
                </div>
              </div>
              {(score.reasons ?? []).length > 0 && (
                <ul className="space-y-0.5 text-xs text-ink-dim">
                  {(score.reasons ?? []).map((r, i) => (
                    <li key={i}>· {r}</li>
                  ))}
                </ul>
              )}
              <div className="space-y-2.5">
                {score.dimensions
                  .filter((d) => d.name !== "overall_fit")
                  .map((d) => (
                    <ScoreBar key={d.name} name={d.name} score={d.score} weight={d.weight} explanation={d.explanation} />
                  ))}
              </div>
            </div>
          )}
        </Card>

        <Card title="Details" className="lg:col-span-2">
          <KeyValue
            items={[
              ["Contract", [o.contract_type, o.employment_type].filter(Boolean).join(" / ") || "—"],
              ["Seniority", o.seniority ?? "—"],
              ["Compensation", o.compensation?.raw ?? "not stated"],
              ["Timezone", o.timezone_range ?? "—"],
              ["Technologies", o.technologies.join(", ") || "—"],
              ["Requirements", o.requirements.slice(0, 6).join(" · ") || "—"],
              ["Red flags", o.red_flags.join("; ") || "none"],
              ["Recruiter", o.recruiter?.email ?? o.recruiter?.name ?? "—"],
              ["Received", formatDate(o.received_at)],
              ["Deadline", formatDate(o.deadline)],
              ["Parser", `${o.parser} (confidence ${o.parse_confidence})`],
              ["URL", o.url ? <a key="u" className="text-accent" href={o.url} target="_blank" rel="noreferrer">{truncate(o.url, 60)}</a> : "—"],
            ]}
          />
        </Card>

        <Card
          title="AI analysis"
          className="lg:col-span-2"
          action={
            analysis && (
              <span className="text-xs text-ink-dim">
                {analysis.provider}/{analysis.model}
              </span>
            )
          }
        >
          {!analysis ? (
            <Empty>Run “Analyze with AI”, or generate an external prompt below — both interpret the same deterministic score.</Empty>
          ) : (
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-2">
                <Badge tone={analysis.verdict === "apply" ? "good" : analysis.verdict === "skip" ? "bad" : "accent"}>
                  {analysis.verdict.toUpperCase()}
                </Badge>
                <span className="font-medium">{analysis.next_action}</span>
              </div>
              <p>{analysis.executive_summary}</p>
              <div className="grid gap-3 sm:grid-cols-3">
                <AnalysisList label="Strengths" items={analysis.strengths ?? []} tone="text-good" />
                <AnalysisList label="Gaps" items={analysis.gaps ?? []} tone="text-warn" />
                <AnalysisList label="Risks" items={analysis.risks ?? []} tone="text-bad" />
              </div>
              {analysis.recommended_cv_variant && (
                <p className="text-sm">
                  Recommended CV: <span className="font-mono text-accent">{analysis.recommended_cv_variant}</span>{" "}
                  <button
                    className="btn ml-2"
                    onClick={() => generateCv.mutate(analysis.recommended_cv_variant!)}
                    disabled={generateCv.isPending}
                  >
                    {generateCv.isPending ? "Generating…" : "Generate tailored CV"}
                  </button>
                </p>
              )}
              {analysis.suggested_response && (
                <div>
                  <div className="label">Suggested response (send it yourself — nothing is auto-sent)</div>
                  <blockquote className="rounded-lg border border-line bg-panel-2 p-3 text-sm">{analysis.suggested_response}</blockquote>
                </div>
              )}
              {(analysis.questions_to_ask ?? []).length > 0 && (
                <AnalysisList label="Questions to ask" items={analysis.questions_to_ask ?? []} tone="text-ink" />
              )}
              {(analysis.interview_prep ?? []).length > 0 && (
                <AnalysisList label="Interview prep" items={analysis.interview_prep ?? []} tone="text-ink" />
              )}
            </div>
          )}
        </Card>

        <Card title="External AI chat (Mode B)">
          <p className="pb-2 text-xs text-ink-dim">
            Generate a self-contained 12-point analysis prompt and paste it into your own chat.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {EXTERNAL_TARGETS.map((t) => (
              <button key={t} className="btn" onClick={() => externalPrompt.mutate(t)} disabled={externalPrompt.isPending}>
                {t}
              </button>
            ))}
          </div>
          {bundle && (
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2">
                <button
                  className="btn btn-primary"
                  onClick={async () => setCopied(await copyToClipboard(bundle.text))}
                >
                  {copied ? "Copied ✓" : "Copy prompt"}
                </button>
                {bundle.deep_link && (
                  <a className="btn" href={bundle.deep_link} target="_blank" rel="noreferrer">
                    Open {bundle.target} ↗
                  </a>
                )}
              </div>
              <textarea readOnly className="input h-40 font-mono text-[11px]" value={bundle.text} />
            </div>
          )}
        </Card>

        <InterviewPrepCard id={id} />
        <NegotiationCard id={id} />

        <Card title="Original text" className="lg:col-span-3">
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 text-xs text-ink-dim">
            {o.raw_text ?? o.description_md ?? "—"}
          </pre>
        </Card>
      </div>
    </div>
  );
}

function AnalysisList({ label, items, tone }: { label: string; items: string[]; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="label">{label}</div>
      <ul className={`list-inside list-disc space-y-0.5 text-xs ${tone}`}>
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </div>
  );
}
