import { describe, expect, it } from "vitest";
import { formatMoney, formatPct, toInputString, toMinor } from "./money";

describe("toMinor", () => {
  it("parses amounts with string math", () => {
    expect(toMinor("1234.5")).toBe(123450);
    expect(toMinor("0.10")).toBe(10);
    expect(toMinor("19.99")).toBe(1999);
    expect(toMinor("1,00,000")).toBe(10000000);
    expect(toMinor("100")).toBe(10000);
  });
  it("is exact where floats are not", () => {
    expect(toMinor("0.07")).toBe(7); // 0.07 * 100 === 7.000000000000001 with floats
    expect(toMinor("1.15")).toBe(115); // 1.15 * 100 === 114.99999999999999
    expect(toMinor("8.20")).toBe(820);
  });
  it("rejects invalid input", () => {
    for (const bad of ["", "abc", "-5", "1.234", "1e3", "12345678901234", ".5", "1."]) expect(toMinor(bad)).toBeNull();
  });
});

describe("formatting", () => {
  it("formats INR with Indian grouping", () => {
    expect(formatMoney(12345678900, "INR")).toBe("₹12,34,56,789.00");
    expect(formatMoney(-5, "USD")).toBe("-$0.05");
    expect(formatMoney(null)).toBe("—");
  });
  it("round-trips input strings", () => {
    expect(toInputString(123450)).toBe("1234.50");
    expect(toMinor(toInputString(7))).toBe(7);
  });
  it("formats percentages", () => {
    expect(formatPct(61.4)).toBe("61.4%");
    expect(formatPct(null)).toBe("—");
  });
});

import { formatCompact } from "./money";
describe("formatCompact (chart axis labels)", () => {
  it("shortens large amounts and keeps the sign and symbol", () => {
    expect(formatCompact(0, "INR")).toBe("₹0");
    expect(formatCompact(150000, "INR")).toBe("₹1.5K");
    expect(formatCompact(-150000, "INR")).toBe("₹-1.5K");
    expect(formatCompact(null)).toBe("—");
  });
});
