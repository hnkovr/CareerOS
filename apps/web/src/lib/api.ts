"use client";

import { createApiClient } from "@careeros/api-client";
import type { components } from "@careeros/schemas/api";

export const api = createApiClient({ baseUrl: "" });

export type Schemas = components["schemas"];
export type VaultStatus = Schemas["VaultStatus"];
export type IssueOut = Schemas["IssueOut"];
export type ChangePreview = Schemas["ChangePreview"];
export type OpportunityOut = Schemas["OpportunityOut"];
export type OpportunityDetail = Schemas["OpportunityDetail"];
export type ScoreOut = Schemas["ScoreOut"];
export type DimensionScore = Schemas["DimensionScore"];
export type CVArtifactOut = Schemas["CVArtifactOut"];
export type CVDocument = Schemas["CVDocument"];
export type VariantOut = Schemas["VariantOut"];
export type CVComparison = Schemas["CVComparison"];
export type SnapshotOut = Schemas["SnapshotOut"];
export type AuditOut = Schemas["AuditOut"];
export type FindingOut = Schemas["FindingOut"];
export type PlatformHealth = Schemas["PlatformHealth"];
export type BundleOut = Schemas["BundleOut"];
export type InterviewPrepOut = Schemas["InterviewPrepOut"];
export type NegotiationOut = Schemas["NegotiationOut"];
export type CompareOut = Schemas["CompareOut"];
export type AskResponse = Schemas["AskResponse"];
export type AIRunOut = Schemas["AIRunOut"];
export type ProviderInfo = Schemas["ProviderInfo"];

/** Unwrap an openapi-fetch result or throw a readable error. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined) {
    const detail =
      typeof result.error === "object" && result.error !== null && "detail" in result.error
        ? JSON.stringify((result.error as { detail: unknown }).detail)
        : String(result.error);
    throw new Error(`${result.response.status}: ${detail}`);
  }
  return result.data as T;
}
