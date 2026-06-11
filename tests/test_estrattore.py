import json
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))
from debug_words import (
    _debug_output_dir,
    _viewer_targets,
    is_supported_input_file,
    save_viewer_manifest,
)
from estrattore.condominium_extraction import find_condominium_candidates
from estrattore import debug_output
from estrattore.debug_output import (
    DebugHighlight,
    save_document_debug_json,
    save_document_lines_csv,
    save_document_spans_csv,
    save_page_debug_image,
)
from estrattore import debug_flags
from estrattore.economic_summary_extraction import find_economic_summary_regions
from estrattore.economic_summary_extraction import (
    find_economic_summary_field_candidates,
    resolve_economic_summary_rows,
)
from estrattore.fiscal_validation import (
    is_valid_italian_tax_code,
    is_valid_italian_vat_number,
    normalize_italian_tax_code,
    normalize_italian_vat_number,
)
from estrattore.fiscal_identifier_extraction import find_fiscal_identifier_candidates
from estrattore.invoice_date_extraction import (
    InvoiceDateCandidate,
    find_invoice_date_candidates,
)
from estrattore.invoice_number_extraction import find_invoice_number_candidates
from estrattore.issuer_name_extraction import (
    IssuerNameCandidate,
    find_issuer_name_candidates,
)
from estrattore import pipeline
from estrattore.model import (
    BoundingBox,
    DocumentPage,
    DocumentRepresentation,
    DocumentStatus,
    FieldStatus,
    InvoiceField,
    LayoutBlock,
    LayoutBlockType,
    OcrToken,
    PerceptionBackend,
    TableRegion,
    TextLine,
    TextSpan,
    init_invoice_result,
)
from estrattore.ocr_normalization import (
    normalize_company_legal_form,
    normalize_fiscal_anchor_text,
)
from estrattore.perception import analyze_pages
from estrattore import tesseract_adapter
from estrattore.tesseract_adapter import (
    DEFAULT_TESSERACT_LANG,
    _configure_tessdata_prefix,
    _missing_tesseract_languages,
    deduplicate_overlapping_tokens,
    resolve_tesseract_cmd,
    tesseract_data_to_tokens,
)
from estrattore.viewer_invoice_segmentation import build_viewer_invoices
from estrattore.text_layout import (
    add_lines_to_document,
    add_spans_to_document,
    group_page_tokens_into_lines,
    split_line_into_spans,
)
from estrattore.vat_extraction import (
    find_vat_candidates,
    select_best_vat_candidate_per_page,
)


# SECTION: Core domain model
# Tests the in-memory result and field objects used by the extraction pipeline.
# These tests do not read manifests, PDFs, or OCR files.
def test_init_invoice_result_starts_pending() -> None:
    source_file = Path("data/input/fattura.pdf")

    result = init_invoice_result(source_file)

    assert result.source_file == source_file
    assert result.status == DocumentStatus.PENDING
    assert result.fields == {}
    assert result.document is None
    assert result.handwritten_date_crop_path is None
    assert result.rejection_reason is None
    assert result.template_id is None


def test_invoice_field_keeps_evidence_and_location() -> None:
    bbox = BoundingBox(x1=10, y1=20, x2=110, y2=45)

    field = InvoiceField(
        name="total_amount",
        value="1234.56",
        status=FieldStatus.ACCEPTED,
        confidence=0.94,
        page=1,
        bbox=bbox,
        evidence=["label vicino: Totale fattura"],
    )

    assert field.name == "total_amount"
    assert field.value == "1234.56"
    assert field.status == FieldStatus.ACCEPTED
    assert field.confidence == 0.94
    assert field.page == 1
    assert field.bbox == bbox
    assert field.evidence == ["label vicino: Totale fattura"]
    assert field.reason is None


# SECTION: Fiscal value normalization and validation
# Tests pure string normalization/checksum logic for Italian VAT numbers and tax
# codes. These are deterministic unit tests, not OCR extraction tests.
def test_normalize_italian_vat_number_accepts_it_prefix_and_separators() -> None:
    assert normalize_italian_vat_number("IT 01303760530") == "01303760530"
    assert normalize_italian_vat_number("P.I. 01303760530") == "01303760530"
    assert normalize_italian_vat_number("123") is None


def test_is_valid_italian_vat_number_checks_control_digit() -> None:
    assert is_valid_italian_vat_number("01303760530") is True
    assert is_valid_italian_vat_number("IT01303760530") is True
    assert is_valid_italian_vat_number("01303760531") is False
    assert is_valid_italian_vat_number("not a vat") is False


def test_normalize_italian_tax_code_accepts_numeric_and_alphanumeric_codes() -> None:
    assert normalize_italian_tax_code("90006070537") == "90006070537"
    assert normalize_italian_tax_code("RSSMRA80A01H501U") == "RSSMRA80A01H501U"
    assert normalize_italian_tax_code("123") is None


def test_normalize_italian_tax_code_corrects_ocr_letters_in_digit_positions() -> None:
    assert normalize_italian_tax_code("SSSMRC81RO3F032J") == "SSSMRC81R03F032J"


def test_is_valid_italian_tax_code_checks_alphanumeric_control_char() -> None:
    assert is_valid_italian_tax_code("RSSMRA80A01H501U") is True
    assert is_valid_italian_tax_code("RSSMRA80A01H501A") is False
    assert is_valid_italian_tax_code("90006070537") is True


def test_ocr_anchor_normalization_handles_common_domain_errors() -> None:
    assert normalize_fiscal_anchor_text("C, F. e P.AVA") == "c f e p iva"
    assert normalize_fiscal_anchor_text("P.1VA") == "p iva"
    assert normalize_fiscal_anchor_text("P.lVA") == "p iva"
    assert normalize_company_legal_form("OTIS Servizi S.r.t.") == "OTIS Servizi SRL"
    assert normalize_company_legal_form("OTIS Servizi S.r.1,") == "OTIS Servizi SRL,"


# SECTION: Fiscal identifier extraction from OCR tokens
# Tests P.IVA, codice fiscale condominio, and issuer tax-code selection from
# synthetic OCR tokens, anchors, proximity, OCR noise, and negative context.
def test_find_fiscal_identifier_candidates_scores_vat_and_tax_code_as_pair() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD.", BoundingBox(10, 10, 50, 25)),
                    OcrToken("FISCALE", BoundingBox(55, 10, 115, 25)),
                    OcrToken("P.IVA", BoundingBox(120, 10, 170, 25)),
                    OcrToken("90000080532", BoundingBox(180, 10, 280, 25)),
                    OcrToken("C.F.", BoundingBox(10, 100, 50, 115)),
                    OcrToken("RSSMRA80A01H501U", BoundingBox(60, 100, 210, 115)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["90000080532"].p_iva_context == "piva|cod. fiscale / p.iva"
    assert by_value["90000080532"].codice_fiscale_context == (
        "cod fiscale|cod. fiscale / p.iva"
    )
    assert by_value["90000080532"].campo_suggerito == "codice_fiscale"
    assert by_value["RSSMRA80A01H501U"].campo_suggerito == "codice_fiscale"


def test_find_fiscal_identifier_candidates_treats_mixed_cod_fiscale_pva_900_as_tax_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD.", BoundingBox(10, 10, 50, 25)),
                    OcrToken("FISCALE/", BoundingBox(55, 10, 130, 25)),
                    OcrToken("PVA", BoundingBox(135, 10, 170, 25)),
                    OcrToken("90000080532", BoundingBox(180, 10, 300, 25)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].p_iva_context == "pva|cod. fiscale / p.iva"
    assert candidates[0].codice_fiscale_context == (
        "cod fiscale|cod. fiscale / p.iva"
    )
    assert candidates[0].p_iva_score == 0
    assert candidates[0].codice_fiscale_score == 100
    assert candidates[0].campo_suggerito == "codice_fiscale"
    assert candidates[0].selected_as_p_iva is False
    assert candidates[0].selected_as_codice_fiscale is True


def test_find_fiscal_identifier_candidates_treats_ambiguous_piva_900_as_condominium_tax_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COND.ARIOSTO", BoundingBox(10, 200, 130, 220)),
                    OcrToken("//PIVA", BoundingBox(320, 500, 370, 520)),
                    OcrToken("90000080532", BoundingBox(410, 500, 560, 520)),
                    OcrToken("P.IVA", BoundingBox(120, 800, 170, 820)),
                    OcrToken("01729590032", BoundingBox(190, 800, 340, 820)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="COND.ARIOSTO 10",
                        bbox=BoundingBox(10, 200, 180, 220),
                    )
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["90000080532"].campo_suggerito == "codice_fiscale"
    assert by_value["90000080532"].selected_as_codice_fiscale is True
    assert by_value["01729590032"].selected_as_p_iva is True


def test_find_fiscal_identifier_candidates_uses_selected_vat_to_resolve_mixed_tax_code_context() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD.", BoundingBox(10, 200, 50, 220)),
                    OcrToken("FISCALE/", BoundingBox(55, 200, 130, 220)),
                    OcrToken("PVA", BoundingBox(135, 200, 170, 220)),
                    OcrToken("90106070530", BoundingBox(190, 200, 340, 220)),
                    OcrToken("P.IVA", BoundingBox(120, 800, 170, 820)),
                    OcrToken("01729590032", BoundingBox(190, 800, 340, 820)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["01729590032"].selected_as_p_iva is True
    assert by_value["90106070530"].selected_as_codice_fiscale is True


def test_find_fiscal_identifier_candidates_accepts_ocr_dirty_piva_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("C,", BoundingBox(10, 10, 30, 25)),
                    OcrToken("F.", BoundingBox(35, 10, 55, 25)),
                    OcrToken("e", BoundingBox(60, 10, 70, 25)),
                    OcrToken("P.AVA", BoundingBox(75, 10, 125, 25)),
                    OcrToken("01729590032", BoundingBox(135, 10, 245, 25)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["01729590032"].p_iva_context == "piva"
    assert by_value["01729590032"].campo_suggerito == "p_iva"


def test_find_fiscal_identifier_candidates_does_not_favor_non_9000_mixed_numeric_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD.", BoundingBox(10, 10, 50, 25)),
                    OcrToken("FISCALE/", BoundingBox(55, 10, 130, 25)),
                    OcrToken("PVA", BoundingBox(135, 10, 170, 25)),
                    OcrToken("90106070530", BoundingBox(180, 10, 300, 25)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].p_iva_score == 0
    assert candidates[0].codice_fiscale_score == 0
    assert candidates[0].campo_suggerito == "uncertain"


def test_find_fiscal_identifier_candidates_accepts_fuzzy_long_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD1CE", BoundingBox(10, 10, 70, 25)),
                    OcrToken("FISCALE", BoundingBox(75, 10, 145, 25)),
                    OcrToken("90006070537", BoundingBox(10, 35, 150, 55)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].codice_fiscale_context == "codice fiscale"
    assert candidates[0].codice_fiscale_score >= 88
    assert candidates[0].campo_suggerito == "codice_fiscale"


def test_find_fiscal_identifier_candidates_accepts_cod_fisc_abbreviation() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Cod,", BoundingBox(10, 10, 50, 25)),
                    OcrToken("Fisc,", BoundingBox(55, 10, 105, 25)),
                    OcrToken("90000080532", BoundingBox(115, 10, 240, 25)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].codice_fiscale_context == "cod fisc"
    assert candidates[0].codice_fiscale_score == 100
    assert candidates[0].campo_suggerito == "codice_fiscale"
    assert candidates[0].selected_as_codice_fiscale is True


def test_find_fiscal_identifier_candidates_accepts_reversed_fisc_cod_scan_order() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Fisc,", BoundingBox(10, 10, 60, 25)),
                    OcrToken("Cod,", BoundingBox(65, 10, 105, 25)),
                    OcrToken("90000080532", BoundingBox(115, 10, 240, 25)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].codice_fiscale_context == "cod fisc"
    assert candidates[0].codice_fiscale_score == 100
    assert candidates[0].campo_suggerito == "codice_fiscale"


def test_find_fiscal_identifier_candidates_does_not_fuzzy_match_short_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("cg", BoundingBox(10, 10, 35, 25)),
                    OcrToken("RSSMRA80A01H501U", BoundingBox(45, 10, 210, 25)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].codice_fiscale_context == ""
    assert candidates[0].codice_fiscale_score == 0
    assert candidates[0].campo_suggerito == "uncertain"


def test_find_fiscal_identifier_candidates_ignores_distant_context_inside_radius() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("P.IVA", BoundingBox(210, 100, 260, 115)),
                    OcrToken("C.F.", BoundingBox(470, 100, 510, 115)),
                    OcrToken("RSSMRA80A01H501U", BoundingBox(520, 100, 690, 115)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].p_iva_context == ""
    assert candidates[0].codice_fiscale_context == "c.f."
    assert candidates[0].campo_suggerito == "codice_fiscale"


def test_find_fiscal_identifier_candidates_prefers_closer_piva_over_farther_tax_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("CODICE", BoundingBox(210, 100, 270, 115)),
                    OcrToken("FISCALE", BoundingBox(275, 100, 335, 115)),
                    OcrToken("P.IVA", BoundingBox(470, 100, 520, 115)),
                    OcrToken("01303760530", BoundingBox(530, 100, 640, 115)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].p_iva_context == "piva"
    assert candidates[0].codice_fiscale_context == ""
    assert candidates[0].campo_suggerito == "p_iva"


def test_find_fiscal_identifier_candidates_accepts_tax_code_when_bank_context_is_farther() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("CODICE", BoundingBox(10, 10, 70, 25)),
                    OcrToken("FISCALE", BoundingBox(75, 10, 145, 25)),
                    OcrToken("90006070537", BoundingBox(10, 35, 150, 55)),
                    OcrToken("BANCA", BoundingBox(10, 85, 80, 105)),
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)

    assert candidates[0].codice_fiscale_context == "codice fiscale"
    assert candidates[0].negative_context == "banca"
    assert candidates[0].campo_suggerito == "codice_fiscale"


def test_find_fiscal_identifier_candidates_selects_alphanumeric_issuer_tax_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("C.F.", BoundingBox(10, 10, 45, 25)),
                    OcrToken("SSSMRC81R03F032J", BoundingBox(55, 10, 215, 25)),
                    OcrToken("P.iva", BoundingBox(225, 10, 275, 25)),
                    OcrToken("01312380536", BoundingBox(285, 10, 395, 25)),
                    OcrToken("Destinatario", BoundingBox(10, 120, 110, 140)),
                    OcrToken("CONDOMINIO", BoundingBox(10, 155, 120, 175)),
                    OcrToken("COD.", BoundingBox(10, 190, 50, 210)),
                    OcrToken("FISCALE", BoundingBox(55, 190, 125, 210)),
                    OcrToken("90006070537", BoundingBox(135, 190, 250, 210)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="CONDOMINIO VIA ROMA",
                        bbox=BoundingBox(10, 155, 220, 175),
                    )
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["90006070537"].selected_as_codice_fiscale is True
    assert by_value["SSSMRC81R03F032J"].selected_as_codice_fiscale_emittente is True
    assert by_value["SSSMRC81R03F032J"].codice_fiscale_emittente_context == (
        "codice_fiscale_alfanumerico|vicino_p_iva|vicino_valore_p_iva|"
        "altro_cf_piu_vicino_condominio"
    )


def test_find_fiscal_identifier_candidates_corrects_ocr_issuer_tax_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("C.F.", BoundingBox(10, 10, 45, 25)),
                    OcrToken("SSSMRC81RO3F032J", BoundingBox(55, 10, 215, 25)),
                    OcrToken("P.iva", BoundingBox(225, 10, 275, 25)),
                    OcrToken("01312380536", BoundingBox(285, 10, 395, 25)),
                    OcrToken("Destinatario", BoundingBox(10, 120, 110, 140)),
                    OcrToken("CONDOMINIO", BoundingBox(10, 155, 120, 175)),
                    OcrToken("Cod.", BoundingBox(10, 190, 50, 210)),
                    OcrToken("Fisc.", BoundingBox(55, 190, 105, 210)),
                    OcrToken("90000080532", BoundingBox(115, 190, 230, 210)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="CONDOMINIO VIA ROMA",
                        bbox=BoundingBox(10, 155, 220, 175),
                    )
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert "SSSMRC81R03F032J" in by_value
    assert by_value["SSSMRC81R03F032J"].raw_text == "SSSMRC81RO3F032J"
    assert by_value["SSSMRC81R03F032J"].selected_as_codice_fiscale_emittente is True


def test_find_fiscal_identifier_candidates_splits_two_numeric_tax_codes_by_role() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD.", BoundingBox(10, 10, 50, 25)),
                    OcrToken("FISCALE", BoundingBox(55, 10, 125, 25)),
                    OcrToken("P.IVA", BoundingBox(130, 10, 180, 25)),
                    OcrToken("90000080532", BoundingBox(190, 10, 305, 25)),
                    OcrToken("01303760530", BoundingBox(315, 10, 425, 25)),
                    OcrToken("Destinatario", BoundingBox(10, 150, 120, 170)),
                    OcrToken("CONDOMINIO", BoundingBox(10, 185, 125, 205)),
                    OcrToken("COD.", BoundingBox(10, 220, 50, 240)),
                    OcrToken("FISCALE", BoundingBox(55, 220, 125, 240)),
                    OcrToken("90006070537", BoundingBox(135, 220, 250, 240)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="CONDOMINIO VIA ROMA",
                        bbox=BoundingBox(10, 185, 220, 205),
                    )
                ],
            )
        ],
    )

    candidates = find_fiscal_identifier_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["90006070537"].selected_as_codice_fiscale is True
    assert by_value["90000080532"].selected_as_codice_fiscale_emittente is True
    assert by_value["90000080532"].selected_as_codice_fiscale is False


