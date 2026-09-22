from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from estrattore.viewer_invoice_segmentation import (
    build_viewer_invoice_fields_for_pages,
    link_shared_payment_dates,
)


class SegmentationEditError(ValueError):
    pass


@dataclass(frozen=True)
class SegmentationEditResult:
    manifest: dict
    selected_invoice_id: str
    removed_invoice_id: str = ""
    created_invoice_id: str = ""
    recipient_invoice_id: str = ""
    numbering_threshold: int = 0
    numbering_delta: int = 0


def move_boundary_page(
    manifest: dict,
    invoice_id: str,
    direction: str,
) -> SegmentationEditResult:
    next_manifest = deepcopy(manifest)
    invoices = _manifest_invoices(next_manifest)
    source_index = _invoice_index(invoices, invoice_id)
    if direction == "first_to_previous":
        target_index = source_index - 1
        boundary_index = 0
    elif direction == "last_to_next":
        target_index = source_index + 1
        boundary_index = -1
    else:
        raise SegmentationEditError("Direzione di spostamento non valida")

    if target_index < 0 or target_index >= len(invoices):
        raise SegmentationEditError("Non esiste una fattura adiacente")

    source_invoice = invoices[source_index]
    target_invoice = invoices[target_index]
    source_pages = _invoice_pages(source_invoice)
    target_pages = _invoice_pages(target_invoice)

    moved_page = source_pages[boundary_index]
    remaining_pages = [page for page in source_pages if page != moved_page]
    next_target_pages = sorted({*target_pages, moved_page})
    _require_contiguous(next_target_pages)

    target_invoice["page_numbers"] = next_target_pages
    target_fields = build_viewer_invoice_fields_for_pages(
        next_manifest.get("pages", []),
        next_target_pages,
    )
    target_invoice["fields"] = target_fields
    target_invoice["extraction_fields"] = deepcopy(target_fields)

    if remaining_pages:
        _require_contiguous(remaining_pages)
        source_invoice["page_numbers"] = remaining_pages
        source_fields = build_viewer_invoice_fields_for_pages(
            next_manifest.get("pages", []),
            remaining_pages,
        )
        source_invoice["fields"] = source_fields
        source_invoice["extraction_fields"] = deepcopy(source_fields)
        _validate_source_pages_preserved(manifest, next_manifest)
        return SegmentationEditResult(
            manifest=next_manifest,
            selected_invoice_id=invoice_id,
            recipient_invoice_id=str(target_invoice.get("invoice_id") or ""),
        )

    removed_display_number = invoice_display_number(source_invoice, source_index + 1)
    removed_invoice_id = str(source_invoice.get("invoice_id") or "")
    invoices.pop(source_index)
    _validate_source_pages_preserved(manifest, next_manifest)
    return SegmentationEditResult(
        manifest=next_manifest,
        selected_invoice_id=str(target_invoice.get("invoice_id") or ""),
        removed_invoice_id=removed_invoice_id,
        recipient_invoice_id=str(target_invoice.get("invoice_id") or ""),
        numbering_threshold=removed_display_number,
        numbering_delta=-1,
    )


