from __future__ import annotations

import json
import sys
import shutil
import tempfile
import threading
import time
import hashlib
from dataclasses import replace
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts.local_http import LocalHTTPServer, LocalRequestHandler

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
    delete_invoice_archive,
    discard_reviewed_invoice,
    load_invoice_registry,
    purge_discarded_invoice_records,
    save_invoice_registry_excel,
    save_invoice_registry_records,
    save_reviewed_invoice,
)
from estrattore.feedback_collection import (
    reconcile_feedback_operations,
    record_capture_issue,
)
from estrattore.feedback_export import export_feedback_to_desktop
from estrattore.invoice_export import delete_export_history, export_invoices
from estrattore.source_archive_storage import (
    archived_source_path,
    delete_archived_sources,
    list_archived_sources,
    migrate_legacy_archived_sources,
    promote_batch_sources,
)
from estrattore.processing_storage import (
    ProcessingCancelled,
    ProcessingInterrupted,
    ProcessingRunIndex,
    ReviewBatchRef,
    clear_all_review_batches,
    cleanup_incomplete_processing_runs,
    delete_review_invoice_drafts,
    is_document_processing_complete,
    list_review_invoice_drafts,
    list_review_batches,
    mutate_review_invoice_segmentation,
    process_imported_batch,
    recover_missing_review_batches,
    recover_review_mutations,
    retire_completed_review_batches,
    review_draft_id_for_invoice_payload,
    save_review_invoice_draft,
)
from estrattore.runtime_cleanup import cleanup_stale_atomic_write_files
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
PROCESSING_SHUTDOWN_REQUESTED = False
PROCESSING_JOB_IDLE = threading.Event()
PROCESSING_JOB_IDLE.set()
INCOMPLETE_PROCESSING_CLEANUPS: list[str] = []
STALE_ATOMIC_WRITE_CLEANUPS: list[str] = []
COMPLETED_REVIEW_CLEANUPS: list[str] = []
PROCESSING_JOBS_FILE_NAME = "processing_jobs.json"
TERMINAL_PROCESSING_JOB_STATUSES = {"completed", "error", "cancelled", "removed"}
SOURCE_DELETE_BLOCKING_JOB_STATUSES = {
    "queued",
    "processing",
    "cancelling",
    "stopping",
    "finalizing",
}
PENDING_IMPORT_SOURCES: dict[str, dict[str, Any]] = {}
PENDING_IMPORT_SOURCES_LOCK = threading.Lock()