# SECTION: Invoice number extraction from OCR tokens and spans
# Tests invoice-number parsing, anchor scoring, accepted formats, and exclusions
# for dates, tax codes, amounts, item codes, and condominium civic numbers.
def test_find_invoice_number_candidates_accepts_slash_format_near_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Numero", BoundingBox(10, 10, 80, 25)),
                    OcrToken("Fattura", BoundingBox(85, 10, 150, 25)),
                    OcrToken("1166/2024", BoundingBox(160, 10, 260, 25)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates[0].normalized_value == "1166/2024"
    assert candidates[0].format_kind == "slash_progressivo"
    assert candidates[0].anchor_context == "numero fattura"
    assert candidates[0].score >= 120
    assert candidates[0].selected_as_numero_fattura is True


def test_find_invoice_number_candidates_excludes_date_suffix_fragment() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Pagamento", BoundingBox(10, 100, 95, 120)),
                    OcrToken("fattura", BoundingBox(105, 100, 165, 120)),
                    OcrToken("n.13", BoundingBox(175, 100, 215, 120)),
                    OcrToken("del", BoundingBox(225, 100, 255, 120)),
                    OcrToken("08/01/2024", BoundingBox(265, 100, 365, 120)),
                    OcrToken("01/2024", BoundingBox(285, 100, 365, 120)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["13"].selected_as_numero_fattura is True
    assert by_value["01/2024"].excluded_reason == "frammento_data"
    assert by_value["01/2024"].selected_as_numero_fattura is False


def test_find_invoice_number_candidates_accepts_fuzzy_alphanumeric_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("PATTURA", BoundingBox(10, 10, 90, 25)),
                    OcrToken("NUMERO", BoundingBox(95, 10, 170, 25)),
                    OcrToken('"3FC24014301', BoundingBox(185, 10, 320, 25)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates[0].normalized_value == "3FC24014301"
    assert candidates[0].format_kind == "alfanumerico"
    assert candidates[0].anchor_context == "fattura numero"
    assert candidates[0].score >= 120
    assert candidates[0].selected_as_numero_fattura is True


def test_find_invoice_number_candidates_keeps_full_fattura_numero_context() -> None:
    noisy_tokens = [
        OcrToken(f"noise{index}", BoundingBox(500 + index * 8, 90, 505 + index * 8, 105))
        for index in range(22)
    ]
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1200,
                height=1000,
                tokens=[
                    *noisy_tokens,
                    OcrToken("FATTURA", BoundingBox(700, 100, 770, 120)),
                    OcrToken("NUMERO.", BoundingBox(775, 100, 850, 120)),
                    OcrToken("3FO24061716", BoundingBox(860, 100, 980, 120)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert "fattura numero" in by_value["3FO24061716"].anchor_context
    assert by_value["3FO24061716"].selected_as_numero_fattura is True


def test_find_invoice_number_candidates_accepts_dirty_single_fattura_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("[Fattura", BoundingBox(10, 10, 90, 25)),
                    OcrToken("3FC24014301", BoundingBox(110, 10, 250, 25)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates[0].normalized_value == "3FC24014301"
    assert candidates[0].anchor_context == "fattura"
    assert candidates[0].score >= 120
    assert candidates[0].selected_as_numero_fattura is True


def test_find_invoice_number_candidates_accepts_numeric_number_after_n_del() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Fattura", BoundingBox(10, 10, 90, 25)),
                    OcrToken("C.F.", BoundingBox(110, 10, 150, 25)),
                    OcrToken("SSSMRC81R03F032J", BoundingBox(160, 10, 330, 25)),
                    OcrToken("n.", BoundingBox(10, 40, 35, 55)),
                    OcrToken("227", BoundingBox(45, 40, 80, 55)),
                    OcrToken("del", BoundingBox(90, 40, 125, 55)),
                    OcrToken("03/09/2024", BoundingBox(135, 40, 245, 55)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["227"].format_kind == "progressivo_numerico"
    assert "n" in by_value["227"].anchor_context
    assert "fattura" in by_value["227"].anchor_context
    assert by_value["227"].selected_as_numero_fattura is True


def test_find_invoice_number_candidates_strips_number_prefix_from_same_token() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Fattura", BoundingBox(10, 10, 90, 25)),
                    OcrToken("N.227", BoundingBox(100, 10, 160, 25)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates[0].raw_text == "N.227"
    assert candidates[0].normalized_value == "227"
    assert candidates[0].format_kind == "progressivo_numerico"


def test_find_invoice_number_candidates_ignores_generic_fattura_after_item_code() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("OTT.3/4", BoundingBox(100, 100, 170, 115)),
                    OcrToken("Copia", BoundingBox(60, 145, 110, 160)),
                    OcrToken("della", BoundingBox(115, 145, 160, 160)),
                    OcrToken("fattura", BoundingBox(165, 145, 230, 160)),
                    OcrToken("elettronica", BoundingBox(235, 145, 330, 160)),
                    OcrToken("03/09/2024", BoundingBox(335, 145, 445, 160)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates[0].normalized_value == "OTT.3/4"
    assert candidates[0].anchor_context == ""
    assert candidates[0].selected_as_numero_fattura is False


def test_find_invoice_number_candidates_excludes_civic_number_inside_condominium_span() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("[FATTURA", BoundingBox(500, 10, 580, 25)),
                    OcrToken("1166/2024", BoundingBox(600, 10, 700, 25)),
                    OcrToken("INTESTATARIO", BoundingBox(10, 100, 120, 115)),
                    OcrToken("FATTURA", BoundingBox(125, 100, 190, 115)),
                    OcrToken("CONDOMINIO", BoundingBox(10, 130, 120, 145)),
                    OcrToken("VIA", BoundingBox(125, 130, 155, 145)),
                    OcrToken("ATENE", BoundingBox(160, 130, 220, 145)),
                    OcrToken("19/27", BoundingBox(230, 130, 290, 145)),
                    OcrToken("DATA", BoundingBox(720, 10, 770, 25)),
                    OcrToken("30/09/2024", BoundingBox(780, 10, 900, 25)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="[FATTURA",
                        bbox=BoundingBox(500, 10, 580, 25),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=1,
                        page_span_index=2,
                        text="1166/2024",
                        bbox=BoundingBox(600, 10, 700, 25),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=2,
                        page_span_index=3,
                        text="INTESTATARIO FATTURA",
                        bbox=BoundingBox(10, 100, 190, 115),
                    ),
                    TextSpan(
                        span_id=4,
                        line_id=3,
                        page_span_index=4,
                        text="CONDOMINIO VIA ATENE 19/27",
                        bbox=BoundingBox(10, 130, 290, 145),
                    ),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)
    by_value = {candidate.normalized_value: candidate for candidate in candidates}

    assert by_value["19/27"].excluded_reason == "numero_civico_in_span_condominio"
    assert by_value["19/27"].selected_as_numero_fattura is False
    assert by_value["1166/2024"].selected_as_numero_fattura is True
    assert by_value["1166/2024"].date_context == "fattura_data"


# SECTION: Condominium name extraction from OCR spans
# Tests fuzzy condominium anchors, abbreviation handling, preferred span shape,
# and penalties for noisy payment lines.
def test_find_condominium_candidates_uses_fuzzy_span_and_nearby_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="INTESTATARIO FATTURA",
                        bbox=BoundingBox(10, 100, 190, 115),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="COND.MINIO VIA ROMA 12",
                        bbox=BoundingBox(10, 130, 260, 145),
                    ),
                ],
            )
        ],
    )

    candidates = find_condominium_candidates(document)

    assert candidates[0].raw_text == "COND.MINIO VIA ROMA 12"
    assert candidates[0].condominio_context == "condominio"
    assert "intestatario" in candidates[0].anchor_context
    assert candidates[0].selected_as_condominio is True


def test_find_condominium_candidates_prefers_span_starting_with_condominio() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="Committente: Condominio Via Atene, 19/27",
                        bbox=BoundingBox(10, 100, 360, 120),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="COND.MINIO VIA ATENE 19/27",
                        bbox=BoundingBox(10, 140, 280, 160),
                    ),
                ],
            )
        ],
    )

    candidates = find_condominium_candidates(document)
    selected = [
        candidate for candidate in candidates if candidate.selected_as_condominio
    ]

    assert len(selected) == 1
    assert selected[0].raw_text == "COND.MINIO VIA ATENE 19/27"


