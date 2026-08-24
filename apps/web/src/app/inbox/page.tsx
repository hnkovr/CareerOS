"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Badge, Card, copyToClipboard, Empty, ErrorBox, Spinner } from "@/components/ui";

type MessageOut = Schemas["MessageOut"];

const CLASS_TONES: Record<string, "good" | "warn" | "bad" | "accent" | "violet" | "neutral"> = {
  offer: "good",
  interview: "warn",
  recruiter_outreach: "accent",
  new_opportunity: "accent",
  client_lead: "accent",
  rejection: "bad",
  follow_up_required: "warn",
  platform_notification: "neutral",
  spam_noise: "neutral",
  application_update: "neutral",
  other: "neutral",
};

function IngestForm({ onDone }: { onDone: () => void }) {
  const [raw, setRaw] = useState("");
  const [useAi, setUseAi] = useState(false);
  const queryClient = useQueryClient();

  const ingest = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/inbox/ingest", {
          params: { query: { use_ai: useAi } },
          body: { body_text: raw, raw, direction: "inbound", provider: "manual" },
        }),
      ),
    onSuccess: () => {
      setRaw("");
      queryClient.invalidateQueries({ queryKey: ["inbox"] });
      onDone();
    },
  });

  return (
    <div className="space-y-3">
      <textarea
        className="input h-56 font-mono text-xs"
        placeholder={"Paste the full email — headers help:\nFrom: Jane <jane@acme.com>\nSubject: Re: Senior DE\n\nHi Dana…"}
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
      />
      <label className="flex items-center gap-2 text-sm text-ink-dim">
        <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
        Refine ambiguous classification with AI
      </label>
      <button className="btn btn-primary" onClick={() => ingest.mutate()} disabled={!raw.trim() || ingest.isPending}>
        {ingest.isPending ? "Classifying…" : "Ingest email"}
      </button>
      {ingest.isError && <ErrorBox error={ingest.error} />}
    </div>
  );
}

