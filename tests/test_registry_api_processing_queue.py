from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
import shutil
import uuid

from estrattore.batch_storage import import_pdf_batch, load_batch_index
from estrattore.processing_storage import (
    ProcessingInterrupted,
    ProcessingRunIndex,
    ReviewBatchRef,
    mark_document_processing_complete,
)
from estrattore.runtime_paths import get_runtime_paths
from estrattore.source_archive_storage import promote_batch_sources
import scripts.registry_api as registry_api


def _workspace() -> Path:
    root = Path("registry-api-queue-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def test_recovers_creation_date_from_legacy_process_id() -> None:
    assert registry_api._recover_legacy_processing_job_created_at(
        {"process_id": "process_20260812_101713_job_000001"}
    ) == "2026-08-12T10:17:13"
    assert registry_api._recover_legacy_processing_job_created_at(
        {"process_id": "process_resume_job_000001"}
    ) == ""


def test_runtime_recovers_review_mutations_before_cleanup_and_review_rebuild(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        startup_events: list[str] = []

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "_REGISTRY_RUNTIME_READY", False)
        monkeypatch.setattr(
            registry_api,
            "cleanup_stale_atomic_write_files",
            lambda _paths: startup_events.append("cleanup_atomic_writes") or [],
        )
        monkeypatch.setattr(
            registry_api,
            "recover_review_mutations",
            lambda _paths: startup_events.append("recover_mutations"),
        )
        monkeypatch.setattr(
            registry_api,
            "cleanup_incomplete_processing_runs",
            lambda _paths, **_kwargs: startup_events.append("cleanup") or [],
        )
        monkeypatch.setattr(
            registry_api,
            "retire_completed_review_batches",
            lambda _paths: startup_events.append("retire_reviews") or [],
        )
        monkeypatch.setattr(
            registry_api,
            "recover_missing_review_batches",
            lambda _paths: startup_events.append("recover_reviews"),
        )
        monkeypatch.setattr(
            registry_api,
            "reconcile_feedback_operations",
            lambda _records, _paths: startup_events.append("recover_feedback_edits"),
        )
        monkeypatch.setattr(registry_api, "_start_next_processing_job_if_idle", lambda: None)

        registry_api.ensure_registry_runtime_ready()

        assert startup_events == [
            "cleanup_atomic_writes",
            "recover_mutations",
            "recover_feedback_edits",
            "cleanup",
            "retire_reviews",
            "recover_reviews",
        ]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_processing_jobs_run_serially(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source_a = workspace / "a.pdf"
        source_b = workspace / "b.pdf"
        source_a.write_bytes(b"%PDF-a")
        source_b.write_bytes(b"%PDF-b")
        batch_a = import_pdf_batch(
            [source_a],
            runtime_paths=runtime_paths,
            now=datetime(2026, 8, 11, 10, 0, 0),
        )
        batch_b = import_pdf_batch(
            [source_b],
            runtime_paths=runtime_paths,
            now=datetime(2026, 8, 11, 10, 1, 0),
        )

        first_job_can_finish = threading.Event()
        started_batches: list[str] = []

        def fake_process_imported_batch(batch_id: str, **_kwargs) -> ProcessingRunIndex:
            started_batches.append(batch_id)
            if batch_id == batch_a.batch_id:
                first_job_can_finish.wait(timeout=2)
            return ProcessingRunIndex(
                schema_version=1,
                process_id=f"process_{batch_id}",
                source_batch_id=batch_id,
                created_at="2026-08-11T10:00:00",
                status="processed",
                document_count=1,
                invoice_count=1,
                review_batch_count=1,
                review_batches=[
                    ReviewBatchRef(
                        id=f"{batch_id}:review_batch_0001",
                        label="Revisione 001 (1-1)",
                        base_path=(
                            f"/runtime/manifests/process_{batch_id}/"
                            "review_batches/review_batch_0001"
                        ),
                        invoice_count=1,
                    )
                ],
                errors=[],
            )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", None)
        monkeypatch.setattr(
            registry_api,
            "process_imported_batch",
            fake_process_imported_batch,
        )

        first_job = registry_api._start_processing_job({"batch_id": batch_a.batch_id})
        second_job = registry_api._start_processing_job({"batch_id": batch_b.batch_id})

        assert first_job["status"] in {"queued", "processing"}
        assert second_job["status"] == "queued"
        assert registry_api._processing_job(second_job["job_id"])["status"] == "queued"

        first_job_can_finish.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if registry_api._processing_job(second_job["job_id"]).get("status") == "completed":
                break
            time.sleep(0.02)

        assert started_batches == [batch_a.batch_id, batch_b.batch_id]
        assert registry_api._processing_job(first_job["job_id"])["status"] == "completed"
        assert registry_api._processing_job(second_job["job_id"])["status"] == "completed"
        assert not (runtime_paths.batches_dir / batch_a.batch_id).exists()
        assert not (runtime_paths.batches_dir / batch_b.batch_id).exists()
        assert len(list(runtime_paths.archive_sources_dir.glob("*.pdf"))) == 2
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_processing_jobs_load_persistent_queue_after_restart(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        runtime_paths.ensure_directories()
        jobs_file = runtime_paths.config_dir / "processing_jobs.json"
        jobs_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "active_job_id": "job_000001",
                    "queued_job_ids": ["job_000002"],
                    "jobs": [
                        {
                            "ok": True,
                            "job_id": "job_000001",
                            "process_id": "process_20260811_100000_job_000001",
                            "batch_id": "batch_a",
                            "status": "processing",
                            "message": "Processamento OCR in corso",
                            "progress_current": 7,
                            "progress_total": 100,
                            "cancel_requested": False,
                        },
                        {
                            "ok": True,
                            "job_id": "job_000002",
                            "batch_id": "batch_b",
                            "status": "queued",
                            "message": "Processamento in coda",
                            "progress_current": 0,
                            "progress_total": 50,
                            "cancel_requested": False,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", None)

        registry_api._load_processing_jobs_from_disk()

        interrupted_job = registry_api._processing_job("job_000001")
        queued_job = registry_api._processing_job("job_000002")
        assert interrupted_job["status"] == "queued"
        assert "Ripresa" in interrupted_job["message"]
        assert interrupted_job["created_at"] == "2026-08-11T10:00:00"
        assert queued_job["status"] == "queued"
        assert registry_api.PROCESSING_JOB_QUEUE == ["job_000001", "job_000002"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_shutdown_interruption_retains_active_job_and_staging(monkeypatch) -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        batch_id = "batch_a"
        process_id = "process_a"
        batch_dir = paths.batches_dir / batch_id
        process_dir = paths.manifests_dir / process_id
        batch_dir.mkdir()
        process_dir.mkdir()
        (batch_dir / "source.pdf").write_bytes(b"%PDF")
        (process_dir / "checkpoint").write_text("done", encoding="utf-8")
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {
            "job_a": {
                "job_id": "job_a", "batch_id": batch_id, "process_id": process_id,
                "status": "stopping", "interrupt_requested": True,
                "cancel_requested": False, "files": [], "progress_total": 2,
            },
            "job_b": {
                "job_id": "job_b", "batch_id": "batch_b", "status": "queued",
                "cancel_requested": False, "files": [], "progress_total": 1,
            },
        })
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["job_b"])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", "job_a")
        monkeypatch.setattr(registry_api, "PROCESSING_SHUTDOWN_REQUESTED", True)
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_IDLE", threading.Event())
        monkeypatch.setattr(
            registry_api, "process_imported_batch",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ProcessingInterrupted()),
        )

        registry_api._run_processing_job("job_a", batch_id)

        assert registry_api.PROCESSING_JOBS["job_a"]["status"] == "queued"
        assert registry_api.PROCESSING_JOB_QUEUE == ["job_a", "job_b"]
        assert (batch_dir / "source.pdf").is_file()
        assert (process_dir / "checkpoint").is_file()
        assert registry_api.PROCESSING_JOB_IDLE.is_set()

        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        registry_api._load_processing_jobs_from_disk()
        assert registry_api.PROCESSING_JOB_QUEUE == ["job_a", "job_b"]
        assert registry_api._resumable_processing_process_ids() == {process_id}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_restart_prioritizes_interrupted_active_job_over_queued_jobs(monkeypatch) -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        jobs_file = paths.config_dir / "processing_jobs.json"
        jobs_file.write_text(json.dumps({
            "schema_version": 1,
            "active_job_id": "job_z",
            "queued_job_ids": ["job_b", "job_a"],
            "jobs": [
                {"job_id": "job_a", "batch_id": "batch_a", "status": "queued", "files": []},
                {"job_id": "job_b", "batch_id": "batch_b", "status": "queued", "files": []},
                {"job_id": "job_z", "batch_id": "batch_z", "process_id": "process_z",
                 "status": "stopping", "interrupt_requested": True, "files": []},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", None)

        registry_api._load_processing_jobs_from_disk()

        assert registry_api.PROCESSING_JOB_QUEUE == ["job_z", "job_b", "job_a"]
        assert registry_api._processing_job("job_z")["status"] == "queued"
        assert registry_api._processing_job("job_z")["interrupt_requested"] is False
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_processing_jobs_migrate_legacy_pause_and_interrupted_cancel(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        runtime_paths.ensure_directories()
        cancelled_process_dir = runtime_paths.manifests_dir / "process_cancel"
        cancelled_process_dir.mkdir(parents=True)
        (cancelled_process_dir / "partial.txt").write_text("partial", encoding="utf-8")
        paused_process_dir = runtime_paths.manifests_dir / "process_pause"
        paused_process_dir.mkdir(parents=True)
        (paused_process_dir / "partial.txt").write_text("partial", encoding="utf-8")
        jobs_file = runtime_paths.config_dir / "processing_jobs.json"
        jobs_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "jobs": [
                        {
                            "job_id": "job_pause",
                            "process_id": "process_pause",
                            "batch_id": "batch_a",
                            "status": "pausing",
                            "pause_requested": True,
                            "cancel_requested": False,
                        },
                        {
                            "job_id": "job_cancel",
                            "process_id": "process_cancel",
                            "batch_id": "batch_b",
                            "status": "cancelling",
                            "pause_requested": False,
                            "cancel_requested": True,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", None)

        registry_api._load_processing_jobs_from_disk()

        migrated_pause = registry_api._processing_job("job_pause")
        cancelled = registry_api._processing_job("job_cancel")
        assert migrated_pause["status"] == "cancelled"
        assert "pause_requested" not in migrated_pause
        assert cancelled["status"] == "cancelled"
        assert not paused_process_dir.exists()
        assert not cancelled_process_dir.exists()
        assert registry_api.PROCESSING_JOB_QUEUE == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_delete_imported_batch_rejects_active_processing_job(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "a.pdf"
        source_file.write_bytes(b"%PDF-a")
        batch = import_pdf_batch(
            [source_file],
            runtime_paths=runtime_paths,
            now=datetime(2026, 8, 11, 10, 0, 0),
        )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_000001": {
                    "job_id": "job_000001",
                    "batch_id": batch.batch_id,
                    "status": "queued",
                }
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["job_000001"])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", None)

        try:
            registry_api._delete_imported_batch(f"/batches/{batch.batch_id}")
        except ValueError as error:
            assert "lavorazione" in str(error)
        else:
            raise AssertionError("Expected active job protection")

        assert (runtime_paths.batches_dir / batch.batch_id).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_delete_imported_batch_removes_batch_from_api_payload(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "a.pdf"
        source_file.write_bytes(b"%PDF-a")
        batch = import_pdf_batch(
            [source_file],
            runtime_paths=runtime_paths,
            now=datetime(2026, 8, 11, 10, 0, 0),
        )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", None)

        payload = registry_api._delete_imported_batch(f"/batches/{batch.batch_id}")

        assert payload["batch_count"] == 0
        assert payload["batches"] == []
        assert not (runtime_paths.batches_dir / batch.batch_id).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_processing_time_estimate_ignores_resumed_documents(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        process_id = "process_resume_estimate_job_000001"
        documents_dir = runtime_paths.manifests_dir / process_id / "documents"
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        for document_number in range(1, 8):
            output_dir = documents_dir / f"document_{document_number:04d}"
            output_dir.mkdir(parents=True)
            (output_dir / "viewer_manifest.json").write_text("{}", encoding="utf-8")
            mark_document_processing_complete(
                output_dir=output_dir,
                document_id=f"document_{document_number:04d}",
                invoice_count=1,
            )

        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_000001": {
                    "job_id": "job_000001",
                    "process_id": process_id,
                    "started_at_monotonic": time.monotonic() - 90,
                    "estimate_progress_baseline": registry_api._completed_document_count_for_process(
                        process_id
                    ),
                }
            },
        )

        early_estimate = registry_api._processing_time_estimate(
            "job_000001",
            progress_current=8,
            progress_total=20,
        )
        stable_estimate = registry_api._processing_time_estimate(
            "job_000001",
            progress_current=10,
            progress_total=20,
        )

        assert early_estimate["estimated_remaining_seconds"] is None
        assert stable_estimate["estimated_remaining_seconds"] is not None
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_active_processing_job_uses_durable_cancelling_state(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_cancel": {
                    "job_id": "job_cancel",
                    "batch_id": "batch_b",
                    "status": "processing",
                    "cancel_requested": False,
                },
            },
        )

        cancelled = registry_api._cancel_processing_job(
            "/processing/jobs/job_cancel/cancel"
        )

        assert cancelled["status"] == "cancelling"
        assert cancelled["cancel_requested"] is True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_queued_processing_job_must_be_removed_instead_of_cancelled(monkeypatch) -> None:
    monkeypatch.setattr(
        registry_api,
        "PROCESSING_JOBS",
        {
            "job_000001": {
                "job_id": "job_000001",
                "batch_id": "batch_a",
                "status": "queued",
                "cancel_requested": False,
            }
        },
    )
    monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["job_000001"])

    try:
        registry_api._cancel_processing_job("/processing/jobs/job_000001/cancel")
    except ValueError as error:
        assert "Rimuovi dalla coda" in str(error)
    else:
        raise AssertionError("Expected queued job cancellation to be rejected")

    assert registry_api.PROCESSING_JOB_QUEUE == ["job_000001"]


def test_remove_queued_processing_job_deletes_temporary_batch(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "a.pdf"
        source_file.write_bytes(b"%PDF-a")
        batch = import_pdf_batch(
            [source_file],
            runtime_paths=runtime_paths,
            now=datetime(2026, 8, 11, 10, 0, 0),
        )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_000001": {
                    "job_id": "job_000001",
                    "batch_id": batch.batch_id,
                    "status": "queued",
                    "cancel_requested": False,
                }
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["job_000001"])

        job = registry_api._remove_processing_job("/processing/jobs/job_000001/remove")

        assert job["status"] == "cancelled"
        assert registry_api.PROCESSING_JOB_QUEUE == []
        assert not (runtime_paths.batches_dir / batch.batch_id).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_delete_processing_history_removes_only_terminal_metadata(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        runtime_paths.ensure_directories()
        archived_pdf = runtime_paths.archive_sources_dir / "source.pdf"
        archived_pdf.write_bytes(b"%PDF-source")
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_done": {"job_id": "job_done", "status": "completed"},
                "job_active": {"job_id": "job_active", "status": "processing"},
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])

        result = registry_api._delete_processing_history(
            {"job_ids": ["job_done", "job_active"]}
        )

        assert result["deleted_count"] == 1
        assert "job_done" not in registry_api.PROCESSING_JOBS
        assert "job_active" in registry_api.PROCESSING_JOBS
        assert archived_pdf.is_file()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_finalizing_job_is_completed_without_restarting_ocr(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "source.pdf"
        source.write_bytes(b"%PDF-source")
        batch = import_pdf_batch([source], runtime_paths=runtime_paths)
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_finalizing": {
                    "job_id": "job_finalizing",
                    "batch_id": batch.batch_id,
                    "process_id": "process_finalizing",
                    "status": "finalizing",
                    "files": [],
                    "result": {"invoice_count": 1},
                }
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])

        registry_api._reconcile_finalizing_jobs()

        job = registry_api._processing_job("job_finalizing")
        assert job["status"] == "completed"
        assert job["result"] == {"invoice_count": 1}
        assert not (runtime_paths.batches_dir / batch.batch_id).exists()
        assert len(list(runtime_paths.archive_sources_dir.glob("*.pdf"))) == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_source_used_by_pending_review_cannot_be_deleted(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "source.pdf"
        source.write_bytes(b"%PDF-source")
        batch = import_pdf_batch([source], runtime_paths=runtime_paths)
        archived = promote_batch_sources(batch, "process_pending", runtime_paths)[0]
        review_dir = runtime_paths.manifests_dir / "_review_queue" / "review_0001"
        review_dir.mkdir(parents=True)
        (review_dir / "review_batch_index.json").write_text(
            json.dumps(
                {
                    "invoices": [
                        {
                            "status": "pending",
                            "source_batch_id": batch.batch_id,
                            "document_id": batch.documents[0].document_id,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)

        try:
            registry_api._reject_sources_used_by_pending_reviews([archived])
        except ValueError as error:
            assert "ancora da revisionare" in str(error)
        else:
            raise AssertionError("Expected pending source deletion to be rejected")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_source_used_by_nonterminal_reprocessing_job_cannot_be_deleted(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "source.pdf"
        source.write_bytes(b"%PDF-source")
        batch = import_pdf_batch([source], runtime_paths=runtime_paths)
        archived = promote_batch_sources(batch, "process_original", runtime_paths)[0]
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {})

        for status in ("queued", "processing", "cancelling", "finalizing"):
            registry_api.PROCESSING_JOBS["job_reprocess"] = {
                "status": status,
                "source_archive_bindings": {"doc_000001": archived.source_id},
            }

            try:
                registry_api._reject_sources_used_by_processing_jobs([archived])
            except ValueError as error:
                assert "Annulla o rimuovi" in str(error)
            else:
                raise AssertionError(f"Expected deletion rejection for {status}")

        registry_api.PROCESSING_JOBS["job_reprocess"]["status"] = "completed"
        registry_api._reject_sources_used_by_processing_jobs([archived])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_invoice_library_reprocessing_preserves_lineage_and_skips_source_archive(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        runtime_paths.ensure_directories()
        invoice_pdf = runtime_paths.archive_invoices_dir / "discarded.pdf"
        invoice_pdf.write_bytes(b"%PDF-invoice")
        runtime_paths.invoice_registry_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [
                        {
                            "record_id": "discarded-record",
                            "status": "discarded",
                            "source_file_name": "origine.pdf",
                            "archived_pdf_path": "archive/invoices/discarded.pdf",
                            "archived_pdf_page_numbers": [4, 7],
                            "invoice": {"fields": []},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        captured_payload: dict = {}

        def fake_start_processing_job(payload: dict) -> dict:
            captured_payload.update(payload)
            return {"job_id": "job_invoice", **payload}

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "_start_processing_job",
            fake_start_processing_job,
        )

        registry_api._reprocess_archived_invoices(
            {"record_ids": ["discarded-record"]}
        )

        batch = load_batch_index(
            runtime_paths.batches_dir / str(captured_payload["batch_id"])
        )
        assert captured_payload["source_kind"] == "discarded_invoice"
        assert captured_payload["archive_sources"] is False
        assert captured_payload["parent_invoice_record_ids"] == ["discarded-record"]
        assert batch.documents[0].original_file_name == "origine.pdf"
        assert batch.documents[0].parent_invoice_record_id == "discarded-record"
        assert batch.documents[0].source_page_numbers == (4, 7)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_invoice_library_reprocessing_removes_staging_if_job_start_fails(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        runtime_paths.ensure_directories()
        invoice_pdf = runtime_paths.archive_invoices_dir / "saved.pdf"
        invoice_pdf.write_bytes(b"%PDF-invoice")
        runtime_paths.invoice_registry_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [
                        {
                            "record_id": "saved-record",
                            "status": "saved",
                            "source_file_name": "origine.pdf",
                            "archived_pdf_path": "archive/invoices/saved.pdf",
                            "archived_pdf_page_numbers": [2],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)

        def fail_job_start(_payload: dict) -> dict:
            raise RuntimeError("job start failed")

        monkeypatch.setattr(registry_api, "_start_processing_job", fail_job_start)

        try:
            registry_api._reprocess_archived_invoices(
                {"record_ids": ["saved-record"]}
            )
        except RuntimeError as error:
            assert str(error) == "job start failed"
        else:
            raise AssertionError("Expected job start failure")

        assert list(runtime_paths.batches_dir.iterdir()) == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_invoice_library_finalization_deletes_staging_without_promoting_source(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "invoice.pdf"
        source.write_bytes(b"%PDF-invoice")
        batch = import_pdf_batch([source], runtime_paths=runtime_paths)
        job = {
            "job_id": "job_invoice",
            "batch_id": batch.batch_id,
            "process_id": "process_invoice",
            "status": "finalizing",
            "archive_sources": False,
            "source_kind": "discarded_invoice",
            "files": [],
        }
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {"job_invoice": job})
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])

        registry_api._reconcile_finalizing_jobs()

        assert registry_api.PROCESSING_JOBS["job_invoice"]["status"] == "completed"
        assert not (runtime_paths.batches_dir / batch.batch_id).exists()
        assert list(runtime_paths.archive_sources_dir.glob("*.pdf")) == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_invoice_used_by_nonterminal_reprocessing_job_cannot_be_deleted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        registry_api,
        "PROCESSING_JOBS",
        {
            "job_invoice": {
                "status": "processing",
                "parent_invoice_record_ids": ["invoice-record"],
            }
        },
    )

    try:
        registry_api._reject_invoice_records_used_by_processing_jobs(
            ["invoice-record"]
        )
    except ValueError as error:
        assert "Annulla o rimuovi" in str(error)
    else:
        raise AssertionError("Expected invoice deletion rejection")


def test_invoice_used_by_pending_reprocessed_review_cannot_be_deleted(
    monkeypatch,
) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        review_dir = runtime_paths.manifests_dir / "_review_queue" / "review_0001"
        review_dir.mkdir(parents=True)
        (review_dir / "review_batch_index.json").write_text(
            json.dumps(
                {
                    "invoices": [
                        {
                            "status": "pending",
                            "parent_invoice_record_id": "invoice-record",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)

        try:
            registry_api._reject_invoice_records_used_by_pending_reviews(
                ["invoice-record"]
            )
        except ValueError as error:
            assert "ancora da revisionare" in str(error)
        else:
            raise AssertionError("Expected pending review deletion rejection")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_import_pending_sources_creates_single_batch_from_multiple_sources(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        folder = workspace / "Giugno"
        folder.mkdir()
        folder_pdf = folder / "a.pdf"
        loose_pdf = workspace / "singola.pdf"
        folder_pdf.write_bytes(b"%PDF-folder")
        loose_pdf.write_bytes(b"%PDF-loose")

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PENDING_IMPORT_SOURCES", {})

        folder_source = registry_api._new_folder_import_source(folder)
        files_source = registry_api._new_files_import_source([loose_pdf])

        batch = registry_api._import_pending_sources(
            {
                "source_ids": [
                    folder_source["source_id"],
                    files_source["source_id"],
                ]
            }
        )

        assert batch.document_count == 2
        assert batch.source_label == "2 sorgenti"
        assert [document.original_file_name for document in batch.documents] == [
            "Giugno/a.pdf",
            "singola.pdf",
        ]
        assert not registry_api.PENDING_IMPORT_SOURCES
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_import_pending_sources_rejects_duplicate_source(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        folder = workspace / "Giugno"
        folder.mkdir()
        (folder / "a.pdf").write_bytes(b"%PDF-folder")

        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(registry_api, "PENDING_IMPORT_SOURCES", {})

        source = registry_api._new_folder_import_source(folder)

        try:
            registry_api._import_pending_sources(
                {"source_ids": [source["source_id"], source["source_id"]]}
        )
        except ValueError as error:
            assert "duplicate" in str(error).lower()
        else:
            raise AssertionError("Expected duplicate source rejection")

        assert registry_api.PENDING_IMPORT_SOURCES[source["source_id"]]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
