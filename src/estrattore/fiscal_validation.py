from __future__ import annotations

import re


_TAX_CODE_PATTERN = re.compile(r"^[A-Z0-9]{16}$")
_ALPHANUMERIC_TAX_CODE_DIGIT_POSITIONS = {6, 7, 9, 10, 12, 13, 14}
_OCR_DIGIT_CONFUSIONS = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
}


def normalize_italian_vat_number(value: str) -> str | None:
    normalized = value.strip().upper()
    if normalized.startswith("IT"):
        normalized = normalized[2:]

    normalized = "".join(char for char in normalized if char.isdigit())
    if len(normalized) != 11:
        return None

    return normalized


def is_valid_italian_vat_number(value: str) -> bool:
    vat_number = normalize_italian_vat_number(value)
    if vat_number is None:
        return False

    total = 0
    for index, char in enumerate(vat_number[:10]):
        digit = int(char)

        if index % 2 == 0:
            total += digit
            continue

        doubled = digit * 2
        total += doubled - 9 if doubled > 9 else doubled

    expected_check_digit = (10 - (total % 10)) % 10
    actual_check_digit = int(vat_number[10])
    return expected_check_digit == actual_check_digit


def normalize_italian_tax_code(value: str) -> str | None:
    normalized = "".join(char for char in value.strip().upper() if char.isalnum())
    if normalized.startswith("IT"):
        normalized = normalized[2:]

    if len(normalized) == 11 and normalized.isdigit():
        return normalized

    if _TAX_CODE_PATTERN.fullmatch(normalized):
        if _is_valid_alphanumeric_tax_code(normalized):
            return normalized

        corrected = _correct_ocr_alphanumeric_tax_code(normalized)
        if corrected is not None:
            return corrected

    return None


def is_valid_italian_tax_code(value: str) -> bool:
    tax_code = normalize_italian_tax_code(value)
    if tax_code is None:
        return False

    if len(tax_code) == 11:
        return True

    return _is_valid_alphanumeric_tax_code(tax_code)


def _is_valid_alphanumeric_tax_code(tax_code: str) -> bool:
    odd_values = {
        "0": 1,
        "1": 0,
        "2": 5,
        "3": 7,
        "4": 9,
        "5": 13,
        "6": 15,
        "7": 17,
        "8": 19,
        "9": 21,
        "A": 1,
        "B": 0,
        "C": 5,
        "D": 7,
        "E": 9,
        "F": 13,
        "G": 15,
        "H": 17,
        "I": 19,
        "J": 21,
        "K": 2,
        "L": 4,
        "M": 18,
        "N": 20,
        "O": 11,
        "P": 3,
        "Q": 6,
        "R": 8,
        "S": 12,
        "T": 14,
        "U": 16,
        "V": 10,
        "W": 22,
        "X": 25,
        "Y": 24,
        "Z": 23,
    }
    even_values = {str(index): index for index in range(10)}
    even_values.update({chr(ord("A") + index): index for index in range(26)})

    total = 0
    for index, char in enumerate(tax_code[:15], start=1):
        total += odd_values[char] if index % 2 == 1 else even_values[char]

    expected_check_char = chr(ord("A") + (total % 26))
    return tax_code[15] == expected_check_char


def _correct_ocr_alphanumeric_tax_code(tax_code: str) -> str | None:
    if not _TAX_CODE_PATTERN.fullmatch(tax_code):
        return None

    candidates = [tax_code]
    for position in sorted(_ALPHANUMERIC_TAX_CODE_DIGIT_POSITIONS):
        char = tax_code[position]
        replacement = _OCR_DIGIT_CONFUSIONS.get(char)
        if replacement is None:
            continue

        current_candidates = list(candidates)
        candidates.extend(
            candidate[:position] + replacement + candidate[position + 1 :]
            for candidate in current_candidates
        )

    for candidate in candidates[1:]:
        if _is_valid_alphanumeric_tax_code(candidate):
            return candidate

    return None
