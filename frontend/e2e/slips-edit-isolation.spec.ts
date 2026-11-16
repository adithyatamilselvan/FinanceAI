import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

// Regression: the edit dialog of one slip must never show, or change, another slip's data.
// Ground truth = the printed values on the original synthetic receipts (supplied by the product owner), NOT read back from the app.
const DIR = path.resolve(process.cwd(), "..", "docs", "test-data", "payment-slips");
const ALL = ["slip_01_phonepe_swiggy.png", "slip_02_gpay_bigbasket.jpg", "slip_03_paytm_received.jpeg", "slip_04_neft_landlord.pdf", "slip_05_card_apollo.jpg",
  "slip_06_bescom_scanned.pdf", "slip_07_cash_kirana.png", "slip_08_failed_actfibernet.png", "slip_09_pending_zomato.jpg", "slip_10_refund_amazon.png"];

type Dialog = { date: string; time: string; amount: string; merchant: string; payment: string; reference: string; status: string; currency: string };
const APOLLO = { file: "slip_05_card_apollo.jpg", date: "2026-10-08", time: "14:32", amount: "842.00", merchant: "APOLLO PHARMACY", payment: "Card", reference: "628455512345", status: "successful", currency: "INR" };
const BESCOM = { file: "slip_06_bescom_scanned.pdf", date: "2026-10-10", time: "", amount: "1840.00", merchant: "BESCOM", payment: "UPI", reference: "BES2610100099871", status: "successful", currency: "INR" };
const KIRANA = { file: "slip_07_cash_kirana.png", date: "2026-10-14", time: "", amount: "310.00", merchant: "SRI LAKSHMI KIRANA STORE", payment: "Cash", reference: "", status: "successful", currency: "INR" };
const SLIPS = [APOLLO, BESCOM, KIRANA];
const pick = ({ file: _f, ...rest }: (typeof SLIPS)[number]): Dialog => rest;

test.describe.configure({ mode: "serial" });
let page: Page;
let token = "";
let batchId = "";

test.beforeAll(async ({ browser }) => { page = await (await browser.newContext()).newPage(); });
test.afterAll(async () => { await page.context().close(); });

async function readDialog(file: string): Promise<Dialog> {
  const d = page.getByRole("dialog", { name: `Edit: ${file}` });
  await expect(d, `dialog title must name ${file}`).toBeVisible();
  const val = (label: string | RegExp) => d.getByLabel(label, { exact: typeof label === "string" }).inputValue();
  return {
    date: await val(/^Date/), time: await val(/^Time/), amount: await val(/^Amount/), merchant: await val(/^Merchant/),
    payment: await val(/^Payment method/), reference: await val(/^Transaction reference/), status: await val(/^Payment status/), currency: await val(/^Currency/),
  };
}
async function openEdit(file: string) { await page.getByRole("button", { name: `Edit ${file}`, exact: true }).click(); }
type CloseHow = "cancel" | "escape" | "x" | "backdrop";
async function closeDialog(how: CloseHow) {
  const d = page.getByRole("dialog");
  if (how === "cancel") await d.getByRole("button", { name: "Cancel" }).click();
  else if (how === "escape") await page.keyboard.press("Escape");
  else if (how === "x") await d.getByRole("button", { name: "Close" }).click();
  else await page.locator("div.fixed.inset-0").first().click({ position: { x: 6, y: 6 } }); // click on the backdrop, outside the dialog
  await expect(d).toHaveCount(0);
}
/** The server's stored view of every slip, fetched with the user's own token (not through the UI). */
async function serverItems(): Promise<Record<string, { id: string; fields: Record<string, unknown>; category: string | null; edited: string[] }>> {
  const r = await page.request.get(`/api/v1/slips/batches/${batchId}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.status()).toBe(200);
  const b = await r.json();
  return Object.fromEntries(b.items.map((i: { file: string }) => [i.file, i]));
}

test("setup: upload all 10 slips (real OCR)", async () => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Edit Isolation");
  await page.getByLabel("Email").fill(`isolation-${Date.now()}@example.com`);
  await page.getByLabel(/^Password/).fill("Passw0rd!x");
  await page.getByRole("button", { name: "Create account" }).last().click();
  await page.getByRole("link", { name: "Payment Slip Scanner" }).click();
  await page.getByLabel("Payment slip files").setInputFiles(ALL.map((n) => path.join(DIR, n)));
  await expect(page.getByRole("table", { name: "Extracted transactions" })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: /^Edit slip_/ })).toHaveCount(10, { timeout: 90_000 });
  token = (await page.evaluate(() => localStorage.getItem("financeai.token")))!;
  batchId = (await page.evaluate(() => Object.entries(sessionStorage).map(([, v]) => v).find((v) => /^[0-9a-f]{24}$/.test(v ?? "")) ?? ""))!;
  expect(batchId).toMatch(/^[0-9a-f]{24}$/);
});

test("the server stores each slip's own values (ground truth, nothing fabricated)", async () => {
  const s = await serverItems();
  expect(Object.keys(s)).toHaveLength(10);
  expect(new Set(Object.values(s).map((i) => i.id)).size).toBe(10); // unique ids
  for (const g of SLIPS) {
    const f = s[g.file].fields;
    expect([f.date, f.amount, f.merchant, f.payment_method, f.reference, f.status, f.currency]).toEqual([g.date, Math.round(Number(g.amount) * 100), g.merchant, g.payment, g.reference || null, g.status, g.currency]);
    expect(f.time ?? "").toBe(g.time); // BESCOM and Kirana print no time: it must stay empty
  }
  expect(s[KIRANA.file].fields.reference).toBeNull();   // no reference printed
  expect(s[BESCOM.file].fields.time).toBeNull();
});

for (const order of [[APOLLO, BESCOM, KIRANA], [KIRANA, BESCOM, APOLLO], [BESCOM, APOLLO, KIRANA], [APOLLO, KIRANA, BESCOM]]) {
  for (const how of ["cancel", "escape", "x", "backdrop"] as CloseHow[]) {
    test(`opening ${order.map((o) => o.merchant.split(" ")[0]).join(" -> ")} one after another (closed by ${how}) shows each slip's own values`, async () => {
      await page.reload();
      await expect(page.getByRole("button", { name: /^Edit slip_/ })).toHaveCount(10);
      for (const g of [...order, ...order]) {            // twice round, to catch state left over from an earlier open
        await openEdit(g.file);
        expect(await readDialog(g.file), `${g.file} dialog`).toEqual(pick(g));
        await closeDialog(how);
      }
    });
  }
}

