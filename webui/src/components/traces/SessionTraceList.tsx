import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  Search,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DEFAULT_TRACE_FILTERS } from "@/hooks/useAuditTraces";
import { listAuditTraces } from "@/lib/audit-api";
import { auditStatusLabel } from "@/lib/audit-display";
import type { AuditIndexStatus, AuditSessionListItem, AuditTraceListItem } from "@/lib/audit-types";
import { cn } from "@/lib/utils";

export function SessionTraceList({
  token,
  sessions,
  index,
  query,
  selectedTraceId,
  selectedSessionKey,
  loading,
  loadingMore,
  hasMore,
  onQueryChange,
  onSelectTrace,
  onLoadMore,
}: {
  token: string;
  sessions: AuditSessionListItem[];
  index: AuditIndexStatus | null;
  query: string;
  selectedTraceId: string | null;
  selectedSessionKey: string | null;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  onQueryChange: (value: string) => void;
  onSelectTrace: (trace: AuditTraceListItem) => void;
  onLoadMore: () => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [traces, setTraces] = useState<Record<string, AuditTraceListItem[]>>({});
  const [nextCursors, setNextCursors] = useState<Record<string, string | null>>({});
  const [loadingSession, setLoadingSession] = useState<Set<string>>(new Set());

  const loadSession = async (session: AuditSessionListItem) => {
    if (traces[session.session_key]) return traces[session.session_key];
    if (loadingSession.has(session.session_key)) return [];
    setLoadingSession((current) => new Set(current).add(session.session_key));
    try {
      const page = await listAuditTraces(
        token,
        DEFAULT_TRACE_FILTERS,
        null,
        session.session_key,
      );
      setTraces((current) => ({ ...current, [session.session_key]: page.items }));
      setNextCursors((current) => ({
        ...current,
        [session.session_key]: page.next_cursor,
      }));
      return page.items;
    } catch {
      return [];
    } finally {
      setLoadingSession((current) => {
        const next = new Set(current);
        next.delete(session.session_key);
        return next;
      });
    }
  };

  const loadMoreTraces = async (session: AuditSessionListItem) => {
    const cursor = nextCursors[session.session_key];
    if (!cursor || loadingSession.has(session.session_key)) return;
    setLoadingSession((current) => new Set(current).add(session.session_key));
    try {
      const page = await listAuditTraces(
        token,
        DEFAULT_TRACE_FILTERS,
        cursor,
        session.session_key,
      );
      setTraces((current) => ({
        ...current,
        [session.session_key]: [
          ...(current[session.session_key] ?? []),
          ...page.items,
        ],
      }));
      setNextCursors((current) => ({
        ...current,
        [session.session_key]: page.next_cursor,
      }));
    } finally {
      setLoadingSession((current) => {
        const next = new Set(current);
        next.delete(session.session_key);
        return next;
      });
    }
  };

  useEffect(() => {
    const keys = sessions
      .filter((session) => session.trace_count === 1 || session.session_key === selectedSessionKey)
      .map((session) => session.session_key);
    if (!keys.length) return;
    setExpanded((current) => new Set([...current, ...keys]));
    sessions.filter((session) => keys.includes(session.session_key)).forEach((session) => {
      void loadSession(session);
    });
    // Session keys are the stable expansion identity; trace polling must not reset this state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionKey, sessions]);

  const toggle = (session: AuditSessionListItem) => {
    const willExpand = !expanded.has(session.session_key);
    setExpanded((current) => {
      const next = new Set(current);
      if (willExpand) next.add(session.session_key); else next.delete(session.session_key);
      return next;
    });
    if (willExpand) {
      void loadSession(session).then((items) => {
        if (items[0] && selectedSessionKey !== session.session_key) onSelectTrace(items[0]);
      });
    }
  };

  return (
    <section className="flex h-full min-h-0 flex-col border-r border-border/60 bg-background" aria-label="Session 与 Trace 导航">
      <div className="border-b border-border/55 p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索 Session" aria-label="搜索 Session" className="h-8 pl-8 text-xs" />
        </div>
        {index ? (
          <div className="mt-2 flex items-center gap-1.5 text-[10.5px] text-muted-foreground">
            {index.state === "ready" ? <CheckCircle2 className="h-3 w-3 text-emerald-600" /> : <AlertTriangle className="h-3 w-3 text-amber-600" />}
            <span>{index.state === "ready" ? "索引就绪" : index.state === "stale" ? "索引滞后" : index.state}</span>
          </div>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto" aria-busy={loading}>
        {loading ? <div className="flex h-28 items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />正在读取 Session</div> : null}
        {!loading && !sessions.length ? <div className="flex h-28 items-center justify-center text-xs text-muted-foreground">没有匹配的 Session</div> : null}
        <div className="divide-y divide-border/55">
          {sessions.map((session) => {
            const open = expanded.has(session.session_key);
            return (
              <div key={session.session_key}>
                <button type="button" className="flex w-full items-start gap-2 px-3 py-2.5 text-left hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" aria-expanded={open} onClick={() => toggle(session)}>
                  {open ? <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
                  <span className="min-w-0 flex-1">
                    <span className="block line-clamp-2 text-xs font-semibold" title={session.title}>{session.title}</span>
                    <span className="mt-1 flex gap-2 text-[10.5px] text-muted-foreground"><span>{session.trace_count} 个 Trace</span>{session.active_trace_count ? <span>{session.active_trace_count} 运行中</span> : null}{session.error_count || session.warning_count ? <span className="text-amber-600">{session.error_count + session.warning_count} 异常</span> : null}</span>
                  </span>
                </button>
                {open ? (
                  <div className="border-t border-border/35 bg-muted/10">
                    {loadingSession.has(session.session_key) ? <div className="px-8 py-3 text-[10.5px] text-muted-foreground">正在读取 Trace</div> : null}
                    {(traces[session.session_key] ?? []).map((trace, index) => (
                      <button type="button" key={trace.trace_id} className={cn("flex w-full items-start gap-2 border-t border-border/30 py-2 pl-8 pr-3 text-left hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", selectedTraceId === trace.trace_id && "bg-sidebar-accent/70")} aria-current={selectedTraceId === trace.trace_id ? "true" : undefined} onClick={() => onSelectTrace(trace)}>
                        {trace.display_status === "succeeded" ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />}
                        <span className="min-w-0 flex-1"><span className="block truncate text-[11.5px] font-medium">第 {session.trace_count - index} 次运行 · {trace.primary_source_type} · {new Date(trace.last_seen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span><span className="mt-0.5 block text-[10px] text-muted-foreground">{auditStatusLabel(trace.display_status)} · {trace.event_count} Event · {trace.trace_id.slice(0, 8)}</span></span>
                      </button>
                    ))}
                    {nextCursors[session.session_key] ? (
                      <Button type="button" variant="ghost" size="sm" className="h-8 w-full rounded-none text-[10.5px]" disabled={loadingSession.has(session.session_key)} onClick={() => void loadMoreTraces(session)}>
                        加载更多 Trace（已加载 {traces[session.session_key]?.length ?? 0}/{session.trace_count}）
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
      {hasMore ? <div className="border-t border-border/55 p-2"><Button variant="ghost" size="sm" className="h-7 w-full text-xs" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? "正在加载" : "加载更多 Session"}</Button></div> : null}
    </section>
  );
}
