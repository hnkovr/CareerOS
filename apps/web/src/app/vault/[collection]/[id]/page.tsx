"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, unwrap, type ChangePreview } from "@/lib/api";
import { Badge, Card, Diff, ErrorBox, Spinner } from "@/components/ui";

/** Edit one canonical item as JSON → preview diff → apply (commit). ADR-001 write path. */
export default function VaultItemPage() {
  const params = useParams<{ collection: string; id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const collection = params.collection;
  const isNew = params.id === "__new__";
  const itemId = isNew ? null : decodeURIComponent(params.id);

  const [text, setText] = useState<string>("");
  const [preview, setPreview] = useState<ChangePreview | null>(null);

  const item = useQuery({
    enabled: !isNew,
    queryKey: ["vault-item", collection, itemId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/vault/{collection}/{item_id}", {
          params: { path: { collection, item_id: itemId! } },
        }),
      ) as Record<string, unknown>,
  });

  useEffect(() => {
    if (item.data && !text) setText(JSON.stringify(item.data, null, 2));
    if (isNew && !text) setText(JSON.stringify({ id: "", status: "draft" }, null, 2));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.data, isNew]);

  const previewMutation = useMutation({
    mutationFn: async () => {
      const data = JSON.parse(text) as Record<string, unknown>;
      return unwrap(
        await api.POST("/api/vault/changes/preview", {
          body: { collection, item_id: itemId, data, op: "upsert" },
        }),
      );
    },
    onSuccess: setPreview,
  });

  const applyMutation = useMutation({
    mutationFn: async () => {
      const data = JSON.parse(text) as Record<string, unknown>;
      return unwrap(
        await api.POST("/api/vault/changes/apply", {
          body: { collection, item_id: itemId, data, op: "upsert", base_sha: preview?.base_sha ?? null },
        }),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries();
      router.push("/vault");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/vault/changes/apply", {
          body: { collection, item_id: itemId, op: "delete" },
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries();
      router.push("/vault");
    },
  });

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card
        title={
          <span>
            <Link href="/vault" className="text-ink-dim hover:text-accent">
              vault
            </Link>{" "}
            / {collection} / <span className="font-mono">{itemId ?? "new"}</span>
          </span>
        }
        action={
          !isNew && (
            <button
              className="btn text-bad"
              onClick={() => {
                if (window.confirm(`Delete ${itemId}? A commit will record the removal.`)) deleteMutation.mutate();
              }}
            >
              Delete
            </button>
          )
        }
      >
        {!isNew && item.isPending ? (
          <Spinner />
        ) : item.isError ? (
          <ErrorBox error={item.error} />
        ) : (
          <div className="space-y-3">
            <textarea
              className="input h-[420px] font-mono text-xs leading-5"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setPreview(null);
              }}
              spellCheck={false}
            />
            <div className="flex gap-2">
              <button className="btn" onClick={() => previewMutation.mutate()} disabled={previewMutation.isPending}>
                Preview diff
              </button>
              <button
                className="btn btn-primary"
                onClick={() => applyMutation.mutate()}
                disabled={!preview?.ok || applyMutation.isPending}
                title={!preview ? "Preview first" : preview.ok ? "" : "Fix validation errors first"}
              >
                Apply &amp; commit
              </button>
            </div>
            {(previewMutation.isError || applyMutation.isError || deleteMutation.isError) && (
              <ErrorBox error={previewMutation.error ?? applyMutation.error ?? deleteMutation.error} />
            )}
          </div>
        )}
      </Card>

      <Card title="Change preview">
        {!preview ? (
          <p className="py-8 text-center text-sm text-ink-dim">
            Edit the JSON and press <em>Preview diff</em>. Nothing is written until you apply — every
            apply becomes a git commit in your vault.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {preview.ok ? <Badge tone="good">valid</Badge> : <Badge tone="bad">invalid</Badge>}
              <span className="font-mono text-xs text-ink-dim">{preview.message}</span>
            </div>
            {preview.issues.length > 0 && (
              <ul className="space-y-1 text-xs">
                {preview.issues.map((i, idx) => (
                  <li key={idx}>
                    <Badge tone={i.level === "error" ? "bad" : "warn"}>{i.level}</Badge>{" "}
                    <span className="text-ink-dim">{i.location}:</span> {i.message}
                  </li>
                ))}
              </ul>
            )}
            <Diff diff={preview.diff} />
          </div>
        )}
      </Card>
    </div>
  );
}
