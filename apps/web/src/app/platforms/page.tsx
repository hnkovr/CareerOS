"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "@/lib/api";
import { formatDate, timeAgo } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";
import { ObservationsPanel, PastePanel, SyncRunsPanel, SYNC_STATUS_TONE } from "./panels";

type Capabilities = Schemas["Capabilities"];
type ConnectionOut = Schemas["ConnectionOut"];
type DoctorCheck = Schemas["DoctorCheck"];
type PlatformUrls = Schemas["PlatformUrls"];
type Platform = Schemas["Platform"];
type SyncKind = Schemas["SyncKind"];
type SyncResult = Schemas["SyncResult"];
type Tone = "good" | "warn" | "bad" | "accent" | "violet" | "neutral";

const ACCESS_TONE: Record<string, Tone> = {
  public: "good",
  authenticated_user_api: "accent",
  manual_import: "warn",
  unsupported: "neutral",
};
const STATUS_TONE: Record<string, Tone> = {
  connected: "good",
  needs_reauth: "warn",
  error: "bad",
  disconnected: "neutral",
};
const METHOD_TONE: Record<string, Tone> = { api: "good", export: "accent", paste: "violet" };
const CAPABILITY_KINDS: Array<{ kind: SyncKind; label: string }> = [
  { kind: "profile", label: "Own profile" },
  { kind: "jobs", label: "Job search" },
  { kind: "applications", label: "Application statuses" },
];

function methodsOf(caps: Capabilities, kind: SyncKind): string[] {
  if (kind === "profile") return caps.profile ?? [];
  if (kind === "jobs") return caps.jobs ?? [];
  if (kind === "applications") return caps.applications ?? [];
  return [];
}

/** The fallback reader: the connector that claims ANY url at low confidence, rather than a board
 * the owner browses. The API is adding `Capabilities.fallback` for exactly this; until it ships we
 * approximate it (today only `website` matches, and real boards like rockethunt/justjoin will be
 * indistinguishable on these fields — hence the flag). Read it defensively so the page becomes
 * correct the moment the field appears, with no second deploy. */
function isGenericReader(caps: Capabilities): boolean {
  const flagged = (caps as { fallback?: boolean }).fallback;
  if (typeof flagged === "boolean") return flagged;
  return (
    caps.auth === "none" &&
    caps.read_one &&
    (caps.profile ?? []).length === 0 &&
    (caps.applications ?? []).length === 0
  );
}

function Methods({ methods }: { methods: string[] }) {
  if (methods.length === 0) return <span className="text-ink-dim">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {methods.map((m) => (
        <Badge key={m} tone={METHOD_TONE[m] ?? "neutral"}>
          {m}
        </Badge>
      ))}
    </span>
  );
}

