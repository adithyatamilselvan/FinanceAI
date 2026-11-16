import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Real-world journey on SYNTHETIC data (docs/test-data). Expected figures come from expected_figures.json, which was
// computed independently of the application by generate_synthetic_data.py (Decimal arithmetic, straight from ground truth).
const DATA = path.resolve(process.cwd(), "..", "docs", "test-data");
const STATEMENT = path.join(DATA, "sample_bank_statement.csv");
const EXP = JSON.parse(fs.readFileSync(path.join(DATA, "expected_figures.json"), "utf-8")) as {
  row_count: number;
  type_counts: Record<string, number>;
  months: Record<string, { income: number; net_expense: number; net_cash_flow: number; savings_pct: number; categories: Record<string, number>; rows: number }>;
};
const EMAIL = `journey-${Date.now()}@example.com`;
const PASSWORD = "Passw0rd!x";

/** Same display convention as the app (en-IN grouping, 2 decimals, sign first). */
const inr = (p: number) => `${p < 0 ? "-" : ""}₹${new Intl.NumberFormat("en-IN").format(Math.floor(Math.abs(p) / 100))}.${String(Math.abs(p) % 100).padStart(2, "0")}`;
const pct1 = (v: number) => `${v.toFixed(1)}%`;

test.describe.configure({ mode: "serial" });
let page: Page;

test.beforeAll(async ({ browser }) => {
  page = await (await browser.newContext({ acceptDownloads: true })).newPage();
});
test.afterAll(async () => { await page.context().close(); });

async function goto(name: string) { await page.getByRole("link", { name, exact: true }).click(); }
async function setMonth(label: string | RegExp, value: string) { await page.getByLabel(label).fill(value); }

test("1 register, profile", async () => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Journey Tester");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).last().click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("No transactions yet")).toBeVisible();

  await goto("Profile & settings");
  await page.getByLabel("Monthly income (expected)").fill("85000");
  await page.getByLabel("Current savings balance").fill("120000");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();
});

test("2 empty states and validation before any data exists", async () => {
  await goto("Transactions");
  await expect(page.getByText("No transactions found")).toBeVisible();
  await goto("Budgets");
  await expect(page.getByText(/No budget for/)).toBeVisible();
  await goto("Savings goals");
  await expect(page.getByText("No goals yet")).toBeVisible();
  await page.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByRole("alert")).toContainText("No completed months");
  await goto("Analytics");
  await expect(page.getByText("No spending data yet")).toBeVisible();
  await expect(page.getByText("Nothing unusual found")).toBeVisible();
  await expect(page.getByText(/Forecasting needs at least 3 completed months/)).toBeVisible();
  await goto("Optimize & simulate");
  await page.getByRole("button", { name: "Find savings opportunities" }).click();
  await expect(page.getByRole("alert")).toContainText("at least one completed month");
  await page.getByRole("tab", { name: "What-if simulation" }).click();
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByRole("alert")).toContainText("No income/expense history");
  await goto("Reports");
  await expect(page.getByText("No transactions were recorded")).toBeVisible();
});

test("3 import the synthetic statement; re-import is all duplicates", async () => {
  await goto("Import statement");
  await page.getByLabel("Statement file").setInputFiles(STATEMENT);
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByText("Review before saving")).toBeVisible();
  await expect(page.getByText("new transactions")).toContainText(String(EXP.row_count));
  await expect(page.getByText("duplicates (will be skipped)")).toContainText("0");
  await expect(page.getByText("rows with problems (not imported)")).toContainText("0");
  // transfers are recognised in the preview table
  await expect(page.getByRole("row", { name: /SELF TRANSFER TO SAVINGS/ }).first()).toContainText("transfer");
  await expect(page.getByRole("row", { name: /REFUND-AMAZON/ })).toContainText("refund");
  await page.getByRole("button", { name: `Import ${EXP.row_count} transactions` }).click();
  await expect(page.getByText(new RegExp(`Imported\\s*${EXP.row_count}\\s*transactions`))).toBeVisible();

  await page.getByLabel("Statement file").setInputFiles(STATEMENT);
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByText("duplicates (will be skipped)")).toContainText(String(EXP.row_count));
  await expect(page.getByText("exact file content was imported before")).toBeVisible();
  await expect(page.getByRole("button", { name: "Import 0 transactions" })).toBeDisabled();
  await page.getByRole("button", { name: "Discard" }).click();
});

