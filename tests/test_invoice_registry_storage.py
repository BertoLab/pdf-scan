import json
from datetime import datetime
from pathlib import Path
import shutil
import uuid

from openpyxl import load_workbook

from estrattore.batch_storage import import_pdf_batch
from estrattore.invoice_pdf_archive import ArchivedInvoicePdf
from estrattore.invoice_registry_storage import (
    discard_reviewed_invoice,
    load_invoice_registry,
    save_invoice_registry_excel,
    save_reviewed_invoice,
)
from estrattore.processing_storage import list_review_batches, process_imported_batch
from estrattore.runtime_paths import get_runtime_paths


def _workspace() -> Path:
    root = Path("invoice-registry-storage-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _clean_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_save_reviewed_invoice_persists_snapshot_and_removes_it_from_review(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        paths, review_batch = _processed_review_batch(workspace)
        invoice_payload = _invoice_payload("invoice-001")
        _mock_pdf_archive(monkeypatch, paths)

        record = save_reviewed_invoice(
            {
                "selected_batch_id": review_batch.id,
                "review_batch_base_path": review_batch.base_path,
                "invoice": invoice_payload,
            },
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )

        registry = load_invoice_registry(paths)
        assert record["status"] == "saved"
        assert record["archived_pdf_path"] == "archive/invoices/fake_invoice.pdf"
        assert record["archived_pdf_url"] == "/runtime/archive/invoices/fake_invoice.pdf"
        assert record["archived_pdf_page_numbers"] == [1]
        assert record["archived_source_pdf_path"] == "archive/sources/source.pdf"
        assert record["archived_source_pdf_url"] == "/runtime/archive/sources/source.pdf"
        assert registry["records"][0]["invoice"]["fields"][0]["value"] == "85,79"
        assert list_review_batches(paths) == []

        viewer_index_path = (
            paths.manifests_dir
            / "process_20260810_110000"
            / "review_batches"
            / "review_batch_0001"
            / "viewer_index.json"
        )
        viewer_index = json.loads(viewer_index_path.read_text(encoding="utf-8"))
        assert viewer_index["documents"] == []
        assert not (
            paths.manifests_dir
            / "process_20260810_110000"
            / "documents"
            / "doc_000001"
        ).exists()
        assert not (
            paths.batches_dir
            / "batch_20260810_100000"
            / "input"
            / "doc_000001.pdf"
        ).exists()
    finally:
        _clean_workspace(workspace)


def test_save_reviewed_invoice_accepts_composite_frontend_invoice_id(monkeypatch) -> None:
    workspace = _workspace()
    try:
        paths, review_batch = _processed_review_batch(workspace)
        invoice_payload = _invoice_payload("doc_000001:invoice-001")
        invoice_payload["manifestInvoiceId"] = "invoice-001"
        _mock_pdf_archive(monkeypatch, paths)

        save_reviewed_invoice(
            {
                "selected_batch_id": review_batch.id,
                "review_batch_base_path": review_batch.base_path,
                "invoice": invoice_payload,
            },
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )

        assert list_review_batches(paths) == []
    finally:
        _clean_workspace(workspace)


def test_discard_reviewed_invoice_is_persistent_and_idempotent() -> None:
    workspace = _workspace()
    try:
        paths, review_batch = _processed_review_batch(workspace)
        payload = {
            "selected_batch_id": review_batch.id,
            "review_batch_base_path": review_batch.base_path,
            "invoice": _invoice_payload("invoice-001"),
        }

        discard_reviewed_invoice(
            payload,
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )
        discard_reviewed_invoice(
            payload,
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 1, 0),
        )

        registry = load_invoice_registry(paths)
        assert len(registry["records"]) == 1
        assert registry["records"][0]["status"] == "discarded"
        assert registry["records"][0]["discarded_at"] == "2026-08-10T12:01:00"
        assert list_review_batches(paths) == []
    finally:
        _clean_workspace(workspace)


