import { AlertTriangle, LoaderCircle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AuditApiError } from "@/lib/audit-api";
import type { AuditPayloadResponse } from "@/lib/audit-types";

export function PayloadViewer({
  payload,
  loading,
  error,
  onRetry,
  onClose,
}: {
  payload: AuditPayloadResponse | null;
  loading: boolean;
  error: AuditApiError | null;
  onRetry: () => void;
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
          <div role="alert" className="text-xs">
            <p className="font-medium text-destructive">
              {error.status === 401
                ? "认证已失效"
                : error.status === 404
                  ? "Payload 未找到、已清理或已过期"
                  : error.status === 413
                    ? "Payload 超过服务端有界读取上限"
                    : error.status === 503
                      ? "Payload 暂时不可用"
                      : "Payload 读取失败"}
            </p>
            <p className="mt-1 text-muted-foreground">
              {error.status === 401
                ? "正在复用现有认证入口；若刷新失败将返回认证页。"
                : error.status === 404
                  ? "服务端无法可靠区分证据从未存在、定位失效或保留期清理。"
                  : error.status === 413
                    ? "不会自动下载或绕过服务端上限。"
                    : error.status === 503
                      ? "审计索引可能正在构建、滞后，或本次查找已超时。"
                      : error.message}
            </p>
            {error.retryable || error.status === 503 ? (
              <Button type="button" variant="outline" size="sm" className="mt-3 h-7 text-[11px]" onClick={onRetry}>
                重试
              </Button>
            ) : null}
          </div>
        ) : payload && !payload.available ? (
          <div className="text-xs text-muted-foreground">
            {payload.reason === "metadata_only"
              ? "当前 Audit 仅保存元数据，没有可读取的 Payload 内容。"
              : `Payload 不可用：${payload.reason ?? "原因未知"}`}
          </div>
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
