import { test, expect } from "@playwright/test";
import fs from "node:fs";
type Account = { username: string; password: string };
const file = process.env.E2E_ACCOUNTS_FILE;
test.skip(!file, "Set E2E_ACCOUNTS_FILE to test against real Keycloak");
async function login(page: any, username: string) {
  const accounts: Account[] = JSON.parse(fs.readFileSync(file!, "utf8"));
  const account = accounts.find((a) => a.username === username)!;
  await page.goto("/");
  await page.getByRole("button", { name: "Войти в систему" }).click();
  await page.locator("#username").fill(account.username);
  await page.locator("#password").fill(account.password);
  await page.locator("#kc-login").click();
  await expect(page.locator(".sidebar")).toBeVisible({ timeout: 20000 });
}
test("admin logs in, sees reports and publishes polygon zones", async ({
  page,
}) => {
  await login(page, "admin");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Обзор объекта",
  );
  const loaded = page.waitForResponse(
    (r) => r.url().endsWith("/map") && r.request().method() === "GET",
  );
  await page.getByRole("button", { name: "Схема и зоны", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Редактор зон" }),
  ).toBeVisible();
  const current = await (await loaded).json();
  if (current.zones.some((z: any) => z.name === "E2E зона"))
    await page
      .getByRole("button", { name: "Удалить E2E зона", exact: true })
      .click();
  // Leaflet + OpenStreetMap: рисуем квадрат внутри границы объекта.
  const map = page.locator(".geo-host");
  await expect(map).toBeVisible();
  await expect(map.locator(".leaflet-tile-pane")).toBeAttached();
  const bounds = await map.boundingBox();
  if (!bounds) throw new Error("Missing map");
  const [near, far] = [0.47, 0.53];
  for (const [x, y] of [
    [near, near],
    [far, near],
    [far, far],
    [near, far],
  ])
    await map.click({
      position: { x: bounds.width * x, y: bounds.height * y },
    });
  await page.getByPlaceholder("Название зоны").fill("E2E зона");
  await page.getByRole("button", { name: /Замкнуть зону/ }).click();
  const saved = page.waitForResponse(
    (r) => r.url().endsWith("/map") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Опубликовать версию" }).click();
  expect((await saved).status()).toBe(200);
  await page
    .getByRole("button", { name: "Heatmap посещений", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Heatmap посещений", level: 1 }),
  ).toBeVisible();
  await expect(page.locator(".zone-stat-list")).toContainText("E2E зона");
});
test("guard cannot fetch another site using its real JWT", async ({ page }) => {
  let authorization = "";
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/me"))
      authorization = request.headers()["authorization"] || "";
  });
  await login(page, "guard-a");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Проходная");
  expect(authorization).toMatch(/^Bearer /);
  const status = await page.evaluate(
    async (bearer) =>
      (
        await fetch(
          "/api/v1/sites/10000000-0000-0000-0000-000000000002/checkpoints",
          { headers: { Authorization: bearer } },
        )
      ).status,
    authorization,
  );
  expect(status).toBe(404);
});
test("worker makes no GPS calls before opening a shift on mobile", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).__geoCalls = 0;
    navigator.geolocation.getCurrentPosition = () => {
      (window as any).__geoCalls++;
    };
  });
  await login(page, "worker-a");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Мой пропуск",
  );
  await expect(
    page.getByRole("heading", { name: "Смена закрыта" }),
  ).toBeVisible();
  expect(await page.evaluate(() => (window as any).__geoCalls)).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("admin creates a real Keycloak employee and assigns the site", async ({
  page,
}) => {
  await login(page, "admin");
  await page.getByRole("button", { name: "Управление", exact: true }).click();
  const username = `e2e-${Date.now()}`;
  const password = `E2e-${crypto.randomUUID()}!`;
  await page.getByLabel("Логин", { exact: true }).fill(username);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByLabel("ФИО", { exact: true }).fill("Тестовый сотрудник E2E");
  await page.getByLabel("Табельный номер", { exact: true }).fill(username);
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/people") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Создать в Keycloak", exact: true })
    .click();
  const response = await created;
  expect(response.status()).toBe(201);
  const person = await response.json();
  await page
    .getByRole("combobox", { name: "Пользователь", exact: true })
    .selectOption(person.principal_id);
  await page
    .getByRole("combobox", { name: "Объект", exact: true })
    .first()
    .selectOption("10000000-0000-0000-0000-000000000001");
  const assigned = page.waitForResponse(
    (r) => r.url().endsWith("/assignments") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Сохранить назначение", exact: true })
    .click();
  expect((await assigned).status()).toBe(200);
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page
    .getByRole("button", { name: "Войти в систему", exact: true })
    .click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(password);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Мой пропуск",
  );
});

test("manager assistant invokes the local model and returns database facts", async ({
  page,
}) => {
  await login(page, "manager");
  await page.getByRole("button", { name: "Помощник", exact: true }).click();
  await page.locator("textarea").fill("Сколько сотрудников сейчас на объекте?");
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/assistant") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Спросить помощника", exact: true })
    .click();
  const result = await response;
  expect(result.status()).toBe(200);
  const payload = await result.json();
  expect(payload.provider).toBe("ollama");
  expect(payload.tools).toContain("onsite_now");
  await expect(page.locator(".assistant-answer")).toContainText(
    "Сейчас на объекте",
  );
  expect(
    payload.fact_ids.every((id: string) =>
      payload.facts.some((f: any) => f.id === id),
    ),
  ).toBeTruthy();
});
