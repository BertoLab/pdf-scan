import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const url = process.argv[2] ?? 'http://127.0.0.1:8770'
const screenshots = path.join(root, 'tmp', 'ui-checks', 'invoice-amounts')
await fs.mkdir(screenshots, { recursive: true })
const browser = await chromium.launch()

try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1100, height: 800 }]) {
    const page = await browser.newPage({ viewport })
    await page.goto(url, { waitUntil: 'networkidle' })
    const card = (name) => page.locator('.invoice-fields-panel li')
      .filter({ has: page.locator('.field-label-row', { hasText: name }) })
    const total = card('Totale fattura')
    const withholding = card('Ritenuta')
    const net = card('Totale netto fattura')
    const input = (field) => field.locator('input[data-field-index]')
    const suggestion = (field) => field.locator('.invoice-amount-suggestion')
    await input(total).waitFor()

    await input(total).fill('125.00')
    await input(withholding).fill('5.00')
    await input(net).fill('')
    assert.match(await suggestion(net).innerText(), /120\.00/)
    const inputWidth = (await input(net).boundingBox()).width
    const cardWidth = (await net.boundingBox()).width
    assert(inputWidth >= cardWidth * 0.7, 'Suggestion compressed the amount input')
    await net.screenshot({ path: path.join(screenshots, `suggestion-${viewport.width}.png`) })
    await suggestion(net).click()
    assert.equal(await input(net).inputValue(), '120.00')
    assert.equal(await page.locator('.economic-mismatch-field').count(), 0)

    await input(withholding).fill('5.01')
    assert.equal(await page.locator('.economic-mismatch-field').count(), 3)
    assert.equal(await input(total).getAttribute('aria-invalid'), 'true')
    assert.equal(await net.locator('.invoice-amount-mismatch-note').count(), 1)
    await net.screenshot({ path: path.join(screenshots, `mismatch-${viewport.width}.png`) })
    await input(withholding).fill('5.00')
    assert.equal(await page.locator('.economic-mismatch-field').count(), 0)

    await input(withholding).fill('')
    await input(net).fill('125.00')
    assert.match(await suggestion(withholding).innerText(), /0\.00/)
    await input(withholding).fill('5.00')
    await input(total).fill('')
    await input(net).fill('120.00')
    assert.match(await suggestion(total).innerText(), /125\.00/)

    await input(total).fill('5.00')
    await input(withholding).fill('')
    assert.equal(await suggestion(withholding).count(), 0, 'Negative withholding was suggested')
    await input(total).fill('abc')
    assert.equal(await suggestion(withholding).count(), 0, 'Malformed amount was used')
    assert.equal(await page.locator('.economic-mismatch-field').count(), 0)
    await page.close()
    console.log(`${viewport.width}x${viewport.height}: amount reconciliation OK`)
  }
} finally {
  await browser.close()
}
