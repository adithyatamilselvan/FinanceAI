import { useState } from "react";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Button, Card, CHART_COLORS, Empty, ErrorBox, Field, Input, Modal, NEGATIVE, Notice, PageHeader, POSITIVE, Select, Spinner, Stat, TableWrap } from "../components/ui";
import { api } from "../lib/api";
import { useAction, useCurrency, useGet } from "../lib/hooks";
import { formatCompact, formatMoney, formatPct, toInputString, toMinor } from "../lib/money";
import type { Dashboard as Dash, Recurring } from "../lib/types";

type Trends = { periods: string[]; categories: { category: string; values: number[]; total: number; avg_per_month: number; trend_per_month: number; direction: string }[] };
type Anomalies = {
  large_transactions: { transaction_id: string; date: string; description: string; category: string; amount: number; reason: string }[];
  category_spikes: { category: string; reason: string; increase_pct: number | null }[];
  possible_duplicates: { transaction_ids: string[]; dates: string[]; description: string; amount: number; reason: string }[];
  disclaimer: string; reference_date: string | null;
};
type Point = { mid: number; low: number; high: number };
type Forecast =
  | { sufficient: false; reason: string; history_months: number; required_months: number }
  | { sufficient: true; history_months: number; history_from: string; history_to: string; confidence: string; stale_months: number; avg_projected_monthly_savings: number;
      months: { period: string; income: Point; expense: Point; net_savings: number; cumulative_savings: number; cumulative_low: number; cumulative_high: number; recurring_expected?: number }[];
      goal_estimates: { goal_id: string; name: string; status: string; months_needed: number | null }[]; method: string[] };

export default function Analytics() {
  return (
    <div className="space-y-6">
      <PageHeader title="Analytics" description="Trends, comparisons, unusual activity and forecasts, all calculated from your own transactions." />
      <Overview /><Trends /><Anomalies /><RecurringSection /><ForecastSection />
    </div>
  );
}

const signedMoney = (v: number, cur: string) => `${v >= 0 ? "+" : "-"}${formatMoney(Math.abs(v), cur)}`;

/** Income vs expenses and the month-over-month comparison, from the same /analytics/dashboard figures the Dashboard uses. */
function Overview() {
  const q = useGet<Dash>("/analytics/dashboard");
  const d = q.data;
  const cur = d?.currency ?? "INR";
  const mom = d?.vs_previous_month;
  return (
    <Card title="Income vs expenses and month-over-month" description={d ? `Latest month with data: ${d.period}. Past values only; nothing here is estimated.` : undefined}>
      {q.isLoading ? <Spinner compact label="Loading overview…" /> : q.error ? <ErrorBox error={q.error} /> : !d || !d.has_transactions ? <Empty title="No data yet">Import or add transactions to see comparisons.</Empty> : (
        <div className="space-y-4">
          {mom ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Stat label="Income vs previous month" value={signedMoney(mom.income_change, cur)} tone={mom.income_change >= 0 ? "good" : "bad"} sub={mom.income_change_pct === null ? undefined : `${formatPct(mom.income_change_pct)} change`} />
              <Stat label="Expenses vs previous month" value={signedMoney(mom.expense_change, cur)} tone={mom.expense_change <= 0 ? "good" : "bad"} sub={mom.expense_change_pct === null ? undefined : `${formatPct(mom.expense_change_pct)} change`} />
              <Stat label="Savings vs previous month" value={signedMoney(mom.savings_change, cur)} tone={mom.savings_change >= 0 ? "good" : "bad"} sub="Net savings difference" />
            </div>
          ) : <Notice tone="info">There is no previous month to compare with yet.</Notice>}
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={d.trend.map((r) => ({ period: r.period, Income: r.income, Expenses: r.net_expense }))}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="period" fontSize={12} tickLine={false} />
                <YAxis fontSize={12} width={56} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} />
                <Tooltip formatter={(v) => formatMoney(Number(v), cur)} /><Legend />
                <Bar isAnimationActive={false} dataKey="Income" fill={POSITIVE} radius={[4, 4, 0, 0]} /><Bar isAnimationActive={false} dataKey="Expenses" fill={NEGATIVE} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-slate-500">Expenses are net of refunds; transfers between your own accounts are excluded.</p>
        </div>
      )}
    </Card>
  );
}

