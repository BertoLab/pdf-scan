import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const work = await fs.mkdtemp(path.join(root, 'build', 'close-check-'));
const data = path.join(work, 'data');
const screenshot = path.join(root, 'tmp', 'ui-checks', 'desktop-close.png');
const executable = path.join(root, 'build/desktop-dist/EstrattoreScanFatture/EstrattoreScanFatture.exe');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const keep = process.argv.includes('--keep');
let app, desktop, browser;

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function start() {
  const port = await freePort();
  app = spawn(executable, ['--data-dir', data], {
    windowsHide: true, stdio: 'ignore',
    env: { ...process.env, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}` },
  });
  for (let i = 0; i < 90; i++) {
    try {
      desktop = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
      const page = desktop.contexts().flatMap(c => c.pages()).find(p => p.url().includes('desktop=1'));
      if (page) {
        page.on('response', async response => {
          if (response.url().includes('/viewer/review-batches')) console.log('Review response', response.url(), response.status(), await response.text().catch(() => 'unavailable'));
        });
        page.on('pageerror', error => console.error('Page error', error.message));
        await page.waitForFunction(() => Boolean(window.estrattoreDesktop), { timeout: 30000 });
        return page;
      }
    } catch { /* Wait for the real native engine to start. */ }
    await sleep(500);
  }
  throw new Error('Native window did not become ready');
}

function requestNativeClose() {
  const script = "import sys,ctypes; from types import SimpleNamespace; " +
    "sys.path.insert(0, sys.argv[2]); from smoke_frozen import _wait_for_window; " +
    "h=_wait_for_window(SimpleNamespace(pid=int(sys.argv[1]),poll=lambda:None),include_hidden=True); " +
    "assert ctypes.windll.user32.PostMessageW(h,16,0,0)";
  return new Promise((resolve, reject) => {
    const command = spawn(path.join(root, '.venv/Scripts/python.exe'),
      ['-c', script, String(app.pid), path.join(root, 'packaging')], { windowsHide: true });
    let stderr = '';
    command.stderr.on('data', chunk => { stderr += chunk; });
    command.on('exit', code => code === 0 ? resolve() : reject(new Error(stderr)));
  });
}

async function waitForExit() {
  for (let i = 0; i < 120; i++) {
    if (app.exitCode !== null) {
      if (app.exitCode !== 0) throw new Error(`Native exit ${app.exitCode}`);
      return;
    }
    await sleep(250);
  }
  throw new Error('Native close did not finish');
}

try {
  browser = await chromium.launch();
  const fixture = await browser.newPage();
  await fixture.setContent('<html><body style="font:24px Arial"><h1>FATTURA N. 123</h1><p>Data documento 01/10/2026</p><p>Totale fattura 100,00</p></body></html>');
  const pdf = await fixture.pdf({ format: 'A4' });
  let page = await start();
  const imported = await page.evaluate(async encoded => {
    const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
    const body = new FormData();
    body.append('files', new Blob([bytes], { type: 'application/pdf' }), 'test.pdf');
    return (await fetch('/batches/import', { method: 'POST', body })).json();
  }, pdf.toString('base64'));
  const job = await page.evaluate(async id => (await fetch('/batches/process', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ batch_id: id }),
  })).json(), imported.batch.batch_id);
  const deadline = Date.now() + 180000;
  while (true) {
    const status = await page.evaluate(async id =>
      (await fetch(`/processing/jobs/${id}`)).json(), job.job_id);
    if (['error', 'cancelled'].includes(status.status)) throw new Error(JSON.stringify(status));
    if (status.status === 'completed') break;
    if (Date.now() > deadline) throw new Error('OCR timeout');
    await sleep(500);
  }
  await page.reload();
  await page.getByLabel('Tutti i campi', { exact: true }).check();
  const input = page.locator('.invoice-fields-panel li').filter({ hasText: 'Numero fattura' }).locator('input');
  await input.waitFor({ timeout: 30000 });
  await page.route('**/viewer/drafts', route =>
    route.request().method() === 'POST' ? route.abort() : route.continue());
  await input.fill('REJECT-CLOSE');
  const rejected = await page.evaluate(() => window.estrattoreDesktop.prepareClose());
  if (rejected.ok) throw new Error('Failed draft write incorrectly approved close');
  await page.evaluate(() => window.estrattoreDesktop.cancelClose());
  await page.unroute('**/viewer/drafts');
  await page.route('**/viewer/drafts', async route => {
    if (route.request().method() === 'POST') await sleep(3000);
    await route.continue();
  });
  await input.fill('CLOSE-TEST-987');
  const close = requestNativeClose();
  close.catch(() => {}); // Attach immediately while waiting for the closing UI.
  await page.getByText('Chiusura in corso', { exact: true }).waitFor({ timeout: 15000 });
  await fs.mkdir(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot });
  await close;
  await waitForExit();
  const drafts = JSON.parse(await fs.readFile(path.join(data, 'manifests', '_review_drafts.json'), 'utf8'));
  if (!JSON.stringify(drafts).includes('CLOSE-TEST-987')) throw new Error('Draft lost on close');
  page = await start();
  await page.locator('.invoice-fields-panel li').filter({ hasText: 'Numero fattura' }).locator('input').waitFor();
  const restored = await page.locator('.invoice-fields-panel li').filter({ hasText: 'Numero fattura' }).locator('input').inputValue();
  if (restored !== 'CLOSE-TEST-987') throw new Error(`Wrong reopened value: ${restored}`);
  await requestNativeClose();
  await waitForExit();
  console.log('Frozen OCR -> failed draft refuses close -> slow draft -> native close -> reopen with same value: OK');
} catch (error) {
  const page = desktop?.contexts().flatMap(c => c.pages()).find(p => p.url().includes('desktop=1'));
  if (page) {
    console.error(await page.locator('body').innerText());
    console.error(JSON.stringify(await page.evaluate(async () => ({
      jobs: await (await fetch('/processing/jobs')).json(),
      batches: await (await fetch('/viewer/review-batches')).json(),
    })), null, 2));
    await fs.mkdir(path.dirname(screenshot), { recursive: true });
    await page.screenshot({ path: screenshot });
  }
  throw error;
} finally {
  if (app && app.exitCode === null) spawnSync('taskkill', ['/PID', String(app.pid), '/T', '/F'], { windowsHide: true });
  await browser?.close();
  if (!keep) await fs.rm(screenshot, { force: true });
  await sleep(1500);
  if (!work.startsWith(path.join(root, 'build') + path.sep)) throw new Error('Unsafe cleanup path');
  await fs.rm(work, { recursive: true, force: true, maxRetries: 10, retryDelay: 500 });
}
