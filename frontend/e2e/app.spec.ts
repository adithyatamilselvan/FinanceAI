import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "Passw0rd!x";

function periodOffset(back: number): string {
  const d = new Date();
  const idx = d.getFullYear() * 12 + d.getMonth() - back;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

/** 4 completed months (ending last month): salary 100,000; rent 30,000; Netflix 649; groceries; food; transport; cinema. */
function statementCsv(): string {
  const rows = ["Date,Description,Debit,Credit"];
  for (let i = 0; i < 4; i++) {
    const p = periodOffset(4 - i);
    rows.push(`${p}-01,Salary ACME Corp,,100000.00`, `${p}-02,House Rent Landlord,30000.00,`, `${p}-05,Netflix Subscription,649.00,`,
      `${p}-08,BigBasket Groceries,${4000 + 100 * i}.00,`, `${p}-12,Swiggy Order,${1500 + 50 * i}.00,`, `${p}-20,Uber Ride,800.00,`, `${p}-25,PVR Cinemas,1200.00,`);
  }
  return rows.join("\n") + "\n";
}

async function register(page: Page, email: string) {
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("E2E User");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).last().click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

async function importStatement(page: Page, csv: string) {
  await page.getByRole("link", { name: "Import statement" }).click();
  await page.getByLabel("Statement file").setInputFiles({ name: "statement.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Preview import" }).click();
}

test("authentication: protected routes, invalid login, logout", async ({ page }) => {
  await page.goto("/transactions");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrong-pass1");
  await page.getByRole("button", { name: "Log in" }).last().click();
  await expect(page.getByRole("alert")).toContainText("Incorrect email or password");
  await page.getByLabel("Email").fill("bad");
  await page.getByRole("button", { name: "Log in" }).last().click();
  await expect(page.getByText("Enter a valid email")).toBeVisible();

  await register(page, `auth-${Date.now()}@example.com`);
  await expect(page.getByText("No transactions yet")).toBeVisible(); // honest empty state, no fake figures
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/budgets");
  await expect(page).toHaveURL(/\/login$/);
});

test("import validation: unsupported file type is rejected in the UI", async ({ page }) => {
  await register(page, `val-${Date.now()}@example.com`);
  await page.getByRole("link", { name: "Import statement" }).click();
  await page.getByLabel("Statement file").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.getByRole("alert")).toContainText("Only .csv and .xlsx");
  await expect(page.getByRole("button", { name: "Preview import" })).toBeDisabled();
});

