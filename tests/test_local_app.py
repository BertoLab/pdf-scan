from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import scripts.registry_api as registry_api
from estrattore.batch_storage import import_pdf_batch
from estrattore.invoice_pdf_archive import ArchivedInvoicePdf
import estrattore.feedback_export as feedback_export
from estrattore.processing_storage import process_imported_batch
from estrattore.runtime_paths import get_runtime_paths
from scripts.local_app import LocalAppHandler


def test_local_app_routes_feedback_export_to_desktop_service(monkeypatch) -> None:
    workspace = Path("local-app-feedback-export-test-manual") / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    server = None
    server_thread = None
    try:
        paths = get_runtime_paths(workspace / "app-data")
        desktop = workspace / "Desktop"
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
        monkeypatch.setattr(registry_api, "ensure_registry_runtime_ready", lambda: None)
        monkeypatch.setattr(feedback_export, "resolve_user_desktop", lambda: desktop)

        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAppHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        request = Request(
            f"http://127.0.0.1:{server.server_port}/feedback/export",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert response.headers.get_content_type() == "application/json"
        assert payload["ok"] is True
        assert payload["folder_name"].startswith(
            "EstrattoreScanFatture_DatiMiglioramento_"
        )
        output_path = Path(payload["output_path"])
        assert output_path.parent == desktop
        assert (output_path / "feedback.sqlite3").is_file()
        manifest = json.loads(
            (output_path / "bundle_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["includes_pdfs"] is True
        assert payload["stats"]["runs"] == 0
        assert payload["missing_pdf_count"] == 0
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=2)
        shutil.rmtree(workspace, ignore_errors=True)


def test_local_app_routes_review_drafts_to_backend(monkeypatch) -> None:
    monkeypatch.setattr(registry_api, "ensure_registry_runtime_ready", lambda: None)
    monkeypatch.setattr(
        registry_api,
        "list_review_invoice_drafts",
        lambda _runtime_paths: {"schema_version": 1, "drafts": []},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAppHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/viewer/drafts",
            timeout=2,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert response.headers.get_content_type() == "application/json"
        assert payload == {"ok": True, "schema_version": 1, "drafts": []}
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_local_app_invoice_save_persists_feedback_revision(monkeypatch) -> None:
    workspace = Path("local-app-test-manual") / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    server = None
    server_thread = None
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF-feedback-http-test")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 9, 16, 9, 0, 0),
        )

        def fake_processor(
            _source_path: Path,
            output_dir: Path,
            first_invoice_number: int,
        ) -> list[Path]:
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
                                "title": "Fattura 001",
                                "page_numbers": [1],
                                "extraction_fields": [
                                    {
                                        "kind": "totale_fattura",
                                        "label": "Totale fattura",
                                        "value": "85,79",
                                        "score": 0.96,
                                    }
                                ],
                                "fields": [
                                    {
                                        "kind": "totale_fattura",
                                        "label": "Totale fattura",
                                        "value": "85,79",
                                        "score": 0.96,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 9, 16, 9, 5, 0),
        )
        review_batch = run_index.review_batches[0]

        def fake_archive(*_args, **_kwargs) -> ArchivedInvoicePdf:
            return ArchivedInvoicePdf(
                path=paths.archive_invoices_dir / "fattura.pdf",
                relative_path="archive/invoices/fattura.pdf",
                page_numbers=[1],
                source_pdf_path=source_file,
                archived_source_path=paths.archive_sources_dir / "source.pdf",
                archived_source_relative_path="archive/sources/source.pdf",
            )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
        monkeypatch.setattr(registry_api, "ensure_registry_runtime_ready", lambda: None)
        monkeypatch.setattr(
            "estrattore.invoice_registry_storage.archive_invoice_pdf",
            fake_archive,
        )

        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAppHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        invoice = {
            "invoiceId": "invoice-001",
            "manifestInvoiceId": "invoice-001",
            "documentId": "doc_000001",
            "sourceFile": "source.pdf",
            "basePath": (
                f"/runtime/manifests/{run_index.process_id}/documents/doc_000001"
            ),
            "pageNumbers": [1],
            "fields": [
                {
                    "kind": "totale_fattura",
                    "label": "Totale fattura",
                    "value": "90,00",
                    "score": 0.96,
                    "review_interaction": "manual_input",
                    "review_value_origin": "manual_input",
                    "review_action_id": "http-action-1",
                }
            ],
        }
        body = json.dumps(
            {
                "selected_batch_id": review_batch.id,
                "review_batch_base_path": review_batch.base_path,
                "invoice": invoice,
            }
        ).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{server.server_port}/invoices/save",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert payload["ok"] is True
        assert paths.invoice_registry_file.is_file()
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            revision = connection.execute(
                "SELECT field_provenance_json FROM review_revisions"
            ).fetchone()
        provenance = json.loads(revision[0])
        assert provenance[0]["extracted_value"] == "85,79"
        assert provenance[0]["accepted_value"] == "90,00"
        assert provenance[0]["interaction"] == "manual_input"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=2)
        shutil.rmtree(workspace, ignore_errors=True)
