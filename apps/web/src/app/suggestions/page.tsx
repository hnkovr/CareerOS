"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Badge, Card, copyToClipboard, Empty, ErrorBox, Spinner } from "@/components/ui";

type SuggestionOut = Schemas["SuggestionOut"];

const STATE_TONES: Record<string, "good" | "warn" | "bad" | "accent" | "violet" | "neutral"> = {
  suggested: "accent",
  reviewed: "warn",
  approved: "good",
  executed: "neutral",
  rejected: "bad",
};

function SuggestionCard({ s }: { s: SuggestionOut }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["suggestions"] });

  const move = useMutation({
    mutationFn: async (state: string) =>
      unwrap(
        await api.PATCH("/api/ai/suggestions/{suggestion_id}", {
          params: { path: { suggestion_id: s.id } },
          body: { state },
        }),
      ),
    onSuccess: invalidate,
  });

  const replySent = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/inbox/messages/{message_id}/reply-sent", {
          params: { path: { message_id: s.target_ref } },
          body: { suggestion_id: s.id },
        }),
      ),
    onSuccess: invalidate,
  });

  const body = typeof s.payload.body === "string" ? s.payload.body : JSON.stringify(s.payload, null, 2);

  return (
    <div className="card space-y-2 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{s.title}</span>
        <span className="flex items-center gap-1.5">
          <Badge tone={STATE_TONES[s.state] ?? "neutral"}>{s.state}</Badge>
          <Badge>{s.target_type}</Badge>
          <span className="text-[11px] text-ink-dim">{timeAgo(s.created_at)}</span>
        </span>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-2 text-xs text-ink-dim">{body}</pre>
      {s.decision_note && <p className="text-xs text-ink-dim">note: {s.decision_note}</p>}
      <div className="flex flex-wrap gap-1.5">
        {s.state === "suggested" && (
          <>
            <button className="btn" onClick={() => move.mutate("approved")}>Approve</button>
            <button className="btn text-bad" onClick={() => move.mutate("rejected")}>Reject</button>
          </>
        )}
        {s.state === "reviewed" && (
          <>
            <button className="btn" onClick={() => move.mutate("approved")}>Approve</button>
            <button className="btn text-bad" onClick={() => move.mutate("rejected")}>Reject</button>
          </>
        )}
        {s.state === "approved" && (
          <>
            {s.target_type === "reply" ? (
              <button className="btn btn-primary" onClick={() => replySent.mutate()} disabled={replySent.isPending}>
                I sent it — mark executed
              </button>
            ) : (
              <button className="btn btn-primary" onClick={() => move.mutate("executed")}>
                Mark executed
              </button>
            )}
            <button className="btn text-bad" onClick={() => move.mutate("rejected")}>Reject</button>
          </>
        )}
        {typeof s.payload.body === "string" && (
          <button className="btn" onClick={async () => setCopied(await copyToClipboard(String(s.payload.body)))}>
            {copied ? "Copied ✓" : "Copy"}
          </button>
        )}
      </div>
      {(move.isError || replySent.isError) && <ErrorBox error={move.error ?? replySent.error} />}
    </div>
  );
}

export default function SuggestionsPage() {
  const [stateFilter, setStateFilter] = useState<string>("");

  const suggestions = useQuery({
    queryKey: ["suggestions", stateFilter],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/ai/suggestions", {
          params: { query: stateFilter ? { state: stateFilter, limit: 100 } : { limit: 100 } },
        }),
      ),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">AI Suggestions</h1>
        <select className="input w-auto" value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
          <option value="">all states</option>
          {["suggested", "reviewed", "approved", "executed", "rejected"].map((st) => (
            <option key={st} value={st}>
              {st}
            </option>
          ))}
        </select>
      </div>
      <p className="text-xs text-ink-dim">
        Every AI proposal waits here for your decision. Nothing is sent or written externally until you
        approve it and act — “executed” records that <em>you</em> did.
      </p>
      {suggestions.isPending ? (
        <Spinner />
      ) : suggestions.isError ? (
        <ErrorBox error={suggestions.error} />
      ) : (suggestions.data ?? []).length === 0 ? (
        <Card>
          <Empty>No suggestions yet — draft a reply in the Inbox to create one.</Empty>
        </Card>
      ) : (
        <div className="space-y-3">
          {(suggestions.data ?? []).map((s) => (
            <SuggestionCard key={s.id} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}
