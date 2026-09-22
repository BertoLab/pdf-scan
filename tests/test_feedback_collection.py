import gzip
import json
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest

from estrattore.feedback_collection import (
    capture_extraction_document,
    collection_stats,
    commit_review_operation,
    discard_staged_extraction_process,
    export_feedback_bundle,
    feedback_operation_id,
    mark_archive_paths_unavailable,
    mark_source_archives_unavailable,
    prepare_registry_edit_operation,
    prepare_review_operation,
    promote_extraction_process,
    update_source_archive_references,
)
from estrattore.runtime_paths import get_runtime_paths


def _field(kind: str, value: str, **extra) -> dict:
    return {
        "kind": kind,
        "label": kind,
        "value": value,
        "score": 90,
        "page": 1,
        "bbox": {"x1": 10, "y1": 20, "x2": 50, "y2": 35},
        **extra,
    }


def _workspace() -> Path:
    root = Path("feedback-collection-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _captured_document(tmp_path: Path):
    paths = get_runtime_paths(tmp_path / "app-data")
    paths.ensure_directories()
    process_id = "process_20260916_100000"
    batch_id = "batch_20260916_090000"
    document_id = "doc_000001"
    source_path = paths.batches_dir / batch_id / "input" / "source.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF-collection-test")
    document_dir = paths.manifests_dir / process_id / "documents" / document_id
    document_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "source_file": str(source_path),
        "ocr_lang": "ita",
        "ocr_profile": "fast",
        "pages": [
            {
                "page": 1,
                "width": 100,
                "height": 200,
                "tokens": [
                    {
                        "token_id": "p1-t1",
                        "text": "85,79",
                        "confidence": 0.95,
                        "bbox": {"x1": 10, "y1": 20, "x2": 50, "y2": 35},
                    }
                ],
                "candidate_tables": {},
            }
        ],
        "invoices": [
            {
                "invoice_id": "invoice-001",
                "display_number": 1,
                "title": "Fattura 001",
                "page_numbers": [1],
                "extraction_fields": [_field("totale", "85,79")],
                "fields": [
                    _field(
                        "totale",
                        "85,79",
                        role="registry_enriched",
                        registry_id="registry-1",
                    )
                ],
            }
        ],
    }
    (document_dir / "viewer_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (document_dir / "economic_summary_field_candidates.csv").write_text(
        'value,score\n"85,79",90\n', encoding="utf-8"
    )
    run_id = capture_extraction_document(
        process_id=process_id,
        source_batch_id=batch_id,
        document_id=document_id,
        source_file_name="source.pdf",
        source_path=source_path,
        document_dir=document_dir,
        runtime_paths=paths,
    )
    promote_extraction_process(process_id, paths)
    return paths, process_id, batch_id, document_id, source_path, document_dir, run_id


