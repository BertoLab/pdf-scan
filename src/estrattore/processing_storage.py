from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from estrattore.batch_storage import ImportedBatchIndex, load_batch_index
from estrattore.document_supervisor import (
    DocumentExecution,
    WorkerCancelled,
    run_document_worker,
)
from estrattore.feedback_collection import (
    capture_extraction_document,
    discard_staged_extraction_process,
    promote_extraction_process,
    record_capture_issue,
    record_segmentation_event,
)
from estrattore.invoice_segmentation_edit import (
    SegmentationEditResult,
    invoice_display_number,
    move_boundary_page,
    set_invoice_display_number,
    shift_invoice_display_numbers,
    split_invoice,
)
from estrattore.runtime_paths import RuntimePaths, get_runtime_paths
from estrattore.source_archive_storage import resolve_archived_source_for_origin


PROCESSING_SCHEMA_VERSION = 1
PROCESSING_INDEX_FILE_NAME = "processing_run_index.json"
PROCESSING_COMPLETE_FILE_NAME = "processing_complete.ok"
DOCUMENT_COMPLETE_FILE_NAME = "document_complete.ok"
DOCUMENT_WORKING_SUFFIX = ".working"
REVIEW_BATCH_SIZE = 15
COMPLETED_PROCESSING_STATUSES = {"processed", "processed_with_errors"}
REVIEW_MUTATIONS_DIR_NAME = "_review_mutations"
REVIEW_DRAFTS_FILE_NAME = "_review_drafts.json"
_REVIEW_MUTATION_LOCK = threading.RLock()

DocumentProcessor = Callable[[Path, Path, int], list[Path]]
ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class ProcessingCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessedInvoiceRef:
    invoice_id: str
    title: str
    document_id: str
    source_batch_id: str
    source_file_name: str
    status: str = "pending"
    process_id: str = ""
    parent_invoice_record_id: str = ""
    source_page_numbers: list[int] = field(default_factory=list)
    display_number: int = 0


@dataclass(frozen=True)
class ProcessedDocumentRef:
    document_id: str
    base_path: str
    invoice_count: int
    review_invoice_ids: list[str]


@dataclass(frozen=True)
class ReviewBatchRef:
    id: str
    label: str
    base_path: str
    invoice_count: int


@dataclass(frozen=True)
class ProcessingRunIndex:
    schema_version: int
    process_id: str
    source_batch_id: str
    created_at: str
    status: str
    document_count: int
    invoice_count: int
    review_batch_count: int
    review_batches: list[ReviewBatchRef]
    errors: list[str]
    retired_review_batch_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReprocessReviewBatchResult:
    review_batch_id: str
    review_batch_base_path: str
    reprocessed_document_ids: list[str]
    updated_invoice_ids: list[str]
    unmapped_invoice_ids: list[str]
    new_invoice_ids_not_in_review: list[str]


@dataclass(frozen=True)
class ClearReviewBatchesResult:
    deleted_review_batch_count: int
    updated_processing_run_count: int


def process_imported_batch(
    batch_id: str,
    runtime_paths: RuntimePaths | None = None,
    document_processor: DocumentProcessor | None = None,
    review_batch_size: int = REVIEW_BATCH_SIZE,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    process_id: str | None = None,
    resume_existing: bool = False,
) -> ProcessingRunIndex:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()
    batch_dir = paths.batches_dir / batch_id
    if not batch_dir.is_dir():
        raise ValueError(f"Batch non trovato: {batch_id}")

    batch_index = load_batch_index(batch_dir)
    processor = document_processor or _default_document_processor
    created_at = (now or datetime.now()).replace(microsecond=0)
    process_id = process_id or _new_process_id(created_at, paths.manifests_dir)
    process_dir = paths.manifests_dir / process_id
    documents_dir = process_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=resume_existing)
    if resume_existing:
        _cleanup_working_document_dirs(documents_dir)

    invoice_refs: list[ProcessedInvoiceRef] = []
    errors: list[str] = []
    next_invoice_number = 1
    run_index = ProcessingRunIndex(
        schema_version=PROCESSING_SCHEMA_VERSION,
        process_id=process_id,
        source_batch_id=batch_index.batch_id,
        created_at=created_at.isoformat(),
        status="processing",
        document_count=len(batch_index.documents),
        invoice_count=0,
        review_batch_count=0,
        review_batches=[],
        errors=[],
    )
    save_processing_run_index(run_index, process_dir)
    try:
        total_documents = len(batch_index.documents)
        for processed_document_count, document in enumerate(batch_index.documents, start=1):
            if should_cancel and should_cancel():
                raise ProcessingCancelled("Processamento annullato dall'utente")
            source_path = batch_dir / document.relative_path
            output_dir = documents_dir / document.document_id
            working_output_dir = documents_dir / (
                f"{document.document_id}{DOCUMENT_WORKING_SUFFIX}"
            )
            if resume_existing and is_document_processing_complete(output_dir):
                try:
                    _capture_extraction_feedback(
                        process_id=process_id,
                        source_batch_id=batch_index.batch_id,
                        document_id=document.document_id,
                        source_file_name=document.original_file_name,
                        source_path=source_path,
                        document_dir=output_dir,
                        runtime_paths=paths,
                        parent_invoice_record_id=document.parent_invoice_record_id,
                        source_page_numbers=list(document.source_page_numbers),
                    )
                    manifest_invoice_refs = _invoice_refs_from_manifest(
                        manifest_path=output_dir / "viewer_manifest.json",
                        document_id=document.document_id,
                        source_batch_id=batch_index.batch_id,
                        source_file_name=document.original_file_name,
                        process_id=process_id,
                        parent_invoice_record_id=document.parent_invoice_record_id,
                        source_page_numbers=list(document.source_page_numbers),
                    )
                    invoice_refs.extend(manifest_invoice_refs)
                    next_invoice_number += len(manifest_invoice_refs)
                except Exception:
                    shutil.rmtree(output_dir, ignore_errors=True)
                else:
                    if progress_callback:
                        progress_callback(
                            processed_document_count,
                            total_documents,
                            document.original_file_name,
                        )
                    continue

            shutil.rmtree(working_output_dir, ignore_errors=True)
            if output_dir.exists() and not is_document_processing_complete(output_dir):
                shutil.rmtree(output_dir, ignore_errors=True)
            try:
                if document_processor is None:
                    run_document_worker(
                        DocumentExecution(
                            source_path=source_path,
                            working_output_dir=working_output_dir,
                            output_dir=output_dir,
                            document_id=document.document_id,
                            first_invoice_number=next_invoice_number,
                            control_dir=process_dir / "workers" / document.document_id,
                        ),
                        should_cancel=should_cancel,
                    )
                    manifest_path = output_dir / "viewer_manifest.json"
                else:
                    saved_paths = processor(
                        source_path,
                        working_output_dir,
                        next_invoice_number,
                    )
                    manifest_path = _manifest_path_from_saved_paths(
                        saved_paths,
                        working_output_dir,
                    )
                manifest_invoice_refs = _invoice_refs_from_manifest(
                    manifest_path=manifest_path,
                    document_id=document.document_id,
                    source_batch_id=batch_index.batch_id,
                    source_file_name=document.original_file_name,
                    process_id=process_id,
                    parent_invoice_record_id=document.parent_invoice_record_id,
                    source_page_numbers=list(document.source_page_numbers),
                )
                if document_processor is not None:
                    shutil.rmtree(output_dir, ignore_errors=True)
                    working_output_dir.replace(output_dir)
                    mark_document_processing_complete(
                        output_dir=output_dir,
                        document_id=document.document_id,
                        invoice_count=len(manifest_invoice_refs),
                    )
                _capture_extraction_feedback(
                    process_id=process_id,
                    source_batch_id=batch_index.batch_id,
                    document_id=document.document_id,
                    source_file_name=document.original_file_name,
                    source_path=source_path,
                    document_dir=output_dir,
                    runtime_paths=paths,
                    parent_invoice_record_id=document.parent_invoice_record_id,
                    source_page_numbers=list(document.source_page_numbers),
                )
                invoice_refs.extend(manifest_invoice_refs)
                next_invoice_number += len(manifest_invoice_refs)
            except WorkerCancelled as error:
                shutil.rmtree(working_output_dir, ignore_errors=True)
                raise ProcessingCancelled(
                    "Processamento annullato dall'utente"
                ) from error
            except Exception as error:
                shutil.rmtree(working_output_dir, ignore_errors=True)
                errors.append(f"{document.original_file_name}: {error}")
            if progress_callback:
                progress_callback(
                    processed_document_count,
                    total_documents,
                    document.original_file_name,
                )
            if should_cancel and should_cancel():
                raise ProcessingCancelled("Processamento annullato dall'utente")

        review_batches = _append_global_review_batches(
            process_id=process_id,
            runtime_paths=paths,
            invoice_refs=invoice_refs,
            review_batch_size=review_batch_size,
        )
        status = "processed_with_errors" if errors else "processed"
        if not invoice_refs and errors:
            status = "error"
        run_index = ProcessingRunIndex(
            schema_version=PROCESSING_SCHEMA_VERSION,
            process_id=process_id,
            source_batch_id=batch_index.batch_id,
            created_at=created_at.isoformat(),
            status=status,
            document_count=len(batch_index.documents),
            invoice_count=len(invoice_refs),
            review_batch_count=len(review_batches),
            review_batches=review_batches,
            errors=errors,
        )
        save_processing_run_index(run_index, process_dir)
        if run_index.status in COMPLETED_PROCESSING_STATUSES:
            _promote_extraction_feedback(process_id, paths)
            mark_processing_run_complete(run_index, process_dir)
        else:
            _discard_staged_extraction_feedback(process_id, paths)
        return run_index
    except ProcessingCancelled:
        _discard_staged_extraction_feedback(process_id, paths)
        shutil.rmtree(process_dir, ignore_errors=True)
        raise
    except Exception as error:
        _discard_staged_extraction_feedback(process_id, paths)
        failed_run_index = ProcessingRunIndex(
            schema_version=PROCESSING_SCHEMA_VERSION,
            process_id=process_id,
            source_batch_id=batch_index.batch_id,
            created_at=created_at.isoformat(),
            status="error",
            document_count=len(batch_index.documents),
            invoice_count=len(invoice_refs),
            review_batch_count=0,
            review_batches=[],
            errors=[*errors, f"Errore critico durante il processamento: {error}"],
        )
        save_processing_run_index(failed_run_index, process_dir)
        raise