function Trends() {
  const cur = useCurrency();
  const [months, setMonths] = useState("6");
  const q = useGet<Trends>("/analytics/category-trends", { months });
  const top = q.data?.categories.slice(0, 5) ?? [];
  const data = q.data?.periods.map((p, i) => Object.fromEntries([["period", p], ...top.map((c) => [c.category, c.values[i]])])) ?? [];
  return (
    <Card title="Category spending trends" description="Your five largest categories, net of refunds" actions={<Select aria-label="Months" className="w-32" value={months} onChange={(e) => setMonths(e.target.value)}>{["3", "6", "12"].map((m) => <option key={m} value={m}>{m} months</option>)}</Select>}>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : top.length === 0 ? <Empty title="No spending data yet" /> : (
        <>
          <div className="h-64"><ResponsiveContainer><LineChart data={data}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="period" fontSize={12} tickLine={false} /><YAxis fontSize={12} width={56} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} /><Tooltip formatter={(v) => formatMoney(Number(v), cur)} /><Legend />
            {top.map((c, i) => <Line isAnimationActive={false} key={c.category} type="monotone" dataKey={c.category} stroke={CHART_COLORS[i]} dot={false} strokeWidth={2} />)}</LineChart></ResponsiveContainer></div>
          <TableWrap className="mt-3"><table className="w-full text-left text-sm"><thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-1.5">Category</th><th className="p-1.5 text-right">Avg / month</th><th className="p-1.5 text-right">Trend / month</th><th className="p-1.5">Direction</th></tr></thead>
            <tbody>{q.data!.categories.map((c) => <tr key={c.category} className="border-t border-slate-100"><td className="p-1.5">{c.category}</td><td className="p-1.5 text-right">{formatMoney(c.avg_per_month, cur)}</td><td className="p-1.5 text-right">{formatMoney(c.trend_per_month, cur)}</td><td className="p-1.5 capitalize">{c.direction}</td></tr>)}</tbody></table></TableWrap>
        </>
      )}
    </Card>
  );
}

function Anomalies() {
  const cur = useCurrency();
  const q = useGet<Anomalies>("/analytics/anomalies");
  const a = q.data;
  const none = a && !a.large_transactions.length && !a.category_spikes.length && !a.possible_duplicates.length;
  return (
    <Card title="Items to review">
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : !a || none ? <Empty title="Nothing unusual found">Flags appear when a transaction or category stands out from your own history.</Empty> : (
        <div className="space-y-4 text-sm">
          <Notice tone="info">{a.disclaimer}</Notice>
          {a.large_transactions.length > 0 && <div><h3 className="mb-1 font-medium">Unusually large transactions</h3><ul className="space-y-1">{a.large_transactions.map((t) => <li key={t.transaction_id}><b>{t.date}</b> · {t.description} · {formatMoney(t.amount, cur)}<div className="text-xs text-slate-500">{t.reason}</div></li>)}</ul></div>}
          {a.category_spikes.length > 0 && <div><h3 className="mb-1 font-medium">Sudden category increases</h3><ul className="space-y-1">{a.category_spikes.map((s) => <li key={s.category}>{s.reason}</li>)}</ul></div>}
          {a.possible_duplicates.length > 0 && <div><h3 className="mb-1 font-medium">Possible duplicate payments</h3><ul className="space-y-1">{a.possible_duplicates.map((d) => <li key={d.transaction_ids.join()}><b>{d.dates.join(" & ")}</b> · {d.description}<div className="text-xs text-slate-500">{d.reason}</div></li>)}</ul></div>}
        </div>
      )}
    </Card>
  );
}

