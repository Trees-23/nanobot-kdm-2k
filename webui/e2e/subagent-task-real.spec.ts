import { createServer } from "node:net";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";

import { expect, test } from "@playwright/test";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const secret = "real-subagent-task-acceptance-secret";
const chatId = "subagent-task-real-20260803";
let gateway: ChildProcess | null = null;
let baseUrl = "";
let workspace = "";

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

function mutate(action: "seed" | "serial" | "reconnect" | "finish" | "after-dismiss"): void {
  const result = spawnSync("python", [
    "webui/e2e/generate-subagent-task-runtime.py",
    "--action", action,
    "--workspace", workspace,
  ], { cwd: repositoryRoot, encoding: "utf-8" });
  if (result.status !== 0) throw new Error(result.stderr || result.stdout);
}

async function waitForBootstrap(): Promise<void> {
  const deadline = Date.now() + 20_000;
  let lastError = "gateway not ready";
  while (Date.now() < deadline) {
    if (gateway?.exitCode != null) throw new Error(`gateway exited with ${gateway.exitCode}`);
    try {
      const response = await fetch(`${baseUrl}/webui/bootstrap`, {
        headers: { "X-Nanobot-Auth": secret },
      });
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error);
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`gateway did not start: ${lastError}`);
}

test.beforeAll(async () => {
  const root = mkdtempSync(join(tmpdir(), "nanobot-subagent-real-"));
  const websocketPort = await freePort();
  const gatewayPort = await freePort();
  const configPath = join(root, "config.json");
  workspace = join(root, "workspace");
  const generated = spawnSync("python", [
    "webui/e2e/generate-subagent-task-runtime.py",
    "--action", "initial",
    "--workspace", workspace,
    "--config", configPath,
    "--websocket-port", String(websocketPort),
    "--gateway-port", String(gatewayPort),
    "--secret", secret,
  ], { cwd: repositoryRoot, encoding: "utf-8" });
  if (generated.status !== 0) throw new Error(generated.stderr || generated.stdout);
  gateway = spawn("python", ["-m", "nanobot", "gateway", "--config", configPath], {
    cwd: repositoryRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  baseUrl = `http://127.0.0.1:${websocketPort}`;
  await waitForBootstrap();
  mutate("seed");
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

test("real Gateway task lifecycle remains coherent on desktop, reconnect, and mobile", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const browserErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
  });

  const route = `/#/chat/${encodeURIComponent(`websocket:${chatId}`)}?bootstrapSecret=${encodeURIComponent(secret)}`;
  await page.goto(`${baseUrl}${route}`);
  await expect(page.getByText("2 active · 7 subagent tasks")).toBeVisible();
  const toggle = page.getByRole("button", { name: "Show subagent task details" });
  await toggle.click();
  const dialog = page.getByRole("dialog", { name: "Subagent task details" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-task-id]")).toHaveCount(2);
  await expect(dialog).toContainText("Research protocol evidence");
  await expect(dialog).toContainText("Inspect compatibility tests");
  await expect(dialog).not.toContainText("Result pending delivery");
  await page.getByRole("button", { name: "All" }).click();
  for (const expected of [
    "Research protocol evidence",
    "Inspect compatibility tests",
    "Result pending delivery",
    "Delivery failed evidence",
    "Lost executor evidence",
    "Timed out child",
    "Cancelled child",
    "ready",
    "delivery failed",
    "lost",
    "timed out",
    "cancelled",
    "150 tokens",
  ]) {
    await expect(dialog).toContainText(expected, { ignoreCase: true });
  }
  const initialRows = await dialog.locator("[data-task-id]").allInnerTexts();
  const stableHeight = (await page.locator(".composer-status-strip").boundingBox())?.height;

  mutate("serial");
  await expect(page.getByText("2 active · 8 subagent tasks")).toBeVisible();
  await expect(dialog).toContainText("Verify delivered result then delegate");
  await expect(dialog.locator('[data-task-id="task-parallel-a"]')).toContainText("delivered", {
    ignoreCase: true,
  });
  expect((await page.locator(".composer-status-strip").boundingBox())?.height).toBe(stableHeight);

  const rowsBeforeRefresh = await dialog.locator("[data-task-id]").allInnerTexts();
  await page.reload();
  await expect(page.getByText("2 active · 8 subagent tasks")).toBeVisible();
  await page.getByRole("button", { name: "Show subagent task details" }).click();
  const refreshedDialog = page.getByRole("dialog", { name: "Subagent task details" });
  await expect(refreshedDialog).toBeVisible();
  await expect(refreshedDialog.locator("[data-task-id]")).toHaveCount(2);
  await page.getByRole("button", { name: "All" }).click();
  expect(await refreshedDialog.locator("[data-task-id]").allInnerTexts()).toEqual(rowsBeforeRefresh);

  await page.context().setOffline(true);
  mutate("reconnect");
  await page.context().setOffline(false);
  await expect(page.getByText("3 active · 9 subagent tasks")).toBeVisible({ timeout: 15_000 });
  await expect(refreshedDialog).toContainText("Restore after reconnect");

  await page.setViewportSize({ width: 390, height: 844 });
  const box = await refreshedDialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(844);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
    .toBe(true);

  await page.getByRole("button", { name: "Hide completed" }).click();
  await expect(page.getByText("3 active · 3 subagent tasks")).toBeVisible();
  mutate("finish");
  await expect(page.getByText("No active · 3 subagent tasks")).toBeVisible();
  await page.getByRole("button", { name: "Dismiss completed subagent tasks" }).click();
  await expect(page.locator(".composer-status-strip")).toHaveCount(0);

  mutate("after-dismiss");
  await expect(page.getByText("1 active · 1 subagent task")).toBeVisible();
  await page.getByRole("button", { name: "Show subagent task details" }).click();
  await expect(page.getByRole("dialog", { name: "Subagent task details" })).toContainText(
    "Visible after terminal history is dismissed",
  );

  await testInfo.attach("subagent-task-desktop-and-mobile.png", {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
  expect(initialRows).toHaveLength(7);
  expect(browserErrors).toEqual([]);
  expect(failedResponses).toEqual([]);
});