def test_find_condominium_candidates_accepts_cond_abbreviation() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="Cond. Ariosto 10",
                        bbox=BoundingBox(10, 100, 170, 120),
                    ),
                ],
            )
        ],
    )

    candidates = find_condominium_candidates(document)

    assert candidates[0].raw_text == "Cond. Ariosto 10"
    assert candidates[0].selected_as_condominio is True


def test_find_condominium_candidates_penalizes_dirty_payment_line() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text=(
                            "1T97T0306972249100000007606-EUR-VIA ARIOSTO 10 "
                            "CONDOMINIO"
                        ),
                        bbox=BoundingBox(10, 100, 650, 120),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="Cond. Ariosto 10",
                        bbox=BoundingBox(10, 150, 170, 170),
                    ),
                ],
            )
        ],
    )

    candidates = find_condominium_candidates(document)
    selected = next(candidate for candidate in candidates if candidate.selected_as_condominio)

    assert selected.raw_text == "Cond. Ariosto 10"


# SECTION: Issuer name extraction from OCR spans
# Tests company-name extraction and normalization for SRL-like names, including
# OCR suffix noise, repeated occurrences, and tie breakers.
def test_find_issuer_name_candidates_extracts_srl_name_from_span() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="ASCENSORI AMIATA S.R.L.",
                        bbox=BoundingBox(10, 100, 230, 120),
                        tokens=[
                            OcrToken("ASCENSORI", BoundingBox(10, 100, 90, 120), 0.95),
                            OcrToken("AMIATA", BoundingBox(95, 100, 155, 120), 0.96),
                            OcrToken("S.R.L.", BoundingBox(160, 100, 230, 120), 0.97),
                        ],
                    ),
                ],
            )
        ],
    )

    candidates = find_issuer_name_candidates(document)

    assert candidates[0].normalized_value == "ASCENSORI AMIATA SRL"
    assert candidates[0].selected_as_nome_emittente is True


def test_find_issuer_name_candidates_normalizes_dirty_srl_ocr_suffix() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="OTIS Servizi S.r.t.",
                        bbox=BoundingBox(10, 100, 180, 120),
                        tokens=[
                            OcrToken("OTIS", BoundingBox(10, 100, 50, 120), 0.91),
                            OcrToken("Servizi", BoundingBox(55, 100, 110, 120), 0.90),
                            OcrToken("S.r.t.", BoundingBox(115, 100, 180, 120), 0.64),
                        ],
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="OTIS Servizi S.r.1,",
                        bbox=BoundingBox(10, 150, 180, 170),
                        tokens=[
                            OcrToken("OTIS", BoundingBox(10, 150, 50, 170), 0.88),
                            OcrToken("Servizi", BoundingBox(55, 150, 110, 170), 0.87),
                            OcrToken("S.r.1,", BoundingBox(115, 150, 180, 170), 0.53),
                        ],
                    ),
                ],
            )
        ],
    )

    candidates = find_issuer_name_candidates(document)

    assert {candidate.normalized_value for candidate in candidates} == {
        "OTIS SERVIZI SRL"
    }
    assert candidates[0].occurrence_count == 2


def test_find_issuer_name_candidates_prefers_most_frequent_srl_name() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="ALFA SRL",
                        bbox=BoundingBox(10, 100, 120, 120),
                        tokens=[OcrToken("ALFA", BoundingBox(10, 100, 60, 120), 0.80)],
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="BETA SRL",
                        bbox=BoundingBox(10, 150, 120, 170),
                        tokens=[OcrToken("BETA", BoundingBox(10, 150, 60, 170), 0.99)],
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="ALFA S.R.L.",
                        bbox=BoundingBox(10, 200, 120, 220),
                        tokens=[OcrToken("ALFA", BoundingBox(10, 200, 60, 220), 0.81)],
                    ),
                ],
            )
        ],
    )

    candidates = find_issuer_name_candidates(document)
    selected = next(candidate for candidate in candidates if candidate.selected_as_nome_emittente)

    assert selected.normalized_value == "ALFA SRL"
    assert selected.occurrence_count == 2


def test_find_issuer_name_candidates_uses_accuracy_as_tie_breaker() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="ALFA SRL",
                        bbox=BoundingBox(10, 100, 120, 120),
                        tokens=[OcrToken("ALFA", BoundingBox(10, 100, 60, 120), 0.80)],
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="BETA SRL",
                        bbox=BoundingBox(10, 150, 120, 170),
                        tokens=[OcrToken("BETA", BoundingBox(10, 150, 60, 170), 0.99)],
                    ),
                ],
            )
        ],
    )

    candidates = find_issuer_name_candidates(document)
    selected = next(candidate for candidate in candidates if candidate.selected_as_nome_emittente)

    assert selected.normalized_value == "BETA SRL"
    assert selected.accuracy == 0.99


def test_viewer_targets_include_page_issuer_candidate_even_when_not_globally_selected() -> None:
    page = DocumentPage(page_number=3, width=1000, height=1000)
    issuer_candidate = IssuerNameCandidate(
        page=3,
        raw_text="OTIS Servizi S.r.1,",
        normalized_value="OTIS SERVIZI SRL",
        bbox=BoundingBox(131, 2157, 310, 2175),
        accuracy=0.76,
        occurrence_count=3,
        score=376.0,
        selected_as_nome_emittente=False,
    )

    targets = _viewer_targets(
        page=page,
        selected_vat=None,
        fiscal_candidates=[],
        issuer_name_candidates=[issuer_candidate],
        invoice_number_candidates=[],
        invoice_date_candidates=[],
        condominium_candidates=[],
        economic_summary_regions=[],
        economic_summary_field_candidates=[],
    )

    assert targets[0]["kind"] == "nome_emittente"
    assert targets[0]["value"] == "OTIS SERVIZI SRL"
    assert targets[0]["page"] == 3


# SECTION: Invoice date extraction from OCR tokens
# Tests invoice-date selection by invoice/date context and verifies that due
# dates and billing-period dates are scored with the intended context rules.
def test_find_invoice_date_candidates_selects_date_near_fattura_data() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("[FATTURA", BoundingBox(10, 10, 90, 25)),
                    OcrToken("1166/2024", BoundingBox(110, 10, 220, 25)),
                    OcrToken("DATA", BoundingBox(230, 10, 285, 25)),
                    OcrToken("30/09/2024", BoundingBox(300, 10, 420, 25)),
                ],
            )
        ],
    )

    candidates = find_invoice_date_candidates(document)

    assert candidates[0].normalized_value == "2024-09-30"
    assert candidates[0].anchor_context == "data|fattura|numero_fattura"
    assert candidates[0].selected_as_data_fattura is True


def test_find_invoice_date_candidates_penalizes_entro_il_due_date() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Fattura", BoundingBox(10, 10, 80, 25)),
                    OcrToken("n.", BoundingBox(90, 10, 115, 25)),
                    OcrToken("52", BoundingBox(125, 10, 150, 25)),
                    OcrToken("del", BoundingBox(160, 10, 195, 25)),
                    OcrToken("08/04/2024", BoundingBox(205, 10, 320, 25)),
                    OcrToken("pagare", BoundingBox(10, 90, 70, 105)),
                    OcrToken("entro", BoundingBox(80, 90, 130, 105)),
                    OcrToken("il", BoundingBox(140, 90, 155, 105)),
                    OcrToken("30/04/2024", BoundingBox(165, 90, 280, 105)),
                ],
            )
        ],
    )

    candidates = find_invoice_date_candidates(document)
    by_raw_text = {candidate.raw_text: candidate for candidate in candidates}

    assert by_raw_text["08/04/2024"].selected_as_data_fattura is True
    assert by_raw_text["30/04/2024"].selected_as_data_fattura is False
    assert by_raw_text["30/04/2024"].negative_context == "entro il"
    assert by_raw_text["30/04/2024"].score < by_raw_text["08/04/2024"].score


def test_find_invoice_date_candidates_treats_billing_period_as_positive_context() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Fattura", BoundingBox(10, 10, 80, 25)),
                    OcrToken("n.", BoundingBox(90, 10, 115, 25)),
                    OcrToken("52", BoundingBox(125, 10, 150, 25)),
                    OcrToken("del", BoundingBox(160, 10, 195, 25)),
                    OcrToken("08/04/2024", BoundingBox(205, 10, 320, 25)),
                    OcrToken("Periodo", BoundingBox(10, 90, 80, 105)),
                    OcrToken("di", BoundingBox(90, 90, 105, 105)),
                    OcrToken("fatturazione", BoundingBox(115, 90, 220, 105)),
                    OcrToken("dal", BoundingBox(230, 90, 260, 105)),
                    OcrToken("01/03/2024", BoundingBox(270, 90, 385, 105)),
                    OcrToken("al", BoundingBox(395, 90, 415, 105)),
                    OcrToken("31/03/2024", BoundingBox(425, 90, 540, 105)),
                ],
            )
        ],
    )

    candidates = find_invoice_date_candidates(document)
    by_raw_text = {candidate.raw_text: candidate for candidate in candidates}

    assert by_raw_text["01/03/2024"].negative_context == ""
    assert by_raw_text["31/03/2024"].negative_context == ""
    assert "periodo di fatturazione" in by_raw_text["01/03/2024"].anchor_context
    assert "periodo di fatturazione" in by_raw_text["31/03/2024"].anchor_context
    assert by_raw_text["01/03/2024"].score >= 120
    assert by_raw_text["31/03/2024"].score >= 120