class RegistryApiHandler(LocalRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_empty_response(204)

    def do_GET(self) -> None:
        if self.path.startswith("/runtime/archive/exports/"):
            import re
            match = re.fullmatch(r"/runtime/archive/exports/([0-9a-f]{32})/(fatture_condomini\.(?:xlsx|zip))", self.path)
            target = RUNTIME_PATHS.archive_dir / "exports" / match[1] / match[2] if match else None
            if target is None or not target.is_file():
                self._send_json_response(404, {"ok": False, "error": "Esportazione non trovata."})
                return
            body = target.read_bytes()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/zip" if target.suffix == ".zip" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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

        if self.path == "/viewer/drafts":
            self._send_json_response(
                200,
                {"ok": True, **list_review_invoice_drafts(RUNTIME_PATHS)},
            )
            return

        if self.path == "/processing/jobs":
            self._send_json_response(200, _processing_jobs_payload())
            return

        if self.path == "/archive/sources":
            self._send_json_response(200, _source_archive_payload())
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

        if self.path == "/batches/import-and-process":
            try:
                payload = self._read_json_body()
                batch = _import_pending_sources(payload)
                job = _start_processing_job(
                    {
                        "batch_id": batch.batch_id,
                        "source_kind": "external_upload",
                        "archive_sources": True,
                    }
                )
                self._send_json_response(
                    202,
                    {
                        "ok": True,
                        "batch": _batch_payload(batch),
                        "job": job,
                    },
                )
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

        if self.path == "/archive/invoices/reprocess":
            try:
                payload = self._read_json_body()
                job = _reprocess_archived_invoices(payload)
                self._send_json_response(202, job)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/processing/history/delete":
            try:
                payload = self._read_json_body()
                self._send_json_response(200, _delete_processing_history(payload))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/invoices/export":
            try:
                payload = self._read_json_body()
                result = export_invoices(
                    payload.get("record_ids"), payload.get("mode"), RUNTIME_PATHS,
                    history_id=payload.get("history_id"),
                    include_summary=payload.get("include_summary", False),
                )
                self._send_json_response(200, result)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/invoices/export-history/delete":
            try:
                payload = self._read_json_body()
                self._send_json_response(200, delete_export_history(payload.get("export_id"), RUNTIME_PATHS))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/archive/delete":
            try:
                payload = self._read_json_body()
                if str(payload.get("scope") or "") == "source" and isinstance(
                    payload.get("source_ids"), list
                ):
                    source_records = [
                        archived_source_path(source_id, RUNTIME_PATHS)[0]
                        for source_id in payload["source_ids"]
                    ]
                    source_paths = [source.relative_path for source in source_records]
                    _reject_sources_used_by_pending_reviews(source_records)
                    _reject_sources_used_by_processing_jobs(source_records)
                    try:
                        delete_invoice_archive(
                            scope="source",
                            archived_source_pdf_paths=source_paths,
                            runtime_paths=RUNTIME_PATHS,
                        )
                    except ValueError as error:
                        if "non trovati" not in str(error):
                            raise
                    deleted_count = delete_archived_sources(
                        payload["source_ids"], RUNTIME_PATHS
                    )
                    self._send_json_response(
                        200, {"ok": True, "deleted_source_file_count": deleted_count}
                    )
                    return
                if str(payload.get("scope") or "") in {"invoice", "all"}:
                    invoice_record_ids = [
                        str(value)
                        for value in [
                            *(payload.get("record_ids") or []),
                            payload.get("record_id"),
                        ]
                        if str(value or "").strip()
                    ]
                    if str(payload.get("scope") or "") == "all":
                        invoice_record_ids = [
                            str(record.get("record_id") or "")
                            for record in load_invoice_registry(RUNTIME_PATHS).get(
                                "records", []
                            )
                            if isinstance(record, dict)
                            and record.get("status") in {"saved", "discarded"}
                            and str(record.get("record_id") or "").strip()
                        ]
                    _reject_invoice_records_used_by_processing_jobs(invoice_record_ids)
                    _reject_invoice_records_used_by_pending_reviews(invoice_record_ids)
                result = delete_invoice_archive(
                    scope=str(payload.get("scope") or ""),
                    record_id=payload.get("record_id"),
                    record_ids=payload.get("record_ids"),
                    archived_source_pdf_path=payload.get("archived_source_pdf_path"),
                    archived_source_pdf_paths=payload.get("archived_source_pdf_paths"),
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, {"ok": True, **result})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path.startswith("/processing/jobs/") and self.path.endswith("/cancel"):
            try:
                self._send_json_response(200, _cancel_processing_job(self.path))
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
                payload = self._read_json_body()
                record = save_reviewed_invoice(
                    payload,
                    runtime_paths=RUNTIME_PATHS,
                )
                draft_id = review_draft_id_for_invoice_payload(
                    payload.get("invoice") or {}
                )
                delete_review_invoice_drafts([draft_id], RUNTIME_PATHS)
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
                payload = self._read_json_body()
                record = discard_reviewed_invoice(
                    payload,
                    runtime_paths=RUNTIME_PATHS,
                )
                draft_id = review_draft_id_for_invoice_payload(
                    payload.get("invoice") or {}
                )
                delete_review_invoice_drafts([draft_id], RUNTIME_PATHS)
                self._send_json_response(200, {"ok": True, "record": record})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/viewer/segmentation":
            try:
                result = mutate_review_invoice_segmentation(
                    self._read_json_body(),
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, result)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/viewer/drafts":
            try:
                draft = save_review_invoice_draft(
                    self._read_json_body(),
                    runtime_paths=RUNTIME_PATHS,
                )
                self._send_json_response(200, {"ok": True, "draft": draft})
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/viewer/review-batches/clear":
            try:
                result = clear_all_review_batches(RUNTIME_PATHS)
                self._send_json_response(
                    200,
                    {
                        "ok": True,
                        "deleted_review_batch_count": result.deleted_review_batch_count,
                        "updated_processing_run_count": result.updated_processing_run_count,
                    },
                )
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/feedback/export":
            try:
                result = export_feedback_to_desktop(runtime_paths=RUNTIME_PATHS)
                self._send_json_response(
                    200,
                    {
                        "ok": True,
                        "folder_name": result.folder_name,
                        "output_path": str(result.output_path),
                        "stats": result.stats,
                        "missing_pdf_count": result.missing_pdf_count,
                    },
                )
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

        if self.path == "/viewer/drafts":
            try:
                payload = self._read_json_body()
                draft_ids = payload.get("draft_ids")
                if not isinstance(draft_ids, list):
                    raise ValueError("Bozze da eliminare mancanti")
                deleted_count = delete_review_invoice_drafts(
                    draft_ids,
                    RUNTIME_PATHS,
                )
                self._send_json_response(
                    200,
                    {"ok": True, "deleted_count": deleted_count},
                )
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

def _warning_counts(records: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for warning in record.warnings or []:
            counts[warning] = counts.get(warning, 0) + 1
    return counts


def ensure_registry_runtime_ready() -> None:
    global LEGACY_REGISTRY_MIGRATIONS
    global INCOMPLETE_PROCESSING_CLEANUPS
    global STALE_ATOMIC_WRITE_CLEANUPS
    global COMPLETED_REVIEW_CLEANUPS
    global _REGISTRY_RUNTIME_READY

    if _REGISTRY_RUNTIME_READY:
        return

    RUNTIME_PATHS.ensure_directories()
    STALE_ATOMIC_WRITE_CLEANUPS = [
        str(path)
        for path in cleanup_stale_atomic_write_files(RUNTIME_PATHS)
    ]
    recover_review_mutations(RUNTIME_PATHS)
    LEGACY_REGISTRY_MIGRATIONS = migrate_legacy_registry_files_if_needed(RUNTIME_PATHS)
    _load_processing_jobs_from_disk()
    migrate_legacy_archived_sources(load_invoice_registry(RUNTIME_PATHS), RUNTIME_PATHS)
    purge_discarded_invoice_records(RUNTIME_PATHS)
    try:
        registry = load_invoice_registry(RUNTIME_PATHS)
        reconcile_feedback_operations(
            registry.get("records", []),
            RUNTIME_PATHS,
        )
    except Exception as error:
        record_capture_issue(
            process_id="",
            document_id="",
            stage="registry_edit_startup_recovery",
            error=error,
            runtime_paths=RUNTIME_PATHS,
        )
    _reconcile_finalizing_jobs()
    _reconcile_terminal_job_storage()
    INCOMPLETE_PROCESSING_CLEANUPS = [
        str(path)
        for path in cleanup_incomplete_processing_runs(
            RUNTIME_PATHS,
            preserve_process_ids=_resumable_processing_process_ids(),
        )
    ]
    COMPLETED_REVIEW_CLEANUPS = [
        str(path)
        for path in retire_completed_review_batches(RUNTIME_PATHS)
    ]
    recover_missing_review_batches(RUNTIME_PATHS)
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
        "stale_atomic_write_cleanups": STALE_ATOMIC_WRITE_CLEANUPS,
        "completed_review_cleanups": COMPLETED_REVIEW_CLEANUPS,
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
    if active_job and active_job.get("status") in {
        "queued",
        "processing",
        "cancelling",
    }:
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


def _recover_legacy_processing_job_created_at(job: dict[str, Any]) -> str:
    """Recover the creation timestamp embedded in legacy process identifiers."""
    process_id = str(job.get("process_id") or "")
    parts = process_id.split("_")
    if len(parts) < 3 or parts[0] != "process":
        return ""
    try:
        return datetime.strptime(
            f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S"
        ).isoformat()
    except ValueError:
        return ""


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
    saved_queue = payload.get("queued_job_ids", [])
    if not isinstance(saved_queue, list):
        saved_queue = []
    saved_active_id = str(payload.get("active_job_id") or "")

    with PROCESSING_JOBS_LOCK:
        PROCESSING_JOBS.clear()
        PROCESSING_JOB_QUEUE.clear()
        PROCESSING_ACTIVE_JOB_ID = None
        recovered_queue: list[str] = []
        recovered_active_id = ""

        for raw_job in loaded_jobs:
            if not isinstance(raw_job, dict):
                continue
            job_id = str(raw_job.get("job_id") or "")
            batch_id = str(raw_job.get("batch_id") or "")
            if not job_id or not batch_id:
                continue

            job = dict(raw_job)
            if not str(job.get("created_at") or "").strip():
                recovered_created_at = _recover_legacy_processing_job_created_at(job)
                if recovered_created_at:
                    job["created_at"] = recovered_created_at
            if not isinstance(job.get("files"), list):
                job["files"] = _job_files_from_batch(batch_id)
            status = str(job.get("status") or "")
            if status in {"processing", "stopping"}:
                if job.get("process_id"):
                    job["status"] = "queued"
                    job["message"] = "Ripresa in coda dopo chiusura dell'app"
                    job["cancel_requested"] = False
                    job["interrupt_requested"] = False
                    recovered_queue.append(job_id)
                    if job_id == saved_active_id:
                        recovered_active_id = job_id
                else:
                    job["status"] = "cancelled"
                    job["message"] = "Processamento interrotto dalla chiusura dell'app"
                    job["cancel_requested"] = True
                job["estimated_remaining_seconds"] = None
            elif status in {"pausing", "paused"}:
                _delete_job_process_dir(job)
                job["status"] = "cancelled"
                job["message"] = "Processamento annullato"
                job["cancel_requested"] = True
                job["estimated_remaining_seconds"] = None
            elif status == "cancelling":
                _delete_job_process_dir(job)
                job["status"] = "cancelled"
                job["message"] = "Processamento annullato"
                job["cancel_requested"] = True
                job["estimated_remaining_seconds"] = None
            elif status == "queued":
                job["interrupt_requested"] = False
                recovered_queue.append(job_id)

            job.pop("pause_requested", None)

            job.pop("started_at_monotonic", None)
            PROCESSING_JOBS[job_id] = job

        ordered_ids = [recovered_active_id, *saved_queue, *recovered_queue]
        PROCESSING_JOB_QUEUE.extend(
            job_id for job_id in dict.fromkeys(ordered_ids)
            if job_id in recovered_queue
        )
        _persist_processing_jobs_locked()


def _persist_processing_jobs() -> None:
    with PROCESSING_JOBS_LOCK:
        _persist_processing_jobs_locked()


def _job_files_from_batch(batch_id: str) -> list[dict[str, Any]]:
    try:
        batch = load_batch_index(RUNTIME_PATHS.batches_dir / batch_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    return [
        {
            "document_id": document.document_id,
            "source_id": "",
            "name": document.original_file_name,
            "size_bytes": document.size_bytes,
        }
        for document in batch.documents
    ]


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
            if str(job.get("status") or "")
            in {"queued", "processing"}
            and job.get("process_id")
        }


def _start_processing_job(payload: dict[str, Any]) -> dict[str, Any]:
    batch_id = str(payload.get("batch_id") or "").strip()
    if not batch_id:
        raise ValueError("Batch da processare mancante")

    batch_index = load_batch_index(RUNTIME_PATHS.batches_dir / batch_id)
    document_count = batch_index.document_count
    job_id = f"job_{uuid4().hex[:12]}"
    process_id = _new_processing_process_id(job_id)
    started_at = time.monotonic()
    with PROCESSING_JOBS_LOCK:
        if PROCESSING_SHUTDOWN_REQUESTED:
            raise RuntimeError("L'app si sta chiudendo. Riprova dopo averla riaperta.")
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
            "created_at": datetime.now().replace(microsecond=0).isoformat(),
            "elapsed_seconds": 0,
            "estimated_remaining_seconds": None,
            "cancel_requested": False,
            "files": [
                {
                    "document_id": document.document_id,
                    "source_id": str(
                        (payload.get("source_archive_bindings") or {}).get(
                            document.document_id
                        )
                        or ""
                    ),
                    "name": document.original_file_name,
                    "size_bytes": document.size_bytes,
                }
                for document in batch_index.documents
            ],
            "source_archive_bindings": dict(
                payload.get("source_archive_bindings") or {}
            ),
            "parent_invoice_record_ids": [
                str(record_id)
                for record_id in payload.get("parent_invoice_record_ids", [])
                if str(record_id).strip()
            ],
            "source_kind": str(payload.get("source_kind") or "external_upload"),
            "archive_sources": bool(payload.get("archive_sources", True)),
        }
        PROCESSING_JOB_QUEUE.append(job_id)
        _persist_processing_jobs_locked()

    _start_next_processing_job_if_idle()
    return _processing_job(job_id)


def _reprocess_archived_source_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    source_ids = [
        str(value).strip()
        for value in payload.get("source_ids", [])
        if str(value).strip()
    ]
    if not source_ids:
        raise ValueError("Seleziona almeno un PDF origine da riprocessare")
    sources = [archived_source_path(source_id, RUNTIME_PATHS) for source_id in source_ids]
    batch = import_pdf_batch(
        [
            PdfBatchSource(
                path=source_path,
                original_file_name=source.original_file_name,
            )
            for source, source_path in sources
        ],
        runtime_paths=RUNTIME_PATHS,
        source_label=f"Riprocessamento {len(sources)} PDF",
    )
    bindings = {
        document.document_id: source.source_id
        for document, (source, _) in zip(batch.documents, sources, strict=True)
    }
    return _start_processing_job(
        {
            "batch_id": batch.batch_id,
            "source_archive_bindings": bindings,
            "source_kind": "source_archive",
            "archive_sources": True,
        }
    )


def _reprocess_archived_invoices(payload: dict[str, Any]) -> dict[str, Any]:
    record_ids = [
        str(value).strip()
        for value in payload.get("record_ids", [])
        if str(value).strip()
    ]
    if not record_ids:
        raise ValueError("Seleziona almeno una fattura da riprocessare")
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("Sono presenti fatture duplicate")

    registry = load_invoice_registry(RUNTIME_PATHS)
    records_by_id = {
        str(record.get("record_id") or ""): record
        for record in registry.get("records", [])
        if isinstance(record, dict)
    }
    records = [records_by_id.get(record_id) for record_id in record_ids]
    if any(record is None for record in records):
        raise ValueError("Una fattura selezionata non e piu disponibile")

    typed_records = [record for record in records if record is not None]
    statuses = {str(record.get("status") or "") for record in typed_records}
    if len(statuses) != 1 or not statuses.issubset({"saved", "discarded"}):
        raise ValueError("Seleziona fatture salvate oppure fatture scartate")

    sources = [
        PdfBatchSource(
            path=_archived_invoice_pdf_path(str(record.get("archived_pdf_path") or "")),
            original_file_name=str(record.get("source_file_name") or "fattura.pdf"),
            parent_invoice_record_id=str(record.get("record_id") or ""),
            source_page_numbers=tuple(
                int(page_number)
                for page_number in record.get("archived_pdf_page_numbers", [])
                if isinstance(page_number, int | float | str)
                and str(page_number).strip().isdigit()
            ),
        )
        for record in typed_records
    ]
    status = next(iter(statuses))
    batch = import_pdf_batch(
        sources,
        runtime_paths=RUNTIME_PATHS,
        source_label=f"Riprocessamento {len(sources)} fatture",
    )
    try:
        return _start_processing_job(
            {
                "batch_id": batch.batch_id,
                "parent_invoice_record_ids": record_ids,
                "source_kind": (
                    "saved_invoice" if status == "saved" else "discarded_invoice"
                ),
                "archive_sources": False,
            }
        )
    except Exception:
        delete_imported_batch(batch.batch_id, RUNTIME_PATHS)
        raise


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


def _archived_invoice_pdf_path(value: str) -> Path:
    normalized_value = value.replace("\\", "/").strip().lstrip("/")
    if not normalized_value:
        raise ValueError("PDF della fattura non disponibile")
    invoice_path = (RUNTIME_PATHS.app_data_dir / normalized_value).resolve()
    archive_invoices_dir = RUNTIME_PATHS.archive_invoices_dir.resolve()
    if invoice_path != archive_invoices_dir and archive_invoices_dir not in invoice_path.parents:
        raise ValueError("PDF della fattura fuori dall'archivio")
    if invoice_path.suffix.lower() != ".pdf" or not invoice_path.is_file():
        raise ValueError("PDF della fattura non trovato")
    return invoice_path


def _start_next_processing_job_if_idle() -> None:
    global PROCESSING_ACTIVE_JOB_ID

    with PROCESSING_JOBS_LOCK:
        if PROCESSING_SHUTDOWN_REQUESTED or PROCESSING_ACTIVE_JOB_ID is not None or not PROCESSING_JOB_QUEUE:
            return
        job_id = PROCESSING_JOB_QUEUE.pop(0)
        job = PROCESSING_JOBS.get(job_id)
        if job is None or job.get("status") != "queued":
            return
        PROCESSING_ACTIVE_JOB_ID = job_id
        PROCESSING_JOB_IDLE.clear()
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
            should_interrupt=lambda: bool(_processing_job(job_id).get("interrupt_requested")),
        )
        _update_processing_job(
            job_id,
            status="finalizing",
            message=(
                "Archiviazione dei file origine"
                if _job_archives_sources(job)
                else "Completamento della lavorazione"
            ),
            result=_processing_run_payload(run_index),
        )
        if _job_archives_sources(job):
            _finalize_job_sources(job_id, batch_id, process_id)
        else:
            _delete_job_staging(job)
        _update_processing_job(
            job_id,
            status="completed",
            message="Processamento completato",
            **_processing_time_estimate(job_id, progress_total, progress_total),
            result=_processing_run_payload(run_index),
        )
    except ProcessingInterrupted:
        with PROCESSING_JOBS_LOCK:
            job = PROCESSING_JOBS[job_id]
            job.update(
                status="queued",
                message="Ripresa in coda dopo chiusura dell'app",
                cancel_requested=False,
                interrupt_requested=False,
                estimated_remaining_seconds=None,
            )
            PROCESSING_JOB_QUEUE.insert(0, job_id)
            _persist_processing_jobs_locked()
    except ProcessingCancelled as error:
        job = _processing_job(job_id)
        _delete_job_process_dir(job)
        _delete_job_staging(job)
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
    except Exception as error:
        job = _processing_job(job_id)
        if _job_archives_sources(job):
            try:
                _finalize_job_sources(
                    job_id,
                    batch_id,
                    str(job.get("process_id") or ""),
                )
            except Exception:
                pass
        else:
            _delete_job_staging(job)
        _delete_job_process_dir(job)
        _update_processing_job(
            job_id,
            status="error",
            message="Elaborazione non completata. Controlla i file e riprova.",
            technical_error=str(error),
        )
    finally:
        _finish_active_processing_job(job_id)