def test_capture_is_idempotent_and_preserves_evidence() -> None:
    workspace = _workspace()
    try:
        paths, process_id, batch_id, document_id, source_path, document_dir, run_id = (
            _captured_document(workspace)
        )
        repeated_run_id = capture_extraction_document(
            process_id=process_id,
            source_batch_id=batch_id,
            document_id=document_id,
            source_file_name="source.pdf",
            source_path=source_path,
            document_dir=document_dir,
            runtime_paths=paths,
        )

        assert repeated_run_id == run_id
        assert collection_stats(paths) == {
            "sources": 1,
            "runs": 1,
            "staged_runs": 0,
            "invoices": 1,
            "revisions": 0,
            "pending": 0,
            "failed": 0,
            "image_unavailable": 1,
        }
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            evidence_relative_path = connection.execute(
                "SELECT evidence_relative_path FROM extraction_runs"
            ).fetchone()[0]
            extracted_json, presented_json = connection.execute(
                "SELECT extracted_fields_json, presented_fields_json FROM predictions"
            ).fetchone()
        with gzip.open(
            paths.feedback_dir / evidence_relative_path, "rt", encoding="utf-8"
        ) as source:
            evidence = json.load(source)

        assert json.loads(extracted_json)[0]["value"] == "85,79"
        assert json.loads(presented_json)[0]["role"] == "registry_enriched"
        assert evidence["manifest"]["collection_run_id"] == run_id
        assert "economic_summary_field_candidates.csv" in evidence["candidate_files"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_review_and_registry_edit_create_an_idempotent_revision_chain() -> None:
    workspace = _workspace()
    try:
        paths, process_id, batch_id, document_id, _source, _document_dir, _run_id = (
            _captured_document(workspace)
        )
        accepted_invoice = {
        "invoiceId": "review-invoice-001",
        "manifestInvoiceId": "invoice-001",
        "documentId": document_id,
        "basePath": f"/runtime/manifests/{process_id}/documents/{document_id}",
        "pageNumbers": [1],
        "fields": [
            _field(
                "totale",
                "90,00",
                review_interaction="manual_input",
                review_value_origin="manual_input",
                review_action_id="action-1",
            )
        ],
    }
        operation_id = feedback_operation_id(
        event_kind="invoice_save",
        record_id="record-1",
        invoice_payload=accepted_invoice,
    )
        prepare_review_operation(
        operation_id=operation_id,
        event_kind="invoice_save",
        outcome="saved",
        record_id="record-1",
        invoice_payload=accepted_invoice,
        review_batch_base_path="/runtime/manifests/review_batch_0001",
        process_id=process_id,
        source_batch_id=batch_id,
        document_id=document_id,
        source_file_name="source.pdf",
        runtime_paths=paths,
    )
        first_revision_id = commit_review_operation(operation_id, paths)
        assert commit_review_operation(operation_id, paths) == first_revision_id

        updated_invoice = {
        **accepted_invoice,
        "fields": [
            _field(
                "totale",
                "91,00",
                review_interaction="manual_input",
                review_value_origin="manual_input",
                review_action_id="action-2",
            )
        ],
    }
        edit_operation_id = feedback_operation_id(
        event_kind="registry_edit",
        record_id="record-1",
        invoice_payload=updated_invoice,
    )
        prepare_registry_edit_operation(
        operation_id=edit_operation_id,
        record_id="record-1",
        previous_invoice_payload=accepted_invoice,
        updated_invoice_payload=updated_invoice,
        runtime_paths=paths,
    )
        commit_review_operation(edit_operation_id, paths)

        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            revisions = connection.execute(
            """
            SELECT event_kind, parent_revision_id, field_provenance_json
            FROM review_revisions ORDER BY rowid
            """
            ).fetchall()
        assert len(revisions) == 2
        assert revisions[0][0] == "invoice_save"
        assert revisions[1][0] == "registry_edit"
        assert revisions[1][1] == first_revision_id
        provenance = json.loads(revisions[0][2])[0]
        assert provenance["extracted_value"] == "85,79"
        assert provenance["presented_value"] == "85,79"
        assert provenance["accepted_value"] == "90,00"
        assert provenance["interaction"] == "manual_input"
        assert provenance["verified_location"] is None
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_automatic_registry_refresh_records_the_value_actually_presented() -> None:
    workspace = _workspace()
    try:
        paths, process_id, batch_id, document_id, *_rest = _captured_document(workspace)
        invoice = {
            "invoiceId": "invoice-001",
            "manifestInvoiceId": "invoice-001",
            "fields": [
                _field(
                    "totale",
                    "90,00",
                    role="registry_enriched",
                    review_interaction="automatic_replacement",
                    review_value_origin="automatic_registry",
                    review_action_id="automatic-action",
                )
            ],
        }
        prepare_review_operation(
            operation_id="automatic-registry-operation",
            event_kind="invoice_save",
            outcome="saved",
            record_id="record-automatic",
            invoice_payload=invoice,
            review_batch_base_path="review-1",
            process_id=process_id,
            source_batch_id=batch_id,
            document_id=document_id,
            source_file_name="source.pdf",
            runtime_paths=paths,
        )
        commit_review_operation("automatic-registry-operation", paths)

        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            provenance_json = connection.execute(
                "SELECT field_provenance_json FROM review_revisions"
            ).fetchone()[0]
        provenance = json.loads(provenance_json)[0]
        assert provenance["extracted_value"] == "85,79"
        assert provenance["presented_value"] == "90,00"
        assert provenance["accepted_value"] == "90,00"
        assert provenance["value_origin"] == "automatic_registry"
        assert provenance["interaction"] == "automatic_replacement"
        assert provenance["review_status"] == "saved_unchanged"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_export_uses_a_database_backup_and_includes_evidence() -> None:
    workspace = _workspace()
    try:
        paths, *_rest = _captured_document(workspace)
        output_dir = workspace / "feedback-export"
        export_feedback_bundle(output_dir, paths, include_pdfs=False)

        manifest = json.loads((output_dir / "bundle_manifest.json").read_text("utf-8"))
        assert (output_dir / "feedback.sqlite3").is_file()
        assert list((output_dir / "evidence").glob("*.json.gz"))
        assert manifest["includes_pdfs"] is False
        assert manifest["stats"]["runs"] == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_export_prunes_staged_runs_and_their_evidence() -> None:
    workspace = _workspace()
    try:
        paths, process_id, *_rest = _captured_document(workspace)
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            connection.execute(
                "UPDATE extraction_runs SET status = 'staged' WHERE process_id = ?",
                (process_id,),
            )
            connection.commit()

        output_dir = export_feedback_bundle(
            workspace / "feedback-export", paths, include_pdfs=False
        )

        with closing(sqlite3.connect(output_dir / "feedback.sqlite3")) as connection:
            assert connection.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM invoice_instances").fetchone()[0] == 0
        assert list((output_dir / "evidence").glob("*.json.gz")) == []
        bundle = json.loads((output_dir / "bundle_manifest.json").read_text("utf-8"))
        assert bundle["stats"]["runs"] == 0
        assert bundle["stats"]["staged_runs"] == 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_export_fails_without_publishing_when_completed_evidence_is_missing() -> None:
    workspace = _workspace()
    try:
        paths, *_rest = _captured_document(workspace)
        next(paths.feedback_evidence_dir.glob("*.json.gz")).unlink()
        output_dir = workspace / "feedback-export"

        with pytest.raises(RuntimeError, match="Evidenza collection mancante"):
            export_feedback_bundle(output_dir, paths, include_pdfs=False)

        assert not output_dir.exists()
        assert list(workspace.glob(".feedback-export.working-*")) == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_export_fails_without_publishing_when_evidence_is_corrupted() -> None:
    workspace = _workspace()
    try:
        paths, *_rest = _captured_document(workspace)
        evidence_path = next(paths.feedback_evidence_dir.glob("*.json.gz"))
        with gzip.open(evidence_path, "wb") as evidence_file:
            evidence_file.write(b'{"different":true}')
        output_dir = workspace / "feedback-export"

        with pytest.raises(RuntimeError, match="Evidenza collection non coerente"):
            export_feedback_bundle(output_dir, paths, include_pdfs=False)

        assert not output_dir.exists()
        assert list(workspace.glob(".feedback-export.working-*")) == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_capture_recovers_manifest_identity_missing_from_database() -> None:
    workspace = _workspace()
    try:
        paths, process_id, batch_id, document_id, source, _dir, run_id = (
            _captured_document(workspace)
        )
        (paths.batches_dir / batch_id / "batch_index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "batch_id": batch_id,
                    "documents": [
                        {
                            "document_id": document_id,
                            "original_file_name": "source.pdf",
                            "stored_file_name": source.name,
                            "relative_path": source.relative_to(
                                paths.batches_dir / batch_id
                            ).as_posix(),
                            "size_bytes": source.stat().st_size,
                            "status": "imported",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            connection.execute("DELETE FROM predictions")
            connection.execute("DELETE FROM invoice_instances")
            connection.execute("DELETE FROM extraction_runs WHERE run_id = ?", (run_id,))
            connection.commit()

        prepare_review_operation(
            operation_id="recover-run-operation",
            event_kind="invoice_save",
            outcome="saved",
            record_id="record-1",
            invoice_payload={
                "invoiceId": "invoice-001",
                "manifestInvoiceId": "invoice-001",
                "fields": [_field("totale", "85,79")],
            },
            review_batch_base_path="review-1",
            process_id=process_id,
            source_batch_id=batch_id,
            document_id=document_id,
            source_file_name="source.pdf",
            runtime_paths=paths,
        )

        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            recovered = connection.execute(
                "SELECT status FROM extraction_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        assert recovered == ("completed",)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_archived_invoice_source_is_available_in_portable_export() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        source_path = paths.archive_invoices_dir / "parent.pdf"
        source_path.write_bytes(b"%PDF-archived-invoice")
        document_dir = paths.manifests_dir / "process-1" / "documents" / "doc-1"
        document_dir.mkdir(parents=True)
        (document_dir / "viewer_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pages": [{"page": 1, "width": 100, "height": 100, "tokens": []}],
                    "invoices": [
                        {
                            "invoice_id": "invoice-001",
                            "page_numbers": [1],
                            "extraction_fields": [],
                            "fields": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        capture_extraction_document(
            process_id="process-1",
            source_batch_id="batch-1",
            document_id="doc-1",
            source_file_name="parent.pdf",
            source_path=source_path,
            document_dir=document_dir,
            runtime_paths=paths,
            published=True,
        )

        output_dir = export_feedback_bundle(workspace / "feedback-export", paths)
        assert collection_stats(paths)["image_unavailable"] == 0
        assert len(list((output_dir / "pdf").glob("*.pdf"))) == 1
        bundle = json.loads((output_dir / "bundle_manifest.json").read_text("utf-8"))
        assert bundle["missing_pdf_asset_ids"] == []

        mark_archive_paths_unavailable(["archive/invoices/parent.pdf"], paths)
        assert collection_stats(paths)["image_unavailable"] == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_copied_reprocessed_invoice_resolves_its_parent_archive() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        archived_path = paths.archive_invoices_dir / "parent.pdf"
        archived_path.write_bytes(b"%PDF-archived-invoice")
        copied_source = workspace / "doc_000001.pdf"
        shutil.copy2(archived_path, copied_source)
        paths.invoice_registry_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [
                        {
                            "record_id": "parent-record",
                            "status": "saved",
                            "archived_pdf_path": "archive/invoices/parent.pdf",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        document_dir = paths.manifests_dir / "process-1" / "documents" / "doc-1"
        document_dir.mkdir(parents=True)
        (document_dir / "viewer_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pages": [],
                    "invoices": [
                        {
                            "invoice_id": "invoice-001",
                            "page_numbers": [1],
                            "extraction_fields": [],
                            "fields": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        capture_extraction_document(
            process_id="process-1",
            source_batch_id="batch-1",
            document_id="doc-1",
            source_file_name="parent.pdf",
            source_path=copied_source,
            document_dir=document_dir,
            runtime_paths=paths,
            parent_invoice_record_id="parent-record",
            published=True,
        )
        output_dir = export_feedback_bundle(workspace / "feedback-export", paths)

        assert len(list((output_dir / "pdf").glob("*.pdf"))) == 1
        with closing(sqlite3.connect(output_dir / "feedback.sqlite3")) as connection:
            archive_reference, available = connection.execute(
                "SELECT archive_relative_path, available FROM source_assets"
            ).fetchone()
        assert archive_reference == "archive/invoices/parent.pdf"
        assert available == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_export_marks_a_missing_archived_pdf_unavailable_in_snapshot() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        source_path = paths.archive_invoices_dir / "parent.pdf"
        source_path.write_bytes(b"%PDF-archived-invoice")
        document_dir = paths.manifests_dir / "process-1" / "documents" / "doc-1"
        document_dir.mkdir(parents=True)
        (document_dir / "viewer_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pages": [],
                    "invoices": [
                        {
                            "invoice_id": "invoice-001",
                            "page_numbers": [1],
                            "extraction_fields": [],
                            "fields": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        capture_extraction_document(
            process_id="process-1",
            source_batch_id="batch-1",
            document_id="doc-1",
            source_file_name="parent.pdf",
            source_path=source_path,
            document_dir=document_dir,
            runtime_paths=paths,
            published=True,
        )
        source_path.unlink()

        output_dir = export_feedback_bundle(workspace / "feedback-export", paths)
        bundle = json.loads((output_dir / "bundle_manifest.json").read_text("utf-8"))
        with closing(sqlite3.connect(output_dir / "feedback.sqlite3")) as connection:
            available = connection.execute(
                "SELECT available FROM source_assets"
            ).fetchone()[0]

        assert bundle["missing_pdf_asset_ids"]
        assert bundle["stats"]["image_unavailable"] == 1
        assert available == 0
        assert not (output_dir / "pdf").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_export_keeps_legacy_revisions_without_fabricated_evidence() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        prepare_registry_edit_operation(
            operation_id=None,
            record_id="legacy-record",
            previous_invoice_payload={
                "invoiceId": "invoice-legacy",
                "fields": [{"kind": "totale_fattura", "value": "85,79"}],
            },
            updated_invoice_payload={
                "invoiceId": "invoice-legacy",
                "fields": [{"kind": "totale_fattura", "value": "90,00"}],
            },
            legacy_record={"source_file_name": "legacy.pdf"},
            runtime_paths=paths,
        )

        output_dir = export_feedback_bundle(
            workspace / "feedback-export",
            paths,
            include_pdfs=False,
        )
        with closing(sqlite3.connect(output_dir / "feedback.sqlite3")) as connection:
            run = connection.execute(
                "SELECT status, evidence_relative_path FROM extraction_runs"
            ).fetchone()

        assert run == ("completed", "")
        assert not (output_dir / "evidence").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_cancelled_process_removes_only_its_staged_collection() -> None:
    workspace = _workspace()
    try:
        paths, process_id, *_rest = _captured_document(workspace)
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            connection.execute(
                "UPDATE extraction_runs SET status = 'staged' WHERE process_id = ?",
                (process_id,),
            )
            connection.commit()

        assert discard_staged_extraction_process(process_id, paths) == 1
        stats = collection_stats(paths)
        assert stats["runs"] == 0
        assert stats["staged_runs"] == 0
        assert stats["sources"] == 0
        assert list(paths.feedback_evidence_dir.glob("*.json.gz")) == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_source_archive_availability_changes_without_losing_feedback() -> None:
    workspace = _workspace()
    try:
        paths, _process_id, batch_id, document_id, *_rest = _captured_document(
            workspace
        )

        @dataclass(frozen=True)
        class Origin:
            batch_id: str
            document_id: str

        @dataclass(frozen=True)
        class PromotedSource:
            source_id: str
            relative_path: str
            origins: list[Origin]

        update_source_archive_references(
            [
                PromotedSource(
                    source_id="source-archive-1",
                    relative_path="archive/sources/source.pdf",
                    origins=[Origin(batch_id=batch_id, document_id=document_id)],
                )
            ],
            paths,
        )
        assert collection_stats(paths)["image_unavailable"] == 0

        mark_source_archives_unavailable(["source-archive-1"], paths)
        stats = collection_stats(paths)
        assert stats["image_unavailable"] == 1
        assert stats["runs"] == 1
        assert stats["invoices"] == 1
        assert list(paths.feedback_evidence_dir.glob("*.json.gz"))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
