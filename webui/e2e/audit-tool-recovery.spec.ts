import { expect, test } from "@playwright/test";

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`recovery graph at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const errors: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/e2e/audit-tool-recovery.html");
    await expect(page.getByText("恢复链路：2 个节点 / 1 条边")).toBeVisible();
    await expect(page.getByText("Tool recovery Chromium fixture")).toBeVisible();
    const edge = page.locator(".react-flow__edge").first();
    await expect(edge.locator("path").first()).toHaveAttribute("d", /.+/);
    await edge.click({ force: true });
    await expect(page.getByRole("complementary", { name: "恢复关系检查器" })).toBeVisible();
    await page.getByRole("button", { name: "定位失败端 Event" }).click();
    await page.getByRole("button", { name: "定位恢复端 Event" }).click();
    await expect(page.getByTestId("located-events")).toContainText("failed-finished");
    await expect(page.getByTestId("located-events")).toContainText("recovered-finished");
    expect(errors).toEqual([]);
  });
}
