from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from estrattore.batch_storage import ImportedBatchIndex
from estrattore.runtime_paths import RuntimePaths, get_runtime_paths


SOURCE_ARCHIVE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SourceOrigin:
    batch_id: str
    document_id: str
    process_id: str


@dataclass(frozen=True)
class ArchivedSource:
    source_id: str
    original_file_name: str
    stored_file_name: str
    relative_path: str
    size_bytes: int
    archived_at: str
    origins: list[SourceOrigin]


def load_source_archive(runtime_paths: RuntimePaths | None = None) -> dict[str, Any]:
    paths = runtime_paths or get_runtime_paths()
    index_path = paths.source_archive_index_file
    if not index_path.is_file():
        return {
            "schema_version": SOURCE_ARCHIVE_SCHEMA_VERSION,
            "updated_at": "",
            "records": [],
        }
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Archivio file origine non valido")
    return payload


def list_archived_sources(
    runtime_paths: RuntimePaths | None = None,
) -> list[ArchivedSource]:
    payload = load_source_archive(runtime_paths)
    return [
        _source_from_payload(record)
        for record in payload.get("records", [])
        if isinstance(record, dict)
    ]


def promote_batch_sources(
    batch_index: ImportedBatchIndex,
    process_id: str,
    runtime_paths: RuntimePaths | None = None,
    source_bindings: dict[str, str] | None = None,
) -> list[ArchivedSource]:
    """Promote staged PDFs exactly once, then leave batch cleanup to the caller."""
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()
    archive = load_source_archive(paths)
    records = [record for record in archive.get("records", []) if isinstance(record, dict)]
    bindings = source_bindings or {}
    promoted: list[ArchivedSource] = []
    batch_dir = paths.batches_dir / batch_index.batch_id

    for document in batch_index.documents:
        bound_source_id = str(bindings.get(document.document_id) or "")
        if bound_source_id and _find_by_source_id(records, bound_source_id) is not None:
            continue
        origin = SourceOrigin(batch_index.batch_id, document.document_id, process_id)
        if _find_by_origin(records, origin) is not None:
            continue
        staged_path = (batch_dir / document.relative_path).resolve()
        if not _is_inside(staged_path, paths.batches_dir) or not staged_path.is_file():
            raise ValueError(f"PDF temporaneo non trovato: {document.original_file_name}")

    for document in batch_index.documents:
        origin = SourceOrigin(batch_index.batch_id, document.document_id, process_id)
        existing = _find_by_origin(records, origin)
        if existing is not None:
            promoted.append(_source_from_payload(existing))
            continue

        bound_source_id = str(bindings.get(document.document_id) or "")
        if bound_source_id:
            bound = _find_by_source_id(records, bound_source_id)
            if bound is not None:
                _append_origin(bound, origin)
                promoted.append(_source_from_payload(bound))
                continue

        staged_path = (batch_dir / document.relative_path).resolve()
        if not _is_inside(staged_path, paths.batches_dir) or not staged_path.is_file():
            raise ValueError(f"PDF temporaneo non trovato: {document.original_file_name}")

        source_id = _stable_source_id(batch_index.batch_id, document.document_id)
        target_name = f"{source_id}_{_safe_stem(document.original_file_name)}.pdf"
        target_path = paths.archive_sources_dir / target_name
        if not target_path.is_file():
            shutil.copy2(staged_path, target_path)
        archived = ArchivedSource(
            source_id=source_id,
            original_file_name=document.original_file_name,
            stored_file_name=target_name,
            relative_path=target_path.relative_to(paths.app_data_dir).as_posix(),
            size_bytes=target_path.stat().st_size,
            archived_at=_timestamp(),
            origins=[origin],
        )
        records.append(_source_payload(archived))
        promoted.append(archived)

    archive["schema_version"] = SOURCE_ARCHIVE_SCHEMA_VERSION
    archive["updated_at"] = _timestamp()
    archive["records"] = records
    _write_archive(paths.source_archive_index_file, archive)
    from estrattore.feedback_collection import (
        record_capture_issue,
        update_source_archive_references,
    )

    try:
        update_source_archive_references(promoted, paths)
    except Exception as error:
        record_capture_issue(
            process_id=process_id,
            document_id="",
            stage="source_archive_promotion",
            error=error,
            runtime_paths=paths,
        )
    for document in batch_index.documents:
        staged_path = (batch_dir / document.relative_path).resolve()
        if _is_inside(staged_path, paths.batches_dir):
            staged_path.unlink(missing_ok=True)
    return promoted


def resolve_archived_source_for_origin(
    batch_id: str,
    document_id: str,
    runtime_paths: RuntimePaths | None = None,
) -> tuple[ArchivedSource, Path]:
    paths = runtime_paths or get_runtime_paths()
    origin = SourceOrigin(batch_id, document_id, "")
    for source in list_archived_sources(paths):
        if any(
            item.batch_id == origin.batch_id and item.document_id == origin.document_id
            for item in source.origins
        ):
            return source, _validated_source_path(source.relative_path, paths)
    raise ValueError("PDF origine non presente nell'archivio")


def archived_source_path(
    source_id: str,
    runtime_paths: RuntimePaths | None = None,
) -> tuple[ArchivedSource, Path]:
    paths = runtime_paths or get_runtime_paths()
    for source in list_archived_sources(paths):
        if source.source_id == source_id:
            return source, _validated_source_path(source.relative_path, paths)
    raise ValueError("File origine archiviato non trovato")


