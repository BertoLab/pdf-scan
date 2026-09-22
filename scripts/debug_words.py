import argparse
import json
from pathlib import Path
from decimal import Decimal, InvalidOperation
import re
import shutil
import stat
from time import perf_counter

from estrattore.condominium_extraction import (
    CondominiumCandidate,
    find_condominium_candidates,
    save_condominium_candidates_csv,
)
from estrattore.debug_output import DebugHighlight, save_document_debug_output
from estrattore.economic_summary_extraction import (
    EconomicSummaryFieldCandidate,
    EconomicSummaryRegionCandidate,
    EconomicSummaryResolvedRow,
    find_economic_summary_field_candidates,
    find_economic_summary_regions,
    resolve_economic_summary_rows,
    save_economic_summary_field_candidates_csv,
    save_economic_summary_regions_csv,
    save_economic_summary_resolved_rows_csv,
)
from estrattore.fiscal_identifier_extraction import (
    FiscalIdentifierCandidate,
    find_fiscal_identifier_candidates,
    save_fiscal_identifier_candidates_csv,
)
from estrattore.invoice_number_extraction import (
    InvoiceNumberCandidate,
    find_invoice_number_candidates,
    save_invoice_number_candidates_csv,
)
from estrattore.invoice_date_extraction import (
    InvoiceDateCandidate,
    find_invoice_date_candidates,
    save_invoice_date_candidates_csv,
)
from estrattore.issuer_name_extraction import (
    IssuerNameCandidate,
    find_issuer_name_candidates,
    save_issuer_name_candidates_csv,
)
from estrattore.io import load_scan
from estrattore.model import BoundingBox, DocumentRepresentation, PerceptionBackend
from estrattore.perception import analyze_pages
from estrattore.registry import DEFAULT_REGISTRY_CACHE_PATH, load_registry_cache
from estrattore.tesseract_adapter import (
    DEFAULT_TESSERACT_LANG,
    DEFAULT_TESSERACT_PROFILE,
    analyze_pages_with_tesseract,
)
from estrattore.viewer_invoice_segmentation import (
    INVOICE_FIELD_DEFINITIONS,
    build_viewer_invoices,
)
from estrattore.vat_extraction import (
    VatCandidate,
    find_vat_candidates,
    save_vat_candidates_csv,
    select_best_vat_candidate_per_page,
)


SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
VIEWER_MANIFEST_SCHEMA_VERSION = 2
VIEWER_SAMPLE_ROOT = Path("apps") / "viewer" / "public" / "sample"
MONEY_TOKEN_PATTERN = re.compile(
    r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2}"
)
FALLBACK_MISSING_TARGET_THRESHOLD = 4
FALLBACK_AVG_OCR_CONFIDENCE_THRESHOLD = 0.82
FALLBACK_LOW_OCR_CONFIDENCE_RATIO_THRESHOLD = 0.22
LOW_OCR_CONFIDENCE_THRESHOLD = 0.75
QUALITY_FALLBACK_MODES = {"none", "best-crop", "crop-only"}


def natural_sort_key(value: str | Path) -> list[int | str]:
    text = value.name if isinstance(value, Path) else str(value)
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]


class DebugTimer:
    def __init__(self, label: str) -> None:
        self.label = label
        self.started_at = perf_counter()
        self.last_mark_at = self.started_at
        print(f"[timing] {self.label}: start", flush=True)

    def mark(self, step: str) -> None:
        now = perf_counter()
        print(
            f"[timing] {self.label}: {step} "
            f"+{now - self.last_mark_at:.2f}s total={now - self.started_at:.2f}s",
            flush=True,
        )
        self.last_mark_at = now


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run word-level OCR and save JSON plus annotated page images."
    )
    parser.add_argument("file_path", help="PDF or image file to inspect.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where debug JSON and annotated images will be saved.",
    )
    parser.add_argument(
        "--ocr-lang",
        default=None,
        help="Tesseract language setting, for example `ita` or `ita+eng`.",
    )
    parser.add_argument(
        "--output-suffix",
        default=None,
        help="Suffix appended to the default viewer sample directory name.",
    )
    parser.add_argument(
        "--disable-quality-fallback",
        action="store_true",
        help="Disable best/crop OCR fallback after the first full-page OCR pass.",
    )
    parser.add_argument(
        "--quality-fallback-mode",
        choices=sorted(QUALITY_FALLBACK_MODES),
        default="crop-only",
        help="Fallback strategy after the first fast full-page OCR pass.",
    )
    args = parser.parse_args()

    saved_paths = debug_words_file(
        file_path=Path(args.file_path),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        ocr_lang=args.ocr_lang,
        output_suffix=args.output_suffix,
        quality_fallback_mode=(
            "none" if args.disable_quality_fallback else args.quality_fallback_mode
        ),
    )

    print("Saved word OCR debug output:")
    for path in saved_paths:
        print(f"- {path}")