test("full workflow: profile -> import -> dashboard -> edit -> budget -> goal -> analytics -> optimise -> simulate -> reports", async ({ page }) => {
  await register(page, `flow-${Date.now()}@example.com`);

  // profile
  await page.getByRole("link", { name: "Profile & settings" }).click();
  await page.getByLabel("Monthly income (expected)").fill("100000");
  await page.getByLabel("Current savings balance").fill("200000");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();

  // import: preview then confirm
  await importStatement(page, statementCsv());
  await expect(page.getByText("Review before saving")).toBeVisible();
  await expect(page.getByText("new transactions")).toContainText("28");
  await page.getByRole("button", { name: "Import 28 transactions" }).click();
  await expect(page.getByText(/Imported\s*28\s*transactions/)).toBeVisible();

  // re-importing the same file: every row is a duplicate
  await importStatement(page, statementCsv());
  await expect(page.getByText("duplicates (will be skipped)")).toContainText("28");
  await page.getByRole("button", { name: "Discard" }).click();

  // dashboard figures come from the imported data: last month income 100,000 and spending 38,599
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByText("₹1,00,000.00").first()).toBeVisible();
  await expect(page.getByText("₹38,599.00").first()).toBeVisible();
  await expect(page.getByText("₹61,401.00").first()).toBeVisible(); // net savings
  await expect(page.getByText("Groceries").first()).toBeVisible();

  // transactions: search + edit category (teaches a rule)
  await page.getByRole("link", { name: "Transactions" }).click();
  await page.getByPlaceholder("Description").fill("swiggy");
  await expect(page.getByRole("row")).toHaveCount(5); // header + 4 Swiggy rows
  await page.getByRole("row").nth(1).getByRole("button", { name: "Edit" }).click();
  await page.getByRole("dialog").getByLabel("Category").selectOption("Entertainment");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Entertainment", exact: true }).first()).toBeVisible();
  // manual transaction with validation
  await page.getByRole("button", { name: "Add transaction" }).click();
  await page.getByRole("dialog").getByLabel("Description").fill("Lunch with team");
  await page.getByRole("dialog").getByLabel("Amount").fill("12.345");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Enter a positive amount with up to 2 decimals")).toBeVisible();
  await page.getByRole("dialog").getByLabel("Amount").fill("250.50");
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await page.getByPlaceholder("Description").fill("lunch with team");
  await expect(page.getByRole("cell", { name: "-₹250.50" })).toBeVisible();
  await page.getByRole("row").nth(1).getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No transactions found")).toBeVisible();

  // budget for the latest data month
  const latest = periodOffset(1);
  await page.getByRole("link", { name: "Budgets" }).click();
  await page.getByLabel("Budget month").fill(latest);
  await page.getByRole("button", { name: "Create budget" }).click();
  await page.getByRole("dialog").getByLabel("Groceries").fill("4000");
  await page.getByRole("dialog").getByLabel("Housing").fill("30000");
  await page.getByRole("button", { name: "Save budget" }).click();
  await expect(page.getByText(/Over budget in: Groceries/)).toBeVisible(); // 4,300 actual vs 4,000 budget

  // goal
  await page.getByRole("link", { name: "Savings goals" }).click();
  await page.getByRole("button", { name: "New goal" }).click();
  await page.getByLabel("Goal name").fill("Holiday");
  await page.getByLabel("Target amount").fill("100000");
  await page.getByLabel("Saved so far").fill("25000");
  await page.getByLabel(/Planned monthly saving/).fill("10000");
  await page.getByRole("button", { name: "Save goal" }).click();
  await expect(page.getByText("25.0%")).toBeVisible();
  await page.getByRole("button", { name: "Add savings" }).click();
  await page.getByLabel("Amount").fill("5000");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("30.0%")).toBeVisible();
  await page.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByText(/Target:/)).toBeVisible();

  // analytics: recurring detection + forecast
  await page.getByRole("link", { name: "Analytics" }).click();
  await expect(page.getByText(/Based on\s*4\s*completed months/)).toBeVisible();
  await page.getByRole("button", { name: "Detect from my transactions" }).click();
  await expect(page.getByRole("cell", { name: /Netflix Subscription/ })).toBeVisible();
  await page.getByRole("row", { name: /Netflix/ }).getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("row", { name: /Netflix/ })).toContainText("confirmed");

  // optimisation: nothing changes until the user accepts
  await page.getByRole("link", { name: "Optimize & simulate" }).click();
  await page.getByRole("button", { name: "Find savings opportunities" }).click();
  await expect(page.getByText("Fixed (not changed)")).toBeVisible();
  await page.getByRole("button", { name: "Accept balanced plan" }).click();
  await expect(page.getByText(/Your budget was not changed/)).toBeVisible();

  // what-if simulation with real baseline
  await page.getByRole("tab", { name: "What-if simulation" }).click();
  await page.getByLabel("Save extra every month").fill("5000");
  await page.getByLabel("Horizon (months)").fill("12");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByText("Savings after 12 months")).toBeVisible();
  await page.getByLabel("Horizon (months)").fill("0");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByText("1 to 360 months")).toBeVisible();

  // reports + downloads
  await page.getByRole("link", { name: "Reports" }).click();
  await expect(page.getByText(/Monthly summary/)).toBeVisible();
  const pdf = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download PDF" }).click();
  const d = await pdf;
  expect(d.suggestedFilename()).toMatch(/^report-\d{4}-\d{2}\.pdf$/);
  const csv = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  expect((await csv).suggestedFilename()).toBe("transactions.csv");
});