function PlatformCard({ caps, connection }: { caps: Capabilities; connection: ConnectionOut | undefined }) {
  const queryClient = useQueryClient();
  const [checks, setChecks] = useState<DoctorCheck[] | null>(null);
  const [urls, setUrls] = useState<PlatformUrls | null>(null);
  const [synced, setSynced] = useState<SyncResult | null>(null);
  const platform = caps.platform;
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["platform-connections"] });
    queryClient.invalidateQueries({ queryKey: ["platform-runs"] });
  };

  const connect = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/platform/{platform}/connect", { params: { path: { platform } } })),
    onSuccess: (out) => window.open(out.authorize_url, "_blank", "noopener,noreferrer"),
  });
  const refresh = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/platform/{platform}/refresh", { params: { path: { platform } } })),
    onSuccess: invalidate,
  });
  const disconnect = useMutation({
    mutationFn: async () =>
      unwrap(await api.DELETE("/api/platform/{platform}/connection", { params: { path: { platform } } })),
    onSuccess: invalidate,
  });
  const doctor = useMutation({
    mutationFn: async () =>
      unwrap(await api.GET("/api/platform/{platform}/doctor", { params: { path: { platform } } })),
    onSuccess: setChecks,
  });
  const links = useMutation({
    mutationFn: async () =>
      unwrap(await api.GET("/api/platform/{platform}/urls", { params: { path: { platform }, query: {} } })),
    onSuccess: setUrls,
  });
  const sync = useMutation({
    mutationFn: async (kind: SyncKind) =>
      unwrap(
        await api.POST("/api/platform/{platform}/sync/{kind}", {
          params: { path: { platform, kind } },
          body: {
            method: null,
            text: null,
            file_path: null,
            query: null,
            use_ai: false,
            provider: null,
            dry_run: false,
          },
        }),
      ),
    onSuccess: (data) => {
      setSynced(data);
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["platform-observations"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });

  const needsAuth = caps.auth !== "none";
  const connected = connection?.status === "connected";
  const apiKinds = CAPABILITY_KINDS.filter(({ kind }) => methodsOf(caps, kind).includes("api"));
  const busy = connect.isPending || refresh.isPending || disconnect.isPending || sync.isPending;
  const error = connect.error ?? refresh.error ?? disconnect.error ?? doctor.error ?? links.error ?? sync.error;

  return (
    <Card
      title={platform}
      action={
        <div className="flex items-center gap-1.5">
          <Badge tone={ACCESS_TONE[caps.access] ?? "neutral"}>{caps.access.replaceAll("_", " ")}</Badge>
          {needsAuth && (
            <Badge tone={STATUS_TONE[connection?.status ?? "disconnected"] ?? "neutral"}>
              {(connection?.status ?? "disconnected").replaceAll("_", " ")}
            </Badge>
          )}
          {caps.official_api && <Badge tone="good">official API</Badge>}
          {caps.read_one && <Badge tone="accent">read one job</Badge>}
        </div>
      }
    >
      <div className="space-y-2.5 text-xs">
        {caps.notes && <p className="text-ink-dim">{caps.notes}</p>}
        <table className="w-full">
          <tbody>
            {CAPABILITY_KINDS.map(({ kind, label }) => (
              <tr key={kind}>
                <td className="w-40 py-0.5 text-ink-dim">{label}</td>
                <td className="py-0.5">
                  <Methods methods={methodsOf(caps, kind)} />
                </td>
              </tr>
            ))}
            {(caps.read_job ?? []).length > 0 && (
              <tr>
                <td className="py-0.5 text-ink-dim">One job by URL</td>
                <td className="py-0.5">
                  <Methods methods={caps.read_job ?? []} />
                </td>
              </tr>
            )}
          </tbody>
        </table>

        {connection && (connection.account_label || connection.token_expires_at || connection.last_sync_at) && (
          <p className="text-ink-dim">
            {connection.account_label ?? connection.account_id}
            {connection.pinned && " · token pinned from env"}
            {connection.token_expires_at && ` · token expires ${formatDate(connection.token_expires_at)}`}
            {connection.last_sync_at && ` · last sync ${timeAgo(connection.last_sync_at)}`}
          </p>
        )}
        {connection?.last_error && <p className="text-bad">{connection.last_error}</p>}

        <div className="flex flex-wrap gap-1.5">
          {needsAuth && !connected && (
            <button className="btn" onClick={() => connect.mutate()} disabled={busy}>
              {connect.isPending ? "Opening…" : "Connect"}
            </button>
          )}
          {connected && caps.auth === "oauth2" && (
            <button className="btn" onClick={() => refresh.mutate()} disabled={busy}>
              Refresh token
            </button>
          )}
          {connected &&
            apiKinds.map(({ kind, label }) => (
              <button key={kind} className="btn" onClick={() => sync.mutate(kind)} disabled={busy}>
                {sync.isPending && sync.variables === kind ? "Syncing…" : `Sync ${label.toLowerCase()}`}
              </button>
            ))}
          <button className="btn" onClick={() => doctor.mutate()} disabled={doctor.isPending}>
            Doctor
          </button>
          <button className="btn" onClick={() => links.mutate()} disabled={links.isPending}>
            Links
          </button>
          {connection?.has_tokens && (
            <button className="btn" onClick={() => disconnect.mutate()} disabled={busy} title="delete the local token">
              Disconnect
            </button>
          )}
        </div>

        {error != null && <ErrorBox error={error} />}
        {synced && (
          <p>
            <Badge tone={SYNC_STATUS_TONE[synced.status] ?? "neutral"}>{synced.status}</Badge>{" "}
            <span className="text-ink-dim">
              {synced.kind} · {synced.items_created} new · {synced.items_updated} updated
              {synced.message ? ` · ${synced.message}` : ""}
            </span>
          </p>
        )}
        {urls && (
          <p className="flex flex-wrap gap-3">
            {urls.search_url ? (
              <a className="text-accent hover:underline" href={urls.search_url} target="_blank" rel="noreferrer">
                open job search ↗
              </a>
            ) : (
              <span className="text-ink-dim">no search URL</span>
            )}
            {urls.profile_url ? (
              <a className="text-accent hover:underline" href={urls.profile_url} target="_blank" rel="noreferrer">
                open my profile ↗
              </a>
            ) : (
              <span className="text-ink-dim">own profile URL unknown</span>
            )}
          </p>
        )}
        {checks && (
          <ul className="space-y-0.5">
            {checks.length === 0 && <li className="text-ink-dim">no checks for this platform</li>}
            {checks.map((c) => (
              <li key={c.name}>
                <Badge tone={c.ok ? "good" : "bad"}>{c.ok ? "ok" : "fix"}</Badge> <span className="font-mono">{c.name}</span>{" "}
                <span className="text-ink-dim">{c.detail}</span>
                {!c.ok && c.fix && <span className="text-warn"> → {c.fix}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

export default function PlatformsPage() {
  const queryClient = useQueryClient();
  const capabilities = useQuery({
    queryKey: ["platform-capabilities"],
    queryFn: async () => unwrap(await api.GET("/api/platform/capabilities")),
  });
  const connections = useQuery({
    queryKey: ["platform-connections"],
    queryFn: async () => unwrap(await api.GET("/api/platform/connections")),
  });
  const syncAll = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/platform/sync-all", { params: { query: { dry_run: false } } })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platform-connections"] });
      queryClient.invalidateQueries({ queryKey: ["platform-runs"] });
      queryClient.invalidateQueries({ queryKey: ["platform-observations"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });

  const byPlatform = new Map<Platform, ConnectionOut>((connections.data ?? []).map((c) => [c.platform, c]));
  const caps = capabilities.data ?? [];
  const accounts = caps.filter((c) => !isGenericReader(c));
  const readers = caps.filter(isGenericReader);
  const connectable = caps.filter((c) => c.auth !== "none").length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">Platforms</h1>
          <p className="text-sm text-ink-dim">
            Read-only connectors: your own profile, job search and application statuses — through an official API where
            one exists, an official export where it does not, and pasting everywhere else. No scraping, no passwords, no
            auto-apply; OAuth tokens are yours, stay on this machine and can be revoked here.
          </p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => syncAll.mutate()}
          disabled={syncAll.isPending || connectable === 0}
          title="sync every connected platform; not-connected ones are skipped"
        >
          {syncAll.isPending ? "Syncing…" : "Sync all"}
        </button>
      </div>
      {syncAll.isError && <ErrorBox error={syncAll.error} />}
      {syncAll.isSuccess && (
        <Card>
          <div className="flex flex-wrap gap-2 text-xs">
            {syncAll.data.map((r, i) => (
              <span key={`${r.platform}-${r.kind}-${i}`} className="flex items-center gap-1">
                <Badge tone={SYNC_STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
                <span className="text-ink-dim">
                  {r.platform}/{r.kind}
                  {r.message ? ` — ${r.message}` : ""}
                </span>
              </span>
            ))}
          </div>
        </Card>
      )}

      {capabilities.isPending ? (
        <Spinner />
      ) : capabilities.isError ? (
        <ErrorBox error={capabilities.error} />
      ) : caps.length === 0 ? (
        <Card>
          <Empty>No connectors registered.</Empty>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          {accounts.map((c) => (
            <PlatformCard key={c.platform} caps={c} connection={byPlatform.get(c.platform)} />
          ))}
        </div>
      )}

      {readers.length > 0 && (
        <Card title="Generic readers">
          <p className="pb-2 text-xs text-ink-dim">
            Not a service you have an account on — the fallback reader for any employer or ATS page you point at.
            Nothing to connect and nothing of yours to sync; it reads a single job behind a URL you provide, and you can
            still paste a job page for it below.
          </p>
          <ul className="space-y-2 text-xs">
            {readers.map((c) => (
              <li key={c.platform} className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{c.platform}</span>
                <Badge tone="neutral">generic reader — no account</Badge>
                <Methods methods={c.read_job ?? []} />
                {c.notes && <span className="text-ink-dim">{c.notes}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <PastePanel capabilities={caps} />
        <SyncRunsPanel />
        <ObservationsPanel />
      </div>
    </div>
  );
}
