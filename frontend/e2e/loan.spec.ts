import { expect, test, type Page } from "@playwright/test";

// Loan / EMI inputs and results in the What-if simulation. Expected figures are worked out by hand from the schedule:
// Rs 10 lakh at 10% for 120 months -> EMI Rs 13,215.07. Baseline income Rs 1,00,000, spending Rs 90,000 (Rs 10,000 saved a month).
test.describe.configure({ mode: "serial" });
let page: Page;
test.beforeAll(async ({ browser }) => { page = await (await browser.newContext()).newPage(); });
test.afterAll(async () => { await page.context().close(); });

const EXISTING = "Existing loan — EMI already included in my regular spending";
const NEW = "New loan — deduct EMI from projected savings";
const card = (label: string) => page.getByText(label, { exact: true }).locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
const fieldOf = (label: string) => page.locator("label").filter({ hasText: label });

async function fillBase() {
  await page.getByLabel("Horizon (months)").fill("12");
  await page.getByLabel("Override monthly income").fill("100000");
  await page.getByLabel("Override monthly spending").fill("90000");
}
async function fillLoan(extra = "") {
  await page.getByLabel("Principal").fill("1000000");
  await page.getByLabel("Annual rate (%)").fill("10");
  await page.getByLabel("Tenure (months)").fill("120");
  await page.getByLabel("Extra payment / month").fill(extra);
}

test("setup", async () => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Loan Tester");
  await page.getByLabel("Email").fill(`loan-${Date.now()}@example.com`);
  await page.getByLabel(/^Password/).fill("Passw0rd!x");
  await page.getByRole("button", { name: "Create account" }).last().click();
  await page.getByRole("link", { name: "Optimize & simulate", exact: true }).click();
  await page.getByRole("tab", { name: "What-if simulation" }).click();
  await fillBase();
});

test("the loan section offers both choices, with the existing loan selected by default", async () => {
  await expect(page.getByLabel(EXISTING)).toHaveCount(0);                      // hidden until a loan is included
  await page.getByLabel("Include a loan / EMI calculation").check();
  await expect(page.getByRole("radio", { name: EXISTING })).toBeChecked();      // default keeps the previous behaviour
  await expect(page.getByRole("radio", { name: NEW })).not.toBeChecked();
  await expect(page.getByText(/already includes this EMI/)).toBeVisible();
  await page.getByRole("radio", { name: NEW }).check();
  await expect(page.getByText(/Every scheduled repayment is deducted from projected savings/)).toBeVisible();
  await page.getByRole("radio", { name: EXISTING }).check();
});

test("invalid loan fields show errors beside their own field and send nothing to the server", async () => {
  let posted = 0;
  page.on("request", (r) => { if (r.url().includes("/simulations/run") && r.method() === "POST") posted++; });
  await page.getByLabel("Principal").fill("0");
  await page.getByLabel("Annual rate (%)").fill("");                              // empty rate: never silently treated as 0%
  await page.getByLabel("Tenure (months)").fill("601");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(fieldOf("Principal").getByRole("alert")).toContainText("Enter the loan amount");
  await expect(fieldOf("Annual rate (%)").getByRole("alert")).toContainText("Enter the annual interest rate. Enter 0 only for an interest-free loan.");
  await expect(fieldOf("Tenure (months)").getByRole("alert")).toContainText("whole number of months, from 1 to 600");   // beside the tenure field
  await page.getByLabel("Tenure (months)").fill("12.5");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(fieldOf("Tenure (months)").getByRole("alert")).toContainText("whole number of months");
  expect(posted, "an invalid form must not call the API").toBe(0);
  // an explicit 0% is accepted (interest-free loan)
  await page.getByLabel("Principal").fill("120000");
  await page.getByLabel("Annual rate (%)").fill("0");
  await page.getByLabel("Tenure (months)").fill("12");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByText(/EMI ₹10,000\.00/)).toBeVisible();                  // 1,20,000 / 12
  expect(posted).toBe(1);
});

test("a loan too small to repay in whole paise gives a clear message", async () => {
  await page.getByLabel("Principal").fill("0.01");
  await page.getByLabel("Annual rate (%)").fill("10");
  await page.getByLabel("Tenure (months)").fill("12");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "cannot be repaid in whole paise" })).toBeVisible();
});

test("existing loan: the EMI is not deducted again; the extra payment is a separate loan cash-flow line", async () => {
  await fillLoan("5000");
  await page.getByRole("radio", { name: EXISTING }).check();
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByText("Existing loan: EMI already in your regular spending", { exact: true })).toBeVisible();
  await expect(page.getByText(/EMI ₹13,215\.07/)).toBeVisible();
  await expect(card("Extra saved per month")).toContainText("₹0.00");                                  // monthly savings unchanged
  await expect(card("Loan cash-flow effect over 12 months")).toContainText("-₹60,000.00");              // 12 x Rs 5,000 extra
  await expect(card("Loan cash-flow effect over 12 months")).toContainText("Not a monthly-savings change");
  await expect(card("Extra after 12 months")).toContainText("-₹60,000.00");
  await expect(page.getByRole("row", { name: /Savings after 12 months/ }).getByRole("cell").nth(1)).toHaveText("₹1,20,000.00");   // baseline: 12 x 10,000
  await expect(page.getByRole("row", { name: /Savings after 12 months/ }).getByRole("cell").nth(2)).toHaveText("₹60,000.00");     // scenario: 12 x 5,000
  await expect(page.getByText(/Of the -₹60,000\.00 difference after 12 months: ₹0\.00 from starting savings \+ ₹0\.00 from monthly changes \+ -₹60,000\.00 from loan cash flow/)).toBeVisible();
  await expect(page.getByText(/never deducted twice/)).toBeVisible();
});

test("new loan: every scheduled payment is deducted; the difference from extra payments is the same", async () => {
  await page.getByRole("radio", { name: NEW }).check();
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByText("New loan: EMI deducted from projected savings", { exact: true })).toBeVisible();
  const row = page.getByRole("row", { name: /Savings after 12 months/ });
  await expect(row.getByRole("cell").nth(1)).toHaveText("-₹38,580.84");                              // 12 x (10,000 - 13,215.07)
  await expect(row.getByRole("cell").nth(2)).toHaveText("-₹98,580.84");                              // 12 x (10,000 - 18,215.07)
  await expect(row.getByRole("cell").nth(3)).toHaveText("-₹60,000.00");
  await expect(card("Loan cash-flow effect over 12 months")).toContainText("-₹60,000.00");
  await expect(card("Extra saved per month")).toContainText("₹0.00");
  await expect(page.getByRole("listitem").filter({ hasText: /New loan: the EMI is not part of your regular spending.*deducted from projected savings in both the baseline and the scenario/ })).toBeVisible();
});
