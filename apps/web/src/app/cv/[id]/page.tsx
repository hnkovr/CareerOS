"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api, unwrap, type CVDocument } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, KeyValue, Spinner } from "@/components/ui";

type BulletDoc = { text: string; derived_from: string[]; source?: "fact" | "ai" };

/** Provenance is the product: every bullet answers "why is this here?". */
function Bullet({ bullet }: { bullet: BulletDoc }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="group">
      <button
        className="w-full rounded-lg px-2 py-1 text-left text-sm hover:bg-panel-2"
        onClick={() => setOpen((v) => !v)}
        title="Why is this here? Click for source facts"
      >
        <span className="mr-1.5">{bullet.text}</span>
        {bullet.source === "ai" && <Badge tone="violet">AI</Badge>}
      </button>
      {open && (
        <div className="ml-2 mt-1 rounded-lg border border-line bg-surface p-2 text-xs">
          <span className="text-ink-dim">Derived from: </span>
          {bullet.derived_from.map((fid) => (
            <FactLink key={fid} factId={fid} />
          ))}
        </div>
      )}
    </li>
  );
}

const FACT_COLLECTIONS: Record<string, string> = {
  ach_: "achievements",
  proj_: "projects",
  exp_: "experience",
  sk_: "skills",
  cert_: "certifications",
  edu_: "education",
  pub_: "publications",
  tst_: "testimonials",
  offer_: "offers",
};

function FactLink({ factId }: { factId: string }) {
  const prefix = Object.keys(FACT_COLLECTIONS).find((p) => factId.startsWith(p));
  const collection = prefix ? FACT_COLLECTIONS[prefix] : factId === "profile" ? "profile" : null;
  if (!collection || collection === "profile") {
    return <span className="mr-2 font-mono text-accent-2">{factId}</span>;
  }
  return (
    <Link href={`/vault/${collection}/${factId}`} className="mr-2 font-mono text-accent hover:underline">
      {factId}
    </Link>
  );
}

export default function CVArtifactPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const artifact = useQuery({
    queryKey: ["cv-artifact", id],
    queryFn: async () =>
      unwrap(await api.GET("/api/cv/artifacts/{artifact_id}", { params: { path: { artifact_id: id } } })),
  });

  if (artifact.isPending) return <Spinner />;
  if (artifact.isError) return <ErrorBox error={artifact.error} />;
  const a = artifact.data;
  const doc = a.document as CVDocument | null | undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold">
          <Link href="/cv" className="text-ink-dim hover:text-accent">
            cv
          </Link>{" "}
          / {a.variant_id}
        </h1>
        <span className="flex gap-1.5">
          {(["pdf", "md", "typst", "json"] as const).map(
            (kind) =>
              a.files[kind === "json" ? "json" : kind] && (
                <a key={kind} className="btn" href={`/api/cv/artifacts/${a.id}/file/${kind}`}>
                  {kind}
                </a>
              ),
          )}
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Generation">
          <KeyValue
            items={[
              ["Status", a.status],
              ["AI", a.ai_used ? `${a.provider} / ${a.model}` : "no — verbatim facts"],
              ["Vault", a.vault_sha ? a.vault_sha.slice(0, 12) : "—"],
              ["Positioning", a.positioning_id],
              ["Channel", a.channel_id],
              ["Created", formatDate(a.created_at)],
            ]}
          />
          {a.warnings.length > 0 && (
            <div className="mt-3 space-y-1">
              <div className="label">Warnings (guard &amp; fallbacks)</div>
              <ul className="space-y-1 text-xs text-warn">
                {a.warnings.map((w, i) => (
                  <li key={i}>· {w}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card title="Document" className="lg:col-span-2">
          {!doc ? (
            <Empty>No document payload</Empty>
          ) : (
            <div className="space-y-4">
              <div>
                <h2 className="text-base font-bold">{doc.header.name}</h2>
                <p className="text-sm text-accent">{doc.header.headline}</p>
                <p className="text-xs text-ink-dim">
                  {doc.header.location} · {doc.header.email}
                </p>
              </div>
              {doc.summary && (
                <div>
                  <div className="label">Summary</div>
                  <ul>
                    <Bullet bullet={doc.summary} />
                  </ul>
                </div>
              )}
              {(doc.experience ?? []).map((e) => (
                <div key={e.experience_id}>
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-sm font-semibold">
                      {e.position} · <span className="text-ink-dim">{e.company}</span>
                    </h3>
                    <span className="text-xs text-ink-dim">
                      {e.start.slice(0, 7)} – {e.end ? e.end.slice(0, 7) : "present"}
                    </span>
                  </div>
                  <ul className="mt-1 space-y-0.5">
                    {(e.bullets ?? []).map((b, i) => (
                      <Bullet key={i} bullet={b} />
                    ))}
                  </ul>
                </div>
              ))}
              {(doc.projects ?? []).length > 0 && (
                <div>
                  <div className="label">Projects</div>
                  {(doc.projects ?? []).map((p) => (
                    <div key={p.project_id} className="mb-2">
                      <h4 className="text-sm font-medium">{p.name}</h4>
                      <p className="text-xs text-ink-dim">{p.summary}</p>
                      <ul className="mt-0.5 space-y-0.5">
                        {(p.bullets ?? []).map((b, i) => (
                          <Bullet key={i} bullet={b} />
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
              {(doc.skills ?? []).length > 0 && (
                <div>
                  <div className="label">Skills</div>
                  <div className="space-y-1 text-sm">
                    {(doc.skills ?? []).map((g) => (
                      <p key={g.label}>
                        <span className="text-ink-dim">{g.label}:</span> {g.items.join(", ")}
                      </p>
                    ))}
                  </div>
                </div>
              )}
              <div className="border-t border-line pt-2 text-xs text-ink-dim">
                ATS keywords covered: {(doc.keywords ?? []).join(", ") || "—"}
                {(doc.jd_keywords ?? []).length > 0 && <> · JD keywords: {(doc.jd_keywords ?? []).join(", ")}</>}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
