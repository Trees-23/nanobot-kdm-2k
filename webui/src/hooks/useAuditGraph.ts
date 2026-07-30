import { useCallback, useEffect, useRef, useState } from "react";

import { AuditApiError, fetchAuditGraph } from "@/lib/audit-api";
import type { AuditGraphResponse } from "@/lib/audit-types";

export function useAuditGraph(
  token: string,
  traceId: string | null,
  runId: string | null,
) {
  const [graph, setGraph] = useState<AuditGraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<AuditApiError | null>(null);
  const etagRef = useRef<string | null>(null);
  const focusRef = useRef("");

  const load = useCallback(async (background = false) => {
    if (!traceId) return;
    const focus = `${traceId}\0${runId ?? ""}`;
    if (focusRef.current !== focus) {
      focusRef.current = focus;
      etagRef.current = null;
      setGraph(null);
    }
    if (background) setUpdating(true);
    else setLoading(true);
    setError(null);
    try {
      const result = await fetchAuditGraph(token, traceId, runId, etagRef.current);
      if (result.status === "ok") {
        etagRef.current = result.etag;
        setGraph(result.data);
      }
    } catch (reason) {
      setError(reason instanceof AuditApiError
        ? reason
        : new AuditApiError(0, "network_error", String(reason)));
    } finally {
      setLoading(false);
      setUpdating(false);
    }
  }, [runId, token, traceId]);

  useEffect(() => {
    if (!traceId) {
      setGraph(null);
      return;
    }
    void load(false);
  }, [load, traceId]);

  useEffect(() => {
    if (!graph?.trace.active) return;
    const timer = window.setInterval(() => void load(true), 2_500);
    return () => window.clearInterval(timer);
  }, [graph?.trace.active, load]);

  return { graph, loading, updating, error, refresh: () => load(false) };
}