def _finish_active_processing_job(job_id: str) -> None:
    global PROCESSING_ACTIVE_JOB_ID

    with PROCESSING_JOBS_LOCK:
        if PROCESSING_ACTIVE_JOB_ID == job_id:
            PROCESSING_ACTIVE_JOB_ID = None
            PROCESSING_JOB_IDLE.set()
        _persist_processing_jobs_locked()
    _start_next_processing_job_if_idle()


def has_active_processing_job() -> bool:
    with PROCESSING_JOBS_LOCK:
        return PROCESSING_ACTIVE_JOB_ID is not None


def shutdown_processing_jobs(timeout: float = 30.0) -> bool:
    global PROCESSING_SHUTDOWN_REQUESTED
    with PROCESSING_JOBS_LOCK:
        PROCESSING_SHUTDOWN_REQUESTED = True
        if PROCESSING_ACTIVE_JOB_ID is not None:
            job = PROCESSING_JOBS.get(PROCESSING_ACTIVE_JOB_ID)
            if job is not None and job.get("status") not in TERMINAL_PROCESSING_JOB_STATUSES:
                job["status"] = "stopping"
                job["interrupt_requested"] = True
                job["message"] = "Interruzione in corso"
                _persist_processing_jobs_locked()
    return PROCESSING_JOB_IDLE.wait(timeout)


