"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

const KIND_LABEL: Record<string, string> = {
  fact: "Vault fact",
  opportunity: "Opportunity",
  message: "Email",
  cv_artifact: "CV",
  contact: "Contact",
};

export default function SearchPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");

  const results = useQuery({
    enabled: query.length >= 2,
    queryKey: ["search", query],
    queryFn: async () => unwrap(await api.GET("/api/search", { params: { query: { q: query, limit: 30 } } })),
  });

  const reindex = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/search/reindex", { body: { embed: false } })),
    onSuccess: () => {
      if (query) results.refetch();
    },
  });

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(input.trim());
        }}
      >
        <input
          autoFocus
          className="input text-base"
          placeholder="Search facts, opportunities, emails, CVs, contacts…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button className="btn btn-primary" type="submit" disabled={input.trim().length < 2}>
          Search
        </button>
        <button className="btn" type="button" onClick={() => reindex.mutate()} disabled={reindex.isPending} title="Rebuild the search index">
          {reindex.isPending ? "Indexing…" : "Reindex"}
        </button>
      </form>
      {reindex.isError && <ErrorBox error={reindex.error} />}

      {query.length >= 2 && (
        <Card
          title={
            results.data ? (
              <span>
                {results.data.hits.length} results
                <span className="ml-2 font-normal text-ink-dim">
                  {results.data.indexed_documents} docs indexed · {results.data.semantic_used ? "FTS + semantic" : "full-text"}
                </span>
              </span>
            ) : (
              "Results"
            )
          }
        >
          {results.isPending ? (
            <Spinner />
          ) : results.isError ? (
            <ErrorBox error={results.error} />
          ) : results.data.hits.length === 0 ? (
            <Empty>Nothing found — try “Reindex” if the data is new.</Empty>
          ) : (
            <ul className="divide-y divide-line/60">
              {results.data.hits.map((h) => (
                <li key={`${h.kind}-${h.ref_id}`} className="py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    {h.url_path ? (
                      <Link href={h.url_path} className="font-medium hover:text-accent">
                        {h.title}
                      </Link>
                    ) : (
                      <span className="font-medium">{h.title}</span>
                    )}
                    <span className="flex shrink-0 items-center gap-1.5">
                      <Badge tone={h.matched_by === "fts" ? "neutral" : "violet"}>{h.matched_by}</Badge>
                      <Badge tone="accent">{KIND_LABEL[h.kind] ?? h.kind}</Badge>
                      <span className="text-xs tabular-nums text-ink-dim">{h.score.toFixed(2)}</span>
                    </span>
                  </div>
                  <p className="pt-0.5 text-xs text-ink-dim">{h.snippet}…</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