def split_invoice(
    manifest: dict,
    invoice_id: str,
    after_position: int,
    new_invoice_id: str,
) -> SegmentationEditResult:
    next_manifest = deepcopy(manifest)
    invoices = _manifest_invoices(next_manifest)
    source_index = _invoice_index(invoices, invoice_id)
    source_invoice = invoices[source_index]
    source_pages = _invoice_pages(source_invoice)

    if after_position < 1 or after_position >= len(source_pages):
        raise SegmentationEditError("Il punto di divisione non e valido")

    left_pages = source_pages[:after_position]
    right_pages = source_pages[after_position:]
    _require_contiguous(left_pages)
    _require_contiguous(right_pages)

    display_number = invoice_display_number(source_invoice, source_index + 1)
    source_invoice["page_numbers"] = left_pages
    source_fields = build_viewer_invoice_fields_for_pages(
        next_manifest.get("pages", []),
        left_pages,
    )
    source_invoice["fields"] = source_fields
    source_invoice["extraction_fields"] = deepcopy(source_fields)
    created_fields = build_viewer_invoice_fields_for_pages(
        next_manifest.get("pages", []),
        right_pages,
    )
    created_invoice = {
        "invoice_id": new_invoice_id,
        "display_number": display_number + 1,
        "title": invoice_display_title(display_number + 1),
        "page_numbers": right_pages,
        "fields": created_fields,
        "extraction_fields": deepcopy(created_fields),
    }
    invoices.insert(source_index + 1, created_invoice)
    _validate_source_pages_preserved(manifest, next_manifest)
    link_shared_payment_dates(invoices, next_manifest.get("pages", []))
    return SegmentationEditResult(
        manifest=next_manifest,
        selected_invoice_id=invoice_id,
        created_invoice_id=new_invoice_id,
        numbering_threshold=display_number,
        numbering_delta=1,
    )


def shift_invoice_display_numbers(
    manifest: dict,
    threshold: int,
    delta: int,
    excluded_invoice_ids: set[str] | None = None,
) -> dict:
    excluded = excluded_invoice_ids or set()
    for index, invoice in enumerate(_manifest_invoices(manifest), start=1):
        invoice_id = str(invoice.get("invoice_id") or "")
        current_number = invoice_display_number(invoice, index)
        if invoice_id not in excluded and current_number > threshold:
            current_number += delta
        set_invoice_display_number(invoice, current_number)
    return manifest


def invoice_display_number(invoice: dict, fallback: int) -> int:
    raw_number = invoice.get("display_number")
    try:
        parsed_number = int(raw_number)
    except (TypeError, ValueError):
        parsed_number = 0
    if parsed_number > 0:
        return parsed_number

    title_match = re.search(r"(\d+)", str(invoice.get("title") or ""))
    if title_match:
        return int(title_match.group(1))
    identity_match = re.search(r"(\d+)$", str(invoice.get("invoice_id") or ""))
    if identity_match:
        return int(identity_match.group(1))
    return fallback


def set_invoice_display_number(invoice: dict, display_number: int) -> None:
    invoice["display_number"] = display_number
    invoice["title"] = invoice_display_title(display_number)


def invoice_display_title(display_number: int) -> str:
    return f"Fattura {display_number:03d}"


def _manifest_invoices(manifest: dict) -> list[dict]:
    invoices = manifest.get("invoices")
    if not isinstance(invoices, list) or not invoices:
        raise SegmentationEditError("Manifest senza fatture modificabili")
    if not all(isinstance(invoice, dict) for invoice in invoices):
        raise SegmentationEditError("Manifest con fatture non valide")
    return invoices


def _invoice_index(invoices: list[dict], invoice_id: str) -> int:
    for index, invoice in enumerate(invoices):
        if str(invoice.get("invoice_id") or "") == invoice_id:
            return index
    raise SegmentationEditError("Fattura non trovata nel documento")


def _invoice_pages(invoice: dict) -> list[int]:
    pages = []
    for raw_page in invoice.get("page_numbers", []):
        try:
            pages.append(int(raw_page))
        except (TypeError, ValueError):
            continue
    pages = sorted(set(pages))
    if not pages:
        raise SegmentationEditError("Fattura senza pagine valide")
    _require_contiguous(pages)
    return pages


def _require_contiguous(pages: list[int]) -> None:
    if any(right != left + 1 for left, right in zip(pages, pages[1:])):
        raise SegmentationEditError(
            "La fattura contiene pagine non contigue e non puo essere modificata"
        )


def _validate_source_pages_preserved(before: dict, after: dict) -> None:
    before_pages = {
        page
        for invoice in _manifest_invoices(before)
        for page in _invoice_pages(invoice)
    }
    after_pages = {
        page
        for invoice in _manifest_invoices(after)
        for page in _invoice_pages(invoice)
    }
    if before_pages != after_pages:
        raise SegmentationEditError("La modifica altererebbe le pagine del file origine")
