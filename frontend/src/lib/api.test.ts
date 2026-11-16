import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, tokenStore } from "./api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => { vi.restoreAllMocks(); tokenStore.clear(); });

describe("api client", () => {
  it("sends the bearer token and JSON body", async () => {
    tokenStore.set("tok123");
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: true }));
    await api.post("/x", { a: 1 });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/x");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok123");
    expect(init.body).toBe('{"a":1}');
  });

  it("turns server validation errors into readable messages", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(422, { detail: "Validation failed", errors: [{ field: "amount", message: "must be positive" }] }));
    await expect(api.get("/x")).rejects.toMatchObject({ status: 422, message: "amount: must be positive" });
  });

  it("never exposes raw server errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(500, { detail: "Internal server error" }));
    await expect(api.get("/x")).rejects.toBeInstanceOf(ApiError);
  });

  it("reports network failure clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    await expect(api.get("/x")).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Cannot reach the server") });
  });

  it("handles 204 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.del("/x")).resolves.toBeUndefined();
  });

  it("drops empty query params", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, {}));
    await api.get("/t", { q: "", page: 2, category: undefined });
    expect(spy.mock.calls[0][0]).toBe("/api/v1/t?page=2");
  });
});
