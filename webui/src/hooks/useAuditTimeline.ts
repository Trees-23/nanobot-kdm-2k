import { useCallback, useEffect, useState } from "react";

import { AuditApiError, fetchAuditEvents } from "@/lib/audit-api";
import type { AuditEventItem } from "@/lib/audit-types";

const LOCATE_MAX_PAGES = 5;
const LOCATE_MAX_EVENTS = 1_000;
const LOCATE_TIMEOUT_MS = 10_000;

export type AuditLocateResult = "found" | "not_found" | "limit" | "cursor_stale" | "revision_mismatch" | "error";

function appendUnique(current: AuditEventItem[], incoming: AuditEventItem[]): AuditEventItem[] {
  const known = new Set(current.map((event) => event.event_id));
  return [...current, ...incoming.filter((event) => !known.has(event.event_id))];
}

export function useAuditTimeline(token: string, traceId: string | null, enabled: boolean) {
  const [events, setEvents] = useState<AuditEventItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [revision, setRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AuditApiError | null>(null);

  const load = useCallback(async (cursor: string | null = null) => {
    if (!traceId) return;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchAuditEvents(token, traceId, cursor);
      setEvents((current) => cursor ? appendUnique(current, page.items) : page.items);
      setNextCursor(page.next_cursor);
      setTotal(page.total);
      setRevision(page.index.revision);
    } catch (reason) {
      setError(reason instanceof AuditApiError
        ? reason
        : new AuditApiError(0, "network_error", String(reason)));
    } finally {
      setLoading(false);
    }
  }, [token, traceId]);

  const ensureEvent = useCallback(async (eventId: string): Promise<AuditLocateResult> => {
    if (!traceId) return "not_found";
    if (events.some((event) => event.event_id === eventId)) return "found";
    let collected = events;
    let cursor = nextCursor;
    let pages = 0;
    const startedAt = Date.now();
    setLoading(true);
    setError(null);
    try {
      while (
        cursor
        && pages < LOCATE_MAX_PAGES
        && collected.length < LOCATE_MAX_EVENTS
        && Date.now() - startedAt < LOCATE_TIMEOUT_MS
      ) {
        const page = await fetchAuditEvents(token, traceId, cursor);
        if (revision !== null && page.index.revision !== revision) {
          return "revision_mismatch";
        }
        collected = appendUnique(collected, page.items).slice(0, LOCATE_MAX_EVENTS);
        cursor = page.next_cursor;
        pages += 1;
        setEvents(collected);
        setNextCursor(cursor);
        setTotal(page.total);
        if (collected.some((event) => event.event_id === eventId)) return "found";
      }
      if (!cursor) return "not_found";
      return "limit";
    } catch (reason) {
      const apiError = reason instanceof AuditApiError
        ? reason
        : new AuditApiError(0, "network_error", String(reason));
      setError(apiError);
      return apiError.code === "cursor_stale" ? "cursor_stale" : "error";
    } finally {
      setLoading(false);
    }
  }, [events, nextCursor, revision, token, traceId]);

  useEffect(() => {
    setEvents([]);
    setNextCursor(null);
    setTotal(0);
    setRevision(null);
    if (enabled && traceId) void load();
  }, [enabled, load, traceId]);

  return {
    events,
    total,
    revision,
    nextCursor,
    loading,
    error,
    loadMore: () => nextCursor && load(nextCursor),
    refresh: () => load(),
    ensureEvent,
  };
}
