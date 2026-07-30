import { useCallback, useEffect, useState } from "react";

import { AuditApiError, listAuditTraces } from "@/lib/audit-api";
import type {
  AuditIndexStatus,
  AuditTraceFilters,
  AuditTraceListItem,
} from "@/lib/audit-types";

export const DEFAULT_TRACE_FILTERS: AuditTraceFilters = {
  query: "",
  since: "",
  until: "",
  status: "all",
  anomaliesOnly: false,
  sourceType: "",
  model: "",
  tool: "",
};

export function useAuditTraces(token: string) {
  const [filters, setFilters] = useState<AuditTraceFilters>(DEFAULT_TRACE_FILTERS);
  const [items, setItems] = useState<AuditTraceListItem[]>([]);
  const [index, setIndex] = useState<AuditIndexStatus | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<AuditApiError | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);
  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listAuditTraces(token, filters, nextCursor);
      setItems((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
      setIndex(page.index);
    } catch (reason) {
      const apiError = reason instanceof AuditApiError
        ? reason
        : new AuditApiError(0, "network_error", String(reason));
      if (apiError.code === "cursor_stale") refresh();
      else setError(apiError);
    } finally {
      setLoadingMore(false);
    }
  }, [filters, loadingMore, nextCursor, refresh, token]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      listAuditTraces(token, filters)
        .then((page) => {
          if (cancelled) return;
          setItems(page.items);
          setNextCursor(page.next_cursor);
          setIndex(page.index);
        })
        .catch((reason) => {
          if (cancelled) return;
          setError(reason instanceof AuditApiError
            ? reason
            : new AuditApiError(0, "network_error", String(reason)));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, filters.query ? 220 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [filters, nonce, token]);

  return {
    filters,
    setFilters,
    items,
    index,
    nextCursor,
    loading,
    loadingMore,
    error,
    refresh,
    loadMore,
  };
}
