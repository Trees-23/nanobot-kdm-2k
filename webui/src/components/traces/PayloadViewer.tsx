import { AlertTriangle, LoaderCircle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AuditPayloadResponse } from "@/lib/audit-types";

export function PayloadViewer({
  payload,
  loading,
  error,
  onClose,
}: {
  payload: AuditPayloadResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-background" aria-label="Payload 查看器">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/60 px-3">
        <div className="min-w-0">
          <h2 className="text-xs font-semibold">Payload</h2>
          <p className="truncate font-mono text-[10px] text-muted-foreground">{payload?.payload_id ?? ""}</p>
        </div>
        <Button type="button" variant="ghost" size="icon" className="h-7 w-7" aria-label="关闭 Payload" title="关闭 Payload" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="mb-3 flex items-start gap-2 border-b border-amber-500/25 pb-3 text-[11px] text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>敏感证据，仅在本次显式请求后显示。</span>
        </div>
        {loading ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-xs text-muted-foreground">
            <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />正在读取
          </div>
        ) : error ? (
          <div className="text-xs text-destructive">{error}</div>
        ) : payload && !payload.available ? (
          <div className="text-xs text-muted-foreground">Payload 不可用：{payload.reason}</div>
        ) : payload ? (
          <>
            {payload.truncated ? <p className="mb-2 text-[11px] text-amber-700 dark:text-amber-300">内容已按服务端上限截断</p> : null}
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-muted/20 p-3 font-mono text-[11px] leading-5 text-foreground">
              {typeof payload.content === "string"
                ? payload.content
                : JSON.stringify(payload.content, null, 2)}
            </pre>
          </>
        ) : null}
      </div>
    </div>
  );
}
