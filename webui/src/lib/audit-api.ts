import { fetchWithTimeout } from "@/lib/http";
import type {
  AuditEventPage,
  AuditGraphResponse,
  AuditPayloadResponse,
  AuditSessionListResponse,
  AuditTraceFilters,
  AuditTraceListResponse,
} from "@/lib/audit-types";

const AUDIT_TIMEOUT_MS = 20_000;

export class AuditApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public retryable = false,
  ) {
    super(message);
    this.name = "AuditApiError";
  }
}

async function auditRequest<T>(
  url: string,
  token: string,
  init?: RequestInit,
): Promise<{ data: T; response: Response }> {
  const response = await fetchWithTimeout(
    url,
    {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${token}`,
      },
      credentials: "same-origin",
    },
    AUDIT_TIMEOUT_MS,
  );
  if (!response.ok) {
    let error = { code: "audit_request_failed", message: `HTTP ${response.status}`, retryable: false };
    try {
      const body = await response.json() as { error?: typeof error };
      if (body.error) error = body.error;
    } catch {
      // Preserve the bounded generic error.
    }
    throw new AuditApiError(response.status, error.code, error.message, error.retryable);
  }
  return { data: await response.json() as T, response };
}

export async function listAuditTraces(
  token: string,
  filters: AuditTraceFilters,
  cursor: string | null = null,
  sessionKey: string | null = null,
): Promise<AuditTraceListResponse> {
  const query = new URLSearchParams();
  if (filters.query.trim()) query.set("query", filters.query.trim());
  if (filters.since) query.set("since", `${filters.since}T00:00:00Z`);
  if (filters.until) query.set("until", `${filters.until}T23:59:59.999Z`);
  if (filters.status !== "all") query.set("status", filters.status);
  if (filters.anomaliesOnly) query.set("anomalies_only", "true");
  if (filters.sourceType.trim()) query.set("source_type", filters.sourceType.trim());
  if (filters.model.trim()) query.set("model", filters.model.trim());
  if (filters.tool.trim()) query.set("tool", filters.tool.trim());
  if (cursor) query.set("cursor", cursor);
  if (sessionKey) query.set("session_key", sessionKey);
  return (await auditRequest<AuditTraceListResponse>(`/api/audit/traces?${query}`, token)).data;
}

export async function listAuditSessions(
  token: string,
  queryValue = "",
  cursor: string | null = null,
): Promise<AuditSessionListResponse> {
  const query = new URLSearchParams();
  if (queryValue.trim()) query.set("query", queryValue.trim());
  if (cursor) query.set("cursor", cursor);
  return (await auditRequest<AuditSessionListResponse>(`/api/audit/sessions?${query}`, token)).data;
}

export async function fetchAuditGraph(
  token: string,
  traceId: string,
  runId: string | null,
  etag?: string | null,
): Promise<{ status: "ok"; data: AuditGraphResponse; etag: string | null } | { status: "not_modified" }> {
  const query = new URLSearchParams({ level: runId ? "run" : "trace_full" });
  if (runId) query.set("run_id", runId);
  const response = await fetchWithTimeout(
    `/api/audit/traces/${encodeURIComponent(traceId)}/graph?${query}`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        ...(etag ? { "If-None-Match": etag } : {}),
      },
      credentials: "same-origin",
    },
    AUDIT_TIMEOUT_MS,
  );
  if (response.status === 304) return { status: "not_modified" };
  if (!response.ok) {
    const body = await response.json() as { error?: { code: string; message: string; retryable: boolean } };
    throw new AuditApiError(
      response.status,
      body.error?.code ?? "audit_graph_failed",
      body.error?.message ?? `HTTP ${response.status}`,
      body.error?.retryable,
    );
  }
  return {
    status: "ok",
    data: await response.json() as AuditGraphResponse,
    etag: response.headers.get("ETag"),
  };
}

export async function fetchAuditEvents(
  token: string,
  traceId: string,
  cursor?: string | null,
): Promise<AuditEventPage> {
  const query = new URLSearchParams();
  if (cursor) query.set("cursor", cursor);
  return (await auditRequest<AuditEventPage>(
    `/api/audit/traces/${encodeURIComponent(traceId)}/events?${query}`,
    token,
  )).data;
}

export async function fetchAuditPayload(
  token: string,
  payloadId: string,
): Promise<AuditPayloadResponse> {
  return (await auditRequest<AuditPayloadResponse>(
    `/api/audit/payloads/${encodeURIComponent(payloadId)}`,
    token,
    { cache: "no-store" },
  )).data;
}