def resume_processing_after_failed_shutdown() -> None:
    global PROCESSING_SHUTDOWN_REQUESTED
    with PROCESSING_JOBS_LOCK:
        PROCESSING_SHUTDOWN_REQUESTED = False
        if PROCESSING_ACTIVE_JOB_ID is not None:
            job = PROCESSING_JOBS.get(PROCESSING_ACTIVE_JOB_ID)
            if job is not None and job.get("status") == "stopping":
                job["status"] = "processing"
                job["interrupt_requested"] = False
                job["message"] = "Processamento OCR in corso"
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


def _source_archive_payload() -> dict[str, Any]:
    registry = load_invoice_registry(RUNTIME_PATHS)
    linked_counts: dict[str, int] = {}
    for record in registry.get("records", []):
        if (
            not isinstance(record, dict)
            or record.get("status") not in {"saved", "discarded"}
        ):
            continue
        source_path = str(record.get("archived_source_pdf_path") or "")
        if source_path:
            linked_counts[source_path] = linked_counts.get(source_path, 0) + 1
    records = list_archived_sources(RUNTIME_PATHS)
    return {
        "ok": True,
        "records": [
            {
                "source_id": source.source_id,
                "source_file_name": source.original_file_name,
                "archived_at": source.archived_at,
                "size_bytes": source.size_bytes,
                "archived_source_pdf_path": source.relative_path,
                "archived_source_pdf_url": f"/runtime/{source.relative_path}",
                "linked_invoice_count": linked_counts.get(source.relative_path, 0),
            }
            for source in sorted(records, key=lambda item: item.archived_at, reverse=True)
        ],
    }


