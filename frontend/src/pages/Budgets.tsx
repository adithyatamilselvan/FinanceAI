import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { Bar, BarChart, Cell, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { z } from "zod";
import { IconWallet } from "../components/icons";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, ProgressBar, Spinner, Stat, TableWrap, CHART_COLORS, NEGATIVE, POSITIVE } from "../components/ui";
import { api } from "../lib/api";
import { currentPeriod, useAction, useCategories, useCurrency, useGet } from "../lib/hooks";
import { formatCompact, formatMoney, formatPct, toInputString, toMinor } from "../lib/money";
import type { BudgetPerf } from "../lib/types";

type Budget = { id: string; period: string; allocations: { category: string; amount: number }[]; total: number };
const schema = z.object({
  period: z.string().regex(/^\d{4}-\d{2}$/, "Pick a month"),
  rows: z.array(z.object({ category: z.string(), amount: z.string().refine((v) => v.trim() === "" || toMinor(v) !== null, "Invalid amount") })),
}).refine((v) => v.rows.some((r) => r.amount.trim() !== "" && toMinor(r.amount)! > 0), { message: "Enter at least one category budget", path: ["rows"] });
type Form = z.infer<typeof schema>;

export default function Budgets() {
  const cur = useCurrency();
  const [period, setPeriod] = useState(currentPeriod());
  const [editing, setEditing] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const perf = useGet<BudgetPerf>(`/budgets/${period}/performance`);
  const list = useGet<{ items: Budget[] }>("/budgets");
  const existing = list.data?.items.find((b) => b.period === period);
  const del = useAction(() => api.del(`/budgets/${period}`), () => setConfirmDel(false), "Budget deleted.");
  const prev = list.data?.items.find((b) => b.period < period);
  const copy = useAction(() => api.post<Budget>("/budgets/copy", {}, { from: prev!.period, to: period }), undefined, "Budget copied.");

  return (
    <div>
      <PageHeader title="Budgets" description="Plan spending by category and compare it with what you actually spend.">
        <Input type="month" aria-label="Budget month" className="w-40" value={period} onChange={(e) => e.target.value && setPeriod(e.target.value)} />
        {existing && <Button variant="danger" disabled={del.isPending} onClick={() => setConfirmDel(true)}>Delete</Button>}
        <Button onClick={() => setEditing(true)}>{existing ? "Edit budget" : "Create budget"}</Button>
      </PageHeader>
      {perf.isLoading ? <Spinner /> : perf.error ? <ErrorBox error={perf.error} retry={() => perf.refetch()} /> : !perf.data!.exists ? (
        <Empty title={`No budget for ${period}`} icon={<IconWallet className="h-5 w-5" />}>
          Create one to compare your real spending against it.{" "}
          {prev && <Button variant="ghost" disabled={copy.isPending} onClick={() => copy.mutate(undefined)}>Copy from {prev.period}</Button>}
          {copy.error ? <ErrorBox error={copy.error} /> : null}
        </Empty>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Budgeted" value={formatMoney(perf.data!.total_budget, cur)} sub="Total monthly budget" />
            <Stat label="Spent" value={formatMoney(perf.data!.total_actual, cur)} sub="Actual spending this month" />
            <Stat label="Remaining" value={formatMoney(perf.data!.remaining, cur)} tone={perf.data!.remaining < 0 ? "bad" : "good"} sub={perf.data!.remaining < 0 ? "Over the total budget" : "Left to spend"} />
            <Stat label="Used" value={formatPct(perf.data!.utilization_pct)} tone={(perf.data!.utilization_pct ?? 0) > 100 ? "bad" : undefined} sub="Spent as a share of budget" />
          </div>
          <ProgressBar pct={perf.data!.utilization_pct} tone={perf.data!.total_actual > perf.data!.total_budget ? "bad" : (perf.data!.utilization_pct ?? 0) >= 85 ? "warn" : "info"} label="Total budget used" />
          {perf.data!.overspent_categories.length > 0 && <Notice tone="warn">Over budget in: {perf.data!.overspent_categories.join(", ")}.</Notice>}
          <Card title="Budget vs actual" description="Red bars mark categories that went over budget">
            <div className="h-64">
              <ResponsiveContainer>
                <BarChart data={perf.data!.rows.filter((r) => r.budget > 0 || r.actual > 0).map((r) => ({ category: r.category, Budget: r.budget, Actual: r.actual }))}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="category" fontSize={11} interval={0} angle={-25} textAnchor="end" height={60} tickLine={false} /><YAxis fontSize={12} width={56} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} />
                  <Tooltip formatter={(v) => formatMoney(Number(v), cur)} /><Legend />
                  <Bar isAnimationActive={false} dataKey="Budget" fill={CHART_COLORS[7]} radius={[4, 4, 0, 0]} />
                  <Bar isAnimationActive={false} dataKey="Actual" radius={[4, 4, 0, 0]} fill={CHART_COLORS[0]}>
                    {perf.data!.rows.filter((r) => r.budget > 0 || r.actual > 0).map((r) => <Cell key={r.category} fill={r.over_budget ? NEGATIVE : POSITIVE} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <TableWrap>
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-2.5">Category</th><th className="p-2.5 text-right">Budget</th><th className="p-2.5 text-right">Actual</th><th className="p-2.5 text-right">Remaining</th><th className="w-44 p-2.5">Utilisation</th></tr></thead>
              <tbody>
                {perf.data!.rows.map((r) => (
                  <tr key={r.category} className="border-t border-slate-100">
                    <td className="p-2.5">{r.category}{r.unbudgeted && <span className="ml-2 text-xs text-slate-500">(not budgeted)</span>}</td>
                    <td className="num p-2.5 text-right">{formatMoney(r.budget, cur)}</td><td className="num p-2.5 text-right">{formatMoney(r.actual, cur)}</td>
                    <td className={`num p-2.5 text-right ${r.over_budget ? "font-medium text-red-700" : ""}`}>{formatMoney(r.remaining, cur)}</td>
                    <td className="p-2.5">{r.budget > 0 ? <div className="space-y-1"><ProgressBar pct={r.utilization_pct} tone={r.over_budget ? "bad" : (r.utilization_pct ?? 0) >= 85 ? "warn" : "good"} label={`${r.category} budget used`} /><div className="flex items-center justify-between text-xs"><span className="num">{formatPct(r.utilization_pct)}</span>{r.over_budget ? <Badge tone="bad">Over</Badge> : (r.utilization_pct ?? 0) >= 85 ? <Badge tone="warn">Near limit</Badge> : null}</div></div> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </div>
      )}

      <Card title="Budget history" className="mt-6" description="Select a month to review it">
        {list.data?.items.length ? (
          <ul className="divide-y divide-slate-100 text-sm">
            {list.data.items.map((b) => (
              <li key={b.id} className="flex items-center justify-between py-1.5">
                <button className={`font-medium underline ${b.period === period ? "text-slate-900" : "text-blue-700"}`} aria-current={b.period === period ? "true" : undefined} onClick={() => setPeriod(b.period)}>{b.period}</button>
                <span>{formatMoney(b.total, cur)} across {b.allocations.length} categories</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-slate-600">No budgets yet.</p>}
      </Card>
      {editing && <BudgetForm period={period} existing={existing} onClose={() => setEditing(false)} />}
      {confirmDel && (
        <ConfirmDialog title="Delete budget?" danger confirmLabel="Delete budget" busyLabel="Deleting…" busy={del.isPending} error={del.error} onConfirm={() => del.mutate(undefined)} onCancel={() => setConfirmDel(false)}>
          Delete the {period} budget? Your transactions are not affected.
        </ConfirmDialog>
      )}
    </div>
  );
}

function BudgetForm({ period, existing, onClose }: { period: string; existing?: Budget; onClose: () => void }) {
  const cats = useCategories();
  const expense = (cats.data?.categories ?? []).filter((c) => c.type === "expense");
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    values: { period, rows: expense.map((c) => ({ category: c.name, amount: toInputString(existing?.allocations.find((a) => a.category === c.name)?.amount ?? 0).replace(/^0\.00$/, "") })) },
  });
  const { fields } = useFieldArray({ control: form.control, name: "rows" });
  const save = useAction(async (v: Form) => {
    const allocations = v.rows.filter((r) => r.amount.trim() !== "" && toMinor(r.amount)! > 0).map((r) => ({ category: r.category, amount: toMinor(r.amount)! }));
    return existing ? api.put<Budget>(`/budgets/${period}`, { allocations }) : api.post<Budget>("/budgets", { period, allocations });
  }, onClose, existing ? "Budget updated." : "Budget created.");
  return (
    <Modal title={`${existing ? "Edit" : "Create"} budget — ${period}`} onClose={onClose}>
      <form onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate className="space-y-2">
        <p className="text-xs text-slate-500">Monthly amounts per category. Leave blank for no budget.</p>
        <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
          {fields.map((f, i) => (
            <Field key={f.id} label={f.category} error={form.formState.errors.rows?.[i]?.amount?.message}>
              <Input inputMode="decimal" placeholder="0.00" {...form.register(`rows.${i}.amount`)} />
            </Field>
          ))}
        </div>
        {(form.formState.errors.rows?.root?.message ?? form.formState.errors.rows?.message) && (
          <p role="alert" className="text-sm font-medium text-red-700">{form.formState.errors.rows?.root?.message ?? form.formState.errors.rows?.message}</p>
        )}
        {save.error ? <ErrorBox error={save.error} /> : null}
        <div className="modal-footer flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save budget"}</Button></div>
      </form>
    </Modal>
  );
}
