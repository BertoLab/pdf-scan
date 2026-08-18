from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
import shutil
import uuid

from estrattore.batch_storage import import_pdf_batch
from estrattore.processing_storage import (
    ProcessingRunIndex,
    ReviewBatchRef,
    mark_document_processing_complete,
)
from estrattore.runtime_paths import get_runtime_paths
import scripts.registry_api as registry_api


def _workspace() -> Path:
    root = Path("registry-api-queue-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


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
                            "process_id": "process_resume_job_000001",
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
        assert queued_job["status"] == "queued"
        assert registry_api.PROCESSING_JOB_QUEUE == ["job_000001", "job_000002"]
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


def test_pause_queued_processing_job_removes_it_from_queue(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_000001": {
                    "job_id": "job_000001",
                    "batch_id": "batch_a",
                    "status": "queued",
                    "cancel_requested": False,
                    "pause_requested": False,
                }
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["job_000001"])

        job = registry_api._pause_processing_job("/processing/jobs/job_000001/pause")

        assert job["status"] == "paused"
        assert registry_api.PROCESSING_JOB_QUEUE == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_resume_paused_processing_job_puts_it_back_in_queue(monkeypatch) -> None:
    workspace = _workspace()
    try:
        runtime_paths = get_runtime_paths(workspace / "app-data")
        monkeypatch.setattr(registry_api, "RUNTIME_PATHS", runtime_paths)
        monkeypatch.setattr(
            registry_api,
            "PROCESSING_JOBS",
            {
                "job_000001": {
                    "job_id": "job_000001",
                    "batch_id": "batch_a",
                    "status": "paused",
                    "cancel_requested": False,
                    "pause_requested": False,
                }
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", [])
        monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", "job_active")

        job = registry_api._resume_processing_job("/processing/jobs/job_000001/resume")

        assert job["status"] == "queued"
        assert registry_api.PROCESSING_JOB_QUEUE == ["job_000001"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_remove_queued_processing_job_does_not_delete_imported_batch(monkeypatch) -> None:
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
                    "pause_requested": False,
                }
            },
        )
        monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["job_000001"])

        job = registry_api._remove_processing_job("/processing/jobs/job_000001/remove")

        assert job["status"] == "removed"
        assert registry_api.PROCESSING_JOB_QUEUE == []
        assert (runtime_paths.batches_dir / batch.batch_id).is_dir()
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
