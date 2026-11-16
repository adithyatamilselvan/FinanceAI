import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";
import { Button, Card, ErrorBox, Field, Input, Modal, Notice, PageHeader, ProgressBar, Select, Spinner } from "../components/ui";
import { api, ApiError, download, uploadWithProgress } from "../lib/api";
import { useAction, useCategories, useGet } from "../lib/hooks";
import { CURRENCIES, formatMoney, toInputString, toMinor } from "../lib/money";
import type { SlipBatch, SlipItem, SlipStatus } from "../lib/types";

const STORE_KEY = "financeai.slipBatch";
const store = {
  get: () => { try { return sessionStorage.getItem(STORE_KEY); } catch { return null; } },
  set: (v: string) => { try { sessionStorage.setItem(STORE_KEY, v); } catch { /* storage unavailable */ } },
  clear: () => { try { sessionStorage.removeItem(STORE_KEY); } catch { /* storage unavailable */ } },
};

type QState = "queued" | "uploading" | "reading" | "done" | "failed";
type QItem = { key: string; file: File; state: QState; progress: number; error?: string };
const STATE_LABEL: Record<QState, string> = { queued: "Waiting", uploading: "Uploading…", reading: "Reading text (OCR)…", done: "Read", failed: "Failed" };
const NOT_SELECTABLE = new Set(["failed", "payment_failed", "already_imported", "missing_details", "imported"]);