function RecurringSection() {
  const cur = useCurrency();
  const q = useGet<{ items: Recurring[]; estimated_monthly_total: number; estimated_annual_total: number; note: string }>("/recurring");
  const detect = useAction(() => api.post<{ detected: number }>("/recurring/detect"));
  const patch = useAction((v: { id: string; body: Record<string, unknown> }) => api.patch<Recurring>(`/recurring/${v.id}`, v.body));
  const [edit, setEdit] = useState<Recurring | null>(null);
  return (
    <Card title="Recurring payments" actions={<Button disabled={detect.isPending} onClick={() => detect.mutate(undefined)}>{detect.isPending ? "Scanning…" : "Detect from my transactions"}</Button>}>
      {detect.error ? <ErrorBox error={detect.error} /> : null}
      {detect.isSuccess && <p className="mb-2 text-sm text-slate-600">Scan complete: {detect.data.detected} recurring payment(s) found.</p>}
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : q.data!.items.length === 0 ? <Empty title="No recurring payments detected">Click “Detect” after importing at least 3 months of history.</Empty> : (
        <>
          <p className="mb-2 text-sm">Estimated recurring cost: <b>{formatMoney(q.data!.estimated_monthly_total, cur)}</b> / month · <b>{formatMoney(q.data!.estimated_annual_total, cur)}</b> / year</p>
          <TableWrap><table className="w-full text-left text-sm"><thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-1.5">Payee</th><th className="p-1.5">Frequency</th><th className="p-1.5 text-right">Amount</th><th className="p-1.5">Confidence &amp; evidence</th><th className="p-1.5">Status</th><th /></tr></thead>
            <tbody>{q.data!.items.map((r) => (
              <tr key={r.id} className="border-t border-slate-100 align-top">
                <td className="p-1.5">{r.name}<div className="text-xs text-slate-500">{r.category} · next ~{r.next_expected}</div></td>
                <td className="p-1.5 capitalize">{r.frequency}</td><td className="p-1.5 text-right">{formatMoney(r.amount, cur)}<div className="text-xs text-slate-500">{formatMoney(r.estimated_monthly, cur)}/mo</div></td>
                <td className="p-1.5"><b>{Math.round(r.confidence * 100)}%</b><div className="text-xs text-slate-500">{r.evidence?.explanation}</div></td>
                <td className="p-1.5 capitalize">{r.status}</td>
                <td className="whitespace-nowrap p-1.5 text-right">
                  {r.status !== "confirmed" && <Button variant="ghost" onClick={() => patch.mutate({ id: r.id, body: { status: "confirmed" } })}>Confirm</Button>}
                  <Button variant="ghost" onClick={() => setEdit(r)}>Edit</Button>
                  <Button variant="ghost" onClick={() => patch.mutate({ id: r.id, body: { status: "dismissed" } })}>Dismiss</Button>
                </td>
              </tr>))}</tbody></table></TableWrap>
          <p className="mt-2 text-xs text-slate-500">{q.data!.note}</p>
        </>
      )}
      {patch.error ? <ErrorBox error={patch.error} /> : null}
      {edit && <EditRecurring r={edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

function EditRecurring({ r, onClose }: { r: Recurring; onClose: () => void }) {
  const [name, setName] = useState(r.name);
  const [amount, setAmount] = useState(toInputString(r.amount));
  const [freq, setFreq] = useState(r.frequency);
  const m = toMinor(amount);
  const save = useAction(() => api.patch<Recurring>(`/recurring/${r.id}`, { name, amount: m, frequency: freq }), onClose);
  return (
    <Modal title="Edit recurring payment" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Amount" error={m === null || m <= 0 ? "Enter a valid amount" : undefined}><Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label="Frequency"><Select value={freq} onChange={(e) => setFreq(e.target.value)}>{["weekly", "monthly", "quarterly", "annual"].map((f) => <option key={f}>{f}</option>)}</Select></Field>
        {save.error ? <ErrorBox error={save.error} /> : null}
        <div className="modal-footer flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={!m || !name.trim() || save.isPending} onClick={() => save.mutate(undefined)}>Save</Button></div>
      </div>
    </Modal>
  );
}

/** Actual history (solid) and estimated future (dashed, shaded) in one chart, so the two can never be confused. */
function ForecastChart({ f, actual, cur }: { f: Extract<Forecast, { sufficient: true }>; actual: { period: string; income: number; net_expense: number }[]; cur: string }) {
  const firstEst = f.months[0]?.period;
  const hist = actual.filter((a) => !firstEst || a.period < firstEst);
  const lastActual = hist[hist.length - 1];
  type Row = { period: string; "Income (actual)"?: number; "Expenses (actual)"?: number; "Income (estimate)"?: number; "Expenses (estimate)"?: number; range?: [number, number] };
  const rows: Row[] = hist.map((a) => ({ period: a.period, "Income (actual)": a.income, "Expenses (actual)": a.net_expense }));
  if (lastActual) { // join the dashed line to the last actual point
    const j = rows[rows.length - 1];
    j["Income (estimate)"] = lastActual.income;
    j["Expenses (estimate)"] = lastActual.net_expense;
  }
  for (const m of f.months) rows.push({ period: m.period, "Income (estimate)": m.income.mid, "Expenses (estimate)": m.expense.mid, range: [m.expense.low, m.expense.high] });
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs"><Badge tone="neutral">Actual</Badge><Badge tone="info">Estimate</Badge></div>
      <div className="h-72"><ResponsiveContainer>
        <ComposedChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="period" fontSize={12} tickLine={false} />
          <YAxis fontSize={12} width={56} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} />
          <Tooltip formatter={(v) => (Array.isArray(v) ? `${formatMoney(Number(v[0]), cur)} – ${formatMoney(Number(v[1]), cur)}` : formatMoney(Number(v), cur))} /><Legend />
          {firstEst && <ReferenceArea x1={firstEst} x2={f.months[f.months.length - 1].period} fill={CHART_COLORS[0]} fillOpacity={0.07} stroke="none" ifOverflow="extendDomain" />}
          <Area isAnimationActive={false} dataKey="range" name="Expense typical range (estimate)" fill={NEGATIVE} stroke="none" fillOpacity={0.15} />
          <Line isAnimationActive={false} type="monotone" dataKey="Income (actual)" stroke={POSITIVE} strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} />
          <Line isAnimationActive={false} type="monotone" dataKey="Expenses (actual)" stroke={NEGATIVE} strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} />
          <Line isAnimationActive={false} type="monotone" dataKey="Income (estimate)" stroke={POSITIVE} strokeWidth={2} strokeDasharray="6 4" dot={false} />
          <Line isAnimationActive={false} type="monotone" dataKey="Expenses (estimate)" stroke={NEGATIVE} strokeWidth={2} strokeDasharray="6 4" dot={false} />
        </ComposedChart></ResponsiveContainer></div>
    </div>
  );
}

