// Thin fetch wrapper: attaches the JWT, normalises errors, never swallows them.
const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const TOKEN_KEY = "financeai.token";

export const tokenStore = {
  get: (): string | null => {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  set: (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* storage unavailable */ } },
  clear: () => { try { localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
};

export class ApiError extends Error {
  status: number;
  fieldErrors: { field: string; message: string }[];
  detail: unknown; // the server's raw `detail` (e.g. per-item reasons when an import is refused)
  constructor(status: number, message: string, fieldErrors: { field: string; message: string }[] = [], detail: unknown = null) {
    super(message);
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.detail = detail;
  }
}

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export type Params = Record<string, string | number | boolean | null | undefined>;

function url(path: string, params?: Params): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return `${BASE}/api/v1${path}${s ? `?${s}` : ""}`;
}

async function parseError(res: Response): Promise<ApiError> {
  let detail: unknown = null;
  let errors: { field: string; message: string }[] = [];
  try {
    const j = await res.json();
    detail = j.detail;
    if (Array.isArray(j.errors)) errors = j.errors;
  } catch { /* non-JSON body */ }
  let msg = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail ? String((detail as { message: string }).message) : "";
  if (!msg) msg = res.status >= 500 ? "The server had a problem. Please try again." : `Request failed (${res.status})`;
  if (errors.length && msg === "Validation failed") msg = errors.map((e) => `${e.field}: ${e.message}`).join("; ");
  return new ApiError(res.status, msg, errors, detail);
}

async function raw(method: string, path: string, opts: { params?: Params; body?: unknown; form?: FormData } = {}): Promise<Response> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  let res: Response;
  try {
    res = await fetch(url(path, opts.params), { method, headers, body });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && token) onUnauthorized();
  if (!res.ok) throw await parseError(res);
  return res;
}

export async function request<T>(method: string, path: string, opts: { params?: Params; body?: unknown; form?: FormData } = {}): Promise<T> {
  const res = await raw(method, path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string, params?: Params) => request<T>("GET", path, { params }),
  post: <T,>(path: string, body?: unknown, params?: Params) => request<T>("POST", path, { body: body ?? {}, params }),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", path, { body }),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", path, { body }),
  del: (path: string) => request<void>("DELETE", path),
  upload: <T,>(path: string, form: FormData) => request<T>("POST", path, { form }),
};

/** Download a file endpoint (needs the auth header, so it cannot be a plain link). */
export async function download(path: string, filename: string, params?: Params): Promise<void> {
  const res = await raw("GET", path, { params });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/** Upload one file with real upload-progress events (fetch cannot report upload progress). */
export function uploadWithProgress<T>(path: string, file: File, onProgress: (fraction: number) => void, signal?: AbortSignal, extra?: Record<string, string>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url(path));
    const token = tokenStore.get();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 401 && token) onUnauthorized();
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T);
      const b = (body ?? {}) as { detail?: unknown; errors?: { field: string; message: string }[] };
      const objMsg = b.detail && typeof b.detail === "object" && "message" in b.detail ? String((b.detail as { message: string }).message) : "";
      let msg = typeof b.detail === "string" ? b.detail : objMsg || (xhr.status === 413 ? "The file is too large." : xhr.status >= 500 ? "The server had a problem. Please try again." : `Upload failed (${xhr.status})`);
      if (b.errors?.length && msg === "Validation failed") msg = b.errors.map((e) => `${e.field}: ${e.message}`).join("; ");
      reject(new ApiError(xhr.status, msg, b.errors ?? [], b.detail));
    };
    xhr.onerror = () => reject(new ApiError(0, "Cannot reach the server. Check your connection and try again."));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
    signal?.addEventListener("abort", () => xhr.abort());
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(extra ?? {})) fd.append(k, v);
    xhr.send(fd);
  });
}