def debug_words_file(
    file_path: Path,
    output_dir: Path | None = None,
    first_invoice_number: int = 1,
    ocr_lang: str | None = None,
    output_suffix: str | None = None,
    enable_quality_fallback: bool | None = None,
    quality_fallback_mode: str = "crop-only",
) -> list[Path]:
    if enable_quality_fallback is not None:
        quality_fallback_mode = "crop-only" if enable_quality_fallback else "none"
    _validate_quality_fallback_mode(quality_fallback_mode)

    timer = DebugTimer(file_path.name)
    source_file = file_path
    images = load_scan(source_file)
    if not images:
        raise RuntimeError(f"Could not load scan: {source_file}")
    timer.mark(f"load_scan pages={len(images)}")

    document = analyze_pages(
        images,
        source_file,
        backend=PerceptionBackend.TESSERACT,
        ocr_lang=ocr_lang,
        ocr_profile=DEFAULT_TESSERACT_PROFILE,
        enable_crop_ocr=False,
    )
    timer.mark("ocr_initial_full_page profile=fast crop=false")
    effective_ocr_lang = ocr_lang or DEFAULT_TESSERACT_LANG
    target_output_dir = output_dir or _debug_output_dir(
        source_file,
        suffix=output_suffix,
    )
    extraction = _extract_debug_candidates(document)
    timer.mark("extract_initial_candidates")
    quality_fallback_candidates = _quality_fallback_candidates(
        document=document,
        output_dir=target_output_dir,
        first_invoice_number=first_invoice_number,
        extraction=extraction,
    )
    fallback_events = []
    if quality_fallback_mode != "none":
        fallback_events = _apply_quality_ocr_fallbacks(
            document=document,
            images=images,
            output_dir=target_output_dir,
            first_invoice_number=first_invoice_number,
            ocr_lang=effective_ocr_lang,
            extraction=extraction,
            quality_fallback_mode=quality_fallback_mode,
        )
        fallback_extraction = (
            _extract_debug_candidates(document)
            if fallback_events
            else extraction
        )
        fallback_events.extend(
            _apply_empty_economic_summary_ocr_fallback(
                document=document,
                images=images,
                ocr_lang=effective_ocr_lang,
                extraction=fallback_extraction,
            )
        )
        timer.mark(
            f"quality_fallback mode={quality_fallback_mode} "
            f"events={len(fallback_events)}"
        )
    else:
        timer.mark("quality_fallback mode=none")
    if fallback_events:
        extraction = _extract_debug_candidates(document)
        timer.mark("extract_after_fallback_candidates")

    vat_candidates = extraction["vat_candidates"]
    fiscal_identifier_candidates = extraction["fiscal_identifier_candidates"]
    issuer_name_candidates = extraction["issuer_name_candidates"]
    condominium_candidates = extraction["condominium_candidates"]
    invoice_number_candidates = extraction["invoice_number_candidates"]
    invoice_date_candidates = extraction["invoice_date_candidates"]
    economic_summary_regions = extraction["economic_summary_regions"]
    economic_summary_field_candidates = extraction["economic_summary_field_candidates"]
    economic_summary_resolved_rows = extraction["economic_summary_resolved_rows"]
    saved_paths = save_document_debug_output(
        document=document,
        images=images,
        output_dir=target_output_dir,
        highlight_boxes_by_page=_selected_vat_boxes_by_page(vat_candidates),
        highlights_by_page=_recognized_target_highlights_by_page(
            fiscal_identifier_candidates,
            issuer_name_candidates,
            invoice_number_candidates,
            invoice_date_candidates,
            condominium_candidates,
            economic_summary_regions,
            economic_summary_field_candidates,
        ),
    )
    timer.mark("write_debug_document_and_images")
    saved_paths.append(
        save_vat_candidates_csv(document, target_output_dir / "vat_candidates.csv")
    )
    saved_paths.append(
        save_fiscal_identifier_candidates_csv(
            document,
            target_output_dir / "fiscal_identifier_candidates.csv",
        )
    )
    saved_paths.append(
        save_issuer_name_candidates_csv(
            document,
            target_output_dir / "issuer_name_candidates.csv",
        )
    )
    saved_paths.append(
        save_invoice_number_candidates_csv(
            document,
            target_output_dir / "invoice_number_candidates.csv",
        )
    )
    saved_paths.append(
        save_invoice_date_candidates_csv(
            document,
            target_output_dir / "invoice_date_candidates.csv",
        )
    )
    saved_paths.append(
        save_condominium_candidates_csv(
            document,
            target_output_dir / "condominium_candidates.csv",
        )
    )
    saved_paths.append(
        save_economic_summary_regions_csv(
            document,
            target_output_dir / "economic_summary_regions.csv",
        )
    )
    saved_paths.append(
        save_economic_summary_field_candidates_csv(
            document,
            target_output_dir / "economic_summary_field_candidates.csv",
        )
    )
    saved_paths.append(
        save_economic_summary_resolved_rows_csv(
            document,
            target_output_dir / "economic_summary_resolved_rows.csv",
        )
    )
    saved_paths.append(
        save_viewer_manifest(
            document=document,
            output_path=target_output_dir / "viewer_manifest.json",
            vat_candidates=vat_candidates,
            fiscal_identifier_candidates=fiscal_identifier_candidates,
            issuer_name_candidates=issuer_name_candidates,
            invoice_number_candidates=invoice_number_candidates,
            invoice_date_candidates=invoice_date_candidates,
            condominium_candidates=condominium_candidates,
            economic_summary_regions=economic_summary_regions,
            economic_summary_field_candidates=economic_summary_field_candidates,
            economic_summary_resolved_rows=economic_summary_resolved_rows,
            first_invoice_number=first_invoice_number,
            ocr_lang=effective_ocr_lang,
            ocr_profile=DEFAULT_TESSERACT_PROFILE,
            fallback_ocr_events=fallback_events,
            quality_fallback_candidates=list(quality_fallback_candidates.values()),
        )
    )
    viewer_index_path = _update_viewer_sample_index(target_output_dir)
    if viewer_index_path:
        saved_paths.append(viewer_index_path)
    _print_economic_summary_rows(economic_summary_resolved_rows)
    timer.mark("done")
    return saved_paths


def clean_viewer_sample_root(sample_root: Path = VIEWER_SAMPLE_ROOT) -> None:
    sample_root.mkdir(parents=True, exist_ok=True)
    resolved_sample_root = sample_root.resolve()

    for entry in sample_root.iterdir():
        resolved_entry = entry.resolve()
        if not resolved_entry.is_relative_to(resolved_sample_root):
            raise RuntimeError(f"Refusing to delete outside sample root: {entry}")

        if entry.is_dir():
            shutil.rmtree(entry, onerror=_make_writable_and_retry)
        elif entry.name in {"viewer_index.json", "viewer_batches.json"}:
            entry.unlink()


def clean_viewer_sample_batch(batch_id: str, sample_root: Path = VIEWER_SAMPLE_ROOT) -> None:
    sample_root.mkdir(parents=True, exist_ok=True)
    resolved_sample_root = sample_root.resolve()
    batch_dir = sample_root / batch_id
    resolved_batch_dir = batch_dir.resolve()
    if not resolved_batch_dir.is_relative_to(resolved_sample_root):
        raise RuntimeError(f"Refusing to delete outside sample root: {batch_dir}")

    if batch_dir.exists():
        shutil.rmtree(batch_dir, onerror=_make_writable_and_retry)


def _make_writable_and_retry(function, path, _exc_info) -> None:
    Path(path).chmod(stat.S_IWRITE)
    function(path)


def _validate_quality_fallback_mode(quality_fallback_mode: str) -> None:
    if quality_fallback_mode not in QUALITY_FALLBACK_MODES:
        allowed_modes = ", ".join(sorted(QUALITY_FALLBACK_MODES))
        raise RuntimeError(
            f"Unsupported quality fallback mode `{quality_fallback_mode}`. "
            f"Expected one of: {allowed_modes}."
        )


def _extract_debug_candidates(document: DocumentRepresentation) -> dict:
    economic_summary_field_candidates = find_economic_summary_field_candidates(
        document
    )
    return {
        "vat_candidates": find_vat_candidates(document),
        "fiscal_identifier_candidates": find_fiscal_identifier_candidates(document),
        "issuer_name_candidates": find_issuer_name_candidates(document),
        "condominium_candidates": find_condominium_candidates(document),
        "invoice_number_candidates": find_invoice_number_candidates(document),
        "invoice_date_candidates": find_invoice_date_candidates(document),
        "economic_summary_regions": find_economic_summary_regions(document),
        "economic_summary_field_candidates": economic_summary_field_candidates,
        "economic_summary_resolved_rows": resolve_economic_summary_rows(document),
    }


