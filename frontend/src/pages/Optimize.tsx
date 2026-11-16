import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { z } from "zod";
import { Badge, Button, Card, CHART_COLORS, cx, Empty, ErrorBox, Field, Input, Notice, PageHeader, POSITIVE, Select, Stat, TableWrap } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { currentPeriod, useAction, useCategories, useCurrency } from "../lib/hooks";
import { formatCompact, formatMoney, formatPct, toInputString, toMinor } from "../lib/money";

export default function Optimize() {
  const [tab, setTab] = useState<"opt" | "sim">("opt");
  return (
    <div>
      <PageHeader title="Optimize & simulate" description="Explore ways to save and test what-if scenarios. Results are estimates; nothing changes in your budget unless you choose to apply it." />
      <div className="mb-5 inline-flex gap-1 rounded-lg bg-slate-100 p-1" role="tablist" aria-label="Optimize or simulate"
        onKeyDown={(e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { setTab((t) => (t === "opt" ? "sim" : "opt")); } }}>
        {([["opt", "Budget optimization"], ["sim", "What-if simulation"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} tabIndex={tab === k ? 0 : -1} onClick={() => setTab(k)}
            className={cx("rounded-md px-3.5 py-1.5 text-sm font-medium transition-colors", tab === k ? "bg-surface text-blue-700 shadow-sm" : "text-slate-600 hover:text-slate-900")}>{l}</button>
        ))}
      </div>
      {tab === "opt" ? <Optimization /> : <Simulation />}
    </div>
  );
}

type Scenario = { name: string; target_savings: number; achieved_savings: number; annual_savings: number; target_reached: boolean; solver: string;
  categories: { category: string; current: number; suggested: number; change: number; change_pct: number; minimum_allowed: number }[] };
type Rec = { id: string; status: string; history_months: number; current_variable_total: number; scenarios: Record<string, Scenario>; fixed_categories: { category: string; monthly: number }[];
  warnings: string[]; disclaimer: string; method: string; basis_periods: string[] };

