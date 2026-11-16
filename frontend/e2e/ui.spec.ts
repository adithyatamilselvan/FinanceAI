import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// UI / responsiveness / theme / accessibility checks on REAL data (the synthetic statement) against the real backend.
// Verifies: every nav link works, no horizontal overflow at 4 viewport sizes, the mobile drawer, sidebar collapse,
// dark-mode persistence, text contrast in both themes (computed from real pixels' colours), keyboard focus handling, toasts.
const DATA = path.resolve(process.cwd(), "..", "docs", "test-data");
const STATEMENT = path.join(DATA, "sample_bank_statement.csv");
const SHOTS = path.resolve(process.cwd(), "test-results", "ui-shots");
const EMAIL = `ui-${Date.now()}@example.com`;
const PASSWORD = "Passw0rd!x";

const PAGES: { link: string; to: string; h1: string }[] = [
  { link: "Dashboard", to: "/", h1: "Dashboard" },
  { link: "Transactions", to: "/transactions", h1: "Transactions" },
  { link: "Import statement", to: "/import", h1: "Import bank statement" },
  { link: "Payment Slip Scanner", to: "/slips", h1: "Payment Slip Scanner" },
  { link: "Budgets", to: "/budgets", h1: "Budgets" },
  { link: "Savings goals", to: "/goals", h1: "Savings goals" },
  { link: "Analytics", to: "/analytics", h1: "Analytics" },
  { link: "Optimize & simulate", to: "/optimize", h1: "Optimize & simulate" },
  { link: "Reports", to: "/reports", h1: "Reports" },
  { link: "Profile & settings", to: "/settings", h1: "Profile & settings" },
];
const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 720 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "mobile", width: 390, height: 844 },
];

test.describe.configure({ mode: "serial" });
let page: Page;

test.beforeAll(async ({ browser }) => {
  page = await (await browser.newContext({ acceptDownloads: true, viewport: { width: 1440, height: 900 } })).newPage();
  fs.mkdirSync(SHOTS, { recursive: true });
});
test.afterAll(async () => { await page.context().close(); });

async function openMenuIfMobile() {
  const toggle = page.getByRole("button", { name: "Toggle menu" });
  if (await toggle.isVisible()) await toggle.click();
}
async function gotoPage(link: string) { await openMenuIfMobile(); await page.getByRole("link", { name: link, exact: true }).click(); }

/** Elements poking past the viewport, ignoring things that legitimately scroll inside their own box or sit off-canvas. */
async function overflow() {
  return page.evaluate(() => {
    const cw = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll("body *")].filter((e) => {
      const r = e.getBoundingClientRect();
      if (r.width === 0 || r.right <= cw + 1) return false;
      if (e.closest(".overflow-x-auto, .overflow-auto, [role=status]") || (e.closest("#app-sidebar") && getComputedStyle(e.closest("#app-sidebar")!).visibility === "hidden") || getComputedStyle(e).position === "fixed") return false;
      return true;
    }).slice(0, 5).map((e) => `${e.tagName.toLowerCase()}.${String((e as HTMLElement).className).slice(0, 70)} right=${Math.round(e.getBoundingClientRect().right)}`);
    return { scrollWidth: document.documentElement.scrollWidth, clientWidth: cw, offenders };
  });
}

/** WCAG contrast of every visible text node against its composited background. Returns failures only. */
async function contrastFailures() {
  await page.addStyleTag({ content: "*,*::before,*::after{transition:none!important;animation:none!important}" }); // measure settled colours, not mid-transition ones
  return page.evaluate(() => {
    const cv = document.createElement("canvas"); cv.width = cv.height = 1;
    const ctx = cv.getContext("2d", { willReadFrequently: true })!;
    const rgba = (c: string): [number, number, number, number] => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
    const over = (top: number[], bottom: number[]) => { const a = top[3]; return [top[0] * a + bottom[0] * (1 - a), top[1] * a + bottom[1] * (1 - a), top[2] * a + bottom[2] * (1 - a), 1]; };
    const lum = (c: number[]) => { const f = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
    const bgOf = (el: Element) => {
      const chain: number[][] = [];
      for (let e: Element | null = el; e; e = e.parentElement) { const b = rgba(getComputedStyle(e).backgroundColor); if (b[3] > 0) chain.push(b); if (b[3] === 1) break; }
      let base = rgba(getComputedStyle(document.body).backgroundColor); if (base[3] === 0) base = [255, 255, 255, 1];
      return chain.reverse().reduce((acc, c) => over(c, acc), base);
    };
    const fails: string[] = [];
    const seen = new Set<Element>();
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.textContent?.trim()) continue;
      const el = n.parentElement!;
      if (seen.has(el)) continue; seen.add(el);
      const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
      if (cs.visibility === "hidden" || cs.display === "none" || r.width < 2 || r.height < 2 || +cs.opacity === 0) continue;
      if (el.closest("[disabled],[aria-disabled=true],#app-sidebar:not(:focus-within)[class*=invisible]")) continue;
      const isSvg = el instanceof SVGElement;
      const fg = rgba(isSvg ? cs.fill : cs.color);
      if (fg[3] === 0) continue;
      const bg = bgOf(el);
      const f = over(fg, bg);
      const L1 = lum(f), L2 = lum(bg);
      const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const px = parseFloat(cs.fontSize); const bold = +cs.fontWeight >= 700;
      const need = px >= 24 || (px >= 18.66 && bold) ? 3 : 4.5;
      if (ratio < need) fails.push(`${ratio.toFixed(2)}<${need} "${n.textContent!.trim().slice(0, 40)}" <${el.tagName.toLowerCase()} class="${String(el.getAttribute("class") ?? "").slice(0, 60)}">`);
    }
    return fails;
  });
}

