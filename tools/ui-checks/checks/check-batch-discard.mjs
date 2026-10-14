import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const baseUrl = process.argv[2] ?? "http://127.0.0.1:4187";
const screenshotRoot = path.join(root, "tmp", "ui-checks");
await fs.mkdir(screenshotRoot, { recursive: true });
const screenshotDir = await fs.mkdtemp(path.join(screenshotRoot, "batch-discard-"));
const browser = await chromium.launch();

const invoiceManifest = {
  schema_version: 1,
  source_file: "fatture-test.pdf",
  pages: [{ page: 1, width: 800, height: 1100, image: "", tokens: [], spans: [], targets: [] }],
  invoices: [1, 2].map((number) => ({
    invoice_id: `invoice-${String(number).padStart(3, "0")}`,
    title: `Fattura ${String(number).padStart(3, "0")}`,
    page_numbers: [1],
    fields: [],
  })),
};

async function runScenario(name, viewport, failSecond) {
  const page = await browser.newPage({ viewport });
  let completed = 0;
  let discardCalls = 0;
  let releaseFirst;
  const firstDiscardHeld = new Promise((resolve) => { releaseFirst = resolve; });
  const json = (route, payload, status = 200) => route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });

  await page.route("**/*", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "POST" && pathname === "/invoices/discard") {
      discardCalls += 1;
      if (discardCalls === 1) await firstDiscardHeld;
      if (failSecond && discardCalls === 2) {
        await json(route, { error: "Scarto non riuscito" }, 500);
      } else {
        completed += 1;
        await json(route, { ok: true });
      }
      return;
    }
    if (pathname === "/viewer/drafts") return json(route, { drafts: [] });
    if (pathname === "/viewer/review-batches") return json(route, {
      batches: completed === 2 ? [] : [{
        id: "review_batch_0001", label: "Revisione 001",
        base_path: "/runtime/manifests/review_batch_0001", invoice_count: 2 - completed,
      }],
    });
    if (pathname.endsWith("/viewer_index.json")) return json(route, {
      documents: [{
        id: "doc_000001",
        base_path: "/runtime/manifests/process_test/documents/doc_000001",
        review_invoice_ids: completed === 0
          ? ["invoice-001", "invoice-002"] : ["invoice-002"],
      }],
    });
    if (pathname.endsWith("/viewer_manifest.json")) return json(route, invoiceManifest);
    if (pathname === "/processing/jobs") return json(route, { jobs: [] });
    if (pathname === "/registry/status") return json(route, { ok: true, has_file: false });
    if (pathname === "/registry/records") return json(route, { ok: true, records: [] });
    return route.continue();
  });

  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Scarta gruppo" }).first().click();
    await page.locator(".batch-review-action-dialog").getByRole("button", { name: "Scarta gruppo" }).click();
    const progress = page.getByRole("alertdialog", { name: "Scarto del gruppo in corso" });
    await progress.waitFor();
    assert.match(await progress.innerText(), /Stiamo eliminando i record delle fatture/);
    await page.keyboard.press("Escape");
    assert.equal(await progress.isVisible(), true);
    assert.equal(await page.getByRole("button", { name: "Scarta gruppo" }).isDisabled(), true);
    assert.equal(discardCalls, 1);
    const bar = progress.locator(".segmentation-progress-bar span");
    await page.waitForTimeout(450);
    const firstBarPosition = await bar.evaluate((element) => getComputedStyle(element).transform);
    await page.waitForTimeout(160);
    const secondBarPosition = await bar.evaluate((element) => getComputedStyle(element).transform);
    assert.notEqual(firstBarPosition, secondBarPosition);
    await page.screenshot({ path: path.join(screenshotDir, `${name}-progress.png`) });
    releaseFirst();
    await progress.waitFor({ state: "hidden" });
    assert.equal(discardCalls, 2);
    if (failSecond) {
      await page.getByText(/Operazione sul gruppo non completata/).waitFor();
      await page.screenshot({ path: path.join(screenshotDir, `${name}-error.png`) });
    } else {
      await page.getByText("2 fatture spostate tra le scartate.").waitFor();
    }
  } finally {
    releaseFirst();
    await page.close();
  }
}

try {
  await runScenario("desktop-success", { width: 1440, height: 900 }, false);
  await runScenario("compact-success", { width: 1024, height: 768 }, false);
  await runScenario("desktop-partial-failure", { width: 1440, height: 900 }, true);
  console.log(`Batch discard UI check passed; screenshots: ${screenshotDir}`);
} finally {
  await browser.close();
  if (!process.argv.includes("--keep")) await fs.rm(screenshotDir, { recursive: true, force: true });
}
