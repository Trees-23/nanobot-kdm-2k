import { createHash } from "node:crypto";
import { createServer } from "node:net";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";

import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const secret = "real-audit-acceptance-secret";
let gateway: ChildProcess | null = null;
let baseUrl = "";
let traceId = "";
let runtimeRevision = 0;
let distHash = "";

async function freePort(): Promise<number> {
  return await new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("failed to allocate a local port"));
        return;
      }
      server.close(() => resolvePort(address.port));
    });
  });
}

async function waitForBootstrap(): Promise<{ api_token: string }> {
  const deadline = Date.now() + 20_000;
  let lastError = "gateway not ready";
  while (Date.now() < deadline) {
    if (gateway?.exitCode != null) throw new Error(`gateway exited with ${gateway.exitCode}`);
    try {
      const response = await fetch(`${baseUrl}/webui/bootstrap`, {
        headers: { "X-Nanobot-Auth": secret },
      });
      if (response.ok) return await response.json() as { api_token: string };
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error);
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`gateway did not start: ${lastError}`);
}

test.beforeAll(async () => {
  const root = mkdtempSync(join(tmpdir(), "nanobot-audit-real-"));
  const websocketPort = await freePort();
  const gatewayPort = await freePort();
  const configPath = join(root, "config.json");
  const auditRoot = join(root, "audit");
  const workspace = join(root, "workspace");
  const runtimeTrace = spawnSync("python", [
    "webui/e2e/generate-audit-tool-recovery-runtime.py",
    "--root", auditRoot,
    "--config", configPath,
    "--workspace", workspace,
    "--websocket-port", String(websocketPort),
    "--gateway-port", String(gatewayPort),
    "--secret", secret,
  ], { cwd: repositoryRoot, encoding: "utf-8" });
  if (runtimeTrace.status !== 0) throw new Error(runtimeTrace.stderr || runtimeTrace.stdout);
  const generated = JSON.parse(runtimeTrace.stdout.trim()) as {
    trace_id: string;
    revision: number;
    generator: string;
  };
  expect(generated.generator).toBe("AgentRunner+ReadFileTool+AuditRuntime");
  traceId = generated.trace_id;
  runtimeRevision = generated.revision;
  distHash = createHash("sha256")
    .update(readFileSync(join(repositoryRoot, "nanobot/web/dist/index.html")))
    .digest("hex");
  gateway = spawn("python", ["-m", "nanobot", "gateway", "--config", configPath], {
    cwd: repositoryRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  baseUrl = `http://127.0.0.1:${websocketPort}`;
  await waitForBootstrap();
});

test.afterAll(async () => {
  if (!gateway || gateway.exitCode != null) return;
  gateway.kill("SIGTERM");
  await new Promise<void>((resolveExit) => {
    const timer = setTimeout(() => {
      gateway?.kill("SIGKILL");
      resolveExit();
    }, 10_000);
    gateway?.once("exit", () => {
      clearTimeout(timer);
      resolveExit();
    });
  });
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`real Gateway recovery graph at ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const browserErrors: string[] = [];
    const requests: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") browserErrors.push(message.text());
    });
    page.on("pageerror", (error) => browserErrors.push(error.message));
    page.on("request", (request) => requests.push(new URL(request.url()).pathname));

    const bootstrap = await waitForBootstrap();
    const graphResponse = await fetch(`${baseUrl}/api/audit/traces/${traceId}/graph?level=trace_full`, {
      headers: { Authorization: `Bearer ${bootstrap.api_token}` },
    });
    expect(graphResponse.ok).toBeTruthy();
    const graph = await graphResponse.json() as {
      nodes: Array<{ id: string; summary: { tool_name?: string; error_message?: string; error_source?: string; retryability?: string; recovery_status?: string } }>;
      edges: Array<{ id: string; type: string; source: string; anchor?: { source_event_id?: string; target_event_id?: string } }>;
      index: { revision: number };
    };
    const recovery = graph.edges.find((edge) => edge.type === "tool_recovery");
    const retry = graph.edges.find((edge) => edge.type === "tool_retry");
    const continuation = graph.edges.find((edge) => edge.type === "tool_continuation");
    expect(recovery).toBeTruthy();
    expect(retry).toBeTruthy();
    expect(continuation).toBeTruthy();
    const failedRead = graph.nodes.find((node) => node.id === recovery!.source);
    expect(failedRead?.summary.error_message).toContain("File not found");
    expect(failedRead?.summary.error_source).toBe("tool_result");
    expect(failedRead?.summary.retryability).toBe("unknown");
    expect(failedRead?.summary.recovery_status).toBe("recovered");
    expect(graph.nodes.find((node) => node.id === retry!.source)?.summary.recovery_status).toBe("unresolved");
    expect(graph.nodes.find((node) => node.id === continuation!.source)?.summary.recovery_status).toBe("continued");

    const nodeParam = `&node=${encodeURIComponent(recovery!.source)}`;
    const route = `/#/traces/${encodeURIComponent(traceId)}?bootstrapSecret=${encodeURIComponent(secret)}${nodeParam}`;
    await page.goto(`${baseUrl}${route}`);
    await expect(page.getByTestId("trace-graph")).toBeVisible();
    const nodeInspector = page.getByRole("complementary", { name: "节点检查器" });
    await expect(nodeInspector).toBeVisible();
    await expect(nodeInspector).toContainText("File not found");
    await expect(nodeInspector).toContainText("错误来源");
    await expect(nodeInspector).toContainText("可重试性");
    await nodeInspector.getByRole("button", { name: "关闭节点检查器" }).click();
    await expect(nodeInspector).toBeHidden();
    const recoveryEdge = page.locator(`.react-flow__edge[data-id="${recovery!.id}"]`);
    await expect(recoveryEdge.locator("path").first()).toHaveAttribute("d", /.+/);
    const sequenceEdge = page.locator(`.react-flow__edge[data-id^="sequence:"]`).first();
    await expect(sequenceEdge.locator("path").first()).toHaveAttribute("d", /.+/);
    await expect(recoveryEdge.locator("path").first()).not.toHaveAttribute(
      "d",
      await sequenceEdge.locator("path").first().getAttribute("d") ?? "",
    );
    const selectRelation = async (edge: { id: string; type: string }) => {
      if (viewport.width < 768) {
        await page.getByTestId(`tool-relation-${edge.type}`).click();
        return;
      }
      await page.locator(`.react-flow__edge[data-id="${edge.id}"] .react-flow__edge-interaction`).click();
    };
    for (const [edge, title] of [
      [retry!, "Tool 重试关系"],
      [continuation!, "Tool 继续关系"],
      [recovery!, "Tool 恢复关系"],
    ] as const) {
      await selectRelation(edge);
      const relationInspector = page.getByRole("complementary", { name: "恢复关系检查器" });
      await expect(relationInspector).toContainText(title);
      await expect(relationInspector).toContainText("证据类型");
      await relationInspector.getByRole("button", { name: "关闭关系检查器" }).click();
    }
    await selectRelation(recovery!);

    const inspector = page.getByRole("complementary", { name: "恢复关系检查器" });
    await expect(inspector).toBeVisible();
    await expect(inspector).toContainText("证据计数");
    await expect(inspector).toContainText(recovery!.anchor!.source_event_id!);
    await expect(inspector).toContainText(recovery!.anchor!.target_event_id!);
    expect(requests.filter((path) => path.startsWith("/api/audit/payloads/"))).toHaveLength(0);

    await testInfo.attach(`tool-recovery-${viewport.width}x${viewport.height}.png`, {
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });

    await inspector.getByRole("button", { name: "定位失败端 Event" }).click();
    await expect(page.getByText("Event 时间线")).toBeVisible();
    if (viewport.width < 768) {
      await expect(page.getByRole("button", { name: /Event 时间线/ }).first())
        .toHaveAttribute("aria-expanded", "true");
    }
    const failedRow = page.locator(`[data-event-id="${recovery!.anchor!.source_event_id}"]`);
    await expect(failedRow).toHaveClass(/bg-sidebar-accent/);
    if (viewport.width < 768) {
      await page.getByRole("button", { name: /Event 时间线/ }).first().click();
      await expect(inspector).toBeVisible();
    }
    await inspector.getByRole("button", { name: "定位后续端 Event" }).click();
    const recoveredRow = page.locator(`[data-event-id="${recovery!.anchor!.target_event_id}"]`);
    await expect(recoveredRow).toHaveClass(/bg-sidebar-accent/);
    const selectedEventIds = await page.locator("[data-event-id]").evaluateAll((rows) => rows
      .filter((row) => row.className.includes("bg-sidebar-accent"))
      .map((row) => row.getAttribute("data-event-id")));
    expect(selectedEventIds).toEqual([recovery!.anchor!.target_event_id]);
    expect(requests.filter((path) => path.startsWith("/api/audit/payloads/"))).toHaveLength(0);
    expect(browserErrors).toEqual([]);

    await testInfo.attach("real-gateway-audit-evidence.json", {
      body: JSON.stringify({
        dist_sha256: distHash,
        runtime_revision: runtimeRevision,
        graph_revision: graph.index.revision,
        requests,
      }, null, 2),
      contentType: "application/json",
    });
  });
}