# SECTION: Economic summary extraction from OCR layout
# Tests summary-region detection, field-to-amount linking, and ritenuta/net-total
# math checks from synthetic OCR tokens and spans.
def test_find_economic_summary_regions_uses_anchors_and_nearby_amounts() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("TOTALE", BoundingBox(600, 700, 660, 715)),
                    OcrToken("IMPONIBILE", BoundingBox(665, 700, 760, 715)),
                    OcrToken("105,00", BoundingBox(870, 700, 930, 715)),
                    OcrToken("RITENUTA", BoundingBox(600, 760, 685, 775)),
                    OcrToken("-4,20", BoundingBox(880, 780, 930, 795)),
                    OcrToken("TOTALE", BoundingBox(600, 820, 660, 835)),
                    OcrToken("NETTO", BoundingBox(665, 820, 725, 835)),
                    OcrToken("FATTURA", BoundingBox(730, 820, 800, 835)),
                    OcrToken("123,90", BoundingBox(870, 820, 930, 835)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="TOTALE IMPONIBILE",
                        bbox=BoundingBox(600, 700, 760, 715),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="RITENUTA 4%",
                        bbox=BoundingBox(600, 760, 700, 775),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="TOTALE NETTO FATTURA EURO",
                        bbox=BoundingBox(600, 820, 835, 835),
                    ),
                ],
            )
        ],
    )

    candidates = find_economic_summary_regions(document)

    assert len(candidates) == 1
    assert candidates[0].selected_as_economic_summary is True
    assert candidates[0].score >= 80
    assert candidates[0].amount_count == 3
    assert "totale_imponibile" in candidates[0].anchor_context
    assert "ritenuta" in candidates[0].anchor_context
    assert "totale_netto_fattura" in candidates[0].anchor_context


def test_find_economic_summary_regions_requires_multiple_anchors() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("TOTALE", BoundingBox(600, 700, 660, 715)),
                    OcrToken("FATTURA", BoundingBox(665, 700, 760, 715)),
                    OcrToken("105,00", BoundingBox(870, 700, 930, 715)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="TOTALE FATTURA",
                        bbox=BoundingBox(600, 700, 760, 715),
                    ),
                ],
            )
        ],
    )

    assert find_economic_summary_regions(document) == []


def test_find_economic_summary_field_candidates_links_labels_to_nearest_right_amounts() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("TOTALE", BoundingBox(600, 700, 660, 715)),
                    OcrToken("IMPONIBILE", BoundingBox(665, 700, 760, 715)),
                    OcrToken("105,00.", BoundingBox(870, 720, 930, 735)),
                    OcrToken("RITENUTA", BoundingBox(600, 760, 685, 775)),
                    OcrToken("-4,20:", BoundingBox(880, 790, 930, 805)),
                    OcrToken("TOTALE", BoundingBox(600, 830, 660, 845)),
                    OcrToken("NETTO", BoundingBox(665, 830, 725, 845)),
                    OcrToken("FATTURA", BoundingBox(730, 830, 800, 845)),
                    OcrToken("123,90.", BoundingBox(870, 860, 930, 875)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="TOTALE IMPONIBILE",
                        bbox=BoundingBox(600, 700, 760, 715),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="RITENUTA 4%",
                        bbox=BoundingBox(600, 760, 700, 775),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="TOTALE NETTO FATTURA EURO",
                        bbox=BoundingBox(600, 830, 835, 845),
                    ),
                ],
            )
        ],
    )

    candidates = find_economic_summary_field_candidates(document)
    selected_by_target = {
        candidate.target: candidate
        for candidate in candidates
        if candidate.selected_as_field
    }

    assert selected_by_target["totale_imponibile"].normalized_value == "105.00"
    assert selected_by_target["ritenuta"].normalized_value == "-4.20"
    assert selected_by_target["totale_netto_fattura"].normalized_value == "123.90"
    assert selected_by_target["totale_imponibile"].value_text == "105,00"
    assert selected_by_target["ritenuta"].value_text == "-4,20"
    assert selected_by_target["totale_netto_fattura"].value_text == "123,90"


def test_resolve_economic_summary_rows_applies_ritenuta_math_checks() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("TOTALE", BoundingBox(600, 700, 660, 715)),
                    OcrToken("IMPONIBILE", BoundingBox(665, 700, 760, 715)),
                    OcrToken("105,00", BoundingBox(870, 720, 930, 735)),
                    OcrToken("TOTALE", BoundingBox(600, 750, 660, 765)),
                    OcrToken("FATTURA", BoundingBox(665, 750, 735, 765)),
                    OcrToken("EURO", BoundingBox(740, 750, 790, 765)),
                    OcrToken("128,10", BoundingBox(870, 770, 930, 785)),
                    OcrToken("RITENUTA", BoundingBox(600, 810, 685, 825)),
                    OcrToken("454", BoundingBox(690, 810, 725, 825)),
                    OcrToken("-4,20", BoundingBox(880, 835, 930, 850)),
                    OcrToken("TOTALE", BoundingBox(600, 880, 660, 895)),
                    OcrToken("NETTO", BoundingBox(665, 880, 725, 895)),
                    OcrToken("FATTURA", BoundingBox(730, 880, 800, 895)),
                    OcrToken("123,90", BoundingBox(870, 905, 930, 920)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="TOTALE IMPONIBILE",
                        bbox=BoundingBox(600, 700, 760, 715),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="TOTALE FATTURA EURO",
                        bbox=BoundingBox(600, 750, 790, 765),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="RITENUTA 454",
                        bbox=BoundingBox(600, 810, 725, 825),
                    ),
                    TextSpan(
                        span_id=4,
                        line_id=4,
                        page_span_index=4,
                        text="TOTALE NETTO FATTURA EURO",
                        bbox=BoundingBox(600, 880, 835, 895),
                    ),
                ],
            )
        ],
    )

    rows = resolve_economic_summary_rows(document)

    assert len(rows) == 1
    assert rows[0].totale_fattura_value == "128.10"
    assert rows[0].ritenuta_value == "-4.20"
    assert rows[0].ritenuta_percentuale == "4%"
    assert rows[0].totale_netto_fattura_value == "123.90"
    assert rows[0].math_check == "ok"


def test_resolve_economic_summary_rows_accepts_netto_a_pagare_direct_value() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("TOTALE", BoundingBox(600, 700, 660, 715)),
                    OcrToken("FATTURA", BoundingBox(665, 700, 735, 715)),
                    OcrToken("104,50", BoundingBox(870, 720, 930, 735)),
                    OcrToken("Ritenuta", BoundingBox(600, 760, 680, 775)),
                    OcrToken("4%", BoundingBox(690, 760, 720, 775)),
                    OcrToken("3,80", BoundingBox(880, 790, 930, 805)),
                    OcrToken("NETTO", BoundingBox(600, 830, 660, 845)),
                    OcrToken("A", BoundingBox(665, 830, 680, 845)),
                    OcrToken("PAGARE", BoundingBox(685, 830, 750, 845)),
                    OcrToken("100,70", BoundingBox(870, 860, 930, 875)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="TOTALE FATTURA",
                        bbox=BoundingBox(600, 700, 735, 715),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="Ritenuta d'acconto 4%",
                        bbox=BoundingBox(600, 760, 760, 775),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="NETTO A PAGARE",
                        bbox=BoundingBox(600, 830, 750, 845),
                    ),
                ],
            )
        ],
    )

    rows = resolve_economic_summary_rows(document)

    assert rows[0].totale_netto_fattura_text == "100,70"
    assert rows[0].totale_netto_fattura_value == "100.70"
    assert rows[0].totale_netto_fattura_source == "direct"
    assert rows[0].ritenuta_percentuale == "4%"
    assert rows[0].math_check == "ok"


def test_viewer_invoice_net_total_prefers_value_consistent_with_total_and_withholding() -> None:
    page_manifests = [
        {
            "page": 1,
            "spans": [],
            "targets": [
                {
                    "kind": "economic_summary.ritenuta.value",
                    "value": "3,80",
                    "score": 100,
                    "page": 1,
                    "bbox": {"x1": 800, "y1": 100, "x2": 860, "y2": 120},
                    "role": "value",
                },
                {
                    "kind": "economic_summary.totale_netto_fattura.value",
                    "value": "100,70",
                    "score": 150,
                    "page": 1,
                    "bbox": {"x1": 800, "y1": 150, "x2": 860, "y2": 170},
                    "role": "value",
                },
            ],
        },
        {
            "page": 2,
            "spans": [],
            "targets": [
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "104,50",
                    "score": 180,
                    "page": 2,
                    "bbox": {"x1": 800, "y1": 100, "x2": 860, "y2": 120},
                    "role": "value",
                },
                {
                    "kind": "economic_summary.totale_netto_fattura.value",
                    "value": "400,70",
                    "score": 170,
                    "page": 2,
                    "bbox": {"x1": 800, "y1": 150, "x2": 860, "y2": 170},
                    "role": "value",
                },
            ],
        },
    ]

    invoice = build_viewer_invoices(page_manifests)[0]
    fields = {field["kind"]: field for field in invoice["fields"]}

    assert fields["economic_summary.totale_netto_fattura.value"]["value"] == "100,70"
    assert fields["economic_summary.totale_netto_fattura.value"]["page"] == 1


# SECTION: Viewer manifest contract
# Tests the JSON contract consumed by the React viewer. It starts from a
# constructed DocumentRepresentation plus extractor candidates; it does not
# re-run OCR from the original PDF.
def test_save_viewer_manifest_writes_stable_overlay_contract() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("TOTALE", BoundingBox(600, 700, 660, 715), 0.95),
                    OcrToken("30/09/2024", BoundingBox(400, 660, 500, 675), 0.61),
                    OcrToken("IMPONIBILE", BoundingBox(665, 700, 760, 715), 0.95),
                    OcrToken("105,00", BoundingBox(870, 720, 930, 735), 0.95),
                    OcrToken("TOTALE", BoundingBox(600, 750, 660, 765), 0.95),
                    OcrToken("FATTURA", BoundingBox(665, 750, 735, 765), 0.95),
                    OcrToken("EURO", BoundingBox(740, 750, 790, 765), 0.95),
                    OcrToken("128,10", BoundingBox(870, 770, 930, 785), 0.95),
                    OcrToken("RITENUTA", BoundingBox(600, 810, 685, 825), 0.95),
                    OcrToken("454", BoundingBox(690, 810, 725, 825), 0.95),
                    OcrToken("-4,20", BoundingBox(880, 835, 930, 850), 0.55),
                    OcrToken("TOTALE", BoundingBox(600, 880, 660, 895), 0.95),
                    OcrToken("NETTO", BoundingBox(665, 880, 725, 895), 0.95),
                    OcrToken("FATTURA", BoundingBox(730, 880, 800, 895), 0.95),
                    OcrToken("123,90", BoundingBox(870, 905, 930, 920), 0.95),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="TOTALE IMPONIBILE",
                        bbox=BoundingBox(600, 700, 760, 715),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="TOTALE FATTURA EURO",
                        bbox=BoundingBox(600, 750, 790, 765),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="RITENUTA 454",
                        bbox=BoundingBox(600, 810, 725, 825),
                    ),
                    TextSpan(
                        span_id=4,
                        line_id=4,
                        page_span_index=4,
                        text="TOTALE NETTO FATTURA EURO",
                        bbox=BoundingBox(600, 880, 835, 895),
                    ),
                ],
            )
        ],
    )
    regions = find_economic_summary_regions(document)
    fields = find_economic_summary_field_candidates(document)
    rows = resolve_economic_summary_rows(document)

    output_path = save_viewer_manifest(
        document=document,
        output_path=Path("outputs/debug/test-viewer-manifest/viewer_manifest.json"),
        vat_candidates=[],
        fiscal_identifier_candidates=[],
        issuer_name_candidates=[],
        invoice_number_candidates=[],
        invoice_date_candidates=[
            InvoiceDateCandidate(
                page=1,
                raw_text="30/09/2024",
                normalized_value="2024-09-30",
                bbox=BoundingBox(400, 660, 500, 675),
                score=170.0,
                selected_as_data_fattura=True,
                anchor_context="fattura|del",
                negative_context="",
                nearby_text="fattura del 30/09/2024",
            )
        ],
        condominium_candidates=[],
        economic_summary_regions=regions,
        economic_summary_field_candidates=fields,
        economic_summary_resolved_rows=rows,
    )

    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    page = manifest["pages"][0]
    invoice = manifest["invoices"][0]
    target_kinds = {target["kind"] for target in page["targets"]}
    invoice_field_kinds = {field["kind"] for field in invoice["fields"]}
    ritenuta_value = next(
        target
        for target in page["targets"]
        if target["kind"] == "economic_summary.ritenuta.value"
    )
    data_fattura_field = next(
        field for field in invoice["fields"] if field["kind"] == "data_fattura"
    )

    assert manifest["schema_version"] == 1
    assert manifest["source_file"] == "fattura.pdf"
    assert invoice["invoice_id"] == "invoice-001"
    assert invoice["page_numbers"] == [1]
    assert "condominio" in invoice_field_kinds
    assert "economic_summary.ritenuta_percentuale" in invoice_field_kinds
    ritenuta_percentuale_field = next(
        field
        for field in invoice["fields"]
        if field["kind"] == "economic_summary.ritenuta_percentuale"
    )
    assert ritenuta_percentuale_field["value"] == "4%"
    assert ritenuta_percentuale_field["page"] == 1
    assert ritenuta_percentuale_field["role"] == "value"
    assert ritenuta_percentuale_field["bbox"]
    assert ritenuta_percentuale_field["ocr_confidence"] == 0.55
    assert data_fattura_field["value"] == "2024-09-30"
    assert data_fattura_field["ocr_confidence"] == 0.61
    assert page["image"] == "page-001-boxes.png"
    assert page["tokens"][0]["confidence"] == 0.95
    assert page["spans"][0]["text"] == "TOTALE IMPONIBILE"
    assert "economic_summary" in target_kinds
    assert "economic_summary.ritenuta" in target_kinds
    assert "economic_summary.ritenuta.label" in target_kinds
    assert "economic_summary.ritenuta.value" in target_kinds
    assert ritenuta_value["role"] == "value"
    assert ritenuta_value["label"] == ""
    assert ritenuta_value["ocr_confidence"] == 0.55
    assert ritenuta_value["has_low_confidence_token"] is True
    assert page["candidate_tables"]["economic_summary_resolved_rows"][0][
        "math_check"
    ] == "ok"


