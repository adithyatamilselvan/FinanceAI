import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

// Regression: a tall modal (the slip edit form) must scroll inside itself on short screens, with the header and the Cancel / Save
// buttons always reachable, while the page behind it stays locked. Checked by mouse wheel, keyboard and touch-style scrolling.
const DIR = path.resolve(process.cwd(), "..", "docs", "test-data", "payment-slips");
const FILE = "slip_05_card_apollo.jpg";

test.describe.configure({ mode: "serial" });
let page: Page;
test.beforeAll(async ({ browser }) => { page = await (await browser.newContext({ hasTouch: true })).newPage(); });
test.afterAll(async () => { await page.context().close(); });

const SIZES = [
  { name: "short laptop", width: 1280, height: 480 },
  { name: "landscape phone", width: 844, height: 390 },
  { name: "portrait phone", width: 390, height: 600 },
  { name: "tall desktop", width: 1440, height: 1000 },
];

const dialog = () => page.getByRole("dialog");
const scroller = () => page.getByTestId("modal-scroll");
const metrics = () => page.evaluate(() => {
  const d = document.querySelector("[role=dialog]")!.getBoundingClientRect();
  const s = document.querySelector("[data-testid=modal-scroll]") as HTMLElement;
  const overlay = document.querySelector("[role=dialog]")!.parentElement!;
  const save = [...document.querySelectorAll("[role=dialog] button")].find((b) => b.textContent?.includes("Save"))!.getBoundingClientRect();
  const cancel = [...document.querySelectorAll("[role=dialog] button")].find((b) => b.textContent?.trim() === "Cancel")!.getBoundingClientRect();
  const h = document.querySelector("[role=dialog] h2")!.getBoundingClientRect();
  return {
    vh: window.innerHeight, vw: window.innerWidth, top: d.top, bottom: d.bottom, left: d.left, right: d.right,
    scrollTop: s.scrollTop, scrollHeight: s.scrollHeight, clientHeight: s.clientHeight,
    bodyOverflow: getComputedStyle(document.body).overflow, pageY: window.scrollY, overlayPos: getComputedStyle(overlay).position,
    saveBottom: save.bottom, saveTop: save.top, cancelBottom: cancel.bottom, headTop: h.top, headBottom: h.bottom,
  };
});

test("setup: register and read one slip", async () => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Name").fill("Modal Scroll");
  await page.getByLabel("Email").fill(`modal-${Date.now()}@example.com`);
  await page.getByLabel(/^Password/).fill("Passw0rd!x");
  await page.getByRole("button", { name: "Create account" }).last().click();
  await page.getByRole("link", { name: "Payment Slip Scanner" }).click();
  await page.getByLabel("Payment slip files").setInputFiles(path.join(DIR, FILE));
  await expect(page.getByRole("button", { name: `Edit ${FILE}` })).toBeVisible({ timeout: 60_000 });
});