function Optimization() {
  const cur = useCurrency();
  const cats = useCategories();
  const [months, setMonths] = useState("3");
  const [mins, setMins] = useState<Record<string, string>>({});
  const [rec, setRec] = useState<Rec | null>(null);
  const [sel, setSel] = useState<"conservative" | "balanced" | "aggressive">("balanced");
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [apply, setApply] = useState(false);
  const [period, setPeriod] = useState(currentPeriod());
  const [outcome, setOutcome] = useState("");

  const run = useAction(() => {
    const min_spend = Object.fromEntries(Object.entries(mins).filter(([, v]) => v.trim() && toMinor(v) !== null).map(([k, v]) => [k, toMinor(v)!]));
    return api.post<Rec>("/optimization/run", { months: Number(months), min_spend });
  }, (r) => { setRec(r); setOutcome(""); setEditing(false); });

  const accept = useAction(() => {
    const body: Record<string, unknown> = { scenario: sel };
    if (editing && rec) {
      const sc = rec.scenarios[sel];
      body.allocations = [
        ...sc.categories.map((c) => ({ category: c.category, amount: edits[c.category] !== undefined && toMinor(edits[c.category]) !== null ? toMinor(edits[c.category])! : c.suggested })),
        ...rec.fixed_categories.map((f) => ({ category: f.category, amount: f.monthly })),
      ];
    }
    if (apply) body.apply_to_budget_period = period;
    return api.post(`/optimization/recommendations/${rec!.id}/accept`, body);
  }, () => { setOutcome(apply ? `Accepted and saved as the ${period} budget.` : "Accepted. Your budget was not changed; you can use these numbers when creating a budget."); setRec(null); });

  const reject = useAction(() => api.post(`/optimization/recommendations/${rec!.id}/reject`), () => { setOutcome("Recommendation rejected. Nothing was changed."); setRec(null); });

  const expenseCats = (cats.data?.categories ?? []).filter((c) => c.type === "expense" && !c.fixed);
  const sc = rec?.scenarios[sel];

  return (
    <div className="space-y-4">
      <Notice tone="info">Suggestions are estimates from your past spending, not guaranteed savings. Fixed commitments (rent, utilities, insurance, loans, subscriptions) are never reduced and nothing changes in your budget unless you choose to apply it.</Notice>
      <Card title="Run optimization">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Months of history to average"><Select value={months} onChange={(e) => setMonths(e.target.value)}>{["1", "3", "6", "12"].map((m) => <option key={m}>{m}</option>)}</Select></Field>
        </div>
        <details className="mt-3 text-sm"><summary className="cursor-pointer font-medium">Minimum monthly spend per category (optional)</summary>
          <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">{expenseCats.map((c) => (
            <Field key={c.name} label={c.name}><Input inputMode="decimal" placeholder="no minimum" value={mins[c.name] ?? ""} onChange={(e) => setMins((m) => ({ ...m, [c.name]: e.target.value }))} /></Field>))}</div>
        </details>
        {run.error ? <div className="mt-3"><ErrorBox error={run.error} /></div> : null}
        <div className="mt-3"><Button disabled={run.isPending} onClick={() => run.mutate(undefined)}>{run.isPending ? "Optimizing…" : "Find savings opportunities"}</Button></div>
      </Card>
      {outcome && <Notice tone="good">{outcome}</Notice>}

      {rec && sc && (
        <Card title={`Results (based on ${rec.history_months} month(s): ${rec.basis_periods[0]} to ${rec.basis_periods[rec.basis_periods.length - 1]})`}>
          {rec.warnings.map((w) => <div key={w} className="mb-2"><Notice tone="warn">{w}</Notice></div>)}
          <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {(["conservative", "balanced", "aggressive"] as const).map((k) => (
              <button key={k} onClick={() => setSel(k)} aria-pressed={sel === k} className={`rounded-xl border p-3.5 text-left text-sm transition-colors ${sel === k ? "border-blue-600 bg-blue-50 ring-1 ring-blue-600" : "border-slate-200 bg-surface hover:border-slate-300"}`}>
                <div className="flex items-center justify-between"><span className="font-medium capitalize">{k}</span><span className="text-xs text-slate-500">potential saving</span></div>
                <div className="num mt-1 text-lg font-semibold text-green-700">{formatMoney(rec.scenarios[k].achieved_savings, cur)}<span className="text-xs font-normal text-slate-500"> / month</span></div>
                <div className="text-xs text-slate-500">{formatMoney(rec.scenarios[k].annual_savings, cur)} per year{!rec.scenarios[k].target_reached && " · target not fully reachable within limits"}</div>
              </button>))}
          </div>
          <TableWrap><table className="w-full text-left text-sm">
            <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-1.5">Category</th><th className="p-1.5 text-right">Current / month</th><th className="p-1.5 text-right">Suggested</th><th className="p-1.5 text-right">Change</th></tr></thead>
            <tbody>{sc.categories.map((c) => (
              <tr key={c.category} className="border-t border-slate-100">
                <td className="p-1.5">{c.category}</td><td className="p-1.5 text-right">{formatMoney(c.current, cur)}</td>
                <td className="p-1.5 text-right">{editing ? <Input aria-label={`Suggested ${c.category}`} className="ml-auto w-28 text-right" inputMode="decimal" value={edits[c.category] ?? toInputString(c.suggested)} onChange={(e) => setEdits((m) => ({ ...m, [c.category]: e.target.value }))} /> : formatMoney(c.suggested, cur)}</td>
                <td className="p-1.5 text-right">{c.change === 0 ? "—" : `${formatMoney(c.change, cur)} (${formatPct(c.change_pct)})`}</td>
              </tr>))}</tbody></table></TableWrap>
          <p className="mt-2 text-xs text-slate-500">Fixed (not changed): {rec.fixed_categories.map((f) => `${f.category} ${formatMoney(f.monthly, cur)}`).join(" · ") || "none"}.</p>
          <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">How this was calculated</summary>{rec.method} Solver used: {sc.solver}. {rec.disclaimer}</details>
          <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={editing} onChange={(e) => setEditing(e.target.checked)} /> Edit the suggested amounts before accepting</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={apply} onChange={(e) => setApply(e.target.checked)} /> Also save as my budget for <Input type="month" aria-label="Apply to month" className="w-40" value={period} onChange={(e) => setPeriod(e.target.value)} /></label>
            {(accept.error || reject.error) ? <ErrorBox error={accept.error ?? reject.error} /> : null}
            <div className="flex gap-2"><Button disabled={accept.isPending} onClick={() => accept.mutate(undefined)}>Accept {sel} plan</Button><Button variant="secondary" disabled={reject.isPending} onClick={() => reject.mutate(undefined)}>Reject</Button></div>
          </div>
        </Card>
      )}
    </div>
  );
}