# SECTION: Viewer invoice segmentation and invoice-level field resolution
# Tests how page manifests are split into invoices, how invoice ids/titles are
# assigned, and how invoice fields are resolved from page targets/candidate
# tables. These tests validate the manifest/viewer layer, not OCR execution.
def test_viewer_invoices_split_pages_when_invoice_number_changes() -> None:
    page_manifests = [
        {
            "page": 1,
            "targets": [
                {"kind": "numero_fattura", "value": "A-001", "score": 120},
                {"kind": "data_fattura", "value": "01/01/2026", "score": 80},
                {"kind": "p_iva", "value": "01312380536", "score": 100},
                {"kind": "codice_fiscale", "value": "90006070537", "score": 100},
                {"kind": "economic_summary", "value": "", "score": 60},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "100,00",
                    "score": 100,
                },
            ],
        },
        {
            "page": 2,
            "targets": [
                {"kind": "numero_fattura", "value": "A-001", "score": 120},
                {"kind": "data_fattura", "value": "01/01/2026", "score": 80},
                {"kind": "p_iva", "value": "01312380536", "score": 100},
                {"kind": "codice_fiscale", "value": "90006070537", "score": 100},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "100,00",
                    "score": 100,
                },
            ],
        },
        {
            "page": 3,
            "targets": [
                {"kind": "numero_fattura", "value": "B-002", "score": 130},
                {"kind": "data_fattura", "value": "02/01/2026", "score": 80},
                {"kind": "p_iva", "value": "01312380536", "score": 100},
                {"kind": "codice_fiscale", "value": "90006070537", "score": 100},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "200,00",
                    "score": 100,
                },
            ],
        },
    ]

    invoices = build_viewer_invoices(page_manifests)

    assert [invoice["page_numbers"] for invoice in invoices] == [[1, 2], [3]]
    assert next(
        field for field in invoices[0]["fields"] if field["kind"] == "numero_fattura"
    )["value"] == "A-001"
    assert next(
        field for field in invoices[1]["fields"] if field["kind"] == "numero_fattura"
    )["value"] == "B-002"
    assert page_manifests[0]["invoice_start_decision"]["starts_invoice"] is True
    assert "prima_pagina" in page_manifests[0]["invoice_start_decision"]["reasons"]
    assert page_manifests[1]["invoice_start_decision"]["starts_invoice"] is False
    assert page_manifests[2]["invoice_start_decision"]["starts_invoice"] is True
    assert page_manifests[2]["invoice_start_decision"]["changed_signal_count"] == 3


def test_viewer_invoices_can_start_from_batch_invoice_number() -> None:
    page_manifests = [
        {
            "page": 1,
            "targets": [
                {"kind": "numero_fattura", "value": "A-001", "score": 120},
                {"kind": "data_fattura", "value": "01/01/2026", "score": 80},
                {"kind": "p_iva", "value": "01312380536", "score": 100},
                {"kind": "codice_fiscale", "value": "90006070537", "score": 100},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "100,00",
                    "score": 100,
                },
            ],
        },
        {
            "page": 2,
            "targets": [
                {"kind": "numero_fattura", "value": "B-002", "score": 130},
                {"kind": "data_fattura", "value": "02/01/2026", "score": 80},
                {"kind": "p_iva", "value": "01312380536", "score": 100},
                {"kind": "codice_fiscale", "value": "90006070537", "score": 100},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "200,00",
                    "score": 100,
                },
            ],
        },
    ]

    invoices = build_viewer_invoices(page_manifests, first_invoice_number=3)

    assert [invoice["invoice_id"] for invoice in invoices] == [
        "invoice-003",
        "invoice-004",
    ]
    assert [invoice["title"] for invoice in invoices] == [
        "Fattura 003",
        "Fattura 004",
    ]


def test_viewer_invoices_uses_aligned_invoice_header_as_split_signal() -> None:
    page_manifests = [
        {
            "page": 1,
            "spans": [],
            "targets": [
                {"kind": "condominio", "value": "CONDOMINIO ARIOSTO", "score": 100},
                {"kind": "numero_fattura", "value": "3FC24014301", "score": 180},
                {"kind": "data_fattura", "value": "2024-05-22", "score": 130},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "100,70",
                    "score": 120,
                },
            ],
        },
        {
            "page": 2,
            "spans": [
                {
                    "text": "FATTURA NUMERO.",
                    "bbox": {"x1": 105, "y1": 930, "x2": 260, "y2": 950},
                },
                {
                    "text": "DEL.",
                    "bbox": {"x1": 106, "y1": 968, "x2": 140, "y2": 988},
                },
                {
                    "text": "IMPORTO DA PAGARE €",
                    "bbox": {"x1": 105, "y1": 1013, "x2": 302, "y2": 1033},
                },
                {
                    "text": "ENTRO IL",
                    "bbox": {"x1": 105, "y1": 1050, "x2": 181, "y2": 1070},
                },
                {
                    "text": "COD. FISCALE //PIVA",
                    "bbox": {"x1": 104, "y1": 1130, "x2": 280, "y2": 1150},
                },
            ],
            "targets": [
                {"kind": "condominio", "value": "CONDOMINIO ARIOSTO", "score": 100},
                {"kind": "p_iva", "value": "01729590032", "score": 130},
                {"kind": "numero_fattura", "value": "3FO24061716", "score": 180},
                {"kind": "data_fattura", "value": "2024-06-20", "score": 130},
            ],
        },
    ]

    invoices = build_viewer_invoices(page_manifests)

    assert [invoice["page_numbers"] for invoice in invoices] == [[1], [2]]
    assert page_manifests[1]["invoice_signature"]["signal_count"] == 5
    assert "invoice_header" in page_manifests[1]["invoice_signature"]["fields"]
    assert page_manifests[1]["invoice_header_regions"][0]["labels"] == [
        "codice_fiscale_piva",
        "del",
        "entro_il",
        "fattura_numero",
        "importo_da_pagare",
    ]
    assert page_manifests[1]["invoice_header_regions"][0]["values"][
        "numero_fattura"
    ] == "3FO24061716"
    assert page_manifests[1]["invoice_start_decision"]["changed_signal_count"] == 3


def test_viewer_invoices_lowers_split_threshold_after_payment_summary_page() -> None:
    page_manifests = [
        {
            "page": 1,
            "spans": [
                {
                    "text": "Fattura",
                    "bbox": {"x1": 10, "y1": 10, "x2": 80, "y2": 30},
                }
            ],
            "targets": [
                {"kind": "numero_fattura", "value": "52", "score": 150},
                {"kind": "data_fattura", "value": "2024-04-08", "score": 120},
                {"kind": "p_iva", "value": "01622230538", "score": 130},
                {"kind": "codice_fiscale", "value": "90002170539", "score": 100},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "540,46",
                    "score": 120,
                },
            ],
        },
        {
            "page": 2,
            "spans": [
                {
                    "text": "Bonifico Istantaneo",
                    "bbox": {"x1": 10, "y1": 100, "x2": 180, "y2": 120},
                },
                {
                    "text": "Beneficiario ELETTRONICA STRIANESE",
                    "bbox": {"x1": 10, "y1": 130, "x2": 300, "y2": 150},
                },
                {
                    "text": "Pagamento fattura n.52 del 08/04/2024",
                    "bbox": {"x1": 10, "y1": 170, "x2": 360, "y2": 190},
                },
            ],
            "targets": [
                {"kind": "numero_fattura", "value": "52", "score": 130},
                {"kind": "data_fattura", "value": "2025-01-29", "score": 170},
            ],
        },
        {
            "page": 3,
            "spans": [],
            "targets": [
                {"kind": "condominio", "value": "CONDOMINIO VIA ALLENDE", "score": 80},
            ],
        },
        {
            "page": 4,
            "spans": [],
            "targets": [
                {"kind": "numero_fattura", "value": "13", "score": 130},
                {"kind": "data_fattura", "value": "2024-01-08", "score": 170},
            ],
        },
    ]

    invoices = build_viewer_invoices(page_manifests)

    assert [invoice["page_numbers"] for invoice in invoices] == [[1, 2, 3], [4]]
    assert page_manifests[1]["invoice_start_decision"]["starts_invoice"] is False
    assert page_manifests[2]["invoice_start_decision"]["starts_invoice"] is False
    assert page_manifests[3]["invoice_start_decision"]["starts_invoice"] is True
    assert (
        "dopo_riepilogo_bancario_almeno_2_segnali_diversi"
        in page_manifests[3]["invoice_start_decision"]["reasons"]
    )


