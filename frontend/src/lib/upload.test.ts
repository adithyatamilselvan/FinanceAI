import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, tokenStore, uploadWithProgress } from "./api";

type Handler = (() => void) | null;
class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: Handler = null; onerror: Handler = null; onabort: Handler = null;
  status = 0; responseText = ""; headers: Record<string, string> = {}; method = ""; url = ""; body: unknown = null;
  constructor() { FakeXhr.last = this; }
  open(m: string, u: string) { this.method = m; this.url = u; }
  setRequestHeader(k: string, v: string) { this.headers[k] = v; }
  send(b: unknown) { this.body = b; }
  abort() { this.onabort?.(); }
}
const file = () => new File(["x"], "slip.png", { type: "image/png" });

afterEach(() => { vi.unstubAllGlobals(); tokenStore.clear(); });

describe("uploadWithProgress", () => {
  it("sends the file with the bearer token and reports real upload progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    tokenStore.set("tok");
    const seen: number[] = [];
    const p = uploadWithProgress<{ ok: boolean }>("/slips/batches/b1/files", file(), (f) => seen.push(f));
    const x = FakeXhr.last;
    expect(x.method).toBe("POST");
    expect(x.url).toBe("/api/v1/slips/batches/b1/files");
    expect(x.headers.Authorization).toBe("Bearer tok");
    expect((x.body as FormData).get("file")).toBeInstanceOf(File);
    x.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 200 });
    x.upload.onprogress?.({ lengthComputable: true, loaded: 200, total: 200 });
    x.status = 200; x.responseText = JSON.stringify({ ok: true }); x.onload?.();
    await expect(p).resolves.toEqual({ ok: true });
    expect(seen).toEqual([0.25, 1]);
  });

  it("turns server errors into readable ApiErrors (including the raw detail for per-item reasons)", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const p = uploadWithProgress("/x", file(), () => {});
    const x = FakeXhr.last;
    x.status = 409; x.responseText = JSON.stringify({ detail: "A batch can hold at most 20 files." }); x.onload?.();
    await expect(p).rejects.toMatchObject({ status: 409, message: "A batch can hold at most 20 files." });
  });

  it("reports an oversized request, a dead network and a user abort distinctly", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    let p = uploadWithProgress("/x", file(), () => {});
    FakeXhr.last.status = 413; FakeXhr.last.responseText = "not json"; FakeXhr.last.onload?.();
    await expect(p).rejects.toMatchObject({ status: 413, message: "The file is too large." });
    p = uploadWithProgress("/x", file(), () => {});
    FakeXhr.last.onerror?.();
    await expect(p).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Cannot reach the server") });
    const ac = new AbortController();
    p = uploadWithProgress("/x", file(), () => {}, ac.signal);
    ac.abort();
    await expect(p).rejects.toBeInstanceOf(ApiError);
  });
});
