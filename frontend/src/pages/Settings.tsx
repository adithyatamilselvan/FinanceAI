import { zodResolver } from "@hookform/resolvers/zod";
import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { IconMoon, IconShield, IconSun } from "../components/icons";
import { Button, Card, cx, ErrorBox, Field, Input, Notice, PageHeader, Select } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAction, useCategories, useGet } from "../lib/hooks";
import { CURRENCIES, toInputString, toMinor } from "../lib/money";
import { useTheme, type ThemePref } from "../lib/theme";
import type { User } from "../lib/types";

const money = z.string().refine((v) => v.trim() === "" || toMinor(v) !== null, "Enter a valid amount");
const profileSchema = z.object({
  name: z.string().trim().min(1, "Enter your name").max(80), currency: z.string(), income: money, savings: money,
  target: z.string().refine((v) => v.trim() === "" || (!Number.isNaN(Number(v)) && Number(v) >= 0 && Number(v) <= 100), "0 to 100"),
});
type PForm = z.infer<typeof profileSchema>;

export default function Settings() {
  return (
    <div className="space-y-6">
      <PageHeader title="Profile & settings" description="Your financial profile, appearance, security and categorisation preferences." />
      <ProfileCard /><AppearanceCard /><PasswordCard /><SecurityCard /><CategoriesCard /><RulesCard />
    </div>
  );
}

function AppearanceCard() {
  const { pref, set } = useTheme();
  const opts: { v: ThemePref; label: string; icon?: ReactNode }[] = [
    { v: "light", label: "Light", icon: <IconSun className="h-4 w-4" /> },
    { v: "dark", label: "Dark", icon: <IconMoon className="h-4 w-4" /> },
    { v: "system", label: "Match my device" },
  ];
  return (
    <Card title="Appearance" description="Applies to this browser only; it is not stored in your account.">
      <div role="radiogroup" aria-label="Theme" className="flex flex-wrap gap-2">
        {opts.map((o) => (
          <button key={o.v} type="button" role="radio" aria-checked={pref === o.v} onClick={() => set(o.v)}
            className={cx("inline-flex min-h-9 items-center gap-2 rounded-lg border px-3.5 py-1.5 text-sm font-medium transition-colors", pref === o.v ? "border-blue-500 bg-blue-50 text-blue-700" : "border-slate-300 text-slate-700 hover:bg-slate-100")}>
            {o.icon}{o.label}
          </button>
        ))}
      </div>
    </Card>
  );
}

function SecurityCard() {
  const { user } = useAuth();
  return (
    <Card title="Security" description="How your account is protected">
      <ul className="space-y-2 text-sm">
        <li className="flex gap-2"><IconShield className="mt-0.5 h-4 w-4 shrink-0 text-green-700" /><span>Your password is stored only as a salted Argon2id hash; it cannot be read back, even by us.</span></li>
        <li className="flex gap-2"><IconShield className="mt-0.5 h-4 w-4 shrink-0 text-green-700" /><span>Sign-in sessions expire automatically (60 minutes by default) and logging out ends the session on the server.</span></li>
        <li className="flex gap-2"><IconShield className="mt-0.5 h-4 w-4 shrink-0 text-green-700" /><span>Changing your password signs you out on every device.</span></li>
        <li className="flex gap-2"><IconShield className="mt-0.5 h-4 w-4 shrink-0 text-green-700" /><span>Statement files and payment slips are read in memory and not kept; only transactions you confirm are saved.</span></li>
      </ul>
      <p className="mt-3 text-xs text-slate-500">Signed in as {user?.email}.</p>
    </Card>
  );
}

function ProfileCard() {
  const { user, setUser } = useAuth();
  const form = useForm<PForm>({
    resolver: zodResolver(profileSchema),
    defaultValues: { name: user!.name, currency: user!.currency, income: user!.monthly_income ? toInputString(user!.monthly_income) : "",
      savings: user!.current_savings !== null ? toInputString(user!.current_savings) : "", target: user!.savings_target_pct?.toString() ?? "" },
  });
  const [saved, setSaved] = useState(false);
  const save = useAction((v: PForm) => api.patch<User>("/profile", {
    name: v.name, currency: v.currency, monthly_income: v.income.trim() ? toMinor(v.income) : 0,
    ...(v.savings.trim() ? { current_savings: toMinor(v.savings) } : {}), ...(v.target.trim() ? { savings_target_pct: Number(v.target) } : {}),
  }), (u) => { setUser(u); setSaved(true); });
  const e = form.formState.errors;
  return (
    <Card title="Financial profile">
      <form onSubmit={form.handleSubmit((v) => { setSaved(false); save.mutate(v); })} noValidate className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name" error={e.name?.message}><Input {...form.register("name")} /></Field>
        <Field label="Currency"><Select {...form.register("currency")}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select></Field>
        <Field label="Monthly income (expected)" error={e.income?.message} hint="Used as a fallback; analytics use your real transactions."><Input inputMode="decimal" {...form.register("income")} /></Field>
        <Field label="Current savings balance" error={e.savings?.message} hint="Used for the emergency fund, forecasts and simulations."><Input inputMode="decimal" {...form.register("savings")} /></Field>
        <Field label="Savings target (% of income)" error={e.target?.message}><Input inputMode="decimal" {...form.register("target")} /></Field>
        <div className="sm:col-span-2">
          {save.error ? <ErrorBox error={save.error} /> : null}
          {saved && <Notice tone="good">Profile saved.</Notice>}
          <Button type="submit" className="mt-2" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save profile"}</Button>
        </div>
      </form>
      <p className="mt-3 text-xs text-slate-500">Signed in as {user!.email}. Changing the currency changes how amounts are labelled; it does not convert existing transactions.</p>
    </Card>
  );
}

const pwSchema = z.object({ current: z.string().min(1, "Enter your current password"), next: z.string().min(8, "At least 8 characters").refine((v) => !/^\d+$/.test(v) && !/^[A-Za-z]+$/.test(v), "Mix letters with numbers or symbols") });
function PasswordCard() {
  const { logout } = useAuth();
  const form = useForm<z.infer<typeof pwSchema>>({ resolver: zodResolver(pwSchema), defaultValues: { current: "", next: "" } });
  const save = useAction((v: z.infer<typeof pwSchema>) => api.post("/profile/password", { current_password: v.current, new_password: v.next }), () => { void logout(); });
  const e = form.formState.errors;
  return (
    <Card title="Change password">
      <form onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Current password" error={e.current?.message}><Input type="password" autoComplete="current-password" {...form.register("current")} /></Field>
        <Field label="New password" error={e.next?.message}><Input type="password" autoComplete="new-password" {...form.register("next")} /></Field>
        <div className="sm:col-span-2">{save.error ? <ErrorBox error={save.error} /> : null}<Button type="submit" disabled={save.isPending}>{save.isPending ? "Changing…" : "Change password"}</Button>
          <span className="ml-2 text-xs text-slate-500">You will be signed out on every device.</span></div>
      </form>
    </Card>
  );
}

function CategoriesCard() {
  const cats = useCategories();
  const [name, setName] = useState("");
  const [type, setType] = useState("expense");
  const [fixed, setFixed] = useState(false);
  const [essential, setEssential] = useState(false);
  const add = useAction(() => api.post("/categories", { name, type, fixed, essential }), () => setName(""), "Category added.");
  const del = useAction((n: string) => api.del(`/categories/${encodeURIComponent(n)}`), undefined, "Category deleted.");
  return (
    <Card title="Custom categories">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <Field label="Name"><Input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Type"><Select value={type} onChange={(e) => setType(e.target.value)}><option value="expense">Expense</option><option value="income">Income</option></Select></Field>
        <label className="flex items-end gap-2 pb-2 text-sm"><input type="checkbox" checked={fixed} onChange={(e) => setFixed(e.target.checked)} /> Fixed monthly cost</label>
        <label className="flex items-end gap-2 pb-2 text-sm"><input type="checkbox" checked={essential} onChange={(e) => setEssential(e.target.checked)} /> Essential</label>
      </div>
      <div className="mt-2"><Button disabled={!name.trim() || add.isPending} onClick={() => add.mutate(undefined)}>Add category</Button></div>
      {add.error ? <div className="mt-2"><ErrorBox error={add.error} /></div> : null}
      {del.error ? <div className="mt-2"><ErrorBox error={del.error} /></div> : null}
      <ul className="mt-3 flex flex-wrap gap-2 text-sm">
        {cats.data?.categories.filter((c) => c.custom).map((c) => (
          <li key={c.name} className="flex items-center gap-1.5 rounded-full border border-slate-300 bg-surface px-3 py-1">{c.name} <span className="text-xs text-slate-500">({c.type}{c.fixed ? ", fixed" : ""}{c.essential ? ", essential" : ""})</span>
            <button aria-label={`Delete ${c.name}`} className="text-red-600" onClick={() => del.mutate(c.name)}>✕</button></li>))}
      </ul>
    </Card>
  );
}

function RulesCard() {
  const q = useGet<{ rules: { id: string; keyword: string; category: string }[] }>("/categories/rules");
  const del = useAction((id: string) => api.del(`/categories/rules/${id}`), undefined, "Rule removed.");
  return (
    <Card title="Learned categorisation rules">
      <p className="mb-2 text-sm text-slate-600">Created when you correct a transaction's category. Matching merchants are categorised this way on future imports.</p>
      {q.data?.rules.length ? <ul className="divide-y divide-slate-100 text-sm">{q.data.rules.map((r) => (
        <li key={r.id} className="flex items-center justify-between py-1.5"><span>“{r.keyword}” → <b>{r.category}</b></span><Button variant="ghost" onClick={() => del.mutate(r.id)}>Forget</Button></li>))}</ul> : <p className="text-sm text-slate-500">No learned rules yet.</p>}
      {del.error ? <ErrorBox error={del.error} /> : null}
    </Card>
  );
}
