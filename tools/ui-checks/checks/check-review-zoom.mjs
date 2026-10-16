import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const url = process.argv[2] ?? "http://127.0.0.1:8770";
const keepScreenshots = process.argv.includes("--keep");
const screenshots = path.join(root, "tmp", "ui-checks", "review-zoom");

await fs.mkdir(screenshots, { recursive: true });
const browser = await chromium.launch();

try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1100, height: 800 }]) {
    const page = await browser.newPage({ viewport });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator(".document-page").first().waitFor();

    const geometry = await page.evaluate(() => {
      const viewportElement = document.querySelector(".document-viewport");
      const pageElement = document.querySelector(".document-page");
      const zoom = document.querySelector("input[aria-label='Percentuale zoom']");
      const view = viewportElement.getBoundingClientRect();
      const documentPage = pageElement.getBoundingClientRect();
      return {
        zoom: Number(zoom.value),
        visible: documentPage.left >= view.left && documentPage.right <= view.right &&
          documentPage.top >= view.top && documentPage.bottom <= view.bottom,
      };
    });
    assert(geometry.visible, `Page is not fully visible at ${viewport.width}px: ${JSON.stringify(geometry)}`);
    await page.screenshot({ path: path.join(screenshots, `fit-${viewport.width}.png`) });

    const zoom = page.getByRole("spinbutton", { name: "Percentuale zoom" });
    const fields = page.locator(".invoice-fields-panel li input[data-field-index]");
    let evidenceField = null;
    for (let index = 0; index < await fields.count(); index += 1) {
      await fields.nth(index).click();
      if (Number(await zoom.inputValue()) === 140) {
        evidenceField = index;
        break;
      }
    }
    assert.notEqual(evidenceField, null, "Demo review has no field with OCR evidence");

    await zoom.fill("75");
    await zoom.press("Enter");
    await page.getByRole("button", { name: "Blocca zoom automatico" }).click();
    assert.equal(await page.getByRole("button", { name: "Sblocca zoom automatico" }).getAttribute("aria-pressed"), "true");
    await page.locator(".document-viewport").evaluate((element) => { element.scrollTop = 180; });
    const scrollBeforeEvidenceField = await page.locator(".document-viewport")
      .evaluate((element) => element.scrollTop);
    await fields.nth(evidenceField).click();
    await page.waitForTimeout(600);
    assert.equal(Number(await zoom.inputValue()), 75, "Locked evidence focus changed zoom");
    assert.equal(await page.locator(".document-viewport").evaluate((element) => element.scrollTop),
      scrollBeforeEvidenceField, "Locked evidence focus moved the document");

    await page.locator(".document-viewport").evaluate((element) => { element.scrollTop = 180; });
    const scrollBeforeEmptyField = await page.locator(".document-viewport")
      .evaluate((element) => element.scrollTop);
    const emptyField = page.locator(".invoice-fields-panel li")
      .filter({ has: page.locator(".field-label-row", { hasText: "Pagata il" }) })
      .locator("input[data-field-index]");
    await emptyField.click();
    assert.equal(Number(await zoom.inputValue()), 75, "Locked empty field reset zoom");
    assert.equal(await page.locator(".document-viewport").evaluate((element) => element.scrollTop), scrollBeforeEmptyField,
      "Locked empty field reset document position");
    await page.screenshot({ path: path.join(screenshots, `locked-${viewport.width}.png`) });

    await page.getByRole("button", { name: "Ripristina", exact: true }).first().click();
    assert.equal(Number(await zoom.inputValue()), geometry.zoom, "Manual reset did not work while locked");
    await fields.nth(evidenceField).click();
    assert.equal(Number(await zoom.inputValue()), geometry.zoom, "Locked evidence focus overrode manual reset");

    await page.getByRole("button", { name: "Sblocca zoom automatico" }).click();
    await fields.nth(evidenceField).click();
    assert.equal(Number(await zoom.inputValue()), 140, "Unlocked evidence focus did not restore automatic zoom");
    await page.getByRole("button", { name: "Ripristina", exact: true }).first().click();
    assert.equal(Number(await zoom.inputValue()), geometry.zoom, "Reset did not restore page fit");
    await zoom.fill("15");
    await zoom.press("Enter");
    const documentViewport = await page.locator(".document-viewport").boundingBox();
    await page.mouse.move(documentViewport.x + documentViewport.width / 2,
      documentViewport.y + documentViewport.height / 2);
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, 100);
    await page.keyboard.up("Control");
    await page.waitForFunction(() => document.querySelector("input[aria-label='Percentuale zoom']")?.value === "10");
    assert.equal(Number(await zoom.inputValue()), 10, "Ctrl+wheel cannot reach the shared minimum");
    console.log(`${viewport.width}x${viewport.height}: fit ${geometry.zoom}%, locked/manual and unlocked focus OK`);
    await page.close();
  }

  const desktopUrl = new URL(url);
  desktopUrl.searchParams.set("desktop", "1");
  const desktopPage = await browser.newPage();
  await desktopPage.goto(desktopUrl.href, { waitUntil: "networkidle" });
  await desktopPage.getByRole("button", { name: "Blocca zoom automatico" }).waitFor();
  const desktopZoom = desktopPage.getByRole("spinbutton", { name: "Percentuale zoom" });
  const evidenceInput = desktopPage.locator(".invoice-fields-panel li")
    .filter({ has: desktopPage.locator(".field-label-row", { hasText: "Numero fattura" }) })
    .locator("input[data-field-index]");
  const viewportState = () => desktopPage.locator(".document-viewport").evaluate((element) => ({
    top: element.scrollTop,
    left: element.scrollLeft,
    pageWidth: element.querySelector(".document-page")?.getBoundingClientRect().width,
  }));
  await desktopZoom.fill("75");
  await desktopZoom.press("Enter");
  await desktopPage.getByRole("button", { name: "Blocca zoom automatico" }).click();
  await desktopPage.locator(".document-viewport").evaluate((element) => { element.scrollTop = 180; });
  const beforeClick = await viewportState();
  await evidenceInput.click();
  await desktopPage.waitForTimeout(700);
  assert.equal(Number(await desktopZoom.inputValue()), 75, "Desktop lock changed the zoom value");
  assert.deepEqual(await viewportState(), beforeClick, "Desktop lock moved the document viewport");
  await desktopPage.getByRole("button", { name: "Sblocca zoom automatico" }).click();
  await desktopPage.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", {
    key: "l", ctrlKey: true, bubbles: true, cancelable: true,
  })));
  assert.equal(await desktopPage.getByRole("button", { name: "Sblocca zoom automatico" })
    .getAttribute("aria-pressed"), "true", "Desktop Ctrl+L handler did not toggle lock");
  await desktopPage.locator(".document-viewport").evaluate((element) => { element.scrollTop = 180; });
  const beforeShortcutClick = await viewportState();
  await evidenceInput.click();
  await desktopPage.waitForTimeout(700);
  assert.equal(Number(await desktopZoom.inputValue()), 75, "Ctrl+L lock changed the zoom value");
  assert.deepEqual(await viewportState(), beforeShortcutClick, "Ctrl+L lock moved the document viewport");
  await desktopPage.close();
  console.log("Desktop-mode lock preserves zoom and scroll (synthetic Ctrl+L event, not native WebView routing)");
} finally {
  await browser.close();
  if (!keepScreenshots) await fs.rm(screenshots, { recursive: true, force: true });
}