def _reject_sources_used_by_pending_reviews(source_records: list[Any]) -> None:
    target_origins = {
        (origin.batch_id, origin.document_id)
        for source in source_records
        for origin in source.origins
    }
    if not target_origins:
        return
    for review_index_path in RUNTIME_PATHS.manifests_dir.rglob("review_batch_index.json"):
        try:
            payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for invoice in payload.get("invoices", []):
            if not isinstance(invoice, dict) or invoice.get("status", "pending") != "pending":
                continue
            origin = (
                str(invoice.get("source_batch_id") or ""),
                str(invoice.get("document_id") or ""),
            )
            if origin in target_origins:
                raise ValueError(
                    "Il file origine contiene fatture ancora da revisionare e non puo essere eliminato"
                )


def _reject_sources_used_by_processing_jobs(source_records: list[Any]) -> None:
    source_ids = {
        str(source.source_id)
        for source in source_records
        if str(source.source_id).strip()
    }
    if not source_ids:
        return

    with PROCESSING_JOBS_LOCK:
        is_in_use = any(
            str(job.get("status") or "") in SOURCE_DELETE_BLOCKING_JOB_STATUSES
            and bool(
                source_ids.intersection(
                    str(source_id)
                    for source_id in (job.get("source_archive_bindings") or {}).values()
                    if str(source_id).strip()
                )
            )
            for job in PROCESSING_JOBS.values()
        )

    if is_in_use:
        raise ValueError(
            "Il file origine e usato da una lavorazione. "
            "Annulla o rimuovi prima la lavorazione."
        )