def delete_archived_sources(
    source_ids: list[str],
    runtime_paths: RuntimePaths | None = None,
) -> int:
    paths = runtime_paths or get_runtime_paths()
    targets = {str(value).strip() for value in source_ids if str(value).strip()}
    if not targets:
        raise ValueError("Seleziona almeno un file origine")
    archive = load_source_archive(paths)
    next_records: list[dict[str, Any]] = []
    deleted = 0
    for record in archive.get("records", []):
        if not isinstance(record, dict) or str(record.get("source_id") or "") not in targets:
            if isinstance(record, dict):
                next_records.append(record)
            continue
        path = _validated_source_path(str(record.get("relative_path") or ""), paths, must_exist=False)
        path.unlink(missing_ok=True)
        deleted += 1
    if deleted != len(targets):
        raise ValueError("Uno o piu file origine non sono stati trovati")
    archive["records"] = next_records
    archive["updated_at"] = _timestamp()
    _write_archive(paths.source_archive_index_file, archive)
    from estrattore.feedback_collection import (
        mark_source_archives_unavailable,
        record_capture_issue,
    )

    try:
        mark_source_archives_unavailable(targets, paths)
    except Exception as error:
        record_capture_issue(
            process_id="",
            document_id="",
            stage="source_archive_delete",
            error=error,
            runtime_paths=paths,
        )
    return deleted


def migrate_legacy_archived_sources(
    invoice_registry: dict[str, Any],
    runtime_paths: RuntimePaths | None = None,
) -> int:
    """Index source PDFs created by the former invoice-save workflow."""
    paths = runtime_paths or get_runtime_paths()
    archive = load_source_archive(paths)
    records = [record for record in archive.get("records", []) if isinstance(record, dict)]
    known_paths = {str(record.get("relative_path") or "") for record in records}
    migrated = 0
    for invoice_record in invoice_registry.get("records", []):
        if not isinstance(invoice_record, dict) or invoice_record.get("status") != "saved":
            continue
        relative_path = str(invoice_record.get("archived_source_pdf_path") or "")
        if not relative_path or relative_path in known_paths:
            continue
        try:
            source_path = _validated_source_path(relative_path, paths)
        except ValueError:
            continue
        batch_id = str(invoice_record.get("source_batch_id") or "")
        document_id = str(invoice_record.get("document_id") or "")
        source_id = _stable_source_id(batch_id or relative_path, document_id or relative_path)
        source = ArchivedSource(
            source_id=source_id,
            original_file_name=str(invoice_record.get("source_file_name") or source_path.name),
            stored_file_name=source_path.name,
            relative_path=relative_path,
            size_bytes=source_path.stat().st_size,
            archived_at=str(invoice_record.get("saved_at") or _timestamp()),
            origins=[
                SourceOrigin(
                    batch_id=batch_id,
                    document_id=document_id,
                    process_id="legacy",
                )
            ],
        )
        records.append(_source_payload(source))
        known_paths.add(relative_path)
        migrated += 1
    if migrated:
        archive["schema_version"] = SOURCE_ARCHIVE_SCHEMA_VERSION
        archive["records"] = records
        archive["updated_at"] = _timestamp()
        _write_archive(paths.source_archive_index_file, archive)
    return migrated


def _source_payload(source: ArchivedSource) -> dict[str, Any]:
    return asdict(source)


def _source_from_payload(payload: dict[str, Any]) -> ArchivedSource:
    return ArchivedSource(
        source_id=str(payload.get("source_id") or ""),
        original_file_name=str(payload.get("original_file_name") or ""),
        stored_file_name=str(payload.get("stored_file_name") or ""),
        relative_path=str(payload.get("relative_path") or ""),
        size_bytes=int(payload.get("size_bytes") or 0),
        archived_at=str(payload.get("archived_at") or ""),
        origins=[
            SourceOrigin(
                batch_id=str(origin.get("batch_id") or ""),
                document_id=str(origin.get("document_id") or ""),
                process_id=str(origin.get("process_id") or ""),
            )
            for origin in payload.get("origins", [])
            if isinstance(origin, dict)
        ],
    )


def _find_by_origin(
    records: list[dict[str, Any]], origin: SourceOrigin
) -> dict[str, Any] | None:
    for record in records:
        for item in record.get("origins", []):
            if (
                isinstance(item, dict)
                and str(item.get("batch_id") or "") == origin.batch_id
                and str(item.get("document_id") or "") == origin.document_id
            ):
                return record
    return None


def _find_by_source_id(
    records: list[dict[str, Any]], source_id: str
) -> dict[str, Any] | None:
    return next(
        (record for record in records if str(record.get("source_id") or "") == source_id),
        None,
    )


def _append_origin(record: dict[str, Any], origin: SourceOrigin) -> None:
    origins = record.setdefault("origins", [])
    if not any(
        isinstance(item, dict)
        and str(item.get("batch_id") or "") == origin.batch_id
        and str(item.get("document_id") or "") == origin.document_id
        for item in origins
    ):
        origins.append(asdict(origin))


def _validated_source_path(
    relative_path: str,
    paths: RuntimePaths,
    must_exist: bool = True,
) -> Path:
    candidate = (paths.app_data_dir / relative_path).resolve()
    root = paths.archive_sources_dir.resolve()
    if candidate == root or root not in candidate.parents or candidate.suffix.lower() != ".pdf":
        raise ValueError("Percorso file origine non autorizzato")
    if must_exist and not candidate.is_file():
        raise ValueError("File origine archiviato non trovato")
    return candidate


def _safe_stem(file_name: str) -> str:
    stem = Path(file_name.replace("\\", "/")).stem
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "_", stem).strip("_")
    return normalized[:80] or "documento"


def _stable_source_id(batch_id: str, document_id: str) -> str:
    digest = hashlib.sha256(f"{batch_id}:{document_id}".encode("utf-8")).hexdigest()
    return f"source_{digest[:24]}"


def _write_archive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.writing")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary_path.replace(path)


def _timestamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _is_inside(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents
