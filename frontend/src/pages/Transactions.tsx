import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Link } from "react-router-dom";
import { IconArrowDown, IconArrowUp, IconEdit, IconPlus, IconSearch, IconSwap, IconTrash } from "../components/icons";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, Field, Input, Modal, PageHeader, Select, Spinner, TableWrap } from "../components/ui";
import { api } from "../lib/api";
import { useAction, useCategories, useCurrency, useGet } from "../lib/hooks";
import { formatMoney, toInputString, toMinor } from "../lib/money";
import type { Tx, TxType } from "../lib/types";

const schema = z.object({
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Pick a date"),
  description: z.string().trim().min(1, "Enter a description").max(200),
  amount: z.string().refine((v) => { const m = toMinor(v); return m !== null && m > 0; }, "Enter a positive amount with up to 2 decimals"),
  type: z.enum(["income", "expense", "refund", "transfer"]),
  category: z.string(),
  notes: z.string().max(500),
  apply_to_similar: z.boolean(),
});
type Form = z.infer<typeof schema>;
type Page = { items: Tx[]; total: number; page: number; page_size: number };

const today = () => new Date().toISOString().slice(0, 10);

const TYPE_META: Record<TxType, { Icon: typeof IconArrowDown; cls: string; sign: string }> = {
  income: { Icon: IconArrowDown, cls: "bg-green-50 text-green-700", sign: "+" },
  refund: { Icon: IconArrowDown, cls: "bg-green-50 text-green-700", sign: "+" },
  expense: { Icon: IconArrowUp, cls: "bg-red-50 text-red-700", sign: "-" },
  transfer: { Icon: IconSwap, cls: "bg-slate-100 text-slate-600", sign: "" },
};
const EMPTY_FILTERS = { q: "", category: "", type: "", date_from: "", date_to: "", sort: "date_desc" };
const FILTER_LABEL: Record<string, string> = { q: "Search", category: "Category", type: "Type", date_from: "From", date_to: "To", sort: "Sort" };

