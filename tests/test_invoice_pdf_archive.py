from pathlib import Path
import shutil
import uuid

from pypdf import PdfReader, PdfWriter

from estrattore.invoice_pdf_archive import archive_invoice_pdf
from estrattore.processing_storage import ProcessedInvoiceRef
from estrattore.runtime_paths import get_runtime_paths


def _workspace() -> Path:
    root = Path("invoice-pdf-archive-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _clean_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_archive_invoice_pdf_splits_selected_pages_from_internal_source() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_pdf_path = paths.batches_dir / "batch_001" / "input" / "doc_000001.pdf"
        _write_blank_pdf(source_pdf_path, page_count=3)

        archived_pdf = archive_invoice_pdf(
            {
                "pageNumbers": [1, 3],
                "fields": [
                    {"kind": "nome_emittente", "value": "Primo Sole"},
                    {"kind": "condominio", "value": "Condominio Due Giugno"},
                    {"kind": "data_fattura", "value": "21-12-2024"},
                ],
            },
            ProcessedInvoiceRef(
                invoice_id="invoice-001",
                title="Fattura 001",
                document_id="doc_000001",
                source_batch_id="batch_001",
                source_file_name="originale.pdf",
            ),
            record_id="invoice:process:review:doc_000001:invoice-001",
            runtime_paths=paths,
        )

        assert archived_pdf.relative_path.startswith("archive/invoices/f_")
        assert archived_pdf.path.is_file()
        assert archived_pdf.archived_source_relative_path.startswith("archive/sources/source_")
        assert archived_pdf.archived_source_path.is_file()
        assert len(PdfReader(str(archived_pdf.path)).pages) == 2
        assert archived_pdf.page_numbers == [1, 3]
    finally:
        _clean_workspace(workspace)


def test_archive_invoice_pdf_keeps_history_when_names_collide() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_pdf_path = paths.batches_dir / "batch_001" / "input" / "doc_000001.pdf"
        _write_blank_pdf(source_pdf_path, page_count=1)
        invoice_ref = ProcessedInvoiceRef(
            invoice_id="invoice-001",
            title="Fattura 001",
            document_id="doc_000001",
            source_batch_id="batch_001",
            source_file_name="originale.pdf",
        )
        invoice_payload = {
            "pageNumbers": [1],
            "fields": [
                {"kind": "nome_emittente", "value": "Primo Sole"},
                {"kind": "condominio", "value": "Condominio Due Giugno"},
                {"kind": "data_fattura", "value": "21-12-2024"},
            ],
        }

        first_archive = archive_invoice_pdf(
            invoice_payload,
            invoice_ref,
            record_id="same-record-id",
            runtime_paths=paths,
        )
        second_archive = archive_invoice_pdf(
            invoice_payload,
            invoice_ref,
            record_id="same-record-id",
            runtime_paths=paths,
        )

        assert first_archive.path.name != second_archive.path.name
        assert second_archive.path.name.endswith("(1).pdf")
        assert first_archive.archived_source_path.name != second_archive.archived_source_path.name
        assert second_archive.archived_source_path.name.endswith("(1).pdf")
    finally:
        _clean_workspace(workspace)


def _write_blank_pdf(path: Path, page_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as output_file:
        writer.write(output_file)
