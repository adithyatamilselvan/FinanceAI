import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import { IconChart, IconChevronLeft, IconChevronRight, IconClose, IconDashboard, IconFile, IconList, IconLogo, IconLogout, IconMenu, IconMoon, IconScan, IconSliders, IconSun, IconTarget, IconUpload, IconUser, IconWallet } from "./icons";
import { cx } from "./ui";

// Only pages that exist. Every `to` below has a route in App.tsx.
const NAV = [
  { to: "/", label: "Dashboard", end: true, Icon: IconDashboard },
  { to: "/transactions", label: "Transactions", Icon: IconList },
  { to: "/import", label: "Import statement", Icon: IconUpload },
  { to: "/slips", label: "Payment Slip Scanner", Icon: IconScan },
  { to: "/budgets", label: "Budgets", Icon: IconWallet },
  { to: "/goals", label: "Savings goals", Icon: IconTarget },
  { to: "/analytics", label: "Analytics", Icon: IconChart },
  { to: "/optimize", label: "Optimize & simulate", Icon: IconSliders },
  { to: "/reports", label: "Reports", Icon: IconFile },
  { to: "/settings", label: "Profile & settings", Icon: IconUser },
];
const COLLAPSE_KEY = "financeai.sidebar.collapsed";

function initials(name?: string) {
  const p = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return ((p[0]?.[0] ?? "?") + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase();
}

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { set, isDark } = useTheme();
  const [open, setOpen] = useState(false); // mobile drawer
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; } });
  const nav = useNavigate();
  const loc = useLocation();

  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => { try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch { /* ignore */ } }, [collapsed]);
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open]);

  const doLogout = async () => { await logout(); nav("/login"); };
  const label = (text: string) => <span className={cx("truncate", collapsed && "md:sr-only")}>{text}</span>;

  return (
    <div className="min-h-screen md:flex">
      <a href="#main" className="sr-only z-[70] rounded-md bg-surface px-3 py-2 text-sm font-medium text-blue-700 shadow-md focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to main content</a>

      {/* mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-slate-200 bg-surface/95 px-4 py-2.5 backdrop-blur md:hidden">
        <span className="flex items-center gap-2 font-semibold"><IconLogo /> FinanceAI</span>
        <button aria-label="Toggle menu" aria-expanded={open} aria-controls="app-sidebar" className="rounded-md border border-slate-300 p-2 text-slate-700 hover:bg-slate-100" onClick={() => setOpen((o) => !o)}>
          {open ? <IconClose /> : <IconMenu />}
        </button>
      </header>
      {open && <div className="fixed inset-0 z-30 bg-slate-900/50 md:hidden" aria-hidden onClick={() => setOpen(false)} />}

      <aside
        id="app-sidebar"
        className={cx(
          "fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col border-r border-slate-200 bg-surface transition-transform duration-200",
          "md:sticky md:top-0 md:h-screen md:max-w-none md:translate-x-0 md:transition-[width]",
          collapsed ? "md:w-[4.75rem]" : "md:w-64",
          open ? "translate-x-0 shadow-xl" : "max-md:invisible -translate-x-full",
        )}
      >
        <div className={cx("flex items-center gap-2.5 px-4 py-4", collapsed && "md:justify-center md:px-2")}>
          <IconLogo />
          <span className={cx("text-lg font-semibold tracking-tight", collapsed && "md:sr-only")}>FinanceAI</span>
        </div>

        <nav aria-label="Main" className="flex-1 space-y-0.5 overflow-y-auto px-2.5 pb-2">
          {NAV.map(({ to, label: text, end, Icon }) => (
            <NavLink
              key={to} to={to} end={end} title={collapsed ? text : undefined} onClick={() => setOpen(false)}
              className={({ isActive }) => cx(
                "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                collapsed && "md:justify-center md:px-0",
                isActive ? "bg-blue-50 font-semibold text-blue-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
              )}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" />
              {label(text)}
            </NavLink>
          ))}
        </nav>

        <div className="space-y-2 border-t border-slate-200 p-3">
          <div className={cx("flex items-center gap-2.5", collapsed && "md:justify-center")}>
            <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white">{initials(user?.name)}</span>
            <div className={cx("min-w-0 flex-1", collapsed && "md:hidden")}>
              <div className="truncate text-sm font-medium">{user?.name}</div>
              <div className="truncate text-xs text-slate-500">{user?.email}</div>
            </div>
          </div>
          <div className={cx("flex items-center gap-1.5", collapsed && "md:flex-col")}>
            <button
              onClick={doLogout} aria-label="Log out" title="Log out"
              className={cx("flex flex-1 items-center justify-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100", collapsed && "md:w-full md:flex-none md:px-0")}
            >
              <IconLogout className="h-4 w-4" />
              <span className={cx(collapsed && "md:sr-only")}>Log out</span>
            </button>
            <button
              onClick={() => set(isDark ? "light" : "dark")} aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"} title={isDark ? "Light theme" : "Dark theme"}
              className="rounded-lg border border-slate-300 p-2 text-slate-700 hover:bg-slate-100"
            >
              {isDark ? <IconSun className="h-4 w-4" /> : <IconMoon className="h-4 w-4" />}
            </button>
            <button
              onClick={() => setCollapsed((c) => !c)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-pressed={collapsed} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="hidden rounded-lg border border-slate-300 p-2 text-slate-700 hover:bg-slate-100 md:block"
            >
              {collapsed ? <IconChevronRight className="h-4 w-4" /> : <IconChevronLeft className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </aside>

      <main id="main" tabIndex={-1} className="min-w-0 flex-1 p-4 outline-none sm:p-6 lg:p-8">
        <div className="mx-auto w-full max-w-[1400px]">{children}</div>
      </main>
    </div>
  );
}
