// Money helpers. The API exchanges integer minor units (paise/cents). Parsing user input uses string math only,
// never binary floating point, so 0.1 + 0.2 style drift cannot reach the backend.

export type Currency = "INR" | "USD" | "EUR" | "GBP";
export const CURRENCIES: Currency[] = ["INR", "USD", "EUR", "GBP"];
const SYMBOL: Record<string, string> = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };

const AMOUNT_RE = /^\d{1,11}(\.\d{1,2})?$/;

/** "1234.5" -> 123450. Returns null when the text is not a valid non-negative amount with at most 2 decimals. */
export function toMinor(text: string): number | null {
  const t = text.trim().replace(/,/g, "");
  if (!AMOUNT_RE.test(t)) return null;
  const [whole, frac = ""] = t.split(".");
  return Number(whole) * 100 + Number((frac + "00").slice(0, 2));
}

/** 123450 -> "1234.50" (plain, for inputs). */
export function toInputString(minor: number): string {
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** Display formatting with currency symbol and locale digit grouping (integer arithmetic for the split). */
export function formatMoney(minor: number | null | undefined, currency: string = "INR"): string {
  if (minor === null || minor === undefined) return "—";
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  const major = Math.floor(abs / 100);
  const frac = String(abs % 100).padStart(2, "0");
  const grouped = new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US").format(major);
  return `${sign}${SYMBOL[currency] ?? ""}${grouped}.${frac}`;
}

export function formatPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(1)}%`;
}

/** Short axis/label form for charts: 1234500 paise -> "₹12.3K" (en-IN uses L / Cr for lakh / crore). Display only; never used for calculations. */
export function formatCompact(minor: number | null | undefined, currency: string = "INR"): string {
  if (minor === null || minor === undefined) return "—";
  const v = minor / 100;
  const n = new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  return `${SYMBOL[currency] ?? ""}${n}`;
}
