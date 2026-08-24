"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api";
import { formatDate, timeAgo } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, KeyValue, ScoreRing, Spinner } from "@/components/ui";

const EVENT_KINDS = ["note", "message_sent", "message_received", "follow_up", "feedback", "offer"] as const;
const INTERVIEW_KINDS = ["recruiter_screen", "technical", "system_design", "take_home", "final", "client_call", "other"] as const;

export default function ApplicationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const queryClient = useQueryClient();
  const [eventTitle, setEventTitle] = useState("");
  const [eventKind, setEventKind] = useState<(typeof EVENT_KINDS)[number]>("note");
  const [interviewKind, setInterviewKind] = useState<(typeof INTERVIEW_KINDS)[number]>("recruiter_screen");
  const [interviewAt, setInterviewAt] = useState("");
  const [followUp, setFollowUp] = useState("");

  const app = useQuery({
    queryKey: ["application", id],
    queryFn: async () =>
      unwrap(await api.GET("/api/pipeline/applications/{application_id}", { params: { path: { application_id: id } } })),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["application", id] });
    queryClient.invalidateQueries({ queryKey: ["pipeline-board"] });
  };

  const addEvent = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/pipeline/applications/{application_id}/events", {
          params: { path: { application_id: id } },
          body: { kind: eventKind, title: eventTitle },
        }),
      ),
    onSuccess: () => {
      setEventTitle("");
      invalidate();
    },
  });

  const addInterview = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/pipeline/applications/{application_id}/interviews", {
          params: { path: { application_id: id } },
          body: { kind: interviewKind, scheduled_at: interviewAt ? new Date(interviewAt).toISOString() : null },
        }),
      ),
    onSuccess: invalidate,
  });

  const setInterviewOutcome = useMutation({
    mutationFn: async ({ interviewId, outcome }: { interviewId: string; outcome: string }) =>
      unwrap(
        await api.PATCH("/api/pipeline/applications/{application_id}/interviews/{interview_id}", {
          params: { path: { application_id: id, interview_id: interviewId } },
          body: { outcome: outcome as never },
        }),
      ),
    onSuccess: invalidate,
  });

  const updateFollowUp = useMutation({
    mutationFn: async (clear: boolean) => {
      const body: Schemas["ApplicationUpdate"] = clear
        ? { clear_follow_up: true }
        : {
            clear_follow_up: false,
            next_follow_up_at: followUp ? new Date(followUp).toISOString() : null,
          };
      return unwrap(
        await api.PATCH("/api/pipeline/applications/{application_id}", {
          params: { path: { application_id: id } },
          body,
        }),
      );
    },
    onSuccess: invalidate,
  });

  if (app.isPending) return <Spinner />;
  if (app.isError) return <ErrorBox error={app.error} />;
  const a = app.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-bold">
          <Link href="/pipeline" className="text-ink-dim hover:text-accent">
            pipeline
          </Link>{" "}
          / {a.opportunity_title}
        </h1>
        <span className="flex items-center gap-2">
          <Badge tone="accent">{a.stage.replaceAll("_", " ")}</Badge>
          <ScoreRing score={a.score_overall} size="text-lg" />
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Application">
          <KeyValue
            items={[
              ["Company", a.company_name ?? "—"],
              ["Kind", a.kind],
              ["Applied", formatDate(a.applied_at)],
              ["Follow-up", a.next_follow_up_at ? formatDate(a.next_follow_up_at) : "—"],
              ["Closed", formatDate(a.closed_at)],
              [
                "Opportunity",
                <Link key="o" href={`/opportunities/${a.opportunity_id}`} className="text-accent">
                  open →
                </Link>,
              ],
              [
                "CV",
                a.cv_artifact_id ? (
                  <Link key="c" href={`/cv/${a.cv_artifact_id}`} className="text-accent">
                    artifact →
                  </Link>
                ) : (
                  "—"
                ),
              ],
            ]}
          />
          <div className="mt-3 space-y-2">
            <div className="label">Set follow-up</div>
            <div className="flex gap-2">
              <input type="datetime-local" className="input" value={followUp} onChange={(e) => setFollowUp(e.target.value)} />
              <button className="btn" onClick={() => updateFollowUp.mutate(false)} disabled={!followUp}>
                Set
              </button>
              <button className="btn" onClick={() => updateFollowUp.mutate(true)}>
                Clear
              </button>
            </div>
          </div>
        </Card>

        <Card title="Interviews">
          <ul className="space-y-2 text-sm">
            {(a.interviews ?? []).map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-2 rounded-lg border border-line p-2">
                <span>
                  {i.kind.replaceAll("_", " ")}
                  <span className="block text-xs text-ink-dim">{i.scheduled_at ? formatDate(i.scheduled_at) : "unscheduled"}</span>
                </span>
                <select
                  className="input w-auto px-1.5 py-0.5 text-[11px]"
                  value={i.outcome}
                  onChange={(e) => setInterviewOutcome.mutate({ interviewId: i.id, outcome: e.target.value })}
                >
                  {["pending", "passed", "failed", "canceled"].map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </select>
              </li>
            ))}
            {(a.interviews ?? []).length === 0 && <Empty>No interviews yet</Empty>}
          </ul>
          <div className="mt-3 flex gap-2">
            <select className="input w-auto" value={interviewKind} onChange={(e) => setInterviewKind(e.target.value as never)}>
              {INTERVIEW_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <input type="datetime-local" className="input" value={interviewAt} onChange={(e) => setInterviewAt(e.target.value)} />
            <button className="btn" onClick={() => addInterview.mutate()}>
              Add
            </button>
          </div>
        </Card>

        <Card title="Add to timeline">
          <div className="space-y-2">
            <select className="input" value={eventKind} onChange={(e) => setEventKind(e.target.value as never)}>
              {EVENT_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <input
              className="input"
              placeholder="What happened?"
              value={eventTitle}
              onChange={(e) => setEventTitle(e.target.value)}
            />
            <button className="btn btn-primary" onClick={() => addEvent.mutate()} disabled={!eventTitle.trim()}>
              Add event
            </button>
            {(addEvent.isError || addInterview.isError) && <ErrorBox error={addEvent.error ?? addInterview.error} />}
          </div>
        </Card>

        <Card title="Timeline" className="lg:col-span-3">
          <ul className="space-y-2">
            {(a.events ?? []).map((e) => (
              <li key={e.id} className="flex items-start gap-3 text-sm">
                <span className="w-24 shrink-0 pt-0.5 text-right text-xs text-ink-dim">{timeAgo(e.at)}</span>
                <Badge tone={e.kind === "stage_change" ? "accent" : e.kind === "offer" ? "good" : "neutral"}>
                  {e.kind.replaceAll("_", " ")}
                </Badge>
                <span>
                  {e.title}
                  {e.body && <span className="block text-xs text-ink-dim">{e.body}</span>}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
