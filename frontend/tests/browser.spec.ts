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
test("PWA manifest, icons, service worker and offline page are served", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
    "href",
    "/manifest.json",
  );
  const manifest = await (await request.get("/manifest.json")).json();
  expect(manifest.display).toBe("standalone");
  expect(manifest.start_url).toBe("/");
  expect(manifest.icons.map((i: any) => i.sizes)).toEqual(
    expect.arrayContaining(["192x192", "512x512"]),
  );
  expect(manifest.icons.some((i: any) => i.purpose === "maskable")).toBe(true);
  for (const icon of manifest.icons)
    expect((await request.get(icon.src)).ok(), icon.src).toBeTruthy();
  const worker = await request.get("/sw.js");
  expect(worker.headers()["content-type"]).toContain("javascript");
  expect(worker.headers()["cache-control"]).toContain("no-cache");
  expect((await request.get("/offline.html")).ok()).toBeTruthy();
});