export default function Slips() {
  const qc = useQueryClient();
  const status = useGet<SlipStatus>("/slips/status");
  const [batchId, setBatchId] = useState<string | null>(store.get());
  const [queue, setQueue] = useState<QItem[]>([]);
  const [processing, setProcessing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allowNonSuccessful, setAllowNonSuccessful] = useState(false);
  const [allowDuplicates, setAllowDuplicates] = useState(false);
  // Keep only the slip ID. The dialog always resolves the CURRENT record from the batch data (never a click-time snapshot),
  // and is keyed by that ID so React can never reuse one slip's form state for another.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [rejected, setRejected] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [dragOver, setDragOver] = useState(false);
  const seen = useRef<Set<string>>(new Set());
  const abort = useRef<AbortController | null>(null);
  const cancelled = useRef(false);

  const batch = useGet<SlipBatch>(batchId ? `/slips/batches/${batchId}` : "/slips/none", undefined, !!batchId && !processing);
  useEffect(() => {
    if (batch.error instanceof ApiError && batch.error.status === 404) { store.clear(); setBatchId(null); }
  }, [batch.error]);
  useEffect(() => {
    const items = batch.data?.items;
    if (!items) return;
    // Decide which items are new OUTSIDE the state updater (updaters must be pure; React StrictMode runs them twice).
    const fresh = items.filter((it) => !seen.current.has(it.id));
    fresh.forEach((it) => seen.current.add(it.id));
    setSelected((prev) => {
      const next = new Set(prev);
      for (const it of fresh) if (it.default_selected) next.add(it.id);
      for (const it of items) if (NOT_SELECTABLE.has(it.import_status)) next.delete(it.id);
      return next;
    });
  }, [batch.data]);

  const limits = status.data?.limits;
  const maxBytes = limits?.max_file_bytes ?? 5 * 1024 * 1024;
  const maxFiles = limits?.max_files_per_batch ?? 20;
  const data = batch.data;
  const open = data?.status === "open";

  function validate(files: File[]): { ok: File[]; bad: string[] } {
    const ok: File[] = [], bad: string[] = [];
    const room = maxFiles - (data?.items.length ?? 0);
    for (const f of files) {
      if (!/\.(jpe?g|png|pdf)$/i.test(f.name)) bad.push(`${f.name}: only JPG, JPEG, PNG and PDF files are supported`);
      else if (f.size > maxBytes) bad.push(`${f.name}: larger than ${Math.round(maxBytes / 1048576)} MB`);
      else if (f.size === 0) bad.push(`${f.name}: the file is empty`);
      else if (ok.length >= room) bad.push(`${f.name}: a batch can hold at most ${maxFiles} files`);
      else ok.push(f);
    }
    return { ok, bad };
  }

  async function start(files: File[]) {
    setError(null); setNotice(null);
    const { ok, bad } = validate(files);
    setRejected(bad);
    if (!ok.length) return;
    cancelled.current = false;
    abort.current = new AbortController();
    const q: QItem[] = ok.map((file, i) => ({ key: `${Date.now()}-${i}-${file.name}`, file, state: "queued", progress: 0 }));
    setQueue(q);
    setProcessing(true);
    try {
      let bid = batchId;
      if (!bid || data?.status !== "open") {
        const r = await api.post<{ id: string }>("/slips/batches");
        bid = r.id; seen.current = new Set(); setSelected(new Set());
        setBatchId(bid); store.set(bid);
      }
      const patch = (key: string, p: Partial<QItem>) => setQueue((cur) => cur.map((x) => (x.key === key ? { ...x, ...p } : x)));
      for (const item of q) {  // one file at a time: bounded memory and CPU on the server
        if (cancelled.current) break;
        patch(item.key, { state: "uploading", progress: 0 });
        try {
          const res = await uploadWithProgress<{ item: SlipItem }>(`/slips/batches/${bid}/files`, item.file, (f) => patch(item.key, { progress: f, state: f >= 1 ? "reading" : "uploading" }), abort.current.signal);
          patch(item.key, res.item.ok ? { state: "done", progress: 1 } : { state: "failed", progress: 1, error: res.item.error ?? "Could not read this file" });
        } catch (e) {
          if (cancelled.current) break;
          patch(item.key, { state: "failed", error: e instanceof Error ? e.message : "Upload failed" });
        }
      }
    } catch (e) {
      setError(e);
    } finally {
      setProcessing(false);
      void qc.invalidateQueries();
    }
  }

  const cancelBatch = useAction(async () => {
    cancelled.current = true; abort.current?.abort();
    if (batchId) await api.del(`/slips/batches/${batchId}`).catch((e) => { if (!(e instanceof ApiError && e.status === 404)) throw e; });
  }, () => { store.clear(); setBatchId(null); setQueue([]); setSelected(new Set()); seen.current = new Set(); setNotice("Batch cancelled. Everything extracted from it was deleted."); setProcessing(false); });

  const verify = useAction((id: string) => api.patch<unknown>(`/slips/batches/${batchId}/items/${id}`, { verified: true }));
  const commit = useAction(() => api.post<{ imported: number; skipped_duplicates: number }>(`/slips/batches/${batchId}/commit`,
    { item_ids: [...selected], allow_non_successful: allowNonSuccessful, allow_possible_duplicates: allowDuplicates }),
  (r) => { setNotice(`Imported ${r.imported} transaction${r.imported === 1 ? "" : "s"}${r.skipped_duplicates ? ` (${r.skipped_duplicates} skipped as identical duplicates)` : ""}.`); });

  const [dlError, setDlError] = useState<unknown>(null);
  async function downloadExcel() {
    setDlError(null);
    try { await download(`/slips/batches/${batchId}/workbook.xlsx`, "payment-slips.xlsx"); } catch (e) { setDlError(e); }
  }

  const onDrop = (e: DragEvent) => { e.preventDefault(); setDragOver(false); if (!processing && (open || !data)) void start([...e.dataTransfer.files]); };
  const items = data?.items ?? [];
  const good = items.filter((i) => i.ok);
  const editing = editingId ? items.find((i) => i.id === editingId) ?? null : null;
  const failed = items.filter((i) => !i.ok);
  const sel = useMemo(() => good.filter((i) => selected.has(i.id)), [good, selected]);
  const done = queue.filter((q) => q.state === "done" || q.state === "failed").length;
  const imported = data?.status === "imported";
  const cur = "INR";
  const commitProblems = commit.error instanceof ApiError && commit.error.detail && typeof commit.error.detail === "object" ? (commit.error.detail as { items?: { id: string; file: string; reasons: string[] }[] }).items ?? [] : [];

  return (
    <div>
      <PageHeader title="Payment Slip Scanner">
        {batchId && !imported && <Button variant="secondary" disabled={cancelBatch.isPending} onClick={() => { if (window.confirm("Cancel this batch and delete everything extracted from it?")) cancelBatch.mutate(undefined); }}>Cancel batch</Button>}
      </PageHeader>
      <Notice tone="info">
        Upload photos or PDFs of payment slips and receipts (JPG, JPEG, PNG, PDF; up to {maxFiles} files per batch, {Math.round(maxBytes / 1048576)} MB each, {limits?.max_pdf_pages ?? 5} pages per PDF).
        Text is read on the server with free, local OCR; nothing is sent to a third party and the files are not stored. OCR can make mistakes, so <b>nothing is saved to your transactions until you review and confirm it</b>.
      </Notice>
      {status.data && !status.data.available && <div className="mt-3"><Notice tone="warn">The OCR engine is not available on this server ({status.data.reason}). Slips cannot be read until Tesseract is installed. See the README.</Notice></div>}
      {notice && <div className="mt-3" role="status"><Notice tone="good">{notice} {imported && <><Link className="underline" to="/transactions">View transactions</Link> · <Link className="underline" to="/">Dashboard</Link> · <Link className="underline" to="/analytics">Analytics</Link></>}</Notice></div>}
      {error ? <div className="mt-3"><ErrorBox error={error} /></div> : null}

      {(!batchId || open || (!data && !batch.isLoading)) && !imported && (
        <Card title={items.length ? "Add more slips" : "1. Choose your slips"} className="mt-4">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={onDrop}
            className={`rounded-lg border-2 border-dashed p-8 text-center ${dragOver ? "border-blue-500 bg-blue-50" : "border-slate-300 bg-slate-50"}`}
          >
            <p className="font-medium">Drag and drop payment slips here</p>
            <p className="my-1 text-sm text-slate-500">or</p>
            <label className="inline-block cursor-pointer rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-hover">
              Choose files
              <input type="file" multiple accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" aria-label="Payment slip files" className="sr-only" disabled={processing || status.data?.available === false}
                onChange={(e) => { const f = [...(e.target.files ?? [])]; e.target.value = ""; void start(f); }} />
            </label>
            <p className="mt-2 text-xs text-slate-500">You can select many files at once (Ctrl/Shift-click).</p>
          </div>
          {rejected.length > 0 && <ul role="alert" className="mt-3 list-disc pl-5 text-sm text-red-700">{rejected.map((r) => <li key={r}>{r}</li>)}</ul>}
        </Card>
      )}

      {queue.length > 0 && (
        <Card title={processing ? `Reading slips: ${done} of ${queue.length} done` : "Processing results"} className="mt-4">
          <ProgressBar pct={(done / queue.length) * 100} />
          <ul className="mt-3 divide-y divide-slate-100 text-sm" aria-label="Per-file status">
            {queue.map((q) => (
              <li key={q.key} className="flex items-center justify-between gap-3 py-1.5">
                <span className="min-w-0 truncate">{q.file.name} <span className="text-xs text-slate-500">({(q.file.size / 1024).toFixed(0)} KB)</span></span>
                <span className="flex shrink-0 items-center gap-2">
                  {q.state === "uploading" && <span className="w-24"><ProgressBar pct={q.progress * 100} /></span>}
                  <span data-state={q.state} className={q.state === "failed" ? "text-red-700" : q.state === "done" ? "text-green-700" : "text-slate-600"}>{STATE_LABEL[q.state]}{q.error ? `: ${q.error}` : ""}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {batchId && batch.isLoading && !processing && <Spinner />}
      {batch.error && !(batch.error instanceof ApiError && batch.error.status === 404) ? <div className="mt-3"><ErrorBox error={batch.error} retry={() => batch.refetch()} /></div> : null}

      {data && items.length > 0 && !processing && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm lg:grid-cols-5">
            {[["Files", data.summary.total_files], ["Read successfully", data.summary.processed], ["Could not read", data.summary.failed], ["Need review", data.summary.requiring_review], ["Possible duplicates", data.summary.possible_duplicates]].map(([l, v]) => (
              <div key={l as string} className="rounded-lg border border-slate-200 bg-surface p-3"><div className="text-xs uppercase text-slate-500">{l}</div><div className="text-xl font-semibold">{v}</div></div>
            ))}
          </div>

          {failed.length > 0 && (
            <Card title="Files that could not be read" className="mt-4">
              <ul className="space-y-1 text-sm">{failed.map((f) => <li key={f.id}><b>{f.file}</b>: <span className="text-red-700">{f.error}</span></li>)}</ul>
            </Card>
          )}

          {good.length > 0 && (
            <Card title={imported ? "Imported slips" : "2. Review, correct and confirm"} className="mt-4">
              {!imported && <p className="mb-2 text-sm text-slate-600">Only successful, complete, unflagged slips are ticked by default. <span className="rounded bg-amber-100 px-1">Amber</span> = please check against the slip; <span className="rounded bg-red-100 px-1">red</span> = missing. Use <b>Edit</b> to correct mistakes.</p>}
              <div className="overflow-x-auto rounded-lg border border-slate-200">
                <table className="w-full text-left text-sm" aria-label="Extracted transactions">
                  <thead className="bg-slate-100 text-xs uppercase text-slate-600">
                    <tr><th className="p-2">Import</th><th className="p-2">File</th><th className="p-2">Date</th><th className="p-2 text-right">Amount</th><th className="p-2">Type</th><th className="p-2">Merchant / sender</th><th className="p-2">Payment</th><th className="p-2">Status</th><th className="p-2">Review</th><th className="p-2"><span className="sr-only">Actions</span></th></tr>
                  </thead>
                  <tbody>
                    {good.map((it) => <Row key={it.id} it={it} cur={cur} imported={imported} checked={selected.has(it.id)}
                      onCheck={(v) => setSelected((p) => { const n = new Set(p); if (v) n.add(it.id); else n.delete(it.id); return n; })}
                      onEdit={() => setEditingId(it.id)} onVerify={() => verify.mutate(it.id)} />)}
                  </tbody>
                </table>
              </div>
              {verify.error ? <div className="mt-2"><ErrorBox error={verify.error} /></div> : null}

              {!imported && (
                <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
                  <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={allowNonSuccessful} onChange={(e) => setAllowNonSuccessful(e.target.checked)} />
                    <span>Allow selected <b>pending, refunded or unclear</b> payments to be imported (failed payments are never imported)</span></label>
                  <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={allowDuplicates} onChange={(e) => setAllowDuplicates(e.target.checked)} />
                    <span>Allow selected <b>possible duplicates</b> to be imported (identical payments already saved are always blocked)</span></label>
                  {commit.error ? (
                    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
                      <div className="font-medium">{commit.error instanceof Error ? commit.error.message : "Import failed"}</div>
                      {commitProblems.length > 0 && <ul className="mt-1 list-disc pl-5">{commitProblems.map((p) => <li key={p.id}><b>{p.file}</b>: {p.reasons.join("; ")}</li>)}</ul>}
                    </div>
                  ) : null}
                  {dlError ? <ErrorBox error={dlError} /> : null}
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => void downloadExcel()}>Download Excel</Button>
                    <Button disabled={sel.length === 0 || commit.isPending} onClick={() => commit.mutate(undefined)}>{commit.isPending ? "Importing…" : `Confirm and import ${sel.length} selected`}</Button>
                  </div>
                </div>
              )}
              {imported && <div className="mt-3 flex gap-2"><Button variant="secondary" onClick={() => void downloadExcel()}>Download Excel</Button><Button onClick={() => { store.clear(); setBatchId(null); setQueue([]); setNotice(null); }}>Start a new batch</Button></div>}
            </Card>
          )}
        </>
      )}
      {editing && batchId && <EditModal key={editing.id} batchId={batchId} item={editing} onClose={() => setEditingId(null)} />}
    </div>
  );
}

function Flag({ it, field, children }: { it: SlipItem; field: string; children: React.ReactNode }) {
  const missing = it.missing.includes(field) && !(field === "merchant" && it.fields.sender);
  const unc = it.uncertain[field];
  const cls = missing ? "bg-red-100" : unc && !it.edited.includes(field) ? "bg-amber-100" : "";
  return <td className={`p-2 ${cls}`} title={missing ? "Missing: not found on the slip" : unc ?? undefined}>{missing ? <span className="text-red-700">missing</span> : children}</td>;
}

function Row({ it, cur, imported, checked, onCheck, onEdit, onVerify }: { it: SlipItem; cur: string; imported: boolean; checked: boolean; onCheck: (v: boolean) => void; onEdit: () => void; onVerify: () => void }) {
  const f = it.fields;
  const blocked = NOT_SELECTABLE.has(it.import_status);
  const reasons = [...it.review_reasons, ...(it.duplicate ? [it.duplicate.detail] : []), ...(f.status !== "successful" ? [`status: ${f.status}`] : [])];
  return (
    <tr className={`border-t border-slate-100 align-top ${checked ? "" : "text-slate-600"}`} data-status={it.import_status}>
      <td className="p-2"><input type="checkbox" aria-label={`Import ${it.file}`} checked={checked} disabled={blocked || imported} onChange={(e) => onCheck(e.target.checked)} /></td>
      <td className="max-w-[10rem] truncate p-2" title={it.file}>{it.file}<div className="text-xs text-slate-500">{it.method === "pdf-text" ? "PDF text" : it.method === "ocr-image" ? "image OCR" : it.method}{it.ocr_confidence !== null && ` · ${Math.round(it.ocr_confidence)}%`}</div></td>
      <Flag it={it} field="date">{f.date}{f.time ? ` ${f.time}` : ""}</Flag>
      <Flag it={it} field="amount"><div className="text-right">{f.amount !== null ? formatMoney(f.amount, f.currency ?? cur) : ""}{f.currency && f.currency !== "INR" ? ` ${f.currency}` : ""}{!f.currency && f.amount !== null ? <div className="text-xs text-red-700">currency?</div> : null}</div></Flag>
      <Flag it={it} field="type"><span className="capitalize">{f.type}</span></Flag>
      <Flag it={it} field="merchant">{f.merchant ?? f.sender}{f.merchant && f.sender ? <div className="text-xs text-slate-500">from {f.sender}</div> : null}</Flag>
      <td className="p-2">{f.payment_method ?? <span className="text-slate-400">—</span>}{f.reference && <div className="text-xs text-slate-500">ref {f.reference}</div>}</td>
      <td className="p-2 capitalize">{f.status}</td>
      <td className="p-2 text-xs">
        <div className={`font-medium ${it.import_status === "ready" || it.import_status === "imported" ? "text-green-700" : it.import_status === "payment_failed" || it.import_status === "already_imported" ? "text-red-700" : "text-amber-700"}`}>{it.import_status_label}</div>
        {!imported && reasons.length > 0 && <ul className="mt-0.5 list-disc pl-4 text-slate-600">{reasons.slice(0, 3).map((r) => <li key={r}>{r}</li>)}</ul>}
        {it.verified && !imported && <div className="text-green-700">verified by you</div>}
      </td>
      <td className="whitespace-nowrap p-2 text-right">
        {!imported && <Button variant="ghost" onClick={onEdit} aria-label={`Edit ${it.file}`}>Edit</Button>}
        {!imported && !it.verified && it.review_reasons.length > 0 && !it.missing.some((m) => ["date", "amount", "type", "currency"].includes(m)) && <Button variant="ghost" onClick={onVerify} aria-label={`Mark ${it.file} verified`}>Mark verified</Button>}
      </td>
    </tr>
  );
}

const optText = z.string().max(80);
const schema = z.object({
  date: z.string().refine((v) => v === "" || /^\d{4}-\d{2}-\d{2}$/.test(v), "Pick a valid date"),
  time: z.string().refine((v) => v === "" || /^([01]\d|2[0-3]):[0-5]\d$/.test(v), "Use 24-hour HH:MM"),
  amount: z.string().refine((v) => v.trim() === "" || (toMinor(v) !== null && toMinor(v)! > 0), "Enter a positive amount with up to 2 decimals"),
  currency: z.string(), type: z.string(), merchant: optText, sender: optText, payment_method: z.string().max(30), reference: z.string().max(40), provider: z.string().max(40),
  status: z.string(), description: z.string().max(200), category: z.string(), verified: z.boolean(),
});
type Form = z.infer<typeof schema>;

function EditModal({ batchId, item, onClose }: { batchId: string; item: SlipItem; onClose: () => void }) {
  const f = item.fields;
  const cats = useCategories();
  const [showText, setShowText] = useState(false);
  const text = useGet<{ text: string }>(`/slips/batches/${batchId}/items/${item.id}/text`, undefined, showText);
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: { date: f.date ?? "", time: f.time ?? "", amount: f.amount !== null ? toInputString(f.amount) : "", currency: f.currency ?? "", type: f.type ?? "", merchant: f.merchant ?? "", sender: f.sender ?? "",
      payment_method: f.payment_method ?? "", reference: f.reference ?? "", provider: f.provider ?? "", status: f.status ?? "unknown", description: f.description ?? "", category: item.category ?? "", verified: item.verified },
  });
  const save = useAction(async (v: Form) => {
    const d = form.formState.dirtyFields;
    const body: Record<string, unknown> = {};
    const nul = (s: string) => (s.trim() === "" ? null : s.trim());
    if (d.date) body.date = nul(v.date);
    if (d.time) body.time = nul(v.time);
    if (d.amount) body.amount = v.amount.trim() === "" ? null : toMinor(v.amount);
    if (d.currency) body.currency = nul(v.currency);
    if (d.type) body.type = nul(v.type);
    for (const k of ["merchant", "sender", "payment_method", "reference", "provider", "description"] as const) if (d[k]) body[k] = nul(v[k]);
    if (d.status) body.status = v.status;
    if (d.category) body.category = nul(v.category);
    if (v.verified !== item.verified || Object.keys(body).length === 0) body.verified = v.verified;
    return api.patch(`/slips/batches/${batchId}/items/${item.id}`, body);
  }, onClose);
  const e = form.formState.errors;
  const hint = (k: string) => (item.missing.includes(k) ? "Not found on the slip: please fill in" : item.uncertain[k] && !item.edited.includes(k) ? `Check: ${item.uncertain[k]}` : item.notes[k]);
  const cs = cats.data?.categories ?? [];
  return (
    <Modal title={`Edit: ${item.file}`} onClose={onClose}>
      <form onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Date" error={e.date?.message} hint={hint("date")}><Input type="date" {...form.register("date")} /></Field>
          <Field label="Time (24h)" error={e.time?.message}><Input placeholder="HH:MM" {...form.register("time")} /></Field>
          <Field label="Amount" error={e.amount?.message} hint={hint("amount")}><Input inputMode="decimal" {...form.register("amount")} /></Field>
          <Field label="Currency" hint={hint("currency")}><Select {...form.register("currency")}><option value="">Not stated</option>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Transaction type" hint={hint("type")}><Select {...form.register("type")}><option value="">Not stated</option><option value="expense">Expense</option><option value="income">Income</option><option value="transfer">Transfer</option><option value="refund">Refund</option></Select></Field>
          <Field label="Payment status"><Select {...form.register("status")}>{["successful", "pending", "failed", "refunded", "unknown"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
          <Field label="Merchant / recipient" error={e.merchant?.message} hint={hint("merchant")}><Input {...form.register("merchant")} /></Field>
          <Field label="Sender" error={e.sender?.message} hint={hint("sender")}><Input {...form.register("sender")} /></Field>
          <Field label="Payment method"><Input list="pm" {...form.register("payment_method")} /></Field>
          <Field label="Bank / provider"><Input {...form.register("provider")} /></Field>
          <Field label="Transaction reference (UTR / ID)" hint={hint("reference")}><Input {...form.register("reference")} /></Field>
          <Field label="Category" hint="Automatic uses your category rules"><Select {...form.register("category")}><option value="">Automatic</option>{cs.map((c) => <option key={c.name}>{c.name}</option>)}</Select></Field>
        </div>
        <datalist id="pm"><option>UPI</option><option>Card</option><option>Bank transfer</option><option>Cash</option><option>Wallet</option></datalist>
        <Field label="Description" error={e.description?.message}><Input {...form.register("description")} /></Field>
        {item.duplicate && <Notice tone="warn">Possible duplicate: {item.duplicate.detail}</Notice>}
        <details onToggle={(ev) => setShowText((ev.target as HTMLDetailsElement).open)}>
          <summary className="cursor-pointer text-sm font-medium">Show the text read from this slip</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-100 p-2 text-xs">{text.isLoading ? "Loading…" : text.data?.text ?? ""}</pre>
        </details>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" {...form.register("verified")} /> I have checked these details against the slip</label>
        {save.error ? <ErrorBox error={save.error} /> : null}
        <div className="modal-footer flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</Button></div>
      </form>
    </Modal>
  );
}
