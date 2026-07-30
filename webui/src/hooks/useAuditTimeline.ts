import { useCallback, useEffect, useState } from "react";

import { AuditApiError, fetchAuditEvents } from "@/lib/audit-api";
import type { AuditEventItem } from "@/lib/audit-types";

export function useAuditTimeline(token: string, traceId: string | null, enabled: boolean) {
  const [events, setEvents] = useState<AuditEventItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AuditApiError | null>(null);

  const load = useCallback(async (cursor: string | null = null) => {
    if (!traceId) return;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchAuditEvents(token, traceId, cursor);
      setEvents((current) => cursor ? [...current, ...page.items] : page.items);
      setNextCursor(page.next_cursor);
      setTotal(page.total);
    } catch (reason) {
      setError(reason instanceof AuditApiError
        ? reason
        : new AuditApiError(0, "network_error", String(reason)));
    } finally {
      setLoading(false);
    }
  }, [token, traceId]);

  useEffect(() => {
    setEvents([]);
    setNextCursor(null);
    setTotal(0);
    if (enabled && traceId) void load();
  }, [enabled, load, traceId]);

  return {
    events,
    total,
    nextCursor,
    loading,
    error,
    loadMore: () => nextCursor && load(nextCursor),
    refresh: () => load(),
  };
}