test("1 register and load real data (statement import, budget, goal, recurring)", async () => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Asha Verma");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).last().click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("No transactions yet")).toBeVisible(); // onboarding empty state
  await expect(page.getByText(/Welcome back, Asha/)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "dashboard-empty-desktop-light.png"), fullPage: true });

  await page.getByRole("link", { name: "Import statement", exact: true }).click();
  await page.getByLabel("Statement file").setInputFiles(STATEMENT);
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByText("Review before saving")).toBeVisible();
  await page.getByRole("button", { name: /^Import \d+ transactions$/ }).click();
  await expect(page.getByText(/Imported\s*\d+\s*transactions/)).toBeVisible();

  const token = await page.evaluate(() => localStorage.getItem("financeai.token"));
  const h = { Authorization: `Bearer ${token}` };
  const b = await page.request.post("/api/v1/budgets", { headers: h, data: { period: "2026-10", allocations: [{ category: "Groceries", amount: 900000 }, { category: "Food", amount: 600000 }, { category: "Housing", amount: 2200000 }] } });
  expect(b.status()).toBe(201);
  const g = await page.request.post("/api/v1/goals", { headers: h, data: { name: "New laptop", target_amount: 8000000, current_amount: 2000000, monthly_contribution: 700000, target_date: "2027-06-30" } });
  expect(g.status()).toBe(201);
  const r = await page.request.post("/api/v1/recurring/detect", { headers: h, data: {} });
  expect(r.status()).toBeLessThan(300);
});

test("2 every navigation link opens a real page (and no link points at a missing page)", async () => {
  await page.goto("/");
  const hrefs = await page.getByRole("navigation", { name: "Main" }).getByRole("link").evaluateAll((els) => els.map((e) => e.getAttribute("href")));
  expect(hrefs).toEqual(PAGES.map((p) => p.to));
  for (const p of PAGES) {
    await gotoPage(p.link);
    await expect(page).toHaveURL(new RegExp(`${p.to === "/" ? "/$" : p.to + "$"}`));
    await expect(page.getByRole("heading", { level: 1, name: p.h1 })).toBeVisible();
    await expect(page.getByRole("link", { name: p.link, exact: true })).toHaveAttribute("aria-current", "page"); // clear active state
  }
});

