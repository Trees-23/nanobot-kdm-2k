import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  ChevronDown,
  ChevronUp,
  Database,
  GripHorizontal,
  LoaderCircle,
  Maximize2,
  Minimize2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { useAuditTimeline } from "@/hooks/useAuditTimeline";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import type { AuditEventItem } from "@/lib/audit-types";
import { auditValueLabel } from "@/lib/audit-display";
import { cn } from "@/lib/utils";

const ROW_HEIGHT = 42;
const MIN_HEIGHT = 160;
const DEFAULT_HEIGHT = 280;
const STORAGE_KEY = "nanobot.audit.timeline.height";

function maxHeight(): number {
  return Math.max(MIN_HEIGHT, Math.floor((window.innerHeight || 800) * 0.7));
}

function clampHeight(value: number): number {
  return Math.min(maxHeight(), Math.max(MIN_HEIGHT, value));
}

export function TraceTimeline({
  timeline,
  total,
  open,
  selectedEventId,
  currentNodeIds,
  onOpenChange,
  onSelectEvent,
  onLoadPayload,
  notice,
}: {
  timeline: ReturnType<typeof useAuditTimeline>;
  total: number;
  open: boolean;
  selectedEventId: string | null;
  currentNodeIds: Set<string>;
  onOpenChange: (open: boolean) => void;
  onSelectEvent: (event: AuditEventItem, nodeId: string | null) => void;
  onLoadPayload: (payloadId: string) => void;
  notice?: string | null;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(180);
  const mobile = useMediaQuery("(max-width: 767px)", false);
  const [height, setHeight] = useState(() => {
    if (typeof window === "undefined") return DEFAULT_HEIGHT;
    try {
      const stored = Number(window.localStorage.getItem(STORAGE_KEY));
      return clampHeight(Number.isFinite(stored) && stored > 0 ? stored : DEFAULT_HEIGHT);
    } catch {
      return DEFAULT_HEIGHT;
    }
  });
  const [maximized, setMaximized] = useState(false);
  const restoredHeight = useRef(height);

  useEffect(() => {
    if (!open || !selectedEventId) return;
    const index = timeline.events.findIndex((event) => event.event_id === selectedEventId);
    if (index >= 0 && viewportRef.current) {
      const nextScrollTop = index * ROW_HEIGHT;
      viewportRef.current.scrollTop = nextScrollTop;
      // Programmatic scroll assignments do not reliably dispatch scroll events,
      // so keep the virtual range in sync for mobile Event navigation.
      setScrollTop(nextScrollTop);
    }
  }, [open, selectedEventId, timeline.events]);

  useEffect(() => {
    if (!open || !viewportRef.current) return;
    const element = viewportRef.current;
    const update = () => setViewportHeight(element.clientHeight || 180);
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [open]);

  useEffect(() => {
    const resize = () => setHeight((current) => clampHeight(current));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  const beginResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (mobile) return;
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = height;
    const move = (pointer: PointerEvent) => {
      const next = clampHeight(startHeight + startY - pointer.clientY);
      setHeight(next);
      setMaximized(false);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      setHeight((current) => {
        try {
          window.localStorage.setItem(STORAGE_KEY, String(current));
        } catch {
          // Height persistence is optional when storage is unavailable.
        }
        restoredHeight.current = current;
        return current;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  const toggleMaximize = () => {
    if (maximized) {
      const next = clampHeight(restoredHeight.current);
      setHeight(next);
      setMaximized(false);
      return;
    }
    restoredHeight.current = height;
    setHeight(maxHeight());
    setMaximized(true);
  };

  const range = useMemo(() => {
    const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 4);
    const count = Math.ceil(viewportHeight / ROW_HEIGHT) + 8;
    return { start, end: Math.min(timeline.events.length, start + count) };
  }, [scrollTop, timeline.events.length, viewportHeight]);

  return (
    <section
      className={cn(
        "flex shrink-0 flex-col border-t border-border/60 bg-background",
        open && mobile && "fixed inset-0 z-50 flex h-dvh flex-col border-0",
        !open && "h-8",
      )}
      style={open && !mobile ? { height } : undefined}
      aria-label="原始 Event 时间线"
    >
      {open && !mobile ? (
        <button
          type="button"
          className="flex h-2 w-full cursor-ns-resize items-center justify-center text-muted-foreground hover:bg-muted/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          aria-label="拖拽调整时间线高度"
          title="拖拽调整时间线高度"
          onPointerDown={beginResize}
        >
          <GripHorizontal className="h-3.5 w-3.5" />
        </button>
      ) : null}
      <div className="flex h-8 w-full items-center text-[11px] text-muted-foreground">
        <button
          type="button"
          className="flex h-full min-w-0 flex-1 items-center gap-1.5 px-3 text-left hover:bg-muted/35 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          onClick={() => onOpenChange(!open)}
          aria-expanded={open}
        >
          <Database className="h-3.5 w-3.5" />Event 时间线 · {total}
          {open && timeline.events.length < total
            ? <span>（已加载 {timeline.events.length}）</span>
            : null}
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
        </button>
        {open && !mobile ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="mr-1 h-7 w-7"
            aria-label={maximized ? "还原时间线高度" : "最大化时间线"}
            title={maximized ? "还原时间线高度" : "最大化时间线"}
            onClick={toggleMaximize}
          >
            {maximized ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </Button>
        ) : null}
      </div>
      {open ? (
        <div className="flex min-h-0 flex-1 flex-col">
          {notice ? (
            <p role="status" className="shrink-0 border-b border-amber-500/25 px-3 py-2 text-[10.5px] text-amber-700 dark:text-amber-300">
              {notice}
            </p>
          ) : null}
          <div
            ref={viewportRef}
            className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain"
            data-testid="audit-timeline-viewport"
            onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
          >
          {timeline.loading && !timeline.events.length ? (
            <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />正在读取 Event
            </div>
          ) : (
            <div className="relative w-full" style={{ height: timeline.events.length * ROW_HEIGHT }}>
              {timeline.events.slice(range.start, range.end).map((event, offset) => {
                const index = range.start + offset;
                return (
                  <div
                    role="button"
                    tabIndex={0}
                    key={event.event_id}
                    data-event-id={event.event_id}
                    data-event-index={index + 1}
                    className={cn(
                      "absolute left-0 grid w-full grid-cols-[74px_minmax(150px,1fr)_110px_auto] items-center gap-2 border-b border-border/40 px-3 text-left text-[10.5px] hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                      selectedEventId === event.event_id && "bg-sidebar-accent/70",
                    )}
                    style={{ top: index * ROW_HEIGHT, height: ROW_HEIGHT }}
                    onClick={() => onSelectEvent(
                      event,
                      event.semantic_node_id && currentNodeIds.has(event.semantic_node_id)
                        ? event.semantic_node_id
                        : null,
                    )}
                    onKeyDown={(keyEvent) => {
                      if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
                      keyEvent.preventDefault();
                      onSelectEvent(
                        event,
                        event.semantic_node_id && currentNodeIds.has(event.semantic_node_id)
                          ? event.semantic_node_id
                          : null,
                      );
                    }}
                  >
                    <span className="font-mono text-muted-foreground">{new Date(event.occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                    <span className="truncate font-medium">{auditValueLabel(event.event_type)}</span>
                    <span className="truncate font-mono text-muted-foreground">{event.run_id ?? event.process_instance_id}</span>
                    {event.payload_id ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-6 px-2 text-[10px]"
                        onClick={(click) => {
                          click.stopPropagation();
                          onLoadPayload(event.payload_id!);
                        }}
                      >
                        Payload
                      </Button>
                    ) : <span className="w-14" />}
                  </div>
                );
              })}
            </div>
          )}
          {timeline.nextCursor ? (
            <div className="sticky bottom-2 flex justify-center">
              <Button variant="secondary" size="sm" className="h-7 text-[10px] shadow" onClick={() => void timeline.loadMore()} disabled={timeline.loading}>加载更多 Event</Button>
            </div>
          ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
