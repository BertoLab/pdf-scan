from pathlib import Path
import shutil
import uuid

from pypdf import PdfReader, PdfWriter

from estrattore.invoice_pdf_archive import archive_invoice_pdf, _archive_file_name
from estrattore.batch_storage import import_pdf_batch
from estrattore.processing_storage import ProcessedInvoiceRef
from estrattore.runtime_paths import get_runtime_paths
from estrattore.source_archive_storage import promote_batch_sources


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
        external_source = workspace / "originale.pdf"
        _write_blank_pdf(external_source, page_count=3)
        batch = import_pdf_batch([external_source], paths)
        promote_batch_sources(batch, "process_001", paths)

        archived_pdf = archive_invoice_pdf(
            {
                "pageNumbers": [1, 3],
                "fields": [
                    {"kind": "numero_fattura", "value": "123/2024"},
                    {"kind": "nome_emittente", "value": "Primo Sole"},
                    {"kind": "condominio", "value": "Condominio Due Giugno"},
                    {"kind": "data_fattura", "value": "21-12-2024"},
                ],
            },
            ProcessedInvoiceRef(
                invoice_id="invoice-001",
                title="Fattura 001",
                document_id="doc_000001",
                source_batch_id=batch.batch_id,
                source_file_name="originale.pdf",
            ),
            runtime_paths=paths,
        )

        assert archived_pdf.path.name == (
            "Fattura_123_2024_Primo_Sole_Condominio_Due_Giugno_21-12-2024.pdf"
        )
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
        external_source = workspace / "originale.pdf"
        _write_blank_pdf(external_source, page_count=1)
        batch = import_pdf_batch([external_source], paths)
        promote_batch_sources(batch, "process_001", paths)
        invoice_ref = ProcessedInvoiceRef(
            invoice_id="invoice-001",
            title="Fattura 001",
            document_id="doc_000001",
            source_batch_id=batch.batch_id,
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
            runtime_paths=paths,
        )
        second_archive = archive_invoice_pdf(
            invoice_payload,
            invoice_ref,
            runtime_paths=paths,
        )

        assert first_archive.path.name != second_archive.path.name
        assert second_archive.path.name.endswith("(1).pdf")
        assert first_archive.archived_source_path == second_archive.archived_source_path
    finally:
        _clean_workspace(workspace)


def test_archive_reprocessed_invoice_composes_parent_page_map_without_source_pdf() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        parent_pdf = paths.archive_invoices_dir / "parent.pdf"
        _write_blank_pdf(parent_pdf, page_count=3)
        parent_record = {
            "record_id": "parent-record",
            "archived_pdf_path": "archive/invoices/parent.pdf",
            "archived_pdf_page_numbers": [4, 7, 9],
            "archived_source_pdf_path": "",
        }

        archived_pdf = archive_invoice_pdf(
            {"pageNumbers": [1, 3], "fields": []},
            ProcessedInvoiceRef(
                invoice_id="invoice-001",
                title="Fattura 001",
                document_id="doc_000001",
                source_batch_id="batch_reprocess",
                source_file_name="documento.pdf",
                parent_invoice_record_id="parent-record",
                source_page_numbers=[4, 7, 9],
            ),
            runtime_paths=paths,
            parent_invoice_record=parent_record,
        )

        assert archived_pdf.page_numbers == [4, 9]
        assert archived_pdf.path.name == "Fattura.pdf"
        assert len(PdfReader(str(archived_pdf.path)).pages) == 2
        assert archived_pdf.archived_source_path is None
        assert archived_pdf.archived_source_relative_path == ""
        assert list(paths.archive_sources_dir.glob("*.pdf")) == []
    finally:
        _clean_workspace(workspace)


def test_readable_archive_name_bounds_long_fields_and_removes_windows_separators() -> None:
    # Long business names and invoice numbers must stay filename-safe and bounded.
    name = _archive_file_name({"fields": [
        {"kind": "numero_fattura", "value": "12/34:56"},
        {"kind": "nome_emittente", "value": "A" * 200},
        {"kind": "condominio", "value": "B" * 200},
        {"kind": "data_fattura", "value": "2026-09-07"},
    ]})
    assert name.startswith("Fattura_12_34_56_")
    assert name.endswith("_2026-09-07.pdf")
    assert len(name) <= 180
    assert not any(character in name for character in '<>:"/\\|?*')


def _write_blank_pdf(path: Path, page_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as output_file:
        writer.write(output_file)
