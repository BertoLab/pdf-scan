import fs from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const executable = path.join(root, "build", "desktop-dist", "EstrattoreScanFatture", "EstrattoreScanFatture.exe");
const dataOption = process.argv.indexOf("--data-dir");
if (dataOption < 0 || !process.argv[dataOption + 1]) {
  throw new Error("Pass --data-dir with an isolated app-data copy under build/");
}
const data = path.resolve(root, process.argv[dataOption + 1]);
if (!data.startsWith(path.join(root, "build") + path.sep)) {
  throw new Error("The parity check may only use isolated data under build/");
}
const output = path.join(root, "tmp", "ui-checks", "desktop-parity");
const keep = process.argv.includes("--keep");

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function connectDesktop(port) {
  let lastError;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      return await chromium.connectOverCDP("http://127.0.0.1:" + port);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw lastError;
}

async function waitForAppPage(browser) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const page = browser.contexts().flatMap((context) => context.pages())
      .find((candidate) => candidate.url().includes("desktop=1"));
    if (page) return page;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Desktop app page was not exposed through WebView2 CDP");
}

async function measurements(page) {
  return page.evaluate(() => {
    const rect = (selector) => {
      const element = document.querySelector(selector);
      if (!element) return null;
      const bounds = element.getBoundingClientRect();
      return { x: Math.round(bounds.x), y: Math.round(bounds.y), width: Math.round(bounds.width), height: Math.round(bounds.height) };
    };
    return {
      viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      scrollWidth: document.documentElement.scrollWidth,
      main: rect("main"), sidebar: rect("main > aside"), content: rect("main > section"),
      fields: rect(".invoice-fields-panel"),
      documentOverflow: (() => {
        const element = document.querySelector(".document-viewport");
        return element ? element.scrollWidth - element.clientWidth : null;
      })(),
      zoom: (() => {
        const input = document.querySelector("input[aria-label='Percentuale zoom']");
        return input ? { value: Number(input.value), min: Number(input.min), valid: input.validity.valid } : null;
      })(),
      title: document.title,
    };
  });
}

async function main() {
  await fs.access(executable);
  await fs.access(data);
  if (!output.startsWith(path.join(root, "tmp") + path.sep)) throw new Error("Unsafe screenshot directory");
  await fs.mkdir(output, { recursive: true });
  const port = await freePort();
  const app = spawn(executable, ["--data-dir", data], {
    env: { ...process.env, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: "--remote-debugging-port=" + port },
    windowsHide: true,
    stdio: "ignore",
  });
  let desktopBrowser;
  let browser;
  try {
    desktopBrowser = await connectDesktop(port);
    const desktop = await waitForAppPage(desktopBrowser);
    await desktop.waitForSelector("main > aside", { timeout: 30000 });
    const desktopGeometry = await measurements(desktop);
    browser = await chromium.launch();
    const web = await browser.newPage({
      viewport: { width: desktopGeometry.viewport.width, height: desktopGeometry.viewport.height },
      deviceScaleFactor: desktopGeometry.viewport.dpr,
    });
    await web.goto(desktop.url().replace(/[?&]desktop=1/, ""), { waitUntil: "domcontentloaded" });
    await web.waitForSelector("main > aside");
    await desktop.waitForTimeout(1500);
    if (await desktop.locator(".page-list button").count() === 0) {
      const options = await desktop.locator(".batch-select select option").evaluateAll((nodes) => nodes.map((node) => node.value));
      for (const option of options.slice().reverse()) {
        await desktop.locator(".batch-select select").selectOption(option);
        await web.locator(".batch-select select").selectOption(option);
        await desktop.waitForTimeout(700);
        if (await desktop.locator(".page-list button").count() > 0) break;
      }
    }
    process.stdout.write("review invoice count " + await desktop.locator(".page-list button").count() + "\n");
    const report = {};
    async function capture(name) {
      await Promise.all([desktop.waitForTimeout(250), web.waitForTimeout(250)]);
      await desktop.screenshot({ path: path.join(output, name + "-desktop.png") });
      await web.screenshot({ path: path.join(output, name + "-browser.png") });
      report[name] = { desktop: await measurements(desktop), browser: await measurements(web) };
      process.stdout.write(name + " " + JSON.stringify(report[name]) + "\n");
    }
    async function clickBoth(role, name) {
      await desktop.getByRole(role, { name, exact: true }).click();
      await web.getByRole(role, { name, exact: true }).click();
    }
    async function openArchive(item) {
      for (const page of [desktop, web]) {
        await page.getByRole("button", { name: "Archivi", exact: true }).click();
        await page.locator(item === "archive" ? ".mystudio-tool-item-green" : ".mystudio-tool-item-blue").click();
      }
    }
    await capture("review");
    if (report.review.desktop.documentOverflow > 2 || report.review.browser.documentOverflow > 2) {
      throw new Error("Invoice page overflows horizontally at initial zoom");
    }
    if (!report.review.desktop.zoom.valid || !report.review.browser.zoom.valid) {
      throw new Error("Automatic invoice zoom is invalid in the zoom input");
    }
    if (await desktop.locator(".page-list button").count() > 1) {
      await desktop.locator(".page-list button").nth(1).click();
      await web.locator(".page-list button").nth(1).click();
      await capture("review-next-invoice");
      if (report["review-next-invoice"].desktop.documentOverflow > 2) {
        throw new Error("The next invoice does not fit the document viewport");
      }
    }
    await clickBoth("button", "Processi");
    await capture("processes-active");
    await clickBoth("tab", "Cronologia");
    await capture("processes-history");
    await clickBoth("button", "Torna alle fatture");
    await openArchive("archive");
    await capture("archive-invoices");
    await clickBoth("tab", "File origine");
    await capture("archive-sources");
    await clickBoth("button", "Torna alle fatture");
    await openArchive("registry");
    await capture("registry-saved");
    await clickBoth("tab", "Fatture esportate");
    await capture("registry-exported");
    await clickBoth("tab", "Fatture scartate");
    await capture("registry-discarded");
    await clickBoth("tab", "Storico esportazioni");
    await capture("registry-history");
    await clickBoth("button", "Torna alle fatture");
    await clickBoth("button", "Anagrafica clienti");
    await capture("customer-registry");
    await fs.writeFile(path.join(output, "metrics.json"), JSON.stringify(report, null, 2));
  } finally {
    if (browser) await browser.close();
    if (app.exitCode === null) {
      spawnSync("taskkill", ["/PID", String(app.pid), "/T", "/F"], { windowsHide: true });
      await new Promise((resolve) => app.once("exit", resolve));
    }
    if (desktopBrowser) await desktopBrowser.close().catch(() => {});
    if (!keep) await fs.rm(output, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
