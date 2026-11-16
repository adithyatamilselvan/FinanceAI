import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { ApiError } from "../lib/api";
import { IconAlert, IconCheck, IconClose, IconInfo } from "./icons";

export function cx(...c: (string | false | null | undefined)[]) { return c.filter(Boolean).join(" "); }

/* ----------------------------------------------------------------------------------------------- buttons & inputs */
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; size?: "sm" | "md" };

export function Button({ variant = "primary", size = "md", className, ...p }: ButtonProps) {
  const styles = {
    primary: "bg-brand text-white shadow-xs hover:bg-brand-hover disabled:bg-brand/40",
    secondary: "border border-slate-300 bg-surface text-slate-800 shadow-xs hover:bg-slate-100 disabled:opacity-50",
    danger: "bg-danger text-white shadow-xs hover:bg-danger-hover disabled:bg-danger/40",
    ghost: "text-blue-700 hover:bg-blue-50 disabled:opacity-50",
  }[variant];
  const sizes = { sm: "px-2.5 py-1 text-xs", md: "min-h-9 px-3.5 py-1.5 text-sm" }[size];
  // `type="button"` by default so a button inside a form never submits it by accident
  return <button type="button" {...p} className={cx("inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed", sizes, styles, className)} />;
}

const inputCls = "min-h-9 w-full rounded-md border border-slate-300 bg-surface px-2.5 py-1.5 text-sm text-slate-900 shadow-xs placeholder:text-slate-400 transition-colors hover:border-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/25 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";
export const Input = (p: InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={cx(inputCls, p.className)} />;
export const Select = (p: SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={cx(inputCls, p.className)} />;

export function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      {error && <span role="alert" className="mt-1 block text-xs font-medium text-red-700">{error}</span>}
    </label>
  );
}