def _reject_invoice_records_used_by_processing_jobs(record_ids: list[str]) -> None:
    targets = {record_id for record_id in record_ids if record_id.strip()}
    if not targets:
        return
    with PROCESSING_JOBS_LOCK:
        is_in_use = any(
            str(job.get("status") or "") in SOURCE_DELETE_BLOCKING_JOB_STATUSES
            and bool(
                targets.intersection(
                    str(record_id)
                    for record_id in job.get("parent_invoice_record_ids", [])
                    if str(record_id).strip()
                )
            )
            for job in PROCESSING_JOBS.values()
        )
    if is_in_use:
        raise ValueError(
            "La fattura e usata da una lavorazione. "
            "Annulla o rimuovi prima la lavorazione."
        )


def _reject_invoice_records_used_by_pending_reviews(record_ids: list[str]) -> None:
    targets = {record_id for record_id in record_ids if record_id.strip()}
    if not targets:
        return
    for review_index_path in RUNTIME_PATHS.manifests_dir.rglob(
        "review_batch_index.json"
    ):
        try:
            payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if any(
            isinstance(invoice, dict)
            and invoice.get("status", "pending") == "pending"
            and str(invoice.get("parent_invoice_record_id") or "") in targets
            for invoice in payload.get("invoices", [])
        ):
            raise ValueError(
                "La fattura contiene un riprocessamento ancora da revisionare."
            )