test("4 review transactions: counts by type match the ground truth", async () => {
  await goto("Transactions");
  await expect(page.getByText(`${EXP.row_count} transactions`, { exact: true })).toBeVisible();
  for (const [type, n] of Object.entries(EXP.type_counts)) {
    await page.getByLabel("Type").selectOption(type);
    await expect(page.getByText(`${n} transactions`, { exact: true })).toBeVisible();
  }
  await page.getByLabel("Type").selectOption("");
  await page.getByPlaceholder("Description").fill("friend amit");
  await expect(page.getByRole("row", { name: /FRIEND AMIT/ })).toContainText("income"); // not mistaken for a transfer
  await page.getByPlaceholder("Description").fill("");
});

for (const period of ["2026-10", "2026-09", "2026-08"]) {
  test(`5 dashboard ${period} shows the independently computed figures`, async () => {
    await goto("Dashboard");
    await page.locator("#period").fill(period);
    const m = EXP.months[period];
    await expect(page.getByText(inr(m.income), { exact: true }).first()).toBeVisible();
    await expect(page.getByText(inr(m.net_expense), { exact: true }).first()).toBeVisible();
    await expect(page.getByText(inr(m.net_cash_flow), { exact: true }).first()).toBeVisible();
    await expect(page.getByText(pct1(m.savings_pct), { exact: true }).first()).toBeVisible();
    for (const [cat, amount] of Object.entries(m.categories)) {
      await expect(page.getByRole("listitem").filter({ hasText: cat }).filter({ hasText: inr(amount) }).first(), `${period} ${cat}`).toBeVisible();
    }
  });
}

test("6 edit a transaction; dashboard recalculates from the stored data", async () => {
  await goto("Transactions");
  await page.getByPlaceholder("Description").fill("zomato bulk");
  await page.getByRole("row").nth(1).getByRole("button", { name: "Edit" }).click();
  await page.getByRole("dialog").getByLabel("Category").selectOption("Entertainment");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Entertainment", exact: true })).toBeVisible();
  await goto("Dashboard");
  await page.locator("#period").fill("2026-10");
  const oct = EXP.months["2026-10"].categories;
  await expect(page.getByRole("listitem").filter({ hasText: "Food" }).filter({ hasText: inr(oct.Food - 680000) }).first()).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Entertainment" }).filter({ hasText: inr(oct.Entertainment + 680000) }).first()).toBeVisible();
  await expect(page.getByText(inr(EXP.months["2026-10"].net_expense), { exact: true }).first()).toBeVisible(); // total unchanged by recategorising
  // restore so later assertions use the original figures
  await goto("Transactions");
  await page.getByPlaceholder("Description").fill("zomato bulk");
  await page.getByRole("row").nth(1).getByRole("button", { name: "Edit" }).click();
  await page.getByRole("dialog").getByLabel("Category").selectOption("Food");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Food", exact: true })).toBeVisible();
});