const amount = (req = false) => z.string().refine((v) => (v.trim() === "" ? !req : toMinor(v) !== null), "Enter a valid amount");
const num = (max: number) => z.string().refine((v) => v.trim() === "" || (!Number.isNaN(Number(v)) && Number(v) >= 0 && Number(v) <= max), `0 to ${max}`);
const simSchema = z.object({
  horizon: z.string().refine((v) => /^\d+$/.test(v) && +v >= 1 && +v <= 360, "1 to 360 months"),
  extra: amount(), incAmt: amount(), incPct: num(500), starting: amount(), baseIncome: amount(), baseExpense: amount(), goal: amount(), efMonths: num(36), ret: num(50),
  cutCat: z.string(), cutPct: num(100),
  loan: z.boolean(), loanMode: z.enum(["existing", "new"]), principal: amount(), rate: num(100), tenure: z.string(), loanExtra: amount(),
}).superRefine((v, ctx) => {
  if (!v.loan) return;
  const issue = (path: string, message: string) => ctx.addIssue({ code: "custom", path: [path], message });
  if ((toMinor(v.principal) ?? 0) <= 0) issue("principal", "Enter the loan amount (more than zero).");
  if (v.rate.trim() === "") issue("rate", "Enter the annual interest rate. Enter 0 only for an interest-free loan.");
  if (!/^\d+$/.test(v.tenure.trim()) || +v.tenure < 1 || +v.tenure > 600) issue("tenure", "Enter the tenure as a whole number of months, from 1 to 600.");
});
type SimForm = z.infer<typeof simSchema>;

type SimOut = {
  horizon_months: number; baseline: { monthly_income: number; monthly_expense: number; monthly_savings: number; final_balance: number };
  scenario: { monthly_income: number; monthly_expense: number; monthly_savings: number; final_balance: number };
  baseline_starting_savings: number; scenario_starting_savings: number;
  annual_return_pct?: number; negative_balance_months?: { baseline: number; scenario: number };
  difference: { monthly_savings: number; final_balance: number; total_extra_saved: number; starting_savings: number; from_monthly_changes: number; from_loan_cash_flow: number; from_growth: number };
  series: { baseline: number[]; scenario: number[] }; assumptions: string[];
  goal?: { target: number; baseline_month: number | null; scenario_month: number | null };
  emergency_fund?: { baseline_target: number; scenario_target: number; baseline_month: number | null; scenario_month: number | null; months_of_cover: number };
  loan?: { emi: number; months: number; total_interest: number; total_paid: number; mode?: "existing" | "new"; cash_flow?: { embedded_emi: number; net_vs_baseline: number };
    with_extra_payment?: { months: number; months_saved: number; interest_saved: number }; assumptions: string[] };
};

