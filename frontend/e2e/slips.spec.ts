import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

// Payment Slip Scanner, end to end, on SYNTHETIC slips (docs/test-data/payment-slips) with real Tesseract OCR on the real backend.
const DIR = path.resolve(process.cwd(), "..", "docs", "test-data", "payment-slips");
const TRUTH = JSON.parse(fs.readFileSync(path.join(DIR, "expected_slips.json"), "utf-8")) as Record<string, { date: string; amount: number; type: string; merchant: string | null; sender: string | null; status: string; reference: string | null; payment_method: string | null }>;
const TEN = Object.keys(TRUTH).filter((k) => !k.startsWith("edge-cases/")).sort();
const PY = process.platform === "win32" ? "..\\backend\\.venv\\Scripts\\python.exe" : "../backend/.venv/bin/python";
const EMAIL = `slips-${Date.now()}@example.com`;
const PASSWORD = "Passw0rd!x";
const inr = (p: number) => `${p < 0 ? "-" : ""}₹${new Intl.NumberFormat("en-IN").format(Math.floor(Math.abs(p) / 100))}.${String(Math.abs(p) % 100).padStart(2, "0")}`;
const paths = (names: string[]) => names.map((n) => path.join(DIR, n));
const row = (page: Page, file: string) => page.getByRole("table", { name: "Extracted transactions" }).getByRole("row").filter({ hasText: file });
const importBtn = (page: Page, n: number) => page.getByRole("button", { name: `Confirm and import ${n} selected` });

test.describe.configure({ mode: "serial" });
let page: Page;
test.beforeAll(async ({ browser }) => { page = await (await browser.newContext({ acceptDownloads: true })).newPage(); });
test.afterAll(async () => { await page.context().close(); });

async function openScanner() { await page.getByRole("link", { name: "Payment Slip Scanner" }).click(); await expect(page.getByRole("heading", { name: "Payment Slip Scanner" })).toBeVisible(); }
async function waitProcessed() { await expect(page.getByRole("table", { name: "Extracted transactions" })).toBeVisible({ timeout: 60_000 }); }

test("1 register and open the scanner (empty state, limits, OCR available)", async () => {
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Slip Tester");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel(/^Password/).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).last().click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await openScanner();
  await expect(page.getByText("Drag and drop payment slips here")).toBeVisible();
  await expect(page.getByText(/JPG, JPEG, PNG, PDF; up to 20 files per batch, 5 MB each/)).toBeVisible();
  await expect(page.getByText("nothing is saved to your transactions until you review and confirm it")).toBeVisible();
  await expect(page.getByText(/OCR engine is not available/)).toHaveCount(0);
});

