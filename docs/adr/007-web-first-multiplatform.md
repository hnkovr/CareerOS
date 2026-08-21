# 007 — Web first; iOS (Expo) and macOS (Tauri) reuse the API contract and shared UI

* Status: accepted
* Date: 2026-08-20

## Context

The brief wants web (P0), iOS (P2) and macOS (P2) with maximal reuse. Business logic must not be
reimplemented per client.

## Decision

* **API-first**: the FastAPI service exposes OpenAPI; `packages/api-client` and TS types are
  generated in CI (`openapi-typescript`). Clients contain no business rules — only presentation,
  caching (TanStack Query) and device integration.
* **Web (P0)**: Next.js App Router + TypeScript + Tailwind + shadcn/ui, PWA manifest; served by the
  compose stack. UI components start inside `apps/web` and are extracted to `packages/ui` when the
  second React client appears.
* **macOS (P2)**: Tauri v2 wrapping the web UI, adding clipboard, file/vault access, local dev-agent
  launch, notifications, menu bar. OS Keychain for credentials.
* **iOS (P2)**: Expo/React Native focused on triage, inbox, approve/reject, notifications and
  Share-Sheet capture; reuses API client, schemas and design tokens (NativeWind or tokens → StyleSheet).

## Alternatives considered

* **Flutter / native Swift** — no sharing with the web codebase.
* **Electron for desktop** — heavier than Tauri, weaker OS integration.
* **Mobile-first** — most P0 work (vault editing, CV comparison) is desktop-shaped.

## Consequences

* + One backend, one contract, generated clients; no logic drift.
* − Three client toolchains eventually; mitigated by deferring two of them to P2 and sharing packages.