def _apply_quality_ocr_fallbacks(
    document: DocumentRepresentation,
    images,
    output_dir: Path,
    first_invoice_number: int,
    ocr_lang: str,
    extraction: dict,
    quality_fallback_mode: str,
) -> list[dict]:
    _validate_quality_fallback_mode(quality_fallback_mode)
    if ocr_lang != DEFAULT_TESSERACT_LANG:
        return []

    best_fallback_pages = _quality_fallback_candidates(
        document=document,
        output_dir=output_dir,
        first_invoice_number=first_invoice_number,
        extraction=extraction,
    )
    if not best_fallback_pages:
        return []

    if quality_fallback_mode == "crop-only":
        events = [
            {
                **decision,
                "stage": "fast_crop",
                "profile": "fast",
                "crop": True,
            }
            for decision in best_fallback_pages.values()
        ]
        _replace_pages_with_tesseract_profile(
            document=document,
            images=images,
            page_numbers=sorted(best_fallback_pages),
            ocr_lang=ocr_lang,
            ocr_profile=DEFAULT_TESSERACT_PROFILE,
            enable_crop_ocr=True,
        )
        return events

    events = [
        {
            **decision,
            "stage": "best_full_page",
            "profile": "best",
            "crop": False,
        }
        for decision in best_fallback_pages.values()
    ]
    _replace_pages_with_tesseract_profile(
        document=document,
        images=images,
        page_numbers=sorted(best_fallback_pages),
        ocr_lang=ocr_lang,
        ocr_profile="best",
        enable_crop_ocr=False,
    )

    best_extraction = _extract_debug_candidates(document)
    best_page_manifests = _build_viewer_page_manifests(
        document=document,
        output_dir=output_dir,
        **best_extraction,
    )
    best_invoices = build_viewer_invoices(
        best_page_manifests,
        first_invoice_number=first_invoice_number,
    )
    crop_fallback_pages = _fallback_pages_for_invoices(document, best_invoices)
    crop_fallback_pages = {
        page_number: decision
        for page_number, decision in crop_fallback_pages.items()
        if page_number in best_fallback_pages
    }
    if not crop_fallback_pages:
        return events

    events.extend(
        {
            **decision,
            "stage": "best_crop",
            "profile": "best",
            "crop": True,
        }
        for decision in crop_fallback_pages.values()
    )
    _replace_pages_with_tesseract_profile(
        document=document,
        images=images,
        page_numbers=sorted(crop_fallback_pages),
        ocr_lang=ocr_lang,
        ocr_profile="best",
        enable_crop_ocr=True,
    )
    return events


def _apply_empty_economic_summary_ocr_fallback(
    document: DocumentRepresentation,
    images,
    ocr_lang: str,
    extraction: dict,
) -> list[dict]:
    if ocr_lang != DEFAULT_TESSERACT_LANG:
        return []

    page_numbers = _pages_with_accounting_text_but_no_economic_fields(
        document=document,
        extraction=extraction,
    )
    if not page_numbers:
        return []

    _replace_pages_with_tesseract_profile(
        document=document,
        images=images,
        page_numbers=page_numbers,
        ocr_lang=ocr_lang,
        ocr_profile=DEFAULT_TESSERACT_PROFILE,
        enable_crop_ocr=True,
        force_crop_ocr=True,
    )
    return [
        {
            "stage": "fast_forced_crop",
            "profile": "fast",
            "crop": True,
            "page": page_number,
            "reason": "accounting_text_without_economic_fields",
        }
        for page_number in page_numbers
    ]


def _pages_with_accounting_text_but_no_economic_fields(
    document: DocumentRepresentation,
    extraction: dict,
) -> list[int]:
    pages_with_fields = {
        candidate.page
        for candidate in extraction["economic_summary_field_candidates"]
    }
    return [
        page.page_number
        for page in document.pages
        if page.page_number not in pages_with_fields
        and _page_has_accounting_summary_text(page)
    ]


def _page_has_accounting_summary_text(page) -> bool:
    normalized = " ".join(
        _normalize_accounting_text(span.text)
        for span in page.spans
    )
    accounting_phrases = [
        "netto a pagare",
        "ritenuta d acconto",
        "ritenuta previdenziale",
        "totale documento",
        "totale fattura",
        "totale imponibile",
        "totale imposta",
        "importo da pagare",
    ]
    return any(phrase in normalized for phrase in accounting_phrases)


def _normalize_accounting_text(text: str) -> str:
    return " ".join(
        "".join(char.lower() if char.isalnum() else " " for char in text).split()
    )


def _quality_fallback_candidates(
    document: DocumentRepresentation,
    output_dir: Path,
    first_invoice_number: int,
    extraction: dict,
) -> dict[int, dict]:
    initial_page_manifests = _build_viewer_page_manifests(
        document=document,
        output_dir=output_dir,
        **extraction,
    )
    initial_invoices = build_viewer_invoices(
        initial_page_manifests,
        first_invoice_number=first_invoice_number,
    )
    return _fallback_pages_for_invoices(document, initial_invoices)


def _fallback_pages_for_invoices(
    document: DocumentRepresentation,
    invoices: list[dict],
) -> dict[int, dict]:
    decisions_by_page: dict[int, dict] = {}
    for invoice in invoices:
        decision = _invoice_ocr_fallback_decision(document, invoice)
        if not decision["needs_fallback"]:
            continue

        for page_number in invoice.get("page_numbers", []):
            decisions_by_page[page_number] = {
                **decision,
                "invoice_id": invoice.get("invoice_id"),
                "invoice_title": invoice.get("title"),
                "page": page_number,
            }

    return decisions_by_page


def _invoice_ocr_fallback_decision(
    document: DocumentRepresentation,
    invoice: dict,
) -> dict:
    fields = invoice.get("fields", [])
    missing_target_count = sum(
        1
        for kind, _label in INVOICE_FIELD_DEFINITIONS
        if not _field_value(fields, kind)
    )
    avg_confidence, low_confidence_ratio = _invoice_ocr_quality(
        document,
        invoice.get("page_numbers", []),
    )
    has_low_ocr_quality = (
        avg_confidence < FALLBACK_AVG_OCR_CONFIDENCE_THRESHOLD
        or low_confidence_ratio > FALLBACK_LOW_OCR_CONFIDENCE_RATIO_THRESHOLD
    )
    needs_fallback = (
        missing_target_count >= FALLBACK_MISSING_TARGET_THRESHOLD
        and has_low_ocr_quality
    )

    return {
        "needs_fallback": needs_fallback,
        "missing_target_count": missing_target_count,
        "avg_ocr_confidence": round(avg_confidence, 4),
        "low_ocr_confidence_ratio": round(low_confidence_ratio, 4),
        "reason": (
            "missing_targets_and_low_ocr_confidence"
            if needs_fallback
            else "quality_gate_passed"
        ),
    }


def _field_value(fields: list[dict], kind: str) -> str:
    for field in fields:
        if field.get("kind") == kind:
            return str(field.get("value") or "").strip()

    return ""


def _invoice_ocr_quality(
    document: DocumentRepresentation,
    page_numbers: list[int],
) -> tuple[float, float]:
    page_number_set = set(page_numbers)
    confidences = [
        token.confidence
        for page in document.pages
        if page.page_number in page_number_set
        for token in page.tokens
        if token.confidence is not None
    ]
    if not confidences:
        return 0.0, 1.0

    avg_confidence = sum(confidences) / len(confidences)
    low_confidence_count = sum(
        1 for confidence in confidences if confidence < LOW_OCR_CONFIDENCE_THRESHOLD
    )
    return avg_confidence, low_confidence_count / len(confidences)


