from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from estrattore.batch_storage import ImportedBatchIndex, load_batch_index
from estrattore.runtime_paths import RuntimePaths, get_runtime_paths


PROCESSING_SCHEMA_VERSION = 1
PROCESSING_INDEX_FILE_NAME = "processing_run_index.json"
PROCESSING_COMPLETE_FILE_NAME = "processing_complete.ok"
DOCUMENT_COMPLETE_FILE_NAME = "document_complete.ok"
DOCUMENT_WORKING_SUFFIX = ".working"
REVIEW_BATCH_SIZE = 15
COMPLETED_PROCESSING_STATUSES = {"processed", "processed_with_errors"}

DocumentProcessor = Callable[[Path, Path, int], list[Path]]
ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class ProcessingCancelled(RuntimeError):
    pass


class ProcessingPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessedInvoiceRef:
    invoice_id: str
    title: str
    document_id: str
    source_batch_id: str
    source_file_name: str
    status: str = "pending"


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


def process_imported_batch(
    batch_id: str,
    runtime_paths: RuntimePaths | None = None,
    document_processor: DocumentProcessor | None = None,
    review_batch_size: int = REVIEW_BATCH_SIZE,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
    should_pause: CancelCheck | None = None,
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
    review_batches_dir = process_dir / "review_batches"
    documents_dir.mkdir(parents=True, exist_ok=resume_existing)
    review_batches_dir.mkdir(parents=True, exist_ok=True)
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
            if should_pause and should_pause():
                raise ProcessingPaused("Processamento messo in pausa dall'utente")
            source_path = batch_dir / document.relative_path
            output_dir = documents_dir / document.document_id
            working_output_dir = documents_dir / (
                f"{document.document_id}{DOCUMENT_WORKING_SUFFIX}"
            )
            if resume_existing and is_document_processing_complete(output_dir):
                try:
                    manifest_invoice_refs = _invoice_refs_from_manifest(
                        manifest_path=output_dir / "viewer_manifest.json",
                        document_id=document.document_id,
                        source_batch_id=batch_index.batch_id,
                        source_file_name=document.original_file_name,
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
                saved_paths = processor(source_path, working_output_dir, next_invoice_number)
                manifest_path = _manifest_path_from_saved_paths(
                    saved_paths,
                    working_output_dir,
                )
                manifest_invoice_refs = _invoice_refs_from_manifest(
                    manifest_path=manifest_path,
                    document_id=document.document_id,
                    source_batch_id=batch_index.batch_id,
                    source_file_name=document.original_file_name,
                )
                shutil.rmtree(output_dir, ignore_errors=True)
                working_output_dir.replace(output_dir)
                mark_document_processing_complete(
                    output_dir=output_dir,
                    document_id=document.document_id,
                    invoice_count=len(manifest_invoice_refs),
                )
                invoice_refs.extend(manifest_invoice_refs)
                next_invoice_number += len(manifest_invoice_refs)
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
            if should_pause and should_pause():
                raise ProcessingPaused("Processamento messo in pausa dall'utente")

        review_batches = _write_review_batches(
            process_id=process_id,
            process_dir=process_dir,
            review_batches_dir=review_batches_dir,
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
            mark_processing_run_complete(run_index, process_dir)
        return run_index
    except ProcessingCancelled:
        shutil.rmtree(process_dir, ignore_errors=True)
        raise
    except ProcessingPaused:
        save_processing_run_index(run_index, process_dir)
        raise
    except Exception as error:
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
    if not paths.manifests_dir.exists():
        return []

    batches: list[ReviewBatchRef] = []
    for index_path in paths.manifests_dir.glob(f"*/{PROCESSING_INDEX_FILE_NAME}"):
        try:
            run_index = load_processing_run_index(index_path.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not is_processing_run_complete(run_index, index_path.parent):
            continue
        batches.extend(
            _review_batch_with_current_count(paths, batch)
            for batch in run_index.review_batches
        )

    return sorted(
        (batch for batch in batches if batch.invoice_count > 0),
        key=lambda batch: batch.id,
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
        if not process_dir.is_dir():
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
    for invoice in review_payload.get("invoices", []):
        if isinstance(invoice, dict) and invoice.get("invoice_id") == invoice_id:
            invoice["status"] = status
            closed_invoice_document_id = str(invoice.get("document_id") or "")
            closed_invoice_source_batch_id = str(invoice.get("source_batch_id") or "")
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

    _cleanup_closed_invoice_working_files(
        runtime_paths=paths,
        review_batch_dir=review_batch_dir,
        document_id=closed_invoice_document_id,
        source_batch_id=closed_invoice_source_batch_id,
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
            )

    raise ValueError(f"Fattura non presente nel batch di revisione: {invoice_id}")


def move_review_invoice_page(
    payload: dict,
    runtime_paths: RuntimePaths | None = None,
) -> dict:
    paths = runtime_paths or get_runtime_paths()
    review_batch_base_path = _required_string(payload, "review_batch_base_path")
    document_id = _required_string(payload, "document_id")
    from_invoice_id = _required_string(payload, "from_invoice_id")
    to_invoice_id = _required_string(payload, "to_invoice_id")
    page_number = _required_int(payload, "page_number")

    review_batch_dir = review_batch_dir_from_base_path(review_batch_base_path, paths)
    _validate_review_page_move(
        review_batch_dir=review_batch_dir,
        document_id=document_id,
        from_invoice_id=from_invoice_id,
        to_invoice_id=to_invoice_id,
    )

    process_dir = review_batch_dir.parent.parent
    manifest_path = process_dir / "documents" / document_id / "viewer_manifest.json"
    if not _is_inside(manifest_path, paths.manifests_dir) or not manifest_path.is_file():
        raise ValueError("Manifest documento non trovato")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    invoices = manifest.get("invoices", [])
    if not isinstance(invoices, list):
        raise ValueError("Manifest senza lista fatture valida")

    from_invoice = _manifest_invoice(invoices, from_invoice_id)
    to_invoice = _manifest_invoice(invoices, to_invoice_id)
    from_pages = _invoice_page_numbers(from_invoice)
    to_pages = _invoice_page_numbers(to_invoice)
    if page_number not in from_pages:
        raise ValueError("La pagina non appartiene alla fattura di origine")
    if len(from_pages) <= 1:
        raise ValueError("Non puoi lasciare una fattura senza pagine")

    from_invoice["page_numbers"] = [
        current_page for current_page in from_pages if current_page != page_number
    ]
    to_invoice["page_numbers"] = sorted({*to_pages, page_number})
    _atomic_write_json(manifest_path, manifest)

    return {
        "ok": True,
        "document_id": document_id,
        "from_invoice_id": from_invoice_id,
        "to_invoice_id": to_invoice_id,
        "page_number": page_number,
        "from_page_numbers": from_invoice["page_numbers"],
        "to_page_numbers": to_invoice["page_numbers"],
    }


def review_batch_dir_from_base_path(
    review_batch_base_path: str,
    runtime_paths: RuntimePaths | None = None,
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
    if not candidate.is_dir():
        raise ValueError("Batch di revisione non trovato")
    return candidate


def _validate_review_page_move(
    review_batch_dir: Path,
    document_id: str,
    from_invoice_id: str,
    to_invoice_id: str,
) -> None:
    review_index_path = review_batch_dir / "review_batch_index.json"
    review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))
    found_invoice_ids = set()
    for invoice in review_payload.get("invoices", []):
        if (
            isinstance(invoice, dict)
            and str(invoice.get("document_id") or "") == document_id
            and invoice.get("status", "pending") == "pending"
        ):
            found_invoice_ids.add(str(invoice.get("invoice_id") or ""))

    if from_invoice_id not in found_invoice_ids or to_invoice_id not in found_invoice_ids:
        raise ValueError("Puoi spostare pagine solo tra fatture pending dello stesso documento")


def _manifest_invoice(invoices: list, invoice_id: str) -> dict:
    for invoice in invoices:
        if isinstance(invoice, dict) and str(invoice.get("invoice_id") or "") == invoice_id:
            return invoice

    raise ValueError(f"Fattura non trovata nel manifest: {invoice_id}")


def _invoice_page_numbers(invoice: dict) -> list[int]:
    page_numbers: list[int] = []
    for page_number in invoice.get("page_numbers", []):
        try:
            page_numbers.append(int(page_number))
        except (TypeError, ValueError):
            continue
    return page_numbers


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


def _cleanup_closed_invoice_working_files(
    runtime_paths: RuntimePaths,
    review_batch_dir: Path,
    document_id: str,
    source_batch_id: str,
) -> None:
    if not document_id or not source_batch_id:
        return

    process_dir = review_batch_dir.parent.parent
    if _has_pending_invoice_for_document(process_dir, document_id):
        return

    document_working_dir = process_dir / "documents" / document_id
    if _is_inside(document_working_dir, runtime_paths.manifests_dir):
        shutil.rmtree(document_working_dir, ignore_errors=True)

    if _has_pending_invoice_for_source_document(
        runtime_paths,
        source_batch_id,
        document_id,
    ):
        return

    source_pdf_path = (
        runtime_paths.batches_dir
        / source_batch_id
        / "input"
        / f"{document_id}.pdf"
    )
    if _is_inside(source_pdf_path, runtime_paths.batches_dir):
        source_pdf_path.unlink(missing_ok=True)


def _has_pending_invoice_for_document(process_dir: Path, document_id: str) -> bool:
    for review_index_path in (process_dir / "review_batches").glob(
        "*/review_batch_index.json"
    ):
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

    for review_index_path in runtime_paths.manifests_dir.glob(
        "*/review_batches/*/review_batch_index.json"
    ):
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


def _is_inside(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _write_review_batches(
    process_id: str,
    process_dir: Path,
    review_batches_dir: Path,
    invoice_refs: list[ProcessedInvoiceRef],
    review_batch_size: int,
) -> list[ReviewBatchRef]:
    if review_batch_size < 1:
        raise ValueError("review_batch_size must be >= 1")

    shutil.rmtree(review_batches_dir, ignore_errors=True)
    review_batches_dir.mkdir(parents=True, exist_ok=True)

    review_batches: list[ReviewBatchRef] = []
    for batch_index, start_index in enumerate(
        range(0, len(invoice_refs), review_batch_size),
        start=1,
    ):
        batch_invoice_refs = invoice_refs[start_index : start_index + review_batch_size]
        review_batch_id = f"review_batch_{batch_index:04d}"
        review_batch_dir = review_batches_dir / review_batch_id
        review_batch_dir.mkdir(parents=True, exist_ok=True)
        base_path = f"/runtime/manifests/{process_id}/review_batches/{review_batch_id}"
        viewer_index = {
            "documents": _viewer_documents_for_invoice_refs(
                process_id=process_id,
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
                id=f"{process_id}:{review_batch_id}",
                label=f"Revisione {batch_index:03d} ({first_number}-{last_number})",
                base_path=base_path,
                invoice_count=len(batch_invoice_refs),
            )
        )

    return review_batches


def _review_batch_with_current_count(
    runtime_paths: RuntimePaths,
    review_batch: ReviewBatchRef,
) -> ReviewBatchRef:
    try:
        review_batch_dir = review_batch_dir_from_base_path(
            review_batch.base_path,
            runtime_paths,
        )
        payload = json.loads(
            (review_batch_dir / "review_batch_index.json").read_text(
                encoding="utf-8"
            )
        )
        active_count = sum(
            1
            for invoice in payload.get("invoices", [])
            if isinstance(invoice, dict)
            and invoice.get("status", "pending") == "pending"
        )
    except (OSError, ValueError, json.JSONDecodeError):
        active_count = review_batch.invoice_count

    return ReviewBatchRef(
        id=review_batch.id,
        label=review_batch.label,
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


def _cleanup_working_document_dirs(documents_dir: Path) -> None:
    if not documents_dir.exists():
        return
    for child in documents_dir.iterdir():
        if child.is_dir() and child.name.endswith(DOCUMENT_WORKING_SUFFIX):
            shutil.rmtree(child, ignore_errors=True)


def _viewer_documents_for_invoice_refs(
    process_id: str,
    invoice_refs: list[ProcessedInvoiceRef],
) -> list[dict]:
    invoice_ids_by_document: dict[str, list[str]] = {}
    for invoice in invoice_refs:
        invoice_ids_by_document.setdefault(invoice.document_id, []).append(
            invoice.invoice_id
        )

    return [
        {
            "id": document_id,
            "base_path": f"/runtime/manifests/{process_id}/documents/{document_id}",
            "review_invoice_ids": invoice_ids,
        }
        for document_id, invoice_ids in invoice_ids_by_document.items()
    ]


def _invoice_refs_from_manifest(
    manifest_path: Path,
    document_id: str,
    source_batch_id: str,
    source_file_name: str,
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


def _new_process_id(created_at: datetime, manifests_dir: Path) -> str:
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    base_id = f"process_{timestamp}"
    if not (manifests_dir / base_id).exists():
        return base_id

    return f"{base_id}_{uuid4().hex[:8]}"