function ForecastSection() {
  const cur = useCurrency();
  const [horizon, setHorizon] = useState("3");
  const q = useGet<Forecast>("/forecasts", { horizon });
  const hist = useGet<Dash>("/analytics/dashboard"); // actual history for the same chart
  const f = q.data;
  return (
    <Card title="Forecast" description="Solid lines are actual past months. Dashed lines inside the shaded area are estimates." actions={<Select aria-label="Horizon" className="w-36" value={horizon} onChange={(e) => setHorizon(e.target.value)}>{["3", "6", "12"].map((m) => <option key={m} value={m}>Next {m} months</option>)}</Select>}>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : !f ? null : !f.sufficient ? (
        <Notice tone="warn">{f.reason}</Notice>
      ) : (
        <div className="space-y-3 text-sm">
          <p>Based on <b>{f.history_months}</b> completed months ({f.history_from} to {f.history_to}). Confidence: <b>{f.confidence}</b>. Average projected savings: <b>{formatMoney(f.avg_projected_monthly_savings, cur)}</b> / month.</p>
          {f.stale_months > 0 && <Notice tone="warn">Your latest data ends {f.stale_months} month(s) before last month, so these projections start after that data, not from today.</Notice>}
          <ForecastChart f={f} actual={hist.data?.trend ?? []} cur={cur} />
          <TableWrap><table className="w-full text-left"><thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-1">Month</th><th className="p-1 text-right">Income</th><th className="p-1 text-right">Spending</th><th className="p-1 text-right">Savings</th><th className="p-1 text-right">Cumulative savings (range)</th></tr></thead>
            <tbody>{f.months.map((m) => <tr key={m.period} className="border-t border-slate-100"><td className="p-1">{m.period}</td><td className="p-1 text-right">{formatMoney(m.income.mid, cur)}</td><td className="p-1 text-right">{formatMoney(m.expense.mid, cur)}</td><td className="p-1 text-right">{formatMoney(m.net_savings, cur)}</td><td className="p-1 text-right">{formatMoney(m.cumulative_savings, cur)} <span className="text-xs text-slate-500">({formatMoney(m.cumulative_low, cur)} – {formatMoney(m.cumulative_high, cur)})</span></td></tr>)}</tbody></table></TableWrap>
          {f.goal_estimates.length > 0 && <div><h3 className="font-medium">Goal estimates</h3><ul>{f.goal_estimates.map((g) => <li key={g.goal_id}>{g.name}: {g.status === "projected" ? `about ${g.months_needed} months` : g.status === "achieved" ? "achieved" : "not reachable at the current trend"}</li>)}</ul></div>}
          <details><summary className="cursor-pointer font-medium">Method and limitations</summary><ul className="mt-1 list-disc pl-5 text-xs text-slate-600">{f.method.map((m) => <li key={m}>{m}</li>)}</ul></details>
          <p className="text-xs text-slate-500">Ranges show typical past variation; they are not guarantees.</p>
        </div>
      )}
    </Card>
  );
}
