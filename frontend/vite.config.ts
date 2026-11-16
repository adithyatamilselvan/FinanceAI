/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Local development: forward API calls to the FastAPI backend (no CORS needed locally).
    proxy: { "/api": process.env.VITE_DEV_API_TARGET ?? "http://localhost:8000" },
  },
  test: { globals: true, environment: "jsdom", setupFiles: ["./src/test-setup.ts"], include: ["src/**/*.test.{ts,tsx}"], css: false },
});
