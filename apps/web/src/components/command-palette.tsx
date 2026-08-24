"use client";

import { Command } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type CommandItem = { label: string; hint?: string; run: () => void };

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<CommandItem[]>(
    () => [
      { label: "Go to Dashboard", run: () => router.push("/") },
      { label: "Search everything", hint: "FTS + semantic", run: () => router.push("/search") },
      { label: "Go to Vault", run: () => router.push("/vault") },
      { label: "Go to Opportunities", run: () => router.push("/opportunities") },
      { label: "Add opportunity (paste JD)", hint: "ingest", run: () => router.push("/opportunities?new=1") },
      { label: "Go to CV", run: () => router.push("/cv") },
      { label: "Generate CV", hint: "variants", run: () => router.push("/cv?generate=1") },
      { label: "Review AI suggestions", hint: "approve / reject", run: () => router.push("/suggestions") },
      { label: "Go to Profiles", run: () => router.push("/profiles") },
      { label: "Add profile snapshot", run: () => router.push("/profiles?new=1") },
      { label: "API docs (OpenAPI)", hint: "backend", run: () => window.open("/api/../docs", "_blank") },
    ],
    [router],
  );

  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim();
    return q ? commands.filter((c) => c.label.toLowerCase().includes(q)) : commands;
  }, [commands, query]);

  const onKey = useCallback((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      setOpen((v) => !v);
      setQuery("");
    }
    if (e.key === "Escape") setOpen(false);
  }, []);

  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  return (
    <>
      <button className="btn" onClick={() => setOpen(true)} aria-label="Command palette">
        <Command className="h-3.5 w-3.5" /> <span className="hidden sm:inline">⌘K</span>
      </button>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/60 p-4 pt-24" onClick={() => setOpen(false)}>
          <div
            className="mx-auto max-w-lg overflow-hidden rounded-xl border border-line bg-panel shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              ref={inputRef}
              className="w-full border-b border-line bg-transparent px-4 py-3 text-sm outline-none placeholder:text-ink-dim"
              placeholder="Type a command…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && filtered[0]) {
                  filtered[0].run();
                  setOpen(false);
                }
              }}
            />
            <ul className="max-h-80 overflow-y-auto p-1">
              {filtered.map((c) => (
                <li key={c.label}>
                  <button
                    className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm hover:bg-panel-2"
                    onClick={() => {
                      c.run();
                      setOpen(false);
                    }}
                  >
                    <span>{c.label}</span>
                    {c.hint && <span className="text-xs text-ink-dim">{c.hint}</span>}
                  </button>
                </li>
              ))}
              {filtered.length === 0 && <li className="px-3 py-4 text-sm text-ink-dim">No matches</li>}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
