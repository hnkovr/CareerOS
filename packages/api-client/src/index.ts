/** Typed API client over the generated OpenAPI types (ADR-007: clients hold no business logic). */
import createClient, { type Client } from "openapi-fetch";
import type { paths } from "@careeros/schemas/api";

export type ApiClient = Client<paths>;
export type { paths } from "@careeros/schemas/api";
export type { components } from "@careeros/schemas/api";

export interface ApiClientOptions {
  /** Origin of the API; spec paths already start with /api. Empty = same origin (Next rewrites). */
  baseUrl?: string;
  /** Bearer token when CAREEROS_API_TOKEN protects the API. */
  token?: string;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const { baseUrl = "", token } = options;
  return createClient<paths>({
    baseUrl,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
}
