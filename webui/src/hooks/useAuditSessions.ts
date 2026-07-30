import { useCallback, useEffect, useState } from "react";

import { AuditApiError, listAuditSessions } from "@/lib/audit-api";
import type { AuditIndexStatus, AuditSessionListItem } from "@/lib/audit-types";

export function useAuditSessions(token: string) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<AuditSessionListItem[]>([]);
  const [index, setIndex] = useState<AuditIndexStatus | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<AuditApiError | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      listAuditSessions(token, query).then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setIndex(page.index);
        setNextCursor(page.next_cursor);
      }).catch((reason) => {
        if (!cancelled) setError(reason instanceof AuditApiError
          ? reason : new AuditApiError(0, "network_error", String(reason)));
      }).finally(() => { if (!cancelled) setLoading(false); });
    }, query ? 220 : 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [nonce, query, token]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listAuditSessions(token, query, nextCursor);
      setItems((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
      setIndex(page.index);
    } catch (reason) {
      setError(reason instanceof AuditApiError
        ? reason : new AuditApiError(0, "network_error", String(reason)));
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, nextCursor, query, token]);

  return { query, setQuery, items, index, nextCursor, loading, loadingMore, error, refresh, loadMore };
}
