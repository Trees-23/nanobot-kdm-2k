import { beforeEach, describe, expect, it } from "vitest";

import { auditNodeTypeLabel, auditStatusLabel, auditValueLabel } from "@/lib/audit-display";

describe("audit display formatter", () => {
  beforeEach(() => {
    document.documentElement.lang = "zh-CN";
  });

  it("keeps technical nouns and translates known audit values", () => {
    expect(auditStatusLabel("incomplete")).toBe("不完整");
    expect(auditValueLabel("checkpoint_written")).toBe("Checkpoint 已写入");
    expect(auditValueLabel("accepted_by_adapter")).toBe("渠道已接收");
    expect(auditNodeTypeLabel("model_call")).toBe("Model 调用");
  });

  it("does not claim every suppressed Delivery is a duplicate", () => {
    expect(auditValueLabel("suppressed")).toBe("已抑制投递");
    expect(auditValueLabel("webui_stream_already_delivered")).toBe("WebUI 流式响应已送达");
  });

  it("provides a readable fallback for unknown values", () => {
    expect(auditValueLabel("future_delivery_state")).toBe("Future Delivery State");
  });

  it("falls back to readable English outside Chinese locales", () => {
    document.documentElement.lang = "en";
    expect(auditStatusLabel("succeeded")).toBe("Succeeded");
  });
});