function MessageDetail({ message }: { message: MessageOut }) {
  const queryClient = useQueryClient();
  const [reply, setReply] = useState<Schemas["ReplySuggestionOut"] | null>(null);
  const [copied, setCopied] = useState(false);
  const [intent, setIntent] = useState<"follow_up" | "accept" | "decline" | "ask_questions" | "negotiate">("follow_up");

  const suggest = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/inbox/messages/{message_id}/suggest-reply", {
          params: { path: { message_id: message.id } },
          body: { intent },
        }),
      ),
    onSuccess: (data) => {
      setReply(data);
      setCopied(false);
    },
  });

  const markRead = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PATCH("/api/inbox/messages/{message_id}", {
          params: { path: { message_id: message.id } },
          body: { mark_read: true },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["inbox"] }),
  });

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-semibold">{message.subject ?? "(no subject)"}</h3>
        <p className="text-xs text-ink-dim">
          {message.from_name ?? message.from_email} · {timeAgo(message.received_at)} · confidence{" "}
          {message.classification_confidence} ({message.classified_by})
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Badge tone={CLASS_TONES[message.classification] ?? "neutral"}>{message.classification.replaceAll("_", " ")}</Badge>
        <Badge tone={message.urgency === "high" ? "bad" : "neutral"}>{message.urgency}</Badge>
        {message.deadline_hint && <Badge tone="warn">{message.deadline_hint}</Badge>}
        {message.links.opportunity_id && (
          <Link href={`/opportunities/${message.links.opportunity_id}`}>
            <Badge tone="accent">opportunity →</Badge>
          </Link>
        )}
        {message.links.application_id && (
          <Link href={`/pipeline/${message.links.application_id}`}>
            <Badge tone="violet">application →</Badge>
          </Link>
        )}
        {message.extracted_opportunity && <Badge tone="good">opportunity extracted</Badge>}
        {!message.read_at && (
          <button className="btn px-2 py-0.5 text-[11px]" onClick={() => markRead.mutate()}>
            mark read
          </button>
        )}
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 text-xs text-ink-dim">
        {message.body_text}
      </pre>
      <div className="flex items-center gap-2">
        <select className="input w-auto" value={intent} onChange={(e) => setIntent(e.target.value as never)}>
          {["follow_up", "accept", "decline", "ask_questions", "negotiate"].map((i) => (
            <option key={i} value={i}>
              {i.replaceAll("_", " ")}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={() => suggest.mutate()} disabled={suggest.isPending}>
          {suggest.isPending ? "Drafting…" : "Suggest reply"}
        </button>
      </div>
      {suggest.isError && <ErrorBox error={suggest.error} />}
      {reply && (
        <div className="space-y-2 rounded-lg border border-line bg-panel-2 p-3">
          <p className="text-xs text-ink-dim">
            Draft only — nothing is sent automatically. {reply.notes && <em>Note: {reply.notes}</em>}
          </p>
          {reply.subject && <p className="text-sm font-medium">{reply.subject}</p>}
          <pre className="whitespace-pre-wrap text-sm">{reply.body}</pre>
          <button className="btn" onClick={async () => setCopied(await copyToClipboard(reply.body))}>
            {copied ? "Copied ✓" : "Copy draft"}
          </button>
        </div>
      )}
    </div>
  );
}

function InboxPageInner() {
  const searchParams = useSearchParams();
  const [showNew, setShowNew] = useState(searchParams.get("new") === "1");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [selected, setSelected] = useState<MessageOut | null>(null);

  const messages = useQuery({
    queryKey: ["inbox", attentionOnly],
    queryFn: async () =>
      unwrap(await api.GET("/api/inbox/messages", { params: { query: { needs_attention: attentionOnly, limit: 100 } } })),
  });
  const stats = useQuery({
    queryKey: ["inbox", "stats"],
    queryFn: async () => unwrap(await api.GET("/api/inbox/stats")),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-bold">
          Career Inbox
          {stats.data && (
            <span className="ml-2 text-sm font-normal text-ink-dim">
              {stats.data.unread} unread · {stats.data.needs_attention} need attention
            </span>
          )}
        </h1>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-ink-dim">
            <input type="checkbox" checked={attentionOnly} onChange={(e) => setAttentionOnly(e.target.checked)} />
            needs attention
          </label>
          <button className="btn btn-primary" onClick={() => setShowNew((v) => !v)}>
            {showNew ? "Close" : "Paste email"}
          </button>
        </div>
      </div>

      {showNew && (
        <Card title="Capture an email (copy/paste or forward-paste — Gmail sync arrives in P1.3)">
          <IngestForm onDone={() => setShowNew(false)} />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(300px,2fr)_3fr]">
        <Card title="Messages">
          {messages.isPending ? (
            <Spinner />
          ) : (messages.data ?? []).length === 0 ? (
            <Empty>No messages yet — paste your first email.</Empty>
          ) : (
            <ul className="divide-y divide-line/60">
              {(messages.data ?? []).map((m) => (
                <li key={m.id}>
                  <button
                    className={`w-full px-1 py-2 text-left hover:bg-panel-2 ${selected?.id === m.id ? "bg-panel-2" : ""}`}
                    onClick={() => setSelected(m)}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className={`truncate text-sm ${m.read_at ? "text-ink-dim" : "font-medium"}`}>
                        {m.subject ?? "(no subject)"}
                      </span>
                      <span className="shrink-0 text-[11px] text-ink-dim">{timeAgo(m.received_at)}</span>
                    </span>
                    <span className="flex items-center gap-1.5 pt-0.5">
                      <Badge tone={CLASS_TONES[m.classification] ?? "neutral"}>{m.classification.replaceAll("_", " ")}</Badge>
                      {m.urgency === "high" && <Badge tone="bad">high</Badge>}
                      <span className="truncate text-[11px] text-ink-dim">{m.from_email}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Message">
          {selected ? <MessageDetail message={selected} /> : <Empty>Select a message.</Empty>}
        </Card>
      </div>
    </div>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <InboxPageInner />
    </Suspense>
  );
}
