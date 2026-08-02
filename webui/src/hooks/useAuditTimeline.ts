import { useCallback, useEffect, useRef, useState } from "react";

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
  const eventsRef = useRef<AuditEventItem[]>([]);
  const cursorRef = useRef<string | null>(null);
  const revisionRef = useRef<number | null>(null);
  const requestEpoch = useRef(0);
  const pageRequests = useRef(new Map<string, Promise<Awaited<ReturnType<typeof fetchAuditEvents>>>>());

  const fetchPage = useCallback((cursor: string | null) => {
    const key = cursor ?? "__first__";
    const existing = pageRequests.current.get(key);
    if (existing) return existing;
    const request = fetchAuditEvents(token, traceId!, cursor).finally(() => {
      pageRequests.current.delete(key);
    });
    pageRequests.current.set(key, request);
    return request;
  }, [token, traceId]);

  const load = useCallback(async (cursor: string | null = null) => {
    if (!traceId) return;
    const epoch = ++requestEpoch.current;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchPage(cursor);
      if (epoch !== requestEpoch.current) return;
      const nextEvents = cursor ? appendUnique(eventsRef.current, page.items) : page.items;
      eventsRef.current = nextEvents;
      cursorRef.current = page.next_cursor;
      revisionRef.current = page.index.revision;
      setEvents(nextEvents);
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
  }, [fetchPage, traceId]);

  const ensureEvent = useCallback(async (eventId: string): Promise<AuditLocateResult> => {
    if (!traceId) return "not_found";
    if (eventsRef.current.some((event) => event.event_id === eventId)) return "found";
    let collected = eventsRef.current;
    let cursor = cursorRef.current;
    let currentRevision = revisionRef.current;
    let pages = 0;
    const startedAt = Date.now();
    setLoading(true);
    setError(null);
    try {
      // A locate can race the timeline's initial effect. Load that first page
      // here so it never decides "not found" from an empty stale closure.
      if (!collected.length && !cursor) {
        const page = await fetchPage(null);
        if (currentRevision !== null && page.index.revision !== currentRevision) {
          return "revision_mismatch";
        }
        currentRevision = page.index.revision;
        collected = appendUnique([], page.items);
        cursor = page.next_cursor;
        pages += 1;
        eventsRef.current = collected;
        cursorRef.current = cursor;
        revisionRef.current = currentRevision;
        setEvents(collected);
        setNextCursor(cursor);
        setTotal(page.total);
        setRevision(currentRevision);
        if (collected.some((event) => event.event_id === eventId)) return "found";
      }
      while (
        cursor
        && pages < LOCATE_MAX_PAGES
        && collected.length < LOCATE_MAX_EVENTS
        && Date.now() - startedAt < LOCATE_TIMEOUT_MS
      ) {
        const page = await fetchPage(cursor);
        if (currentRevision !== null && page.index.revision !== currentRevision) {
          return "revision_mismatch";
        }
        collected = appendUnique(collected, page.items).slice(0, LOCATE_MAX_EVENTS);
        currentRevision = page.index.revision;
        cursor = page.next_cursor;
        pages += 1;
        eventsRef.current = collected;
        cursorRef.current = cursor;
        revisionRef.current = currentRevision;
        setEvents(collected);
        setNextCursor(cursor);
        setTotal(page.total);
        setRevision(currentRevision);
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
  }, [fetchPage, traceId]);

  useEffect(() => {
    setEvents([]);
    setNextCursor(null);
    setTotal(0);
    setRevision(null);
    eventsRef.current = [];
    cursorRef.current = null;
    revisionRef.current = null;
    requestEpoch.current += 1;
    pageRequests.current.clear();
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