test("7 invalid input is rejected with clear messages", async () => {
  await goto("Transactions");
  await page.getByPlaceholder("Description").fill("");
  await page.getByRole("button", { name: "Add transaction" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Enter a description")).toBeVisible();
  await expect(page.getByText("Enter a positive amount with up to 2 decimals")).toBeVisible();
  await page.getByRole("dialog").getByLabel("Description").fill("Test");
  for (const bad of ["abc", "-5", "0", "1.234", "1e5"]) {
    await page.getByRole("dialog").getByLabel("Amount").fill(bad);
    await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Enter a positive amount with up to 2 decimals")).toBeVisible();
  }
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await goto("Savings goals");
  await page.getByRole("button", { name: "New goal" }).click();
  await page.getByRole("button", { name: "Save goal" }).click();
  await expect(page.getByText("Enter a name")).toBeVisible();
  await expect(page.getByText("Target: enter a valid amount")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await goto("Budgets");
  await setMonth("Budget month", "2026-10");
  await page.getByRole("button", { name: "Create budget" }).click();
  await page.getByRole("button", { name: "Save budget" }).click();
  await expect(page.getByText("Enter at least one category budget")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await goto("Import statement");
  await page.getByLabel("Statement file").setInputFiles({ name: "evil.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ") });
  await expect(page.getByRole("alert")).toContainText("Only .csv and .xlsx");
  await page.getByLabel("Statement file").setInputFiles({ name: "garbage.csv", mimeType: "text/csv", buffer: Buffer.from("Date,Description,Debit,Credit\n2026-01-01,Shop,abc,\n") });
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByRole("alert")).toContainText("No valid transactions found");
});

test("8 budget for October uses real spending", async () => {
  await goto("Budgets");
  await setMonth("Budget month", "2026-10");
  await page.getByRole("button", { name: "Create budget" }).click();
  const alloc: Record<string, number> = { Groceries: 9000, Food: 6000, Healthcare: 2000, Housing: 22000 };
  for (const [cat, rupees] of Object.entries(alloc)) await page.getByRole("dialog").getByLabel(cat, { exact: true }).fill(String(rupees));
  await page.getByRole("button", { name: "Save budget" }).click();
  const oct = EXP.months["2026-10"].categories;
  for (const [cat, rupees] of Object.entries(alloc)) {
    const row = page.getByRole("row").filter({ hasText: cat }).first();
    await expect(row).toContainText(inr(rupees * 100));
    await expect(row).toContainText(inr(oct[cat]));
    await expect(row).toContainText(inr(rupees * 100 - oct[cat]));
  }
  await expect(page.getByText(/Over budget in:/)).toContainText("Healthcare");
  await expect(page.getByText(/Over budget in:/)).toContainText("Food");
});

test("9 savings goal progress and emergency fund", async () => {
  await goto("Savings goals");
  await page.getByRole("button", { name: "New goal" }).click();
  await page.getByLabel("Goal name").fill("New laptop");
  await page.getByLabel("Target amount").fill("80000");
  await page.getByLabel("Saved so far").fill("19999.99");
  await page.getByLabel(/Planned monthly saving/).fill("7000");
  await page.getByRole("button", { name: "Save goal" }).click();
  await expect(page.getByText("25.0%")).toBeVisible(); // 1,999,999 / 8,000,000 = 24.99999 -> 25.0
  await expect(page.getByText(inr(6000001), { exact: true })).toBeVisible(); // remaining
  await page.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByText(/Target:/)).toBeVisible();
  await expect(page.getByText("Gap:")).toBeVisible();
});

test("10 analytics: trends, anomalies, recurring, forecast", async () => {
  await goto("Analytics");
  await expect(page.getByText(/Based on\s*3\s*completed months \(2026-08 to 2026-10\)/)).toBeVisible();
  await expect(page.getByText("Confidence: low".replace("Confidence: ", ""), { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/Rs 64,900.00 is .* the typical Shopping/)).toBeVisible();
  await expect(page.getByText(/Rs 6,800.00 is .* the typical Food/)).toBeVisible();
  await expect(page.getByText(/Healthcare spending of Rs 10,520.00/)).toBeVisible();
  await expect(page.getByText(/New spending category in 2026-10: Travel Rs 18,500.00/)).toBeVisible();
  await expect(page.getByText("Possible duplicate payments")).toBeVisible();
  await expect(page.getByText(/not necessarily wrong or fraudulent/)).toBeVisible();

  await page.getByRole("button", { name: "Detect from my transactions" }).click();
  await expect(page.getByRole("cell", { name: /NETFLIX/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /LANDLORD/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /BESCOM/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /SWIGGY|ZOMATO|APPLE|MAKEMYTRIP|CREDIT CARD/i })).toHaveCount(0);
  await expect(page.getByText(inr(3425600), { exact: true })).toBeVisible(); // independent sum of bill/subscription medians
  await page.getByRole("row", { name: /NETFLIX/ }).getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("row", { name: /NETFLIX/ })).toContainText("confirmed");
  await page.getByRole("row", { name: /SPOTIFY/ }).getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByRole("cell", { name: /SPOTIFY/ })).toHaveCount(0);
  await expect(page.getByText(inr(3425600 - 11900), { exact: true })).toBeVisible(); // dismissed item leaves the total
});