for (const sz of SIZES) {
  test(`edit dialog scrolls inside itself and stays reachable at ${sz.name} (${sz.width}x${sz.height})`, async () => {
    await page.setViewportSize({ width: sz.width, height: sz.height });
    await page.reload();
    await page.getByRole("button", { name: `Edit ${FILE}` }).click();
    await expect(dialog()).toBeVisible();
    await page.waitForTimeout(300); // let the 160ms fade-in finish so geometry is measured at rest
    const m0 = await metrics();

    // the overlay really is fixed to the viewport, and the dialog never extends beyond it
    expect(m0.overlayPos, "overlay must stay position:fixed").toBe("fixed");
    expect(m0.top, "dialog top inside viewport").toBeGreaterThanOrEqual(0);
    expect(m0.bottom, "dialog bottom inside viewport").toBeLessThanOrEqual(m0.vh + 0.5);
    expect(m0.left).toBeGreaterThanOrEqual(0);
    expect(m0.right).toBeLessThanOrEqual(m0.vw + 0.5);
    expect(m0.bottom - m0.top, "max height about 90dvh").toBeLessThanOrEqual(m0.vh * 0.9 + 1);
    expect(m0.bodyOverflow, "page behind is locked").toBe("hidden");

    // header and the action buttons are visible WITHOUT scrolling
    expect(m0.headTop).toBeGreaterThanOrEqual(0);
    expect(m0.saveBottom, "Save visible at once").toBeLessThanOrEqual(m0.vh + 0.5);
    expect(m0.cancelBottom, "Cancel visible at once").toBeLessThanOrEqual(m0.vh + 0.5);

    const needsScroll = m0.scrollHeight > m0.clientHeight + 1;
    if (sz.height <= 700) expect(needsScroll, "the form is taller than this screen, so it must scroll internally").toBe(true);

    if (needsScroll) {
      // mouse wheel / touchpad over the dialog scrolls the dialog, not the page
      await page.mouse.move(m0.left + 60, m0.top + 200 < m0.vh ? m0.top + 200 : m0.vh / 2);
      await page.mouse.wheel(0, 300);
      await expect.poll(async () => (await metrics()).scrollTop, "wheel scrolls the modal").toBeGreaterThan(0);
      const m1 = await metrics();
      expect(m1.pageY, "page behind did not scroll").toBe(m0.pageY);
      expect(m1.headTop, "header stays visible while scrolling").toBeGreaterThanOrEqual(0);
      expect(m1.headBottom).toBeGreaterThan(0);
      expect(m1.saveBottom, "Save stays visible while scrolling").toBeLessThanOrEqual(m1.vh + 0.5);

      // can reach the very bottom and back to the top
      await page.mouse.wheel(0, 3000);
      await expect.poll(async () => { const m = await metrics(); return m.scrollTop + m.clientHeight >= m.scrollHeight - 1; }).toBe(true);
      await page.mouse.wheel(0, -3000);
      await expect.poll(async () => (await metrics()).scrollTop).toBe(0);

      // keyboard: with the scroll area focused, each key is tested from a KNOWN position where it must move the dialog.
      // (Chrome animates keyboard scrolling, and on a short form PageDown can already reach the bottom, so keys are never chained.)
      // keyboard scrolling is animated: wait until the position stops changing before resetting it, or the animation overwrites the reset
      const settle = async () => { let last = -1; await expect.poll(async () => { const v = (await metrics()).scrollTop; const stable = v === last; last = v; return stable; }, { intervals: [150] }).toBe(true); };
      const setTop = async (where: "top" | "bottom") => { await settle(); await page.evaluate((w) => { const e = document.querySelector("[data-testid=modal-scroll]") as HTMLElement; e.scrollTop = w === "top" ? 0 : e.scrollHeight; }, where); };
      const atBottom = async () => { const m = await metrics(); return m.scrollTop + m.clientHeight >= m.scrollHeight - 1; };
      await scroller().focus();
      await setTop("top");
      await expect.poll(async () => (await metrics()).scrollTop).toBe(0);
      await page.keyboard.press("PageDown");
      await expect.poll(async () => (await metrics()).scrollTop, "PageDown from the top").toBeGreaterThan(0);
      await setTop("top");
      await expect.poll(async () => (await metrics()).scrollTop).toBe(0);
      await page.keyboard.press("ArrowDown");
      await expect.poll(async () => (await metrics()).scrollTop, "ArrowDown from the top").toBeGreaterThan(0);
      await setTop("top");
      await expect.poll(async () => (await metrics()).scrollTop).toBe(0);
      await page.keyboard.press("End");
      await expect.poll(atBottom, "End reaches the bottom").toBe(true);
      // at the very bottom nothing may be hidden behind the sticky footer: the last control ends above the footer
      const clear = await page.evaluate(() => {
        const dlg = document.querySelector("[role=dialog]")!;
        const footer = dlg.querySelector(".modal-footer")!.getBoundingClientRect();
        const last = [...dlg.querySelectorAll("form input, form select, form label")].filter((e) => !e.closest(".modal-footer")).map((e) => e.getBoundingClientRect().bottom).sort((x, y) => y - x)[0];
        return { lastBottom: last, footerTop: footer.top };
      });
      expect(clear.lastBottom, "last field is not covered by the sticky footer").toBeLessThanOrEqual(clear.footerTop + 0.5);
      await setTop("bottom");
      await expect.poll(atBottom).toBe(true);
      const bottomTop = (await metrics()).scrollTop;
      await page.keyboard.press("PageUp");
      await expect.poll(async () => (await metrics()).scrollTop, "PageUp from the bottom").toBeLessThan(bottomTop);
      await setTop("bottom");
      await expect.poll(atBottom).toBe(true);
      await page.keyboard.press("ArrowUp");
      await expect.poll(async () => (await metrics()).scrollTop, "ArrowUp from the bottom").toBeLessThan(bottomTop);
      await setTop("bottom");
      await expect.poll(atBottom).toBe(true);
      await page.keyboard.press("Home");
      await expect.poll(async () => (await metrics()).scrollTop, "Home from the bottom").toBe(0);

      // Tab to the last field brings it into view inside the dialog (and the page still did not move)
      await dialog().getByLabel("Description").focus();
      const m2 = await metrics();
      const d = await page.evaluate(() => { const e = document.activeElement!.getBoundingClientRect(); const s = document.querySelector("[data-testid=modal-scroll]")!.getBoundingClientRect(); return { top: e.top, bottom: e.bottom, sTop: s.top, sBottom: s.bottom }; });
      expect(d.top).toBeGreaterThanOrEqual(d.sTop - 1);
      expect(d.bottom).toBeLessThanOrEqual(d.sBottom + 1);
      expect(m2.pageY).toBe(m0.pageY);

      // touch-style drag scrolls the dialog too
      await page.evaluate(() => { (document.querySelector("[data-testid=modal-scroll]") as HTMLElement).scrollTop = 0; });
      const cdp = await page.context().newCDPSession(page);
      const x = Math.round(m0.left + (m0.right - m0.left) / 2), y1 = Math.round(Math.min(m0.vh - 140, m0.top + 260)), y0 = Math.max(y1 - 220, Math.round(m0.top + 90));
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: y1 }] });
      for (let i = 1; i <= 12; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y1 - Math.round(((y1 - y0) * i) / 12) }] });
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await expect.poll(async () => (await metrics()).scrollTop, "touch drag").toBeGreaterThan(0);
      expect((await metrics()).pageY).toBe(m0.pageY);
    }

    // existing behaviour preserved: Escape closes, the page unlocks, focus returns to the Edit button
    await page.keyboard.press("Escape");
    await expect(dialog()).toHaveCount(0);
    await expect(page.getByRole("button", { name: `Edit ${FILE}` })).toBeFocused();
    expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe("hidden");
  });
}

