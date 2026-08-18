import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const screenshotDir = path.join(projectRoot, "tmp", "ui-checks");

const args = parseArgs(process.argv.slice(2));
const appUrl = args.url ?? "http://127.0.0.1:8765";
const scope = args.scope ?? "modifica-ui";
const keepScreenshots = Boolean(args.keep);
const clickTargets = args.click ?? [];

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "wide", width: 1728, height: 972 }
];

const checks = [
  {
    name: "app-iniziale",
    description: "Stato iniziale raggiungibile dell'app",
    run: async (page) => {
      await page.goto(appUrl, { waitUntil: "networkidle" });
    }
  },
  ...clickTargets.map((target, index) => ({
    name: `click-${index + 1}-${target}`,
    description: `Stato dopo click su "${target}"`,
    run: async (page) => {
      await page.getByText(target, { exact: true }).first().click();
    }
  })),
  {
    name: "stato-finale",
    description: "Stato finale dopo le interazioni richieste",
    run: async () => {}
  }
];

await fs.rm(screenshotDir, { recursive: true, force: true });
await fs.mkdir(screenshotDir, { recursive: true });

const producedScreenshots = [];
const browser = await chromium.launch();

try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });

    for (const check of checks) {
      await check.run(page);
      await page.waitForTimeout(250);

      const fileName = safeFileName(`${scope}-${check.name}-${viewport.name}.png`);
      const screenshotPath = path.join(screenshotDir, fileName);
      await page.screenshot({ path: screenshotPath, fullPage: true });
      producedScreenshots.push({
        path: screenshotPath,
        viewport: viewport.name,
        check: check.description
      });
    }

    await page.close();
  }

  console.log("Controlli UI eseguiti:");
  for (const item of producedScreenshots) {
    console.log(`- ${item.viewport}: ${item.check}`);
    console.log(`  ${item.path}`);
  }
} finally {
  await browser.close();

  if (!keepScreenshots) {
    await fs.rm(screenshotDir, { recursive: true, force: true });
    await fs.mkdir(screenshotDir, { recursive: true });
    console.log("Screenshot temporanei cancellati a fine controllo.");
  } else {
    console.log("Screenshot mantenuti temporaneamente su richiesta esplicita.");
  }
}

function parseArgs(rawArgs) {
  const parsed = { click: [] };

  for (let index = 0; index < rawArgs.length; index += 1) {
    const arg = rawArgs[index];
    if (arg === "--keep") {
      parsed.keep = true;
      continue;
    }

    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const value = rawArgs[index + 1];
      if (value && !value.startsWith("--")) {
        if (key === "click") {
          parsed.click.push(value);
        } else {
          parsed[key] = value;
        }
        index += 1;
      }
    }
  }

  return parsed;
}

function safeFileName(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9.-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