test("11 optimisation numbers shown = API numbers; accept does not touch the budget", async () => {
  await goto("Optimize & simulate");
  const resp = page.waitForResponse((r) => r.url().includes("/optimization/run") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Find savings opportunities" }).click();
  const rec = await (await resp).json();
  for (const k of ["conservative", "balanced", "aggressive"]) {
    await expect(page.getByRole("button", { name: new RegExp(k) }).first()).toContainText(inr(rec.scenarios[k].achieved_savings));
    expect(rec.scenarios[k].valid).toBe(true);
  }
  await expect(page.getByText("Fixed (not changed)")).toContainText("Housing");
  await page.getByRole("button", { name: "Accept balanced plan" }).click();
  await expect(page.getByText(/Your budget was not changed/)).toBeVisible();
  await goto("Budgets");
  await setMonth("Budget month", "2026-11");
  await expect(page.getByText("No budget for 2026-11")).toBeVisible();
});

test("12 what-if simulation and EMI", async () => {
  await goto("Optimize & simulate");
  await page.getByRole("tab", { name: "What-if simulation" }).click();
  await page.getByLabel("Save extra every month").fill("3000");
  await page.getByLabel("Horizon (months)").fill("12");
  const resp = page.waitForResponse((r) => r.url().includes("/simulations/run"));
  await page.getByLabel("Include a loan / EMI calculation").check();
  await page.getByLabel("Principal").fill("1000000");
  await page.getByLabel("Annual rate (%)").fill("10");
  await page.getByLabel("Tenure (months)").fill("120");
  await page.getByRole("button", { name: "Run simulation" }).click();
  const sim = await (await resp).json();
  expect(sim.loan.emi).toBe(1321507);
  await expect(page.getByText(/EMI ₹13,215.07/)).toBeVisible();
  await expect(page.getByRole("row", { name: /Monthly savings/ })).toContainText(inr(sim.scenario.monthly_savings));
  await expect(page.getByRole("row", { name: /Savings after 12 months/ })).toContainText(inr(sim.scenario.final_balance));
  expect(sim.difference.monthly_savings).toBe(300000);
});

test("13 reports and downloads contain the real data", async () => {
  await goto("Reports");
  await expect(page.getByText(/Monthly summary — 2026-10/)).toBeVisible();
  await expect(page.getByText(inr(EXP.months["2026-10"].net_cash_flow), { exact: true })).toBeVisible();
  const pdf = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download PDF" }).click();
  const pdfFile = await pdf;
  expect(pdfFile.suggestedFilename()).toBe("report-2026-10.pdf");
  expect(fs.readFileSync((await pdfFile.path())!).subarray(0, 4).toString()).toBe("%PDF");
  const csv = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  const lines = fs.readFileSync((await (await csv).path())!, "utf-8").trim().split(/\r?\n/);
  expect(lines.length).toBe(EXP.row_count + 1);
  const xlsx = page.waitForEvent("download");
  await page.getByRole("button", { name: "Excel", exact: true }).click();
  expect((await xlsx).suggestedFilename()).toBe("transactions.xlsx");
});

test("14 refresh, logout and login again: data persists", async () => {
  await goto("Dashboard");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText(inr(EXP.months["2026-10"].net_expense), { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/transactions");
  await expect(page).toHaveURL(/\/login$/); // token was invalidated server-side
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("wrong-Passw0rd");
  await page.getByRole("button", { name: "Log in" }).last().click();
  await expect(page.getByRole("alert")).toContainText("Incorrect email or password");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).last().click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText(inr(EXP.months["2026-10"].income), { exact: true }).first()).toBeVisible();
  await goto("Transactions");
  await expect(page.getByText(`${EXP.row_count + 0} transactions`, { exact: true })).toBeVisible();
  await goto("Budgets");
  await setMonth("Budget month", "2026-10");
  await expect(page.getByRole("row").filter({ hasText: "Housing" }).first()).toContainText(inr(2200000));
  await goto("Savings goals");
  await expect(page.getByText("New laptop")).toBeVisible();
  await goto("Profile & settings");
  await expect(page.getByLabel("Monthly income (expected)")).toHaveValue("85000.00");
});

test("15 API failures are shown, not hidden", async () => {
  await goto("Transactions");
  // server error
  await page.route("**/api/v1/analytics/dashboard*", (r) => r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Internal server error" }) }));
  await page.goto("/"); // full load: bypass the in-memory query cache
  await expect(page.getByRole("alert")).toContainText("Internal server error", { timeout: 15_000 });
  await page.unroute("**/api/v1/analytics/dashboard*");
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText(inr(EXP.months["2026-10"].net_expense), { exact: true }).first()).toBeVisible();
  // database unavailable
  await page.route("**/api/v1/goals*", (r) => r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "The database is currently unavailable. Please try again shortly." }) }));
  await page.goto("/goals");
  await expect(page.getByRole("alert")).toContainText("database is currently unavailable", { timeout: 15_000 });
  await page.unroute("**/api/v1/goals*");
  // network down
  await page.route("**/api/v1/budgets**", (r) => r.abort("connectionrefused"));
  await page.goto("/budgets");
  await expect(page.getByRole("alert").first()).toContainText("Cannot reach the server", { timeout: 15_000 });
  await page.unroute("**/api/v1/budgets**");
  // failed save keeps the form open with the server's message
  await goto("Transactions");
  await page.route("**/api/v1/transactions", (r) => (r.request().method() === "POST" ? r.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "Validation failed", errors: [{ field: "amount", message: "too large" }] }) }) : r.continue()));
  await page.getByRole("button", { name: "Add transaction" }).click();
  await page.getByRole("dialog").getByLabel("Description").fill("X");
  await page.getByRole("dialog").getByLabel("Amount").fill("5");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("amount: too large");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await page.unroute("**/api/v1/transactions");
  // expired session mid-use -> back to login
  await page.route("**/api/v1/transactions*", (r) => r.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Invalid or expired authentication" }) }));
  await page.reload();
  await expect(page).toHaveURL(/\/login$/, { timeout: 15_000 });
  await page.unroute("**/api/v1/transactions*");
});