def _replace_pages_with_tesseract_profile(
    document: DocumentRepresentation,
    images,
    page_numbers: list[int],
    ocr_lang: str,
    ocr_profile: str,
    enable_crop_ocr: bool,
    force_crop_ocr: bool = False,
) -> None:
    if not page_numbers:
        return

    replacements = {}
    for page_number in page_numbers:
        replacement_document = analyze_pages_with_tesseract(
            [images[page_number - 1]],
            document.source_file,
            lang=ocr_lang,
            ocr_profile=ocr_profile,
            enable_crop_ocr=enable_crop_ocr,
            force_crop_ocr=force_crop_ocr,
            first_page_number=page_number,
        )
        replacements[page_number] = replacement_document.pages[0]

    document.pages = [
        replacements.get(page.page_number, page)
        for page in document.pages
    ]


def _build_viewer_page_manifests(
    document: DocumentRepresentation,
    output_dir: Path,
    vat_candidates: list[VatCandidate],
    fiscal_identifier_candidates: list[FiscalIdentifierCandidate],
    issuer_name_candidates: list[IssuerNameCandidate],
    invoice_number_candidates: list[InvoiceNumberCandidate],
    invoice_date_candidates: list[InvoiceDateCandidate],
    condominium_candidates: list[CondominiumCandidate],
    economic_summary_regions: list[EconomicSummaryRegionCandidate],
    economic_summary_field_candidates: list[EconomicSummaryFieldCandidate],
    economic_summary_resolved_rows: list[EconomicSummaryResolvedRow],
) -> list[dict]:
    vat_by_page = _index_by_page(vat_candidates)
    fiscal_by_page = _index_by_page(fiscal_identifier_candidates)
    issuer_name_by_page = _index_by_page(issuer_name_candidates)
    invoice_number_by_page = _index_by_page(invoice_number_candidates)
    invoice_date_by_page = _index_by_page(invoice_date_candidates)
    condominium_by_page = _index_by_page(condominium_candidates)
    economic_region_by_page = _index_by_page(economic_summary_regions)
    economic_field_by_page = _index_by_page(economic_summary_field_candidates)
    economic_row_by_page = _index_by_page(economic_summary_resolved_rows)

    return [
        _viewer_page_manifest(
            page=page,
            output_dir=output_dir,
            vat_candidates=vat_by_page.get(page.page_number, []),
            fiscal_identifier_candidates=fiscal_by_page.get(page.page_number, []),
            issuer_name_candidates=issuer_name_by_page.get(page.page_number, []),
            invoice_number_candidates=invoice_number_by_page.get(page.page_number, []),
            invoice_date_candidates=invoice_date_by_page.get(page.page_number, []),
            condominium_candidates=condominium_by_page.get(page.page_number, []),
            economic_summary_regions=economic_region_by_page.get(page.page_number, []),
            economic_summary_field_candidates=economic_field_by_page.get(
                page.page_number,
                [],
            ),
            economic_summary_resolved_rows=economic_row_by_page.get(
                page.page_number,
                [],
            ),
        )
        for page in document.pages
    ]


