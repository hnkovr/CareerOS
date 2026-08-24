"use client";

import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { diffLines, scoreColor } from "@/lib/format";

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const badgeTones: Record<string, string> = {
  neutral: "border-line text-ink-dim",
  good: "border-good/40 text-good",
  warn: "border-warn/40 text-warn",
  bad: "border-bad/40 text-bad",
  accent: "border-accent/40 text-accent",
  violet: "border-accent-2/40 text-accent-2",
};

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof badgeTones; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${badgeTones[tone] ?? badgeTones.neutral}`}>
      {children}
    </span>
  );
}

export function severityTone(severity: string): keyof typeof badgeTones {
  if (severity === "critical") return "bad";
  if (severity === "high") return "warn";
  if (severity === "medium") return "accent";
  return "neutral";
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-ink-dim">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="rounded-lg border border-bad/40 bg-bad/10 p-3 text-sm text-bad">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-ink-dim">{children}</div>;
}

export function ScoreRing({ score, size = "text-2xl" }: { score: number | null | undefined; size?: string }) {
  return <span className={`font-bold tabular-nums ${size} ${scoreColor(score)}`}>{score ?? "—"}</span>;
}

export function ScoreBar({ name, score, weight, explanation }: { name: string; score: number; weight?: number; explanation?: string }) {
  return (
    <div className="space-y-0.5" title={explanation}>
      <div className="flex justify-between text-xs">
        <span className="text-ink-dim">
          {name.replaceAll("_", " ")}
          {weight != null && weight > 0 && <span className="ml-1 opacity-60">×{weight.toFixed(2)}</span>}
        </span>
        <span className={`tabular-nums ${scoreColor(score)}`}>{score}</span>
      </div>
      <div className="h-1.5 rounded bg-panel-2">
        <div
          className={`h-1.5 rounded ${score >= 80 ? "bg-good" : score >= 60 ? "bg-warn" : "bg-bad"}`}
          style={{ width: `${Math.max(2, score)}%` }}
        />
      </div>
      {explanation && <p className="text-[11px] leading-4 text-ink-dim/80">{explanation}</p>}
    </div>
  );
}

export function Diff({ diff }: { diff: string }) {
  if (!diff.trim()) return <Empty>No changes</Empty>;
  return (
    <pre className="diff">
      {diffLines(diff).map((line, i) => (
        <div key={i} className={line.kind === "ctx" ? "" : line.kind}>
          {line.text || " "}
        </div>
      ))}
    </pre>
  );
}

export function KeyValue({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-dim">{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
