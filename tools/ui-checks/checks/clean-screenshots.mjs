import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const screenshotDir = path.join(projectRoot, "tmp", "ui-checks");

await fs.rm(screenshotDir, { recursive: true, force: true });
await fs.mkdir(screenshotDir, { recursive: true });

console.log(`Screenshot temporanei cancellati: ${screenshotDir}`);
