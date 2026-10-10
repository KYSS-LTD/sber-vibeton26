import { test, expect } from "@playwright/test";
test("login screen renders on desktop and phone", async ({ page }) => {
  await page.route("**/auth/**", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: "{}" }),
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "Каждый проход",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Войти в систему" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
test("login screen uses local schematic without external images", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".landing-visual")).toBeVisible();
  expect(await page.locator('img[src^="http"]').count()).toBe(0);
});