/* ----------------------------------------------------------------------------------------------- surfaces */
export function Card({ title, actions, children, className, description }: { title?: string; actions?: ReactNode; children: ReactNode; className?: string; description?: string }) {
  return (
    <section className={cx("min-w-0 rounded-xl border border-slate-200 bg-surface p-4 shadow-sm sm:p-5", className)}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

/** KPI tile. Green = good, red = bad, neutral otherwise. The value stays in its own element (tests and screen readers read it as one figure). */
export function Stat({ label, value, sub, tone, icon }: { label: string; value: string; sub?: string; tone?: "good" | "bad"; icon?: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-surface p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
        {icon && <span className={cx("flex h-8 w-8 items-center justify-center rounded-lg", tone === "good" ? "bg-green-50 text-green-700" : tone === "bad" ? "bg-red-50 text-red-700" : "bg-blue-50 text-blue-700")}>{icon}</span>}
      </div>
      <div className={cx("num mt-2 break-words text-2xl font-semibold tracking-tight", tone === "good" && "text-green-700", tone === "bad" && "text-red-700")}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function Badge({ tone = "neutral", children, className }: { tone?: "neutral" | "good" | "bad" | "warn" | "info"; children: ReactNode; className?: string }) {
  const t = {
    neutral: "bg-slate-100 text-slate-700 ring-slate-200", good: "bg-green-50 text-green-700 ring-green-200", bad: "bg-red-50 text-red-700 ring-red-200",
    warn: "bg-amber-50 text-amber-700 ring-amber-200", info: "bg-blue-50 text-blue-700 ring-blue-200",
  }[tone];
  return <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", t, className)}>{children}</span>;
}

/* ----------------------------------------------------------------------------------------------- loading / error / empty */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("fa-skeleton", className ?? "h-4 w-full")} />;
}

/** Page-level loading placeholder. Keeps role="status" so assistive tech announces it. */
export function Spinner({ label = "Loading…", compact }: { label?: string; compact?: boolean }) {
  if (compact) return <div role="status" aria-live="polite" className="space-y-2 py-2"><span className="sr-only">{label}</span><Skeleton className="h-4 w-1/2" /><Skeleton className="h-4 w-2/3" /></div>;
  return (
    <div role="status" aria-live="polite" className="space-y-4 py-4">
      <span className="sr-only">{label}</span>
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      <Skeleton className="h-64" />
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof ApiError || error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <IconAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">{msg} {retry && <button className="ml-1 font-medium underline" onClick={retry}>Retry</button>}</div>
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-surface/60 p-8 text-center">
      {icon && <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-blue-50 text-blue-700">{icon}</div>}
      <div className="font-medium text-slate-800">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-slate-500">{children}</div>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "good"; children: ReactNode }) {
  const t = { info: "border-blue-200 bg-blue-50 text-blue-900", warn: "border-amber-200 bg-amber-50 text-amber-900", good: "border-green-200 bg-green-50 text-green-900" }[tone];
  const I = tone === "good" ? IconCheck : tone === "warn" ? IconAlert : IconInfo;
  return (
    <div className={cx("flex items-start gap-2 rounded-lg border p-3 text-sm", t)}>
      <I className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/* ----------------------------------------------------------------------------------------------- dialogs */
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Accessible modal: Escape closes, Tab is trapped inside, focus returns to the trigger afterwards, background does not scroll. */
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    // Lock the page behind the modal. Both <html> and <body> are locked (browsers differ on which one owns the page scroll),
    // and the scrollbar's width is kept as padding so the layout does not jump. Scrolling INSIDE the modal is unaffected.
    const html = document.documentElement, bodyEl = document.body;
    const saved = { h: html.style.overflow, b: bodyEl.style.overflow, pr: bodyEl.style.paddingRight };
    const gutter = window.innerWidth - html.clientWidth;
    html.style.overflow = "hidden";
    bodyEl.style.overflow = "hidden";
    if (gutter > 0) bodyEl.style.paddingRight = `${gutter}px`;
    const first = ref.current?.querySelector<HTMLElement>('input,select,textarea,[data-autofocus]') ?? ref.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") { closeRef.current(); return; }
      if (e.key !== "Tab" || !ref.current) return;
      const els = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!els.length) return;
      const a = els[0], z = els[els.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    window.addEventListener("keydown", h);
    return () => { window.removeEventListener("keydown", h); html.style.overflow = saved.h; bodyEl.style.overflow = saved.b; bodyEl.style.paddingRight = saved.pr; prev?.focus?.(); };
  }, []);
  return (
    // The overlay is fixed to the viewport and does NOT scroll; the panel inside it scrolls its own body.
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-0 sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className="modal-panel fa-fade-in flex w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-surface shadow-xl sm:rounded-2xl">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 px-5 py-3.5">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button aria-label="Close" className="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-800" onClick={onClose}><IconClose className="h-5 w-5" /></button>
        </div>
        {/* Focusable so Page Up/Down, arrows, Home and End scroll it from the keyboard; overscroll-contain stops scroll chaining to the page. */}
        <div data-testid="modal-scroll" tabIndex={0} role="region" aria-label={`${title} form`} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5 pt-4 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/40">
          {children}
        </div>
      </div>
    </div>
  );
}

/** Confirmation for destructive or irreversible actions. */
export function ConfirmDialog({ title, children, confirmLabel = "Confirm", busyLabel, busy, danger, error, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel?: string; busyLabel?: string; busy?: boolean; danger?: boolean; error?: unknown; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-sm text-slate-700">{children}</div>
      {error ? <div className="mt-3"><ErrorBox error={error} /></div> : null}
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} disabled={busy} onClick={onConfirm} data-autofocus>{busy ? (busyLabel ?? "Working…") : confirmLabel}</Button>
      </div>
    </Modal>
  );
}

/* ----------------------------------------------------------------------------------------------- toasts */
type ToastKind = "success" | "error" | "info";
type ToastItem = { id: number; kind: ToastKind; text: string };
type ToastApi = { show: (kind: ToastKind, text: string) => void; success: (t: string) => void; error: (t: string) => void; info: (t: string) => void };
const ToastCtx = createContext<ToastApi | null>(null);
const NOOP: ToastApi = { show: () => {}, success: () => {}, error: () => {}, info: () => {} };

/** Safe outside a provider (unit tests): toasts are simply dropped. */
export function useToast(): ToastApi { return useContext(ToastCtx) ?? NOOP; }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => setItems((s) => s.filter((t) => t.id !== id)), []);
  const show = useCallback((kind: ToastKind, text: string) => {
    const id = ++seq.current;
    setItems((s) => [...s.slice(-3), { id, kind, text }]);
    window.setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4500);
  }, [dismiss]);
  const api = useMemo<ToastApi>(() => ({ show, success: (t) => show("success", t), error: (t) => show("error", t), info: (t) => show("info", t) }), [show]);
  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end" aria-live="polite" aria-atomic="false">
        {items.map((t) => (
          <div key={t.id} role={t.kind === "error" ? "alert" : "status"} className={cx("fa-fade-in pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-lg border bg-surface p-3 text-sm shadow-md",
            t.kind === "success" ? "border-green-200" : t.kind === "error" ? "border-red-200" : "border-slate-200")}>
            <span className={cx("mt-0.5", t.kind === "success" ? "text-green-700" : t.kind === "error" ? "text-red-700" : "text-blue-700")}>
              {t.kind === "success" ? <IconCheck className="h-4 w-4" /> : t.kind === "error" ? <IconAlert className="h-4 w-4" /> : <IconInfo className="h-4 w-4" />}
            </span>
            <span className="min-w-0 flex-1 text-slate-800">{t.text}</span>
            <button aria-label="Dismiss notification" className="text-slate-400 hover:text-slate-700" onClick={() => dismiss(t.id)}><IconClose className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ----------------------------------------------------------------------------------------------- misc */
export function ProgressBar({ pct, over, tone, label }: { pct: number | null; over?: boolean; tone?: "good" | "warn" | "bad" | "info"; label?: string }) {
  const w = Math.max(0, Math.min(100, pct ?? 0));
  const t = tone ?? (over ? "bad" : "info");
  const color = { good: "bg-green-600", warn: "bg-amber-500", bad: "bg-red-500", info: "bg-blue-600" }[t];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-label={label} aria-valuenow={Math.round(w)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cx("h-full rounded-full transition-[width] duration-300", color)} style={{ width: `${w}%` }} />
    </div>
  );
}

export function PageHeader({ title, children, description }: { title: string; children?: ReactNode; description?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-slate-500">{description}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

/** Responsive table wrapper: scrolls inside its own box so the page itself never scrolls sideways. */
export function TableWrap({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("overflow-x-auto rounded-xl border border-slate-200 bg-surface shadow-sm", className)}>{children}</div>;
}

export function useStableId(prefix: string) { return `${prefix}-${useId()}`; }

/** Chart palette: distinguishable for common colour-vision deficiencies, readable on light and dark surfaces. */
export const CHART_COLORS = ["#3b6fe8", "#f08c2e", "#1fa968", "#8b5cf6", "#e5484d", "#14a3b8", "#d4a017", "#7b8aa3", "#d6409f", "#0f9d8a"];
export const POSITIVE = "#1fa968";
export const NEGATIVE = "#e5484d";
