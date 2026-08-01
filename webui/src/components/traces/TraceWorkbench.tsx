import { useEffect, useState } from "react";
import { ArrowLeft, ExternalLink, LoaderCircle, RefreshCw, Workflow, X } from "lucide-react";

import { TraceGraph } from "@/components/traces/TraceGraph";
import { SessionTraceList } from "@/components/traces/SessionTraceList";
import { PayloadViewer } from "@/components/traces/PayloadViewer";
import { TraceTimeline } from "@/components/traces/TraceTimeline";
import {
  TraceNodeInspector,
  type TraceFocusMode,
} from "@/components/traces/TraceNodeInspector";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useAuditGraph } from "@/hooks/useAuditGraph";
import { useAuditSessions } from "@/hooks/useAuditSessions";
import { useAuditTimeline } from "@/hooks/useAuditTimeline";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { AuditApiError, fetchAuditPayload } from "@/lib/audit-api";
import type { AuditGraphEdge, AuditPayloadResponse, AuditTraceListItem } from "@/lib/audit-types";
import { cn } from "@/lib/utils";

export interface TraceSelection {
  traceId: string | null;
  turnId: string | null;
  runId: string | null;
  nodeId: string | null;
  eventId: string | null;
}

export const EMPTY_TRACE_SELECTION: TraceSelection = {
  traceId: null,
  turnId: null,
  runId: null,
  nodeId: null,
  eventId: null,
};

