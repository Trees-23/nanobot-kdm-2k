import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:5174", trace: "retain-on-failure" },
  webServer: { command: "bun run dev -- --host 127.0.0.1 --port 5174", url: "http://127.0.0.1:5174/e2e/audit-tool-recovery.html", reuseExistingServer: true },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