test("3 the dashboard shows every requested section with real figures", async () => {
  await page.goto("/");
  await page.locator("#period").fill("2026-10");
  for (const t of ["Total income", "Total expenses", "Net savings", "Savings rate", "Income vs expenses", "Spending by category", "Spending trend", "Recent transactions", "Budget usage", "Savings goals", "Upcoming recurring payments", "Important insights"])
    await expect(page.getByText(t, { exact: true }).first(), t).toBeVisible();
  await expect(page.getByText("New laptop")).toBeVisible();                       // goal from the real API
  await expect(page.getByRole("progressbar", { name: "Total budget used" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Quick actions" })).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "dashboard-desktop-light.png"), fullPage: true });
});

for (const vp of VIEWPORTS) {
  test(`4 no horizontal overflow on any page at ${vp.name} (${vp.width}x${vp.height})`, async () => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto("/");
    for (const p of PAGES) {
      await gotoPage(p.link);
      await expect(page.getByRole("heading", { level: 1, name: p.h1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const o = await overflow();
      expect(o.offenders, `${vp.name} ${p.to} elements past the viewport`).toEqual([]);
      expect(o.scrollWidth, `${vp.name} ${p.to} scrollWidth`).toBeLessThanOrEqual(o.clientWidth);
      if (["/", "/transactions", "/budgets", "/analytics", "/reports"].includes(p.to) && (vp.name === "mobile" || vp.name === "tablet"))
        await page.screenshot({ path: path.join(SHOTS, `${p.link.replace(/\W+/g, "-").toLowerCase()}-${vp.name}-light.png`), fullPage: true });
    }
  });
}

test("5 mobile: drawer opens, closes with Escape and on navigation; forms stay usable", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const sidebar = page.locator("#app-sidebar");
  await expect(sidebar).toBeHidden();                       // closed by default on phones
  await page.getByRole("button", { name: "Toggle menu" }).click();
  await expect(sidebar).toBeVisible();
  await expect(page.getByRole("button", { name: "Log out" })).toBeVisible(); // profile section reachable
  await page.keyboard.press("Escape");
  await expect(sidebar).toBeHidden();
  await page.getByRole("button", { name: "Toggle menu" }).click();
  await page.getByRole("link", { name: "Budgets", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Budgets" })).toBeVisible();
  await expect(sidebar).toBeHidden();                       // closes after choosing a page
  await page.screenshot({ path: path.join(SHOTS, "budgets-mobile-light.png"), fullPage: true });
  // a form in a bottom-sheet modal on a phone: fields reachable and inside the viewport
  await gotoPage("Transactions");
  await page.getByRole("button", { name: "Add transaction" }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toBeVisible();
  for (const l of ["Date", "Amount", "Description"]) { const f = dlg.getByLabel(l, { exact: true }); await f.scrollIntoViewIfNeeded(); const bb = (await f.boundingBox())!; expect(bb.x + bb.width, l).toBeLessThanOrEqual(390); }
  await page.screenshot({ path: path.join(SHOTS, "transaction-form-mobile-light.png") });
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
});

test("6 desktop: sidebar collapses, keeps working and remembers the choice", async () => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const aside = page.locator("#app-sidebar");
  const wide = (await aside.boundingBox())!.width;
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect.poll(async () => (await aside.boundingBox())!.width).toBeLessThan(wide - 100);
  await expect(page.getByRole("link", { name: "Transactions", exact: true })).toBeVisible(); // still reachable by name
  await page.getByRole("link", { name: "Transactions", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Transactions" })).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "transactions-collapsed-desktop-light.png"), fullPage: true });
  await page.reload();
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();   // persisted
  await page.getByRole("button", { name: "Expand sidebar" }).click();
  await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeVisible();
});

test("7 keyboard: skip link, modal focus trap and focus return", async () => {
  await page.goto("/transactions");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  const add = page.getByRole("button", { name: "Add transaction" });
  await add.focus();
  await page.keyboard.press("Enter");
  const dlg = page.getByRole("dialog");
  await expect(dlg).toBeVisible();
  for (let i = 0; i < 14; i++) { await page.keyboard.press("Tab"); expect(await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]")), `tab ${i} stays inside the dialog`).toBe(true); }
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
  await expect(add).toBeFocused();                          // focus returns to the trigger
});

test("8 toast on success, disabled state while saving, and confirm dialog for destructive actions", async () => {
  await page.goto("/transactions");
  await page.getByRole("button", { name: "Add transaction" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel("Description").fill("UI toast check");
  await dlg.getByLabel("Amount").fill("123.45");
  await dlg.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Transaction added." })).toBeVisible();
  await page.getByPlaceholder("Description").fill("UI toast check");
  const row = page.getByRole("row", { name: /UI toast check/ });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Delete" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete transaction?" });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(row).toBeVisible();                          // cancelling deletes nothing
  await row.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("row", { name: /UI toast check/ })).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "Transaction deleted." })).toBeVisible();
});

test("9 dark theme: toggle, persistence across reload, settings control", async () => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.locator("html")).not.toHaveClass(/dark/);               // light is the default
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);                   // persisted
  await expect(page.getByText("Recent transactions", { exact: true })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: path.join(SHOTS, "dashboard-desktop-dark.png"), fullPage: true });
  await gotoPage("Profile & settings");
  await expect(page.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("radio", { name: "Light" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
});

test("9b negative control: the contrast checker really flags unreadable text", async () => {
  await page.goto("/");
  await page.evaluate(() => { const p = document.createElement("p"); p.id = "bad-contrast"; p.textContent = "pale grey on white"; p.style.cssText = "color:#c8c8c8;background:#ffffff;position:relative"; document.querySelector("main")!.prepend(p); });
  const f = await contrastFailures();
  expect(f.some((x) => x.includes("pale grey on white")), "the deliberately bad pair must be reported").toBe(true);
  await page.evaluate(() => document.getElementById("bad-contrast")!.remove());
});

for (const theme of ["light", "dark"] as const) {
  test(`10 text contrast meets WCAG AA on every page (${theme})`, async () => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.evaluate((t) => { localStorage.setItem("financeai.theme", t); }, theme);
    await page.reload();
    await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
    const all: Record<string, string[]> = {};
    for (const p of PAGES) {
      await gotoPage(p.link);
      await expect(page.getByRole("heading", { level: 1, name: p.h1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      if (p.to === "/optimize") { await page.getByRole("button", { name: "Find savings opportunities" }).click(); await expect(page.getByText(/Results \(based on/)).toBeVisible(); await expect(page.getByRole("button", { name: "Find savings opportunities" })).toBeEnabled(); }
      if (p.to === "/") await page.locator("#period").fill("2026-10");
      await page.waitForLoadState("networkidle");
      const f = await contrastFailures();
      if (f.length) all[p.to] = f.slice(0, 8);
      if (theme === "dark" && ["/transactions", "/budgets", "/analytics", "/optimize", "/settings"].includes(p.to)) await page.screenshot({ path: path.join(SHOTS, `${p.link.replace(/\W+/g, "-").toLowerCase()}-desktop-dark.png`), fullPage: true });
    }
    expect(all, `contrast failures (${theme})`).toEqual({});
    await page.evaluate(() => localStorage.setItem("financeai.theme", "light"));
  });
}
