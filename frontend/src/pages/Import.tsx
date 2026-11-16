import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { IconCheck, IconUpload } from "../components/icons";
import { Badge, Button, Card, ErrorBox, Field, Notice, PageHeader, Select, TableWrap, cx } from "../components/ui";
import { api, uploadWithProgress } from "../lib/api";
import { useAction, useCurrency } from "../lib/hooks";
import { formatMoney } from "../lib/money";
import type { ImportPreview } from "../lib/types";

const MAX_BYTES = 5 * 1024 * 1024;
const kb = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export default function ImportPage() {
  const cur = useCurrency();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [dayfirst, setDayfirst] = useState(true);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [result, setResult] = useState<{ inserted: number; skipped_duplicates: number; excluded: number; error_rows: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);

  // The preview upload reports real upload progress (same endpoint and form fields as before).
  const run = useAction(async (m?: Record<string, string>) => {
    setProgress(0);
    try {
      return await uploadWithProgress<ImportPreview>("/imports/preview", file!, setProgress, undefined, m ? { mapping: JSON.stringify({ ...m, dayfirst }) } : undefined);
    } finally { setProgress(null); }
  }, (p) => { setPreview(p); setExcluded(new Set()); setResult(null); if (p.needs_mapping) setMapping(p.suggested_mapping ?? {}); });

  const commit = useAction(() => api.post<{ inserted: number; skipped_duplicates: number; excluded: number; error_rows: number }>(
    `/imports/${preview!.import_id}/commit`, { exclude_rows: [...excluded] }), (r) => { setResult(r); setPreview(null); setFile(null); if (fileRef.current) fileRef.current.value = ""; });

  const discard = useAction(() => api.del(`/imports/${preview!.import_id}`), () => { setPreview(null); }, "Import discarded. Nothing was saved.");

  function pick(f: File | null) {
    setPreview(null); setResult(null); setFileError("");
    if (!f) { setFile(null); return; }
    if (!/\.(csv|xlsx)$/i.test(f.name)) { setFileError("Only .csv and .xlsx files are supported."); setFile(null); return; }
    if (f.size > MAX_BYTES) { setFileError("File is larger than 5 MB. Export a shorter date range."); setFile(null); return; }
    setFile(f);
  }
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDragOver(false); if (!run.isPending) pick(e.dataTransfer.files?.[0] ?? null); };

  const s = preview?.summary;
  const needs = preview?.needs_mapping;
  const mapOk = !!mapping.date && !!mapping.description && (!!mapping.amount || !!mapping.debit || !!mapping.credit);
  const header = preview?.headers ?? [];
  const step = result ? 3 : preview ? 2 : 1;

  return (
    <div>
      <PageHeader title="Import bank statement" description="Upload a statement, check what will be saved, then confirm." />
      <Notice tone="info">
        <b>Your privacy:</b> upload a CSV or XLSX statement exported from your bank (max 5 MB, 5,000 rows). The file is read in memory and discarded; only the transactions you confirm are saved. Never share your online-banking password with any app.
      </Notice>

      <ol className="my-5 flex items-center gap-2 text-sm" aria-label="Import steps">
        {["Choose file", "Review", "Done"].map((label, i) => {
          const n = i + 1, done = step > n, cur_ = step === n;
          return (
            <li key={label} className="flex items-center gap-2" aria-current={cur_ ? "step" : undefined}>
              <span className={cx("flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold", done ? "bg-green-50 text-green-700 ring-1 ring-green-200" : cur_ ? "bg-brand text-white" : "bg-slate-200 text-slate-600")}>{done ? <IconCheck className="h-3.5 w-3.5" /> : n}</span>
              <span className={cx(cur_ ? "font-medium text-slate-900" : "text-slate-500")}>{label}</span>
              {n < 3 && <span aria-hidden className="mx-1 h-px w-6 bg-slate-300 sm:w-10" />}
            </li>
          );
        })}
      </ol>

      <Card title="1. Choose a file">
        <label
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={onDrop}
          className={cx("flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed p-6 text-center transition-colors focus-within:ring-2 focus-within:ring-blue-500/40 sm:p-8",
            dragOver ? "border-blue-500 bg-blue-50" : "border-slate-300 bg-slate-50 hover:border-blue-400")}
        >
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-blue-50 text-blue-700"><IconUpload className="h-5 w-5" /></span>
          <span className="font-medium">Drag and drop your statement here, or <span className="text-blue-700 underline">browse</span></span>
          <span className="text-xs text-slate-500">CSV or XLSX · up to 5 MB · up to 5,000 rows</span>
          <input ref={fileRef} type="file" accept=".csv,.xlsx" aria-label="Statement file" onChange={(e) => pick(e.target.files?.[0] ?? null)} className="sr-only" />
        </label>
        {file && <p className="mt-3 text-sm"><b>{file.name}</b> <span className="text-slate-500">({kb(file.size)})</span></p>}
        {fileError && <p role="alert" className="mt-2 text-sm font-medium text-red-700">{fileError}</p>}
        {progress !== null && (
          <div className="mt-3" role="status" aria-live="polite">
            <div className="mb-1 flex justify-between text-xs text-slate-600"><span>{progress < 1 ? "Uploading…" : "Reading and checking rows…"}</span><span className="num">{Math.round(progress * 100)}%</span></div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-blue-600 transition-[width]" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
          </div>
        )}
        <div className="mt-3"><Button disabled={!file || run.isPending} onClick={() => run.mutate(undefined)}>{run.isPending ? "Reading file…" : "Preview import"}</Button></div>
        {run.error ? <div className="mt-3"><ErrorBox error={run.error} /></div> : null}
      </Card>

      {needs && (
        <Card title="2. Map the columns" className="mt-4">
          <p className="mb-3 text-sm text-slate-600">We could not recognise the columns automatically. Tell us which column holds what.</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[["date", "Date *"], ["description", "Description *"], ["amount", "Amount (one signed column)"], ["debit", "Debit / withdrawal"], ["credit", "Credit / deposit"], ["type", "Dr/Cr indicator (optional)"]].map(([k, label]) => (
              <Field key={k} label={label}>
                <Select value={mapping[k] ?? ""} onChange={(e) => setMapping((m) => ({ ...m, [k]: e.target.value }))}>
                  <option value="">— none —</option>
                  {header.map((h) => <option key={h}>{h}</option>)}
                </Select>
              </Field>
            ))}
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={dayfirst} onChange={(e) => setDayfirst(e.target.checked)} /> Dates are day-first (31/12/2025)</label>
          {preview?.sample_rows && (
            <TableWrap className="mt-3"><table className="w-full text-xs"><thead className="bg-slate-100"><tr>{header.map((h) => <th key={h} className="p-1.5 text-left">{h}</th>)}</tr></thead>
              <tbody>{preview.sample_rows.map((r, i) => <tr key={i} className="border-t border-slate-100">{r.map((c, j) => <td key={j} className="p-1.5">{c}</td>)}</tr>)}</tbody></table></TableWrap>
          )}
          <div className="mt-3"><Button disabled={!mapOk || run.isPending} onClick={() => run.mutate(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)))}>Apply mapping and preview</Button></div>
        </Card>
      )}

      {preview && s && !needs && (
        <Card title="2. Review before saving" className="mt-4" description="Nothing is saved until you press the import button below.">
          {preview.already_imported && <div className="mb-3"><Notice tone="warn">This exact file content was imported before; duplicates will be skipped.</Notice></div>}
          <ul className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
            <li className="rounded-lg bg-slate-50 p-3"><b className="num text-lg">{s.new}</b> new transactions</li>
            <li className="rounded-lg bg-slate-50 p-3"><b className="num text-lg">{s.duplicates}</b> duplicates (will be skipped)</li>
            <li className="rounded-lg bg-slate-50 p-3"><b className={cx("num text-lg", s.errors > 0 && "text-red-700")}>{s.errors}</b> rows with problems (not imported)</li>
            <li className="rounded-lg bg-slate-50 p-3">New income: <b className="num text-green-700">{formatMoney(s.income_total, cur)}</b></li>
            <li className="rounded-lg bg-slate-50 p-3">New spending: <b className="num">{formatMoney(s.expense_total, cur)}</b></li>
            <li className="rounded-lg bg-slate-50 p-3">Refunds / transfers: <b className="num">{formatMoney(s.refund_total, cur)}</b> / {s.transfers}</li>
          </ul>
          {s.duplicates > 0 && <div className="mt-3"><Notice tone="warn"><b>{s.duplicates}</b> row(s) match transactions you already have. They are greyed out and will not be imported again.</Notice></div>}
          {preview.errors && preview.errors.length > 0 && (
            <details className="mt-3 text-sm" open>
              <summary className="cursor-pointer font-medium text-red-700">Rows that cannot be imported ({s.errors})</summary>
              <ul className="mt-1 max-h-40 overflow-y-auto text-xs">{preview.errors.map((e) => <li key={e.row}>Line {e.row}: {e.reason}</li>)}</ul>
            </details>
          )}
          <TableWrap className="mt-3 max-h-96 overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-100"><tr><th className="p-2">Import</th><th className="p-2">Date</th><th className="p-2">Description</th><th className="hidden p-2 sm:table-cell">Category</th><th className="hidden p-2 sm:table-cell">Type</th><th className="p-2 text-right">Amount</th><th className="p-2">Status</th></tr></thead>
              <tbody>
                {preview.rows!.map((r) => (
                  <tr key={r.row} className={`border-t border-slate-100 ${r.status === "duplicate" || excluded.has(r.row) ? "text-slate-400" : ""}`}>
                    <td className="p-2"><input type="checkbox" aria-label={`Include row ${r.row}`} disabled={r.status === "duplicate"} checked={r.status === "new" && !excluded.has(r.row)}
                      onChange={(e) => setExcluded((p) => { const n = new Set(p); if (e.target.checked) n.delete(r.row); else n.add(r.row); return n; })} /></td>
                    <td className="whitespace-nowrap p-2">{r.date}</td><td className="p-2">{r.description}</td><td className="hidden p-2 sm:table-cell">{r.category}</td><td className="hidden p-2 capitalize sm:table-cell">{r.type}</td>
                    <td className="num whitespace-nowrap p-2 text-right">{formatMoney(r.amount, cur)}</td>
                    <td className="p-2">{r.status === "duplicate" ? <Badge tone="warn">duplicate</Badge> : <Badge tone="good">new</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          {preview.rows_truncated && <p className="mt-1 text-xs text-slate-500">Showing the first 500 rows. All rows are imported unless excluded.</p>}
          {commit.error ? <div className="mt-3"><ErrorBox error={commit.error} /></div> : null}
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <Button disabled={commit.isPending || s.new - [...excluded].length <= 0} onClick={() => commit.mutate(undefined)}>{commit.isPending ? "Saving…" : `Import ${Math.max(0, s.new - excluded.size)} transactions`}</Button>
            <Button variant="secondary" disabled={discard.isPending} onClick={() => discard.mutate(undefined)}>Discard</Button>
          </div>
        </Card>
      )}

      {result && (
        <div className="mt-4"><Notice tone="good">
          Imported <b>{result.inserted}</b> transactions; skipped {result.skipped_duplicates} duplicates, {result.excluded} excluded, {result.error_rows} invalid rows.{" "}
          <Link to="/transactions" className="font-medium underline">View transactions</Link> · <Link to="/" className="font-medium underline">Go to dashboard</Link>
        </Notice></div>
      )}
    </div>
  );
}
