import type { AuditNodeType, TraceDisplayStatus } from "@/lib/audit-types";

const ZH_STATUS: Record<TraceDisplayStatus, string> = {
  running: "运行中",
  failed: "失败",
  interrupted: "已中断",
  cancelled: "已取消",
  incomplete: "不完整",
  warning: "警告",
  succeeded: "成功",
  unknown: "未知",
};

const ZH_VALUES: Record<string, string> = {
  turn_response_prepared: "Turn 响应已准备",
  model_response_received: "已收到模型响应",
  checkpoint_written: "Checkpoint 已写入",
  checkpoint_restored: "Checkpoint 已恢复",
  checkpoint_cleared: "Checkpoint 已清理",
  delivery_attempted: "Delivery 正在投递",
  delivery_finished: "Delivery 已结束",
  awaiting_tools: "等待工具执行",
  tools_completed: "工具执行完成",
  final_response: "最终响应",
  accepted_by_adapter: "渠道已接收",
  suppressed: "已抑制投递",
  webui_stream_already_delivered: "WebUI 流式响应已送达",
  duplicate_outbound: "重复消息已抑制",
  response_prepared: "响应已准备",
  command_completed: "命令已完成",
  turn_completed: "Turn 完成后清理",
  model_request_started: "模型请求已开始",
  model_request_failed: "模型请求失败",
  model_attempt_started: "模型尝试已开始",
  model_attempt_finished: "模型尝试已结束",
  tool_started: "Tool 已开始",
  tool_finished: "Tool 已结束",
  run_started: "Run 已开始",
  run_finished: "Run 已结束",
  turn_started: "Turn 已开始",
  iteration_started: "Iteration 已开始",
  iteration_finished: "Iteration 已结束",
  returned_to_caller: "已返回调用方",
  provider_route_decision: "Provider 路由决策",
  retry_scheduled: "已安排重试",
  continuation_requested: "已请求继续执行",
  finalization_requested: "已请求生成最终响应",
};

const ZH_NODE_TYPES: Record<AuditNodeType, string> = {
  run: "Run",
  task: "Task",
  model_call: "Model 调用",
  model_attempt: "Model 尝试",
  tool_call: "Tool 调用",
  decision: "决策",
  checkpoint: "Checkpoint",
  goal: "目标",
  turn_result: "Turn 结果",
  delivery: "Delivery",
  anomaly: "审计异常",
  external_reference: "外部引用",
};

function readableFallback(value: string): string {
  if (!value) return "-";
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isChinese(): boolean {
  if (typeof document === "undefined") return true;
  const language = document.documentElement.lang.toLowerCase();
  return !language || language.startsWith("zh");
}

export function auditStatusLabel(status: TraceDisplayStatus): string {
  return isChinese() ? ZH_STATUS[status] : readableFallback(status);
}

export function auditValueLabel(value: string | null | undefined): string {
  if (!value) return "-";
  return isChinese() ? (ZH_VALUES[value] ?? readableFallback(value)) : readableFallback(value);
}

export function auditNodeTypeLabel(type: AuditNodeType): string {
  return isChinese() ? ZH_NODE_TYPES[type] : readableFallback(type);
}
