import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { IconAlert, IconArrowDown, IconArrowUp, IconCheck, IconList, IconPlus, IconRepeat, IconScan, IconTarget, IconUpload, IconWallet } from "../components/icons";
import { Badge, Card, CHART_COLORS, Empty, ErrorBox, Input, NEGATIVE, Notice, PageHeader, POSITIVE, ProgressBar, Skeleton, Spinner, Stat } from "../components/ui";
import { useAuth } from "../lib/auth";
import { useGet } from "../lib/hooks";
import { formatCompact, formatMoney, formatPct } from "../lib/money";
import type { Dashboard as D, Goal, Recurring, Tx } from "../lib/types";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const monthName = (p: string) => { const [y, m] = p.split("-").map(Number); return m >= 1 && m <= 12 ? `${MONTHS[m - 1]} ${y}` : p; };

export default function Dashboard() {
  const { user } = useAuth();
  const [period, setPeriod] = useState<string>("");
  const q = useGet<D>("/analytics/dashboard", { period: period || undefined });
  if (q.isLoading) return <Spinner label="Loading your dashboard…" />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const cur = d.currency;
  const m = (v: number) => formatMoney(v, cur);
  const t = d.totals;
  const first = (user?.name ?? "").trim().split(/\s+/)[0];

  return (
    <div>
      <PageHeader title="Dashboard" description={`Welcome back${first ? `, ${first}` : ""}. Here is your financial picture for ${monthName(d.period)}.`}>
        <label className="text-sm font-medium text-slate-600" htmlFor="period">Month</label>
        <Input id="period" type="month" className="w-40" value={period || d.period} onChange={(e) => setPeriod(e.target.value)} />
      </PageHeader>

      {!d.has_transactions ? <Onboarding /> : (
        <div className="space-y-5">
          <QuickActions />

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat icon={<IconArrowDown className="h-4 w-4" />} label="Total income" value={m(t.income)} tone="good" sub={d.vs_previous_month ? `${signed(d.vs_previous_month.income_change, cur)} vs last month` : undefined} />
            <Stat icon={<IconArrowUp className="h-4 w-4" />} label="Total expenses" value={m(t.net_expense)} tone="bad" sub={d.vs_previous_month ? `${signed(d.vs_previous_month.expense_change, cur)} vs last month` : "Net of refunds"} />
            <Stat icon={<IconWallet className="h-4 w-4" />} label="Net savings" value={m(t.net_cash_flow)} tone={t.net_cash_flow >= 0 ? "good" : "bad"} sub="Income minus expenses" />
            <Stat icon={<IconTarget className="h-4 w-4" />} label="Savings rate" value={formatPct(t.savings_pct)} tone={t.savings_pct === null ? undefined : t.savings_pct >= 0 ? "good" : "bad"} sub={t.refunds ? `Includes ${m(t.refunds)} refunds` : "Share of income kept"} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
            <Card className="lg:col-span-3" title="Income vs expenses" description="Last 6 months, net of refunds">
              <div className="h-64">
                <ResponsiveContainer>
                  <BarChart data={d.trend.map((r) => ({ period: r.period, Income: r.income, Expenses: r.net_expense }))} margin={{ left: 0, right: 8, top: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="period" fontSize={12} tickLine={false} />
                    <YAxis fontSize={12} width={56} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} />
                    <Tooltip formatter={(v) => m(Number(v))} />
                    <Legend />
                    <Bar isAnimationActive={false} dataKey="Income" fill={POSITIVE} radius={[4, 4, 0, 0]} />
                    <Bar isAnimationActive={false} dataKey="Expenses" fill={NEGATIVE} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card className="lg:col-span-2" title="Spending by category" description={monthName(d.period)}>
              {d.categories.length === 0 ? <Empty title="No spending this month" /> : (
                <div className="space-y-3">
                  <div className="h-44">
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie isAnimationActive={false} data={d.categories.map((c) => ({ name: c.category, value: Math.max(0, c.amount) }))} dataKey="value" nameKey="name" innerRadius={45} outerRadius={80} paddingAngle={1} stroke="none">
                          {d.categories.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                        </Pie>
                        <Tooltip formatter={(v) => m(Number(v))} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <ul className="space-y-1.5 text-sm">
                    {d.categories.map((c, i) => (
                      <li key={c.category} className="flex items-center justify-between gap-2">
                        <span className="flex min-w-0 items-center gap-2"><span aria-hidden className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: CHART_COLORS[i % CHART_COLORS.length] }} /><span className="truncate">{c.category}{c.fixed ? " (fixed)" : ""}</span></span>
                        <span className="num shrink-0 text-slate-700">{m(c.amount)} · {formatPct(c.pct)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
            <Card className="lg:col-span-2" title="Spending trend" description="Net expenses and net savings, last 6 months">
              <div className="h-56">
                <ResponsiveContainer>
                  <AreaChart data={d.trend.map((r) => ({ period: r.period, Expenses: r.net_expense, "Net savings": r.net_cash_flow }))} margin={{ left: 0, right: 8, top: 8 }}>
                    <defs><linearGradient id="spendFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={NEGATIVE} stopOpacity={0.25} /><stop offset="100%" stopColor={NEGATIVE} stopOpacity={0} /></linearGradient></defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="period" fontSize={12} tickLine={false} />
                    <YAxis fontSize={12} width={56} tickLine={false} axisLine={false} tickFormatter={(v) => formatCompact(Number(v), cur)} />
                    <Tooltip formatter={(v) => m(Number(v))} />
                    <Legend />
                    <Area isAnimationActive={false} type="monotone" dataKey="Expenses" stroke={NEGATIVE} strokeWidth={2} fill="url(#spendFill)" />
                    <Line isAnimationActive={false} type="monotone" dataKey="Net savings" stroke={POSITIVE} strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <RecentTransactions period={d.period} cur={cur} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <BudgetOverview d={d} cur={cur} />
            <GoalsOverview cur={cur} />
            <UpcomingRecurring cur={cur} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Insights period={d.period} />
            <Card title="Fixed vs variable spending">
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-slate-600">Fixed</span><b className="num">{m(d.fixed_variable.fixed)} ({formatPct(d.fixed_variable.fixed_pct)})</b></div>
                <div className="flex justify-between"><span className="text-slate-600">Variable</span><b className="num">{m(d.fixed_variable.variable)}</b></div>
                {d.vs_same_month_last_year && (
                  <p className="pt-1 text-xs text-slate-500">Same month last year: spending {signed(d.vs_same_month_last_year.expense_change, cur)}, income {signed(d.vs_same_month_last_year.income_change, cur)}.</p>
                )}
              </div>
            </Card>
            <Card title="Financial health score">
              {d.health_score.score === null ? <p className="text-sm text-slate-600">{d.health_score.note}</p> : (
                <div className="text-sm">
                  <div className="num text-3xl font-semibold">{d.health_score.score}<span className="text-base font-normal text-slate-500"> / 100</span></div>
                  <ul className="mt-2 space-y-1.5">
                    {d.health_score.components.map((c) => <li key={c.name}><b>{c.name}</b>: {c.score}/{c.max}<div className="text-xs text-slate-500">{c.detail}</div></li>)}
                  </ul>
                  <div className="mt-3"><Notice tone="info">{d.health_score.note}</Notice></div>
                </div>
              )}
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function signed(v: number, cur: string) { return `${v >= 0 ? "+" : "-"}${formatMoney(Math.abs(v), cur)}`; }

function QuickActions() {
  const A = ({ to, icon, children }: { to: string; icon: ReactNode; children: ReactNode }) => (
    <Link to={to} className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-slate-300 bg-surface px-3 py-1.5 text-sm font-medium text-slate-800 shadow-xs hover:bg-slate-100">{icon}{children}</Link>
  );
  return (
    <div className="flex flex-wrap gap-2" aria-label="Quick actions" role="group">
      <A to="/transactions" icon={<IconPlus className="h-4 w-4" />}>Add a transaction</A>
      <A to="/import" icon={<IconUpload className="h-4 w-4" />}>Upload a statement</A>
      <A to="/slips" icon={<IconScan className="h-4 w-4" />}>Scan slips</A>
      <A to="/budgets" icon={<IconWallet className="h-4 w-4" />}>Plan a budget</A>
    </div>
  );
}

function Onboarding() {
  const steps = [
    { to: "/import", icon: <IconUpload />, title: "Import a bank statement", text: "Upload a CSV or Excel export. Duplicates are detected and nothing is saved until you confirm." },
    { to: "/slips", icon: <IconScan />, title: "Scan your payment slips", text: "Upload UPI, card or bank slips. Text is read on the server, you review it, then confirm." },
    { to: "/transactions", icon: <IconList />, title: "Add a transaction", text: "Enter a single income or expense by hand to get started." },
    { to: "/settings", icon: <IconTarget />, title: "Complete your profile", text: "Set your currency, expected income and savings target." },
  ];
  return (
    <div className="space-y-4">
      <Empty title="No transactions yet" icon={<IconWallet className="h-5 w-5" />}>
        Your dashboard fills in as soon as you add data. Nothing here is sample data: every figure is calculated from your own transactions.
      </Empty>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((s, i) => (
          <Link key={s.to} to={s.to} className="group rounded-xl border border-slate-200 bg-surface p-4 shadow-sm transition-colors hover:border-blue-300">
            <div className="mb-2 flex items-center gap-2 text-blue-700"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 [&>svg]:h-4 [&>svg]:w-4">{s.icon}</span><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step {i + 1}</span></div>
            <div className="font-medium text-slate-900 group-hover:text-blue-700">{s.title}</div>
            <p className="mt-1 text-xs text-slate-500">{s.text}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}

function RecentTransactions({ period, cur }: { period: string; cur: string }) {
  const q = useGet<{ items: Tx[]; total: number }>("/transactions", { sort: "date_desc", page_size: 6, date_from: `${period}-01`, date_to: lastDay(period) });
  return (
    <Card className="lg:col-span-3" title="Recent transactions" actions={<Link to="/transactions" aria-label="See every recent entry" className="text-sm font-medium text-blue-700 hover:underline">View all</Link>} description={`Latest in ${monthName(period)}`}>
      {q.isLoading ? <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-8" />)}</div> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : !q.data!.items.length ? <Empty title="No transactions in this month" /> : (
        <ul className="divide-y divide-slate-100">
          {q.data!.items.map((t) => {
            const plus = t.type === "income" || t.type === "refund";
            return (
              <li key={t.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="truncate font-medium">{t.description}</div>
                  <div className="text-xs text-slate-500">{t.date} · {t.category}{t.source === "slip" ? " · slip" : ""}</div>
                </div>
                <div className={`num shrink-0 font-medium ${plus ? "text-green-700" : t.type === "transfer" ? "text-slate-600" : "text-slate-900"}`}>{plus ? "+" : t.type === "expense" ? "-" : ""}{formatMoney(t.amount, cur)}</div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function lastDay(period: string) {
  const [y, m] = period.split("-").map(Number);
  return `${period}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
}

function BudgetOverview({ d, cur }: { d: D; cur: string }) {
  const b = d.budget;
  const rows = [...(b.rows ?? [])].filter((r) => !r.unbudgeted).sort((x, y) => (y.utilization_pct ?? 0) - (x.utilization_pct ?? 0)).slice(0, 4);
  return (
    <Card title="Budget usage" actions={<Link to="/budgets" aria-label="Manage this month's budget" className="text-sm font-medium text-blue-700 hover:underline">Manage</Link>}>
      {b.exists ? (
        <div className="space-y-3 text-sm">
          <div>
            <div className="num flex justify-between"><span>{formatMoney(b.total_actual, cur)} of {formatMoney(b.total_budget, cur)} used</span><b>{formatPct(b.utilization_pct)}</b></div>
            <div className="mt-1.5"><ProgressBar pct={b.utilization_pct} tone={b.total_actual > b.total_budget ? "bad" : (b.utilization_pct ?? 0) >= 85 ? "warn" : "info"} label="Total budget used" /></div>
          </div>
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.category}>
                <div className="flex justify-between text-xs"><span>{r.category}</span><span className={r.over_budget ? "font-medium text-red-700" : "text-slate-500"}>{formatPct(r.utilization_pct)}</span></div>
                <ProgressBar pct={r.utilization_pct} over={r.over_budget} label={`${r.category} budget used`} />
              </li>
            ))}
          </ul>
          {b.overspent_categories.length > 0 && <div className="flex items-start gap-1.5 text-red-700"><IconAlert className="mt-0.5 h-4 w-4 shrink-0" /><span>Over budget: {b.overspent_categories.join(", ")}</span></div>}
        </div>
      ) : <p className="text-sm text-slate-600">No budget for {d.period}. <Link className="font-medium text-blue-700 underline" to="/budgets">Create one</Link>.</p>}
    </Card>
  );
}

function GoalsOverview({ cur }: { cur: string }) {
  const q = useGet<{ items: Goal[] }>("/goals");
  const items = (q.data?.items ?? []).slice(0, 4);
  return (
    <Card title="Savings goals" actions={<Link to="/goals" aria-label="See every goal" className="text-sm font-medium text-blue-700 hover:underline">All goals</Link>}>
      {q.isLoading ? <Skeleton className="h-24" /> : q.error ? <ErrorBox error={q.error} /> : !items.length ? <p className="text-sm text-slate-600">No goals yet. <Link className="font-medium text-blue-700 underline" to="/goals">Create a goal</Link> to track progress here.</p> : (
        <ul className="space-y-3 text-sm">
          {items.map((g) => (
            <li key={g.id}>
              <div className="flex items-center justify-between gap-2"><span className="truncate font-medium">{g.name}</span>{g.achieved ? <Badge tone="good"><IconCheck className="h-3 w-3" />Achieved</Badge> : <span className="num text-xs text-slate-500">{formatPct(g.progress_pct)}</span>}</div>
              <div className="my-1"><ProgressBar pct={g.progress_pct} tone={g.achieved ? "good" : "info"} label={`${g.name} progress`} /></div>
              <div className="num text-xs text-slate-500">{formatMoney(g.current_amount, cur)} of {formatMoney(g.target_amount, cur)}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function UpcomingRecurring({ cur }: { cur: string }) {
  const q = useGet<{ items: (Recurring & { kind?: string })[] }>("/recurring");
  const today = new Date().toISOString().slice(0, 10);
  const items = (q.data?.items ?? []).filter((r) => r.next_expected && r.next_expected >= today).sort((a, b) => a.next_expected!.localeCompare(b.next_expected!)).slice(0, 5);
  return (
    <Card title="Upcoming recurring payments" description="Estimated from your past transactions" actions={<Link to="/analytics" aria-label="Recurring payment details" className="text-sm font-medium text-blue-700 hover:underline">Details</Link>}>
      {q.isLoading ? <Skeleton className="h-24" /> : q.error ? <ErrorBox error={q.error} /> : !items.length ? (
        <p className="text-sm text-slate-600">No upcoming payments detected. Recurring payments can be detected once you have a few months of history: <Link className="font-medium text-blue-700 underline" to="/analytics">detect them here</Link>.</p>
      ) : (
        <ul className="divide-y divide-slate-100 text-sm">
          {items.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2 py-2">
              <div className="flex min-w-0 items-center gap-2"><IconRepeat className="h-4 w-4 shrink-0 text-slate-400" /><div className="min-w-0"><div className="truncate font-medium">{r.name}</div><div className="text-xs text-slate-500">~{r.next_expected} · {r.frequency}</div></div></div>
              <div className="num shrink-0">{formatMoney(r.amount, cur)}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** The backend generates these sentences from the month's real figures (the same ones used in reports). Nothing is written here. */
function Insights({ period }: { period: string }) {
  const q = useGet<{ insights: string[] }>("/reports/monthly", { period });
  return (
    <Card title="Important insights" description="Generated from your own data">
      {q.isLoading ? <Skeleton className="h-24" /> : q.error ? <ErrorBox error={q.error} /> : !q.data?.insights.length ? <p className="text-sm text-slate-600">No insights for this month yet.</p> : (
        <ul className="space-y-2 text-sm">{q.data.insights.slice(0, 5).map((i) => <li key={i} className="flex gap-2"><IconCheck className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" /><span>{i}</span></li>)}</ul>
      )}
    </Card>
  );
}