def _delete_processing_history(payload: dict[str, Any]) -> dict[str, Any]:
    delete_all = bool(payload.get("all"))
    requested_ids = {
        str(value).strip()
        for value in payload.get("job_ids", [])
        if str(value).strip()
    }
    with PROCESSING_JOBS_LOCK:
        target_ids = {
            job_id
            for job_id, job in PROCESSING_JOBS.items()
            if str(job.get("status") or "") in TERMINAL_PROCESSING_JOB_STATUSES
            and (delete_all or job_id in requested_ids)
        }
        if not target_ids:
            raise ValueError("Nessun processo in cronologia selezionato")
        for job_id in target_ids:
            PROCESSING_JOBS.pop(job_id, None)
        _persist_processing_jobs_locked()
    return {"ok": True, "deleted_count": len(target_ids)}


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
        if job.get("status") == "cancelling":
            return dict(job)
        if job.get("status") == "queued":
            raise ValueError("Rimuovi dalla coda la lavorazione non ancora avviata")
        if job.get("status") != "processing":
            return dict(job)
        PROCESSING_JOBS[job_id] = {
            **job,
            "status": "cancelling",
            "cancel_requested": True,
            "message": "Annullamento in corso",
        }
        _persist_processing_jobs_locked()
        return dict(PROCESSING_JOBS[job_id])


