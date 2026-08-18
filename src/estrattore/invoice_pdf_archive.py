from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from estrattore.processing_storage import ProcessedInvoiceRef
from estrattore.runtime_paths import RuntimePaths, get_runtime_paths


@dataclass(frozen=True)
class ArchivedInvoicePdf:
    path: Path
    relative_path: str
    page_numbers: list[int]
    source_pdf_path: Path
    archived_source_path: Path
    archived_source_relative_path: str


def archive_invoice_pdf(
    invoice_payload: dict[str, Any],
    invoice_ref: ProcessedInvoiceRef,
    record_id: str,
    runtime_paths: RuntimePaths | None = None,
) -> ArchivedInvoicePdf:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()

    source_pdf_path = (
        paths.batches_dir
        / invoice_ref.source_batch_id
        / "input"
        / f"{invoice_ref.document_id}.pdf"
    )
    if not source_pdf_path.is_file():
        raise ValueError(f"PDF sorgente interno non trovato: {source_pdf_path}")

    page_numbers = _page_numbers(invoice_payload)
    output_path = _unique_path(
        paths.archive_invoices_dir
        / _archive_file_name(
            invoice_payload,
            record_id,
        )
    )
    archived_source_path = _archive_source_pdf(
        source_pdf_path=source_pdf_path,
        invoice_ref=invoice_ref,
        runtime_paths=paths,
    )
    _split_pdf_pages(source_pdf_path, output_path, page_numbers)
    return ArchivedInvoicePdf(
        path=output_path,
        relative_path=output_path.relative_to(paths.app_data_dir).as_posix(),
        page_numbers=page_numbers,
        source_pdf_path=source_pdf_path,
        archived_source_path=archived_source_path,
        archived_source_relative_path=archived_source_path.relative_to(
            paths.app_data_dir
        ).as_posix(),
    )


def _archive_source_pdf(
    source_pdf_path: Path,
    invoice_ref: ProcessedInvoiceRef,
    runtime_paths: RuntimePaths,
) -> Path:
    original_name = Path(invoice_ref.source_file_name).name
    original_stem = Path(original_name).stem or invoice_ref.document_id
    source_file_name = "_".join(
        part
        for part in (
            "source",
            _slug(invoice_ref.source_batch_id),
            _slug(invoice_ref.document_id),
            _slug(original_stem),
        )
        if part
    )
    target_path = _unique_path(
        runtime_paths.archive_sources_dir / f"{source_file_name}.pdf"
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_pdf_path, target_path)
    return target_path


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


def _archive_file_name(invoice_payload: dict[str, Any], record_id: str) -> str:
    fields = _fields_by_kind(invoice_payload)
    uid = hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:12]
    parts = [
        f"f_{uid}",
        fields.get("nome_emittente", ""),
        fields.get("condominio", ""),
        fields.get("data_fattura", ""),
    ]
    stem = "_".join(_slug(part) for part in parts if _slug(part))
    return f"{stem or f'f_{uid}'}.pdf"


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