test("each slip's 'text read from this slip' belongs to that slip", async () => {
  await page.reload();
  for (const [g, needle] of [[APOLLO, "APOLLO"], [BESCOM, "BESCOM"], [KIRANA, "KIRANA"]] as const) {
    await openEdit(g.file);
    const d = page.getByRole("dialog", { name: `Edit: ${g.file}` });
    await d.getByText("Show the text read from this slip").click();
    await expect(d.locator("pre")).toContainText(needle, { ignoreCase: true });
    for (const other of SLIPS.filter((o) => o !== g)) await expect(d.locator("pre")).not.toContainText(other.merchant.split(" ")[0], { ignoreCase: true });
    await closeDialog("cancel");
  }
});

test("saving without changing anything leaves the record exactly as extracted", async () => {
  const before = await serverItems();
  await page.reload();
  await openEdit(BESCOM.file);
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const after = await serverItems();
  expect(after[BESCOM.file].fields).toEqual(before[BESCOM.file].fields);          // time/reference not invented
  for (const g of [APOLLO, KIRANA]) expect(after[g.file].fields).toEqual(before[g.file].fields);
});

test("editing one slip changes only that slip; each edit persists after reload", async () => {
  const before = await serverItems();
  const edits = [
    { g: APOLLO, merchant: "APOLLO PHARMACY KORAMANGALA", amount: "845.50", description: "edited-apollo" },
    { g: BESCOM, merchant: "BESCOM BILL PAYMENT", amount: "1850.00", description: "edited-bescom" },
    { g: KIRANA, merchant: "SRI LAKSHMI STORES", amount: "315.25", description: "edited-kirana" },
  ];
  for (const e of edits) {
    await page.reload();
    await openEdit(e.g.file);
    const d = page.getByRole("dialog", { name: `Edit: ${e.g.file}` });
    await d.getByLabel(/^Merchant/).fill(e.merchant);
    await d.getByLabel(/^Amount/).fill(e.amount);
    await d.getByLabel("Description").fill(e.description);
    await d.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // after this save, ONLY this slip differs from the original; every other slip is byte-for-byte unchanged
    const now = await serverItems();
    const done = edits.slice(0, edits.indexOf(e) + 1).map((x) => x.g.file);
    for (const file of Object.keys(before)) {
      if (!done.includes(file)) expect(now[file], `${file} must be untouched after editing ${e.g.file}`).toEqual(before[file]);
    }
    expect(now[e.g.file].fields.merchant).toBe(e.merchant);
    expect(now[e.g.file].fields.amount).toBe(Math.round(Number(e.amount) * 100));
    expect(now[e.g.file].fields.description).toBe(e.description);
    expect(now[e.g.file].edited.sort()).toEqual(["amount", "description", "merchant"]);
    // the untouched fields of the edited slip are still its own (not another slip's)
    expect(now[e.g.file].fields.reference).toBe(before[e.g.file].fields.reference);
    expect(now[e.g.file].fields.date).toBe(e.g.date);
    expect(now[e.g.file].fields.payment_method).toBe(e.g.payment);
  }

  // reload the page and reopen all three: each shows ITS OWN edited values and its own unedited fields
  await page.reload();
  for (const e of edits) {
    await openEdit(e.g.file);
    const got = await readDialog(e.g.file);
    expect(got).toEqual({ ...pick(e.g), merchant: e.merchant, amount: e.amount });
    await expect(page.getByRole("dialog").getByLabel("Description")).toHaveValue(e.description);
    await closeDialog("escape");
  }
  // and the other 7 slips are still exactly as extracted
  const final = await serverItems();
  for (const file of ALL.filter((f) => !edits.some((e) => e.g.file === f))) expect(final[file]).toEqual(before[file]);
});

test("a failed save keeps the dialog on the same slip (no other slip's data appears)", async () => {
  await page.reload();
  await page.route("**/api/v1/slips/batches/*/items/*", (r) => (r.request().method() === "PATCH" ? r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Internal server error" }) }) : r.continue()));
  await openEdit(KIRANA.file);
  const d = page.getByRole("dialog", { name: `Edit: ${KIRANA.file}` });
  await d.getByLabel("Description").fill("will fail");
  await d.getByRole("button", { name: "Save changes" }).click();
  await expect(d.getByRole("alert")).toBeVisible();
  expect((await readDialog(KIRANA.file)).merchant).toBe("SRI LAKSHMI STORES"); // its own (earlier edited) merchant, still
  await page.unroute("**/api/v1/slips/batches/*/items/*");
  await closeDialog("cancel");
});