def save_viewer_manifest(
    document: DocumentRepresentation,
    output_path: Path,
    vat_candidates: list[VatCandidate],
    fiscal_identifier_candidates: list[FiscalIdentifierCandidate],
    issuer_name_candidates: list[IssuerNameCandidate],
    invoice_number_candidates: list[InvoiceNumberCandidate],
    invoice_date_candidates: list[InvoiceDateCandidate],
    condominium_candidates: list[CondominiumCandidate],
    economic_summary_regions: list[EconomicSummaryRegionCandidate],
    economic_summary_field_candidates: list[EconomicSummaryFieldCandidate],
    economic_summary_resolved_rows: list[EconomicSummaryResolvedRow],
    first_invoice_number: int = 1,
    ocr_lang: str | None = None,
    ocr_profile: str = DEFAULT_TESSERACT_PROFILE,
    fallback_ocr_events: list[dict] | None = None,
    quality_fallback_candidates: list[dict] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_manifests = _build_viewer_page_manifests(
        document=document,
        output_dir=output_path.parent,
        vat_candidates=vat_candidates,
        fiscal_identifier_candidates=fiscal_identifier_candidates,
        issuer_name_candidates=issuer_name_candidates,
        invoice_number_candidates=invoice_number_candidates,
        invoice_date_candidates=invoice_date_candidates,
        condominium_candidates=condominium_candidates,
        economic_summary_regions=economic_summary_regions,
        economic_summary_field_candidates=economic_summary_field_candidates,
        economic_summary_resolved_rows=economic_summary_resolved_rows,
    )

    manifest = {
        "schema_version": VIEWER_MANIFEST_SCHEMA_VERSION,
        "source_file": document.source_file.as_posix(),
        "ocr_lang": ocr_lang,
        "ocr_profile": ocr_profile,
        "fallback_ocr_events": fallback_ocr_events or [],
        "quality_fallback_candidates": quality_fallback_candidates or [],
        "pages": page_manifests,
        "invoices": build_viewer_invoices(
            page_manifests,
            first_invoice_number=first_invoice_number,
            registry_cache=_load_optional_registry_cache(),
        ),
    }
    output_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def _load_optional_registry_cache():
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        return None

    try:
        return load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    except Exception as error:
        print(f"[registry] cache anagrafica ignorata: {error}")
        return None


def _viewer_page_manifest(
    page,
    output_dir: Path,
    vat_candidates: list[VatCandidate],
    fiscal_identifier_candidates: list[FiscalIdentifierCandidate],
    issuer_name_candidates: list[IssuerNameCandidate],
    invoice_number_candidates: list[InvoiceNumberCandidate],
    invoice_date_candidates: list[InvoiceDateCandidate],
    condominium_candidates: list[CondominiumCandidate],
    economic_summary_regions: list[EconomicSummaryRegionCandidate],
    economic_summary_field_candidates: list[EconomicSummaryFieldCandidate],
    economic_summary_resolved_rows: list[EconomicSummaryResolvedRow],
) -> dict:
    selected_vat = select_best_vat_candidate_per_page(vat_candidates).get(
        page.page_number
    )

    return {
        "page": page.page_number,
        "width": page.width,
        "height": page.height,
        "image": (output_dir / f"page-{page.page_number:03d}-boxes.png").name,
        "tokens": [
            {
                "token_id": f"p{page.page_number}-t{token_index}",
                "text": token.text,
                "confidence": token.confidence,
                "bbox": _bbox_dict(token.bbox),
                **({"source": token.source} if token.source else {}),
                **({"source_id": token.source_id} if token.source_id else {}),
            }
            for token_index, token in enumerate(page.tokens, start=1)
        ],
        "spans": [
            {
                "span_id": span.span_id,
                "line_id": span.line_id,
                "text": span.text,
                "bbox": _bbox_dict(span.bbox),
            }
            for span in page.spans
        ],
        "ocr_hole_regions": [
            {
                "bbox": _bbox_dict(region.bbox),
                "reason": region.reason,
                "ink_density": region.ink_density,
                "token_density": region.token_density,
                "token_count": region.token_count,
            }
            for region in page.ocr_hole_regions
        ],

        "detected_table_regions": [
            {
                "bbox": _bbox_dict(region.bbox),
                "horizontal_line_count": region.horizontal_line_count,
                "vertical_line_count": region.vertical_line_count,
            }
            for region in page.detected_table_regions
        ],

        "detected_table_cell_regions": [
            {
                "bbox": _bbox_dict(region.bbox),
                "polygon": [
                    {"x": point.x, "y": point.y} for point in region.polygon
                ],
                "table_index": region.table_index,
                "row_index": region.row_index,
                "column_index": region.column_index,
            }
            for region in page.detected_table_cell_regions
        ],

        "targets": _viewer_targets(
            page=page,
            selected_vat=selected_vat,
            fiscal_candidates=fiscal_identifier_candidates,
            issuer_name_candidates=issuer_name_candidates,
            invoice_number_candidates=invoice_number_candidates,
            invoice_date_candidates=invoice_date_candidates,
            condominium_candidates=condominium_candidates,
            economic_summary_regions=economic_summary_regions,
            economic_summary_field_candidates=economic_summary_field_candidates,
            economic_summary_resolved_rows=economic_summary_resolved_rows,
        ),
        "candidate_tables": {
            "vat": [
                _viewer_vat_candidate(candidate, selected_vat)
                for candidate in vat_candidates
            ],
            "fiscal_identifiers": [
                _viewer_fiscal_candidate(candidate)
                for candidate in fiscal_identifier_candidates
            ],
            "issuer_names": [
                _viewer_issuer_name_candidate(candidate)
                for candidate in issuer_name_candidates
            ],
            "invoice_numbers": [
                _viewer_invoice_number_candidate(candidate)
                for candidate in invoice_number_candidates
            ],
            "invoice_dates": [
                _viewer_invoice_date_candidate(page, candidate)
                for candidate in invoice_date_candidates
            ],
            "condominiums": [
                _viewer_condominium_candidate(candidate)
                for candidate in condominium_candidates
            ],
            "economic_summary_regions": [
                _viewer_economic_region(candidate)
                for candidate in economic_summary_regions
            ],
            "economic_summary_fields": [
                _viewer_economic_field(candidate)
                for candidate in economic_summary_field_candidates
            ],
            "economic_summary_resolved_rows": [
                _viewer_economic_resolved_row(row)
                for row in economic_summary_resolved_rows
            ],
        },
    }


def _index_by_page(items) -> dict[int, list]:
    items_by_page: dict[int, list] = {}
    for item in items:
        items_by_page.setdefault(item.page, []).append(item)

    return items_by_page


def _viewer_targets(
    page,
    selected_vat: VatCandidate | None,
    fiscal_candidates: list[FiscalIdentifierCandidate],
    issuer_name_candidates: list[IssuerNameCandidate],
    invoice_number_candidates: list[InvoiceNumberCandidate],
    invoice_date_candidates: list[InvoiceDateCandidate],
    condominium_candidates: list[CondominiumCandidate],
    economic_summary_regions: list[EconomicSummaryRegionCandidate],
    economic_summary_field_candidates: list[EconomicSummaryFieldCandidate],
    economic_summary_resolved_rows: list[EconomicSummaryResolvedRow],
) -> list[dict]:
    targets = []

    _append_selected_or_best_target(
        targets,
        page,
        issuer_name_candidates,
        selected_attr="selected_as_nome_emittente",
        kind="nome_emittente",
        value_attr="normalized_value",
        score_attr="score",
    )

    for candidate in fiscal_candidates:
        _append_selected_target(
            targets,
            page,
            candidate,
            selected_attr="selected_as_p_iva",
            kind="p_iva",
            value_attr="normalized_value",
            score_attr="p_iva_score",
        )
        _append_selected_target(
            targets,
            page,
            candidate,
            selected_attr="selected_as_codice_fiscale",
            kind="codice_fiscale",
            value_attr="normalized_value",
            score_attr="codice_fiscale_score",
        )
        _append_selected_target(
            targets,
            page,
            candidate,
            selected_attr="selected_as_codice_fiscale_emittente",
            kind="codice_fiscale_emittente",
            value_attr="normalized_value",
            score_attr="codice_fiscale_emittente_score",
        )

    for candidate in invoice_number_candidates:
        _append_selected_target(
            targets,
            page,
            candidate,
            selected_attr="selected_as_numero_fattura",
            kind="numero_fattura",
            value_attr="normalized_value",
            score_attr="score",
        )

    for candidate in invoice_date_candidates:
        _append_selected_target(
            targets,
            page,
            candidate,
            selected_attr="selected_as_data_fattura",
            kind="data_fattura",
            value_attr="normalized_value",
            score_attr="score",
        )

    for candidate in condominium_candidates:
        _append_selected_target(
            targets,
            page,
            candidate,
            selected_attr="selected_as_condominio",
            kind="condominio",
            value_attr="raw_text",
            score_attr="score",
        )

    for candidate in economic_summary_regions:
        if candidate.selected_as_economic_summary:
            targets.append(
                _target_manifest(
                    page=page,
                    kind="economic_summary",
                    label="economic_summary",
                    bbox=candidate.bbox,
                    value=candidate.anchor_context,
                    score=candidate.score,
                    confidence_sensitive=False,
                )
            )

    for candidate in economic_summary_field_candidates:
        if not candidate.selected_as_field:
            continue

        rejected_by_resolver = _economic_field_rejected_by_resolver(
            candidate,
            economic_summary_resolved_rows,
        )

        targets.append(
            _target_manifest(
                page=page,
                kind=f"economic_summary.{candidate.target}",
                label=candidate.target,
                bbox=candidate.bbox,
                value=candidate.normalized_value,
                score=candidate.score,
                confidence_sensitive=False,
                role="pair",
            )
        )
        targets.append(
            _target_manifest(
                page=page,
                kind=f"economic_summary.{candidate.target}.label",
                label="",
                bbox=_bbox_with_height(candidate.label_bbox, candidate.bbox),
                value=candidate.label_text,
                score=candidate.score,
                role="label",
            )
        )
        if not rejected_by_resolver:
            targets.append(
                _target_manifest(
                    page=page,
                    kind=f"economic_summary.{candidate.target}.value",
                    label="",
                    bbox=candidate.value_bbox,
                    value=candidate.value_text,
                    score=candidate.score,
                    role="value",
                )
            )

    for row in economic_summary_resolved_rows:
        _append_economic_resolved_target(
            targets,
            page=page,
            row=row,
            kind="economic_summary.totale_fattura.value",
            value=row.totale_fattura_value,
            source=row.totale_fattura_source,
        )
        _append_economic_resolved_target(
            targets,
            page=page,
            row=row,
            kind="economic_summary.totale_netto_fattura.value",
            value=row.totale_netto_fattura_value,
            source=row.totale_netto_fattura_source,
        )

    return targets


def _economic_field_rejected_by_resolver(
    candidate: EconomicSummaryFieldCandidate,
    rows: list[EconomicSummaryResolvedRow],
) -> bool:
    source_attribute = {
        "totale_fattura": "totale_fattura_source",
        "totale_netto_fattura": "totale_netto_fattura_source",
    }.get(candidate.target)
    if source_attribute is None:
        return False

    return any(
        row.page == candidate.page
        and str(getattr(row, source_attribute, "")).startswith("rejected_")
        for row in rows
    )


def _append_economic_resolved_target(
    targets: list[dict],
    page,
    row: EconomicSummaryResolvedRow,
    kind: str,
    value: str,
    source: str,
) -> None:
    if not value:
        return
    if source == "direct":
        return

    targets.append(
        _target_manifest(
            page=page,
            kind=kind,
            label=kind,
            bbox=_resolved_value_bbox(page, row, value) or row.bbox,
            value=value,
            score=220 if row.math_check == "ok" else 120,
            confidence_sensitive=False,
            role="calculated" if source.startswith("calculated_") else "resolved",
        )
    )


def _resolved_value_bbox(
    page,
    row: EconomicSummaryResolvedRow,
    value: str,
) -> BoundingBox | None:
    expected_value = _manifest_money_decimal(value)
    if expected_value is None:
        return None

    matching_tokens = [
        token
        for token in page.tokens
        if _manifest_money_decimal(token.text) == expected_value
    ]
    if not matching_tokens:
        return None

    return max(
        matching_tokens,
        key=lambda token: (
            _bbox_center_inside(row.bbox, token.bbox),
            _economic_value_token_anchor_score(page, token),
            -_bbox_distance(row.bbox, token.bbox),
            token.confidence or 0,
        ),
    ).bbox


def _manifest_money_decimal(value: str) -> Decimal | None:
    match = MONEY_TOKEN_PATTERN.search(value.strip())
    if not match:
        return None

    text = match.group(0)
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return abs(Decimal(text).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _economic_value_token_anchor_score(page, token) -> int:
    token_center_y = (token.bbox.y1 + token.bbox.y2) / 2
    score = 0
    for span in page.spans:
        span_center_y = (span.bbox.y1 + span.bbox.y2) / 2
        if abs(span_center_y - token_center_y) > max(28, page.height * 0.018):
            continue

        normalized = span.text.lower()
        if any(
            anchor in normalized
            for anchor in [
                "importo scadenza",
                "netto",
                "pagare",
                "dovuto",
                "totale documento",
                "ritenuta",
            ]
        ):
            score += 2
        if span.bbox.x2 <= token.bbox.x1 + page.width * 0.04:
            score += 1

    return score


def _bbox_center_inside(outer_bbox: BoundingBox, inner_bbox: BoundingBox) -> bool:
    center_x = (inner_bbox.x1 + inner_bbox.x2) / 2
    center_y = (inner_bbox.y1 + inner_bbox.y2) / 2
    return outer_bbox.x1 <= center_x <= outer_bbox.x2 and outer_bbox.y1 <= center_y <= outer_bbox.y2


def _bbox_distance(left: BoundingBox, right: BoundingBox) -> float:
    left_center_x = (left.x1 + left.x2) / 2
    left_center_y = (left.y1 + left.y2) / 2
    right_center_x = (right.x1 + right.x2) / 2
    right_center_y = (right.y1 + right.y2) / 2
    return ((left_center_x - right_center_x) ** 2 + (left_center_y - right_center_y) ** 2) ** 0.5


def _append_selected_target(
    targets: list[dict],
    page,
    candidate,
    selected_attr: str,
    kind: str,
    value_attr: str,
    score_attr: str,
) -> None:
    if not getattr(candidate, selected_attr):
        return

    targets.append(
        _target_manifest(
            page=page,
            kind=kind,
            label=kind,
            bbox=candidate.bbox,
            value=getattr(candidate, value_attr),
            score=getattr(candidate, score_attr),
        )
    )


def _append_selected_or_best_target(
    targets: list[dict],
    page,
    candidates,
    selected_attr: str,
    kind: str,
    value_attr: str,
    score_attr: str,
) -> None:
    if not candidates:
        return

    selected = [candidate for candidate in candidates if getattr(candidate, selected_attr)]
    target_candidate = max(
        selected or candidates,
        key=lambda candidate: getattr(candidate, score_attr),
    )
    targets.append(
        _target_manifest(
            page=page,
            kind=kind,
            label=kind,
            bbox=target_candidate.bbox,
            value=getattr(target_candidate, value_attr),
            score=getattr(target_candidate, score_attr),
        )
    )


def _target_manifest(
    page,
    kind: str,
    label: str,
    bbox: BoundingBox,
    value: str,
    score: float,
    confidence_sensitive: bool = True,
    role: str = "target",
) -> dict:
    return {
        "kind": kind,
        "label": label,
        "role": role,
        "page": page.page_number,
        "value": value,
        "score": score,
        "ocr_confidence": _bbox_ocr_confidence(page, bbox),
        "bbox": _bbox_dict(bbox),
        "confidence_sensitive": confidence_sensitive,
        "has_low_confidence_token": (
            confidence_sensitive and _bbox_has_low_confidence_token(page, bbox)
        ),
    }


def _viewer_vat_candidate(
    candidate: VatCandidate,
    selected_vat: VatCandidate | None,
) -> dict:
    return {
        "raw_text": candidate.raw_text,
        "normalized_value": candidate.normalized_value,
        "score": candidate.score,
        "selected": candidate == selected_vat,
        "positive_context": candidate.positive_context,
        "negative_context": candidate.negative_context,
        "nearby_text": candidate.nearby_text,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_fiscal_candidate(candidate: FiscalIdentifierCandidate) -> dict:
    return {
        "raw_text": candidate.raw_text,
        "normalized_value": candidate.normalized_value,
        "is_valid_vat_number": candidate.is_valid_vat_number,
        "is_valid_tax_code": candidate.is_valid_tax_code,
        "p_iva_score": candidate.p_iva_score,
        "codice_fiscale_score": candidate.codice_fiscale_score,
        "codice_fiscale_emittente_score": candidate.codice_fiscale_emittente_score,
        "campo_suggerito": candidate.campo_suggerito,
        "selected_as_p_iva": candidate.selected_as_p_iva,
        "selected_as_codice_fiscale": candidate.selected_as_codice_fiscale,
        "selected_as_codice_fiscale_emittente": (
            candidate.selected_as_codice_fiscale_emittente
        ),
        "p_iva_context": candidate.p_iva_context,
        "codice_fiscale_context": candidate.codice_fiscale_context,
        "codice_fiscale_emittente_context": (
            candidate.codice_fiscale_emittente_context
        ),
        "negative_context": candidate.negative_context,
        "nearby_text": candidate.nearby_text,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_issuer_name_candidate(candidate: IssuerNameCandidate) -> dict:
    return {
        "raw_text": candidate.raw_text,
        "normalized_value": candidate.normalized_value,
        "accuracy": candidate.accuracy,
        "occurrence_count": candidate.occurrence_count,
        "score": candidate.score,
        "selected_as_nome_emittente": candidate.selected_as_nome_emittente,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_invoice_number_candidate(candidate: InvoiceNumberCandidate) -> dict:
    return {
        "raw_text": candidate.raw_text,
        "normalized_value": candidate.normalized_value,
        "format_kind": candidate.format_kind,
        "score": candidate.score,
        "selected_as_numero_fattura": candidate.selected_as_numero_fattura,
        "anchor_context": candidate.anchor_context,
        "anchor_quality": candidate.anchor_quality,
        "date_context": candidate.date_context,
        "excluded_reason": candidate.excluded_reason,
        "nearby_text": candidate.nearby_text,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_invoice_date_candidate(page, candidate: InvoiceDateCandidate) -> dict:
    return {
        "raw_text": candidate.raw_text,
        "normalized_value": candidate.normalized_value,
        "score": candidate.score,
        "ocr_confidence": _bbox_ocr_confidence(page, candidate.bbox),
        "selected_as_data_fattura": candidate.selected_as_data_fattura,
        "anchor_context": candidate.anchor_context,
        "negative_context": candidate.negative_context,
        "nearby_text": candidate.nearby_text,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_condominium_candidate(candidate: CondominiumCandidate) -> dict:
    return {
        "raw_text": candidate.raw_text,
        "score": candidate.score,
        "selected_as_condominio": candidate.selected_as_condominio,
        "condominio_context": candidate.condominio_context,
        "anchor_context": candidate.anchor_context,
        "nearby_text": candidate.nearby_text,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_economic_region(candidate: EconomicSummaryRegionCandidate) -> dict:
    return {
        "score": candidate.score,
        "selected_as_economic_summary": candidate.selected_as_economic_summary,
        "anchor_context": candidate.anchor_context,
        "amount_count": candidate.amount_count,
        "amount_context": candidate.amount_context,
        "bbox": _bbox_dict(candidate.bbox),
    }


def _viewer_economic_field(candidate: EconomicSummaryFieldCandidate) -> dict:
    return {
        "target": candidate.target,
        "label_text": candidate.label_text,
        "value_text": candidate.value_text,
        "normalized_value": candidate.normalized_value,
        "score": candidate.score,
        "selected_as_field": candidate.selected_as_field,
        "bbox": _bbox_dict(candidate.bbox),
        "label_bbox": _bbox_dict(candidate.label_bbox),
        "value_bbox": _bbox_dict(candidate.value_bbox),
    }


def _viewer_economic_resolved_row(row: EconomicSummaryResolvedRow) -> dict:
    return {
        "bbox": _bbox_dict(row.bbox),
        "totale_imponibile_text": row.totale_imponibile_text,
        "totale_imponibile_value": row.totale_imponibile_value,
        "totale_fattura_text": row.totale_fattura_text,
        "totale_fattura_value": row.totale_fattura_value,
        "totale_fattura_source": row.totale_fattura_source,
        "ritenuta_label_text": row.ritenuta_label_text,
        "ritenuta_text": row.ritenuta_text,
        "ritenuta_value": row.ritenuta_value,
        "ritenuta_percentuale": row.ritenuta_percentuale,
        "ritenuta_source": row.ritenuta_source,
        "totale_netto_fattura_text": row.totale_netto_fattura_text,
        "totale_netto_fattura_value": row.totale_netto_fattura_value,
        "totale_netto_fattura_source": row.totale_netto_fattura_source,
        "math_check": row.math_check,
        "notes": row.notes,
    }


def _bbox_dict(bbox: BoundingBox) -> dict:
    return {
        "x1": bbox.x1,
        "y1": bbox.y1,
        "x2": bbox.x2,
        "y2": bbox.y2,
    }


def _bbox_has_low_confidence_token(page, bbox: BoundingBox) -> bool:
    return any(
        token.confidence is not None
        and token.confidence < 0.9
        and _bbox_contains_center(bbox, token.bbox)
        for token in page.tokens
    )


def _bbox_ocr_confidence(page, bbox: BoundingBox) -> float | None:
    confidences = [
        token.confidence
        for token in page.tokens
        if token.confidence is not None and _bbox_contains_center(bbox, token.bbox)
    ]
    if not confidences:
        return None

    return round(min(confidences), 4)


def _bbox_contains_center(bbox: BoundingBox, token_bbox: BoundingBox) -> bool:
    center_x = (token_bbox.x1 + token_bbox.x2) / 2
    center_y = (token_bbox.y1 + token_bbox.y2) / 2
    return bbox.x1 <= center_x <= bbox.x2 and bbox.y1 <= center_y <= bbox.y2


def _selected_vat_boxes_by_page(
    candidates: list[VatCandidate],
) -> dict[int, list[BoundingBox]]:
    selected_by_page = select_best_vat_candidate_per_page(candidates)
    return {
        page: [candidate.bbox]
        for page, candidate in selected_by_page.items()
        if candidate is not None
    }


def _fiscal_identifier_highlights_by_page(
    candidates: list[FiscalIdentifierCandidate],
) -> dict[int, list[DebugHighlight]]:
    highlights_by_page: dict[int, list[DebugHighlight]] = {}
    _append_selected_highlights(
        highlights_by_page,
        candidates,
        selected_attr="selected_as_p_iva",
        label="p_iva",
    )
    _append_selected_highlights(
        highlights_by_page,
        candidates,
        selected_attr="selected_as_codice_fiscale",
        label="codice_fiscale",
    )
    _append_selected_highlights(
        highlights_by_page,
        candidates,
        selected_attr="selected_as_codice_fiscale_emittente",
        label="codice_fiscale_emittente",
    )

    return highlights_by_page


def _issuer_name_highlights_by_page(
    candidates: list[IssuerNameCandidate],
) -> dict[int, list[DebugHighlight]]:
    return _selected_highlights_by_page(
        candidates,
        selected_attr="selected_as_nome_emittente",
        label="nome_emittente",
    )


def _invoice_number_highlights_by_page(
    candidates: list[InvoiceNumberCandidate],
) -> dict[int, list[DebugHighlight]]:
    return _selected_highlights_by_page(
        candidates,
        selected_attr="selected_as_numero_fattura",
        label="numero_fattura",
    )


def _invoice_date_highlights_by_page(
    candidates: list[InvoiceDateCandidate],
) -> dict[int, list[DebugHighlight]]:
    return _selected_highlights_by_page(
        candidates,
        selected_attr="selected_as_data_fattura",
        label="data_fattura",
    )


def _condominium_highlights_by_page(
    candidates: list[CondominiumCandidate],
) -> dict[int, list[DebugHighlight]]:
    return _selected_highlights_by_page(
        candidates,
        selected_attr="selected_as_condominio",
        label="condominio",
    )


def _economic_summary_highlights_by_page(
    candidates: list[EconomicSummaryRegionCandidate],
) -> dict[int, list[DebugHighlight]]:
    return _selected_highlights_by_page(
        candidates,
        selected_attr="selected_as_economic_summary",
        label="economic_summary",
        confidence_sensitive=False,
    )


def _selected_highlights_by_page(
    candidates,
    selected_attr: str,
    label: str,
    confidence_sensitive: bool = True,
) -> dict[int, list[DebugHighlight]]:
    highlights_by_page: dict[int, list[DebugHighlight]] = {}
    _append_selected_highlights(
        highlights_by_page,
        candidates,
        selected_attr=selected_attr,
        label=label,
        confidence_sensitive=confidence_sensitive,
    )
    return highlights_by_page


def _append_selected_highlights(
    highlights_by_page: dict[int, list[DebugHighlight]],
    candidates,
    selected_attr: str,
    label: str,
    confidence_sensitive: bool = True,
) -> None:
    for candidate in candidates:
        if getattr(candidate, selected_attr):
            highlights_by_page.setdefault(candidate.page, []).append(
                DebugHighlight(
                    bbox=candidate.bbox,
                    label=label,
                    confidence_sensitive=confidence_sensitive,
                )
            )


def _economic_summary_field_highlights_by_page(
    candidates: list[EconomicSummaryFieldCandidate],
) -> dict[int, list[DebugHighlight]]:
    highlights_by_page: dict[int, list[DebugHighlight]] = {}

    for candidate in candidates:
        if candidate.selected_as_field:
            highlights_by_page.setdefault(candidate.page, []).append(
                DebugHighlight(
                    bbox=candidate.bbox,
                    label=candidate.target,
                    confidence_sensitive=False,
                )
            )
            highlights_by_page.setdefault(candidate.page, []).append(
                DebugHighlight(
                    bbox=_bbox_with_height(candidate.label_bbox, candidate.bbox),
                    label="",
                )
            )
            highlights_by_page.setdefault(candidate.page, []).append(
                DebugHighlight(
                    bbox=candidate.value_bbox,
                    label="",
                )
            )

    return highlights_by_page


def _bbox_with_height(bbox: BoundingBox, height_source: BoundingBox) -> BoundingBox:
    return BoundingBox(
        x1=bbox.x1,
        y1=height_source.y1,
        x2=bbox.x2,
        y2=height_source.y2,
    )


def _recognized_target_highlights_by_page(
    fiscal_candidates: list[FiscalIdentifierCandidate],
    issuer_name_candidates: list[IssuerNameCandidate],
    invoice_number_candidates: list[InvoiceNumberCandidate],
    invoice_date_candidates: list[InvoiceDateCandidate],
    condominium_candidates: list[CondominiumCandidate],
    economic_summary_regions: list[EconomicSummaryRegionCandidate],
    economic_summary_field_candidates: list[EconomicSummaryFieldCandidate],
) -> dict[int, list[DebugHighlight]]:
    highlights_by_page = _fiscal_identifier_highlights_by_page(fiscal_candidates)

    for extra_highlights in [
        _issuer_name_highlights_by_page(issuer_name_candidates),
        _invoice_number_highlights_by_page(invoice_number_candidates),
        _invoice_date_highlights_by_page(invoice_date_candidates),
        _condominium_highlights_by_page(condominium_candidates),
        _economic_summary_highlights_by_page(economic_summary_regions),
        _economic_summary_field_highlights_by_page(
            economic_summary_field_candidates
        ),
    ]:
        for page, highlights in extra_highlights.items():
            highlights_by_page.setdefault(page, []).extend(highlights)

    return highlights_by_page


def _print_economic_summary_rows(rows: list[EconomicSummaryResolvedRow]) -> None:
    print("Economic summary resolved rows:", flush=True)
    if not rows:
        print("  none", flush=True)
        return

    print(
        "page | totale_fattura | ritenuta | ritenuta_% | "
        "totale_netto | check | notes",
        flush=True,
    )
    for row in rows:
        print(
            f"{row.page} | "
            f"{row.totale_fattura_text}->{row.totale_fattura_value} | "
            f"{row.ritenuta_text}->{row.ritenuta_value} | "
            f"{row.ritenuta_percentuale} | "
            f"{row.totale_netto_fattura_text}->{row.totale_netto_fattura_value} | "
            f"{row.math_check} | "
            f"{row.notes}",
            flush=True,
        )


def _debug_output_dir(source_file: Path, suffix: str | None = None) -> Path:
    directory_name = source_file.stem
    if suffix:
        directory_name = f"{directory_name}{suffix}"

    return VIEWER_SAMPLE_ROOT / directory_name


def _update_viewer_sample_index(output_dir: Path) -> Path | None:
    sample_root = VIEWER_SAMPLE_ROOT.resolve()
    resolved_output_dir = output_dir.resolve()

    try:
        relative_output_dir = resolved_output_dir.relative_to(sample_root)
    except ValueError:
        return None

    if not relative_output_dir.parts:
        return None

    if len(relative_output_dir.parts) >= 2:
        batch_id = relative_output_dir.parts[0]
        document_id = Path(*relative_output_dir.parts[1:]).as_posix()
        index_path = sample_root / batch_id / "viewer_index.json"
        base_path = f"/sample/{batch_id}/{document_id}"
        _update_viewer_batch_index(batch_id=batch_id, sample_root=sample_root)
    else:
        document_id = relative_output_dir.as_posix()
        index_path = sample_root / "viewer_index.json"
        base_path = f"/sample/{document_id}"

    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"documents": []}

    documents = [
        document
        for document in index.get("documents", [])
        if document.get("id") != document_id
        and document.get("base_path") != base_path
        and _viewer_manifest_path_exists(
            sample_root=sample_root,
            index_path=index_path,
            document=document,
        )
    ]
    documents.append({"id": document_id, "base_path": base_path})
    documents.sort(key=lambda document: natural_sort_key(str(document["id"])))
    index["documents"] = documents

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return index_path


def _update_viewer_batch_index(batch_id: str, sample_root: Path) -> None:
    index_path = sample_root / "viewer_batches.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"batches": []}

    base_path = f"/sample/{batch_id}"
    batches = [
        batch
        for batch in index.get("batches", [])
        if batch.get("id") != batch_id and batch.get("base_path") != base_path
    ]
    batches.append(
        {
            "id": batch_id,
            "label": batch_id.replace("_", " "),
            "base_path": base_path,
        }
    )
    batches.sort(key=lambda batch: batch["id"])
    index["batches"] = batches
    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _viewer_manifest_path_exists(
    sample_root: Path,
    index_path: Path,
    document: dict,
) -> bool:
    base_path = str(document.get("base_path", ""))
    if base_path.startswith("/sample/"):
        return (sample_root / base_path.removeprefix("/sample/") / "viewer_manifest.json").exists()

    return (index_path.parent / str(document.get("id", "")) / "viewer_manifest.json").exists()


def is_supported_input_file(file_path: Path) -> bool:
    return file_path.is_file() and file_path.suffix.lower() in SUPPORTED_SUFFIXES


if __name__ == "__main__":
    main()
