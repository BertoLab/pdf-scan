from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from estrattore.processing_storage import ProcessedInvoiceRef
from estrattore.runtime_paths import RuntimePaths, get_runtime_paths
from estrattore.source_archive_storage import resolve_archived_source_for_origin


@dataclass(frozen=True)
class ArchivedInvoicePdf:
    path: Path
    relative_path: str
    page_numbers: list[int]
    source_pdf_path: Path
    archived_source_path: Path | None
    archived_source_relative_path: str


def archive_invoice_pdf(
    invoice_payload: dict[str, Any],
    invoice_ref: ProcessedInvoiceRef,
    runtime_paths: RuntimePaths | None = None,
    parent_invoice_record: dict[str, Any] | None = None,
    file_stem: str | None = None,
) -> ArchivedInvoicePdf:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()

    local_page_numbers = _page_numbers(invoice_payload)
    archived_source_relative_path = ""
    archived_source_path: Path | None = None
    if parent_invoice_record is not None:
        source_pdf_path = _parent_invoice_pdf_path(parent_invoice_record, paths)
        page_numbers = _compose_page_numbers(
            local_page_numbers,
            invoice_ref.source_page_numbers,
        )
        archived_source_relative_path = str(
            parent_invoice_record.get("archived_source_pdf_path") or ""
        )
        if archived_source_relative_path:
            candidate = (paths.app_data_dir / archived_source_relative_path).resolve()
            if candidate.is_file() and _is_inside(candidate, paths.archive_sources_dir):
                archived_source_path = candidate
    else:
        archived_source, source_pdf_path = resolve_archived_source_for_origin(
            invoice_ref.source_batch_id,
            invoice_ref.document_id,
            paths,
        )
        page_numbers = local_page_numbers
        archived_source_relative_path = archived_source.relative_path
        archived_source_path = source_pdf_path

    output_path = _unique_path(
        paths.archive_invoices_dir
        / _archive_file_name(
            invoice_payload,
            file_stem=file_stem,
        )
    )
    _split_pdf_pages(source_pdf_path, output_path, local_page_numbers)
    return ArchivedInvoicePdf(
        path=output_path,
        relative_path=output_path.relative_to(paths.app_data_dir).as_posix(),
        page_numbers=page_numbers,
        source_pdf_path=source_pdf_path,
        archived_source_path=archived_source_path,
        archived_source_relative_path=archived_source_relative_path,
    )


def _split_pdf_pages(
    source_pdf_path: Path,
    output_path: Path,
    page_numbers: list[int],
) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as error:
        raise RuntimeError(
            "Split PDF non disponibile: installa la dipendenza pypdf."
        ) from error

    reader = PdfReader(str(source_pdf_path))
    writer = PdfWriter()
    page_count = len(reader.pages)
    for page_number in page_numbers:
        if page_number < 1 or page_number > page_count:
            raise ValueError(
                f"Pagina {page_number} fuori dal PDF sorgente di {page_count} pagine"
            )
        writer.add_page(reader.pages[page_number - 1])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        writer.write(output_file)


def _page_numbers(invoice_payload: dict[str, Any]) -> list[int]:
    raw_page_numbers = invoice_payload.get("pageNumbers")
    if not isinstance(raw_page_numbers, list) or not raw_page_numbers:
        raw_page_numbers = [
            page.get("page")
            for page in invoice_payload.get("pages", [])
            if isinstance(page, dict)
        ]

    page_numbers = sorted(
        {
            int(page_number)
            for page_number in raw_page_numbers
            if isinstance(page_number, int | float | str)
            and str(page_number).strip().isdigit()
        }
    )
    if not page_numbers:
        raise ValueError("Numeri pagina mancanti per lo split PDF")
    return page_numbers


def _archive_file_name(
    invoice_payload: dict[str, Any],
    file_stem: str | None = None,
) -> str:
    if file_stem:
        safe_file_stem = _slug(file_stem)
        if safe_file_stem:
            return f"{safe_file_stem}.pdf"
    fields = _fields_by_kind(invoice_payload)
    parts = [
        "Fattura",
        _slug(fields.get("numero_fattura", ""))[:30],
        _slug(fields.get("nome_emittente", ""))[:55],
        _slug(fields.get("condominio", ""))[:55],
        _slug(fields.get("data_fattura", ""))[:20],
    ]
    stem = "_".join(part for part in parts if part)
    return f"{stem}.pdf"


def invoice_page_label(source_file_name: str, page_numbers: list[int]) -> str:
    source_stem = Path(source_file_name).stem or "File origine"
    clean_pages = sorted(set(page_numbers))
    if not clean_pages:
        return source_stem
    if len(clean_pages) == 1:
        return f"{source_stem} - pagina {clean_pages[0]}"
    if clean_pages == list(range(clean_pages[0], clean_pages[-1] + 1)):
        return f"{source_stem} - pagine {clean_pages[0]}-{clean_pages[-1]}"
    return f"{source_stem} - pagine {', '.join(str(page) for page in clean_pages)}"


def _parent_invoice_pdf_path(
    parent_invoice_record: dict[str, Any],
    paths: RuntimePaths,
) -> Path:
    relative_path = str(parent_invoice_record.get("archived_pdf_path") or "")
    candidate = (paths.app_data_dir / relative_path).resolve()
    if not relative_path or not candidate.is_file() or not _is_inside(
        candidate, paths.archive_invoices_dir
    ):
        raise ValueError("PDF della fattura di origine non disponibile")
    return candidate


def _compose_page_numbers(
    local_page_numbers: list[int],
    source_page_numbers: list[int],
) -> list[int]:
    if not source_page_numbers:
        return local_page_numbers
    mapped: list[int] = []
    for local_page_number in local_page_numbers:
        source_index = local_page_number - 1
        if source_index < 0 or source_index >= len(source_page_numbers):
            raise ValueError("Mappa pagine della fattura non valida")
        mapped.append(source_page_numbers[source_index])
    return mapped


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}({index}){path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Troppi file con lo stesso nome in archivio: {path.name}")


def _fields_by_kind(invoice_payload: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in invoice_payload.get("fields", []):
        if not isinstance(field, dict):
            continue
        kind = str(field.get("kind") or "").strip()
        value = str(field.get("value") or "").strip()
        if kind and value:
            fields[kind] = value
    return fields


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^0-9a-zA-Z_-]+", "_", ascii_value.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:80]


def _is_inside(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents
