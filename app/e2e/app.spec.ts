import { test, expect } from "@playwright/test";

test.describe("Maelstrom App", () => {
  test("renders the app layout", async ({ page }) => {
    await page.goto("/");

    // Should show the Projects header
    await expect(page.getByText("Projects")).toBeVisible();

    // Should show the Agents header
    await expect(page.getByText("Agents")).toBeVisible();
  });

  test("shows coming soon panel", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("Coming Soon")).toBeVisible();
  });
});
