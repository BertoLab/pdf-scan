from __future__ import annotations

import json
import tempfile
import threading
import time
import hashlib
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from estrattore.registry import (
    DEFAULT_REGISTRY_CACHE_PATH,
    DEFAULT_REGISTRY_DUPLICATE_REPORT_PATH,
    DEFAULT_REGISTRY_INPUT_PATH,
    RegistryCache,
    RegistryRecord,
    clean_registry_cache_duplicates,
    extract_registry_fiscal_identifiers,
    import_registry_excel,
    extract_registry_code,
    load_registry_cache,
    normalize_registry_name,
    preview_registry_cache_duplicates,
    save_registry_cache,
    save_registry_cache_excel,
    save_registry_duplicate_report_excel,
)
from estrattore.fiscal_validation import (
    normalize_italian_tax_code,
    normalize_italian_vat_number,
)
from estrattore.batch_storage import (
    ImportedBatchIndex,
    PdfBatchSource,
    delete_imported_batch,
    import_pdf_batch,
    list_imported_batches,
    load_batch_index,
)
from estrattore.invoice_registry_storage import (
    discard_reviewed_invoice,
    load_invoice_registry,
    save_invoice_registry_excel,
    save_invoice_registry_records,
    save_reviewed_invoice,
)
from estrattore.processing_storage import (
    ProcessingCancelled,
    ProcessingPaused,
    ProcessingRunIndex,
    ReviewBatchRef,
    cleanup_incomplete_processing_runs,
    is_document_processing_complete,
    list_review_batches,
    move_review_invoice_page,
    process_imported_batch,
)
from estrattore.runtime_paths import (
    get_runtime_paths,
    migrate_legacy_registry_files_if_needed,
)


HOST = "127.0.0.1"
PORT = 8765
UPLOAD_FIELD_NAME = "file"
PDF_UPLOAD_FIELD_NAME = "files"
RUNTIME_PATHS = get_runtime_paths()
LEGACY_REGISTRY_MIGRATIONS: list[tuple[Path, Path]] = []
_REGISTRY_RUNTIME_READY = False
REGISTRY_DUPLICATE_REPORT_XLSX_PATH = RUNTIME_PATHS.registry_duplicate_report_xlsx_file
REGISTRY_EXPORT_XLSX_PATH = RUNTIME_PATHS.registry_export_xlsx_file
PROCESSING_JOBS: dict[str, dict[str, Any]] = {}
PROCESSING_JOBS_LOCK = threading.Lock()
PROCESSING_JOB_QUEUE: list[str] = []
PROCESSING_ACTIVE_JOB_ID: str | None = None
INCOMPLETE_PROCESSING_CLEANUPS: list[str] = []
PROCESSING_JOBS_FILE_NAME = "processing_jobs.json"
TERMINAL_PROCESSING_JOB_STATUSES = {"completed", "error", "cancelled", "removed"}
PENDING_IMPORT_SOURCES: dict[str, dict[str, Any]] = {}
PENDING_IMPORT_SOURCES_LOCK = threading.Lock()


class RegistryApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_empty_response(204)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json_response(200, {"ok": True})
            return

        if self.path == "/runtime/paths":
            self._send_json_response(200, _runtime_paths_payload())
            return

        ensure_registry_runtime_ready()

        if self.path == "/batches":
            self._send_json_response(200, _batches_payload())
            return

        if self.path == "/viewer/review-batches":
            self._send_json_response(200, _review_batches_payload())
            return

        if self.path == "/processing/jobs":
            self._send_json_response(200, _processing_jobs_payload())
            return

        if self.path == "/invoices/registry":
            self._send_json_response(
                200,
                {"ok": True, "registry": load_invoice_registry(RUNTIME_PATHS)},
            )
            return

        if self.path == "/invoices/export.xlsx":
            self._send_invoice_registry_export_file_response()
            return

        if self.path.startswith("/processing/jobs/"):
            try:
                self._send_json_response(200, _processing_job_payload(self.path))
            except Exception as error:
                self._send_json_response(404, {"ok": False, "error": str(error)})
            return

        if self.path == "/registry/status":
            self._send_json_response(200, _registry_status_payload())
            return

        if self.path == "/registry/file":
            self._send_registry_file_response()
            return

        if self.path == "/registry/cache.json":
            self._send_registry_cache_file_response()
            return

        if self.path == "/registry/export.xlsx":
            self._send_registry_export_file_response()
            return

        if self.path == "/registry/cleaning-report.xlsx":
            self._send_registry_cleaning_report_file_response()
            return

        if self.path == "/registry/records":
            self._send_json_response(200, _registry_records_payload())
            return

        self._send_json_response(404, {"error": "Endpoint non trovato"})

    def do_POST(self) -> None:
        ensure_registry_runtime_ready()

        if self.path == "/batches/import":
            try:
                upload_sources = self._save_uploaded_pdf_files()
                try:
                    batch = import_pdf_batch(upload_sources, runtime_paths=RUNTIME_PATHS)
                finally:
                    for upload_source in upload_sources:
                        upload_source.path.unlink(missing_ok=True)

                self._send_json_response(200, _batch_import_payload(batch))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/batches/pick-folder":
            try:
                folder_path = _pick_windows_folder()
                if folder_path is None:
                    self._send_json_response(
                        200,
                        {"ok": False, "cancelled": True, "message": "Selezione cartella annullata"},
                    )
                    return

                self._send_json_response(
                    200,
                    _pending_import_source_response(_new_folder_import_source(folder_path)),
                )
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/batches/pick-files":
            try:
                file_paths = _pick_windows_pdf_files()
                if not file_paths:
                    self._send_json_response(
                        200,
                        {"ok": False, "cancelled": True, "message": "Selezione PDF annullata"},
                    )
                    return

                self._send_json_response(
                    200,
                    _pending_import_source_response(_new_files_import_source(file_paths)),
                )
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/batches/import-sources":
            try:
                payload = self._read_json_body()
                batch = _import_pending_sources(payload)
                self._send_json_response(200, _batch_import_payload(batch))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/batches/import-sources/discard":
            try:
                payload = self._read_json_body()
                _discard_pending_sources(payload)
                self._send_json_response(200, {"ok": True})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/batches/process":
            try:
                payload = self._read_json_body()
                job = _start_processing_job(payload)
                self._send_json_response(202, job)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/archive/sources/reprocess":
            try:
                payload = self._read_json_body()
                job = _reprocess_archived_source_pdf(payload)
                self._send_json_response(202, job)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path.startswith("/processing/jobs/") and self.path.endswith("/cancel"):
            try:
                self._send_json_response(200, _cancel_processing_job(self.path))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path.startswith("/processing/jobs/") and self.path.endswith("/pause"):
            try:
                self._send_json_response(200, _pause_processing_job(self.path))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path.startswith("/processing/jobs/") and self.path.endswith("/resume"):
            try:
                self._send_json_response(200, _resume_processing_job(self.path))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path.startswith("/processing/jobs/") and self.path.endswith("/remove"):
            try:
                self._send_json_response(200, _remove_processing_job(self.path))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/invoices/save":
            try:
                record = save_reviewed_invoice(
                    self._read_json_body(),
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, {"ok": True, "record": record})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/invoices/registry":
            try:
                payload = self._read_json_body()
                records = payload.get("records")
                if not isinstance(records, list):
                    raise ValueError("Righe registro fatture mancanti")
                registry = save_invoice_registry_records(
                    records,
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, {"ok": True, "registry": registry})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/invoices/discard":
            try:
                record = discard_reviewed_invoice(
                    self._read_json_body(),
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, {"ok": True, "record": record})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/viewer/move-page":
            try:
                result = move_review_invoice_page(
                    self._read_json_body(),
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, result)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/registry/records":
            try:
                payload = self._read_json_body()
                cache = _save_registry_records_payload(payload)
                self._send_json_response(200, _registry_records_payload(cache))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/registry/clean-duplicates":
            try:
                payload = self._read_optional_json_body()
                cache = _clean_registry_duplicates(payload)
                self._send_json_response(200, cache)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/registry/clean-duplicates/preview":
            try:
                payload = _preview_registry_duplicates()
                self._send_json_response(200, payload)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path != "/registry/import":
            self._send_json_response(404, {"error": "Endpoint non trovato"})
            return

        try:
            upload_path = self._save_uploaded_file()
            try:
                cache = import_registry_excel(upload_path)
                REGISTRY_DUPLICATE_REPORT_XLSX_PATH.unlink(missing_ok=True)
            finally:
                upload_path.unlink(missing_ok=True)

            self._send_json_response(
                200,
                {
                    "ok": True,
                    "record_count": len(cache.records),
                    "warning_counts": _warning_counts(cache.records),
                    "source_file": cache.source_file,
                },
            )
        except Exception as error:
            self._send_json_response(400, {"ok": False, "error": str(error)})

    def do_DELETE(self) -> None:
        ensure_registry_runtime_ready()

        if self.path == "/registry":
            try:
                _delete_registry()
                self._send_json_response(200, {"ok": True})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path.startswith("/batches/"):
            try:
                self._send_json_response(200, _delete_imported_batch(self.path))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        self._send_json_response(404, {"error": "Endpoint non trovato"})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[registry-api] {self.address_string()} - {format % args}")

    def _save_uploaded_file(self) -> Path:
        content_type = self.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("La richiesta deve essere multipart/form-data")

        content_length = int(self.headers.get("content-length", "0"))
        if content_length <= 0:
            raise ValueError("File anagrafica mancante")

        raw_body = self.rfile.read(content_length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + content_type.encode("utf-8")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + raw_body
        )
        file_part = next(
            (
                part
                for part in message.iter_parts()
                if part.get_param("name", header="content-disposition")
                == UPLOAD_FIELD_NAME
            ),
            None,
        )
        if file_part is None:
            raise ValueError("File anagrafica mancante")

        filename = Path(file_part.get_filename() or "").name
        if Path(filename).suffix.lower() != ".xlsx":
            raise ValueError("Il file anagrafica deve essere un Excel .xlsx")

        payload = file_part.get_payload(decode=True)
        if not payload:
            raise ValueError("File anagrafica vuoto")

        upload_dir = RUNTIME_PATHS.uploads_dir / "pdf"
        upload_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=upload_dir,
            suffix=".xlsx",
            delete=False,
        ) as temporary_file:
            temporary_file.write(payload)
            return Path(temporary_file.name)

    def _save_uploaded_pdf_files(self) -> list[PdfBatchSource]:
        content_type = self.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("La richiesta deve essere multipart/form-data")

        content_length = int(self.headers.get("content-length", "0"))
        if content_length <= 0:
            raise ValueError("PDF mancanti")

        raw_body = self.rfile.read(content_length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + content_type.encode("utf-8")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + raw_body
        )
        file_parts = [
            part
            for part in message.iter_parts()
            if part.get_param("name", header="content-disposition")
            in {PDF_UPLOAD_FIELD_NAME, UPLOAD_FIELD_NAME}
        ]
        if not file_parts:
            raise ValueError("Seleziona almeno un PDF da caricare")

        upload_dir = RUNTIME_PATHS.registry_uploads_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_sources: list[PdfBatchSource] = []
        try:
            for file_part in file_parts:
                filename = Path(file_part.get_filename() or "").name
                if Path(filename).suffix.lower() != ".pdf":
                    raise ValueError("Puoi caricare solo file PDF")

                payload = file_part.get_payload(decode=True)
                if not payload:
                    raise ValueError("Uno dei PDF caricati e vuoto")

                with tempfile.NamedTemporaryFile(
                    dir=upload_dir,
                    suffix=".pdf",
                    delete=False,
                ) as temporary_file:
                    temporary_file.write(payload)
                    upload_sources.append(
                        PdfBatchSource(
                            path=Path(temporary_file.name),
                            original_file_name=filename,
                        )
                    )
            return upload_sources
        except Exception:
            for upload_source in upload_sources:
                upload_source.path.unlink(missing_ok=True)
            raise

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length <= 0:
            raise ValueError("Richiesta vuota")

        raw_body = self.rfile.read(content_length)
        return json.loads(raw_body.decode("utf-8"))

    def _read_optional_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)
        if not raw_body.strip():
            return {}

        return json.loads(raw_body.decode("utf-8"))

    def _send_empty_response(self, status_code: int) -> None:
        self.send_response(status_code)
        self._send_cors_headers()
        self.end_headers()

    def _send_json_response(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_registry_file_response(self) -> None:
        if not DEFAULT_REGISTRY_INPUT_PATH.exists():
            self._send_json_response(404, {"ok": False, "error": "Nessuna anagrafica in memoria"})
            return

        body = DEFAULT_REGISTRY_INPUT_PATH.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header(
            "Content-Disposition",
            f'inline; filename="{DEFAULT_REGISTRY_INPUT_PATH.name}"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_registry_cache_file_response(self) -> None:
        if not DEFAULT_REGISTRY_CACHE_PATH.exists():
            self._send_json_response(404, {"ok": False, "error": "Nessuna anagrafica modificata in memoria"})
            return

        body = DEFAULT_REGISTRY_CACHE_PATH.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            'attachment; filename="anagrafica_clienti_modificata.json"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_registry_export_file_response(self) -> None:
        if not DEFAULT_REGISTRY_CACHE_PATH.exists():
            self._send_json_response(404, {"ok": False, "error": "Nessuna anagrafica modificata in memoria"})
            return

        cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
        save_registry_cache_excel(cache, REGISTRY_EXPORT_XLSX_PATH)
        body = REGISTRY_EXPORT_XLSX_PATH.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header(
            "Content-Disposition",
            'attachment; filename="anagrafica_estrattore_scan.xlsx"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_invoice_registry_export_file_response(self) -> None:
        output_path = save_invoice_registry_excel(
            runtime_paths=RUNTIME_PATHS,
        )
        body = output_path.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header(
            "Content-Disposition",
            'attachment; filename="registro_fatture.xlsx"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_registry_cleaning_report_file_response(self) -> None:
        if not REGISTRY_DUPLICATE_REPORT_XLSX_PATH.exists():
            self._send_json_response(
                404,
                {"ok": False, "error": "Nessun report pulizia disponibile"},
            )
            return

        body = REGISTRY_DUPLICATE_REPORT_XLSX_PATH.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header(
            "Content-Disposition",
            'attachment; filename="report_pulizia_anagrafica.xlsx"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def _warning_counts(records: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for warning in record.warnings or []:
            counts[warning] = counts.get(warning, 0) + 1
    return counts


def ensure_registry_runtime_ready() -> None:
    global LEGACY_REGISTRY_MIGRATIONS
    global INCOMPLETE_PROCESSING_CLEANUPS
    global _REGISTRY_RUNTIME_READY

    if _REGISTRY_RUNTIME_READY:
        return

    RUNTIME_PATHS.ensure_directories()
    LEGACY_REGISTRY_MIGRATIONS = migrate_legacy_registry_files_if_needed(RUNTIME_PATHS)
    _load_processing_jobs_from_disk()
    INCOMPLETE_PROCESSING_CLEANUPS = [
        str(path)
        for path in cleanup_incomplete_processing_runs(
            RUNTIME_PATHS,
            preserve_process_ids=_resumable_processing_process_ids(),
        )
    ]
    _REGISTRY_RUNTIME_READY = True
    _start_next_processing_job_if_idle()


def _registry_status_payload() -> dict[str, Any]:
    if not DEFAULT_REGISTRY_INPUT_PATH.exists():
        return {"ok": True, "has_file": False}

    stat = DEFAULT_REGISTRY_INPUT_PATH.stat()
    payload: dict[str, Any] = {
        "ok": True,
        "has_file": True,
        "file_name": DEFAULT_REGISTRY_INPUT_PATH.name,
        "file_size": stat.st_size,
        "file_url": "/registry/file",
    }

    if DEFAULT_REGISTRY_CACHE_PATH.exists():
        try:
            cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
            payload["record_count"] = len(cache.records)
            payload["json_file_url"] = "/registry/cache.json"
            payload["json_file_name"] = "anagrafica_clienti_modificata.json"
            payload["export_file_url"] = "/registry/export.xlsx"
            payload["export_file_name"] = "anagrafica_estrattore_scan.xlsx"
        except Exception as error:
            payload["cache_error"] = str(error)

    return payload


def _runtime_paths_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "paths": RUNTIME_PATHS.as_dict(),
        "registry_files": {
            "input": str(DEFAULT_REGISTRY_INPUT_PATH),
            "cache": str(DEFAULT_REGISTRY_CACHE_PATH),
            "duplicate_report_json": str(DEFAULT_REGISTRY_DUPLICATE_REPORT_PATH),
            "duplicate_report_xlsx": str(REGISTRY_DUPLICATE_REPORT_XLSX_PATH),
            "export_xlsx": str(REGISTRY_EXPORT_XLSX_PATH),
        },
        "legacy_registry_migrations": [
            {"source": str(source), "target": str(target)}
            for source, target in LEGACY_REGISTRY_MIGRATIONS
        ],
        "incomplete_processing_cleanups": INCOMPLETE_PROCESSING_CLEANUPS,
    }


def _batches_payload() -> dict[str, Any]:
    batches = list_imported_batches(RUNTIME_PATHS)
    return {
        "ok": True,
        "batch_count": len(batches),
        "batches": [_batch_payload(batch) for batch in batches],
    }


def _batch_import_payload(batch: ImportedBatchIndex) -> dict[str, Any]:
    return {
        "ok": True,
        "batch": _batch_payload(batch),
        "batch_count": len(list_imported_batches(RUNTIME_PATHS)),
    }


def _batch_payload(batch: ImportedBatchIndex) -> dict[str, Any]:
    return {
        "batch_id": batch.batch_id,
        "label": batch.label,
        "source_label": batch.source_label,
        "created_at": batch.created_at,
        "document_count": batch.document_count,
        "documents": [
            {
                "document_id": document.document_id,
                "original_file_name": document.original_file_name,
                "stored_file_name": document.stored_file_name,
                "relative_path": document.relative_path,
                "size_bytes": document.size_bytes,
                "status": document.status,
            }
            for document in batch.documents
        ],
    }


def _delete_imported_batch(path: str) -> dict[str, Any]:
    batch_id = path.rsplit("/", 1)[-1]
    active_job = _processing_job_for_batch(batch_id)
    if active_job and active_job.get("status") in {"queued", "processing", "paused"}:
        raise ValueError("Il gruppo e in lavorazione: annulla prima il processamento")

    delete_imported_batch(batch_id, runtime_paths=RUNTIME_PATHS)
    return _batches_payload()


def _new_folder_import_source(folder_path: Path) -> dict[str, Any]:
    if not folder_path.is_dir():
        raise ValueError("Cartella non trovata")

    pdf_paths = _pdf_paths_in_folder(folder_path)
    if not pdf_paths:
        raise ValueError("La cartella selezionata non contiene PDF")

    source = {
        "source_id": f"source_{uuid4().hex}",
        "kind": "folder",
        "label": folder_path.name,
        "document_count": len(pdf_paths),
        "duplicate_key": _duplicate_key([folder_path]),
        "folder_path": str(folder_path),
        "file_paths": [str(path) for path in pdf_paths],
    }
    _store_pending_import_source(source)
    return source


def _new_files_import_source(file_paths: list[Path]) -> dict[str, Any]:
    pdf_paths = [path for path in file_paths if path.is_file() and path.suffix.lower() == ".pdf"]
    if not pdf_paths:
        raise ValueError("Seleziona almeno un PDF valido")
    if len(pdf_paths) != len(file_paths):
        raise ValueError("Puoi selezionare solo file PDF")

    label = pdf_paths[0].name if len(pdf_paths) == 1 else f"{len(pdf_paths)} PDF selezionati"
    source = {
        "source_id": f"source_{uuid4().hex}",
        "kind": "files",
        "label": label,
        "document_count": len(pdf_paths),
        "duplicate_key": _duplicate_key(pdf_paths),
        "file_paths": [str(path) for path in pdf_paths],
    }
    _store_pending_import_source(source)
    return source


def _store_pending_import_source(source: dict[str, Any]) -> None:
    with PENDING_IMPORT_SOURCES_LOCK:
        PENDING_IMPORT_SOURCES[str(source["source_id"])] = source


def _pending_import_source_response(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "source": _pending_import_source_payload(source),
    }


def _pending_import_source_payload(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source["source_id"],
        "kind": source["kind"],
        "label": source["label"],
        "document_count": source["document_count"],
        "duplicate_key": source["duplicate_key"],
    }


def _import_pending_sources(payload: dict[str, Any]) -> ImportedBatchIndex:
    raw_source_ids = payload.get("source_ids")
    if not isinstance(raw_source_ids, list) or not raw_source_ids:
        raise ValueError("Seleziona almeno una sorgente da importare")

    source_ids = [str(source_id) for source_id in raw_source_ids]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Sono presenti sorgenti duplicate")

    with PENDING_IMPORT_SOURCES_LOCK:
        sources = [PENDING_IMPORT_SOURCES.get(source_id) for source_id in source_ids]

    if any(source is None for source in sources):
        raise ValueError("Una sorgente selezionata non e piu disponibile")

    typed_sources = [source for source in sources if source is not None]
    duplicate_keys = [str(source.get("duplicate_key") or "") for source in typed_sources]
    if len(duplicate_keys) != len(set(duplicate_keys)):
        raise ValueError("Sono presenti sorgenti duplicate")

    pdf_sources: list[PdfBatchSource] = []
    for source in typed_sources:
        kind = str(source.get("kind") or "")
        if kind == "folder":
            folder_path = Path(str(source.get("folder_path") or ""))
            for file_path in _pdf_paths_in_folder(folder_path):
                pdf_sources.append(
                    PdfBatchSource(
                        path=file_path,
                        original_file_name=f"{folder_path.name}/{file_path.relative_to(folder_path).as_posix()}",
                    )
                )
            continue

        if kind == "files":
            for raw_file_path in source.get("file_paths") or []:
                file_path = Path(str(raw_file_path))
                pdf_sources.append(
                    PdfBatchSource(
                        path=file_path,
                        original_file_name=file_path.name,
                    )
                )
            continue

        raise ValueError("Tipo sorgente non valido")

    if not pdf_sources:
        raise ValueError("Le sorgenti selezionate non contengono PDF")

    source_label = (
        str(typed_sources[0].get("label") or "")
        if len(typed_sources) == 1
        else f"{len(typed_sources)} sorgenti"
    )
    batch = import_pdf_batch(
        pdf_sources,
        runtime_paths=RUNTIME_PATHS,
        source_label=source_label,
    )

    with PENDING_IMPORT_SOURCES_LOCK:
        for source_id in source_ids:
            PENDING_IMPORT_SOURCES.pop(source_id, None)

    return batch


def _discard_pending_sources(payload: dict[str, Any]) -> None:
    raw_source_ids = payload.get("source_ids")
    if not isinstance(raw_source_ids, list):
        return

    with PENDING_IMPORT_SOURCES_LOCK:
        for source_id in raw_source_ids:
            PENDING_IMPORT_SOURCES.pop(str(source_id), None)


def _pdf_paths_in_folder(folder_path: Path) -> list[Path]:
    if not folder_path.is_dir():
        raise ValueError("Cartella non trovata")
    return sorted(
        (
            path
            for path in folder_path.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: path.relative_to(folder_path).as_posix().lower(),
    )


def _duplicate_key(paths: list[Path]) -> str:
    normalized_paths = [
        str(path.resolve()).casefold()
        for path in paths
    ]
    digest = hashlib.sha256("\n".join(sorted(normalized_paths)).encode("utf-8"))
    return digest.hexdigest()


def _review_batches_payload() -> dict[str, Any]:
    review_batches = list_review_batches(RUNTIME_PATHS)
    return {
        "ok": True,
        "batches": [_review_batch_payload(batch) for batch in review_batches],
    }


def _review_batch_payload(batch: ReviewBatchRef) -> dict[str, Any]:
    return {
        "id": batch.id,
        "label": batch.label,
        "base_path": batch.base_path,
        "invoice_count": batch.invoice_count,
    }


def _processing_jobs_file_path() -> Path:
    return RUNTIME_PATHS.config_dir / PROCESSING_JOBS_FILE_NAME


def _load_processing_jobs_from_disk() -> None:
    global PROCESSING_ACTIVE_JOB_ID
    jobs_file = _processing_jobs_file_path()
    if not jobs_file.is_file():
        return

    try:
        payload = json.loads(jobs_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    loaded_jobs = payload.get("jobs", [])
    if not isinstance(loaded_jobs, list):
        return

    with PROCESSING_JOBS_LOCK:
        PROCESSING_JOBS.clear()
        PROCESSING_JOB_QUEUE.clear()
        PROCESSING_ACTIVE_JOB_ID = None

        for raw_job in loaded_jobs:
            if not isinstance(raw_job, dict):
                continue
            job_id = str(raw_job.get("job_id") or "")
            batch_id = str(raw_job.get("batch_id") or "")
            if not job_id or not batch_id:
                continue

            job = dict(raw_job)
            status = str(job.get("status") or "")
            if status == "processing":
                if job.get("process_id"):
                    job["status"] = "queued"
                    job["message"] = "Ripresa in coda dopo chiusura dell'app"
                    job["cancel_requested"] = False
                    job["pause_requested"] = False
                    PROCESSING_JOB_QUEUE.append(job_id)
                else:
                    job["status"] = "cancelled"
                    job["message"] = "Processamento interrotto dalla chiusura dell'app"
                    job["cancel_requested"] = True
                    job["pause_requested"] = False
                job["estimated_remaining_seconds"] = None
            elif status == "queued":
                PROCESSING_JOB_QUEUE.append(job_id)
            elif status == "paused":
                job["cancel_requested"] = False
                job["pause_requested"] = False
                job["estimated_remaining_seconds"] = None

            job.pop("started_at_monotonic", None)
            PROCESSING_JOBS[job_id] = job

        _persist_processing_jobs_locked()


def _persist_processing_jobs() -> None:
    with PROCESSING_JOBS_LOCK:
        _persist_processing_jobs_locked()


def _persist_processing_jobs_locked() -> None:
    jobs_file = _processing_jobs_file_path()
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = jobs_file.with_name(f".{jobs_file.name}.writing")
    serializable_jobs = []
    for job in PROCESSING_JOBS.values():
        serializable_job = {
            key: value
            for key, value in job.items()
            if key != "started_at_monotonic"
        }
        serializable_jobs.append(serializable_job)

    temporary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_job_id": PROCESSING_ACTIVE_JOB_ID,
                "queued_job_ids": list(PROCESSING_JOB_QUEUE),
                "jobs": sorted(
                    serializable_jobs,
                    key=lambda job: str(job.get("job_id") or ""),
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(jobs_file)


def _resumable_processing_process_ids() -> set[str]:
    with PROCESSING_JOBS_LOCK:
        return {
            str(job.get("process_id"))
            for job in PROCESSING_JOBS.values()
            if str(job.get("status") or "") in {"queued", "processing", "paused"}
            and job.get("process_id")
        }


def _start_processing_job(payload: dict[str, Any]) -> dict[str, Any]:
    batch_id = str(payload.get("batch_id") or "").strip()
    if not batch_id:
        raise ValueError("Batch da processare mancante")

    batch_index = load_batch_index(RUNTIME_PATHS.batches_dir / batch_id)
    document_count = batch_index.document_count
    job_id = f"job_{len(PROCESSING_JOBS) + 1:06d}"
    process_id = _new_processing_process_id(job_id)
    started_at = time.monotonic()
    with PROCESSING_JOBS_LOCK:
        PROCESSING_JOBS[job_id] = {
            "ok": True,
            "job_id": job_id,
            "process_id": process_id,
            "batch_id": batch_id,
            "status": "queued",
            "message": "Processamento in coda",
            "progress_current": 0,
            "progress_total": document_count,
            "started_at_monotonic": started_at,
            "elapsed_seconds": 0,
            "estimated_remaining_seconds": None,
            "cancel_requested": False,
            "pause_requested": False,
        }
        PROCESSING_JOB_QUEUE.append(job_id)
        _persist_processing_jobs_locked()

    _start_next_processing_job_if_idle()
    return _processing_job(job_id)


def _reprocess_archived_source_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    archived_source_pdf_path = str(payload.get("archived_source_pdf_path") or "").strip()
    source_file_name = Path(str(payload.get("source_file_name") or "")).name
    if not archived_source_pdf_path:
        raise ValueError("PDF origine da riprocessare mancante")

    source_path = _archived_source_pdf_path(archived_source_pdf_path)
    batch = import_pdf_batch(
        [
            PdfBatchSource(
                path=source_path,
                original_file_name=source_file_name or source_path.name,
            )
        ],
        runtime_paths=RUNTIME_PATHS,
        source_label=f"Riprocessa {source_file_name or source_path.name}",
    )
    return _start_processing_job({"batch_id": batch.batch_id})


def _archived_source_pdf_path(value: str) -> Path:
    normalized_value = value.replace("\\", "/").strip()
    runtime_prefix = "/runtime/"
    if normalized_value.startswith(runtime_prefix):
        normalized_value = normalized_value.removeprefix(runtime_prefix)
    normalized_value = normalized_value.lstrip("/")
    if not normalized_value:
        raise ValueError("PDF origine non valido")

    source_path = (RUNTIME_PATHS.app_data_dir / normalized_value).resolve()
    archive_sources_dir = RUNTIME_PATHS.archive_sources_dir.resolve()
    if source_path != archive_sources_dir and archive_sources_dir not in source_path.parents:
        raise ValueError("PDF origine fuori dall'archivio")
    if source_path.suffix.lower() != ".pdf" or not source_path.is_file():
        raise ValueError("PDF origine non trovato")
    return source_path


def _start_next_processing_job_if_idle() -> None:
    global PROCESSING_ACTIVE_JOB_ID

    with PROCESSING_JOBS_LOCK:
        if PROCESSING_ACTIVE_JOB_ID is not None or not PROCESSING_JOB_QUEUE:
            return
        job_id = PROCESSING_JOB_QUEUE.pop(0)
        job = PROCESSING_JOBS.get(job_id)
        if job is None or job.get("status") != "queued":
            return
        PROCESSING_ACTIVE_JOB_ID = job_id
        batch_id = str(job.get("batch_id") or "")
        job["started_at_monotonic"] = time.monotonic()
        _persist_processing_jobs_locked()

    thread = threading.Thread(
        target=_run_processing_job,
        args=(job_id, batch_id),
        daemon=True,
    )
    thread.start()


def _run_processing_job(job_id: str, batch_id: str) -> None:
    try:
        job = _processing_job(job_id)
        process_id = str(job.get("process_id") or _new_processing_process_id(job_id))
        progress_total = int(job.get("progress_total") or 0)
        estimate_progress_baseline = _completed_document_count_for_process(process_id)
        _update_processing_job(
            job_id,
            process_id=process_id,
            status="processing",
            message="Processamento OCR in corso",
            elapsed_seconds=0,
            estimate_progress_baseline=estimate_progress_baseline,
        )
        run_index = process_imported_batch(
            batch_id,
            runtime_paths=RUNTIME_PATHS,
            process_id=process_id,
            resume_existing=True,
            progress_callback=lambda current, total, file_name: (
                _update_processing_job(
                    job_id,
                    progress_current=current,
                    progress_total=total,
                    message="Processamento OCR in corso",
                    current_file=file_name,
                    **_processing_time_estimate(job_id, current, total),
                )
            ),
            should_cancel=lambda: bool(_processing_job(job_id).get("cancel_requested")),
            should_pause=lambda: bool(_processing_job(job_id).get("pause_requested")),
        )
        _update_processing_job(
            job_id,
            status="completed",
            message="Processamento completato",
            **_processing_time_estimate(job_id, progress_total, progress_total),
            result=_processing_run_payload(run_index),
        )
    except ProcessingCancelled as error:
        job = _processing_job(job_id)
        _delete_job_process_dir(job)
        _update_processing_job(
            job_id,
            status="cancelled",
            message="Processamento annullato" if job else str(error),
            **_processing_time_estimate(
                job_id,
                int(job.get("progress_current") or 0) if job else 0,
                int(job.get("progress_total") or 0) if job else 0,
            ),
        )
    except ProcessingPaused as error:
        job = _processing_job(job_id)
        _update_processing_job(
            job_id,
            status="paused",
            message="Processamento in pausa" if job else str(error),
            pause_requested=False,
            **_processing_time_estimate(
                job_id,
                int(job.get("progress_current") or 0) if job else 0,
                int(job.get("progress_total") or 0) if job else 0,
            ),
        )
    except Exception as error:
        _update_processing_job(
            job_id,
            status="error",
            message=str(error),
        )
    finally:
        _finish_active_processing_job(job_id)


def _finish_active_processing_job(job_id: str) -> None:
    global PROCESSING_ACTIVE_JOB_ID

    with PROCESSING_JOBS_LOCK:
        if PROCESSING_ACTIVE_JOB_ID == job_id:
            PROCESSING_ACTIVE_JOB_ID = None
        _persist_processing_jobs_locked()
    _start_next_processing_job_if_idle()


def _new_processing_process_id(job_id: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"process_{timestamp}_{job_id}"


def _processing_run_payload(run_index: ProcessingRunIndex) -> dict[str, Any]:
    return {
        "process_id": run_index.process_id,
        "source_batch_id": run_index.source_batch_id,
        "status": run_index.status,
        "document_count": run_index.document_count,
        "invoice_count": run_index.invoice_count,
        "review_batch_count": run_index.review_batch_count,
        "review_batches": [
            _review_batch_payload(batch) for batch in run_index.review_batches
        ],
        "errors": run_index.errors,
    }


def _processing_time_estimate(
    job_id: str,
    progress_current: int,
    progress_total: int,
) -> dict[str, int | None]:
    job = _processing_job(job_id)
    started_at = job.get("started_at_monotonic")
    if not isinstance(started_at, (int, float)):
        return {
            "elapsed_seconds": 0,
            "estimated_remaining_seconds": None,
        }

    elapsed_seconds = max(0, int(time.monotonic() - started_at))
    estimate_progress_baseline = int(job.get("estimate_progress_baseline") or 0)
    session_processed_file_count = max(0, progress_current - estimate_progress_baseline)
    if session_processed_file_count < 3 or progress_total <= progress_current:
        return {
            "elapsed_seconds": elapsed_seconds,
            "estimated_remaining_seconds": None,
        }

    average_seconds_per_file = elapsed_seconds / session_processed_file_count
    remaining_file_count = progress_total - progress_current
    return {
        "elapsed_seconds": elapsed_seconds,
        "estimated_remaining_seconds": max(
            0,
            int(average_seconds_per_file * remaining_file_count),
        ),
    }


def _completed_document_count_for_process(process_id: str) -> int:
    documents_dir = RUNTIME_PATHS.manifests_dir / process_id / "documents"
    if not documents_dir.is_dir():
        return 0

    return sum(
        1
        for document_dir in documents_dir.iterdir()
        if document_dir.is_dir() and is_document_processing_complete(document_dir)
    )


def _update_processing_job(job_id: str, **updates: Any) -> None:
    with PROCESSING_JOBS_LOCK:
        current_job = PROCESSING_JOBS.get(job_id)
        if current_job is None:
            return
        PROCESSING_JOBS[job_id] = {**current_job, **updates}
        _persist_processing_jobs_locked()


def _processing_job_payload(path: str) -> dict[str, Any]:
    job_id = path.rsplit("/", 1)[-1]
    job = _processing_job(job_id)
    if not job:
        raise ValueError("Lavorazione non trovata")
    return job


def _processing_jobs_payload() -> dict[str, Any]:
    with PROCESSING_JOBS_LOCK:
        jobs = [dict(job) for job in PROCESSING_JOBS.values()]
    return {
        "ok": True,
        "jobs": sorted(jobs, key=lambda job: str(job.get("job_id") or "")),
        "active_job_id": PROCESSING_ACTIVE_JOB_ID,
        "queued_job_ids": list(PROCESSING_JOB_QUEUE),
    }


def _processing_job_for_batch(batch_id: str) -> dict[str, Any]:
    with PROCESSING_JOBS_LOCK:
        for job in PROCESSING_JOBS.values():
            if str(job.get("batch_id") or "") == batch_id:
                return dict(job)
    return {}


def _cancel_processing_job(path: str) -> dict[str, Any]:
    job_id = path.removesuffix("/cancel").rsplit("/", 1)[-1]
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        if job is None:
            raise ValueError("Lavorazione non trovata")
        if job.get("status") in TERMINAL_PROCESSING_JOB_STATUSES:
            return dict(job)
        if job.get("status") in {"queued", "paused", "removed"}:
            if job_id in PROCESSING_JOB_QUEUE:
                PROCESSING_JOB_QUEUE.remove(job_id)
            _delete_job_process_dir(job)
            PROCESSING_JOBS[job_id] = {
                **job,
                "status": "cancelled",
                "cancel_requested": True,
                "pause_requested": False,
                "message": "Processamento annullato",
            }
            _persist_processing_jobs_locked()
            return dict(PROCESSING_JOBS[job_id])
        PROCESSING_JOBS[job_id] = {
            **job,
            "cancel_requested": True,
            "pause_requested": False,
            "message": "Annullamento richiesto",
        }
        _persist_processing_jobs_locked()
        return dict(PROCESSING_JOBS[job_id])


def _pause_processing_job(path: str) -> dict[str, Any]:
    job_id = path.removesuffix("/pause").rsplit("/", 1)[-1]
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        if job is None:
            raise ValueError("Lavorazione non trovata")
        if job.get("status") in TERMINAL_PROCESSING_JOB_STATUSES or job.get("status") == "paused":
            return dict(job)
        if job.get("status") == "queued":
            if job_id in PROCESSING_JOB_QUEUE:
                PROCESSING_JOB_QUEUE.remove(job_id)
            PROCESSING_JOBS[job_id] = {
                **job,
                "status": "paused",
                "pause_requested": False,
                "cancel_requested": False,
                "message": "Processamento in pausa",
                "estimated_remaining_seconds": None,
            }
            _persist_processing_jobs_locked()
            return dict(PROCESSING_JOBS[job_id])

        PROCESSING_JOBS[job_id] = {
            **job,
            "pause_requested": True,
            "cancel_requested": False,
            "message": "Pausa richiesta",
        }
        _persist_processing_jobs_locked()
        return dict(PROCESSING_JOBS[job_id])


def _resume_processing_job(path: str) -> dict[str, Any]:
    job_id = path.removesuffix("/resume").rsplit("/", 1)[-1]
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        if job is None:
            raise ValueError("Lavorazione non trovata")
        if job.get("status") != "paused":
            return dict(job)
        PROCESSING_JOBS[job_id] = {
            **job,
            "status": "queued",
            "message": "Processamento rimesso in coda",
            "cancel_requested": False,
            "pause_requested": False,
            "estimated_remaining_seconds": None,
        }
        if job_id not in PROCESSING_JOB_QUEUE:
            PROCESSING_JOB_QUEUE.append(job_id)
        _persist_processing_jobs_locked()

    _start_next_processing_job_if_idle()
    return _processing_job(job_id)


def _remove_processing_job(path: str) -> dict[str, Any]:
    job_id = path.removesuffix("/remove").rsplit("/", 1)[-1]
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        if job is None:
            raise ValueError("Lavorazione non trovata")
        if job.get("status") == "processing":
            raise ValueError("Metti in pausa o annulla la lavorazione attiva")
        if job_id in PROCESSING_JOB_QUEUE:
            PROCESSING_JOB_QUEUE.remove(job_id)
        _delete_job_process_dir(job)
        PROCESSING_JOBS[job_id] = {
            **job,
            "status": "removed",
            "message": "Lavorazione rimossa dalla coda",
            "cancel_requested": False,
            "pause_requested": False,
            "estimated_remaining_seconds": None,
        }
        _persist_processing_jobs_locked()
        return dict(PROCESSING_JOBS[job_id])


def _delete_job_process_dir(job: dict[str, Any] | None) -> None:
    if not job:
        return
    process_id = str(job.get("process_id") or "")
    if not process_id:
        return
    shutil.rmtree(RUNTIME_PATHS.manifests_dir / process_id, ignore_errors=True)


def _processing_job(job_id: str) -> dict[str, Any]:
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        return dict(job) if job else {}


def _pick_windows_folder() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as error:
        raise RuntimeError(
            "Selettore cartella Windows non disponibile in questo ambiente"
        ) from error

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected_folder = filedialog.askdirectory(
            parent=root,
            title="Seleziona cartella PDF",
            mustexist=True,
        )
    finally:
        root.destroy()

    if not selected_folder:
        return None
    return Path(selected_folder)


def _pick_windows_pdf_files() -> list[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as error:
        raise RuntimeError(
            "Selettore PDF Windows non disponibile in questo ambiente"
        ) from error

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected_files = filedialog.askopenfilenames(
            parent=root,
            title="Seleziona PDF",
            filetypes=[("PDF", "*.pdf"), ("Tutti i file", "*.*")],
        )
    finally:
        root.destroy()

    return [Path(path) for path in selected_files]


def _registry_records_payload(cache: RegistryCache | None = None) -> dict[str, Any]:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        return {"ok": True, "records": [], "record_count": 0}

    cache = cache or load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    return {
        "ok": True,
        "record_count": len(cache.records),
        "records": [
            {
                "registry_id": record.registry_id,
                "source_row": record.source_row,
                "codice_anagrafica": record.codice_anagrafica,
                "nominativo": record.normalized_name or record.nominativo,
                "codice_fiscale": record.codice_fiscale,
                "partita_iva": record.partita_iva,
                "tipologia": record.tipologia,
                "warnings": [_registry_warning_label(warning) for warning in record.warnings or []],
            }
            for record in cache.records
        ],
    }


def _save_registry_records_payload(payload: dict[str, Any]) -> RegistryCache:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        raise ValueError("Anagrafica non caricata")

    existing_cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    records_payload = payload.get("records")
    if not isinstance(records_payload, list):
        raise ValueError("Formato record anagrafica non valido")

    existing_by_key = {
        (record.registry_id, record.source_row): record
        for record in existing_cache.records
    }
    updated_records: list[RegistryRecord] = []
    next_source_row = max((record.source_row for record in existing_cache.records), default=0) + 1

    for item in records_payload:
        if not isinstance(item, dict):
            raise ValueError("Record anagrafica non valido")

        registry_id = str(item.get("registry_id") or "").strip()
        source_row = int(item.get("source_row") or 0)
        existing_record = existing_by_key.get((registry_id, source_row))
        is_new_record = existing_record is None and (
            source_row <= 0 or registry_id.startswith("new_")
        )
        if existing_record is None and not is_new_record:
            raise ValueError("Record anagrafica non riconosciuto")
        if is_new_record:
            source_row = next_source_row
            next_source_row += 1
            registry_id = f"manual_{source_row:06d}"

        nominativo = str(item.get("nominativo") or "").strip()
        codice_anagrafica_raw = str(item.get("codice_anagrafica") or "").strip()
        codice_fiscale_raw = str(item.get("codice_fiscale") or "").strip()
        partita_iva_raw = str(item.get("partita_iva") or "").strip()
        tipologia = str(item.get("tipologia") or "").strip()
        normalized_name = normalize_registry_name(nominativo)
        codice_anagrafica = (
            codice_anagrafica_raw
            or extract_registry_code(nominativo)
            or (existing_record.codice_anagrafica if existing_record else None)
        )
        extracted_codice_fiscale, extracted_partita_iva = (
            extract_registry_fiscal_identifiers(nominativo)
        )
        codice_fiscale = (
            _normalized_edit_tax_code(codice_fiscale_raw)
            or extracted_codice_fiscale
        )
        partita_iva = (
            _normalized_edit_vat_number(partita_iva_raw)
            or extracted_partita_iva
        )

        updated_records.append(
            RegistryRecord(
                registry_id=registry_id,
                source_row=source_row,
                codice_anagrafica=codice_anagrafica,
                nominativo=nominativo,
                normalized_name=normalized_name,
                codice_fiscale_raw=codice_fiscale_raw,
                codice_fiscale=codice_fiscale,
                partita_iva_raw=partita_iva_raw,
                partita_iva=partita_iva,
                tipologia=tipologia,
                ultima_modifica=existing_record.ultima_modifica if existing_record else None,
                utente=existing_record.utente if existing_record else None,
                warnings=_registry_record_warnings(
                    normalized_name=normalized_name,
                    codice_fiscale_raw=codice_fiscale_raw,
                    codice_fiscale=codice_fiscale,
                    partita_iva_raw=partita_iva_raw,
                    partita_iva=partita_iva,
                ),
            )
        )

    cache = RegistryCache(
        schema_version=existing_cache.schema_version,
        source_file=existing_cache.source_file,
        records=updated_records,
    )
    save_registry_cache(cache, DEFAULT_REGISTRY_CACHE_PATH)
    return cache


def _clean_registry_duplicates(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        raise ValueError("Anagrafica non caricata")

    selected_merge_group_keys = None
    if payload is not None and "selected_merge_group_keys" in payload:
        raw_keys = payload.get("selected_merge_group_keys")
        if not isinstance(raw_keys, list) or not all(isinstance(key, str) for key in raw_keys):
            raise ValueError("Selezione pulizia non valida")
        selected_merge_group_keys = set(raw_keys)

    existing_cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    result = clean_registry_cache_duplicates(
        existing_cache,
        selected_merge_group_keys=selected_merge_group_keys,
    )
    save_registry_cache(result.cache, DEFAULT_REGISTRY_CACHE_PATH)
    save_registry_duplicate_report_excel(
        result.report,
        REGISTRY_DUPLICATE_REPORT_XLSX_PATH,
    )
    payload = _registry_records_payload(result.cache)
    payload["cleaning_report"] = result.report
    payload["cleaning_report_file_url"] = "/registry/cleaning-report.xlsx"
    return payload


def _preview_registry_duplicates() -> dict[str, Any]:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        raise ValueError("Anagrafica non caricata")

    existing_cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    result = preview_registry_cache_duplicates(existing_cache)
    save_registry_duplicate_report_excel(
        result.report,
        REGISTRY_DUPLICATE_REPORT_XLSX_PATH,
    )
    return {
        "ok": True,
        "cleaning_report": result.report,
        "cleaning_report_file_url": "/registry/cleaning-report.xlsx",
    }


def _delete_registry() -> None:
    DEFAULT_REGISTRY_INPUT_PATH.unlink(missing_ok=True)
    DEFAULT_REGISTRY_CACHE_PATH.unlink(missing_ok=True)
    DEFAULT_REGISTRY_DUPLICATE_REPORT_PATH.unlink(missing_ok=True)
    REGISTRY_DUPLICATE_REPORT_XLSX_PATH.unlink(missing_ok=True)


def _normalized_edit_tax_code(value: str) -> str | None:
    normalized = normalize_italian_tax_code(value)
    if normalized in {None, "0", "00000000000", "000000000000"}:
        return None
    return normalized


def _normalized_edit_vat_number(value: str) -> str | None:
    normalized = normalize_italian_vat_number(value)
    if normalized in {None, "00000000000"}:
        return None
    return normalized


def _registry_record_warnings(
    normalized_name: str,
    codice_fiscale_raw: str,
    codice_fiscale: str | None,
    partita_iva_raw: str,
    partita_iva: str | None,
) -> list[str]:
    warnings = []
    if not normalized_name:
        warnings.append("missing_normalized_name")
    if codice_fiscale_raw and codice_fiscale is None:
        warnings.append("invalid_codice_fiscale")
    if partita_iva_raw and partita_iva is None:
        warnings.append("invalid_partita_iva")
    return warnings


def _registry_warning_label(warning: str) -> str:
    labels = {
        "missing_normalized_name": "Nominativo mancante o non leggibile",
        "invalid_codice_fiscale": "Codice fiscale non valido",
        "invalid_partita_iva": "Partita IVA non valida",
        "merged_duplicate_fiscal_identifier": "Duplicato fiscale unito automaticamente",
        "conflicting_duplicate_fiscal_identifier": "Duplicato fiscale da controllare",
    }
    return labels.get(warning, warning.replace("_", " "))


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), RegistryApiHandler)
    print(f"Registry API attiva su http://{HOST}:{PORT}")
    print("Endpoint import: POST /registry/import")
    server.serve_forever()


if __name__ == "__main__":
    main()
