import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

// What-if simulation: starting savings is a scenario-only opening balance, shown separately from monthly changes.
// Case under test: baseline (profile) savings Rs 0, scenario starting savings Rs 5,000, 24 months.
const STATEMENT = path.resolve(process.cwd(), "..", "docs", "test-data", "sample_bank_statement.csv");

test.describe.configure({ mode: "serial" });
let page: Page;
test.beforeAll(async ({ browser }) => { page = await (await browser.newContext()).newPage(); });
test.afterAll(async () => { await page.context().close(); });

const card = (label: string) => page.getByText(label, { exact: true }).locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");

test("setup: new user with imported history and Rs 0 profile savings", async () => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Sim Tester");
  await page.getByLabel("Email").fill(`sim-${Date.now()}@example.com`);
  await page.getByLabel(/^Password/).fill("Passw0rd!x");
  await page.getByRole("button", { name: "Create account" }).last().click();
  await page.getByRole("link", { name: "Import statement", exact: true }).click();
  await page.getByLabel("Statement file").setInputFiles(STATEMENT);
  await page.getByRole("button", { name: "Preview import" }).click();
  await page.getByRole("button", { name: /^Import \d+ transactions$/ }).click();
  await expect(page.getByText(/Imported\s*\d+\s*transactions/)).toBeVisible();
});

test("starting savings Rs 5,000 over 24 months: cards, table, breakdown and chart data all agree", async () => {
  await page.getByRole("link", { name: "Optimize & simulate", exact: true }).click();
  await page.getByRole("tab", { name: "What-if simulation" }).click();
  await expect(page.getByText(/baseline always starts from your profile balance \(₹0\.00\)/)).toBeVisible(); // hint says what the input does
  await page.getByLabel("Horizon (months)").fill("24");
  await page.getByLabel("Starting savings (default: profile)").fill("5000");
  const resp = page.waitForResponse((r) => r.url().includes("/simulations/run"));
  await page.getByRole("button", { name: "Run simulation" }).click();
  const sim = await (await resp).json();

  // API: the difference is exactly Rs 5,000 and is attributed to starting savings, not to monthly saving
  expect(sim.baseline_starting_savings).toBe(0);
  expect(sim.scenario_starting_savings).toBe(500000);
  expect(sim.difference).toMatchObject({ final_balance: 500000, starting_savings: 500000, monthly_savings: 0, from_monthly_changes: 0, from_growth: 0 });
  expect(sim.scenario.final_balance - sim.baseline.final_balance).toBe(500000);
  expect(sim.series.scenario.every((v: number, i: number) => v - sim.series.baseline[i] === 500000)).toBe(true);
  expect(sim.series.scenario).toHaveLength(24);

  // cards: starting savings shown on its own; monthly extra stays Rs 0
  await expect(card("Extra from starting savings")).toContainText("₹5,000.00");
  await expect(card("Extra from starting savings")).toContainText("One-off opening balance, not monthly");
  await expect(card("Extra saved per month")).toContainText("₹0.00");
  await expect(card("Extra from monthly changes over 24 months")).toContainText("₹0.00");
  await expect(card("Extra after 24 months")).toContainText("₹5,000.00");

  // table: a separate starting-savings row (baseline 0, scenario 5,000, difference 5,000); the final-balance row differs by exactly 5,000
  const start = page.getByRole("row", { name: /Starting savings \(one-off\)/ });
  await expect(start).toContainText("₹0.00");
  await expect(start.getByRole("cell").nth(2)).toHaveText("₹5,000.00");
  await expect(start.getByRole("cell").nth(3)).toHaveText("₹5,000.00");
  const fin = page.getByRole("row", { name: /Savings after 24 months/ });
  const fmt = (p: number) => `₹${new Intl.NumberFormat("en-IN").format(Math.floor(p / 100))}.${String(p % 100).padStart(2, "0")}`;
  await expect(fin.getByRole("cell").nth(1)).toHaveText(fmt(sim.baseline.final_balance));
  await expect(fin.getByRole("cell").nth(2)).toHaveText(fmt(sim.scenario.final_balance));
  await expect(fin.getByRole("cell").nth(3)).toHaveText("₹5,000.00");
  await expect(page.getByRole("row", { name: /Monthly savings/ }).getByRole("cell").nth(3)).toHaveText("₹0.00"); // not labelled as monthly saving

  // plain-language breakdown
  await expect(page.getByText(/Of the ₹5,000\.00 difference after 24 months: ₹5,000\.00 from starting savings \+ ₹0\.00 from monthly changes\./)).toBeVisible();
});

test("a monthly contribution is shown as monthly, with no starting-savings effect", async () => {
  await page.getByLabel("Starting savings (default: profile)").fill("");
  await page.getByLabel("Save extra every month").fill("1000");
  await page.getByLabel("Horizon (months)").fill("12");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(card("Extra saved per month")).toContainText("₹1,000.00");
  await expect(card("Extra from monthly changes over 12 months")).toContainText("₹12,000.00");
  await expect(card("Extra from starting savings")).toContainText("₹0.00");
  await expect(card("Extra after 12 months")).toContainText("₹12,000.00");
});
