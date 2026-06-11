from __future__ import annotations

import re


SRL_OCR_PATTERN = r"\bS\.?\s*R\.?\s*[LIT1]\.?"


def normalize_anchor_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def normalize_fiscal_anchor_text(text: str) -> str:
    normalized = normalize_anchor_text(text)
    normalized = re.sub(r"\bp\s+(?:ava|1va|lva|iva)\b", "p iva", normalized)
    normalized = re.sub(r"\bp(?:ava|1va|lva|iva)\b", "piva", normalized)
    return normalized


def normalize_company_legal_form(text: str) -> str:
    return re.sub(SRL_OCR_PATTERN, "SRL", text, flags=re.IGNORECASE)
