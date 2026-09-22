import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeMoneyValueForInput } from '../src/moneyFormatting.ts'

test('normalizes common monetary input to a dot decimal separator', () => {
  const cases = new Map([
    ['85,79', '85.79'],
    ['85.79', '85.79'],
    ['1.234,56', '1234.56'],
    ['1,234.56', '1234.56'],
    ['1,234', '1234.00'],
    ['1.234', '1234.00'],
    ['1234', '1234.00'],
    ['EUR 12,50', 'EUR 12,50'],
    ['1.2.3', '1.2.3'],
  ])

  for (const [input, expected] of cases) {
    assert.equal(normalizeMoneyValueForInput(input), expected, input)
  }
})
