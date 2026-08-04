import { ChevronDown, ChevronUp, EyeOff, ListTodo, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { auditValueLabel } from "@/lib/audit-display";
import type { SubagentTaskPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACTIVE_STATUSES = new Set(["created", "queued", "running"]);

function taskTone(status: string): string {
  if (status === "succeeded") return "bg-emerald-500";
  if (["failed", "lost"].includes(status)) return "bg-destructive";
  if (status === "timed_out") return "bg-amber-500";
  if (status === "cancelled") return "bg-muted-foreground/60";
  return "bg-blue-500";
}

function budgetSummary(task: SubagentTaskPayload): string {
  const values = [
    task.budget.max_tokens != null ? `≤ ${task.budget.max_tokens} tokens` : null,
    task.budget.max_cost_usd != null ? `≤ $${task.budget.max_cost_usd.toFixed(4)}` : null,
    task.budget.wall_time_seconds != null ? `≤ ${task.budget.wall_time_seconds}s` : null,
  ].filter(Boolean);
  return values.join(" · ");
}

function usageSummary(task: SubagentTaskPayload): string {
  const values = [`${task.usage.total_tokens} tokens`];
  if (task.usage.cost_usd != null) values.push(`$${task.usage.cost_usd.toFixed(4)}`);
  return values.join(" · ");
}

export function SubagentTaskStrip({ tasks }: { tasks: SubagentTaskPayload[] }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const [hiddenTerminalIds, setHiddenTerminalIds] = useState<Set<string>>(
    () => new Set(),
  );
  const panelRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const activeCount = tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length;
  const visibleTasks = tasks.filter(
    (task) => ACTIVE_STATUSES.has(task.status) || !hiddenTerminalIds.has(task.task_id),
  );
  const visible = visibleTasks.length > 0;
  const effectiveFilter = activeCount > 0 ? filter : "all";
  const recent = visibleTasks
    .filter((task) => effectiveFilter === "all" || ACTIVE_STATUSES.has(task.status))
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
    .slice(0, 20);

  function hideTerminalTasks(): void {
    setHiddenTerminalIds((previous) => {
      const next = new Set(previous);
      tasks.forEach((task) => {
        if (!ACTIVE_STATUSES.has(task.status)) next.add(task.task_id);
      });
      return next;
    });
    setOpen(false);
  }

  useEffect(() => {
    setHiddenTerminalIds((previous) => {
      const terminalIds = new Set(
        tasks
          .filter((task) => !ACTIVE_STATUSES.has(task.status))
          .map((task) => task.task_id),
      );
      const retained = new Set([...previous].filter((taskId) => terminalIds.has(taskId)));
      return retained.size === previous.size ? previous : retained;
    });
  }, [tasks]);

  useEffect(() => {
    if (!visible) setOpen(false);
  }, [visible]);

  useEffect(() => {
    if (!open) return;
    function close(event: MouseEvent): void {
      const target = event.target as Node | null;
      if (!target || panelRef.current?.contains(target) || toggleRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  if (!visible) return null;

  return (
    <div className="composer-status-strip relative z-40 border-b border-black/[0.04] dark:border-white/[0.06]">
      {open ? (
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="false"
          aria-label="Subagent task details"
          className={cn(
            "left-3 right-3 z-[60] overflow-hidden",
            "fixed bottom-[calc(env(safe-area-inset-bottom)+7rem)] max-h-[32dvh] sm:absolute sm:bottom-[calc(100%+8px)] sm:max-h-[min(60dvh,30rem)]",
            "rounded-lg border border-black/[0.08] bg-card shadow-[0_12px_40px_rgba(15,23,42,0.14)]",
            "dark:border-white/[0.1] dark:shadow-[0_16px_48px_rgba(0,0,0,0.45)]",
          )}
        >
          <div className="flex items-center justify-between border-b border-border/55 px-3 py-2">
            <div>
              <h2 className="text-[13px] font-semibold">Subagent Tasks</h2>
              <p className="text-[10.5px] text-muted-foreground">
                Task → Run → Model / Tool → Delivery
              </p>
            </div>
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground hover:bg-muted/65 hover:text-foreground"
              aria-label="Close subagent task details"
              onClick={() => setOpen(false)}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex items-center justify-between gap-2 border-b border-border/45 px-3 py-2">
            <div
              className="inline-flex h-7 items-center rounded-md bg-muted/70 p-0.5"
              role="group"
              aria-label="Filter subagent tasks"
            >
              <button
                type="button"
                className={cn(
                  "h-6 rounded px-2 text-[10.5px] font-medium text-muted-foreground",
                  effectiveFilter === "active" && "bg-background text-foreground shadow-sm",
                )}
                aria-pressed={effectiveFilter === "active"}
                disabled={activeCount === 0}
                onClick={() => setFilter("active")}
              >
                Active
              </button>
              <button
                type="button"
                className={cn(
                  "h-6 rounded px-2 text-[10.5px] font-medium text-muted-foreground",
                  effectiveFilter === "all" && "bg-background text-foreground shadow-sm",
                )}
                aria-pressed={effectiveFilter === "all"}
                onClick={() => setFilter("all")}
              >
                All
              </button>
            </div>
            {visibleTasks.some((task) => !ACTIVE_STATUSES.has(task.status)) ? (
              <button
                type="button"
                className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[10.5px] font-medium text-muted-foreground hover:bg-muted/65 hover:text-foreground"
                onClick={hideTerminalTasks}
              >
                <EyeOff className="h-3.5 w-3.5" />
                Hide completed
              </button>
            ) : null}
          </div>
          <div className="max-h-[24dvh] divide-y divide-border/45 overflow-y-auto px-3 sm:max-h-[min(52dvh,25rem)]">
            {recent.map((task) => {
              const budget = budgetSummary(task);
              const phase =
                !ACTIVE_STATUSES.has(task.status) && task.phase === "initializing"
                  ? null
                  : auditValueLabel(task.phase);
              return (
                <section key={task.task_id} className="py-3" data-task-id={task.task_id}>
                  <div className="flex items-start gap-2">
                    <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", taskTone(task.status))} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <h3 className="max-w-full truncate text-[12px] font-semibold">{task.label}</h3>
                        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[9.5px] text-muted-foreground">
                          {task.required ? "Required" : "Background"}
                        </span>
                      </div>
                      <p className="mt-1 break-words text-[10.5px] text-muted-foreground">
                        {[
                          auditValueLabel(task.status),
                          phase,
                          auditValueLabel(task.termination_state),
                          auditValueLabel(task.delivery_phase),
                        ].filter(Boolean).join(" · ")}
                      </p>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        Usage {usageSummary(task)}{budget ? ` · Budget ${budget}` : ""}
                      </p>
                      {task.error ? (
                        <p className="mt-1 break-words text-[10.5px] text-destructive">{task.error}</p>
                      ) : null}
                    </div>
                    <span className="shrink-0 font-mono text-[9.5px] text-muted-foreground">
                      r{task.revision}
                    </span>
                  </div>
                </section>
              );
            })}
          </div>
        </div>
      ) : null}
      <div className="flex min-h-[36px] items-center gap-2 px-3 py-2" role="status">
        <ListTodo className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground/75">
          {activeCount > 0 ? `${activeCount} active` : "No active"} · {visibleTasks.length} subagent task{visibleTasks.length === 1 ? "" : "s"}
        </span>
        {activeCount === 0 ? (
          <button
            type="button"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-muted/55 hover:text-foreground"
            aria-label="Dismiss completed subagent tasks"
            title="Dismiss completed subagent tasks"
            onClick={hideTerminalTasks}
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
        <button
          ref={toggleRef}
          type="button"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-muted/55 hover:text-foreground"
          aria-expanded={open}
          aria-label="Show subagent task details"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}