def test_save_invoice_registry_excel_exports_only_saved_records(monkeypatch) -> None:
    workspace = _workspace()
    try:
        paths, review_batch = _processed_review_batch(workspace)
        _mock_pdf_archive(monkeypatch, paths)
        save_reviewed_invoice(
            {
                "selected_batch_id": review_batch.id,
                "review_batch_base_path": review_batch.base_path,
                "invoice": _invoice_payload("invoice-001"),
            },
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )
        registry = load_invoice_registry(paths)
        registry["records"].append(
            {
                "record_id": "discarded-record",
                "status": "discarded",
                "invoice": _invoice_payload("invoice-999"),
            }
        )
        paths.invoice_registry_file.write_text(
            json.dumps(registry, ensure_ascii=False),
            encoding="utf-8",
        )

        output_path = save_invoice_registry_excel(runtime_paths=paths)

        workbook = load_workbook(output_path)
        sheet = workbook["Registro fatture"]
        headers = [cell.value for cell in sheet[1]]
        values = [cell.value for cell in sheet[2]]
        assert headers[:4] == [
            "Salvata il",
            "Nome emittente",
            "C.F. emittente",
            "P.IVA emittente",
        ]
        assert sheet.max_row == 2
        assert values[0] == "2026-08-10"
        assert values[6] == "6254/CV"
        assert values[8] == "85,79"
        assert values[12] == "source.pdf"
        assert values[13] == "archive/invoices/fake_invoice.pdf"
        assert values[14] == "archive/sources/source.pdf"
    finally:
        _clean_workspace(workspace)


def _processed_review_batch(workspace: Path):
    source_file = workspace / "source.pdf"
    source_file.write_bytes(b"%PDF")
    paths = get_runtime_paths(workspace / "app-data")
    imported_batch = import_pdf_batch(
        [source_file],
        runtime_paths=paths,
        now=datetime(2026, 8, 10, 10, 0, 0),
    )

    def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "viewer_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_file": "source.pdf",
                    "pages": [],
                    "invoices": [
                        {
                            "invoice_id": f"invoice-{first_invoice_number:03d}",
                            "title": f"Fattura {first_invoice_number:03d}",
                            "page_numbers": [1],
                            "fields": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return [manifest_path]

    run_index = process_imported_batch(
        imported_batch.batch_id,
        runtime_paths=paths,
        document_processor=fake_processor,
        now=datetime(2026, 8, 10, 11, 0, 0),
    )
    return paths, run_index.review_batches[0]


def _invoice_payload(invoice_id: str) -> dict:
    return {
        "invoiceId": invoice_id,
        "title": "Fattura 001",
        "documentId": "doc_000001",
        "manifestInvoiceId": invoice_id.rsplit(":", 1)[-1],
        "sourceFile": "source.pdf",
        "basePath": "/runtime/manifests/process_20260810_110000/documents/doc_000001",
        "pageNumbers": [1],
        "pages": [],
        "fields": [
            {
                "kind": "totale_fattura",
                "label": "Totale fattura",
                "value": "85,79",
                "score": 0.96,
            },
            {
                "kind": "numero_fattura",
                "label": "Numero fattura",
                "value": "6254/CV",
                "score": 0.91,
            }
        ],
    }


def _mock_pdf_archive(monkeypatch, paths) -> None:
    def fake_archive(_invoice_payload, _invoice_ref, _record_id, _runtime_paths):
        return ArchivedInvoicePdf(
            path=paths.archive_invoices_dir / "fake_invoice.pdf",
            relative_path="archive/invoices/fake_invoice.pdf",
            page_numbers=[1],
            source_pdf_path=paths.batches_dir / "batch" / "input" / "doc_000001.pdf",
            archived_source_path=paths.archive_sources_dir / "source.pdf",
            archived_source_relative_path="archive/sources/source.pdf",
        )

    monkeypatch.setattr(
        "estrattore.invoice_registry_storage.archive_invoice_pdf",
        fake_archive,
    )
