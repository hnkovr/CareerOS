export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-GB", { year: "numeric", month: "short", day: "numeric" });
}

export function timeAgo(value: string | null | undefined): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return value;
  const seconds = Math.round((Date.now() - then) / 1000);
  const units: Array<[number, string]> = [
    [60, "s"],
    [60, "m"],
    [24, "h"],
    [7, "d"],
    [4.345, "w"],
    [12, "mo"],
  ];
  let val = seconds;
  let unit = "s";
  for (const [step, label] of units) {
    if (val < step) break;
    val = Math.floor(val / step);
    unit = label === "s" ? "m" : label;
  }
  return seconds < 60 ? "just now" : `${val}${unit} ago`;
}

export function scoreColor(score: number | null | undefined): string {
  if (score == null) return "text-ink-dim";
  if (score >= 80) return "text-good";
  if (score >= 60) return "text-warn";
  return "text-bad";
}

export function recommendationLabel(rec: string | null | undefined): string {
  if (!rec) return "—";
  return rec.replaceAll("_", " ").toUpperCase();
}

export function truncate(text: string, max = 120): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** Render a unified diff as HTML-safe spans (class per line kind). */
export function diffLines(diff: string): Array<{ kind: "add" | "del" | "hunk" | "ctx"; text: string }> {
  return diff.split("\n").map((line) => {
    if (line.startsWith("+") && !line.startsWith("+++")) return { kind: "add" as const, text: line };
    if (line.startsWith("-") && !line.startsWith("---")) return { kind: "del" as const, text: line };
    if (line.startsWith("@@")) return { kind: "hunk" as const, text: line };
    return { kind: "ctx" as const, text: line };
  });
}