function Simulation() {
  const cur = useCurrency();
  const profileSavings = useAuth().user?.current_savings ?? 0;
  const cats = useCategories();
  const form = useForm<SimForm>({ resolver: zodResolver(simSchema), defaultValues: {
    horizon: "24", extra: "", incAmt: "", incPct: "", starting: "", baseIncome: "", baseExpense: "", goal: "", efMonths: "", ret: "", cutCat: "", cutPct: "", loan: false, loanMode: "existing", principal: "", rate: "", tenure: "", loanExtra: "" } });
  const [out, setOut] = useState<SimOut | null>(null);
  const [save, setSave] = useState(false);
  const run = useAction(async (v: SimForm) => {
    const m = (s: string) => (s.trim() ? toMinor(s)! : undefined);
    const body: Record<string, unknown> = { horizon_months: +v.horizon, extra_monthly_saving: m(v.extra) ?? 0, income_increase_amount: m(v.incAmt) ?? 0, income_increase_pct: v.incPct ? +v.incPct : 0,
      annual_return_pct: v.ret ? +v.ret : 0, save, name: "Scenario" };
    for (const [k, s] of [["starting_savings", v.starting], ["base_monthly_income", v.baseIncome], ["base_monthly_expense", v.baseExpense], ["goal_target", v.goal]] as const) if (s.trim()) body[k] = m(s);
    if (v.efMonths) body.emergency_fund_months = +v.efMonths;
    if (v.cutCat && v.cutPct) body.category_cuts = [{ category: v.cutCat, percent: +v.cutPct }];
    if (v.loan) body.loan = { principal: m(v.principal), annual_rate_pct: +v.rate, tenure_months: +v.tenure, extra_monthly_payment: m(v.loanExtra) ?? 0, mode: v.loanMode };
    return api.post<SimOut>("/simulations/run", body);
  }, setOut);
  const e = form.formState.errors;
  const loan = form.watch("loan");
  const loanMode = form.watch("loanMode");
  const chart = out ? out.series.baseline.map((b, i) => ({ month: i + 1, Baseline: b, Scenario: out.series.scenario[i] })) : [];
  const mo = (v: number | null | undefined) => (v === null || v === undefined ? "not within horizon" : `month ${v}`);

  return (
    <div className="space-y-4">
      <Notice tone="info">Projections use your real average income and spending unless you override them. Investment growth is an illustrative assumption, not a guaranteed return or personalised investment advice.</Notice>
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-5">
      <Card title="Scenario inputs" description="Leave a field empty to use your real figures" className="xl:col-span-2">
        <form onSubmit={form.handleSubmit((v) => run.mutate(v))} noValidate className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Horizon (months)" error={e.horizon?.message}><Input inputMode="numeric" {...form.register("horizon")} /></Field>
            <Field label="Save extra every month" error={e.extra?.message}><Input inputMode="decimal" placeholder="0.00" {...form.register("extra")} /></Field>
            <Field label="Income increase (amount / month)" error={e.incAmt?.message}><Input inputMode="decimal" placeholder="0.00" {...form.register("incAmt")} /></Field>
            <Field label="Income increase (%)" error={e.incPct?.message}><Input inputMode="decimal" placeholder="0" {...form.register("incPct")} /></Field>
            <Field label="Reduce category by (%)" error={e.cutPct?.message}>
              <div className="flex gap-2"><Select aria-label="Category to reduce" {...form.register("cutCat")}><option value="">Category…</option>{(cats.data?.categories ?? []).filter((c) => c.type === "expense").map((c) => <option key={c.name}>{c.name}</option>)}</Select>
                <Input className="w-20" inputMode="decimal" placeholder="%" {...form.register("cutPct")} /></div>
            </Field>
            <Field label="Illustrative annual return (%)" error={e.ret?.message} hint="0 = no growth. Growth is applied only to a positive savings balance; a negative balance is never grown."><Input inputMode="decimal" placeholder="0" {...form.register("ret")} /></Field>
            <Field label="Goal target (optional)" error={e.goal?.message}><Input inputMode="decimal" {...form.register("goal")} /></Field>
            <Field label="Emergency fund months (optional)" error={e.efMonths?.message}><Input inputMode="numeric" {...form.register("efMonths")} /></Field>
            <Field label="Starting savings (default: profile)" error={e.starting?.message} hint={`Scenario only: a one-off opening balance, not a monthly saving. The baseline always starts from your profile balance (${formatMoney(profileSavings, cur)}).`}><Input inputMode="decimal" {...form.register("starting")} /></Field>
            <Field label="Override monthly income" error={e.baseIncome?.message}><Input inputMode="decimal" {...form.register("baseIncome")} /></Field>
            <Field label="Override monthly spending" error={e.baseExpense?.message}><Input inputMode="decimal" {...form.register("baseExpense")} /></Field>
          </div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" {...form.register("loan")} /> Include a loan / EMI calculation</label>
          {loan && (
            <div className="space-y-3 rounded-lg border border-slate-200 p-3">
              <fieldset className="space-y-1.5 text-sm">
                <legend className="mb-1 font-medium text-slate-700">Which kind of loan is this?</legend>
                <label className="flex items-start gap-2"><input type="radio" value="existing" className="mt-1" {...form.register("loanMode")} /> <span>Existing loan — EMI already included in my regular spending</span></label>
                <label className="flex items-start gap-2"><input type="radio" value="new" className="mt-1" {...form.register("loanMode")} /> <span>New loan — deduct EMI from projected savings</span></label>
                <p className="text-xs text-slate-500">{loanMode === "new"
                  ? "Every scheduled repayment is deducted from projected savings in both the baseline and the scenario, so the comparison shows what extra payments change."
                  : "Your regular spending already includes this EMI, so only the difference from it changes your savings: extra payments reduce them, and the EMI you stop paying after payoff is added back."}</p>
              </fieldset>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Principal" error={e.principal?.message}><Input inputMode="decimal" {...form.register("principal")} /></Field>
                <Field label="Annual rate (%)" error={e.rate?.message} hint="Required. Enter 0 for an interest-free loan."><Input inputMode="decimal" {...form.register("rate")} /></Field>
                <Field label="Tenure (months)" error={e.tenure?.message} hint="1 to 600"><Input inputMode="numeric" {...form.register("tenure")} /></Field>
                <Field label="Extra payment / month" error={e.loanExtra?.message} hint="Optional, on top of the EMI"><Input inputMode="decimal" {...form.register("loanExtra")} /></Field>
              </div>
            </div>
          )}
          {run.error ? <ErrorBox error={run.error} /> : null}
          <div className="flex items-center gap-4"><Button type="submit" disabled={run.isPending}>{run.isPending ? "Calculating…" : "Run simulation"}</Button>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={save} onChange={(e2) => setSave(e2.target.checked)} /> Save this scenario</label></div>
        </form>
      </Card>

      <div className="space-y-4 xl:col-span-3">
      {!out && <Empty title="Your results will appear here">Fill in any scenario inputs and run the simulation. Projected savings are estimates, not guarantees.</Empty>}
      {out && (
        <Card title="Results" description="Scenario compared with your baseline">
          <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Extra saved per month" value={formatMoney(out.difference.monthly_savings, cur)} tone={out.difference.monthly_savings >= 0 ? "good" : "bad"} sub="Monthly changes only (extra saving, income, cuts)" />
            <Stat label="Extra from starting savings" value={formatMoney(out.difference.starting_savings, cur)} tone={out.difference.starting_savings > 0 ? "good" : out.difference.starting_savings < 0 ? "bad" : undefined} sub="One-off opening balance, not monthly" />
            <Stat label={`Extra from monthly changes over ${out.horizon_months} months`} value={formatMoney(out.difference.from_monthly_changes, cur)} tone={out.difference.from_monthly_changes >= 0 ? "good" : "bad"} sub="Extra saving, income, cuts. Before any growth." />
            {out.loan && <Stat label={`Loan cash-flow effect over ${out.horizon_months} months`} value={formatMoney(out.difference.from_loan_cash_flow, cur)} tone={out.difference.from_loan_cash_flow > 0 ? "good" : out.difference.from_loan_cash_flow < 0 ? "bad" : undefined} sub="Extra repayments (−) and freed EMI (+). Not a monthly-savings change." />}
            <Stat label={`Extra after ${out.horizon_months} months`} value={formatMoney(out.difference.final_balance, cur)} tone={out.difference.final_balance >= 0 ? "good" : "bad"} sub="Everything combined, vs baseline" />
          </div>
          <TableWrap><table className="w-full text-left text-sm">
            <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600"><tr><th className="p-1.5" /><th className="p-1.5 text-right">Baseline</th><th className="p-1.5 text-right">Scenario</th><th className="p-1.5 text-right">Difference</th></tr></thead>
            <tbody>
              {([["Monthly income", out.baseline.monthly_income, out.scenario.monthly_income], ["Monthly spending", out.baseline.monthly_expense, out.scenario.monthly_expense],
                ["Monthly savings", out.baseline.monthly_savings, out.scenario.monthly_savings], ["Starting savings (one-off)", out.baseline_starting_savings, out.scenario_starting_savings], [`Savings after ${out.horizon_months} months`, out.baseline.final_balance, out.scenario.final_balance]] as const).map(([l, a, b]) => (
                <tr key={l} className="border-t border-slate-100"><td className="p-1.5">{l}</td><td className="p-1.5 text-right">{formatMoney(a, cur)}</td><td className="p-1.5 text-right">{formatMoney(b, cur)}</td><td className="p-1.5 text-right">{formatMoney(b - a, cur)}</td></tr>))}
            </tbody></table></TableWrap>
          {!!out.annual_return_pct && (
            <div className="mt-3">
              <Notice tone="info">
                <b>How the {out.annual_return_pct}% illustrative return is applied:</b> only to a positive savings balance, month by month. A balance at or below zero earns no return
                and is never multiplied by a growth factor, because a shortfall is not an invested asset. If the balance turns positive during the projection, growth applies only to the positive balance from then on.
                {out.negative_balance_months && (out.negative_balance_months.baseline > 0 || out.negative_balance_months.scenario > 0) && (
                  <> In this projection the baseline balance is negative in {out.negative_balance_months.baseline} of {out.horizon_months} months and the scenario balance in {out.negative_balance_months.scenario}; those months earn no return.</>
                )}
              </Notice>
            </div>
          )}
          <p className="num mt-2 text-sm text-slate-700">
            Of the {formatMoney(out.difference.final_balance, cur)} difference after {out.horizon_months} months:{" "}
            {formatMoney(out.difference.starting_savings, cur)} from starting savings + {formatMoney(out.difference.from_monthly_changes, cur)} from monthly changes{out.loan && <> + {formatMoney(out.difference.from_loan_cash_flow, cur)} from loan cash flow</>}
            {out.difference.from_growth !== 0 && <> + {formatMoney(out.difference.from_growth, cur)} from illustrative growth</>}.
          </p>
          <div className="mt-4"><div className="mb-1 flex items-center gap-2 text-sm font-medium">Projected savings balance <Badge tone="info">Estimate</Badge></div></div>
          <div className="h-64"><ResponsiveContainer><LineChart data={chart}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="month" fontSize={12} tickLine={false} label={{ value: "Month", position: "insideBottom", offset: -2, fontSize: 11 }} /><YAxis fontSize={12} width={60} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} /><Tooltip formatter={(v) => formatMoney(Number(v), cur)} labelFormatter={(l) => `Month ${l}`} /><Legend />
            <Line isAnimationActive={false} dataKey="Baseline" stroke={CHART_COLORS[7]} dot={false} strokeWidth={2} strokeDasharray="5 4" /><Line isAnimationActive={false} dataKey="Scenario" stroke={POSITIVE} dot={false} strokeWidth={2.5} /></LineChart></ResponsiveContainer></div>
          <div className="mt-3 space-y-1 text-sm">
            {out.goal && <div>Goal of {formatMoney(out.goal.target, cur)}: baseline {mo(out.goal.baseline_month)}, scenario {mo(out.goal.scenario_month)}.</div>}
            {out.emergency_fund && <div>Emergency fund ({out.emergency_fund.months_of_cover} months): baseline target {formatMoney(out.emergency_fund.baseline_target, cur)} reached {mo(out.emergency_fund.baseline_month)}; scenario {mo(out.emergency_fund.scenario_month)}.</div>}
            {out.loan && (<div className="rounded-md bg-slate-50 p-3">
              <div className="mb-1 text-xs font-medium text-slate-600">{out.loan.mode === "new" ? "New loan: EMI deducted from projected savings" : "Existing loan: EMI already in your regular spending"}</div>
              <b>Loan:</b> EMI {formatMoney(out.loan.emi, cur)} · {out.loan.months} months · total interest {formatMoney(out.loan.total_interest, cur)} · total paid {formatMoney(out.loan.total_paid, cur)}
              {out.loan.with_extra_payment && <div>With extra payment: finishes in {out.loan.with_extra_payment.months} months ({out.loan.with_extra_payment.months_saved} sooner), saving {formatMoney(out.loan.with_extra_payment.interest_saved, cur)} interest.</div>}
              <ul className="mt-1 list-disc pl-5 text-xs text-slate-500">{out.loan.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></div>)}
          </div>
          <details className="mt-3 text-xs text-slate-500" open><summary className="cursor-pointer font-medium">Assumptions</summary><ul className="list-disc pl-5">{out.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
        </Card>
      )}
      </div>
      </div>
    </div>
  );
}
