import { useCallback, useEffect, useState } from "react";

// Theme preference is a per-browser UI setting (localStorage), not account data, so it needs no backend.
export type ThemePref = "light" | "dark" | "system";
const KEY = "financeai.theme";

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light"; // light is the default
  } catch { return "light"; }
}

function apply(p: ThemePref) {
  const dark = p === "dark" || (p === "system" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(read);
  useEffect(() => {
    apply(pref);
    if (pref !== "system" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => apply("system");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [pref]);
  const set = useCallback((p: ThemePref) => {
    try { localStorage.setItem(KEY, p); } catch { /* storage unavailable: the choice just lasts for this session */ }
    setPref(p);
    window.dispatchEvent(new Event("financeai-theme"));
  }, []);
  // keep several hook instances (sidebar toggle + settings page) in sync
  useEffect(() => {
    const sync = () => setPref(read());
    window.addEventListener("financeai-theme", sync);
    return () => window.removeEventListener("financeai-theme", sync);
  }, []);
  return { pref, set, isDark: typeof document !== "undefined" && document.documentElement.classList.contains("dark") };
}
