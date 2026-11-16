import { defineConfig } from "@playwright/test";

// End-to-end tests run the REAL backend (FastAPI + MongoDB) and the REAL frontend dev server.
// They need a MongoDB reachable at E2E_MONGODB_URI (default: local mongod). A throwaway database is used and dropped.
const API_PORT = 8099;
const WEB_PORT = 5199;
export const E2E_DB = "financeai_e2e_run";
const MONGO = process.env.E2E_MONGODB_URI ?? "mongodb://localhost:27017";
const PY = process.platform === "win32" ? "..\\backend\\.venv\\Scripts\\python.exe" : "../backend/.venv/bin/python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./e2e/db.ts",
  globalTeardown: "./e2e/db.ts",
  use: { baseURL: `http://localhost:${WEB_PORT}`, trace: "retain-on-failure", acceptDownloads: true },
  webServer: [
    {
      command: `${PY} -m uvicorn app.main:app --port ${API_PORT}`,
      cwd: "../backend",
      url: `http://localhost:${API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ENVIRONMENT: "development", MONGODB_URI: MONGO, MONGODB_DB: E2E_DB, JWT_SECRET: "e2e-secret-e2e-secret-e2e-secret-123456",
        CORS_ORIGINS: `http://localhost:${WEB_PORT}`, AUTH_RATE_LIMIT_PER_MINUTE: "1000",
      },
    },
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_DEV_API_TARGET: `http://localhost:${API_PORT}` },
    },
  ],
});
