from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import NAMESPACE_URL, uuid4, uuid5

from estrattore.runtime_paths import RuntimePaths, get_runtime_paths, project_root


FEEDBACK_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1
_COLLECTION_LOCK = threading.RLock()
_CODE_FINGERPRINT: str | None = None
_CANDIDATE_FILE_NAMES = (
    "vat_candidates.csv",
    "fiscal_identifier_candidates.csv",
    "issuer_name_candidates.csv",
    "invoice_number_candidates.csv",
    "invoice_date_candidates.csv",
    "condominium_candidates.csv",
    "economic_summary_regions.csv",
    "economic_summary_field_candidates.csv",
    "economic_summary_resolved_rows.csv",
)


def capture_extraction_document(
    *,
    process_id: str,
    source_batch_id: str,
    document_id: str,
    source_file_name: str,
    source_path: Path,
    document_dir: Path,
    runtime_paths: RuntimePaths | None = None,
    parent_invoice_record_id: str = "",
    source_page_numbers: list[int] | None = None,
    published: bool = False,
) -> str:
    """Persist one completed OCR run without executing extraction again."""
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()
    manifest_path = document_dir / "viewer_manifest.json"
    if not source_path.is_file():
        raise ValueError(f"Sorgente collection non trovata: {source_file_name}")
    if not manifest_path.is_file():
        raise ValueError(f"Manifest collection non trovato: {document_id}")

    with _COLLECTION_LOCK:
        manifest = _read_json_object(manifest_path)
        run_id = str(manifest.get("collection_run_id") or "").strip() or str(uuid4())
        manifest["collection_run_id"] = run_id
        _ensure_manifest_collection_identity(manifest, run_id)
        _write_json_atomic(manifest_path, manifest)

        source_sha256 = _sha256_file(source_path)
        source_asset_id = f"sha256:{source_sha256}"
        config = _extraction_configuration(manifest)
        evidence_payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "run_id": run_id,
            "source_asset_id": source_asset_id,
            "source_file_name": source_file_name,
            "document_id": document_id,
            "process_id": process_id,
            "source_batch_id": source_batch_id,
            "coordinate_system": {
                "origin": "top_left",
                "x_unit": "rendered_page_pixel",
                "y_unit": "rendered_page_pixel",
                "page_index_base": 1,
            },
            "configuration": config,
            "manifest": manifest,
            "candidate_files": _candidate_file_payload(document_dir),
        }
        evidence_bytes = _canonical_json_bytes(evidence_payload)
        evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
        evidence_relative_path = f"evidence/{evidence_sha256}.json.gz"
        _write_gzip_once(paths.feedback_dir / evidence_relative_path, evidence_bytes)

        now = _timestamp()
        with _connect(paths) as connection:
            _upsert_source_asset(
                connection,
                source_asset_id=source_asset_id,
                sha256=source_sha256,
                original_file_name=source_file_name,
                byte_size=source_path.stat().st_size,
                page_count=len(manifest.get("pages", [])),
                now=now,
            )
            archive_reference = _archive_reference_for_capture(
                source_path=source_path,
                source_sha256=source_sha256,
                parent_invoice_record_id=parent_invoice_record_id,
                paths=paths,
            )
            if archive_reference:
                connection.execute(
                    """
                    UPDATE source_assets
                    SET archive_source_id = ?, archive_relative_path = ?,
                        available = 1, updated_at = ?
                    WHERE source_asset_id = ?
                    """,
                    (
                        f"archive-path:{archive_reference}",
                        archive_reference,
                        now,
                        source_asset_id,
                    ),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO source_origins (
                    source_asset_id, source_batch_id, document_id, process_id
                ) VALUES (?, ?, ?, ?)
                """,
                (source_asset_id, source_batch_id, document_id, process_id),
            )
            connection.execute(
                """
                INSERT INTO extraction_runs (
                    run_id, process_id, source_batch_id, document_id,
                    source_asset_id, status, completed_at, configuration_json,
                    evidence_relative_path, evidence_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = CASE
                        WHEN extraction_runs.status = 'completed' THEN 'completed'
                        ELSE excluded.status END,
                    completed_at = excluded.completed_at,
                    configuration_json = excluded.configuration_json,
                    evidence_relative_path = excluded.evidence_relative_path,
                    evidence_sha256 = excluded.evidence_sha256
                """,
                (
                    run_id,
                    process_id,
                    source_batch_id,
                    document_id,
                    source_asset_id,
                    "completed" if published else "staged",
                    now,
                    _json_text(config),
                    evidence_relative_path,
                    evidence_sha256,
                ),
            )
            for invoice in _manifest_invoices(manifest):
                _upsert_invoice_prediction(
                    connection,
                    run_id=run_id,
                    invoice=invoice,
                    parent_invoice_record_id=parent_invoice_record_id,
                    source_page_numbers=source_page_numbers or [],
                    now=now,
                )
            connection.execute(
                """
                UPDATE capture_issues SET resolved_at = ?
                WHERE process_id = ? AND document_id = ? AND resolved_at = ''
                """,
                (now, process_id, document_id),
            )
        return run_id


def record_capture_issue(
    *,
    process_id: str,
    document_id: str,
    stage: str,
    error: Exception,
    runtime_paths: RuntimePaths | None = None,
) -> None:
    paths = runtime_paths or get_runtime_paths()
    now = _timestamp()
    issue_id = str(
        uuid5(
            NAMESPACE_URL,
            f"feedback-issue:{process_id}:{document_id}:{stage}:{type(error).__name__}:{error}",
        )
    )
    try:
        with _COLLECTION_LOCK, _connect(paths) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO capture_issues (
                    issue_id, process_id, document_id, stage,
                    error_type, error_message, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    issue_id,
                    process_id,
                    document_id,
                    stage,
                    type(error).__name__,
                    str(error),
                    now,
                ),
            )
    except Exception:
        paths.feedback_dir.mkdir(parents=True, exist_ok=True)
        fallback_path = paths.feedback_dir / "capture_issues.jsonl"
        with _COLLECTION_LOCK, fallback_path.open("a", encoding="utf-8") as target:
            target.write(
                _json_text(
                    {
                        "issue_id": issue_id,
                        "process_id": process_id,
                        "document_id": document_id,
                        "stage": stage,
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                        "created_at": now,
                    }
                )
                + "\n"
            )


def promote_extraction_process(
    process_id: str, runtime_paths: RuntimePaths | None = None
) -> int:
    paths = runtime_paths or get_runtime_paths()
    with _COLLECTION_LOCK, _connect(paths) as connection:
        cursor = connection.execute(
            """
            UPDATE extraction_runs SET status = 'completed'
            WHERE process_id = ? AND status = 'staged'
            """,
            (process_id,),
        )
        return int(cursor.rowcount)


def discard_staged_extraction_process(
    process_id: str, runtime_paths: RuntimePaths | None = None
) -> int:
    paths = runtime_paths or get_runtime_paths()
    if not paths.feedback_database_file.is_file():
        return 0
    evidence_paths: list[str] = []
    with _COLLECTION_LOCK, _connect(paths) as connection:
        rows = connection.execute(
            """
            SELECT run_id, evidence_relative_path FROM extraction_runs
            WHERE process_id = ? AND status = 'staged'
            """,
            (process_id,),
        ).fetchall()
        run_ids = [str(row[0]) for row in rows]
        evidence_paths = [str(row[1]) for row in rows]
        for run_id in run_ids:
            invoice_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT invoice_instance_id FROM invoice_instances WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            ]
            for invoice_id in invoice_ids:
                connection.execute(
                    "DELETE FROM predictions WHERE invoice_instance_id = ?",
                    (invoice_id,),
                )
            connection.execute("DELETE FROM invoice_instances WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM extraction_runs WHERE run_id = ?", (run_id,))
        connection.execute(
            """
            DELETE FROM source_origins
            WHERE source_asset_id NOT IN (SELECT source_asset_id FROM extraction_runs)
            """
        )
        connection.execute(
            """
            DELETE FROM source_assets
            WHERE source_asset_id NOT IN (SELECT source_asset_id FROM extraction_runs)
            """
        )
        remaining_evidence = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT evidence_relative_path FROM extraction_runs"
            ).fetchall()
        }
    for relative_path in evidence_paths:
        if relative_path not in remaining_evidence:
            (paths.feedback_dir / relative_path).unlink(missing_ok=True)
    return len(run_ids)


def record_segmentation_event(
    *,
    process_id: str,
    document_id: str,
    operation: str,
    request_payload: dict[str, Any],
    result_payload: dict[str, Any],
    manifest: dict[str, Any],
    runtime_paths: RuntimePaths | None = None,
) -> None:
    paths = runtime_paths or get_runtime_paths()
    run_id = str(manifest.get("collection_run_id") or "").strip()
    if not run_id:
        return
    event_basis = {
        "run_id": run_id,
        "operation": operation,
        "request": request_payload,
        "result": result_payload,
    }
    event_id = str(uuid5(NAMESPACE_URL, _json_text(event_basis)))
    with _COLLECTION_LOCK, _connect(paths) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO segmentation_events (
                event_id, run_id, process_id, document_id, operation,
                request_json, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                process_id,
                document_id,
                operation,
                _json_text(request_payload),
                _json_text(result_payload),
                _timestamp(),
            ),
        )
        for invoice in _manifest_invoices(manifest):
            _upsert_invoice_prediction(
                connection,
                run_id=run_id,
                invoice=invoice,
                parent_invoice_record_id="",
                source_page_numbers=[],
                now=_timestamp(),
                preserve_prediction=True,
            )


def prepare_review_operation(
    *,
    operation_id: str,
    event_kind: str,
    outcome: str,
    record_id: str,
    invoice_payload: dict[str, Any],
    review_batch_base_path: str,
    process_id: str,
    source_batch_id: str,
    document_id: str,
    source_file_name: str,
    parent_invoice_record_id: str = "",
    source_page_numbers: list[int] | None = None,
    previous_invoice_payload: dict[str, Any] | None = None,
    runtime_paths: RuntimePaths | None = None,
) -> str:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()
    manifest_path = _manifest_path_for_document(
        process_id=process_id,
        document_id=document_id,
        paths=paths,
    )
    manifest = _read_json_object(manifest_path)
    run_id = str(manifest.get("collection_run_id") or "").strip()
    if not run_id or not _collection_run_exists(run_id, paths):
        source_path = _resolve_source_path(source_batch_id, document_id, paths)
        run_id = capture_extraction_document(
            process_id=process_id,
            source_batch_id=source_batch_id,
            document_id=document_id,
            source_file_name=source_file_name,
            source_path=source_path,
            document_dir=manifest_path.parent,
            runtime_paths=paths,
            parent_invoice_record_id=parent_invoice_record_id,
            source_page_numbers=source_page_numbers,
            published=True,
        )
        manifest = _read_json_object(manifest_path)

    manifest_invoice_id = str(
        invoice_payload.get("manifestInvoiceId")
        or invoice_payload.get("invoiceId")
        or ""
    )
    manifest_invoice = _find_manifest_invoice(manifest, manifest_invoice_id)
    if manifest_invoice is None:
        manifest_invoice = {
            "invoice_id": manifest_invoice_id,
            "display_number": invoice_payload.get("displayNumber"),
            "title": invoice_payload.get("title", ""),
            "page_numbers": invoice_payload.get("pageNumbers", []),
            "extraction_fields": invoice_payload.get("fields", []),
            "fields": invoice_payload.get("fields", []),
        }

    now = _timestamp()
    with _COLLECTION_LOCK, _connect(paths) as connection:
        invoice_instance_id = _upsert_invoice_prediction(
            connection,
            run_id=run_id,
            invoice=manifest_invoice,
            parent_invoice_record_id=parent_invoice_record_id,
            source_page_numbers=source_page_numbers or [],
            now=now,
            preserve_prediction=True,
        )
        prediction = connection.execute(
            """
            SELECT extracted_fields_json, presented_fields_json
            FROM predictions WHERE invoice_instance_id = ?
            """,
            (invoice_instance_id,),
        ).fetchone()
        extracted_fields = json.loads(prediction[0]) if prediction else []
        initial_presented_fields = json.loads(prediction[1]) if prediction else []
        previous_fields = (
            _field_list(previous_invoice_payload)
            if previous_invoice_payload is not None
            else initial_presented_fields
        )
        accepted_fields = _field_list(invoice_payload)
        presented_fields = _review_presented_fields(previous_fields, accepted_fields)
        pending_payload = {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "operation_id": operation_id,
            "event_kind": event_kind,
            "outcome": outcome,
            "record_id": record_id,
            "invoice_instance_id": invoice_instance_id,
            "run_id": run_id,
            "review_batch_base_path": review_batch_base_path,
            "page_numbers": invoice_payload.get("pageNumbers", []),
            "extracted_fields": extracted_fields,
            "presented_fields": presented_fields,
            "accepted_fields": accepted_fields,
            "field_provenance": _field_provenance(
                extracted_fields=extracted_fields,
                presented_fields=presented_fields,
                accepted_fields=accepted_fields,
            ),
            "created_at": now,
        }
        connection.execute(
            """
            INSERT INTO pending_operations (
                operation_id, event_kind, payload_json, status, error,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'prepared', '', ?, ?)
            ON CONFLICT(operation_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                status = 'prepared',
                error = '',
                updated_at = excluded.updated_at
            WHERE pending_operations.status != 'completed'
            """,
            (operation_id, event_kind, _json_text(pending_payload), now, now),
        )
    return operation_id


def prepare_registry_edit_operation(
    *,
    operation_id: str | None,
    record_id: str,
    previous_invoice_payload: dict[str, Any],
    updated_invoice_payload: dict[str, Any],
    legacy_record: dict[str, Any] | None = None,
    runtime_paths: RuntimePaths | None = None,
) -> str:
    paths = runtime_paths or get_runtime_paths()
    now = _timestamp()
    with _COLLECTION_LOCK, _connect(paths) as connection:
        previous_fields = _field_list(previous_invoice_payload)
        submitted_fields = _field_list(updated_invoice_payload)
        if operation_id is None:
            operation_id = _matching_prepared_registry_operation(
                connection,
                record_id=record_id,
                previous_fields=previous_fields,
                accepted_fields=submitted_fields,
            ) or str(uuid4())
        latest = connection.execute(
            """
            SELECT invoice_instance_id FROM review_revisions
            WHERE record_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (record_id,),
        ).fetchone()
        if latest is None:
            invoice_instance_id = _create_legacy_invoice_baseline(
                connection,
                record_id=record_id,
                invoice_payload=previous_invoice_payload,
                legacy_record=legacy_record or {},
                now=now,
            )
        else:
            invoice_instance_id = str(latest[0])
        prediction = connection.execute(
            """
            SELECT extracted_fields_json FROM predictions
            WHERE invoice_instance_id = ?
            """,
            (invoice_instance_id,),
        ).fetchone()
        extracted_fields = json.loads(prediction[0]) if prediction else []
        accepted_fields = _registry_edit_fields(
            previous_fields,
            submitted_fields,
            operation_id,
        )
        pending_payload = {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "operation_id": operation_id,
            "event_kind": "registry_edit",
            "outcome": "saved",
            "record_id": record_id,
            "invoice_instance_id": invoice_instance_id,
            "review_batch_base_path": "",
            "page_numbers": updated_invoice_payload.get("pageNumbers", []),
            "extracted_fields": extracted_fields,
            "presented_fields": previous_fields,
            "accepted_fields": accepted_fields,
            "field_provenance": _field_provenance(
                extracted_fields=extracted_fields,
                presented_fields=previous_fields,
                accepted_fields=accepted_fields,
            ),
            "created_at": now,
        }
        connection.execute(
            """
            INSERT INTO pending_operations (
                operation_id, event_kind, payload_json, status, error,
                created_at, updated_at
            ) VALUES (?, 'registry_edit', ?, 'prepared', '', ?, ?)
            ON CONFLICT(operation_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                status = 'prepared',
                error = '',
                updated_at = excluded.updated_at
            WHERE pending_operations.status != 'completed'
            """,
            (operation_id, _json_text(pending_payload), now, now),
        )
    return operation_id


def _create_legacy_invoice_baseline(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    invoice_payload: dict[str, Any],
    legacy_record: dict[str, Any],
    now: str,
) -> str:
    source_asset_id = f"legacy:{record_id}"
    run_id = str(uuid5(NAMESPACE_URL, f"feedback-legacy-run:{record_id}"))
    invoice_instance_id = str(uuid5(NAMESPACE_URL, f"feedback-legacy-invoice:{record_id}"))
    connection.execute(
        """
        INSERT OR IGNORE INTO source_assets (
            source_asset_id, sha256, original_file_name, byte_size, page_count,
            archive_source_id, archive_relative_path, available,
            created_at, updated_at
        ) VALUES (?, '', ?, 0, 0, '', ?, 0, ?, ?)
        """,
        (
            source_asset_id,
            str(legacy_record.get("source_file_name") or invoice_payload.get("sourceFile") or ""),
            str(legacy_record.get("archived_source_pdf_path") or ""),
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO extraction_runs (
            run_id, process_id, source_batch_id, document_id, source_asset_id,
            status, completed_at, configuration_json,
            evidence_relative_path, evidence_sha256
        ) VALUES (?, 'legacy', ?, ?, ?, 'completed', ?, ?, '', '')
        """,
        (
            run_id,
            str(legacy_record.get("source_batch_id") or ""),
            str(legacy_record.get("document_id") or ""),
            source_asset_id,
            now,
            _json_text({"legacy": True, "provenance": "unknown"}),
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO invoice_instances (
            invoice_instance_id, run_id, manifest_invoice_id, display_number,
            title, page_numbers_json, source_page_numbers_json,
            parent_invoice_record_id, created_at
        ) VALUES (?, ?, ?, NULL, ?, ?, '[]', '', ?)
        """,
        (
            invoice_instance_id,
            run_id,
            str(invoice_payload.get("manifestInvoiceId") or invoice_payload.get("invoiceId") or record_id),
            str(invoice_payload.get("title") or ""),
            _json_text(invoice_payload.get("pageNumbers", [])),
            now,
        ),
    )
    previous_fields = _field_list(invoice_payload)
    connection.execute(
        """
        INSERT OR IGNORE INTO predictions (
            invoice_instance_id, extracted_fields_json, presented_fields_json, created_at
        ) VALUES (?, '[]', ?, ?)
        """,
        (invoice_instance_id, _json_text(previous_fields), now),
    )
    operation_id = str(uuid5(NAMESPACE_URL, f"feedback-legacy-import:{record_id}"))
    revision_id = str(uuid5(NAMESPACE_URL, f"feedback-review:{operation_id}"))
    connection.execute(
        """
        INSERT OR IGNORE INTO review_revisions (
            revision_id, operation_id, invoice_instance_id, record_id,
            event_kind, outcome, parent_revision_id, review_batch_base_path,
            accepted_fields_json, field_provenance_json, page_numbers_json,
            created_at
        ) VALUES (?, ?, ?, ?, 'legacy_import', 'saved', '', '', ?, ?, ?, ?)
        """,
        (
            revision_id,
            operation_id,
            invoice_instance_id,
            record_id,
            _json_text(previous_fields),
            _json_text(
                _field_provenance(
                    extracted_fields=[],
                    presented_fields=previous_fields,
                    accepted_fields=previous_fields,
                )
            ),
            _json_text(invoice_payload.get("pageNumbers", [])),
            now,
        ),
    )
    return invoice_instance_id


def commit_review_operation(
    operation_id: str,
    runtime_paths: RuntimePaths | None = None,
) -> str:
    paths = runtime_paths or get_runtime_paths()
    with _COLLECTION_LOCK, _connect(paths) as connection:
        row = connection.execute(
            "SELECT payload_json, status FROM pending_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Operazione collection non preparata")
        payload = json.loads(row[0])
        revision_id = str(uuid5(NAMESPACE_URL, f"feedback-review:{operation_id}"))
        if row[1] == "completed":
            return revision_id
        parent_row = connection.execute(
            """
            SELECT revision_id FROM review_revisions
            WHERE record_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (payload["record_id"],),
        ).fetchone()
        parent_revision_id = str(parent_row[0]) if parent_row else ""
        connection.execute(
            """
            INSERT OR IGNORE INTO review_revisions (
                revision_id, operation_id, invoice_instance_id, record_id,
                event_kind, outcome, parent_revision_id, review_batch_base_path,
                accepted_fields_json, field_provenance_json, page_numbers_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                operation_id,
                payload["invoice_instance_id"],
                payload["record_id"],
                payload["event_kind"],
                payload["outcome"],
                parent_revision_id,
                payload.get("review_batch_base_path", ""),
                _json_text(payload.get("accepted_fields", [])),
                _json_text(payload.get("field_provenance", [])),
                _json_text(payload.get("page_numbers", [])),
                payload.get("created_at") or _timestamp(),
            ),
        )
        connection.execute(
            """
            UPDATE pending_operations
            SET status = 'completed', error = '', updated_at = ?
            WHERE operation_id = ?
            """,
            (_timestamp(), operation_id),
        )
        return revision_id


def feedback_operation_id(
    *,
    event_kind: str,
    record_id: str,
    invoice_payload: dict[str, Any],
    previous_invoice_payload: dict[str, Any] | None = None,
) -> str:
    accepted = [
        {"kind": str(field.get("kind") or ""), "value": str(field.get("value") or "")}
        for field in _field_list(invoice_payload)
    ]
    previous = [
        {"kind": str(field.get("kind") or ""), "value": str(field.get("value") or "")}
        for field in _field_list(previous_invoice_payload)
    ]
    basis = _json_text(
        {
            "event_kind": event_kind,
            "record_id": record_id,
            "previous": previous,
            "accepted": accepted,
        }
    )
    return str(uuid5(NAMESPACE_URL, f"feedback-operation:{basis}"))


def reconcile_feedback_operations(
    records: Iterable[dict[str, Any]],
    runtime_paths: RuntimePaths | None = None,
    *,
    abandon_mismatched: bool = False,
) -> int:
    """Commit prepared reviews only when the business JSON proves the write."""
    paths = runtime_paths or get_runtime_paths()
    if not paths.feedback_database_file.is_file():
        return 0
    records_by_id = {
        str(record.get("record_id") or ""): record
        for record in records
        if isinstance(record, dict) and str(record.get("record_id") or "")
    }
    matching_operation_ids: list[str] = []
    stale_operation_ids: list[str] = []
    with _COLLECTION_LOCK, _connect(paths) as connection:
        rows = connection.execute(
            """
            SELECT operation_id, event_kind, payload_json FROM pending_operations
            WHERE status = 'prepared'
              AND event_kind IN ('invoice_save', 'invoice_discard', 'registry_edit')
            """
        ).fetchall()
        for operation_id, event_kind, payload_json in rows:
            payload = json.loads(payload_json)
            current_record = records_by_id.get(str(payload.get("record_id") or ""))
            current_invoice = (
                current_record.get("invoice")
                if isinstance(current_record, dict)
                and isinstance(current_record.get("invoice"), dict)
                else {}
            )
            expected_status = "discarded" if payload.get("outcome") == "discarded" else "saved"
            status_matches = (
                isinstance(current_record, dict)
                and str(current_record.get("status") or "") == expected_status
            )
            values_match = _field_value_map(_field_list(current_invoice)) == _field_value_map(
                payload.get("accepted_fields", [])
            )
            if status_matches and values_match:
                matching_operation_ids.append(str(operation_id))
            elif abandon_mismatched and event_kind == "registry_edit":
                stale_operation_ids.append(str(operation_id))
        if stale_operation_ids:
            placeholders = ",".join("?" for _ in stale_operation_ids)
            connection.execute(
                f"""
                UPDATE pending_operations
                SET status = 'abandoned',
                    error = 'Business registry state no longer matches this operation',
                    updated_at = ?
                WHERE operation_id IN ({placeholders})
                """,
                (_timestamp(), *stale_operation_ids),
            )

    for operation_id in matching_operation_ids:
        commit_review_operation(operation_id, paths)
    return len(matching_operation_ids)


def update_source_archive_references(
    promoted_sources: Iterable[Any],
    runtime_paths: RuntimePaths | None = None,
) -> None:
    paths = runtime_paths or get_runtime_paths()
    now = _timestamp()
    with _COLLECTION_LOCK, _connect(paths) as connection:
        for source in promoted_sources:
            relative_path = str(getattr(source, "relative_path", "") or "")
            for origin in getattr(source, "origins", []):
                rows = connection.execute(
                    """
                    SELECT source_asset_id FROM source_origins
                    WHERE source_batch_id = ? AND document_id = ?
                    """,
                    (str(origin.batch_id), str(origin.document_id)),
                ).fetchall()
                for row in rows:
                    connection.execute(
                        """
                        UPDATE source_assets
                        SET archive_source_id = ?, archive_relative_path = ?,
                            available = 1, updated_at = ?
                        WHERE source_asset_id = ?
                        """,
                        (str(source.source_id), relative_path, now, str(row[0])),
                    )


def mark_source_archives_unavailable(
    source_ids: Iterable[str],
    runtime_paths: RuntimePaths | None = None,
) -> None:
    paths = runtime_paths or get_runtime_paths()
    normalized = [str(value).strip() for value in source_ids if str(value).strip()]
    if not normalized or not paths.feedback_database_file.is_file():
        return
    placeholders = ",".join("?" for _ in normalized)
    with _COLLECTION_LOCK, _connect(paths) as connection:
        connection.execute(
            f"""
            UPDATE source_assets
            SET available = 0, archive_relative_path = '', updated_at = ?
            WHERE archive_source_id IN ({placeholders})
            """,
            (_timestamp(), *normalized),
        )


def mark_archive_paths_unavailable(
    relative_paths: Iterable[str],
    runtime_paths: RuntimePaths | None = None,
) -> None:
    """Mark image evidence unavailable when an archived PDF is deleted."""
    paths = runtime_paths or get_runtime_paths()
    normalized = [
        str(value).replace("\\", "/").strip().lstrip("/")
        for value in relative_paths
        if str(value).strip()
    ]
    if not normalized or not paths.feedback_database_file.is_file():
        return
    placeholders = ",".join("?" for _ in normalized)
    with _COLLECTION_LOCK, _connect(paths) as connection:
        connection.execute(
            f"""
            UPDATE source_assets
            SET available = 0, archive_relative_path = '', updated_at = ?
            WHERE archive_relative_path IN ({placeholders})
            """,
            (_timestamp(), *normalized),
        )


def collection_stats(runtime_paths: RuntimePaths | None = None) -> dict[str, int]:
    paths = runtime_paths or get_runtime_paths()
    if not paths.feedback_database_file.is_file():
        return {
            "sources": 0,
            "runs": 0,
            "staged_runs": 0,
            "invoices": 0,
            "revisions": 0,
            "pending": 0,
            "failed": 0,
            "image_unavailable": 0,
        }
    with _connect(paths) as connection:
        return _collection_stats_from_connection(connection)


def export_feedback_bundle(
    output_dir: Path,
    runtime_paths: RuntimePaths | None = None,
    include_pdfs: bool = True,
) -> Path:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    working_dir = output_dir.with_name(f".{output_dir.name}.working-{uuid4().hex}")
    working_dir.mkdir()
    database_target = working_dir / "feedback.sqlite3"
    try:
        with _COLLECTION_LOCK, _connect(paths) as source_connection:
            target_connection = sqlite3.connect(database_target)
            try:
                source_connection.backup(target_connection)
                target_connection.execute("PRAGMA foreign_keys = ON")
                _prune_export_snapshot(target_connection)
                evidence_rows = target_connection.execute(
                    """
                    SELECT DISTINCT evidence_relative_path, evidence_sha256
                    FROM extraction_runs
                    WHERE evidence_relative_path != ''
                    """
                ).fetchall()
                source_rows = target_connection.execute(
                    """
                    SELECT DISTINCT source_assets.source_asset_id,
                           source_assets.archive_relative_path, source_assets.sha256
                    FROM source_assets
                    JOIN extraction_runs USING(source_asset_id)
                    WHERE source_assets.available = 1
                      AND source_assets.archive_relative_path != ''
                    """
                ).fetchall()

                checksums: dict[str, str] = {}
                for relative_path_value, expected_sha256 in evidence_rows:
                    relative_path = str(relative_path_value)
                    source_path = paths.feedback_dir / relative_path
                    if not source_path.is_file():
                        raise RuntimeError(
                            f"Evidenza collection mancante: {relative_path}"
                        )
                    try:
                        with gzip.open(source_path, "rb") as evidence_file:
                            actual_evidence_sha256 = hashlib.sha256(
                                evidence_file.read()
                            ).hexdigest()
                    except (OSError, gzip.BadGzipFile) as error:
                        raise RuntimeError(
                            f"Evidenza collection non leggibile: {relative_path}"
                        ) from error
                    if actual_evidence_sha256 != str(expected_sha256):
                        raise RuntimeError(
                            f"Evidenza collection non coerente: {relative_path}"
                        )
                    target_path = working_dir / relative_path
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, target_path)
                    checksums[target_path.relative_to(working_dir).as_posix()] = (
                        _sha256_file(target_path)
                    )

                missing_pdf_assets: list[str] = []
                if include_pdfs:
                    for source_asset_id, relative_path, expected_sha256 in source_rows:
                        source_path = paths.app_data_dir / str(relative_path)
                        target_name = f"{str(source_asset_id).replace(':', '_')}.pdf"
                        target_path = working_dir / "pdf" / target_name
                        if not source_path.is_file():
                            missing_pdf_assets.append(str(source_asset_id))
                            _mark_snapshot_asset_unavailable(
                                target_connection, str(source_asset_id)
                            )
                            continue
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source_path, target_path)
                        actual_sha256 = _sha256_file(target_path)
                        if actual_sha256 != str(expected_sha256):
                            target_path.unlink(missing_ok=True)
                            missing_pdf_assets.append(str(source_asset_id))
                            _mark_snapshot_asset_unavailable(
                                target_connection, str(source_asset_id)
                            )
                            continue
                        checksums[target_path.relative_to(working_dir).as_posix()] = (
                            actual_sha256
                        )

                target_connection.commit()
                foreign_key_violations = target_connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if foreign_key_violations:
                    raise RuntimeError("Snapshot collection con riferimenti non validi")
                stats = _collection_stats_from_connection(target_connection)
            finally:
                target_connection.close()

        checksums[database_target.name] = _sha256_file(database_target)
        manifest = {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "exported_at": _timestamp(),
            "includes_pdfs": include_pdfs,
            "missing_pdf_asset_ids": missing_pdf_assets,
            "checksums_sha256": checksums,
            "stats": stats,
        }
        _write_json_atomic(working_dir / "bundle_manifest.json", manifest)
        working_dir.replace(output_dir)
        return output_dir
    except Exception:
        shutil.rmtree(working_dir, ignore_errors=True)
        raise


@contextmanager
def _connect(paths: RuntimePaths) -> Iterator[sqlite3.Connection]:
    paths.feedback_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(paths.feedback_database_file, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        _initialize_schema(connection)
        connection.commit()
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_assets (
            source_asset_id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            original_file_name TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            page_count INTEGER NOT NULL,
            archive_source_id TEXT NOT NULL DEFAULT '',
            archive_relative_path TEXT NOT NULL DEFAULT '',
            available INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_origins (
            source_asset_id TEXT NOT NULL REFERENCES source_assets(source_asset_id),
            source_batch_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            process_id TEXT NOT NULL,
            PRIMARY KEY(source_asset_id, source_batch_id, document_id, process_id)
        );
        CREATE TABLE IF NOT EXISTS extraction_runs (
            run_id TEXT PRIMARY KEY,
            process_id TEXT NOT NULL,
            source_batch_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            source_asset_id TEXT NOT NULL REFERENCES source_assets(source_asset_id),
            status TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            configuration_json TEXT NOT NULL,
            evidence_relative_path TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invoice_instances (
            invoice_instance_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES extraction_runs(run_id),
            manifest_invoice_id TEXT NOT NULL,
            display_number INTEGER,
            title TEXT NOT NULL,
            page_numbers_json TEXT NOT NULL,
            source_page_numbers_json TEXT NOT NULL,
            parent_invoice_record_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, manifest_invoice_id)
        );
        CREATE TABLE IF NOT EXISTS predictions (
            invoice_instance_id TEXT PRIMARY KEY REFERENCES invoice_instances(invoice_instance_id),
            extracted_fields_json TEXT NOT NULL,
            presented_fields_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_operations (
            operation_id TEXT PRIMARY KEY,
            event_kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_revisions (
            revision_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL UNIQUE,
            invoice_instance_id TEXT NOT NULL REFERENCES invoice_instances(invoice_instance_id),
            record_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            outcome TEXT NOT NULL,
            parent_revision_id TEXT NOT NULL DEFAULT '',
            review_batch_base_path TEXT NOT NULL DEFAULT '',
            accepted_fields_json TEXT NOT NULL,
            field_provenance_json TEXT NOT NULL,
            page_numbers_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS review_revisions_record_idx
            ON review_revisions(record_id, created_at);
        CREATE TABLE IF NOT EXISTS segmentation_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES extraction_runs(run_id),
            process_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS capture_issues (
            issue_id TEXT PRIMARY KEY,
            process_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            error_type TEXT NOT NULL,
            error_message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    row = connection.execute(
        "SELECT value FROM collection_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is not None and int(row[0]) != FEEDBACK_SCHEMA_VERSION:
        raise ValueError(f"Schema collection non supportato: {row[0]}")
    connection.execute(
        "INSERT OR IGNORE INTO collection_metadata(key, value) VALUES('schema_version', ?)",
        (str(FEEDBACK_SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT OR IGNORE INTO collection_metadata(key, value) VALUES('dataset_id', ?)",
        (str(uuid4()),),
    )
    connection.execute(
        "INSERT OR IGNORE INTO collection_metadata(key, value) VALUES('created_at', ?)",
        (_timestamp(),),
    )


def _upsert_source_asset(
    connection: sqlite3.Connection,
    *,
    source_asset_id: str,
    sha256: str,
    original_file_name: str,
    byte_size: int,
    page_count: int,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_assets (
            source_asset_id, sha256, original_file_name, byte_size, page_count,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_asset_id) DO UPDATE SET
            original_file_name = excluded.original_file_name,
            byte_size = excluded.byte_size,
            page_count = excluded.page_count,
            updated_at = excluded.updated_at
        """,
        (
            source_asset_id,
            sha256,
            original_file_name,
            byte_size,
            page_count,
            now,
            now,
        ),
    )


def _upsert_invoice_prediction(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    invoice: dict[str, Any],
    parent_invoice_record_id: str,
    source_page_numbers: list[int],
    now: str,
    preserve_prediction: bool = False,
) -> str:
    manifest_invoice_id = str(invoice.get("invoice_id") or "").strip()
    if not manifest_invoice_id:
        raise ValueError("Fattura collection priva di identificativo")
    invoice_instance_id = str(
        invoice.get("collection_invoice_instance_id")
        or uuid5(NAMESPACE_URL, f"feedback-invoice:{run_id}:{manifest_invoice_id}")
    )
    connection.execute(
        """
        INSERT INTO invoice_instances (
            invoice_instance_id, run_id, manifest_invoice_id, display_number,
            title, page_numbers_json, source_page_numbers_json,
            parent_invoice_record_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, manifest_invoice_id) DO UPDATE SET
            display_number = excluded.display_number,
            title = excluded.title,
            page_numbers_json = excluded.page_numbers_json,
            source_page_numbers_json = CASE
                WHEN excluded.source_page_numbers_json != '[]'
                THEN excluded.source_page_numbers_json
                ELSE invoice_instances.source_page_numbers_json END,
            parent_invoice_record_id = CASE
                WHEN excluded.parent_invoice_record_id != ''
                THEN excluded.parent_invoice_record_id
                ELSE invoice_instances.parent_invoice_record_id END
        """,
        (
            invoice_instance_id,
            run_id,
            manifest_invoice_id,
            _optional_int(invoice.get("display_number")),
            str(invoice.get("title") or ""),
            _json_text(invoice.get("page_numbers", [])),
            _json_text(source_page_numbers),
            parent_invoice_record_id,
            now,
        ),
    )
    actual_row = connection.execute(
        """
        SELECT invoice_instance_id FROM invoice_instances
        WHERE run_id = ? AND manifest_invoice_id = ?
        """,
        (run_id, manifest_invoice_id),
    ).fetchone()
    actual_id = str(actual_row[0])
    if not preserve_prediction:
        connection.execute(
            """
            INSERT INTO predictions (
                invoice_instance_id, extracted_fields_json,
                presented_fields_json, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(invoice_instance_id) DO NOTHING
            """,
            (
                actual_id,
                _json_text(invoice.get("extraction_fields", invoice.get("fields", []))),
                _json_text(invoice.get("fields", [])),
                now,
            ),
        )
    else:
        connection.execute(
            """
            INSERT OR IGNORE INTO predictions (
                invoice_instance_id, extracted_fields_json,
                presented_fields_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                actual_id,
                _json_text(invoice.get("extraction_fields", invoice.get("fields", []))),
                _json_text(invoice.get("fields", [])),
                now,
            ),
        )
    return actual_id


def _matching_prepared_registry_operation(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    previous_fields: list[dict[str, Any]],
    accepted_fields: list[dict[str, Any]],
) -> str:
    previous_values = _field_value_map(previous_fields)
    accepted_values = _field_value_map(accepted_fields)
    rows = connection.execute(
        """
        SELECT operation_id, payload_json FROM pending_operations
        WHERE event_kind = 'registry_edit' AND status = 'prepared'
        """
    ).fetchall()
    for operation_id, payload_json in rows:
        payload = json.loads(payload_json)
        if str(payload.get("record_id") or "") != record_id:
            continue
        if _field_value_map(payload.get("presented_fields", [])) != previous_values:
            continue
        if _field_value_map(payload.get("accepted_fields", [])) == accepted_values:
            return str(operation_id)
    return ""


def _registry_edit_fields(
    previous_fields: list[dict[str, Any]],
    accepted_fields: list[dict[str, Any]],
    operation_id: str,
) -> list[dict[str, Any]]:
    """Attach provenance for the action known at the registry-edit boundary."""
    previous_by_kind = _fields_by_kind(previous_fields)
    result: list[dict[str, Any]] = []
    for field in accepted_fields:
        next_field = dict(field)
        kind = str(next_field.get("kind") or "")
        previous_value = str(previous_by_kind.get(kind, {}).get("value") or "")
        accepted_value = str(next_field.get("value") or "")
        if previous_value != accepted_value:
            next_field["review_interaction"] = (
                "cleared" if previous_value and not accepted_value else "manual_input"
            )
            next_field["review_value_origin"] = "manual_input"
            next_field["review_action_id"] = operation_id
            next_field["review_source_field_kind"] = kind
            for key in (
                "registry_id",
                "registry_source_row",
                "review_registry_snapshot",
            ):
                next_field.pop(key, None)
        else:
            next_field["review_interaction"] = "untouched"
            next_field.pop("review_action_id", None)
            next_field.pop("review_source_field_kind", None)
        result.append(next_field)
    return result


def _review_presented_fields(
    initial_presented_fields: list[dict[str, Any]],
    accepted_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reconstruct the final automatic presentation from explicit frontend provenance."""
    accepted_by_kind = _fields_by_kind(accepted_fields)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in initial_presented_fields:
        kind = str(field.get("kind") or "")
        accepted = accepted_by_kind.get(kind, {})
        origin = str(accepted.get("review_value_origin") or "")
        interaction = str(accepted.get("review_interaction") or "")
        if origin == "automatic_registry" or interaction == "automatic_replacement":
            result.append(dict(accepted))
        else:
            result.append(dict(field))
        seen.add(kind)
    for field in accepted_fields:
        kind = str(field.get("kind") or "")
        if kind not in seen and str(field.get("review_value_origin") or "") == "automatic_registry":
            result.append(dict(field))
    return result


def _field_provenance(
    *,
    extracted_fields: list[dict[str, Any]],
    presented_fields: list[dict[str, Any]],
    accepted_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    extracted_by_kind = _fields_by_kind(extracted_fields)
    presented_by_kind = _fields_by_kind(presented_fields)
    accepted_by_kind = _fields_by_kind(accepted_fields)
    kinds = list(dict.fromkeys([
        *extracted_by_kind.keys(),
        *presented_by_kind.keys(),
        *accepted_by_kind.keys(),
    ]))
    result = []
    for kind in kinds:
        extracted = extracted_by_kind.get(kind, {})
        presented = presented_by_kind.get(kind, {})
        accepted = accepted_by_kind.get(kind, {})
        extracted_value = str(extracted.get("value") or "")
        presented_value = str(presented.get("value") or "")
        accepted_value = str(accepted.get("value") or "")
        interaction = str(accepted.get("review_interaction") or "").strip()
        if not interaction:
            interaction = (
                "untouched"
                if accepted_value == presented_value
                else "cleared"
                if not accepted_value and presented_value
                else "manual_input"
            )
        origin = str(accepted.get("review_value_origin") or "").strip()
        if not origin:
            role = str(accepted.get("role") or presented.get("role") or "")
            origin = (
                "automatic_registry"
                if role == "registry_enriched" and accepted_value == presented_value
                else "document_extraction"
                if accepted_value == extracted_value
                else "manual_input"
            )
        result.append(
            {
                "kind": kind,
                "value_semantics": (
                    "monetary_amount"
                    if kind in {"ritenuta", "economic_summary.ritenuta.value"}
                    else ""
                ),
                "extracted_value": extracted_value,
                "presented_value": presented_value,
                "accepted_value": accepted_value,
                "value_origin": origin,
                "interaction": interaction,
                "action_id": str(accepted.get("review_action_id") or ""),
                "source_field_kind": str(
                    accepted.get("review_source_field_kind") or ""
                ),
                "review_status": (
                    "saved_unchanged"
                    if accepted_value == presented_value
                    else "saved_after_edit"
                ),
                "predicted_location": {
                    "page": extracted.get("page"),
                    "bbox": extracted.get("bbox"),
                },
                "verified_location": None,
                "registry_id": accepted.get("registry_id"),
                "registry_source_row": accepted.get("registry_source_row"),
                "registry_snapshot": accepted.get("review_registry_snapshot"),
            }
        )
    return result


def _collection_run_exists(run_id: str, paths: RuntimePaths) -> bool:
    if not paths.feedback_database_file.is_file():
        return False
    with _COLLECTION_LOCK, _connect(paths) as connection:
        return connection.execute(
            "SELECT 1 FROM extraction_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone() is not None


def _archive_reference_for_path(source_path: Path, paths: RuntimePaths) -> str:
    try:
        resolved = source_path.resolve(strict=True)
    except OSError:
        return ""
    for archive_root in (paths.archive_sources_dir, paths.archive_invoices_dir):
        root = archive_root.resolve()
        if resolved != root and root in resolved.parents:
            return resolved.relative_to(paths.app_data_dir.resolve()).as_posix()
    return ""


def _archive_reference_for_capture(
    *,
    source_path: Path,
    source_sha256: str,
    parent_invoice_record_id: str,
    paths: RuntimePaths,
) -> str:
    direct_reference = _archive_reference_for_path(source_path, paths)
    if direct_reference:
        return direct_reference
    if not parent_invoice_record_id or not paths.invoice_registry_file.is_file():
        return ""

    try:
        registry = _read_json_object(paths.invoice_registry_file)
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    parent_record = next(
        (
            record
            for record in registry.get("records", [])
            if isinstance(record, dict)
            and str(record.get("record_id") or "") == parent_invoice_record_id
        ),
        None,
    )
    if parent_record is None:
        return ""

    relative_path = str(parent_record.get("archived_pdf_path") or "").replace("\\", "/")
    candidate = paths.app_data_dir / relative_path.lstrip("/")
    archive_reference = _archive_reference_for_path(candidate, paths)
    if not archive_reference:
        return ""
    try:
        return archive_reference if _sha256_file(candidate) == source_sha256 else ""
    except OSError:
        return ""


def _ensure_manifest_collection_identity(manifest: dict[str, Any], run_id: str) -> None:
    for invoice in _manifest_invoices(manifest):
        invoice_id = str(invoice.get("invoice_id") or "")
        invoice["collection_invoice_instance_id"] = str(
            uuid5(NAMESPACE_URL, f"feedback-invoice:{run_id}:{invoice_id}")
        )


def _candidate_file_payload(document_dir: Path) -> dict[str, list[dict[str, str]]]:
    payload: dict[str, list[dict[str, str]]] = {}
    for file_name in _CANDIDATE_FILE_NAMES:
        path = document_dir / file_name
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            payload[file_name] = [dict(row) for row in csv.DictReader(source)]
    return payload


def _extraction_configuration(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "app_build_id": os.environ.get("ESTRATTORE_SCAN_BUILD_ID", "development"),
        "code_fingerprint": _code_fingerprint(),
        "ocr_engine": "tesseract",
        "ocr_lang": manifest.get("ocr_lang"),
        "ocr_profile": manifest.get("ocr_profile"),
        "manifest_schema_version": manifest.get("schema_version"),
        "fallback_ocr_events": manifest.get("fallback_ocr_events", []),
        "quality_fallback_candidates": manifest.get("quality_fallback_candidates", []),
        "traineddata_hashes": None,
        "rendering_dpi": None,
    }


def _code_fingerprint() -> str:
    global _CODE_FINGERPRINT
    if _CODE_FINGERPRINT is not None:
        return _CODE_FINGERPRINT
    digest = hashlib.sha256()
    root = project_root()
    files = sorted((root / "src" / "estrattore").glob("*.py"))
    debug_words = root / "scripts" / "debug_words.py"
    if debug_words.is_file():
        files.append(debug_words)
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    _CODE_FINGERPRINT = digest.hexdigest()
    return _CODE_FINGERPRINT


def _manifest_path_for_document(
    *, process_id: str, document_id: str, paths: RuntimePaths
) -> Path:
    if not process_id or not document_id:
        raise ValueError("Identita documento collection incompleta")
    candidate = (
        paths.manifests_dir / process_id / "documents" / document_id
    ).resolve()
    root = paths.manifests_dir.resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Percorso manifest collection non autorizzato")
    manifest_path = candidate / "viewer_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Manifest collection non disponibile")
    return manifest_path


def _resolve_source_path(
    source_batch_id: str, document_id: str, paths: RuntimePaths
) -> Path:
    staged = paths.batches_dir / source_batch_id
    if staged.is_dir():
        from estrattore.batch_storage import load_batch_index

        index = load_batch_index(staged)
        for document in index.documents:
            if document.document_id == document_id:
                candidate = staged / document.relative_path
                if candidate.is_file():
                    return candidate
    from estrattore.source_archive_storage import resolve_archived_source_for_origin

    return resolve_archived_source_for_origin(source_batch_id, document_id, paths)[1]


def _find_manifest_invoice(
    manifest: dict[str, Any], invoice_id: str
) -> dict[str, Any] | None:
    return next(
        (
            invoice
            for invoice in _manifest_invoices(manifest)
            if str(invoice.get("invoice_id") or "") == invoice_id
        ),
        None,
    )


def _manifest_invoices(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    invoices = manifest.get("invoices", [])
    return [invoice for invoice in invoices if isinstance(invoice, dict)]


def _field_list(invoice_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(invoice_payload, dict):
        return []
    fields = invoice_payload.get("fields", [])
    return [dict(field) for field in fields if isinstance(field, dict)]


def _fields_by_kind(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(field.get("kind") or ""): field
        for field in fields
        if str(field.get("kind") or "")
    }


def _field_value_map(fields: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(field.get("kind") or ""): str(field.get("value") or "")
        for field in fields
        if isinstance(field, dict) and str(field.get("kind") or "")
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON non valido: {path.name}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.writing")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_gzip_once(path: Path, payload: bytes) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.writing")
    try:
        with gzip.open(temporary, "wb", compresslevel=6) as target:
            target.write(payload)
        if path.exists():
            return
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_text(payload: Any) -> str:
    return _canonical_json_bytes(payload).decode("utf-8")


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _prune_export_snapshot(connection: sqlite3.Connection) -> None:
    incomplete_invoices = """
        SELECT invoice_instance_id FROM invoice_instances
        WHERE run_id IN (
            SELECT run_id FROM extraction_runs WHERE status != 'completed'
        )
    """
    connection.execute(
        f"DELETE FROM review_revisions WHERE invoice_instance_id IN ({incomplete_invoices})"
    )
    connection.execute(
        f"DELETE FROM predictions WHERE invoice_instance_id IN ({incomplete_invoices})"
    )
    connection.execute(
        """
        DELETE FROM segmentation_events
        WHERE run_id IN (SELECT run_id FROM extraction_runs WHERE status != 'completed')
        """
    )
    connection.execute(
        f"DELETE FROM invoice_instances WHERE invoice_instance_id IN ({incomplete_invoices})"
    )
    connection.execute("DELETE FROM extraction_runs WHERE status != 'completed'")
    connection.execute(
        """
        DELETE FROM pending_operations
        WHERE status != 'completed'
           OR operation_id NOT IN (SELECT operation_id FROM review_revisions)
        """
    )
    connection.execute(
        """
        DELETE FROM source_origins
        WHERE source_asset_id NOT IN (SELECT source_asset_id FROM extraction_runs)
        """
    )
    connection.execute(
        """
        DELETE FROM source_assets
        WHERE source_asset_id NOT IN (SELECT source_asset_id FROM extraction_runs)
        """
    )
    connection.commit()


def _mark_snapshot_asset_unavailable(
    connection: sqlite3.Connection,
    source_asset_id: str,
) -> None:
    connection.execute(
        """
        UPDATE source_assets
        SET available = 0, archive_relative_path = '', updated_at = ?
        WHERE source_asset_id = ?
        """,
        (_timestamp(), source_asset_id),
    )


def _collection_stats_from_connection(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    return {
        "sources": _count(connection, "source_assets"),
        "runs": _count_where(connection, "extraction_runs", "status = 'completed'"),
        "staged_runs": _count_where(connection, "extraction_runs", "status = 'staged'"),
        "invoices": _count(connection, "invoice_instances"),
        "revisions": _count(connection, "review_revisions"),
        "pending": _count_where(connection, "pending_operations", "status = 'prepared'"),
        "failed": (
            _count_where(connection, "pending_operations", "status = 'failed'")
            + _count_where(connection, "capture_issues", "resolved_at = ''")
        ),
        "image_unavailable": _count_where(connection, "source_assets", "available = 0"),
    }


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _count_where(connection: sqlite3.Connection, table: str, condition: str) -> int:
    return int(
        connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {condition}").fetchone()[0]
    )
