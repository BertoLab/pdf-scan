function integerDigits(value: string, groupingSeparator?: ',' | '.') {
  if (/^\d+$/.test(value)) {
    return value
  }

  const groupedPattern =
    groupingSeparator === ','
      ? /^\d{1,3}(?:,\d{3})+$/
      : /^\d{1,3}(?:\.\d{3})+$/
  return groupedPattern.test(value) ? value.replace(/[.,]/g, '') : null
}

export function normalizeMoneyValueForInput(value: string) {
  const compactValue = value.trim().replace(/[\s\u00a0\u20ac]/g, '')
  if (!compactValue || !/^[+-]?[\d.,]+$/.test(compactValue)) {
    return value
  }

  const sign = compactValue.startsWith('-') ? '-' : ''
  const unsignedValue = compactValue.replace(/^[+-]/, '')
  const commaIndex = unsignedValue.lastIndexOf(',')
  const dotIndex = unsignedValue.lastIndexOf('.')
  const hasComma = commaIndex >= 0
  const hasDot = dotIndex >= 0
  let wholeDigits: string | null
  let decimalDigits = '00'

  if (hasComma && hasDot) {
    const decimalIndex = Math.max(commaIndex, dotIndex)
    const decimalSeparator = unsignedValue[decimalIndex]
    const groupingSeparator = decimalSeparator === ',' ? '.' : ','
    const fraction = unsignedValue.slice(decimalIndex + 1)
    const integerPart = unsignedValue.slice(0, decimalIndex)

    if (!/^\d{1,2}$/.test(fraction)) {
      return value
    }
    wholeDigits = integerDigits(integerPart, groupingSeparator)
    decimalDigits = fraction.padEnd(2, '0')
  } else if (hasComma || hasDot) {
    const separator = hasComma ? ',' : '.'
    const parts = unsignedValue.split(separator)
    const finalPart = parts.at(-1) ?? ''

    if (parts.length === 2 && /^\d{1,2}$/.test(finalPart)) {
      wholeDigits = /^\d+$/.test(parts[0]) ? parts[0] : null
      decimalDigits = finalPart.padEnd(2, '0')
    } else {
      wholeDigits = integerDigits(unsignedValue, separator)
    }
  } else {
    wholeDigits = integerDigits(unsignedValue)
  }

  if (!wholeDigits) {
    return value
  }

  const integerPart = wholeDigits.replace(/^0+(?=\d)/, '')
  return `${sign}${integerPart}.${decimalDigits}`
}
