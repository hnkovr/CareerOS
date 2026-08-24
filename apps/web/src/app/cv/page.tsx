"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, unwrap, type CVComparison } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

function GeneratePanel() {
  const router = useRouter();
  const [variantId, setVariantId] = useState("general-core");
  const [jd, setJd] = useState("");
  const [useAi, setUseAi] = useState(true);

  const variants = useQuery({
    queryKey: ["cv-variants"],
    queryFn: async () => unwrap(await api.GET("/api/cv/variants")),
  });

  const generate = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/cv/generate", {
          body: { variant_id: variantId, jd_text: jd || null, use_ai: useAi },
        }),
      ),
    onSuccess: (artifact) => router.push(`/cv/${artifact.id}`),
  });

  return (
    <div className="space-y-3">
      {variants.isPending ? (
        <Spinner />
      ) : (
        <div>
          <label className="label">Variant</label>
          <div className="flex flex-wrap gap-1.5">
            {(variants.data ?? []).map((v) => (
              <button
                key={v.id}
                className={`btn ${v.id === variantId ? "btn-primary" : ""}`}
                onClick={() => setVariantId(v.id)}
                title={`${v.positioning_id} → ${v.channel_id} · ${v.sections.join(", ")}`}
              >
                {v.id}
              </button>
            ))}
          </div>
        </div>
      )}
      <div>
        <label className="label">Tailor to a JD (optional)</label>
        <textarea
          className="input h-28 font-mono text-xs"
          placeholder="Paste a job description to bias fact selection and wording…"
          value={jd}
          onChange={(e) => setJd(e.target.value)}
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-ink-dim">
        <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
        Rewrite bullets with AI (provenance-guarded; falls back to verbatim facts)
      </label>
      <button className="btn btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
        {generate.isPending ? "Generating (RenderCV)…" : "Generate CV"}
      </button>
      {generate.isError && <ErrorBox error={generate.error} />}
    </div>
  );
}

function CVPageInner() {
  const searchParams = useSearchParams();
  const [showGenerate, setShowGenerate] = useState(searchParams.get("generate") === "1");
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<CVComparison | null>(null);

  const artifacts = useQuery({
    queryKey: ["cv-artifacts"],
    queryFn: async () => unwrap(await api.GET("/api/cv/artifacts", { params: { query: { limit: 50 } } })),
  });

  const compare = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/cv/compare", { body: { a: selected[0], b: selected[1] } })),
    onSuccess: setComparison,
  });

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev.slice(-1), id]));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">CV-as-Code</h1>
        <div className="flex gap-2">
          <button className="btn" disabled={selected.length !== 2 || compare.isPending} onClick={() => compare.mutate()}>
            Compare selected ({selected.length}/2)
          </button>
          <button className="btn btn-primary" onClick={() => setShowGenerate((v) => !v)}>
            {showGenerate ? "Close" : "Generate"}
          </button>
        </div>
      </div>

      {showGenerate && (
        <Card title="Generate a variant from canonical facts">
          <GeneratePanel />
        </Card>
      )}

      {comparison && (
        <Card title={`Comparison — ${comparison.unchanged} unchanged bullets`} action={<button className="btn" onClick={() => setComparison(null)}>Close</button>}>
          <div className="grid gap-4 md:grid-cols-3">
            <DiffColumn title={`Only in A (${comparison.removed.length})`} tone="text-bad" items={comparison.removed.map((d) => d.text_a ?? "")} />
            <DiffColumn title={`Only in B (${comparison.added.length})`} tone="text-good" items={comparison.added.map((d) => d.text_b ?? "")} />
            <div>
              <div className="label">Rewritten ({comparison.rewritten.length})</div>
              <ul className="space-y-2 text-xs">
                {comparison.rewritten.map((d, i) => (
                  <li key={i} className="rounded-lg border border-line p-2">
                    <div className="text-bad line-through">{d.text_a}</div>
                    <div className="text-good">{d.text_b}</div>
                    <div className="pt-1 font-mono text-[10px] text-ink-dim">{d.derived_from.join(", ")}</div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="grid gap-4 pt-3 text-xs text-ink-dim md:grid-cols-2">
            <div>Keywords only in A: {comparison.keywords_only_a.join(", ") || "—"}</div>
            <div>Keywords only in B: {comparison.keywords_only_b.join(", ") || "—"}</div>
          </div>
        </Card>
      )}
      {compare.isError && <ErrorBox error={compare.error} />}

      <Card title="Artifacts">
        {artifacts.isPending ? (
          <Spinner />
        ) : (artifacts.data ?? []).length === 0 ? (
          <Empty>No CVs yet — generate the first one.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-dim">
                <th className="pb-2" />
                <th className="pb-2">Variant</th>
                <th className="pb-2">Positioning → Channel</th>
                <th className="pb-2">Bullets</th>
                <th className="pb-2">Files</th>
                <th className="pb-2 text-right">Created</th>
              </tr>
            </thead>
            <tbody>
              {(artifacts.data ?? []).map((a) => (
                <tr key={a.id} className="border-t border-line/60">
                  <td className="py-2 pr-2">
                    <input type="checkbox" checked={selected.includes(a.id)} onChange={() => toggle(a.id)} />
                  </td>
                  <td className="py-2 pr-2">
                    <Link href={`/cv/${a.id}`} className="hover:text-accent">
                      {a.variant_id}
                    </Link>
                    <div className="flex gap-1 pt-0.5">
                      {a.ai_used ? <Badge tone="violet">AI · {a.provider}</Badge> : <Badge>facts verbatim</Badge>}
                      {a.status !== "ready" && <Badge tone="bad">{a.status}</Badge>}
                      {a.warnings.length > 0 && <Badge tone="warn">{a.warnings.length} warnings</Badge>}
                    </div>
                  </td>
                  <td className="py-2 pr-2 text-xs text-ink-dim">
                    {a.positioning_id} → {a.channel_id}
                  </td>
                  <td className="py-2 pr-2 tabular-nums">{a.bullet_count}</td>
                  <td className="py-2 pr-2">
                    <span className="flex gap-1">
                      {(["pdf", "md", "json"] as const).map(
                        (kind) =>
                          a.files[kind] && (
                            <a key={kind} className="btn px-2 py-0.5 text-[11px]" href={`/api/cv/artifacts/${a.id}/file/${kind}`}>
                              {kind}
                            </a>
                          ),
                      )}
                    </span>
                  </td>
                  <td className="py-2 text-right text-xs text-ink-dim">{timeAgo(a.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function DiffColumn({ title, tone, items }: { title: string; tone: string; items: string[] }) {
  return (
    <div>
      <div className="label">{title}</div>
      <ul className={`list-inside list-disc space-y-1 text-xs ${tone}`}>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
        {items.length === 0 && <li className="list-none text-ink-dim">—</li>}
      </ul>
    </div>
  );
}

export default function CVPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <CVPageInner />
    </Suspense>
  );
}