test("focus trap still holds while the dialog scrolls", async () => {
  await page.setViewportSize({ width: 1280, height: 480 });
  await page.reload();
  await page.getByRole("button", { name: `Edit ${FILE}` }).click();
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]")), `tab ${i}`).toBe(true);
  }
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Shift+Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]")), `shift-tab ${i}`).toBe(true);
  }
  await page.keyboard.press("Escape");
});

test("the page behind scrolls normally again after the dialog closes", async () => {
  await page.setViewportSize({ width: 1280, height: 480 });
  await page.reload();
  await page.getByRole("button", { name: `Edit ${FILE}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await page.mouse.move(700, 300);
  await page.mouse.wheel(0, 400);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
});

test("other tall dialogs (Add transaction, Create budget) scroll the same way", async () => {
  await page.setViewportSize({ width: 390, height: 500 });
  for (const [route, button] of [["/transactions", "Add transaction"], ["/budgets", "Create budget"]] as const) {
    await page.goto(route);
    await page.getByRole("button", { name: button }).click();
    await page.waitForTimeout(300);
    const m = await metrics();
    expect(m.overlayPos).toBe("fixed");
    expect(m.bottom).toBeLessThanOrEqual(m.vh + 0.5);
    expect(m.saveBottom, `${button}: Save reachable`).toBeLessThanOrEqual(m.vh + 0.5);
    await page.keyboard.press("Escape");
  }
});