export default function Transactions() {
  const cur = useCurrency();
  const [page, setPage] = useState(1);
  const [f, setF] = useState(EMPTY_FILTERS);
  const [editing, setEditing] = useState<Tx | "new" | null>(null);
  const [deleting, setDeleting] = useState<Tx | null>(null);
  const list = useGet<Page>("/transactions", { ...f, page, page_size: 20 });
  const cats = useCategories();
  const del = useAction((id: string) => api.del(`/transactions/${id}`), () => setDeleting(null), "Transaction deleted.");
  const set = (k: keyof typeof f, v: string) => { setF((s) => ({ ...s, [k]: v })); setPage(1); };
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.page_size)) : 1;
  const active = (Object.keys(EMPTY_FILTERS) as (keyof typeof f)[]).filter((k) => k !== "sort" && f[k]);
  const clear = () => { setF(EMPTY_FILTERS); setPage(1); };

  return (
    <div>
      <PageHeader title="Transactions" description="Search, filter and correct everything that feeds your dashboard, budgets and reports.">
        <Button onClick={() => setEditing("new")}><IconPlus className="h-4 w-4" />Add transaction</Button>
      </PageHeader>
      <Card className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <Field label="Search"><Input type="search" placeholder="Description" value={f.q} onChange={(e) => set("q", e.target.value)} /></Field>
          <Field label="Category">
            <Select value={f.category} onChange={(e) => set("category", e.target.value)}>
              <option value="">All</option>
              {cats.data?.categories.map((c) => <option key={c.name}>{c.name}</option>)}
              <option>Transfer</option>
            </Select>
          </Field>
          <Field label="Type">
            <Select value={f.type} onChange={(e) => set("type", e.target.value)}>
              <option value="">All</option><option value="income">Income</option><option value="expense">Expense</option><option value="refund">Refund</option><option value="transfer">Transfer</option>
            </Select>
          </Field>
          <Field label="From"><Input type="date" value={f.date_from} max={f.date_to || undefined} onChange={(e) => set("date_from", e.target.value)} /></Field>
          <Field label="To"><Input type="date" value={f.date_to} min={f.date_from || undefined} onChange={(e) => set("date_to", e.target.value)} /></Field>
          <Field label="Sort">
            <Select value={f.sort} onChange={(e) => set("sort", e.target.value)}>
              <option value="date_desc">Newest first</option><option value="date_asc">Oldest first</option>
              <option value="amount_desc">Largest first</option><option value="amount_asc">Smallest first</option>
            </Select>
          </Field>
        </div>
        {active.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">Filtering by:</span>
            {active.map((k) => <Badge key={k} tone="info">{FILTER_LABEL[k]}: {f[k]}</Badge>)}
            <button className="font-medium text-blue-700 underline" onClick={clear}>Clear filters</button>
          </div>
        )}
      </Card>

      {list.isLoading ? <Spinner /> : list.error ? <ErrorBox error={list.error} retry={() => list.refetch()} /> : list.data!.total === 0 ? (
        active.length ? (
          <Empty title="No transactions found" icon={<IconSearch className="h-5 w-5" />}>Nothing matches the current filters. <button className="font-medium text-blue-700 underline" onClick={clear}>Clear filters</button> to see everything again.</Empty>
        ) : (
          <Empty title="No transactions found" icon={<IconSearch className="h-5 w-5" />}>
            Import a statement or add one manually. <Link className="font-medium text-blue-700 underline" to="/import">Import a statement</Link> · <Link className="font-medium text-blue-700 underline" to="/slips">Scan payment slips</Link>
          </Empty>
        )
      ) : (
        <>
          <TableWrap>
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="p-2.5">Date</th><th className="p-2.5">Description</th>
                  <th className="hidden p-2.5 sm:table-cell">Category</th><th className="hidden p-2.5 md:table-cell">Type</th><th className="hidden p-2.5 lg:table-cell">Payment</th>
                  <th className="p-2.5 text-right">Amount</th><th className="p-2.5"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {list.data!.items.map((t) => {
                  const meta = TYPE_META[t.type];
                  return (
                    <tr key={t.id} className="border-t border-slate-100 hover:bg-slate-50">
                      <td className="whitespace-nowrap p-2.5 text-slate-600">{t.date}</td>
                      <td className="p-2.5">
                        <div className="flex items-start gap-2.5">
                          <span aria-hidden className={`mt-0.5 hidden h-7 w-7 shrink-0 items-center justify-center rounded-full sm:flex ${meta.cls}`}><meta.Icon className="h-3.5 w-3.5" /></span>
                          <div className="min-w-0">
                            <div className="break-words font-medium">{t.description}</div>
                            {t.notes && <div className="break-words text-xs text-slate-500">{t.notes}</div>}
                            <div className="mt-0.5 text-xs text-slate-500 sm:hidden">{t.category}</div>
                          </div>
                        </div>
                      </td>
                      <td className="hidden p-2.5 sm:table-cell">{t.category}</td>
                      <td className="hidden p-2.5 capitalize md:table-cell">{t.type}</td>
                      <td className="hidden p-2.5 text-slate-600 lg:table-cell">{t.slip?.payment_method ?? (t.source === "slip" ? "Slip" : "—")}</td>
                      <td className={`num whitespace-nowrap p-2.5 text-right font-medium ${t.type === "income" || t.type === "refund" ? "text-green-700" : t.type === "transfer" ? "text-slate-600" : ""}`}>
                        {meta.sign}{formatMoney(t.amount, cur)}
                      </td>
                      <td className="whitespace-nowrap p-2.5 text-right">
                        <button aria-label="Edit" title="Edit" className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-blue-700" onClick={() => setEditing(t)}><IconEdit className="h-4 w-4" /></button>
                        <button aria-label="Delete" title="Delete" className="rounded-md p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-700" onClick={() => setDeleting(t)}><IconTrash className="h-4 w-4" /></button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>{list.data!.total} transactions</span>
            <div className="flex items-center gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <span className="text-slate-600">Page {page} of {pages}</span>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        </>
      )}

      {editing && <TxForm tx={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {deleting && (
        <ConfirmDialog title="Delete transaction?" danger confirmLabel="Delete" busyLabel="Deleting…" busy={del.isPending} error={del.error} onConfirm={() => del.mutate(deleting.id)} onCancel={() => setDeleting(null)}>
          <p>Delete “{deleting.description}” ({formatMoney(deleting.amount, cur)} on {deleting.date})? This cannot be undone.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function TxForm({ tx, onClose }: { tx: Tx | null; onClose: () => void }) {
  const cats = useCategories();
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: tx
      ? { date: tx.date, description: tx.description, amount: toInputString(tx.amount), type: tx.type, category: tx.category, notes: tx.notes ?? "", apply_to_similar: false }
      : { date: today(), description: "", amount: "", type: "expense", category: "", notes: "", apply_to_similar: false },
  });
  const type = form.watch("type") as TxType;
  const options = (cats.data?.categories ?? []).filter((c) => (type === "income" ? c.type === "income" : c.type === "expense"));
  const save = useAction(async (v: Form) => {
    const body: Record<string, unknown> = { date: v.date, description: v.description, amount: toMinor(v.amount), type: v.type, notes: v.notes || null };
    if (v.type !== "transfer" && v.category) body.category = v.category;
    if (tx) return api.patch<Tx>(`/transactions/${tx.id}`, { ...body, apply_to_similar: v.apply_to_similar });
    return api.post<Tx>("/transactions", body);
  }, onClose, tx ? "Transaction updated." : "Transaction added.");
  const e = form.formState.errors;
  return (
    <Modal title={tx ? "Edit transaction" : "Add transaction"} onClose={onClose}>
      <form onSubmit={form.handleSubmit((v) => save.mutate(v))} className="space-y-3" noValidate>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Date" error={e.date?.message}><Input type="date" {...form.register("date")} /></Field>
          <Field label="Amount" error={e.amount?.message}><Input inputMode="decimal" placeholder="0.00" {...form.register("amount")} /></Field>
        </div>
        <Field label="Description" error={e.description?.message}><Input {...form.register("description")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Type">
            <Select {...form.register("type", { onChange: () => form.setValue("category", "") })}>
              <option value="expense">Expense</option><option value="income">Income</option><option value="refund">Refund (reduces spending)</option><option value="transfer">Transfer between own accounts</option>
            </Select>
          </Field>
          <Field label="Category" hint={type === "transfer" ? "Transfers are excluded from income and spending." : "Leave on Automatic to categorise by keywords."}>
            <Select disabled={type === "transfer"} {...form.register("category")}>
              <option value="">Automatic</option>
              {options.map((c) => <option key={c.name}>{c.name}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Notes" error={e.notes?.message}><Input {...form.register("notes")} /></Field>
        {tx && <label className="flex items-center gap-2 text-sm"><input type="checkbox" {...form.register("apply_to_similar")} /> Also re-categorise similar transactions from the same merchant</label>}
        {tx && <p className="text-xs text-slate-500">Changing the category teaches the app: future imports from the same merchant use it.</p>}
        {save.error ? <ErrorBox error={save.error} /> : null}
        <div className="modal-footer flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
        </div>
      </form>
    </Modal>
  );
}
