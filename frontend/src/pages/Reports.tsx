import { useState } from "react";
import { IconDownload, IconFile } from "../components/icons";
import { Badge, Button, Card, ErrorBox, Field, Input, Notice, PageHeader, ProgressBar, Spinner, Stat, TableWrap, useToast } from "../components/ui";
import { ApiError, download } from "../lib/api";
import { useCurrency, useGet } from "../lib/hooks";
import { formatMoney, formatPct } from "../lib/money";
import type { BudgetPerf, Goal, Health, Totals } from "../lib/types";

type Report = { period: string; totals: Totals; categories: { category: string; amount: number; pct: number | null }[]; budget: BudgetPerf; goals: Goal[]; insights: string[]; health_score: Health; disclaimer: string };

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const PRESETS: { label: string; range: () => [string, string] }[] = [
  { label: "This month", range: () => { const n = new Date(); return [iso(new Date(n.getFullYear(), n.getMonth(), 1)), iso(new Date(n.getFullYear(), n.getMonth() + 1, 0))]; } },
  { label: "Last month", range: () => { const n = new Date(); return [iso(new Date(n.getFullYear(), n.getMonth() - 1, 1)), iso(new Date(n.getFullYear(), n.getMonth(), 0))]; } },
  { label: "Last 3 months", range: () => { const n = new Date(); return [iso(new Date(n.getFullYear(), n.getMonth() - 2, 1)), iso(new Date(n.getFullYear(), n.getMonth() + 1, 0))]; } },
  { label: "This year", range: () => { const n = new Date(); return [iso(new Date(n.getFullYear(), 0, 1)), iso(new Date(n.getFullYear(), 11, 31))]; } },
  { label: "All time", range: () => ["", ""] },
];

export default function Reports() {
  const cur = useCurrency();
  const toast = useToast();
  const [period, setPeriod] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState("");
  const [dlError, setDlError] = useState<unknown>(null);
  const q = useGet<Report>("/reports/monthly", { period: period || undefined });
  const r = q.data;
  const m = (v: number) => formatMoney(v, cur);

  async function get(kind: string, path: string, name: string, params?: Record<string, string | undefined>) {
    setBusy(kind); setDlError(null);
    try { await download(path, name, params); toast.success(`${name} downloaded.`); }
    catch (e) { setDlError(e instanceof ApiError || e instanceof Error ? e : new Error("Download failed")); }
    finally { setBusy(""); }
  }
  const range = { date_from: from || undefined, date_to: to || undefined };

  return (
    <div className="space-y-5">
      <PageHeader title="Reports" description="A monthly financial summary, plus exports of your transactions for any date range.">
        <Input type="month" aria-label="Report month" className="w-40" value={period || r?.period || ""} onChange={(e) => setPeriod(e.target.value)} />
        <Button disabled={!r || busy === "pdf"} onClick={() => get("pdf", "/reports/monthly.pdf", `report-${r!.period}.pdf`, { period: r!.period })}><IconFile className="h-4 w-4" />{busy === "pdf" ? "Preparing…" : "Download PDF"}</Button>
      </PageHeader>
      {dlError ? <ErrorBox error={dlError} /> : null}

      {q.isLoading ? <Spinner label="Loading report…" /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : r && (
        <Card title={`Monthly summary — ${r.period}`} description="Calculated from your transactions with the same rules as the dashboard">
          <div className="space-y-5 text-sm">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Income" value={m(r.totals.income)} tone="good" />
              <Stat label="Expenses (net)" value={m(r.totals.net_expense)} tone="bad" />
              <Stat label="Net savings" value={m(r.totals.net_cash_flow)} tone={r.totals.net_cash_flow >= 0 ? "good" : "bad"} />
              <Stat label="Savings rate" value={formatPct(r.totals.savings_pct)} tone={r.totals.savings_pct === null ? undefined : r.totals.savings_pct >= 0 ? "good" : "bad"} />
            </div>
            {r.insights.length > 0 && (
              <div><h3 className="mb-1.5 font-semibold">Highlights</h3><ul className="list-disc space-y-1 pl-5">{r.insights.map((i) => <li key={i}>{i}</li>)}</ul></div>
            )}
            {r.categories.length > 0 && (
              <div>
                <h3 className="mb-1.5 font-semibold">Spending by category</h3>
                <TableWrap>
                  <table className="w-full text-left"><thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-2.5">Category</th><th className="p-2.5 text-right">Amount</th><th className="p-2.5 text-right">Share</th><th className="hidden w-40 p-2.5 sm:table-cell"><span className="sr-only">Share bar</span></th></tr></thead>
                    <tbody>{r.categories.map((c) => (
                      <tr key={c.category} className="border-t border-slate-100"><td className="p-2.5">{c.category}</td><td className="num p-2.5 text-right">{m(c.amount)}</td><td className="num p-2.5 text-right">{formatPct(c.pct)}</td>
                        <td className="hidden p-2.5 sm:table-cell"><ProgressBar pct={c.pct} label={`${c.category} share of spending`} /></td></tr>))}</tbody></table>
                </TableWrap>
              </div>
            )}
            {r.budget.exists && (
              <div><h3 className="mb-1.5 font-semibold">Budget performance</h3>
                <p>{m(r.budget.total_actual)} of {m(r.budget.total_budget)} ({formatPct(r.budget.utilization_pct)}). {r.budget.overspent_categories.length ? <span className="font-medium text-red-700">Over budget: {r.budget.overspent_categories.join(", ")}.</span> : "All categories within budget."}</p></div>
            )}
            {r.goals.length > 0 && (
              <div><h3 className="mb-1.5 font-semibold">Savings goals</h3>
                <ul className="space-y-2">{r.goals.map((g) => <li key={g.id}><div className="flex items-center justify-between gap-2"><span>{g.name}: {m(g.current_amount)} of {m(g.target_amount)}</span><Badge tone={g.achieved ? "good" : "neutral"}>{formatPct(g.progress_pct)}</Badge></div></li>)}</ul></div>
            )}
            <Notice tone="info">{r.disclaimer}</Notice>
          </div>
        </Card>
      )}

      <Card title="Export transactions" description="Choose a date range (leave empty for everything), then pick a format. Files are generated on demand and not stored.">
        <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Date range presets">
          {PRESETS.map((p) => <Button key={p.label} variant="secondary" size="sm" onClick={() => { const [a, b] = p.range(); setFrom(a); setTo(b); }}>{p.label}</Button>)}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="From (optional)"><Input type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="To (optional)"><Input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} /></Field>
          <div className="flex items-end gap-2 sm:col-span-2">
            <Button variant="secondary" disabled={busy === "csv"} onClick={() => get("csv", "/reports/transactions.csv", "transactions.csv", range)}><IconDownload className="h-4 w-4" />CSV</Button>
            <Button variant="secondary" disabled={busy === "xlsx"} onClick={() => get("xlsx", "/reports/transactions.xlsx", "transactions.xlsx", range)}><IconDownload className="h-4 w-4" />Excel</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
