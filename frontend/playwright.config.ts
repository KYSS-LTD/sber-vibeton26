import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
    ignoreHTTPSErrors: true,
    channel: process.env.E2E_CHANNEL || undefined,
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:3000",
        reuseExistingServer: true,
      },
  reporter: "list",
});
