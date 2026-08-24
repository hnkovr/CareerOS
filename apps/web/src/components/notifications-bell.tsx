"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Badge } from "@/components/ui";

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: async () => unwrap(await api.GET("/api/notifications")),
    refetchInterval: 60_000,
  });

  const count = notifications.data?.count ?? 0;
  const high = notifications.data?.high ?? 0;

  return (
    <div className="relative">
      <button className="btn relative" onClick={() => setOpen((v) => !v)} aria-label="Notifications">
        <Bell className="h-4 w-4" />
        {count > 0 && (
          <span
            className={`absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold ${
              high > 0 ? "bg-bad text-surface" : "bg-accent text-surface"
            }`}
          >
            {count}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-line bg-panel p-2 shadow-2xl">
            {count === 0 ? (
              <p className="p-3 text-center text-sm text-ink-dim">All clear — nothing needs you.</p>
            ) : (
              <ul className="max-h-96 space-y-1 overflow-y-auto">
                {(notifications.data?.items ?? []).map((n, i) => (
                  <li key={i}>
                    <Link
                      href={n.url_path}
                      className="block rounded-lg px-2.5 py-2 hover:bg-panel-2"
                      onClick={() => setOpen(false)}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm">{n.title}</span>
                        {n.severity === "high" && <Badge tone="bad">!</Badge>}
                      </span>
                      <span className="block text-[11px] text-ink-dim">
                        {n.detail ? `${n.detail} · ` : ""}
                        {n.at ? timeAgo(n.at) : ""}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