def list_review_batches(
    runtime_paths: RuntimePaths | None = None,
) -> list[ReviewBatchRef]:
    paths = runtime_paths or get_runtime_paths()
    recover_review_mutations(paths)
    if not paths.manifests_dir.exists():
        return []

    batches: list[tuple[str, ReviewBatchRef]] = []
    for index_path in paths.manifests_dir.glob(f"*/{PROCESSING_INDEX_FILE_NAME}"):
        try:
            run_index = load_processing_run_index(index_path.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not is_processing_run_complete(run_index, index_path.parent):
            continue
        for batch in run_index.review_batches:
            current_batch = _review_batch_with_current_count(paths, batch)
            if current_batch is not None:
                batches.append((run_index.created_at, current_batch))

    sorted_batches = [
        batch
        for _created_at, batch in sorted(
            (
                (created_at, batch)
                for created_at, batch in batches
                if batch.invoice_count > 0
            ),
            key=lambda item: (_created_at_sort_key(item[0]), item[1].id),
        )
    ]
    return sorted_batches


def clear_all_review_batches(
    runtime_paths: RuntimePaths | None = None,
) -> ClearReviewBatchesResult:
    paths = runtime_paths or get_runtime_paths()
    if not paths.manifests_dir.exists():
        return ClearReviewBatchesResult(
            deleted_review_batch_count=0,
            updated_processing_run_count=0,
        )

    deleted_count = 0
    for review_index_path in _all_review_index_paths(paths):
        review_batch_dir = review_index_path.parent
        if not _is_inside(review_batch_dir, paths.manifests_dir):
            continue
        shutil.rmtree(review_batch_dir, ignore_errors=True)
        deleted_count += 1

    global_queue_dir = _global_review_queue_dir(paths)
    if global_queue_dir.exists() and _is_inside(global_queue_dir, paths.manifests_dir):
        shutil.rmtree(global_queue_dir, ignore_errors=True)

    updated_run_count = 0
    for index_path in paths.manifests_dir.glob(f"*/{PROCESSING_INDEX_FILE_NAME}"):
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("review_batches") or payload.get("review_batch_count"):
            payload["review_batches"] = []
            payload["review_batch_count"] = 0
            _atomic_write_json(index_path, payload)
            updated_run_count += 1

    _review_drafts_path(paths).unlink(missing_ok=True)

    return ClearReviewBatchesResult(
        deleted_review_batch_count=deleted_count,
        updated_processing_run_count=updated_run_count,
    )


def cleanup_incomplete_processing_runs(
    runtime_paths: RuntimePaths | None = None,
    preserve_process_ids: set[str] | None = None,
) -> list[Path]:
    paths = runtime_paths or get_runtime_paths()
    if not paths.manifests_dir.exists():
        return []

    preserved = preserve_process_ids or set()
    removed_process_dirs: list[Path] = []
    for process_dir in paths.manifests_dir.iterdir():
        if not _is_processing_run_directory(process_dir):
            continue
        if process_dir.name in preserved:
            continue

        try:
            run_index = load_processing_run_index(process_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            shutil.rmtree(process_dir, ignore_errors=True)
            removed_process_dirs.append(process_dir)
            continue

        if is_processing_run_complete(run_index, process_dir):
            continue

        shutil.rmtree(process_dir, ignore_errors=True)
        removed_process_dirs.append(process_dir)

    return removed_process_dirs


def recover_missing_review_batches(
    runtime_paths: RuntimePaths | None = None,
) -> int:
    """Rebuild missing review projections without rerunning document OCR."""
    paths = runtime_paths or get_runtime_paths()
    if not paths.manifests_dir.exists():
        return 0

    finalized_invoice_keys = _finalized_review_invoice_keys(paths)
    recovered_count = 0
    for index_path in sorted(
        paths.manifests_dir.glob(f"process_*/{PROCESSING_INDEX_FILE_NAME}")
    ):
        process_dir = index_path.parent
        try:
            run_index = load_processing_run_index(process_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not is_processing_run_complete(run_index, process_dir):
            continue

        retired_review_batch_ids = set(run_index.retired_review_batch_ids)
        missing_batches = [
            batch
            for batch in run_index.review_batches
            if batch.id not in retired_review_batch_ids
            if not _review_batch_projection_is_valid(paths, batch)
        ]
        if not missing_batches:
            continue

        try:
            invoice_refs = _recoverable_process_invoice_refs(
                run_index,
                process_dir,
                paths,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        expected_invoice_count = sum(
            batch.invoice_count for batch in run_index.review_batches
        )
        if (
            len(invoice_refs) != run_index.invoice_count
            or len(invoice_refs) != expected_invoice_count
        ):
            continue

        offset = 0
        missing_ids = {batch.id for batch in missing_batches}
        for batch in run_index.review_batches:
            batch_refs = invoice_refs[offset : offset + batch.invoice_count]
            offset += batch.invoice_count
            if batch.id not in missing_ids:
                continue
            pending_refs = [
                invoice
                for invoice in batch_refs
                if (batch.base_path, invoice.invoice_id)
                not in finalized_invoice_keys
            ]
            _write_recovered_review_batch(
                paths,
                batch,
                run_index.process_id,
                pending_refs,
            )
            recovered_count += 1

    return recovered_count


def load_processing_run_index(process_dir: Path) -> ProcessingRunIndex:
    payload = json.loads(
        (process_dir / PROCESSING_INDEX_FILE_NAME).read_text(encoding="utf-8")
    )
    review_batches = [
        ReviewBatchRef(
            id=str(batch.get("id") or ""),
            label=str(batch.get("label") or ""),
            base_path=str(batch.get("base_path") or ""),
            invoice_count=int(batch.get("invoice_count") or 0),
        )
        for batch in payload.get("review_batches", [])
        if isinstance(batch, dict)
    ]
    return ProcessingRunIndex(
        schema_version=int(payload.get("schema_version") or PROCESSING_SCHEMA_VERSION),
        process_id=str(payload.get("process_id") or process_dir.name),
        source_batch_id=str(payload.get("source_batch_id") or ""),
        created_at=str(payload.get("created_at") or ""),
        status=str(payload.get("status") or ""),
        document_count=int(payload.get("document_count") or 0),
        invoice_count=int(payload.get("invoice_count") or 0),
        review_batch_count=int(payload.get("review_batch_count") or len(review_batches)),
        review_batches=review_batches,
        errors=[
            str(error)
            for error in payload.get("errors", [])
            if isinstance(error, str)
        ],
        retired_review_batch_ids=[
            str(batch_id)
            for batch_id in payload.get("retired_review_batch_ids", [])
            if str(batch_id).strip()
        ],
    )


def save_processing_run_index(
    run_index: ProcessingRunIndex,
    process_dir: Path,
) -> Path:
    output_path = process_dir / PROCESSING_INDEX_FILE_NAME
    temporary_path = output_path.with_name(f".{output_path.name}.writing")
    temporary_path.write_text(
        json.dumps(asdict(run_index), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path


def mark_processing_run_complete(
    run_index: ProcessingRunIndex,
    process_dir: Path,
) -> Path:
    if run_index.status not in COMPLETED_PROCESSING_STATUSES:
        raise ValueError(f"Stato processamento non completabile: {run_index.status}")

    marker_path = process_dir / PROCESSING_COMPLETE_FILE_NAME
    temporary_path = marker_path.with_name(f".{marker_path.name}.writing")
    temporary_path.write_text(
        json.dumps(
            {
                "process_id": run_index.process_id,
                "status": run_index.status,
                "invoice_count": run_index.invoice_count,
                "review_batch_count": run_index.review_batch_count,
                "completed_at": datetime.now().replace(microsecond=0).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(marker_path)
    return marker_path


def mark_document_processing_complete(
    output_dir: Path,
    document_id: str,
    invoice_count: int,
) -> Path:
    marker_path = output_dir / DOCUMENT_COMPLETE_FILE_NAME
    temporary_path = marker_path.with_name(f".{marker_path.name}.writing")
    temporary_path.write_text(
        json.dumps(
            {
                "document_id": document_id,
                "invoice_count": invoice_count,
                "completed_at": datetime.now().replace(microsecond=0).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(marker_path)
    return marker_path


def is_processing_run_complete(
    run_index: ProcessingRunIndex,
    process_dir: Path,
) -> bool:
    if run_index.status not in COMPLETED_PROCESSING_STATUSES:
        return False
    if run_index.review_batch_count != len(run_index.review_batches):
        return False
    if run_index.review_batch_count == 0:
        return False
    return (process_dir / PROCESSING_COMPLETE_FILE_NAME).is_file()


def is_document_processing_complete(output_dir: Path) -> bool:
    manifest_path = output_dir / "viewer_manifest.json"
    marker_path = output_dir / DOCUMENT_COMPLETE_FILE_NAME
    return output_dir.is_dir() and manifest_path.is_file() and marker_path.is_file()


def mark_review_invoice(
    review_batch_base_path: str,
    invoice_id: str,
    status: str,
    runtime_paths: RuntimePaths | None = None,
) -> None:
    if status not in {"saved", "discarded"}:
        raise ValueError(f"Stato review non valido: {status}")

    paths = runtime_paths or get_runtime_paths()
    review_batch_dir = review_batch_dir_from_base_path(review_batch_base_path, paths)
    review_index_path = review_batch_dir / "review_batch_index.json"
    viewer_index_path = review_batch_dir / "viewer_index.json"
    review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))

    found = False
    closed_invoice_document_id = ""
    closed_invoice_source_batch_id = ""
    closed_invoice_document_dir: Path | None = None
    should_cleanup_closed_invoice = False
    for invoice in review_payload.get("invoices", []):
        if isinstance(invoice, dict) and invoice.get("invoice_id") == invoice_id:
            previous_status = str(invoice.get("status") or "pending")
            invoice["status"] = status
            closed_invoice_document_id = str(invoice.get("document_id") or "")
            closed_invoice_source_batch_id = str(invoice.get("source_batch_id") or "")
            should_cleanup_closed_invoice = previous_status == "pending"
            if should_cleanup_closed_invoice and closed_invoice_document_id:
                closed_invoice_document_dir = _document_dir_for_review_document(
                    review_batch_dir=review_batch_dir,
                    document_id=closed_invoice_document_id,
                    runtime_paths=paths,
                )
            found = True
            break
    if not found:
        raise ValueError(f"Fattura non presente nel batch di revisione: {invoice_id}")

    active_invoices = [
        invoice
        for invoice in review_payload.get("invoices", [])
        if isinstance(invoice, dict) and invoice.get("status", "pending") == "pending"
    ]
    review_payload["invoice_count"] = len(active_invoices)
    _atomic_write_json(review_index_path, review_payload)

    viewer_payload = json.loads(viewer_index_path.read_text(encoding="utf-8"))
    next_documents = []
    for document in viewer_payload.get("documents", []):
        if not isinstance(document, dict):
            continue
        next_invoice_ids = [
            str(current_invoice_id)
            for current_invoice_id in document.get("review_invoice_ids", [])
            if str(current_invoice_id) != invoice_id
        ]
        if next_invoice_ids:
            next_documents.append({**document, "review_invoice_ids": next_invoice_ids})
    viewer_payload["documents"] = next_documents
    _atomic_write_json(viewer_index_path, viewer_payload)

    if should_cleanup_closed_invoice:
        _cleanup_closed_invoice_working_files(
            runtime_paths=paths,
            review_batch_dir=review_batch_dir,
            document_id=closed_invoice_document_id,
            source_batch_id=closed_invoice_source_batch_id,
            document_dir=closed_invoice_document_dir,
        )
    _retire_review_batch_if_complete(
        runtime_paths=paths,
        review_batch_dir=review_batch_dir,
        review_payload=review_payload,
    )


def load_review_invoice_ref(
    review_batch_base_path: str,
    invoice_id: str,
    runtime_paths: RuntimePaths | None = None,
) -> ProcessedInvoiceRef:
    paths = runtime_paths or get_runtime_paths()
    review_batch_dir = review_batch_dir_from_base_path(review_batch_base_path, paths)
    review_index_path = review_batch_dir / "review_batch_index.json"
    review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))

    for invoice in review_payload.get("invoices", []):
        if isinstance(invoice, dict) and invoice.get("invoice_id") == invoice_id:
            return ProcessedInvoiceRef(
                invoice_id=str(invoice.get("invoice_id") or ""),
                title=str(invoice.get("title") or ""),
                document_id=str(invoice.get("document_id") or ""),
                source_batch_id=str(invoice.get("source_batch_id") or ""),
                source_file_name=str(invoice.get("source_file_name") or ""),
                status=str(invoice.get("status") or "pending"),
                process_id=str(invoice.get("process_id") or ""),
                parent_invoice_record_id=str(
                    invoice.get("parent_invoice_record_id") or ""
                ),
                source_page_numbers=[
                    int(page_number)
                    for page_number in invoice.get("source_page_numbers", [])
                    if isinstance(page_number, int | float | str)
                    and str(page_number).strip().isdigit()
                ],
                display_number=_positive_int(invoice.get("display_number")),
            )

    raise ValueError(f"Fattura non presente nel batch di revisione: {invoice_id}")


def reprocess_review_batch(
    review_batch_base_path: str,
    invoice_id: str | None = None,
    runtime_paths: RuntimePaths | None = None,
    document_processor: DocumentProcessor | None = None,
) -> ReprocessReviewBatchResult:
    paths = runtime_paths or get_runtime_paths()
    processor = document_processor or _default_document_processor
    review_batch_dir = review_batch_dir_from_base_path(review_batch_base_path, paths)
    review_index_path = review_batch_dir / "review_batch_index.json"
    viewer_index_path = review_batch_dir / "viewer_index.json"
    review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))
    viewer_payload = json.loads(viewer_index_path.read_text(encoding="utf-8"))
    invoice_entries = [
        invoice
        for invoice in review_payload.get("invoices", [])
        if isinstance(invoice, dict)
    ]
    if not invoice_entries:
        raise ValueError("La revisione non contiene fatture riprocessabili")

    if invoice_id:
        selected_entries = [
            invoice
            for invoice in invoice_entries
            if str(invoice.get("invoice_id") or "") == invoice_id
        ]
        if not selected_entries:
            raise ValueError(f"Fattura non presente nella revisione: {invoice_id}")
    else:
        selected_entries = [
            invoice
            for invoice in invoice_entries
            if invoice.get("status", "pending") == "pending"
        ] or invoice_entries

    target_document_ids = {
        str(invoice.get("document_id") or "")
        for invoice in selected_entries
        if str(invoice.get("document_id") or "")
    }
    if not target_document_ids:
        raise ValueError("Nessun documento sorgente trovato per la revisione")

    source_batch_ids = {
        str(invoice.get("source_batch_id") or "")
        for invoice in selected_entries
        if str(invoice.get("source_batch_id") or "")
    }
    if len(source_batch_ids) != 1:
        raise ValueError("La revisione contiene sorgenti da gruppi diversi")
    source_batch_id = next(iter(source_batch_ids))
    source_names_by_document = {
        str(invoice.get("document_id") or ""): str(
            invoice.get("source_file_name") or ""
        )
        for invoice in invoice_entries
        if str(invoice.get("document_id") or "")
    }

    refs_by_document: dict[str, list[ProcessedInvoiceRef]] = {}
    old_invoice_ids_by_document: dict[str, list[str]] = {}
    new_invoice_ids_not_in_review: list[str] = []
    draft_store = _load_review_draft_store(paths)
    draft_store_changed = False

    for document_id in sorted(target_document_ids):
        document_entries = [
            invoice
            for invoice in selected_entries
            if str(invoice.get("document_id") or "") == document_id
        ]
        source_path = _review_reprocess_source_path(
            source_batch_id=source_batch_id,
            document_id=document_id,
            invoice_entries=document_entries,
            runtime_paths=paths,
        )
        parent_invoice_record_ids = {
            str(invoice.get("parent_invoice_record_id") or "").strip()
            for invoice in document_entries
            if str(invoice.get("parent_invoice_record_id") or "").strip()
        }
        parent_invoice_record_id = (
            next(iter(parent_invoice_record_ids))
            if len(parent_invoice_record_ids) == 1
            else ""
        )
        source_page_numbers = sorted(
            {
                int(page_number)
                for invoice in document_entries
                for page_number in invoice.get("source_page_numbers", [])
                if isinstance(page_number, int | float | str)
                and str(page_number).strip().isdigit()
            }
        )

        document_dir = _document_dir_for_review_document(
            review_batch_dir=review_batch_dir,
            document_id=document_id,
            runtime_paths=paths,
        )
        old_manifest_path = document_dir / "viewer_manifest.json"
        if not _is_inside(document_dir, paths.manifests_dir) or not old_manifest_path.is_file():
            raise ValueError(f"Manifest documento non trovato: {document_id}")

        old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        old_manifest_invoices = [
            invoice
            for invoice in old_manifest.get("invoices", [])
            if isinstance(invoice, dict) and invoice.get("invoice_id")
        ]
        old_invoice_ids = [
            str(invoice.get("invoice_id") or "")
            for invoice in old_manifest_invoices
        ]
        old_invoice_ids_by_document[document_id] = old_invoice_ids
        first_invoice_number = min(
            (
                invoice_display_number(invoice, index)
                for index, invoice in enumerate(old_manifest_invoices, start=1)
            ),
            default=1,
        )
        documents_dir = document_dir.parent
        working_dir = documents_dir / f"{document_id}.reprocess_working"
        shutil.rmtree(working_dir, ignore_errors=True)

        try:
            saved_paths = processor(source_path, working_dir, first_invoice_number)
            manifest_path = _manifest_path_from_saved_paths(saved_paths, working_dir)
            new_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            _preserve_reprocessed_manifest_invoice_ids(
                manifest=new_manifest,
                old_invoice_ids=old_invoice_ids,
            )
            _atomic_write_json(manifest_path, new_manifest)
            reprocessed_refs = _invoice_refs_from_manifest(
                manifest_path=manifest_path,
                document_id=document_id,
                source_batch_id=source_batch_id,
                source_file_name=source_names_by_document.get(document_id, source_path.name),
                process_id=document_dir.parent.parent.name,
                parent_invoice_record_id=parent_invoice_record_id,
                source_page_numbers=source_page_numbers,
            )
            draft_store_changed = (
                _rebase_review_drafts_after_reprocess(
                    draft_store=draft_store,
                    process_id=document_dir.parent.parent.name,
                    document_id=document_id,
                    old_manifest=old_manifest,
                    new_manifest=new_manifest,
                )
                or draft_store_changed
            )
            refs_by_document[document_id] = reprocessed_refs
            mark_document_processing_complete(
                output_dir=working_dir,
                document_id=document_id,
                invoice_count=len(reprocessed_refs),
            )
            _replace_directory_atomically(working_dir, document_dir)
            _capture_extraction_feedback(
                process_id=document_dir.parent.parent.name,
                source_batch_id=source_batch_id,
                document_id=document_id,
                source_file_name=source_names_by_document.get(document_id, source_path.name),
                source_path=source_path,
                document_dir=document_dir,
                runtime_paths=paths,
                parent_invoice_record_id=parent_invoice_record_id,
                source_page_numbers=source_page_numbers,
                published=True,
            )
        except Exception:
            shutil.rmtree(working_dir, ignore_errors=True)
            raise

    if draft_store_changed:
        _atomic_write_json_durable(_review_drafts_path(paths), draft_store)

    updated_invoice_ids: list[str] = []
    unmapped_invoice_ids: list[str] = []
    next_invoice_entries: list[dict] = []
    for invoice in invoice_entries:
        document_id = str(invoice.get("document_id") or "")
        if document_id not in target_document_ids:
            next_invoice_entries.append(invoice)
            continue

        old_invoice_id = str(invoice.get("invoice_id") or "")
        mapped_ref = _mapped_reprocessed_invoice_ref(
            old_invoice_id=old_invoice_id,
            old_invoice_ids=old_invoice_ids_by_document.get(document_id, []),
            new_invoice_refs=refs_by_document.get(document_id, []),
        )
        if mapped_ref is None:
            unmapped_invoice_ids.append(old_invoice_id)
            next_invoice_entries.append(invoice)
            continue

        updated_invoice_ids.append(mapped_ref.invoice_id)
        next_invoice_entries.append(
            {
                **asdict(mapped_ref),
                "status": str(invoice.get("status") or mapped_ref.status),
            }
        )

    review_payload["invoices"] = next_invoice_entries
    review_payload["invoice_count"] = sum(
        1
        for invoice in next_invoice_entries
        if invoice.get("status", "pending") == "pending"
    )
    _atomic_write_json(review_index_path, review_payload)

    viewer_refs = [
        ProcessedInvoiceRef(
            invoice_id=str(invoice.get("invoice_id") or ""),
            title=str(invoice.get("title") or ""),
            document_id=str(invoice.get("document_id") or ""),
            source_batch_id=str(invoice.get("source_batch_id") or ""),
            source_file_name=str(invoice.get("source_file_name") or ""),
            status=str(invoice.get("status") or "pending"),
            process_id=str(invoice.get("process_id") or "")
            or _process_id_for_review_document(
                review_batch_dir=review_batch_dir,
                document_id=str(invoice.get("document_id") or ""),
                viewer_payload=viewer_payload,
            ),
            parent_invoice_record_id=str(
                invoice.get("parent_invoice_record_id") or ""
            ),
            source_page_numbers=[
                int(page_number)
                for page_number in invoice.get("source_page_numbers", [])
                if isinstance(page_number, int | float | str)
                and str(page_number).strip().isdigit()
            ],
            display_number=_positive_int(invoice.get("display_number")),
        )
        for invoice in next_invoice_entries
        if invoice.get("status", "pending") == "pending"
    ]
    _atomic_write_json(
        viewer_index_path,
        {
            "documents": _viewer_documents_for_invoice_refs(
                default_process_id="",
                invoice_refs=viewer_refs,
            )
        },
    )

    review_invoice_ids = {str(invoice.get("invoice_id") or "") for invoice in next_invoice_entries}
    for new_refs in refs_by_document.values():
        for new_ref in new_refs:
            if new_ref.invoice_id not in review_invoice_ids:
                new_invoice_ids_not_in_review.append(new_ref.invoice_id)

    return ReprocessReviewBatchResult(
        review_batch_id=str(review_payload.get("review_batch_id") or review_batch_dir.name),
        review_batch_base_path=review_batch_base_path,
        reprocessed_document_ids=sorted(target_document_ids),
        updated_invoice_ids=updated_invoice_ids,
        unmapped_invoice_ids=unmapped_invoice_ids,
        new_invoice_ids_not_in_review=sorted(new_invoice_ids_not_in_review),
    )


def _review_reprocess_source_path(
    source_batch_id: str,
    document_id: str,
    invoice_entries: list[dict],
    runtime_paths: RuntimePaths,
) -> Path:
    try:
        _, source_path = resolve_archived_source_for_origin(
            source_batch_id,
            document_id,
            runtime_paths,
        )
        return source_path
    except ValueError as origin_error:
        parent_record_ids = {
            str(invoice.get("parent_invoice_record_id") or "").strip()
            for invoice in invoice_entries
            if str(invoice.get("parent_invoice_record_id") or "").strip()
        }
        if len(parent_record_ids) != 1:
            raise origin_error

        registry_path = runtime_paths.invoice_registry_file
        if not registry_path.is_file():
            raise origin_error
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise origin_error

        parent_record_id = next(iter(parent_record_ids))
        parent_record = next(
            (
                record
                for record in registry.get("records", [])
                if isinstance(record, dict)
                and str(record.get("record_id") or "") == parent_record_id
            ),
            None,
        )
        if parent_record is None:
            raise origin_error

        relative_path = str(parent_record.get("archived_pdf_path") or "").strip()
        candidate = (runtime_paths.app_data_dir / relative_path).resolve()
        if (
            not relative_path
            or not _is_inside(candidate, runtime_paths.archive_invoices_dir)
            or not candidate.is_file()
        ):
            raise origin_error
        return candidate


def mutate_review_invoice_segmentation(
    payload: dict,
    runtime_paths: RuntimePaths | None = None,
) -> dict:
    paths = runtime_paths or get_runtime_paths()
    review_batch_base_path = _required_string(payload, "review_batch_base_path")
    document_id = _required_string(payload, "document_id")
    invoice_id = _required_string(payload, "invoice_id")
    operation = _required_string(payload, "operation")

    with _REVIEW_MUTATION_LOCK:
        recover_review_mutations(paths)
        review_batch_dir = review_batch_dir_from_base_path(
            review_batch_base_path,
            paths,
        )
        document_dir = _document_dir_for_review_document(
            review_batch_dir=review_batch_dir,
            document_id=document_id,
            runtime_paths=paths,
        )
        process_dir = document_dir.parent.parent
        process_index_path = process_dir / PROCESSING_INDEX_FILE_NAME
        process_payload = json.loads(process_index_path.read_text(encoding="utf-8"))
        review_states = _load_process_review_states(process_payload, paths)
        current_state = next(
            (
                state
                for state in review_states
                if state["base_path"] == review_batch_base_path
            ),
            None,
        )
        if current_state is None:
            raise ValueError("Revisione corrente non trovata")

        source_ref = _pending_review_invoice(
            current_state["review_payload"],
            document_id,
            invoice_id,
        )
        manifest_path = document_dir / "viewer_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if operation == "move_boundary":
            direction = _required_string(payload, "direction")
            result = move_boundary_page(manifest, invoice_id, direction)
            _require_pending_process_invoice(
                review_states,
                document_id,
                result.recipient_invoice_id,
            )
        elif operation == "split":
            after_position = _required_int(payload, "after_position")
            result = split_invoice(
                manifest,
                invoice_id,
                after_position,
                f"invoice-manual-{uuid4().hex}",
            )
        else:
            raise ValueError("Operazione di segmentazione non valida")

        manifest_updates = _process_manifest_updates(
            process_dir=process_dir,
            target_manifest_path=manifest_path,
            target_result=result,
        )
        display_projection = _display_projection(manifest_updates, process_dir)
        _apply_display_projection(review_states, display_projection)

        if result.removed_invoice_id:
            _remove_review_invoice(
                current_state["review_payload"],
                document_id,
                result.removed_invoice_id,
            )
        if result.created_invoice_id:
            _insert_split_review_invoice(
                current_state["review_payload"],
                source_ref,
                result.created_invoice_id,
                display_projection[(document_id, result.created_invoice_id)],
            )

        for state in review_states:
            _refresh_review_state(state)

        if result.created_invoice_id:
            _cascade_review_overflow(
                review_states=review_states,
                current_state=current_state,
                process_payload=process_payload,
                paths=paths,
            )

        selected_state = _review_state_for_invoice(
            review_states,
            document_id,
            result.selected_invoice_id,
        )
        if selected_state is None:
            raise RuntimeError("La fattura risultante non e presente nelle revisioni")

        process_payload["review_batch_count"] = len(
            process_payload.get("review_batches", [])
        )
        process_payload["invoice_count"] = max(
            0,
            int(process_payload.get("invoice_count") or 0)
            + result.numbering_delta,
        )
        updates: dict[Path, object] = {
            **manifest_updates,
            process_index_path: process_payload,
        }
        for state in review_states:
            updates[state["review_index_path"]] = state["review_payload"]
            updates[state["viewer_index_path"]] = state["viewer_payload"]

        affected_invoice_ids = {
            value
            for value in (
                invoice_id,
                result.recipient_invoice_id,
                result.removed_invoice_id,
                result.created_invoice_id,
            )
            if value
        }
        draft_store = _load_review_draft_store(paths)
        if _remove_review_drafts_from_store(
            draft_store,
            process_id=process_dir.name,
            document_id=document_id,
            invoice_ids=affected_invoice_ids,
        ):
            updates[_review_drafts_path(paths)] = draft_store
        _commit_review_mutation(paths, updates)
        response = {
            "ok": True,
            "operation": operation,
            "selected_batch_id": selected_state["base_path"],
            "selected_invoice_id": result.selected_invoice_id,
            "removed_invoice_id": result.removed_invoice_id,
            "created_invoice_id": result.created_invoice_id,
            "affected_invoice_ids": sorted(affected_invoice_ids),
        }
        try:
            record_segmentation_event(
                process_id=process_dir.name,
                document_id=document_id,
                operation=operation,
                request_payload={
                    key: value
                    for key, value in payload.items()
                    if key not in {"review_batch_base_path"}
                },
                result_payload=response,
                manifest=(
                    manifest_updates.get(manifest_path)
                    if isinstance(manifest_updates.get(manifest_path), dict)
                    else result.manifest
                ),
                runtime_paths=paths,
            )
        except Exception as error:
            record_capture_issue(
                process_id=process_dir.name,
                document_id=document_id,
                stage="segmentation_event",
                error=error,
                runtime_paths=paths,
            )
        return response


def list_review_invoice_drafts(
    runtime_paths: RuntimePaths | None = None,
) -> dict:
    paths = runtime_paths or get_runtime_paths()
    with _REVIEW_MUTATION_LOCK:
        store = _load_review_draft_store(paths)
        return {
            "schema_version": 1,
            "drafts": list(store["drafts"].values()),
        }


def save_review_invoice_draft(
    payload: dict,
    runtime_paths: RuntimePaths | None = None,
) -> dict:
    paths = runtime_paths or get_runtime_paths()
    review_batch_base_path = _required_string(payload, "review_batch_base_path")
    document_id = _required_string(payload, "document_id")
    invoice_id = _required_string(payload, "invoice_id")
    fields = _validated_review_draft_fields(payload.get("fields"))

    with _REVIEW_MUTATION_LOCK:
        recover_review_mutations(paths)
        review_batch_dir = review_batch_dir_from_base_path(
            review_batch_base_path,
            paths,
        )
        review_payload = json.loads(
            (review_batch_dir / "review_batch_index.json").read_text(encoding="utf-8")
        )
        _pending_review_invoice(review_payload, document_id, invoice_id)
        document_dir = _document_dir_for_review_document(
            review_batch_dir=review_batch_dir,
            document_id=document_id,
            runtime_paths=paths,
        )
        process_id = document_dir.parent.parent.name
        document_base_path = _runtime_manifest_base_path(document_dir, paths)
        draft_id = _review_draft_id(process_id, document_id, invoice_id)
        draft = {
            "draft_id": draft_id,
            "process_id": process_id,
            "document_id": document_id,
            "document_base_path": document_base_path,
            "invoice_id": invoice_id,
            "fields": fields,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        store = _load_review_draft_store(paths)
        store["drafts"][draft_id] = draft
        _atomic_write_json_durable(_review_drafts_path(paths), store)
        return draft


def delete_review_invoice_drafts(
    draft_ids: list[str],
    runtime_paths: RuntimePaths | None = None,
) -> int:
    paths = runtime_paths or get_runtime_paths()
    normalized_ids = {
        str(draft_id or "").strip()
        for draft_id in draft_ids
        if str(draft_id or "").strip()
    }
    if not normalized_ids:
        return 0

    with _REVIEW_MUTATION_LOCK:
        store = _load_review_draft_store(paths)
        deleted_count = 0
        for draft_id in normalized_ids:
            if store["drafts"].pop(draft_id, None) is not None:
                deleted_count += 1
        if deleted_count:
            _atomic_write_json_durable(_review_drafts_path(paths), store)
        return deleted_count


def review_draft_id_for_invoice_payload(invoice: dict) -> str:
    document_base_path = str(invoice.get("basePath") or "").strip()
    document_id = str(invoice.get("documentId") or "").strip()
    invoice_id = str(invoice.get("manifestInvoiceId") or "").strip()
    match = re.fullmatch(
        r"/runtime/manifests/([^/]+)/documents/([^/]+)",
        document_base_path,
    )
    if not match or not document_id or match.group(2) != document_id or not invoice_id:
        return ""
    return _review_draft_id(match.group(1), document_id, invoice_id)


def _load_process_review_states(process_payload: dict, paths: RuntimePaths) -> list[dict]:
    states = []
    for batch in process_payload.get("review_batches", []):
        if not isinstance(batch, dict):
            continue
        base_path = str(batch.get("base_path") or "")
        try:
            batch_dir = review_batch_dir_from_base_path(base_path, paths)
            review_index_path = batch_dir / "review_batch_index.json"
            viewer_index_path = batch_dir / "viewer_index.json"
            review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))
            viewer_payload = json.loads(viewer_index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        states.append(
            {
                "batch_ref": batch,
                "base_path": base_path,
                "batch_dir": batch_dir,
                "review_index_path": review_index_path,
                "viewer_index_path": viewer_index_path,
                "review_payload": review_payload,
                "viewer_payload": viewer_payload,
            }
        )
    return states


def _pending_review_invoice(
    review_payload: dict,
    document_id: str,
    invoice_id: str,
) -> dict:
    for invoice in review_payload.get("invoices", []):
        if (
            isinstance(invoice, dict)
            and str(invoice.get("document_id") or "") == document_id
            and str(invoice.get("invoice_id") or "") == invoice_id
            and invoice.get("status", "pending") == "pending"
        ):
            return invoice
    raise ValueError("Fattura non modificabile nella revisione corrente")


def _require_pending_process_invoice(
    review_states: list[dict],
    document_id: str,
    invoice_id: str,
) -> None:
    if _review_state_for_invoice(review_states, document_id, invoice_id) is None:
        raise ValueError("La fattura adiacente non e piu da revisionare")


def _review_state_for_invoice(
    review_states: list[dict],
    document_id: str,
    invoice_id: str,
) -> dict | None:
    for state in review_states:
        try:
            _pending_review_invoice(
                state["review_payload"],
                document_id,
                invoice_id,
            )
        except ValueError:
            continue
        return state
    return None


def _process_manifest_updates(
    process_dir: Path,
    target_manifest_path: Path,
    target_result: SegmentationEditResult,
) -> dict[Path, object]:
    updates: dict[Path, object] = {}
    for manifest_path in sorted(
        (process_dir / "documents").glob("doc_*/viewer_manifest.json")
    ):
        manifest = (
            target_result.manifest
            if manifest_path.resolve() == target_manifest_path.resolve()
            else json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        if target_result.numbering_delta:
            excluded_ids = (
                {target_result.created_invoice_id}
                if target_result.created_invoice_id
                else set()
            )
            shift_invoice_display_numbers(
                manifest,
                threshold=target_result.numbering_threshold,
                delta=target_result.numbering_delta,
                excluded_invoice_ids=excluded_ids,
            )
        else:
            for index, invoice in enumerate(manifest.get("invoices", []), start=1):
                if isinstance(invoice, dict):
                    set_invoice_display_number(
                        invoice,
                        invoice_display_number(invoice, index),
                    )
        updates[manifest_path] = manifest
    return updates


def _display_projection(
    manifest_updates: dict[Path, object],
    process_dir: Path,
) -> dict[tuple[str, str], tuple[int, str]]:
    projection = {}
    for manifest_path, raw_manifest in manifest_updates.items():
        if not isinstance(raw_manifest, dict):
            continue
        document_id = manifest_path.parent.name
        for index, invoice in enumerate(raw_manifest.get("invoices", []), start=1):
            if not isinstance(invoice, dict):
                continue
            invoice_id = str(invoice.get("invoice_id") or "")
            if not invoice_id:
                continue
            display_number = invoice_display_number(invoice, index)
            projection[(document_id, invoice_id)] = (
                display_number,
                str(invoice.get("title") or f"Fattura {display_number:03d}"),
            )
    return projection


def _apply_display_projection(
    review_states: list[dict],
    projection: dict[tuple[str, str], tuple[int, str]],
) -> None:
    for state in review_states:
        for invoice in state["review_payload"].get("invoices", []):
            if not isinstance(invoice, dict):
                continue
            key = (
                str(invoice.get("document_id") or ""),
                str(invoice.get("invoice_id") or ""),
            )
            display = projection.get(key)
            if display is not None:
                invoice["display_number"], invoice["title"] = display


def _remove_review_invoice(
    review_payload: dict,
    document_id: str,
    invoice_id: str,
) -> None:
    review_payload["invoices"] = [
        invoice
        for invoice in review_payload.get("invoices", [])
        if not (
            isinstance(invoice, dict)
            and str(invoice.get("document_id") or "") == document_id
            and str(invoice.get("invoice_id") or "") == invoice_id
        )
    ]


def _insert_split_review_invoice(
    review_payload: dict,
    source_ref: dict,
    created_invoice_id: str,
    display: tuple[int, str],
) -> None:
    invoices = review_payload.get("invoices", [])
    source_index = next(
        index
        for index, invoice in enumerate(invoices)
        if invoice is source_ref
    )
    display_number, title = display
    created_ref = {
        **source_ref,
        "invoice_id": created_invoice_id,
        "display_number": display_number,
        "title": title,
        "status": "pending",
    }
    invoices.insert(source_index + 1, created_ref)


def _refresh_review_state(state: dict) -> None:
    pending_refs = [
        invoice
        for invoice in state["review_payload"].get("invoices", [])
        if isinstance(invoice, dict)
        and invoice.get("status", "pending") == "pending"
    ]
    state["review_payload"]["invoice_count"] = len(pending_refs)
    state["batch_ref"]["invoice_count"] = len(pending_refs)
    state["viewer_payload"] = {
        "documents": _viewer_documents_for_invoice_refs(
            default_process_id="",
            invoice_refs=[_processed_invoice_ref_from_payload(invoice) for invoice in pending_refs],
        )
    }


def _processed_invoice_ref_from_payload(invoice: dict) -> ProcessedInvoiceRef:
    return ProcessedInvoiceRef(
        invoice_id=str(invoice.get("invoice_id") or ""),
        title=str(invoice.get("title") or ""),
        document_id=str(invoice.get("document_id") or ""),
        source_batch_id=str(invoice.get("source_batch_id") or ""),
        source_file_name=str(invoice.get("source_file_name") or ""),
        status=str(invoice.get("status") or "pending"),
        process_id=str(invoice.get("process_id") or ""),
        parent_invoice_record_id=str(invoice.get("parent_invoice_record_id") or ""),
        source_page_numbers=[
            int(page_number)
            for page_number in invoice.get("source_page_numbers", [])
            if str(page_number).strip().isdigit()
        ],
        display_number=_positive_int(invoice.get("display_number")),
    )


def _cascade_review_overflow(
    review_states: list[dict],
    current_state: dict,
    process_payload: dict,
    paths: RuntimePaths,
) -> None:
    state_index = review_states.index(current_state)
    while state_index < len(review_states):
        state = review_states[state_index]
        pending = [
            invoice
            for invoice in state["review_payload"].get("invoices", [])
            if isinstance(invoice, dict)
            and invoice.get("status", "pending") == "pending"
        ]
        if len(pending) <= REVIEW_BATCH_SIZE:
            break

        overflow_invoice = pending[-1]
        state["review_payload"]["invoices"].remove(overflow_invoice)
        if state_index + 1 >= len(review_states):
            review_states.append(
                _new_review_state(process_payload, paths)
            )
        next_state = review_states[state_index + 1]
        next_state["review_payload"].setdefault("invoices", []).insert(
            0,
            overflow_invoice,
        )
        _refresh_review_state(state)
        _refresh_review_state(next_state)
        state_index += 1


def _new_review_state(process_payload: dict, paths: RuntimePaths) -> dict:
    batch_number = _next_global_review_batch_number(paths)
    batch_id = f"review_batch_{batch_number:04d}"
    base_path = f"/runtime/manifests/_review_queue/{batch_id}"
    batch_dir = _global_review_queue_dir(paths) / batch_id
    batch_ref = {
        "id": batch_id,
        "label": f"Revisione {batch_number:03d}",
        "base_path": base_path,
        "invoice_count": 0,
    }
    process_payload.setdefault("review_batches", []).append(batch_ref)
    return {
        "batch_ref": batch_ref,
        "base_path": base_path,
        "batch_dir": batch_dir,
        "review_index_path": batch_dir / "review_batch_index.json",
        "viewer_index_path": batch_dir / "viewer_index.json",
        "review_payload": {
            "review_batch_id": batch_id,
            "invoice_count": 0,
            "invoices": [],
        },
        "viewer_payload": {"documents": []},
    }


def review_batch_dir_from_base_path(
    review_batch_base_path: str,
    runtime_paths: RuntimePaths | None = None,
    *,
    must_exist: bool = True,
) -> Path:
    paths = runtime_paths or get_runtime_paths()
    prefix = "/runtime/manifests/"
    if not review_batch_base_path.startswith(prefix):
        raise ValueError("Il batch selezionato non e' un batch runtime")

    relative_path = Path(review_batch_base_path.removeprefix(prefix).lstrip("/"))
    candidate = (paths.manifests_dir / relative_path).resolve()
    manifests_dir = paths.manifests_dir.resolve()
    if candidate != manifests_dir and manifests_dir not in candidate.parents:
        raise ValueError("Percorso batch runtime non valido")
    if must_exist and not candidate.is_dir():
        raise ValueError("Batch di revisione non trovato")
    return candidate


def _required_string(payload: dict, key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Campo obbligatorio mancante: {key}")
    return value


def _required_int(payload: dict, key: str) -> int:
    value = payload.get(key)
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Campo numerico obbligatorio non valido: {key}") from error


def retire_completed_review_batches(
    runtime_paths: RuntimePaths | None = None,
) -> list[Path]:
    """Retire review projections that no longer contain pending invoices."""
    paths = runtime_paths or get_runtime_paths()
    review_queue_dir = _global_review_queue_dir(paths)
    if not review_queue_dir.is_dir():
        return []

    retired_paths: list[Path] = []
    for review_batch_dir in sorted(review_queue_dir.glob("review_batch_*")):
        if not review_batch_dir.is_dir():
            continue
        review_index_path = review_batch_dir / "review_batch_index.json"
        try:
            review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if _retire_review_batch_if_complete(
            runtime_paths=paths,
            review_batch_dir=review_batch_dir,
            review_payload=review_payload,
        ):
            retired_paths.append(review_batch_dir)

    return retired_paths


def _retire_review_batch_if_complete(
    *,
    runtime_paths: RuntimePaths,
    review_batch_dir: Path,
    review_payload: dict,
) -> bool:
    invoices = [
        invoice
        for invoice in review_payload.get("invoices", [])
        if isinstance(invoice, dict)
    ]
    if not invoices or any(
        invoice.get("status", "pending") not in {"saved", "discarded"}
        for invoice in invoices
    ):
        return False
    if not _is_inside(review_batch_dir, runtime_paths.manifests_dir):
        return False

    review_batch_id = str(
        review_payload.get("review_batch_id") or review_batch_dir.name
    ).strip()
    if not review_batch_id or review_batch_id != review_batch_dir.name:
        return False
    if not _record_retired_review_batch(
        runtime_paths,
        review_batch_id=review_batch_id,
        review_batch_dir=review_batch_dir,
    ):
        return False

    try:
        draft_store = _load_review_draft_store(runtime_paths)
        drafts_changed = False
        for invoice in invoices:
            draft_id = _review_draft_id(
                str(invoice.get("process_id") or ""),
                str(invoice.get("document_id") or ""),
                str(invoice.get("invoice_id") or ""),
            )
            if draft_store["drafts"].pop(draft_id, None) is not None:
                drafts_changed = True
        if drafts_changed:
            _atomic_write_json_durable(_review_drafts_path(runtime_paths), draft_store)
    except (OSError, ValueError, json.JSONDecodeError):
        return False

    shutil.rmtree(review_batch_dir, ignore_errors=True)
    return not review_batch_dir.exists()


def _record_retired_review_batch(
    runtime_paths: RuntimePaths,
    *,
    review_batch_id: str,
    review_batch_dir: Path,
) -> bool:
    matching_process_indexes: list[tuple[Path, dict]] = []
    for index_path in runtime_paths.manifests_dir.glob(
        f"process_*/{PROCESSING_INDEX_FILE_NAME}"
    ):
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if any(
            isinstance(batch, dict)
            and str(batch.get("id") or "") == review_batch_id
            and _review_ref_points_to_dir(batch, review_batch_dir, runtime_paths)
            for batch in payload.get("review_batches", [])
        ):
            matching_process_indexes.append((index_path, payload))

    if len(matching_process_indexes) != 1:
        return False

    try:
        for index_path, payload in matching_process_indexes:
            retired_ids = [
                str(batch_id)
                for batch_id in payload.get("retired_review_batch_ids", [])
                if str(batch_id).strip()
            ]
            if review_batch_id not in retired_ids:
                retired_ids.append(review_batch_id)
                payload["retired_review_batch_ids"] = retired_ids
                _atomic_write_json_durable(index_path, payload)
    except OSError:
        return False
    return True


def _review_ref_points_to_dir(
    batch: dict,
    review_batch_dir: Path,
    runtime_paths: RuntimePaths,
) -> bool:
    base_path = str(batch.get("base_path") or "").strip()
    if not base_path:
        return False
    try:
        return review_batch_dir_from_base_path(
            base_path,
            runtime_paths,
            must_exist=False,
        ).resolve() == review_batch_dir.resolve()
    except ValueError:
        return False


def _positive_int(value: object) -> int:
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed_value if parsed_value > 0 else 0


def _cleanup_closed_invoice_working_files(
    runtime_paths: RuntimePaths,
    review_batch_dir: Path,
    document_id: str,
    source_batch_id: str,
    document_dir: Path | None = None,
) -> None:
    if not document_id or not source_batch_id:
        return

    if document_dir is None:
        try:
            document_working_dir = _document_dir_for_review_document(
                review_batch_dir=review_batch_dir,
                document_id=document_id,
                runtime_paths=runtime_paths,
            )
        except ValueError:
            document_working_dir = review_batch_dir.parent.parent / "documents" / document_id
    else:
        document_working_dir = document_dir
    process_dir = document_working_dir.parent.parent
    if _has_pending_invoice_for_document(process_dir, document_id):
        return

    if _is_inside(document_working_dir, runtime_paths.manifests_dir):
        shutil.rmtree(document_working_dir, ignore_errors=True)

    # Source PDFs belong to the independent source archive after OCR finalization.
    # Closing one review item must only release its derived OCR working directory.


def _has_pending_invoice_for_document(process_dir: Path, document_id: str) -> bool:
    for review_index_path in _review_index_paths_for_process(process_dir):
        try:
            payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if _payload_has_pending_invoice_for_document(payload, document_id):
            return True

    return False


def _has_pending_invoice_for_source_document(
    runtime_paths: RuntimePaths,
    source_batch_id: str,
    document_id: str,
) -> bool:
    if not runtime_paths.manifests_dir.exists():
        return False

    for review_index_path in _all_review_index_paths(runtime_paths):
        try:
            payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        for invoice in payload.get("invoices", []):
            if (
                isinstance(invoice, dict)
                and str(invoice.get("source_batch_id") or "") == source_batch_id
                and str(invoice.get("document_id") or "") == document_id
                and invoice.get("status", "pending") == "pending"
            ):
                return True

    return False


def _review_index_paths_for_process(process_dir: Path) -> list[Path]:
    process_id = process_dir.name
    legacy_paths = list((process_dir / "review_batches").glob("*/review_batch_index.json"))
    global_paths: list[Path] = []
    review_queue_dir = process_dir.parent / "_review_queue"
    if review_queue_dir.exists():
        for review_index_path in review_queue_dir.glob("*/review_batch_index.json"):
            try:
                payload = json.loads(review_index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if any(
                isinstance(invoice, dict)
                and str(invoice.get("process_id") or "") == process_id
                for invoice in payload.get("invoices", [])
            ):
                global_paths.append(review_index_path)
    return [*legacy_paths, *global_paths]


def _all_review_index_paths(runtime_paths: RuntimePaths) -> list[Path]:
    return [
        *runtime_paths.manifests_dir.glob("*/review_batches/*/review_batch_index.json"),
        *(_global_review_queue_dir(runtime_paths).glob("*/review_batch_index.json")),
    ]


def _payload_has_pending_invoice_for_document(
    payload: dict,
    document_id: str,
) -> bool:
    return any(
        isinstance(invoice, dict)
        and str(invoice.get("document_id") or "") == document_id
        and invoice.get("status", "pending") == "pending"
        for invoice in payload.get("invoices", [])
    )


def _document_manifest_path_for_review_document(
    review_batch_dir: Path,
    document_id: str,
    runtime_paths: RuntimePaths,
) -> Path:
    return (
        _document_dir_for_review_document(
            review_batch_dir=review_batch_dir,
            document_id=document_id,
            runtime_paths=runtime_paths,
        )
        / "viewer_manifest.json"
    )


def _document_dir_for_review_document(
    review_batch_dir: Path,
    document_id: str,
    runtime_paths: RuntimePaths,
) -> Path:
    viewer_index_path = review_batch_dir / "viewer_index.json"
    try:
        viewer_payload = json.loads(viewer_index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Indice visualizzazione revisione non leggibile") from error

    for document in viewer_payload.get("documents", []):
        if not isinstance(document, dict):
            continue
        if str(document.get("id") or "") != document_id:
            continue
        base_path = str(document.get("base_path") or "")
        prefix = "/runtime/manifests/"
        if not base_path.startswith(prefix):
            break
        relative_path = Path(base_path.removeprefix(prefix).lstrip("/"))
        candidate = (runtime_paths.manifests_dir / relative_path).resolve()
        if not _is_inside(candidate, runtime_paths.manifests_dir):
            break
        if candidate.is_dir():
            return candidate

    legacy_candidate = (review_batch_dir.parent.parent / "documents" / document_id).resolve()
    if _is_inside(legacy_candidate, runtime_paths.manifests_dir) and legacy_candidate.is_dir():
        return legacy_candidate

    raise ValueError(f"Documento revisione non trovato: {document_id}")


def _process_id_for_review_document(
    review_batch_dir: Path,
    document_id: str,
    viewer_payload: dict,
) -> str:
    prefix = "/runtime/manifests/"
    for document in viewer_payload.get("documents", []):
        if not isinstance(document, dict):
            continue
        if str(document.get("id") or "") != document_id:
            continue
        base_path = str(document.get("base_path") or "")
        if not base_path.startswith(prefix):
            continue
        relative_parts = Path(base_path.removeprefix(prefix).lstrip("/")).parts
        if len(relative_parts) >= 3 and relative_parts[1] == "documents":
            return relative_parts[0]

    if review_batch_dir.parent.name == "review_batches":
        return review_batch_dir.parent.parent.name

    return ""


def _created_at_sort_key(created_at: str) -> float:
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except ValueError:
        return 0.0


def review_batch_base_path_by_number(
    review_number: int,
    runtime_paths: RuntimePaths | None = None,
) -> str:
    if review_number < 1:
        raise ValueError("Numero revisione non valido")

    paths = runtime_paths or get_runtime_paths()
    expected_label = f"Revisione {review_number:03d}"
    for review_batch in list_review_batches(paths):
        if review_batch.label == expected_label:
            return review_batch.base_path

    raise ValueError(f"Revisione {review_number:03d} non trovata")


def _mapped_reprocessed_invoice_ref(
    old_invoice_id: str,
    old_invoice_ids: list[str],
    new_invoice_refs: list[ProcessedInvoiceRef],
) -> ProcessedInvoiceRef | None:
    try:
        old_index = old_invoice_ids.index(old_invoice_id)
    except ValueError:
        return next(
            (
                new_ref
                for new_ref in new_invoice_refs
                if new_ref.invoice_id == old_invoice_id
            ),
            None,
        )

    if old_index >= len(new_invoice_refs):
        return None
    return new_invoice_refs[old_index]


def _preserve_reprocessed_manifest_invoice_ids(
    manifest: dict,
    old_invoice_ids: list[str],
) -> None:
    invoices = [
        invoice
        for invoice in manifest.get("invoices", [])
        if isinstance(invoice, dict)
    ]
    for index, invoice in enumerate(invoices):
        if index >= len(old_invoice_ids):
            break
        invoice["invoice_id"] = old_invoice_ids[index]


def _rebase_review_drafts_after_reprocess(
    *,
    draft_store: dict,
    process_id: str,
    document_id: str,
    old_manifest: dict,
    new_manifest: dict,
) -> bool:
    old_invoices = [
        invoice
        for invoice in old_manifest.get("invoices", [])
        if isinstance(invoice, dict)
    ]
    new_invoices = [
        invoice
        for invoice in new_manifest.get("invoices", [])
        if isinstance(invoice, dict)
    ]
    changed = False

    for old_invoice, new_invoice in zip(old_invoices, new_invoices):
        invoice_id = str(old_invoice.get("invoice_id") or "")
        if not invoice_id:
            continue
        draft_id = _review_draft_id(process_id, document_id, invoice_id)
        draft = draft_store.get("drafts", {}).get(draft_id)
        if not isinstance(draft, dict):
            continue

        rebased_fields = _rebase_review_draft_fields(
            old_fields=old_invoice.get("fields", []),
            new_fields=new_invoice.get("fields", []),
            draft_fields=draft.get("fields", []),
        )
        if rebased_fields != draft.get("fields"):
            draft["fields"] = rebased_fields
            changed = True

    return changed


def _rebase_review_draft_fields(
    *,
    old_fields: object,
    new_fields: object,
    draft_fields: object,
) -> list[dict]:
    old_by_kind = _fields_by_kind(old_fields)
    new_by_kind = _fields_by_kind(new_fields)
    draft_by_kind = _fields_by_kind(draft_fields)
    rebased_fields = []

    for kind, new_field in new_by_kind.items():
        old_field = old_by_kind.get(kind)
        draft_field = draft_by_kind.get(kind)
        if (
            draft_field is not None
            and old_field is not None
            and draft_field.get("value") != old_field.get("value")
        ):
            rebased_fields.append(draft_field)
        else:
            rebased_fields.append(new_field)

    for kind, draft_field in draft_by_kind.items():
        if kind not in new_by_kind:
            rebased_fields.append(draft_field)

    return rebased_fields


def _fields_by_kind(raw_fields: object) -> dict[str, dict]:
    if not isinstance(raw_fields, list):
        return {}
    return {
        str(field.get("kind") or ""): field
        for field in raw_fields
        if isinstance(field, dict) and str(field.get("kind") or "")
    }


def _replace_directory_atomically(working_dir: Path, target_dir: Path) -> None:
    backup_dir = target_dir.with_name(f"{target_dir.name}.reprocess_backup")
    shutil.rmtree(backup_dir, ignore_errors=True)
    try:
        target_dir.replace(backup_dir)
        working_dir.replace(target_dir)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if backup_dir.exists():
            backup_dir.replace(target_dir)
        raise
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _is_inside(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _append_global_review_batches(
    process_id: str,
    runtime_paths: RuntimePaths,
    invoice_refs: list[ProcessedInvoiceRef],
    review_batch_size: int,
) -> list[ReviewBatchRef]:
    if review_batch_size < 1:
        raise ValueError("review_batch_size must be >= 1")

    review_batches_dir = _global_review_queue_dir(runtime_paths)
    review_batches_dir.mkdir(parents=True, exist_ok=True)

    review_batches: list[ReviewBatchRef] = []
    first_batch_number = _next_global_review_batch_number(runtime_paths)
    for batch_index, start_index in enumerate(
        range(0, len(invoice_refs), review_batch_size),
        start=first_batch_number,
    ):
        batch_invoice_refs = invoice_refs[start_index : start_index + review_batch_size]
        review_batch_id = f"review_batch_{batch_index:04d}"
        review_batch_dir = review_batches_dir / review_batch_id
        review_batch_dir.mkdir(parents=True, exist_ok=True)
        base_path = f"/runtime/manifests/_review_queue/{review_batch_id}"
        viewer_index = {
            "documents": _viewer_documents_for_invoice_refs(
                default_process_id=process_id,
                invoice_refs=batch_invoice_refs,
            )
        }
        (review_batch_dir / "viewer_index.json").write_text(
            json.dumps(viewer_index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (review_batch_dir / "review_batch_index.json").write_text(
            json.dumps(
                {
                    "review_batch_id": review_batch_id,
                    "invoice_count": len(batch_invoice_refs),
                    "invoices": [asdict(invoice) for invoice in batch_invoice_refs],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        first_number = start_index + 1
        last_number = start_index + len(batch_invoice_refs)
        review_batches.append(
            ReviewBatchRef(
                id=review_batch_id,
                label=f"Revisione {batch_index:03d}",
                base_path=base_path,
                invoice_count=len(batch_invoice_refs),
            )
        )

    return review_batches


def _global_review_queue_dir(runtime_paths: RuntimePaths) -> Path:
    return runtime_paths.manifests_dir / "_review_queue"


def _is_processing_run_directory(path: Path) -> bool:
    return (
        path.is_dir()
        and re.fullmatch(r"process_[A-Za-z0-9_-]+", path.name) is not None
    )


def _review_batch_projection_is_valid(
    runtime_paths: RuntimePaths,
    review_batch: ReviewBatchRef,
) -> bool:
    try:
        review_batch_dir = review_batch_dir_from_base_path(
            review_batch.base_path,
            runtime_paths,
        )
        review_payload = json.loads(
            (review_batch_dir / "review_batch_index.json").read_text(encoding="utf-8")
        )
        viewer_payload = json.loads(
            (review_batch_dir / "viewer_index.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(review_payload, dict)
        and isinstance(review_payload.get("invoices"), list)
        and isinstance(viewer_payload, dict)
        and isinstance(viewer_payload.get("documents"), list)
    )


def _recoverable_process_invoice_refs(
    run_index: ProcessingRunIndex,
    process_dir: Path,
    runtime_paths: RuntimePaths,
) -> list[ProcessedInvoiceRef]:
    documents_dir = process_dir / "documents"
    if not documents_dir.is_dir():
        raise ValueError("Processo senza documenti recuperabili")

    invoice_refs: list[ProcessedInvoiceRef] = []
    for document_dir in sorted(documents_dir.iterdir(), key=lambda path: path.name):
        if not is_document_processing_complete(document_dir):
            continue
        archived_source, _source_path = resolve_archived_source_for_origin(
            run_index.source_batch_id,
            document_dir.name,
            runtime_paths,
        )
        invoice_refs.extend(
            _invoice_refs_from_manifest(
                manifest_path=document_dir / "viewer_manifest.json",
                document_id=document_dir.name,
                source_batch_id=run_index.source_batch_id,
                source_file_name=archived_source.original_file_name,
                process_id=run_index.process_id,
            )
        )
    return invoice_refs


def _finalized_review_invoice_keys(runtime_paths: RuntimePaths) -> set[tuple[str, str]]:
    registry_path = runtime_paths.invoice_registry_file
    if not registry_path.is_file():
        return set()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    keys: set[tuple[str, str]] = set()
    for record in payload.get("records", []):
        if (
            not isinstance(record, dict)
            or record.get("status") not in {"saved", "discarded"}
        ):
            continue
        invoice = record.get("invoice")
        if not isinstance(invoice, dict):
            continue
        invoice_id = str(
            invoice.get("manifestInvoiceId") or invoice.get("invoiceId") or ""
        ).strip()
        review_base_path = str(record.get("review_batch_base_path") or "").strip()
        if invoice_id and review_base_path:
            keys.add((review_base_path, invoice_id))
    return keys


def _write_recovered_review_batch(
    runtime_paths: RuntimePaths,
    review_batch: ReviewBatchRef,
    process_id: str,
    invoice_refs: list[ProcessedInvoiceRef],
) -> None:
    review_batch_dir = review_batch_dir_from_base_path(
        review_batch.base_path,
        runtime_paths,
        must_exist=False,
    )
    review_batch_dir.parent.mkdir(parents=True, exist_ok=True)
    working_dir = review_batch_dir.with_name(
        f".{review_batch_dir.name}.recovering-{uuid4().hex}"
    )
    working_dir.mkdir(parents=True)
    try:
        _atomic_write_json(
            working_dir / "viewer_index.json",
            {
                "documents": _viewer_documents_for_invoice_refs(
                    default_process_id=process_id,
                    invoice_refs=invoice_refs,
                )
            },
        )
        _atomic_write_json(
            working_dir / "review_batch_index.json",
            {
                "review_batch_id": review_batch.id,
                "invoice_count": len(invoice_refs),
                "invoices": [asdict(invoice) for invoice in invoice_refs],
            },
        )
        if review_batch_dir.exists():
            _replace_directory_atomically(working_dir, review_batch_dir)
        else:
            working_dir.replace(review_batch_dir)
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)


def _next_global_review_batch_number(runtime_paths: RuntimePaths) -> int:
    stored_batches = _stored_review_batches(runtime_paths)
    max_number = 0
    for batch in stored_batches:
        number = _review_batch_number_from_ref(batch)
        if number > max_number:
            max_number = number
    return max(max_number, len(stored_batches)) + 1


def _stored_review_batches(runtime_paths: RuntimePaths) -> list[ReviewBatchRef]:
    stored_batches: list[ReviewBatchRef] = []
    for index_path in runtime_paths.manifests_dir.glob(f"*/{PROCESSING_INDEX_FILE_NAME}"):
        try:
            run_index = load_processing_run_index(index_path.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not is_processing_run_complete(run_index, index_path.parent):
            continue
        stored_batches.extend(run_index.review_batches)
    return stored_batches


def _review_batch_number_from_ref(review_batch: ReviewBatchRef) -> int:
    for value in (review_batch.base_path, review_batch.id, review_batch.label):
        match = re.search(r"(?:review_batch_|Revisione\s+)(\d+)", value)
        if match:
            return int(match.group(1))
    return 0


def _review_batch_with_current_count(
    runtime_paths: RuntimePaths,
    review_batch: ReviewBatchRef,
) -> ReviewBatchRef | None:
    try:
        review_batch_dir = review_batch_dir_from_base_path(
            review_batch.base_path,
            runtime_paths,
        )
        review_payload = json.loads(
            (review_batch_dir / "review_batch_index.json").read_text(
                encoding="utf-8"
            )
        )
        viewer_payload = json.loads(
            (review_batch_dir / "viewer_index.json").read_text(encoding="utf-8")
        )
        if not isinstance(review_payload, dict) or not isinstance(viewer_payload, dict):
            return None
        if not isinstance(viewer_payload.get("documents"), list):
            return None
        active_count = sum(
            1
            for invoice in review_payload.get("invoices", [])
            if isinstance(invoice, dict)
            and invoice.get("status", "pending") == "pending"
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    if active_count == 0:
        return None

    batch_number = _review_batch_number_from_ref(review_batch)
    label = (
        f"Revisione {batch_number:03d}"
        if batch_number > 0
        else review_batch.label
    )

    return ReviewBatchRef(
        id=review_batch.id,
        label=label,
        base_path=review_batch.base_path,
        invoice_count=active_count,
    )


def _atomic_write_json(output_path: Path, payload: object) -> None:
    temporary_path = output_path.with_name(f".{output_path.name}.writing")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def _review_drafts_path(runtime_paths: RuntimePaths) -> Path:
    return runtime_paths.manifests_dir / REVIEW_DRAFTS_FILE_NAME


def _empty_review_draft_store() -> dict:
    return {"schema_version": 1, "drafts": {}}


def _load_review_draft_store(runtime_paths: RuntimePaths) -> dict:
    path = _review_drafts_path(runtime_paths)
    if not path.exists():
        return _empty_review_draft_store()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("drafts"), dict):
        raise ValueError("Archivio bozze revisione non valido")
    return payload


def _review_draft_id(process_id: str, document_id: str, invoice_id: str) -> str:
    return f"{process_id}:{document_id}:{invoice_id}"


def _runtime_manifest_base_path(document_dir: Path, runtime_paths: RuntimePaths) -> str:
    relative_path = document_dir.resolve().relative_to(
        runtime_paths.manifests_dir.resolve()
    )
    return f"/runtime/manifests/{relative_path.as_posix()}"


def _validated_review_draft_fields(raw_fields: object) -> list[dict]:
    if not isinstance(raw_fields, list):
        raise ValueError("Campi bozza mancanti")
    allowed_keys = {
        "kind",
        "label",
        "value",
        "score",
        "ocr_confidence",
        "page",
        "bbox",
        "role",
        "registry_id",
        "registry_source_row",
        "registry_match_group",
        "registry_replaced_value",
        "review_interaction",
        "review_value_origin",
        "review_action_id",
        "review_source_field_kind",
        "review_registry_snapshot",
    }
    fields: list[dict] = []
    seen_kinds: set[str] = set()
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            raise ValueError("Campo bozza non valido")
        kind = str(raw_field.get("kind") or "").strip()
        if not kind or kind in seen_kinds:
            raise ValueError("Identita campo bozza non valida")
        seen_kinds.add(kind)
        field = {
            key: value
            for key, value in raw_field.items()
            if key in allowed_keys
        }
        field["kind"] = kind
        field["label"] = str(field.get("label") or "").strip()
        field["value"] = str(field.get("value") or "")
        fields.append(field)
    return fields


def _remove_review_drafts_from_store(
    store: dict,
    *,
    process_id: str,
    document_id: str,
    invoice_ids: set[str],
) -> bool:
    drafts = store["drafts"]
    draft_ids = {
        _review_draft_id(process_id, document_id, invoice_id)
        for invoice_id in invoice_ids
    }
    removed = False
    for draft_id in draft_ids:
        if drafts.pop(draft_id, None) is not None:
            removed = True
    return removed


def _commit_review_mutation(
    runtime_paths: RuntimePaths,
    updates: dict[Path, object],
) -> None:
    manifests_root = runtime_paths.manifests_dir.resolve()
    entries = []
    for target_path, payload in updates.items():
        resolved_target = target_path.resolve()
        if not _is_inside(resolved_target, manifests_root):
            raise ValueError("La transazione contiene un percorso non valido")
        entries.append(
            {
                "relative_path": resolved_target.relative_to(manifests_root).as_posix(),
                "payload": payload,
            }
        )

    transaction_dir = manifests_root / REVIEW_MUTATIONS_DIR_NAME
    transaction_dir.mkdir(parents=True, exist_ok=True)
    journal_path = transaction_dir / f"mutation_{uuid4().hex}.json"
    journal_payload = {
        "schema_version": 1,
        "state": "prepared",
        "updates": entries,
    }
    _atomic_write_json_durable(journal_path, journal_payload)
    _apply_review_mutation_journal(runtime_paths, journal_payload)
    journal_path.unlink(missing_ok=True)


def recover_review_mutations(
    runtime_paths: RuntimePaths | None = None,
) -> int:
    paths = runtime_paths or get_runtime_paths()
    transaction_dir = paths.manifests_dir / REVIEW_MUTATIONS_DIR_NAME
    if not transaction_dir.exists():
        return 0

    recovered_count = 0
    with _REVIEW_MUTATION_LOCK:
        for journal_path in sorted(transaction_dir.glob("mutation_*.json")):
            try:
                journal_payload = json.loads(journal_path.read_text(encoding="utf-8"))
                _apply_review_mutation_journal(paths, journal_payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            journal_path.unlink(missing_ok=True)
            recovered_count += 1
    return recovered_count


def _apply_review_mutation_journal(
    runtime_paths: RuntimePaths,
    journal_payload: dict,
) -> None:
    if journal_payload.get("state") != "prepared":
        raise ValueError("Stato transazione revisione non valido")

    manifests_root = runtime_paths.manifests_dir.resolve()
    updates = journal_payload.get("updates")
    if not isinstance(updates, list):
        raise ValueError("Transazione revisione senza aggiornamenti")

    validated_updates = []
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Aggiornamento transazione non valido")
        relative_path = Path(str(update.get("relative_path") or ""))
        target_path = (manifests_root / relative_path).resolve()
        if not _is_inside(target_path, manifests_root):
            raise ValueError("Percorso transazione revisione non valido")
        validated_updates.append((target_path, update.get("payload")))

    for target_path, payload in validated_updates:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json_durable(target_path, payload)


def _atomic_write_json_durable(output_path: Path, payload: object) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.writing")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary_path.replace(output_path)


def _cleanup_working_document_dirs(documents_dir: Path) -> None:
    if not documents_dir.exists():
        return
    for child in documents_dir.iterdir():
        if child.is_dir() and child.name.endswith(DOCUMENT_WORKING_SUFFIX):
            shutil.rmtree(child, ignore_errors=True)


def _viewer_documents_for_invoice_refs(
    default_process_id: str,
    invoice_refs: list[ProcessedInvoiceRef],
) -> list[dict]:
    invoice_ids_by_document: dict[tuple[str, str], list[str]] = {}
    for invoice in invoice_refs:
        process_id = invoice.process_id or default_process_id
        invoice_ids_by_document.setdefault((process_id, invoice.document_id), []).append(
            invoice.invoice_id
        )

    return [
        {
            "id": document_id,
            "base_path": f"/runtime/manifests/{process_id}/documents/{document_id}",
            "review_invoice_ids": invoice_ids,
        }
        for (process_id, document_id), invoice_ids in invoice_ids_by_document.items()
    ]


def _invoice_refs_from_manifest(
    manifest_path: Path,
    document_id: str,
    source_batch_id: str,
    source_file_name: str,
    process_id: str,
    parent_invoice_record_id: str = "",
    source_page_numbers: list[int] | None = None,
) -> list[ProcessedInvoiceRef]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        ProcessedInvoiceRef(
            invoice_id=str(invoice.get("invoice_id") or ""),
            title=str(invoice.get("title") or ""),
            document_id=document_id,
            source_batch_id=source_batch_id,
            source_file_name=source_file_name,
            status=str(invoice.get("status") or "pending"),
            process_id=process_id,
            parent_invoice_record_id=parent_invoice_record_id,
            source_page_numbers=list(source_page_numbers or []),
            display_number=invoice_display_number(invoice, 0),
        )
        for invoice in manifest.get("invoices", [])
        if isinstance(invoice, dict) and invoice.get("invoice_id")
    ]


def _manifest_path_from_saved_paths(saved_paths: list[Path], output_dir: Path) -> Path:
    manifest_path = next(
        (path for path in saved_paths if path.name == "viewer_manifest.json"),
        output_dir / "viewer_manifest.json",
    )
    if not manifest_path.exists():
        raise RuntimeError("viewer_manifest.json non generato")
    return manifest_path


def _default_document_processor(
    source_path: Path,
    output_dir: Path,
    first_invoice_number: int,
) -> list[Path]:
    from scripts.debug_words import debug_words_file

    return debug_words_file(
        source_path,
        output_dir=output_dir,
        first_invoice_number=first_invoice_number,
        quality_fallback_mode="crop-only",
    )


def _capture_extraction_feedback(**kwargs) -> str:
    try:
        return capture_extraction_document(**kwargs)
    except Exception as error:
        record_capture_issue(
            process_id=str(kwargs.get("process_id") or ""),
            document_id=str(kwargs.get("document_id") or ""),
            stage="extraction_capture",
            error=error,
            runtime_paths=kwargs.get("runtime_paths"),
        )
        return ""


def _promote_extraction_feedback(process_id: str, paths: RuntimePaths) -> None:
    try:
        promote_extraction_process(process_id, paths)
    except Exception as error:
        record_capture_issue(
            process_id=process_id,
            document_id="",
            stage="process_promotion",
            error=error,
            runtime_paths=paths,
        )


def _discard_staged_extraction_feedback(process_id: str, paths: RuntimePaths) -> None:
    try:
        discard_staged_extraction_process(process_id, paths)
    except Exception as error:
        record_capture_issue(
            process_id=process_id,
            document_id="",
            stage="staging_cleanup",
            error=error,
            runtime_paths=paths,
        )


def _new_process_id(created_at: datetime, manifests_dir: Path) -> str:
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    base_id = f"process_{timestamp}"
    if not (manifests_dir / base_id).exists():
        return base_id

    return f"{base_id}_{uuid4().hex[:8]}"