def test_viewer_invoice_date_prefers_date_after_current_invoice_number() -> None:
    page_manifests = [
        {
            "page": 1,
            "spans": [],
            "tokens": [
                {
                    "text": "n.3FC24014301",
                    "bbox": {"x1": 680, "y1": 1727, "x2": 848, "y2": 1746},
                },
                {
                    "text": "22/05/24",
                    "bbox": {"x1": 900, "y1": 1728, "x2": 991, "y2": 1746},
                },
                {
                    "text": "n.3FO24061716",
                    "bbox": {"x1": 1019, "y1": 1728, "x2": 1190, "y2": 1746},
                },
                {
                    "text": "20/06/24",
                    "bbox": {"x1": 1240, "y1": 1728, "x2": 1331, "y2": 1747},
                },
            ],
            "targets": [
                {"kind": "numero_fattura", "value": "3FO24061716", "score": 180},
            ],
            "candidate_tables": {
                "invoice_dates": [
                    {
                        "raw_text": "22/05/24",
                        "normalized_value": "2024-05-22",
                        "score": 170.0,
                        "anchor_context": "data|fattura|del|numero_fattura",
                        "negative_context": "",
                        "bbox": {"x1": 900, "y1": 1728, "x2": 991, "y2": 1746},
                    },
                    {
                        "raw_text": "20/06/24",
                        "normalized_value": "2024-06-20",
                        "score": 170.0,
                        "anchor_context": "data|fattura|del|numero_fattura",
                        "negative_context": "",
                        "bbox": {"x1": 1240, "y1": 1728, "x2": 1331, "y2": 1747},
                    },
                ]
            },
        }
    ]

    invoices = build_viewer_invoices(page_manifests)

    fields = {field["kind"]: field for field in invoices[0]["fields"]}
    assert fields["data_fattura"]["value"] == "2024-06-20"


def test_viewer_invoices_extracts_paid_date_from_bank_context() -> None:
    page_manifests = [
        {
            "page": 1,
            "spans": [],
            "tokens": [],
            "targets": [
                {
                    "kind": "data_fattura",
                    "value": "2024-09-30",
                    "score": 170,
                    "page": 1,
                    "bbox": {"x1": 100, "y1": 100, "x2": 180, "y2": 120},
                    "ocr_confidence": 0.96,
                }
            ],
            "candidate_tables": {
                "invoice_dates": [
                    {
                        "normalized_value": "2024-09-30",
                        "score": 170,
                        "ocr_confidence": 0.96,
                        "bbox": {"x1": 100, "y1": 100, "x2": 180, "y2": 120},
                        "nearby_text": "Fattura del 30/09/2024",
                    },
                    {
                        "normalized_value": "2025-01-14",
                        "score": 170,
                        "ocr_confidence": 0.91,
                        "bbox": {"x1": 420, "y1": 500, "x2": 510, "y2": 520},
                        "nearby_text": (
                            "Bonifico Europeo Unico Data/Ora: 14.01.2025 "
                            "Stato: Inoltrata Data esecuzione: 14.01.2025 "
                            "Tipo di bonifico Credit Transfer"
                        ),
                    },
                ]
            },
        }
    ]

    invoice = build_viewer_invoices(page_manifests)[0]
    fields = {field["kind"]: field for field in invoice["fields"]}

    assert fields["data_fattura"]["value"] == "2024-09-30"
    assert fields["pagata_il"]["value"] == "2025-01-14"
    assert fields["pagata_il"]["page"] == 1
    assert fields["pagata_il"]["bbox"] == {
        "x1": 420,
        "y1": 500,
        "x2": 510,
        "y2": 520,
    }
    assert fields["pagata_il"]["ocr_confidence"] == 0.91


def test_viewer_invoices_links_shared_payment_to_referenced_invoice_numbers() -> None:
    page_manifests = [
        {
            "page": 1,
            "spans": [],
            "tokens": [],
            "targets": [
                {"kind": "condominio", "value": "COND.ARIOSTO 10", "score": 100},
                {"kind": "p_iva", "value": "01729590032", "score": 100},
                {"kind": "codice_fiscale", "value": "90000080532", "score": 100},
                {"kind": "numero_fattura", "value": "3FC24014301", "score": 180},
                {"kind": "data_fattura", "value": "2024-05-22", "score": 130},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "104,50",
                    "score": 120,
                },
            ],
            "candidate_tables": {
                "invoice_dates": [
                    {
                        "normalized_value": "2024-05-22",
                        "score": 130,
                        "nearby_text": "Fattura numero 3FC24014301 del 22/05/2024",
                    }
                ]
            },
        },
        {
            "page": 2,
            "spans": [],
            "tokens": [],
            "targets": [
                {"kind": "condominio", "value": "COND.ARIOSTO 10", "score": 100},
                {"kind": "p_iva", "value": "01729590032", "score": 100},
                {"kind": "codice_fiscale", "value": "90000080532", "score": 100},
                {"kind": "numero_fattura", "value": "3FO24061716", "score": 180},
                {"kind": "data_fattura", "value": "2024-06-20", "score": 130},
                {
                    "kind": "economic_summary.totale_fattura.value",
                    "value": "643,08",
                    "score": 120,
                },
            ],
            "candidate_tables": {
                "invoice_dates": [
                    {
                        "normalized_value": "2024-06-20",
                        "score": 130,
                        "nearby_text": "Fattura numero 3FO24061716 del 20/06/2024",
                    },
                    {
                        "normalized_value": "2025-01-21",
                        "score": 170,
                        "ocr_confidence": 0.93,
                        "bbox": {"x1": 120, "y1": 500, "x2": 220, "y2": 520},
                        "nearby_text": (
                            "Bonifico Europeo Unico Data esecuzione 21.01.2025 "
                            "Pagamento fattura n.3FC24014301 del 22/05/24 "
                            "n.3FO24061716 del 20/06/24"
                        ),
                    },
                ]
            },
        },
    ]

    invoices = build_viewer_invoices(page_manifests)
    first_fields = {field["kind"]: field for field in invoices[0]["fields"]}
    second_fields = {field["kind"]: field for field in invoices[1]["fields"]}

    assert [invoice["page_numbers"] for invoice in invoices] == [[1, 2], [2]]
    assert first_fields["pagata_il"]["value"] == "2025-01-21"
    assert first_fields["pagata_il"]["role"] == "payment_event"
    assert first_fields["pagata_il"]["page"] == 2
    assert second_fields["pagata_il"]["value"] == "2025-01-21"


# SECTION: Economic summary regression variants
# Tests additional real-layout variants for economic summary resolution,
# especially tot documento / rit acconto / tot dovuto rows.
def test_resolve_economic_summary_rows_accepts_tot_documento_rit_acconto_tot_dovuto_variant() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Tot.", BoundingBox(600, 700, 635, 715)),
                    OcrToken("imponibile", BoundingBox(640, 700, 740, 715)),
                    OcrToken("443,00", BoundingBox(880, 700, 940, 715)),
                    OcrToken("Tot.", BoundingBox(600, 750, 635, 765)),
                    OcrToken("Iva", BoundingBox(640, 750, 670, 765)),
                    OcrToken("97,46", BoundingBox(890, 750, 940, 765)),
                    OcrToken("Tot.", BoundingBox(600, 800, 635, 815)),
                    OcrToken("documento", BoundingBox(640, 800, 740, 815)),
                    OcrToken("540,46", BoundingBox(880, 800, 940, 815)),
                    OcrToken("Rit.", BoundingBox(600, 850, 635, 865)),
                    OcrToken("acconto", BoundingBox(640, 850, 710, 865)),
                    OcrToken("4%", BoundingBox(715, 850, 740, 865)),
                    OcrToken("su", BoundingBox(745, 850, 765, 865)),
                    OcrToken("443,00", BoundingBox(790, 850, 850, 865)),
                    OcrToken("-17,72", BoundingBox(880, 850, 940, 865)),
                    OcrToken("Tot.", BoundingBox(600, 910, 635, 925)),
                    OcrToken("dovuto", BoundingBox(640, 910, 710, 925)),
                    OcrToken("522,74", BoundingBox(880, 910, 940, 925)),
                ],
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="Tot. imponibile",
                        bbox=BoundingBox(600, 700, 740, 715),
                    ),
                    TextSpan(
                        span_id=2,
                        line_id=2,
                        page_span_index=2,
                        text="Tot. Iva",
                        bbox=BoundingBox(600, 750, 670, 765),
                    ),
                    TextSpan(
                        span_id=3,
                        line_id=3,
                        page_span_index=3,
                        text="Tot. documento",
                        bbox=BoundingBox(600, 800, 740, 815),
                    ),
                    TextSpan(
                        span_id=4,
                        line_id=4,
                        page_span_index=4,
                        text="Rit. acconto 4% su € 443,00",
                        bbox=BoundingBox(600, 850, 850, 865),
                    ),
                    TextSpan(
                        span_id=5,
                        line_id=5,
                        page_span_index=5,
                        text="Tot. dovuto",
                        bbox=BoundingBox(600, 910, 710, 925),
                    ),
                ],
            )
        ],
    )

    rows = resolve_economic_summary_rows(document)

    assert len(rows) == 1
    assert rows[0].totale_imponibile_value == "443.00"
    assert rows[0].totale_fattura_value == "540.46"
    assert rows[0].ritenuta_value == "-17.72"
    assert rows[0].ritenuta_percentuale == "4%"
    assert rows[0].totale_netto_fattura_value == "522.74"
    assert rows[0].math_check == "ok"


# SECTION: Invoice number regression cases
# Tests stricter selection/exclusion behavior for invoice numbers that were
# ambiguous in real documents or easy to confuse with other numeric values.
def test_find_invoice_number_candidates_requires_anchor_for_selection() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Cliente", BoundingBox(10, 10, 80, 25)),
                    OcrToken("24P00022813.MA.P", BoundingBox(90, 10, 260, 25)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates[0].score == 60
    assert candidates[0].anchor_context == ""
    assert candidates[0].selected_as_numero_fattura is False


def test_find_invoice_number_candidates_rejects_tax_codes_amounts_and_dates() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("Numero", BoundingBox(10, 10, 80, 25)),
                    OcrToken("fattura", BoundingBox(85, 10, 145, 25)),
                    OcrToken("90000080532", BoundingBox(155, 10, 275, 25)),
                    OcrToken("100,70", BoundingBox(155, 40, 220, 55)),
                    OcrToken("10/06/2024", BoundingBox(155, 70, 260, 85)),
                ],
            )
        ],
    )

    candidates = find_invoice_number_candidates(document)

    assert candidates == []


# SECTION: VAT extraction from OCR tokens
# Tests P.IVA candidate scoring, accepted anchor variants, OCR-dirty labels,
# mixed fiscal labels, and one-best-candidate selection per page.
def test_find_vat_candidates_scores_positive_and_negative_context() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("P.I.", BoundingBox(10, 10, 40, 25)),
                    OcrToken("01303760530", BoundingBox(50, 10, 150, 25)),
                    OcrToken("CODICE", BoundingBox(10, 100, 70, 115)),
                    OcrToken("FISCALE", BoundingBox(75, 100, 140, 115)),
                    OcrToken("90006070537", BoundingBox(150, 100, 250, 115)),
                ],
            )
        ],
    )

    candidates = find_vat_candidates(document)

    by_value = {candidate.normalized_value: candidate for candidate in candidates}
    assert by_value["01303760530"].score > by_value["90006070537"].score
    assert by_value["01303760530"].positive_context == "p.i."
    assert "codice fiscale" in by_value["90006070537"].negative_context


def test_find_vat_candidates_accepts_common_piva_anchor_variants() -> None:
    anchor_variants = [
        (["P", "IVA"], "piva"),
        (["P.", "IVA"], "piva"),
        (["PIVA"], "piva"),
        (["PVA"], "pva"),
        (["Partita", "IVA"], "partita iva"),
    ]

    for anchor_tokens, expected_context in anchor_variants:
        tokens = [
            OcrToken(
                text=anchor,
                bbox=BoundingBox(
                    x1=10 + index * 30,
                    y1=10,
                    x2=35 + index * 30,
                    y2=25,
                ),
            )
            for index, anchor in enumerate(anchor_tokens)
        ]
        tokens.append(OcrToken("01303760530", BoundingBox(150, 10, 250, 25)))
        document = DocumentRepresentation(
            source_file=Path("fattura.pdf"),
            pages=[
                DocumentPage(
                    page_number=1,
                    width=1000,
                    height=1000,
                    tokens=tokens,
                )
            ],
        )

        candidates = find_vat_candidates(document)

        assert candidates[0].positive_context == expected_context