export function TraceWorkbench({
  token,
  selection,
  onSelectionChange,
  onOpenConversation,
  onReauth,
}: {
  token: string;
  selection: TraceSelection;
  onSelectionChange: (selection: TraceSelection, replace?: boolean) => void;
  onOpenConversation: (sessionKey: string) => void;
  onReauth?: () => Promise<string | null>;
}) {
  const sessions = useAuditSessions(token);
  const auditGraph = useAuditGraph(token, selection.traceId, selection.runId);
  const [focusMode, setFocusMode] = useState<TraceFocusMode>(null);
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [payloadId, setPayloadId] = useState<string | null>(null);
  const [payload, setPayload] = useState<AuditPayloadResponse | null>(null);
  const [payloadLoading, setPayloadLoading] = useState(false);
  const [payloadError, setPayloadError] = useState<AuditApiError | null>(null);
  const [timelineNotice, setTimelineNotice] = useState<string | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<AuditTraceListItem | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<AuditGraphEdge | null>(null);
  const wideInspector = useMediaQuery("(min-width: 1440px)", false);
  const selected = selectedTrace?.trace_id === selection.traceId ? selectedTrace : null;
  const selectedNode = auditGraph.graph?.nodes.find((node) => node.id === selection.nodeId) ?? null;
  const edgeSource = selectedEdge ? auditGraph.graph?.nodes.find((node) => node.id === selectedEdge.source) : null;
  const edgeTarget = selectedEdge ? auditGraph.graph?.nodes.find((node) => node.id === selectedEdge.target) : null;
  const timeline = useAuditTimeline(
    token,
    selection.traceId,
    timelineOpen || Boolean(selectedNode),
  );
  const unavailableCode = sessions.error?.code;

  useEffect(() => {
    if (!selection.nodeId || !auditGraph.graph || selectedNode) return;
    onSelectionChange({ ...selection, nodeId: null, eventId: null }, true);
  }, [auditGraph.graph, onSelectionChange, selectedNode, selection]);

  useEffect(() => {
    setSelectedEdge(null);
  }, [selection.traceId]);

  useEffect(() => {
    const closeInspector = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (payloadId) {
        setPayloadId(null);
        return;
      }
      if (!selection.nodeId) return;
      onSelectionChange({ ...selection, nodeId: null, eventId: null });
    };
    window.addEventListener("keydown", closeInspector);
    return () => window.removeEventListener("keydown", closeInspector);
  }, [onSelectionChange, payloadId, selection]);

  const loadPayload = async (nextPayloadId: string, requestToken = token) => {
    setPayloadId(nextPayloadId);
    setPayload(null);
    setPayloadError(null);
    setPayloadLoading(true);
    try {
      setPayload(await fetchAuditPayload(requestToken, nextPayloadId));
    } catch (reason) {
      const apiError = reason instanceof AuditApiError
        ? reason
        : new AuditApiError(0, "network_error", String(reason), true);
      if (apiError.status === 401 && onReauth) {
        const refreshedToken = await onReauth();
        if (refreshedToken) {
          try {
            setPayload(await fetchAuditPayload(refreshedToken, nextPayloadId));
            return;
          } catch (retryReason) {
            setPayloadError(retryReason instanceof AuditApiError
              ? retryReason
              : new AuditApiError(0, "network_error", String(retryReason), true));
            return;
          }
        }
      }
      setPayloadError(apiError);
    } finally {
      setPayloadLoading(false);
    }
  };

  const locateEvent = async (eventId: string) => {
    setTimelineOpen(true);
    setTimelineNotice(null);
    const graphRevision = auditGraph.graph?.index.revision;
    if (graphRevision != null && timeline.revision != null && graphRevision !== timeline.revision) {
      setTimelineNotice("Graph 与 Events revision 不一致，请刷新轨迹后重试。");
      return;
    }
    const result = await timeline.ensureEvent(eventId);
    if (result === "found") {
      onSelectionChange({ ...selection, eventId });
      return;
    }
    const messages = {
      cursor_stale: "Event 索引已变化，请刷新轨迹后重试。",
      revision_mismatch: "Events 分页 revision 不一致，请刷新轨迹后重试。",
      limit: "已达到定位上限（5 页、1000 Event 或 10 秒），请缩小范围后重试。",
      not_found: "该 Event 未找到，可能已清理或不在当前索引中。",
      error: "定位 Event 时读取失败，请重试。",
    } as const;
    setTimelineNotice(messages[result]);
  };

  const selectTrace = (trace: AuditTraceListItem) => {
    setSelectedTrace(trace);
    onSelectionChange({ ...EMPTY_TRACE_SELECTION, traceId: trace.trace_id });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground" data-testid="trace-workbench">
      <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-border/60 px-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
          {selection.traceId ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 md:hidden"
              aria-label="返回运行轨迹列表"
              title="返回运行轨迹列表"
              onClick={() => onSelectionChange(EMPTY_TRACE_SELECTION)}
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          ) : null}
          <Workflow className="h-4 w-4 shrink-0 text-orange-500" />
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold">运行轨迹</h1>
            {selected || auditGraph.graph ? <p className="truncate text-[11px] text-muted-foreground">{selected?.title ?? auditGraph.graph?.trace.title}</p> : null}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {(selected?.session_key ?? auditGraph.graph?.trace.session_key)?.startsWith("websocket:") ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 px-2 text-xs"
              onClick={() => onOpenConversation((selected?.session_key ?? auditGraph.graph?.trace.session_key)!)}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">打开会话</span>
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            aria-label="刷新运行轨迹"
            title="刷新运行轨迹"
            onClick={() => { sessions.refresh(); auditGraph.refresh(); }}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {sessions.error && !sessions.items.length ? (
        <div className="flex min-h-0 flex-1 items-center justify-center px-6">
          <div className="max-w-sm text-center">
            <Workflow className="mx-auto h-7 w-7 text-muted-foreground" />
            <h2 className="mt-3 text-sm font-semibold">
              {unavailableCode === "audit_off"
                ? "审计记录已关闭"
                : unavailableCode === "audit_index_disabled"
                  ? "审计索引已关闭"
                  : unavailableCode === "audit_index_building"
                    ? "正在构建审计索引"
                    : "运行轨迹暂不可用"}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">{sessions.error.message}</p>
            {sessions.error.retryable ? (
              <Button variant="outline" size="sm" className="mt-4 h-8 text-xs" onClick={sessions.refresh}>
                重试
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className={cn(
          "grid min-h-0 flex-1 md:grid-cols-[280px_minmax(0,1fr)]",
          selectedNode && wideInspector && "2xl:grid-cols-[280px_minmax(0,1fr)_380px]",
        )}>
          <div className={cn("min-h-0", selection.traceId && "hidden md:block")}>
            <SessionTraceList
              token={token}
              sessions={sessions.items}
              index={sessions.index}
              query={sessions.query}
              selectedTraceId={selection.traceId}
              selectedSessionKey={selected?.session_key ?? auditGraph.graph?.trace.session_key ?? null}
              loading={sessions.loading}
              loadingMore={sessions.loadingMore}
              hasMore={Boolean(sessions.nextCursor)}
              onQueryChange={sessions.setQuery}
              onSelectTrace={selectTrace}
              onLoadMore={sessions.loadMore}
            />
          </div>
          <section className={cn("relative flex min-h-0 flex-col overflow-hidden", !selection.traceId && "hidden md:flex")} aria-label="运行轨迹图">
            {selection.traceId ? (
              <>
                <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border/55 px-2 text-[11px] text-muted-foreground">
                  <button type="button" className="rounded px-1.5 py-1 hover:bg-muted hover:text-foreground" onClick={() => onSelectionChange({ ...selection, runId: null, nodeId: null, eventId: null })}>
                    Trace
                  </button>
                  {selection.runId ? <><span>/</span><span className="truncate font-mono">{selection.runId}</span></> : null}
                  {auditGraph.updating ? <LoaderCircle className="ml-auto h-3.5 w-3.5 animate-spin motion-reduce:animate-none" /> : null}
                </div>
                <div className="min-h-0 flex-1">
                  {auditGraph.loading && !auditGraph.graph ? (
                    <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
                      <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />正在构建轨迹图
                    </div>
                  ) : auditGraph.error ? (
                    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-xs text-muted-foreground">
                      <span>{auditGraph.error.message}</span>
                      <Button variant="outline" size="sm" className="h-8" onClick={auditGraph.refresh}>重试</Button>
                    </div>
                  ) : auditGraph.graph ? (
                    <TraceGraph
                      graph={auditGraph.graph}
                      selectedNodeId={selection.nodeId}
                      focusMode={focusMode}
                      onSelectNode={(nodeId) => onSelectionChange({ ...selection, nodeId, eventId: null })}
                      onFocusMode={setFocusMode}
                      onSelectEdge={setSelectedEdge}
                    />
                  ) : null}
                  {selectedEdge ? (
                    <aside className="absolute right-3 top-12 z-10 w-[min(360px,calc(100%-24px))] rounded-md border border-border/70 bg-background/95 p-3 text-xs shadow-lg" aria-label="恢复关系检查器">
                      <div className="flex items-center justify-between gap-2">
                        <h2 className="font-semibold">{selectedEdge.type === "tool_recovery" ? "Tool 恢复关系" : "关系检查器"}</h2>
                        <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="关闭关系检查器" title="关闭关系检查器" onClick={() => setSelectedEdge(null)}><X className="h-3.5 w-3.5" /></Button>
                      </div>
                      <dl className="mt-2 divide-y divide-border/45">
                        <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-2 py-1.5"><dt className="text-muted-foreground">失败端</dt><dd className="min-w-0 truncate">{edgeSource?.label ?? "节点未找到"} · {edgeSource?.status ?? "unknown"}</dd></div>
                        <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-2 py-1.5"><dt className="text-muted-foreground">恢复端</dt><dd className="min-w-0 truncate">{edgeTarget?.label ?? "节点未找到"} · {edgeTarget?.status ?? "unknown"}</dd></div>
                        <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-2 py-1.5"><dt className="text-muted-foreground">证据计数</dt><dd>{selectedEdge.evidence_count ?? 0}</dd></div>
                      </dl>
                      <div className="mt-2 grid gap-1.5">
                        {selectedEdge.anchor?.source_event_id ? <Button type="button" variant="outline" size="sm" className="h-7 justify-start text-[11px]" onClick={() => void locateEvent(selectedEdge.anchor!.source_event_id!)}>定位失败端 Event {selectedEdge.anchor.source_event_id.slice(0, 12)}</Button> : <p className="text-[10.5px] text-muted-foreground">失败端 Event 不可定位</p>}
                        {selectedEdge.anchor?.target_event_id ? <Button type="button" variant="outline" size="sm" className="h-7 justify-start text-[11px]" onClick={() => void locateEvent(selectedEdge.anchor!.target_event_id!)}>定位恢复端 Event {selectedEdge.anchor.target_event_id.slice(0, 12)}</Button> : <p className="text-[10.5px] text-muted-foreground">恢复端 Event 不可定位</p>}
                      </div>
                    </aside>
                  ) : null}
                </div>
                <TraceTimeline
                  timeline={timeline}
                  total={auditGraph.graph?.trace.event_count ?? timeline.total}
                  open={timelineOpen}
                  selectedEventId={selection.eventId}
                  currentNodeIds={new Set(auditGraph.graph?.nodes.map((node) => node.id) ?? [])}
                  onOpenChange={setTimelineOpen}
                  onSelectEvent={(event, nodeId) => onSelectionChange({
                    ...selection,
                    eventId: event.event_id,
                    nodeId: nodeId ?? selection.nodeId,
                  })}
                  onLoadPayload={(nextPayloadId) => void loadPayload(nextPayloadId)}
                  notice={timelineNotice}
                />
              </>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                选择一条运行轨迹
              </div>
            )}
          </section>
          {selectedNode && wideInspector ? (
            <TraceNodeInspector
              node={selectedNode}
              onLocateEvent={(event) => void locateEvent(event.event_id)}
              onLoadPayload={(nextPayloadId) => void loadPayload(nextPayloadId)}
              focusMode={focusMode}
              onFocusMode={setFocusMode}
              onClose={() => onSelectionChange({ ...selection, nodeId: null, eventId: null })}
            />
          ) : null}
        </div>
      )}
      {!wideInspector ? (
        <Sheet
          open={Boolean(selectedNode)}
          onOpenChange={(open) => {
            if (!open) onSelectionChange({ ...selection, nodeId: null, eventId: null });
          }}
        >
          <SheetContent side="right" className="w-[min(100vw,400px)] p-0" aria-describedby={undefined}>
            <SheetTitle className="sr-only">节点检查器</SheetTitle>
            {selectedNode ? (
              <TraceNodeInspector
                node={selectedNode}
                onLocateEvent={(event) => void locateEvent(event.event_id)}
                onLoadPayload={(nextPayloadId) => void loadPayload(nextPayloadId)}
                focusMode={focusMode}
                onFocusMode={setFocusMode}
                onClose={() => onSelectionChange({ ...selection, nodeId: null, eventId: null })}
              />
            ) : null}
          </SheetContent>
        </Sheet>
      ) : null}
      <Sheet open={Boolean(payloadId)} onOpenChange={(open) => { if (!open) setPayloadId(null); }}>
        <SheetContent side="right" className="w-[min(100vw,640px)] p-0" aria-describedby={undefined}>
          <SheetTitle className="sr-only">Payload 查看器</SheetTitle>
          <PayloadViewer
            payload={payload}
            loading={payloadLoading}
            error={payloadError}
            onRetry={() => payloadId && void loadPayload(payloadId)}
            onClose={() => setPayloadId(null)}
          />
        </SheetContent>
      </Sheet>
    </div>
  );
}
