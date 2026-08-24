"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

type Item = { id: string; status?: string; [key: string]: unknown };

const TITLE_KEYS = ["title", "name", "headline", "label", "company_name"];

function itemTitle(item: Item): string {
  for (const key of TITLE_KEYS) {
    const value = item[key];
    if (typeof value === "string" && value) return value;
  }
  return item.id;
}

export default function VaultPage() {
  const [collection, setCollection] = useState("achievements");

  const status = useQuery({
    queryKey: ["vault-status"],
    queryFn: async () => unwrap(await api.GET("/api/vault/status")),
  });
  const issues = useQuery({
    queryKey: ["vault-issues"],
    queryFn: async () => unwrap(await api.GET("/api/vault/issues")),
  });
  const collections = useQuery({
    queryKey: ["vault-collections"],
    queryFn: async () => unwrap(await api.GET("/api/vault/collections")),
  });
  const items = useQuery({
    queryKey: ["vault-collection", collection],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/vault/{collection}", { params: { path: { collection } } }),
      ) as unknown as Item | Item[] | null,
  });

  const history = useQuery({
    queryKey: ["vault-history"],
    queryFn: async () => unwrap(await api.GET("/api/vault/history", { params: { query: { n: 8 } } })),
  });

  const list: Item[] = Array.isArray(items.data) ? items.data : items.data ? [items.data] : [];

  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr_300px]">
      <div className="space-y-4">
        <Card title="Collections">
          {collections.isPending ? (
            <Spinner />
          ) : (
            <ul className="space-y-0.5">
              {(collections.data ?? []).map((c) => (
                <li key={c}>
                  <button
                    className={`w-full rounded-lg px-2.5 py-1.5 text-left text-sm capitalize ${
                      c === collection ? "bg-accent/15 text-accent" : "text-ink-dim hover:bg-panel-2 hover:text-ink"
                    }`}
                    onClick={() => setCollection(c)}
                  >
                    {c.replaceAll("_", " ")}
                    {status.data?.counts?.[c] != null && (
                      <span className="float-right text-xs opacity-60">{status.data.counts[c]}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card
        title={<span className="capitalize">{collection.replaceAll("_", " ")}</span>}
        action={
          <Link className="btn" href={`/vault/${collection}/__new__`}>
            Add
          </Link>
        }
      >
        {items.isPending ? (
          <Spinner />
        ) : items.isError ? (
          <ErrorBox error={items.error} />
        ) : list.length === 0 ? (
          <Empty>Empty collection</Empty>
        ) : (
          <ul className="divide-y divide-line/60">
            {list.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-2 py-2">
                <Link href={`/vault/${collection}/${item.id}`} className="min-w-0 flex-1 hover:text-accent">
                  <span className="block truncate text-sm">{itemTitle(item)}</span>
                  <span className="block truncate font-mono text-[11px] text-ink-dim">{item.id}</span>
                </Link>
                {item.status === "verified" && <Badge tone="good">verified</Badge>}
                {item.status === "draft" && <Badge tone="warn">draft</Badge>}
                {item.status === "retired" && <Badge>retired</Badge>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="space-y-4">
        <Card title="Validation">
          {issues.isPending ? (
            <Spinner />
          ) : (issues.data ?? []).length === 0 ? (
            <p className="text-sm text-good">No issues — vault is clean.</p>
          ) : (
            <ul className="space-y-2 text-xs">
              {(issues.data ?? []).slice(0, 10).map((i, idx) => (
                <li key={idx}>
                  <Badge tone={i.level === "error" ? "bad" : "warn"}>{i.level}</Badge>{" "}
                  <span className="text-ink-dim">
                    {i.file}:{i.location}
                  </span>
                  <div>{i.message}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent commits">
          {history.isPending ? (
            <Spinner />
          ) : (history.data ?? []).length === 0 ? (
            <Empty>No history</Empty>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {(history.data ?? []).map((c) => (
                <li key={c.sha}>
                  <span className="font-mono text-ink-dim">{c.sha.slice(0, 7)}</span> {c.message}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
