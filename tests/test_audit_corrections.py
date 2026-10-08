from pathlib import Path

import pytest
from openpyxl import load_workbook

from estrattore.economic_summary_extraction import resolve_economic_summary_rows
from estrattore.invoice_number_extraction import find_invoice_number_candidates
from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken, TextSpan
from estrattore.registry import (
    RegistryCache, RegistryRecord, read_registry_records_from_excel, save_registry_cache_excel,
)
from estrattore.viewer_invoice_segmentation import _best_paid_date_target, build_viewer_invoices


@pytest.mark.parametrize("label", ["Importo pagamento", "Netto vs. debito"])
def test_observed_net_anchor_resolves_amount(label):
    labels = [("Totale fattura", 700), (label, 800)]
    spans = [TextSpan(span_id=i, line_id=i, page_span_index=i, text=text,
                      bbox=BoundingBox(600, y, 780, y + 20))
             for i, (text, y) in enumerate(labels, 1)]
    tokens = [OcrToken(word, BoundingBox(600 + i * 60, y, 655 + i * 60, y + 20))
              for text, y in labels for i, word in enumerate(text.split())]
    tokens += [OcrToken("198,00", BoundingBox(870, 700, 940, 720)),
               OcrToken("190,80", BoundingBox(870, 800, 940, 820))]
    doc = DocumentRepresentation(source_file=Path("fixture.pdf"), pages=[
        DocumentPage(page_number=1, width=1000, height=1000, tokens=tokens, spans=spans),
    ])
    rows = resolve_economic_summary_rows(doc)
    assert len(rows) == 1
    assert rows[0].totale_fattura_value == "198.00"
    assert rows[0].totale_netto_fattura_value == "190.80"
    assert rows[0].totale_netto_fattura_source == "direct"
    assert rows[0].ritenuta_value == "-7.20"


@pytest.mark.parametrize("number, accepted", [
    ("000002-2025", True), ("8", True), ("4/A", True),
    ("21-01-2025", False), ("2025-01-21", False), ("198,00", False),
    ("12345678901", False), ("123-45-67", False),
])
def test_invoice_number_formats_remain_role_candidates(number, accepted):
    doc = DocumentRepresentation(source_file=Path("fixture.pdf"), pages=[
        DocumentPage(page_number=1, width=1000, height=1000, tokens=[
            OcrToken("Numero", BoundingBox(10, 10, 80, 30)),
            OcrToken("documento", BoundingBox(85, 10, 170, 30)),
            OcrToken(number, BoundingBox(180, 10, 310, 30)),
        ]),
    ])
    selected = [c for c in find_invoice_number_candidates(doc) if c.selected_as_numero_fattura]
    assert [c.normalized_value for c in selected] == ([number] if accepted else [])


@pytest.mark.parametrize("payment_label, expected", [
    ("Data esecuzione", "2025-01-22"),
    ("Data di addebito", "2025-01-22"),
    ("Data/Ora stampa bonifico", ""),
    ("Fattura del", ""),
])
def test_same_day_payment_uses_distinct_explicit_evidence(payment_label, expected):
    issue = dict(x1=200, y1=100, x2=300, y2=120)
    paid = dict(x1=200, y1=500, x2=300, y2=520)
    page = {
        "page": 1, "tokens": [],
        "spans": [dict(text="Data documento", bbox=issue), dict(text=payment_label, bbox=paid)],
        "targets": [dict(kind="data_fattura", value="2025-01-22", score=170, page=1, bbox=issue)],
        "candidate_tables": {"invoice_dates": [
            dict(normalized_value="2025-01-22", score=170, bbox=issue,
                 nearby_text="Data documento 22/01/2025"),
            dict(normalized_value="2025-01-22", score=170, bbox=paid,
                 nearby_text=payment_label + " 22/01/2025 bonifico"),
        ]},
    }
    fields = {f["kind"]: f for f in build_viewer_invoices([page])[0]["fields"]}
    assert fields["data_fattura"]["value"] == "2025-01-22"
    assert fields["pagata_il"]["value"] == expected
    if expected:
        assert fields["pagata_il"]["bbox"] == paid


def test_same_day_payment_does_not_reuse_invoice_date_occurrence():
    bbox = dict(x1=100, y1=100, x2=200, y2=120)
    target = dict(kind="data_fattura", value="2025-01-22", score=170, page=1, bbox=bbox)
    page = {
        "page": 1, "spans": [], "targets": [target],
        "candidate_tables": {"invoice_dates": [
            dict(normalized_value="2025-01-22", score=170, bbox=bbox,
                 nearby_text="Data esecuzione bonifico 22/01/2025"),
        ]},
    }
    assert _best_paid_date_target([page], [target]) is None


def test_registry_excel_preserves_formula_looking_text_and_leading_zeros(tmp_path):
    record = RegistryRecord(
        registry_id="000001", source_row=2, codice_anagrafica="000001",
        nominativo="=1+1", normalized_name="=1+1", codice_fiscale_raw="",
        codice_fiscale=None, partita_iva_raw="", partita_iva=None,
        tipologia="Altro soggetto", utente="=SUM(1,2)", warnings=[],
    )
    path = tmp_path / "registry.xlsx"
    save_registry_cache_excel(RegistryCache(schema_version=1, source_file="fixture.xlsx", records=[record]), path)
    workbook = load_workbook(path, data_only=False)
    try:
        assert workbook.active["A2"].value == "000001"
        assert workbook.active["B2"].value == "=1+1"
        assert workbook.active["B2"].data_type == "s"
        assert workbook.active["G2"].data_type == "s"
    finally:
        workbook.close()
    restored = read_registry_records_from_excel(path)[0]
    assert restored.codice_anagrafica == "000001"
    assert restored.nominativo == "=1+1"
    assert restored.utente == "=SUM(1,2)"