test("2 unsupported / oversized / empty files are rejected before upload", async () => {
  await page.getByLabel("Payment slip files").setInputFiles([
    { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") },
    { name: "huge.png", mimeType: "image/png", buffer: Buffer.alloc(5 * 1024 * 1024 + 10) },
    { name: "empty.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(0) },
  ]);
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("notes.txt: only JPG, JPEG, PNG and PDF");
  await expect(alert).toContainText("huge.png: larger than 5 MB");
  await expect(alert).toContainText("empty.pdf: the file is empty");
  await expect(page.getByRole("table", { name: "Extracted transactions" })).toHaveCount(0);
});

test("3 upload 10 slips: per-file progress, then every extracted value matches the ground truth", async () => {
  await page.getByLabel("Payment slip files").setInputFiles(paths(TEN));
  await expect(page.getByLabel("Per-file status").getByRole("listitem")).toHaveCount(10);
  await waitProcessed();
  await expect(page.getByLabel("Per-file status").locator('[data-state="done"]')).toHaveCount(10);
  for (const name of TEN) {
    const t = TRUTH[name];
    const r = row(page, path.basename(name));
    await expect(r, name).toContainText(t.date);
    await expect(r, name).toContainText(inr(t.amount));
    await expect(r, name).toContainText(t.type);
    await expect(r, name).toContainText((t.merchant ?? t.sender)!);
    await expect(r, name).toContainText(t.status);
    if (t.reference) await expect(r, name).toContainText(t.reference);
  }
  // defaults: only successful, complete, unflagged slips are pre-selected
  const ready = ["slip_01_phonepe_swiggy.png", "slip_02_gpay_bigbasket.jpg", "slip_03_paytm_received.jpeg", "slip_04_neft_landlord.pdf"];
  for (const n of TEN) {
    const box = page.getByLabel(`Import ${n}`);
    if (ready.includes(n)) await expect(box).toBeChecked(); else await expect(box).not.toBeChecked();
  }
  await expect(page.getByLabel("Import slip_08_failed_actfibernet.png")).toBeDisabled(); // a failed payment can never be imported
  await expect(row(page, "slip_08")).toContainText("Payment failed");
  await expect(row(page, "slip_05")).toContainText("Needs review");
  await expect(row(page, "slip_09")).toContainText("not a successful payment");
  await expect(importBtn(page, 4)).toBeEnabled();
});

test("4 the Excel workbook downloads and contains the extracted data", async () => {
  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Excel" }).click();
  const file = await dl;
  expect(file.suggestedFilename()).toBe("payment-slips.xlsx");
  const script = path.join(process.cwd(), "test-results", "read_workbook.py");
  fs.mkdirSync(path.dirname(script), { recursive: true });
  fs.writeFileSync(script, [
    "import sys,json,openpyxl",
    "wb=openpyxl.load_workbook(sys.argv[1])",
    "ws=wb['Extracted Transactions']",
    "rows=[[str(c) if c is not None else None for c in r] for r in ws.iter_rows(values_only=True)]",
    "summ={r[0]:r[1] for r in list(wb['Extraction Summary'].iter_rows(values_only=True))[1:]}",
    "print(json.dumps({'sheets':wb.sheetnames,'header':rows[0][:15],'rows':rows[1:],'summary':{k:str(v) for k,v in summ.items()}}))",
  ].join("\n"));
  const saved = path.join(process.cwd(), "test-results", "downloaded-slips.xlsx");
  await file.saveAs(saved);
  const out = execFileSync(PY, [script, saved], { encoding: "utf-8" });
  const wb = JSON.parse(out) as { sheets: string[]; header: string[]; rows: (string | null)[][]; summary: Record<string, string> };
  expect(wb.sheets).toEqual(["Extracted Transactions", "Extraction Summary", "Errors", "Source Text"]);
  expect(wb.header).toEqual(["Source File", "Transaction Date", "Transaction Time", "Amount (INR)", "Transaction Type", "Merchant / Recipient", "Sender", "Payment Method", "Transaction Reference",
    "Bank / Provider", "Status", "Description", "OCR Confidence", "Review Required", "Import Status"]);
  expect(wb.rows).toHaveLength(10);
  for (const name of TEN) {
    const t = TRUTH[name];
    const r = wb.rows.find((x) => x[0] === path.basename(name))!;
    expect(r[1]).toContain(t.date);
    expect(Math.round(Number(r[3]) * 100)).toBe(t.amount);
    expect(r[4]).toBe(t.type);
    expect(r[10]).toBe(t.status);
    expect(r[8]).toBe(t.reference);
  }
  expect(wb.summary["Total files uploaded"]).toBe("10");
  expect(wb.summary["Failed files"]).toBe("0");
  expect(wb.summary["Extracted transactions"]).toBe("10");
  expect(wb.summary["Transactions requiring review"]).toBe("6");
});

test("5 human review: non-successful and unverified slips are refused until the user decides", async () => {
  // try to import pending (09) + refunded (10) without any decision
  await page.getByLabel("Import slip_09_pending_zomato.jpg").check();
  await page.getByLabel("Import slip_10_refund_amazon.png").check();
  await importBtn(page, 6).click();
  const alert = page.getByRole("alert").filter({ hasText: "cannot be imported yet" });
  await expect(alert).toContainText("Nothing was saved");
  await expect(alert).toContainText("slip_09_pending_zomato.jpg");
  await expect(alert).toContainText("confirm that you want to import non-successful payments");
  // tick the explicit decision; still must be verified by the user
  await page.getByLabel(/Allow selected pending, refunded or unclear payments/).check();
  await importBtn(page, 6).click();
  await expect(page.getByRole("alert").filter({ hasText: "cannot be imported yet" })).toContainText("please review and mark this row as verified");
  // the user checks the slips and verifies them (edit modal), correcting the uncertain merchant on 05 and 07 on the way
  for (const f of ["slip_09_pending_zomato.jpg", "slip_10_refund_amazon.png"]) {
    await page.getByRole("button", { name: `Edit ${f}` }).click();
    await page.getByRole("dialog").getByLabel("I have checked these details against the slip").check();
    await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
    await expect(row(page, f)).toContainText("verified by you");
  }
  await page.getByRole("button", { name: "Edit slip_05_card_apollo.jpg" }).click();
  await expect(page.getByRole("dialog").getByText(/Check: taken from the top of the slip/)).toBeVisible();
  await page.getByRole("dialog").getByText("Show the text read from this slip").click();
  await expect(page.getByRole("dialog").getByText("APPROVAL CODE")).toBeVisible();
  await page.getByRole("dialog").getByLabel("Merchant / recipient").fill("Apollo Pharmacy");
  await page.getByRole("dialog").getByLabel("Category").selectOption("Healthcare");
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(row(page, "slip_05")).toContainText("Apollo Pharmacy");
  await expect(row(page, "slip_05")).toContainText("verified by you");
  await page.getByRole("button", { name: "Edit slip_07_cash_kirana.png" }).click();
  await page.getByRole("dialog").getByLabel("Amount").fill("310.50"); // user corrects an amount
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(row(page, "slip_07")).toContainText(inr(31050));
  await page.getByRole("button", { name: "Edit slip_07_cash_kirana.png" }).click();
  await page.getByRole("dialog").getByLabel("Amount").fill("12.345"); // invalid input is refused inline
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Enter a positive amount with up to 2 decimals")).toBeVisible();
  await page.getByRole("dialog").getByLabel("Amount").fill("310.00");
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(row(page, "slip_07")).toContainText(inr(31000));
  await page.getByRole("button", { name: "Mark slip_06_bescom_scanned.pdf verified" }).click();
  await expect(row(page, "slip_06")).toContainText("verified by you");
  for (const n of ["slip_05_card_apollo.jpg", "slip_06_bescom_scanned.pdf", "slip_07_cash_kirana.png"]) await page.getByLabel(`Import ${n}`).check();
});

test("6 confirm and import: records reach Transactions, Dashboard and Analytics", async () => {
  await importBtn(page, 9).click();
  await expect(page.getByRole("status").filter({ hasText: "Imported 9 transactions" })).toBeVisible();
  await page.getByRole("link", { name: "Transactions" }).first().click();
  await expect(page.getByText("9 transactions", { exact: true })).toBeVisible();
  await page.getByPlaceholder("Description").fill("swiggy");
  await expect(page.getByRole("row").filter({ hasText: "SWIGGY" })).toContainText("2026-10-12");
  await expect(page.getByRole("row").filter({ hasText: "SWIGGY" })).toContainText("-₹486.00");
  await page.getByPlaceholder("Description").fill("amazon");
  await expect(page.getByRole("row").filter({ hasText: "AMAZON" })).toContainText("+₹1,299.00"); // refund

  // Dashboard (October) computed independently from the ground truth
  const gross = 48600 + 234050 + 2200000 + 84200 + 184000 + 31000 + 75000;
  const net = gross - 129900;
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByText(inr(150000), { exact: true }).first()).toBeVisible(); // income (slip 03)
  await expect(page.getByText(inr(net), { exact: true }).first()).toBeVisible();
  await expect(page.getByText(inr(150000 - net), { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Housing" }).filter({ hasText: inr(2200000) }).first()).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "Groceries" }).filter({ hasText: inr(234050 + 31000) }).first()).toBeVisible();
  await page.getByRole("link", { name: "Analytics" }).click();
  await expect(page.getByRole("cell", { name: "Housing", exact: true })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Housing" }).first()).toContainText(inr(2200000));
  await page.getByRole("link", { name: "Reports" }).click();
  await expect(page.getByText(inr(150000 - net), { exact: true }).first()).toBeVisible();
});

test("7 uploading the same slips again creates no duplicates", async () => {
  await openScanner();
  await expect(page.getByText(/Imported slips/)).toBeVisible(); // the finished batch is still shown after revisiting
  await page.getByRole("button", { name: "Start a new batch" }).click();
  await page.getByLabel("Payment slip files").setInputFiles(paths(TEN));
  await waitProcessed();
  for (const n of TEN.filter((x) => !x.includes("slip_08"))) {
    await expect(row(page, n)).toContainText("Already imported");
    await expect(page.getByLabel(`Import ${n}`)).toBeDisabled();
    await expect(page.getByLabel(`Import ${n}`)).not.toBeChecked();
  }
  await expect(row(page, "slip_08")).toContainText("Payment failed");
  await expect(importBtn(page, 0)).toBeDisabled();
  await page.getByRole("link", { name: "Transactions" }).first().click();
  await expect(page.getByText("9 transactions", { exact: true })).toBeVisible(); // unchanged
});

test("8 cancel deletes the batch; a refresh restores an unfinished one", async () => {
  await openScanner();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Cancel batch" }).click();
  await expect(page.getByText(/Batch cancelled/)).toBeVisible();
  await expect(page.getByRole("table", { name: "Extracted transactions" })).toHaveCount(0);

  await page.getByLabel("Payment slip files").setInputFiles(paths([TEN[0], TEN[3]]));
  await waitProcessed();
  await page.reload();
  await expect(page.getByRole("table", { name: "Extracted transactions" })).toBeVisible(); // batch restored from the server after refresh
  await expect(page.getByRole("row").filter({ hasText: "slip_04_neft_landlord.pdf" })).toContainText("ICIC26100312345678");
  await page.getByRole("button", { name: "Add more slips" }).count();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Cancel batch" }).click();
  await expect(page.getByText(/Batch cancelled/)).toBeVisible();
});

test("9 edge cases: unreadable image, missing fields, server errors, OCR unavailable", async () => {
  await openScanner();
  await page.getByLabel("Payment slip files").setInputFiles(paths(["edge-cases/edge_blurry_noisy.jpg", "edge-cases/edge_missing_date.png", "edge-cases/edge_missing_amount.png", "edge-cases/edge_blank.png"]));
  await expect(page.getByText("Files that could not be read")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("edge_blurry_noisy.jpg", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/No transaction details/).first()).toBeVisible();
  await expect(page.getByText(/No readable text was found/).first()).toBeVisible();
  const missingDate = row(page, "edge_missing_date");
  await expect(missingDate).toContainText("missing");
  await expect(missingDate).toContainText("Missing details");
  await expect(page.getByLabel("Import edge_missing_date.png")).toBeDisabled(); // cannot be selected until the user fills the date
  await page.getByRole("button", { name: "Edit edge_missing_date.png" }).click();
  await expect(page.getByRole("dialog").getByText("Not found on the slip: please fill in").first()).toBeVisible();
  await page.getByRole("dialog").getByLabel(/^Date/).fill("2026-10-12");
  await page.getByRole("dialog").getByRole("button", { name: "Save changes" }).click();
  await expect(row(page, "edge_missing_date")).toContainText("2026-10-12");
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Cancel batch" }).click();

  // server error while reading one file: shown per file, others continue
  await page.route("**/api/v1/slips/batches/*/files", (r) => r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Internal server error" }) }));
  await page.getByLabel("Payment slip files").setInputFiles(paths([TEN[0]]));
  await expect(page.getByLabel("Per-file status")).toContainText("Failed: ");
  await page.unroute("**/api/v1/slips/batches/*/files");
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Cancel batch" }).click();

  // OCR engine missing on the server: clear message, uploads disabled
  await page.route("**/api/v1/slips/status", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ available: false, engine: null, reason: "Tesseract binary not found", limits: { max_file_bytes: 5242880, max_files_per_batch: 20, max_pdf_pages: 5, allowed_extensions: ["jpg"], batch_expiry_hours: 24, low_confidence_below: 60 } }) }));
  await page.reload();
  await expect(page.getByText(/OCR engine is not available on this server \(Tesseract binary not found\)/)).toBeVisible();
  await expect(page.getByLabel("Payment slip files")).toBeDisabled();
  await page.unroute("**/api/v1/slips/status");
});