def test_find_vat_candidates_accepts_ocr_dirty_piva_anchor() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("P.AVA", BoundingBox(10, 10, 60, 25)),
                    OcrToken("01729590032", BoundingBox(70, 10, 180, 25)),
                ],
            )
        ],
    )

    candidates = find_vat_candidates(document)

    assert candidates[0].normalized_value == "01729590032"
    assert candidates[0].positive_context == "piva"


def test_find_vat_candidates_accepts_mixed_fiscal_code_vat_label() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("COD.", BoundingBox(10, 10, 50, 25)),
                    OcrToken("FISCA", BoundingBox(55, 10, 105, 25)),
                    OcrToken("PVA", BoundingBox(110, 10, 145, 25)),
                    OcrToken("01303760530", BoundingBox(160, 10, 260, 25)),
                ],
            )
        ],
    )

    candidates = find_vat_candidates(document)
    selected = select_best_vat_candidate_per_page(candidates)

    assert candidates[0].positive_context == "pva"
    assert candidates[0].negative_context == "cod fisca"
    assert selected[1] is not None
    assert selected[1].normalized_value == "01303760530"


def test_select_best_vat_candidate_per_page_selects_only_one() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=1000,
                height=1000,
                tokens=[
                    OcrToken("P.IVA", BoundingBox(10, 10, 50, 25)),
                    OcrToken("01303760530", BoundingBox(60, 10, 160, 25)),
                    OcrToken("90006070537", BoundingBox(60, 100, 160, 115)),
                ],
            )
        ],
    )

    candidates = find_vat_candidates(document)
    selected = select_best_vat_candidate_per_page(candidates)

    assert selected[1] is not None
    assert selected[1].normalized_value == "01303760530"


# SECTION: Document and perception data structures
# Tests that OCR/layout/table structures can be stored and that the empty
# perception backend creates page records with the expected shape.
def test_document_representation_can_store_ocr_layout_and_table_data() -> None:
    bbox = BoundingBox(x1=1, y1=2, x2=100, y2=50)

    token = OcrToken(text="Totale", bbox=bbox, confidence=0.98)
    block = LayoutBlock(
        block_type=LayoutBlockType.TEXT,
        bbox=bbox,
        confidence=0.91,
        text="Totale 1234.56",
    )
    table = TableRegion(
        bbox=bbox,
        confidence=0.89,
        html="<table></table>",
        rows=[["Aliquota", "Importo"]],
    )

    document = DocumentRepresentation(source_file=Path("fattura.pdf"))
    analyzed_document = analyze_pages(
        [Image.new("RGB", (200, 300), "white")],
        document.source_file,
    )
    page = analyzed_document.pages[0]
    document.pages.append(page)

    page.tokens.append(token)
    page.blocks.append(block)
    page.tables.append(table)

    assert document.pages[0].width == 200
    assert document.pages[0].height == 300
    assert document.pages[0].tokens == [token]
    assert document.pages[0].lines == []
    assert document.pages[0].blocks == [block]
    assert document.pages[0].tables == [table]


def test_analyze_pages_creates_empty_document_pages() -> None:
    source_file = Path("data/input/fattura.pdf")
    images = [
        Image.new("RGB", (100, 200), "white"),
        Image.new("RGB", (300, 400), "white"),
    ]

    document = analyze_pages(images, source_file)

    assert document.source_file == source_file
    assert document.backend == PerceptionBackend.EMPTY
    assert len(document.pages) == 2
    assert document.pages[0].page_number == 1
    assert document.pages[0].width == 100
    assert document.pages[0].height == 200
    assert document.pages[0].tokens == []
    assert document.pages[0].lines == []
    assert document.pages[0].blocks == []
    assert document.pages[0].tables == []
    assert document.pages[1].page_number == 2
    assert document.pages[1].width == 300
    assert document.pages[1].height == 400


# SECTION: Tesseract adapter and OCR layout normalization
# Tests conversion from Tesseract dictionaries, missing-confidence handling,
# line/span grouping, progressive ids, and duplicate-token cleanup.
def test_tesseract_data_to_tokens_converts_words_and_boxes() -> None:
    data = {
        "text": ["", "Totale", "123,45", "bad"],
        "conf": ["-1", "96.5", "88", "-1"],
        "left": [0, 10, 80, 1],
        "top": [0, 20, 20, 1],
        "width": [0, 60, 50, 1],
        "height": [0, 15, 15, 1],
    }

    tokens = tesseract_data_to_tokens(data)

    assert [token.text for token in tokens] == ["Totale", "123,45"]
    assert tokens[0].bbox == BoundingBox(x1=10, y1=20, x2=70, y2=35)
    assert tokens[0].confidence == 0.965
    assert tokens[1].confidence == 0.88


def test_tesseract_data_to_tokens_tolerates_missing_confidence() -> None:
    data = {
        "text": ["Totale", "123,45"],
        "left": [10, 80],
        "top": [20, 20],
        "width": [60, 50],
        "height": [15, 15],
    }

    tokens = tesseract_data_to_tokens(data)

    assert [token.text for token in tokens] == ["Totale", "123,45"]
    assert tokens[0].confidence is None
    assert tokens[1].confidence is None


def test_group_page_tokens_into_lines_orders_words_by_visual_line() -> None:
    page = DocumentPage(
        page_number=1,
        width=300,
        height=200,
        tokens=[
            OcrToken(
                text="123,45",
                bbox=BoundingBox(x1=90, y1=50, x2=140, y2=65),
                confidence=0.90,
            ),
            OcrToken(
                text="Totale",
                bbox=BoundingBox(x1=10, y1=49, x2=70, y2=64),
                confidence=0.95,
            ),
            OcrToken(
                text="P.IVA",
                bbox=BoundingBox(x1=10, y1=90, x2=55, y2=105),
                confidence=0.93,
            ),
        ],
    )

    lines = group_page_tokens_into_lines(page)

    assert [line.text for line in lines] == ["Totale 123,45", "P.IVA"]
    assert lines[0].line_id == 1
    assert lines[0].page_line_index == 1
    assert lines[0].bbox == BoundingBox(x1=10, y1=49, x2=140, y2=65)
    assert lines[0].y_center_n == 57 / 200


def test_split_line_into_spans_uses_datev_gap_rule() -> None:
    line = TextLine(
        line_id=1,
        page_line_index=1,
        text="CODICE FISCALE PARTITA IVA 90006070537",
        bbox=BoundingBox(10, 20, 330, 35),
        y_center=27.5,
        y_center_n=0.275,
        tokens=[
            OcrToken("CODICE", BoundingBox(10, 20, 50, 35)),
            OcrToken("FISCALE", BoundingBox(55, 20, 105, 35)),
            OcrToken("PARTITA", BoundingBox(180, 20, 235, 35)),
            OcrToken("IVA", BoundingBox(240, 20, 265, 35)),
            OcrToken("90006070537", BoundingBox(420, 20, 520, 35)),
        ],
    )

    spans = split_line_into_spans(line)

    assert [span.text for span in spans] == [
        "CODICE FISCALE",
        "PARTITA IVA",
        "90006070537",
    ]


def test_add_lines_to_document_uses_progressive_line_ids() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=100,
                height=100,
                tokens=[
                    OcrToken("A", BoundingBox(1, 10, 10, 20)),
                    OcrToken("B", BoundingBox(1, 40, 10, 50)),
                ],
            ),
            DocumentPage(
                page_number=2,
                width=100,
                height=100,
                tokens=[
                    OcrToken("C", BoundingBox(1, 10, 10, 20)),
                ],
            ),
        ],
    )

    add_lines_to_document(document)

    assert [line.line_id for page in document.pages for line in page.lines] == [1, 2, 3]
    assert [line.page_line_index for page in document.pages for line in page.lines] == [
        1,
        2,
        1,
    ]


def test_add_spans_to_document_uses_progressive_span_ids() -> None:
    document = DocumentRepresentation(
        source_file=Path("fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=200,
                height=100,
                lines=[
                    TextLine(
                        line_id=1,
                        page_line_index=1,
                        text="A B C",
                        bbox=BoundingBox(1, 10, 120, 20),
                        y_center=15,
                        y_center_n=0.15,
                        tokens=[
                            OcrToken("A", BoundingBox(1, 10, 10, 20)),
                            OcrToken("B", BoundingBox(15, 10, 25, 20)),
                            OcrToken("C", BoundingBox(100, 10, 120, 20)),
                        ],
                    )
                ],
            )
        ],
    )

    add_spans_to_document(document)

    assert [span.span_id for span in document.pages[0].spans] == [1, 2]
    assert [span.page_span_index for span in document.pages[0].spans] == [1, 2]


def test_deduplicate_overlapping_tokens_keeps_best_duplicate() -> None:
    tokens = [
        OcrToken(
            text="WwW",
            bbox=BoundingBox(x1=10, y1=20, x2=30, y2=35),
            confidence=0.41,
        ),
        OcrToken(
            text="W",
            bbox=BoundingBox(x1=11, y1=20, x2=31, y2=35),
            confidence=0.92,
        ),
        OcrToken(
            text="Totale",
            bbox=BoundingBox(x1=80, y1=20, x2=130, y2=35),
            confidence=0.95,
        ),
    ]

    deduplicated = deduplicate_overlapping_tokens(tokens)

    assert [token.text for token in deduplicated] == ["W", "Totale"]


def test_deduplicate_overlapping_tokens_keeps_adjacent_words() -> None:
    tokens = [
        OcrToken(
            text="Totale",
            bbox=BoundingBox(x1=10, y1=20, x2=60, y2=35),
            confidence=0.95,
        ),
        OcrToken(
            text="fattura",
            bbox=BoundingBox(x1=64, y1=20, x2=120, y2=35),
            confidence=0.93,
        ),
    ]

    deduplicated = deduplicate_overlapping_tokens(tokens)

    assert [token.text for token in deduplicated] == ["Totale", "fattura"]


# SECTION: Debug script helpers
# Tests local debug tooling helpers: output path conventions, supported file
# suffixes, and vendored Tesseract runtime configuration.
def test_debug_words_output_dir_uses_file_name_by_default() -> None:
    assert _debug_output_dir(Path("data/input/fattura.pdf")) == Path(
        "apps/viewer/public/sample/fattura"
    )


def test_debug_words_output_dir_can_append_comparison_suffix() -> None:
    assert _debug_output_dir(Path("data/input/fattura.pdf"), suffix="__ita") == Path(
        "apps/viewer/public/sample/fattura__ita"
    )


def test_default_tesseract_language_is_italian_only() -> None:
    assert DEFAULT_TESSERACT_LANG == "ita"


def test_is_supported_input_file_accepts_pdf_and_images(tmp_path) -> None:
    pdf = tmp_path / "fattura.pdf"
    png = tmp_path / "fattura.png"
    txt = tmp_path / "notes.txt"
    pdf.touch()
    png.touch()
    txt.touch()

    assert is_supported_input_file(pdf) is True
    assert is_supported_input_file(png) is True
    assert is_supported_input_file(txt) is False


def test_resolve_tesseract_cmd_prefers_vendored_runtime(
    monkeypatch,
    tmp_path,
) -> None:
    vendored_runtime = tmp_path / "vendor" / "tesseract" / "tesseract.exe"
    vendored_runtime.parent.mkdir(parents=True)
    vendored_runtime.touch()

    monkeypatch.setattr(tesseract_adapter, "_project_root", lambda: tmp_path)

    assert resolve_tesseract_cmd() == vendored_runtime


def test_configure_tessdata_prefix_uses_sibling_tessdata(
    monkeypatch,
    tmp_path,
) -> None:
    tesseract_cmd = tmp_path / "vendor" / "tesseract" / "tesseract.exe"
    tessdata_dir = tesseract_cmd.parent / "tessdata"
    tessdata_dir.mkdir(parents=True)
    tesseract_cmd.touch()
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)

    _configure_tessdata_prefix(tesseract_cmd)

    assert "TESSDATA_PREFIX" in os.environ
    assert Path(os.environ["TESSDATA_PREFIX"]) == tessdata_dir


