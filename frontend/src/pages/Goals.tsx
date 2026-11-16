import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { IconPlus, IconTarget } from "../components/icons";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, ProgressBar, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useAction, useCurrency, useGet } from "../lib/hooks";
import { formatMoney, formatPct, toInputString, toMinor } from "../lib/money";
import type { Goal } from "../lib/types";

const amt = (label: string, required = true) => z.string().refine((v) => (v.trim() === "" ? !required : toMinor(v) !== null && (!required || toMinor(v)! > 0)), `${label}: enter a valid amount`);
const schema = z.object({
  name: z.string().trim().min(1, "Enter a name").max(80),
  target: amt("Target"), current: amt("Current", false), monthly: amt("Monthly", false),
  target_date: z.string(),
});
type Form = z.infer<typeof schema>;

type EF = { basis: string; months_of_cover: number; average_monthly_expense: number; target: number; current_savings: number; gap: number; coverage_months: number | null; months_to_goal: number | null; history_months_used: number; assumptions: string[] };

export default function Goals() {
  const cur = useCurrency();
  const goals = useGet<{ items: Goal[] }>("/goals");
  const [editing, setEditing] = useState<Goal | "new" | null>(null);
  const [adding, setAdding] = useState<Goal | null>(null);
  const [deleting, setDeleting] = useState<Goal | null>(null);
  const del = useAction((id: string) => api.del(`/goals/${id}`), () => setDeleting(null), "Goal deleted.");

  return (
    <div>
      <PageHeader title="Savings goals" description="Track what you are saving for and what it takes to get there."><Button onClick={() => setEditing("new")}><IconPlus className="h-4 w-4" />New goal</Button></PageHeader>
      {goals.isLoading ? <Spinner /> : goals.error ? <ErrorBox error={goals.error} retry={() => goals.refetch()} /> : goals.data!.items.length === 0 ? (
        <Empty title="No goals yet" icon={<IconTarget className="h-5 w-5" />}>Create a goal such as an emergency fund, a trip or a down payment.</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {goals.data!.items.map((g) => (
            <Card key={g.id} title={g.name} actions={g.achieved ? <Badge tone="good">Achieved</Badge> : <Badge tone={g.on_track === false ? "warn" : "neutral"}>{g.target_date ? `Target date ${g.target_date}` : "No target date"}</Badge>}>
              <ProgressBar pct={g.progress_pct} tone={g.achieved ? "good" : g.on_track === false ? "warn" : "info"} label={`${g.name} progress`} />
              <div className="num mt-1.5 flex justify-between text-sm"><span>{formatMoney(g.current_amount, cur)} of {formatMoney(g.target_amount, cur)}</span><b>{formatPct(g.progress_pct)}</b></div>
              <dl className="num mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-slate-500">Remaining</dt><dd>{formatMoney(g.remaining, cur)}</dd>
                <dt className="text-slate-500">Needed per month</dt><dd>{g.required_monthly_contribution === null ? "Set a target date" : formatMoney(g.required_monthly_contribution, cur)}</dd>
                <dt className="text-slate-500">Planned per month</dt><dd>{g.monthly_contribution ? formatMoney(g.monthly_contribution, cur) : "—"}</dd>
                <dt className="text-slate-500">Estimated completion</dt><dd>{g.estimated_completion ?? "Set a planned contribution"}{g.on_track === false && <span className="ml-1 font-medium text-amber-700">(after target date)</span>}{g.on_track === true && <span className="ml-1 font-medium text-green-700">(on track)</span>}</dd>
              </dl>
              <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">Assumptions</summary><ul className="list-disc pl-4">{g.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
              <div className="mt-4 flex flex-wrap gap-1">
                <Button variant="secondary" onClick={() => setAdding(g)}>Add savings</Button>
                <Button variant="ghost" onClick={() => setEditing(g)}>Edit</Button>
                <Button variant="ghost" disabled={del.isPending} onClick={() => setDeleting(g)}>Delete</Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      <EmergencyFund />
      {editing && <GoalForm goal={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {adding && <Contribute goal={adding} onClose={() => setAdding(null)} />}
      {deleting && (
        <ConfirmDialog title="Delete goal?" danger confirmLabel="Delete goal" busyLabel="Deleting…" busy={del.isPending} error={del.error} onConfirm={() => del.mutate(deleting.id)} onCancel={() => setDeleting(null)}>
          Delete the goal “{deleting.name}”? This cannot be undone.
        </ConfirmDialog>
      )}
    </div>
  );
}

function GoalForm({ goal, onClose }: { goal: Goal | null; onClose: () => void }) {
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: { name: goal?.name ?? "", target: goal ? toInputString(goal.target_amount) : "", current: goal ? toInputString(goal.current_amount) : "",
      monthly: goal?.monthly_contribution ? toInputString(goal.monthly_contribution) : "", target_date: goal?.target_date ?? "" },
  });
  const save = useAction(async (v: Form) => {
    const body = { name: v.name, target_amount: toMinor(v.target)!, current_amount: v.current.trim() ? toMinor(v.current)! : 0,
      monthly_contribution: v.monthly.trim() ? toMinor(v.monthly)! : 0, target_date: v.target_date || null };
    return goal ? api.patch<Goal>(`/goals/${goal.id}`, body) : api.post<Goal>("/goals", body);
  }, onClose, goal ? "Goal updated." : "Goal created.");
  const e = form.formState.errors;
  return (
    <Modal title={goal ? "Edit goal" : "New goal"} onClose={onClose}>
      <form onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate className="space-y-3">
        <Field label="Goal name" error={e.name?.message}><Input {...form.register("name")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Target amount" error={e.target?.message}><Input inputMode="decimal" {...form.register("target")} /></Field>
          <Field label="Saved so far" error={e.current?.message}><Input inputMode="decimal" placeholder="0.00" {...form.register("current")} /></Field>
          <Field label="Target date (optional)"><Input type="date" {...form.register("target_date")} /></Field>
          <Field label="Planned monthly saving (optional)" error={e.monthly?.message}><Input inputMode="decimal" {...form.register("monthly")} /></Field>
        </div>
        {save.error ? <ErrorBox error={save.error} /> : null}
        <div className="modal-footer flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save goal"}</Button></div>
      </form>
    </Modal>
  );
}

function Contribute({ goal, onClose }: { goal: Goal; onClose: () => void }) {
  const form = useForm<{ amount: string }>({ resolver: zodResolver(z.object({ amount: amt("Amount") })), defaultValues: { amount: "" } });
  const save = useAction((v: { amount: string }) => api.post<Goal>(`/goals/${goal.id}/contributions`, { amount: toMinor(v.amount)! }), onClose, "Savings added to goal.");
  return (
    <Modal title={`Add savings to ${goal.name}`} onClose={onClose}>
      <form onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate className="space-y-3">
        <Field label="Amount" error={form.formState.errors.amount?.message}><Input inputMode="decimal" autoFocus {...form.register("amount")} /></Field>
        {save.error ? <ErrorBox error={save.error} /> : null}
        <div className="modal-footer flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" disabled={save.isPending}>Add</Button></div>
      </form>
    </Modal>
  );
}

function EmergencyFund() {
  const cur = useCurrency();
  const [months, setMonths] = useState("6");
  const [basis, setBasis] = useState("essential");
  const [contrib, setContrib] = useState("");
  const [manual, setManual] = useState("");
  const [run, setRun] = useState(false);
  const c = toMinor(contrib), mm = toMinor(manual);
  const q = useGet<EF>("/goals/emergency-fund", { months, basis, monthly_contribution: c ?? undefined, monthly_expense: manual.trim() ? mm ?? undefined : undefined }, run);
  return (
    <Card title="Emergency fund calculator" description="How much cover you need, and how long it takes to get there" className="mt-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <Field label="Months of cover"><Select value={months} onChange={(e) => setMonths(e.target.value)}>{[3, 6, 9, 12].map((m) => <option key={m}>{m}</option>)}</Select></Field>
        <Field label="Cover which spending?"><Select value={basis} onChange={(e) => setBasis(e.target.value)}><option value="essential">Essential only</option><option value="total">All spending</option></Select></Field>
        <Field label="Monthly contribution" hint="Optional"><Input inputMode="decimal" value={contrib} onChange={(e) => setContrib(e.target.value)} /></Field>
        <Field label="Or enter monthly spending" hint="Optional override"><Input inputMode="decimal" value={manual} onChange={(e) => setManual(e.target.value)} /></Field>
      </div>
      <div className="mt-3"><Button disabled={run && q.isFetching} onClick={() => { if (run) void q.refetch(); else setRun(true); }}>{run && q.isFetching ? "Calculating…" : "Calculate"}</Button></div>
      {run && q.isFetching && <Spinner label="Calculating…" compact />}
      {run && q.error ? <div className="mt-3"><ErrorBox error={q.error} /></div> : null}
      {run && q.data && !q.isFetching && (
        <div className="mt-4 space-y-2 rounded-lg bg-slate-50 p-4 text-sm">
          <div>Target: <b>{formatMoney(q.data.target, cur)}</b> ({q.data.months_of_cover} × {formatMoney(q.data.average_monthly_expense, cur)})</div>
          <div>Current savings (from your profile): {formatMoney(q.data.current_savings, cur)} {q.data.coverage_months !== null && `≈ ${q.data.coverage_months} months of cover`}</div>
          <div>Gap: <b className={q.data.gap > 0 ? "text-red-700" : "text-green-700"}>{formatMoney(q.data.gap, cur)}</b>{q.data.months_to_goal !== null && q.data.gap > 0 && <> · about <b>{q.data.months_to_goal}</b> months at your contribution</>}</div>
          <Notice tone="info"><ul className="list-disc pl-4">{q.data.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></Notice>
        </div>
      )}
    </Card>
  );
}