def _remove_processing_job(path: str) -> dict[str, Any]:
    job_id = path.removesuffix("/remove").rsplit("/", 1)[-1]
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        if job is None:
            raise ValueError("Lavorazione non trovata")
        if job.get("status") in {"processing", "cancelling"}:
            raise ValueError("Annulla la lavorazione attiva prima di rimuoverla")
        if job.get("status") != "queued":
            return dict(job)
        if job_id in PROCESSING_JOB_QUEUE:
            PROCESSING_JOB_QUEUE.remove(job_id)
        _delete_job_process_dir(job)
        _delete_job_staging(job)
        PROCESSING_JOBS[job_id] = {
            **job,
            "status": "cancelled",
            "message": "Lavorazione rimossa dalla coda",
            "cancel_requested": False,
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


def _delete_job_staging(job: dict[str, Any] | None) -> None:
    if not job:
        return
    batch_id = str(job.get("batch_id") or "")
    if not batch_id:
        return
    shutil.rmtree(RUNTIME_PATHS.batches_dir / batch_id, ignore_errors=True)


def _finalize_job_sources(
    job_id: str,
    batch_id: str,
    process_id: str,
    allow_missing: bool = False,
) -> None:
    batch_dir = RUNTIME_PATHS.batches_dir / batch_id
    if not batch_dir.is_dir():
        return
    job = _processing_job(job_id)
    batch = load_batch_index(batch_dir)
    if allow_missing:
        available_documents = [
            document
            for document in batch.documents
            if (batch_dir / document.relative_path).is_file()
            or str(
                (job.get("source_archive_bindings") or {}).get(document.document_id)
                or ""
            )
        ]
        batch = replace(
            batch,
            document_count=len(available_documents),
            documents=available_documents,
        )
    promoted = promote_batch_sources(
        batch,
        process_id,
        RUNTIME_PATHS,
        source_bindings=dict(job.get("source_archive_bindings") or {}),
    )
    promoted_files = [
            {
                "document_id": document.document_id,
                "source_id": source.source_id,
                "name": document.original_file_name,
                "size_bytes": document.size_bytes,
            }
            for document, source in zip(batch.documents, promoted, strict=True)
        ]
    if allow_missing:
        promoted_by_document = {
            str(item.get("document_id") or ""): item for item in promoted_files
        }
        files = [
            promoted_by_document.get(str(item.get("document_id") or ""), item)
            for item in job.get("files", [])
            if isinstance(item, dict)
        ]
    else:
        files = promoted_files
    _update_processing_job(job_id, files=files)
    shutil.rmtree(batch_dir, ignore_errors=True)


def _reconcile_terminal_job_storage() -> None:
    with PROCESSING_JOBS_LOCK:
        jobs = [dict(job) for job in PROCESSING_JOBS.values()]
    for job in jobs:
        status = str(job.get("status") or "")
        if status not in TERMINAL_PROCESSING_JOB_STATUSES:
            continue
        if status in {"completed", "error"}:
            if _job_archives_sources(job):
                try:
                    _finalize_job_sources(
                        str(job.get("job_id") or ""),
                        str(job.get("batch_id") or ""),
                        str(job.get("process_id") or ""),
                        allow_missing=True,
                    )
                except Exception:
                    continue
            else:
                _delete_job_staging(job)
        else:
            _delete_job_staging(job)


def _reconcile_finalizing_jobs() -> None:
    with PROCESSING_JOBS_LOCK:
        jobs = [
            dict(job)
            for job in PROCESSING_JOBS.values()
            if str(job.get("status") or "") == "finalizing"
        ]
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        try:
            if _job_archives_sources(job):
                _finalize_job_sources(
                    job_id,
                    str(job.get("batch_id") or ""),
                    str(job.get("process_id") or ""),
                )
            else:
                _delete_job_staging(job)
            _update_processing_job(
                job_id,
                status="completed",
                message="Processamento completato",
            )
        except Exception as error:
            _update_processing_job(
                job_id,
                status="error",
                message="Archiviazione dei file origine non completata.",
                technical_error=str(error),
            )


def _processing_job(job_id: str) -> dict[str, Any]:
    with PROCESSING_JOBS_LOCK:
        job = PROCESSING_JOBS.get(job_id)
        return dict(job) if job else {}


def _job_archives_sources(job: dict[str, Any] | None) -> bool:
    if not job:
        return True
    return bool(job.get("archive_sources", True))


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
    server = LocalHTTPServer((HOST, PORT), RegistryApiHandler, dev_origins=(
        "http://localhost:5173", "http://127.0.0.1:5173",
    ))
    print(f"Registry API attiva su http://{HOST}:{PORT}")
    print("Endpoint import: POST /registry/import")
    server.serve_forever()


if __name__ == "__main__":
    main()