def test_missing_tesseract_languages_reports_unavailable_requested_languages() -> None:
    assert _missing_tesseract_languages("ita+eng", ["eng", "osd"]) == ["ita"]
    assert _missing_tesseract_languages("ita+eng", ["eng", "ita", "osd"]) == []


# SECTION: Pipeline orchestration success path
# Tests process_invoice behavior when scan loading and perception succeed. It
# uses monkeypatched dependencies instead of real OCR.
def test_process_invoice_marks_analyzed_when_scan_load_succeeds(monkeypatch) -> None:
    source_file = Path("data/input/fattura.pdf")

    def fake_load_scan(file_path: Path) -> list[Image.Image]:
        assert file_path == source_file
        return [Image.new("RGB", (10, 20), "white")]

    monkeypatch.setattr(pipeline, "load_scan", fake_load_scan)

    result = pipeline.process_invoice(source_file)

    assert result.source_file == source_file
    assert result.status == DocumentStatus.ANALYZED
    assert result.document is not None
    assert result.document.pages[0].width == 10
    assert result.document.pages[0].height == 20
    assert result.rejection_reason is None


# SECTION: Debug output writers and viewer image annotations
# Tests JSON/CSV/debug-image writers and annotation behavior from small
# synthetic documents/images. It does not test extraction decisions.
def test_save_document_debug_json_writes_readable_document(tmp_path) -> None:
    document = DocumentRepresentation(
        source_file=Path("data/input/fattura.pdf"),
        backend=PerceptionBackend.EMPTY,
        pages=[
            DocumentPage(
                page_number=1,
                width=100,
                height=200,
                tokens=[
                    OcrToken(
                        text="Totale",
                        bbox=BoundingBox(x1=10, y1=20, x2=50, y2=35),
                        confidence=0.97,
                    )
                ],
                blocks=[
                    LayoutBlock(
                        block_type=LayoutBlockType.TEXT,
                        bbox=BoundingBox(x1=8, y1=18, x2=80, y2=45),
                        confidence=0.91,
                    )
                ],
                tables=[
                    TableRegion(
                        bbox=BoundingBox(x1=5, y1=60, x2=90, y2=120),
                        html="<table></table>",
                    )
                ],
            )
        ],
    )

    output_path = save_document_debug_json(document, tmp_path / "document.json")

    content = output_path.read_text(encoding="utf-8")
    assert '"backend": "empty"' in content
    assert '"text": "Totale"' in content
    assert '"block_type": "text"' in content
    assert "\n  " in content


def test_save_document_lines_csv_writes_line_records(tmp_path) -> None:
    document = DocumentRepresentation(
        source_file=Path("data/input/fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=100,
                height=200,
                lines=[
                    TextLine(
                        line_id=1,
                        page_line_index=1,
                        text="Totale 123,45",
                        bbox=BoundingBox(x1=10, y1=20, x2=90, y2=35),
                        y_center=27.5,
                        y_center_n=27.5 / 200,
                        tokens=[
                            OcrToken("Totale", BoundingBox(10, 20, 50, 35)),
                            OcrToken("123,45", BoundingBox(55, 20, 90, 35)),
                        ],
                    )
                ],
            )
        ],
    )

    output_path = save_document_lines_csv(document, tmp_path / "lines.csv")

    content = output_path.read_text(encoding="utf-8-sig")
    assert "source_file,page,line_id,page_line_index,text" in content
    assert "data/input/fattura.pdf,1,1,1,\"Totale 123,45\"" in content


def test_save_document_spans_csv_writes_span_records(tmp_path) -> None:
    document = DocumentRepresentation(
        source_file=Path("data/input/fattura.pdf"),
        pages=[
            DocumentPage(
                page_number=1,
                width=100,
                height=200,
                spans=[
                    TextSpan(
                        span_id=1,
                        line_id=1,
                        page_span_index=1,
                        text="Totale",
                        bbox=BoundingBox(x1=10, y1=20, x2=50, y2=35),
                        tokens=[
                            OcrToken("Totale", BoundingBox(10, 20, 50, 35)),
                        ],
                    )
                ],
            )
        ],
    )

    output_path = save_document_spans_csv(document, tmp_path / "spans.csv")

    content = output_path.read_text(encoding="utf-8-sig")
    assert "source_file,page,span_id,line_id,page_span_index,text" in content
    assert "data/input/fattura.pdf,1,1,1,1,Totale" in content


def test_save_page_debug_image_respects_token_box_flag(tmp_path) -> None:
    image = Image.new("RGB", (120, 120), "white")
    page = DocumentPage(
        page_number=1,
        width=120,
        height=120,
        spans=[
            TextSpan(
                span_id=1,
                line_id=1,
                page_span_index=1,
                text="IVA",
                bbox=BoundingBox(x1=8, y1=8, x2=52, y2=27),
            )
        ],
        tokens=[
            OcrToken(
                text="IVA",
                bbox=BoundingBox(x1=10, y1=10, x2=50, y2=25),
                confidence=0.88,
            )
        ],
        blocks=[
            LayoutBlock(
                block_type=LayoutBlockType.TABLE,
                bbox=BoundingBox(x1=5, y1=5, x2=80, y2=60),
            )
        ],
        tables=[
            TableRegion(
                bbox=BoundingBox(x1=6, y1=70, x2=90, y2=100),
            )
        ],
    )

    output_path = save_page_debug_image(page, image, tmp_path / "page.png")

    with Image.open(output_path) as annotated:
        assert annotated.size == (120, 120)
        token_border_pixel = annotated.getpixel((10, 10))
        if debug_flags.DRAW_TOKEN_BOXES:
            assert token_border_pixel != (255, 255, 255)
        else:
            assert token_border_pixel == (255, 255, 255)


def test_debug_flags_are_centralized_booleans() -> None:
    assert isinstance(debug_flags.DRAW_BLOCK_BOXES, bool)
    assert isinstance(debug_flags.DRAW_TABLE_BOXES, bool)
    assert isinstance(debug_flags.DRAW_SPAN_BOXES, bool)
    assert isinstance(debug_flags.DRAW_TOKEN_BOXES, bool)
    assert isinstance(debug_flags.DRAW_LEGACY_TARGET_BOXES, bool)
    assert isinstance(debug_flags.DRAW_RECOGNIZED_TARGETS, bool)


def test_save_page_debug_image_keeps_labeled_highlights_off_by_default() -> None:
    image = Image.new("RGB", (120, 120), "white")
    page = DocumentPage(page_number=1, width=120, height=120)
    output_path = save_page_debug_image(
        page=page,
        image=image,
        output_path=Path("outputs/debug/test-labeled-highlight/page.png"),
        highlights=[
            DebugHighlight(
                bbox=BoundingBox(x1=10, y1=20, x2=70, y2=40),
                label="p_iva",
            )
        ],
    )

    with Image.open(output_path) as annotated:
        assert annotated.size == (120, 120)
        assert annotated.getpixel((10, 20)) == (255, 255, 255)


def test_save_page_debug_image_draws_low_confidence_highlight_orange(
    monkeypatch,
) -> None:
    monkeypatch.setattr(debug_output, "DRAW_RECOGNIZED_TARGETS", True)
    image = Image.new("RGB", (120, 120), "white")
    page = DocumentPage(
        page_number=1,
        width=120,
        height=120,
        tokens=[
            OcrToken(
                text="SSSMRC81RO3F032J",
                bbox=BoundingBox(x1=12, y1=22, x2=68, y2=38),
                confidence=0.55,
            )
        ],
    )
    output_path = save_page_debug_image(
        page=page,
        image=image,
        output_path=Path("outputs/debug/test-low-confidence-highlight/page.png"),
        highlights=[
            DebugHighlight(
                bbox=BoundingBox(x1=10, y1=20, x2=70, y2=40),
                label="codice_fiscale_emittente",
            )
        ],
    )

    with Image.open(output_path) as annotated:
        assert annotated.getpixel((8, 18)) == (255, 159, 26)


def test_save_page_debug_image_keeps_non_sensitive_highlight_yellow(
    monkeypatch,
) -> None:
    monkeypatch.setattr(debug_output, "DRAW_RECOGNIZED_TARGETS", True)
    image = Image.new("RGB", (120, 120), "white")
    page = DocumentPage(
        page_number=1,
        width=120,
        height=120,
        tokens=[
            OcrToken(
                text="Totale",
                bbox=BoundingBox(x1=12, y1=22, x2=68, y2=38),
                confidence=0.55,
            )
        ],
    )
    output_path = save_page_debug_image(
        page=page,
        image=image,
        output_path=Path("outputs/debug/test-non-sensitive-highlight/page.png"),
        highlights=[
            DebugHighlight(
                bbox=BoundingBox(x1=10, y1=20, x2=70, y2=40),
                label="economic_summary",
                confidence_sensitive=False,
            )
        ],
    )

    with Image.open(output_path) as annotated:
        assert annotated.getpixel((8, 18)) == (255, 212, 0)


def test_save_page_debug_image_draws_unlabeled_highlight_without_label_plate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(debug_output, "DRAW_RECOGNIZED_TARGETS", True)
    image = Image.new("RGB", (120, 120), "white")
    page = DocumentPage(page_number=1, width=120, height=120)
    output_path = save_page_debug_image(
        page=page,
        image=image,
        output_path=Path("outputs/debug/test-unlabeled-highlight/page.png"),
        highlights=[
            DebugHighlight(
                bbox=BoundingBox(x1=10, y1=20, x2=70, y2=40),
                label="",
            )
        ],
    )

    with Image.open(output_path) as annotated:
        assert annotated.getpixel((8, 18)) == (255, 212, 0)
        assert annotated.getpixel((8, 10)) == (255, 255, 255)


# SECTION: Pipeline orchestration failure paths
# Tests process_invoice rejection reasons when scan loading returns no images,
# scan loading raises, or perception raises. Dependencies are monkeypatched.
def test_process_invoice_rejects_when_scan_load_fails(monkeypatch) -> None:
    source_file = Path("data/input/fattura.pdf")

    def fake_load_scan(file_path: Path) -> list[Image.Image]:
        raise RuntimeError("poppler not available")

    monkeypatch.setattr(pipeline, "load_scan", fake_load_scan)

    result = pipeline.process_invoice(source_file)

    assert result.source_file == source_file
    assert result.status == DocumentStatus.REJECTED
    assert result.rejection_reason == "scan load failed: poppler not available"


def test_process_invoice_rejects_when_scan_has_no_images(monkeypatch) -> None:
    source_file = Path("data/input/fattura.pdf")

    def fake_load_scan(file_path: Path) -> list[Image.Image] | None:
        return None

    monkeypatch.setattr(pipeline, "load_scan", fake_load_scan)

    result = pipeline.process_invoice(source_file)

    assert result.source_file == source_file
    assert result.status == DocumentStatus.REJECTED
    assert result.rejection_reason == "scan load returned no images"


def test_process_invoice_rejects_when_perception_fails(monkeypatch) -> None:
    source_file = Path("data/input/fattura.pdf")

    def fake_load_scan(file_path: Path) -> list[Image.Image]:
        assert file_path == source_file
        return [Image.new("RGB", (10, 20), "white")]

    def fake_analyze_pages(
        images: list[Image.Image],
        source_file: Path,
        backend: PerceptionBackend = PerceptionBackend.EMPTY,
    ) -> DocumentRepresentation:
        raise RuntimeError("ocr unavailable")

    monkeypatch.setattr(pipeline, "load_scan", fake_load_scan)
    monkeypatch.setattr(pipeline, "analyze_pages", fake_analyze_pages)

    result = pipeline.process_invoice(source_file)

    assert result.status == DocumentStatus.REJECTED
    assert result.rejection_reason == "perception failed: ocr unavailable"
