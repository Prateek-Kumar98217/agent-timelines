import { defineConfig } from "@playwright/test";

const API_PORT = 8001;
const WEB_PORT = 3002;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  // The scripted model runs one step at a time, and every test shares its branch limit.
  workers: 1,
  reporter: "line",
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "uv run python web/e2e/api_server.py",
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      env: { E2E_API_PORT: String(API_PORT) },
      reuseExistingServer: false,
      timeout: 120_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
    {
      command: `npx next start -H 127.0.0.1 -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
