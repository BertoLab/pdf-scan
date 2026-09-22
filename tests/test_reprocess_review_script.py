from __future__ import annotations

import pytest

from scripts.reprocess_review import (
    _resolve_invoice_selector,
    _unique_invoice_entries,
)


def _invoice(invoice_id: str, display_number: int) -> dict:
    return {
        "invoice_id": invoice_id,
        "title": f"Fattura {display_number:03d}",
        "display_number": display_number,
    }


def test_invoice_selector_prefers_display_number_over_review_position() -> None:
    entries = [_invoice("invoice-016", 16), _invoice("invoice-030", 30)]

    assert _resolve_invoice_selector("30", entries) == "invoice-030"


def test_invoice_selector_retains_position_fallback() -> None:
    entries = [_invoice("invoice-016", 16), _invoice("invoice-017", 17)]

    assert _resolve_invoice_selector("2", entries) == "invoice-017"


def test_invoice_selector_accepts_canonical_id_and_title() -> None:
    entries = [_invoice("invoice-030", 30)]

    assert _resolve_invoice_selector("invoice-030", entries) == "invoice-030"
    assert _resolve_invoice_selector("Fattura 030", entries) == "invoice-030"


def test_invoice_entries_are_deduplicated_by_canonical_id() -> None:
    entries = [
        _invoice("invoice-029", 29),
        _invoice("invoice-029", 29),
        _invoice("invoice-030", 30),
    ]

    assert [
        invoice["invoice_id"] for invoice in _unique_invoice_entries(entries)
    ] == ["invoice-029", "invoice-030"]


def test_invoice_selector_rejects_ambiguous_display_number() -> None:
    entries = [_invoice("invoice-a", 30), _invoice("invoice-b", 30)]

    with pytest.raises(ValueError, match="Numero fattura ambiguo"):
        _resolve_invoice_selector("30", entries)
