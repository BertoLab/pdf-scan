import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
import shutil
import uuid

import pytest

from estrattore import processing_storage
from estrattore.batch_storage import import_pdf_batch
from estrattore.document_supervisor import WorkerCancelled
from estrattore.document_worker import (
    DOCUMENT_WORKER_SCHEMA_VERSION,
    DocumentWorkerResult,
)
from estrattore.processing_storage import (
    DOCUMENT_COMPLETE_FILE_NAME,
    PROCESSING_COMPLETE_FILE_NAME,
    ProcessingCancelled,
    ProcessingInterrupted,
    clear_all_review_batches,
    cleanup_incomplete_processing_runs,
    delete_review_invoice_drafts,
    load_processing_run_index,
    list_review_invoice_drafts,
    list_review_batches,
    mark_review_invoice,
    mutate_review_invoice_segmentation,
    process_imported_batch,
    recover_missing_review_batches,
    recover_review_mutations,
    reprocess_review_batch,
    retire_completed_review_batches,
    review_batch_dir_from_base_path,
    review_batch_base_path_by_number,
    save_review_invoice_draft,
)
from estrattore.runtime_paths import get_runtime_paths
from estrattore.source_archive_storage import promote_batch_sources


def _workspace() -> Path:
    root = Path("processing-storage-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _clean_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_default_processing_uses_isolated_document_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        executions = []

        def fake_worker(execution, **checks):
            del checks
            executions.append(execution)
            execution.output_dir.mkdir(parents=True)
            (execution.output_dir / "viewer_manifest.json").write_text(
                json.dumps(
                    {
                        "invoices": [
                            {
                                "invoice_id": "invoice-001",
                                "title": "Fattura 1",
                                "fields": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            processing_storage.mark_document_processing_complete(
                execution.output_dir,
                execution.document_id,
                1,
            )
            return DocumentWorkerResult(
                schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
                status="completed",
                document_id=execution.document_id,
                invoice_count=1,
            )

        monkeypatch.setattr(processing_storage, "run_document_worker", fake_worker)

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        assert run_index.status == "processed"
        assert run_index.invoice_count == 1
        assert len(executions) == 1
        assert executions[0].first_invoice_number == 1
    finally:
        _clean_workspace(workspace)


def test_worker_cancellation_removes_incomplete_processing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        process_id = "process_interrupted"

        def interrupted_worker(execution, **checks):
            del checks
            execution.working_output_dir.mkdir(parents=True)
            raise WorkerCancelled("cancel")

        monkeypatch.setattr(
            processing_storage,
            "run_document_worker",
            interrupted_worker,
        )

        with pytest.raises(ProcessingCancelled):
            process_imported_batch(
                imported_batch.batch_id,
                runtime_paths=paths,
                process_id=process_id,
            )

        process_dir = paths.manifests_dir / process_id
        assert not process_dir.exists()
        assert not (
            process_dir / "documents" / "doc_000001.working"
        ).exists()
    finally:
        _clean_workspace(workspace)


def test_shutdown_interruption_keeps_completed_pdf_checkpoint_for_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    try:
        sources = [workspace / "first.pdf", workspace / "second.pdf"]
        for source in sources:
            source.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        batch = import_pdf_batch(sources, runtime_paths=paths)
        process_id = "process_resume_after_close"
        worker_calls: list[str] = []
        interrupted = True

        def fake_worker(execution, **_checks):
            worker_calls.append(execution.document_id)
            if interrupted and len(worker_calls) == 2:
                execution.working_output_dir.mkdir(parents=True)
                raise WorkerCancelled("desktop closing")
            execution.output_dir.mkdir(parents=True)
            (execution.output_dir / "viewer_manifest.json").write_text(
                json.dumps({"invoices": [{
                    "invoice_id": f"invoice-{execution.first_invoice_number:03d}",
                    "title": "Fattura", "fields": [],
                }]}),
                encoding="utf-8",
            )
            processing_storage.mark_document_processing_complete(
                execution.output_dir, execution.document_id, 1,
            )

        monkeypatch.setattr(processing_storage, "run_document_worker", fake_worker)
        with pytest.raises(ProcessingInterrupted):
            process_imported_batch(
                batch.batch_id, runtime_paths=paths, process_id=process_id,
                resume_existing=True,
                should_interrupt=lambda: interrupted and len(worker_calls) >= 2,
            )

        process_dir = paths.manifests_dir / process_id
        assert (process_dir / "documents" / batch.documents[0].document_id / DOCUMENT_COMPLETE_FILE_NAME).is_file()
        assert not (process_dir / "documents" / f"{batch.documents[1].document_id}.working").exists()
        assert (paths.batches_dir / batch.batch_id).is_dir()

        interrupted = False
        result = process_imported_batch(
            batch.batch_id, runtime_paths=paths, process_id=process_id,
            resume_existing=True,
        )
        assert result.invoice_count == 2
        assert worker_calls == [
            batch.documents[0].document_id,
            batch.documents[1].document_id,
            batch.documents[1].document_id,
        ]
    finally:
        _clean_workspace(workspace)


def test_process_imported_batch_creates_review_batches_by_invoice_count() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            invoices = [
                {
                    "invoice_id": f"invoice-{first_invoice_number + index:03d}",
                    "title": f"Fattura {first_invoice_number + index:03d}",
                    "page_numbers": [index + 1],
                    "fields": [],
                }
                for index in range(45)
            ]
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": "source.pdf",
                        "pages": [],
                        "invoices": invoices,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=20,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        assert run_index.status == "processed"
        assert run_index.invoice_count == 45
        assert [batch.invoice_count for batch in run_index.review_batches] == [20, 20, 5]
        assert (
            paths.manifests_dir / run_index.process_id / PROCESSING_COMPLETE_FILE_NAME
        ).is_file()

        first_review_index_path = (
            review_batch_dir_from_base_path(run_index.review_batches[0].base_path, paths)
            / "viewer_index.json"
        )
        first_review_index = json.loads(first_review_index_path.read_text(encoding="utf-8"))
        assert first_review_index["documents"] == [
            {
                "id": "doc_000001",
                "base_path": (
                    f"/runtime/manifests/{run_index.process_id}/documents/doc_000001"
                ),
                "review_invoice_ids": [f"invoice-{index:03d}" for index in range(1, 21)],
            }
        ]

        assert [batch.id for batch in list_review_batches(paths)] == [
            batch.id for batch in run_index.review_batches
        ]
    finally:
        _clean_workspace(workspace)


def test_clear_all_review_batches_removes_review_queue_without_documents() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            invoices = [
                {
                    "invoice_id": f"invoice-{first_invoice_number + index:03d}",
                    "title": f"Fattura {first_invoice_number + index:03d}",
                    "page_numbers": [index + 1],
                    "fields": [],
                }
                for index in range(3)
            ]
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": "source.pdf",
                        "pages": [],
                        "invoices": invoices,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=2,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        assert len(list_review_batches(paths)) == 2
        save_review_invoice_draft(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "fields": [],
            },
            paths,
        )
        assert len(list_review_invoice_drafts(paths)["drafts"]) == 1

        result = clear_all_review_batches(paths)

        assert result.deleted_review_batch_count == 2
        assert result.updated_processing_run_count == 1
        assert list_review_batches(paths) == []
        assert list_review_invoice_drafts(paths)["drafts"] == []
        assert (
            paths.manifests_dir
            / run_index.process_id
            / "documents"
            / "doc_000001"
            / "viewer_manifest.json"
        ).is_file()
        assert load_processing_run_index(
            paths.manifests_dir / run_index.process_id
        ).review_batches == []
    finally:
        _clean_workspace(workspace)


def test_process_imported_batch_continues_after_document_error() -> None:
    workspace = _workspace()
    try:
        source_a = workspace / "a.pdf"
        source_b = workspace / "b.pdf"
        source_a.write_bytes(b"%PDF-a")
        source_b.write_bytes(b"%PDF-b")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_a, source_b],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(source_path: Path, output_dir: Path, first_invoice_number: int):
            if source_path.name == "doc_000001.pdf":
                raise RuntimeError("OCR fallito")
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": source_path.name,
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        assert run_index.status == "processed_with_errors"
        assert run_index.invoice_count == 1
        assert run_index.errors == ["a.pdf: OCR fallito"]
    finally:
        _clean_workspace(workspace)


def test_process_imported_batch_reports_document_progress() -> None:
    workspace = _workspace()
    try:
        source_a = workspace / "a.pdf"
        source_b = workspace / "b.pdf"
        source_a.write_bytes(b"%PDF-a")
        source_b.write_bytes(b"%PDF-b")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_a, source_b],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        progress_events: list[tuple[int, int, str]] = []

        def fake_processor(source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": source_path.name,
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            progress_callback=lambda current, total, file_name: progress_events.append(
                (current, total, file_name)
            ),
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        assert progress_events == [
            (1, 2, "a.pdf"),
            (2, 2, "b.pdf"),
        ]
    finally:
        _clean_workspace(workspace)


def test_process_imported_batch_resumes_completed_documents() -> None:
    workspace = _workspace()
    try:
        source_a = workspace / "a.pdf"
        source_b = workspace / "b.pdf"
        source_c = workspace / "c.pdf"
        source_a.write_bytes(b"%PDF-a")
        source_b.write_bytes(b"%PDF-b")
        source_c.write_bytes(b"%PDF-c")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_a, source_b, source_c],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        process_id = "process_resume_test"
        completed_doc_dir = paths.manifests_dir / process_id / "documents" / "doc_000001"
        completed_doc_dir.mkdir(parents=True)
        completed_manifest_path = completed_doc_dir / "viewer_manifest.json"
        completed_manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_file": "a.pdf",
                    "pages": [],
                    "invoices": [
                        {
                            "invoice_id": "invoice-001",
                            "title": "Fattura 001",
                            "page_numbers": [1],
                            "fields": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (completed_doc_dir / DOCUMENT_COMPLETE_FILE_NAME).write_text(
            "{}",
            encoding="utf-8",
        )
        called_documents: list[tuple[str, int]] = []

        def fake_processor(source_path: Path, output_dir: Path, first_invoice_number: int):
            called_documents.append((source_path.name, first_invoice_number))
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": source_path.name,
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            process_id=process_id,
            resume_existing=True,
            review_batch_size=10,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        assert called_documents == [
            ("doc_000002.pdf", 2),
            ("doc_000003.pdf", 3),
        ]
        assert run_index.invoice_count == 3
        assert run_index.review_batch_count == 1
        assert (paths.manifests_dir / process_id / PROCESSING_COMPLETE_FILE_NAME).is_file()
        assert (
            paths.manifests_dir
            / process_id
            / "documents"
            / "doc_000003"
            / DOCUMENT_COMPLETE_FILE_NAME
        ).is_file()
    finally:
        _clean_workspace(workspace)


def test_process_imported_batch_can_be_cancelled_between_documents() -> None:
    workspace = _workspace()
    try:
        source_a = workspace / "a.pdf"
        source_b = workspace / "b.pdf"
        source_a.write_bytes(b"%PDF-a")
        source_b.write_bytes(b"%PDF-b")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_a, source_b],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        processed_count = 0

        def fake_processor(source_path: Path, output_dir: Path, first_invoice_number: int):
            nonlocal processed_count
            processed_count += 1
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": source_path.name,
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        try:
            process_imported_batch(
                imported_batch.batch_id,
                runtime_paths=paths,
                document_processor=fake_processor,
                should_cancel=lambda: processed_count >= 1,
                now=datetime(2026, 8, 10, 11, 0, 0),
            )
        except ProcessingCancelled:
            pass
        else:
            raise AssertionError("Expected processing cancellation")

        run_indexes = list(paths.manifests_dir.glob("*/processing_run_index.json"))
        assert run_indexes == []
        assert list_review_batches(paths) == []
    finally:
        _clean_workspace(workspace)


def test_cleanup_incomplete_processing_runs_removes_crashed_run() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        process_dir = paths.manifests_dir / "process_20260810_110000"
        process_dir.mkdir(parents=True)
        (process_dir / "documents" / "doc_000001").mkdir(parents=True)
        (process_dir / "processing_run_index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "process_id": "process_20260810_110000",
                    "source_batch_id": "batch_20260810_100000",
                    "created_at": "2026-08-10T11:00:00",
                    "status": "processing",
                    "document_count": 10,
                    "invoice_count": 1,
                    "review_batch_count": 0,
                    "review_batches": [],
                    "errors": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        removed = cleanup_incomplete_processing_runs(paths)

        assert removed == [process_dir]
        assert not process_dir.exists()
        assert list_review_batches(paths) == []
    finally:
        _clean_workspace(workspace)


def test_cleanup_incomplete_processing_runs_preserves_resumable_run() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        process_dir = paths.manifests_dir / "process_resumable"
        process_dir.mkdir(parents=True)
        (process_dir / "processing_run_index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "process_id": "process_resumable",
                    "source_batch_id": "batch_20260810_100000",
                    "created_at": "2026-08-10T11:00:00",
                    "status": "processing",
                    "document_count": 10,
                    "invoice_count": 1,
                    "review_batch_count": 0,
                    "review_batches": [],
                    "errors": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        removed = cleanup_incomplete_processing_runs(
            paths,
            preserve_process_ids={"process_resumable"},
        )

        assert removed == []
        assert process_dir.exists()
    finally:
        _clean_workspace(workspace)


def test_cleanup_incomplete_processing_runs_preserves_technical_directories() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        review_queue_dir = paths.manifests_dir / "_review_queue"
        mutation_dir = paths.manifests_dir / "_review_mutations"
        unknown_dir = paths.manifests_dir / "future_projection"
        for directory in (review_queue_dir, mutation_dir, unknown_dir):
            directory.mkdir(parents=True)
            (directory / "sentinel.json").write_text("{}", encoding="utf-8")

        broken_process_dir = paths.manifests_dir / "process_broken"
        broken_process_dir.mkdir()

        removed = cleanup_incomplete_processing_runs(paths)

        assert removed == [broken_process_dir]
        assert not broken_process_dir.exists()
        assert (review_queue_dir / "sentinel.json").is_file()
        assert (mutation_dir / "sentinel.json").is_file()
        assert (unknown_dir / "sentinel.json").is_file()
    finally:
        _clean_workspace(workspace)


def test_process_imported_batch_persists_error_run_index_on_critical_failure() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, _first_invoice_number: int):
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
                                "invoice_id": "invoice-001",
                                "title": "Fattura 001",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        try:
            process_imported_batch(
                imported_batch.batch_id,
                runtime_paths=paths,
                document_processor=fake_processor,
                review_batch_size=0,
                now=datetime(2026, 8, 10, 11, 0, 0),
            )
        except ValueError:
            pass

        process_dir = paths.manifests_dir / "process_20260810_110000"
        run_index = load_processing_run_index(process_dir)
        assert run_index.status == "error"
        assert run_index.source_batch_id == imported_batch.batch_id
        assert "Errore critico durante il processamento" in run_index.errors[-1]
    finally:
        _clean_workspace(workspace)


def test_mark_review_invoice_cleans_working_files_after_last_document_invoice() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "debug.json").write_text("{}", encoding="utf-8")
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
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            },
                            {
                                "invoice_id": f"invoice-{first_invoice_number + 1:03d}",
                                "title": f"Fattura {first_invoice_number + 1:03d}",
                                "page_numbers": [2],
                                "fields": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=1,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        document_working_dir = (
            paths.manifests_dir / run_index.process_id / "documents" / "doc_000001"
        )
        source_working_pdf = (
            paths.batches_dir / imported_batch.batch_id / "input" / "doc_000001.pdf"
        )
        first_review_dir = review_batch_dir_from_base_path(
            run_index.review_batches[0].base_path,
            paths,
        )
        second_review_dir = review_batch_dir_from_base_path(
            run_index.review_batches[1].base_path,
            paths,
        )

        mark_review_invoice(
            run_index.review_batches[0].base_path,
            "invoice-001",
            "saved",
            paths,
        )

        assert document_working_dir.is_dir()
        assert source_working_pdf.is_file()
        assert not first_review_dir.exists()

        mark_review_invoice(
            run_index.review_batches[1].base_path,
            "invoice-002",
            "discarded",
            paths,
        )

        assert not document_working_dir.exists()
        assert source_working_pdf.exists()
        assert not second_review_dir.exists()
        updated_run = load_processing_run_index(
            paths.manifests_dir / run_index.process_id
        )
        assert updated_run.retired_review_batch_ids == [
            "review_batch_0001",
            "review_batch_0002",
        ]
    finally:
        _clean_workspace(workspace)


def test_startup_retires_zero_pending_review_and_removes_its_draft() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "source_file": "source.pdf",
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_number:03d}",
                                "title": f"Fattura {first_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
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
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        review = run_index.review_batches[0]
        review_dir = review_batch_dir_from_base_path(review.base_path, paths)
        save_review_invoice_draft(
            {
                "review_batch_base_path": review.base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "fields": [{"kind": "numero_fattura", "value": "MANUALE"}],
            },
            paths,
        )

        assert retire_completed_review_batches(paths) == []
        assert review_dir.is_dir()
        assert len(list_review_invoice_drafts(paths)["drafts"]) == 1

        review_index_path = review_dir / "review_batch_index.json"
        review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        review_payload["invoices"][0]["status"] = "saved"
        review_payload["invoice_count"] = 0
        review_index_path.write_text(json.dumps(review_payload), encoding="utf-8")
        (review_dir / "viewer_index.json").write_text(
            json.dumps({"documents": []}),
            encoding="utf-8",
        )

        assert retire_completed_review_batches(paths) == [review_dir]
        assert retire_completed_review_batches(paths) == []
        assert not review_dir.exists()
        assert list_review_invoice_drafts(paths)["drafts"] == []
        assert load_processing_run_index(
            paths.manifests_dir / run_index.process_id
        ).retired_review_batch_ids == [review.id]
        assert recover_missing_review_batches(paths) == 0
        assert not review_dir.exists()
    finally:
        _clean_workspace(workspace)


def test_mark_review_invoice_scopes_equal_local_ids_by_document() -> None:
    workspace = _workspace()
    try:
        source_files = [workspace / "source-a.pdf", workspace / "source-b.pdf"]
        for source_file in source_files:
            source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            source_files,
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, _first_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "invoices": [
                            {
                                "invoice_id": "invoice-001",
                                "title": "Fattura 001",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        review = run_index.review_batches[0]
        review_dir = review_batch_dir_from_base_path(review.base_path, paths)

        mark_review_invoice(
            review.base_path,
            "invoice-001",
            "saved",
            paths,
            document_id="doc_000001",
        )

        review_payload = json.loads(
            (review_dir / "review_batch_index.json").read_text(encoding="utf-8")
        )
        statuses = {
            invoice["document_id"]: invoice["status"]
            for invoice in review_payload["invoices"]
        }
        assert statuses == {"doc_000001": "saved", "doc_000002": "pending"}
        viewer_payload = json.loads(
            (review_dir / "viewer_index.json").read_text(encoding="utf-8")
        )
        assert [document["id"] for document in viewer_payload["documents"]] == [
            "doc_000002"
        ]
    finally:
        _clean_workspace(workspace)


def test_listing_repairs_terminal_duplicate_and_retires_empty_review() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, _first_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "invoices": [
                            {
                                "invoice_id": "invoice-001",
                                "title": "Fattura 001",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        review = run_index.review_batches[0]
        review_dir = review_batch_dir_from_base_path(review.base_path, paths)
        review_index_path = review_dir / "review_batch_index.json"
        review_payload = json.loads(review_index_path.read_text(encoding="utf-8"))
        saved_invoice = {**review_payload["invoices"][0], "status": "saved"}
        pending_duplicate = {**review_payload["invoices"][0], "status": "pending"}
        review_payload["invoices"] = [saved_invoice, pending_duplicate]
        review_payload["invoice_count"] = 1
        review_index_path.write_text(json.dumps(review_payload), encoding="utf-8")
        (review_dir / "viewer_index.json").write_text(
            json.dumps({"documents": []}),
            encoding="utf-8",
        )

        assert list_review_batches(paths) == []
        assert not review_dir.exists()
        assert load_processing_run_index(
            paths.manifests_dir / run_index.process_id
        ).retired_review_batch_ids == [review.id]
    finally:
        _clean_workspace(workspace)


def test_segmentation_boundary_move_updates_document_manifest() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
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
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1, 2],
                                "fields": [],
                            },
                            {
                                "invoice_id": f"invoice-{first_invoice_number + 1:03d}",
                                "title": f"Fattura {first_invoice_number + 1:03d}",
                                "page_numbers": [3],
                                "fields": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        source_draft = save_review_invoice_draft(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "fields": [
                    {
                        "kind": "numero_fattura",
                        "label": "Numero fattura",
                        "value": "MODIFICATO",
                        "score": 1.0,
                    }
                ],
            },
            paths,
        )
        recipient_draft = save_review_invoice_draft(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-002",
                "fields": [
                    {
                        "kind": "numero_fattura",
                        "label": "Numero fattura",
                        "value": "DA CONSERVARE",
                        "score": 1.0,
                    }
                ],
            },
            paths,
        )

        result = mutate_review_invoice_segmentation(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "operation": "move_boundary",
                "direction": "last_to_next",
            },
            runtime_paths=paths,
        )

        assert set(result["affected_invoice_ids"]) == {
            "invoice-001",
            "invoice-002",
        }
        assert list_review_invoice_drafts(paths)["drafts"] == []
        assert delete_review_invoice_drafts(
            [source_draft["draft_id"], recipient_draft["draft_id"]],
            paths,
        ) == 0

        manifest_path = (
            paths.manifests_dir
            / run_index.process_id
            / "documents"
            / "doc_000001"
            / "viewer_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert result["selected_invoice_id"] == "invoice-001"
        assert [invoice["page_numbers"] for invoice in manifest["invoices"]] == [
            [1],
            [2, 3],
        ]
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            segmentation_event = connection.execute(
                """
                SELECT operation, request_json, result_json
                FROM segmentation_events
                """
            ).fetchone()
        assert segmentation_event is not None
        assert segmentation_event[0] == "move_boundary"
        assert json.loads(segmentation_event[1])["direction"] == "last_to_next"
        assert set(json.loads(segmentation_event[2])["affected_invoice_ids"]) == {
            "invoice-001",
            "invoice-002",
        }
    finally:
        _clean_workspace(workspace)


def test_segmentation_boundary_move_can_remove_single_page_invoice() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
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
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            },
                            {
                                "invoice_id": f"invoice-{first_invoice_number + 1:03d}",
                                "title": f"Fattura {first_invoice_number + 1:03d}",
                                "page_numbers": [2],
                                "fields": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        result = mutate_review_invoice_segmentation(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "operation": "move_boundary",
                "direction": "last_to_next",
            },
            runtime_paths=paths,
        )

        assert result["removed_invoice_id"] == "invoice-001"
        assert result["selected_invoice_id"] == "invoice-002"
    finally:
        _clean_workspace(workspace)


def test_segmentation_move_merges_single_page_invoice_and_renumbers_following() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": "source.pdf",
                        "pages": [
                            {"page": page, "targets": []}
                            for page in range(1, 5)
                        ],
                        "invoices": [
                            {
                                "invoice_id": "invoice-001",
                                "display_number": 1,
                                "title": "Fattura 001",
                                "page_numbers": [1, 2],
                                "fields": [],
                            },
                            {
                                "invoice_id": "invoice-002",
                                "display_number": 2,
                                "title": "Fattura 002",
                                "page_numbers": [3],
                                "fields": [],
                            },
                            {
                                "invoice_id": "invoice-003",
                                "display_number": 3,
                                "title": "Fattura 003",
                                "page_numbers": [4],
                                "fields": [],
                            },
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
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        review = run_index.review_batches[0]

        result = mutate_review_invoice_segmentation(
            {
                "review_batch_base_path": review.base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-002",
                "operation": "move_boundary",
                "direction": "first_to_previous",
            },
            paths,
        )

        manifest_path = (
            paths.manifests_dir
            / run_index.process_id
            / "documents"
            / "doc_000001"
            / "viewer_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        review_dir = review_batch_dir_from_base_path(review.base_path, paths)
        review_payload = json.loads(
            (review_dir / "review_batch_index.json").read_text(encoding="utf-8")
        )
        viewer_payload = json.loads(
            (review_dir / "viewer_index.json").read_text(encoding="utf-8")
        )

        assert result["selected_invoice_id"] == "invoice-001"
        assert result["removed_invoice_id"] == "invoice-002"
        assert [invoice["invoice_id"] for invoice in manifest["invoices"]] == [
            "invoice-001",
            "invoice-003",
        ]
        assert [invoice["title"] for invoice in manifest["invoices"]] == [
            "Fattura 001",
            "Fattura 002",
        ]
        assert manifest["invoices"][0]["page_numbers"] == [1, 2, 3]
        assert [invoice["invoice_id"] for invoice in review_payload["invoices"]] == [
            "invoice-001",
            "invoice-003",
        ]
        assert viewer_payload["documents"][0]["review_invoice_ids"] == [
            "invoice-001",
            "invoice-003",
        ]
    finally:
        _clean_workspace(workspace)


def test_segmentation_split_creates_invoice_and_cascades_review_overflow() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            invoices = [
                {
                    "invoice_id": f"invoice-{number:03d}",
                    "display_number": number,
                    "title": f"Fattura {number:03d}",
                    "page_numbers": [1, 2] if number == 1 else [number + 1],
                    "fields": [],
                }
                for number in range(1, 16)
            ]
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": "source.pdf",
                        "pages": [
                            {"page": page, "targets": []}
                            for page in range(1, 17)
                        ],
                        "invoices": invoices,
                    }
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=15,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        source_draft = save_review_invoice_draft(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "fields": [
                    {
                        "kind": "numero_fattura",
                        "label": "Numero fattura",
                        "value": "MODIFICATO",
                        "score": 1.0,
                    }
                ],
            },
            paths,
        )
        unaffected_draft = save_review_invoice_draft(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-002",
                "fields": [
                    {
                        "kind": "numero_fattura",
                        "label": "Numero fattura",
                        "value": "DA CONSERVARE",
                        "score": 1.0,
                    }
                ],
            },
            paths,
        )

        result = mutate_review_invoice_segmentation(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "invoice_id": "invoice-001",
                "operation": "split",
                "after_position": 1,
            },
            paths,
        )

        listed_reviews = list_review_batches(paths)
        assert [review.invoice_count for review in listed_reviews] == [15, 1]
        assert load_processing_run_index(
            paths.manifests_dir / run_index.process_id
        ).invoice_count == 16
        assert result["created_invoice_id"].startswith("invoice-manual-")
        assert set(result["affected_invoice_ids"]) == {
            "invoice-001",
            result["created_invoice_id"],
        }
        remaining_drafts = list_review_invoice_drafts(paths)["drafts"]
        assert [draft["draft_id"] for draft in remaining_drafts] == [
            unaffected_draft["draft_id"]
        ]
        assert remaining_drafts[0]["fields"][0]["value"] == "DA CONSERVARE"
        assert delete_review_invoice_drafts(
            [source_draft["draft_id"], unaffected_draft["draft_id"]],
            paths,
        ) == 1
        first_review_dir = review_batch_dir_from_base_path(
            listed_reviews[0].base_path,
            paths,
        )
        second_review_dir = review_batch_dir_from_base_path(
            listed_reviews[1].base_path,
            paths,
        )
        first_payload = json.loads(
            (first_review_dir / "review_batch_index.json").read_text(encoding="utf-8")
        )
        second_payload = json.loads(
            (second_review_dir / "review_batch_index.json").read_text(encoding="utf-8")
        )
        assert first_payload["invoices"][1]["invoice_id"] == result["created_invoice_id"]
        assert first_payload["invoices"][1]["title"] == "Fattura 002"
        assert second_payload["invoices"][0]["invoice_id"] == "invoice-015"
        assert second_payload["invoices"][0]["title"] == "Fattura 016"
    finally:
        _clean_workspace(workspace)


def test_review_mutation_recovery_rolls_forward_prepared_journal() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        target_path = paths.manifests_dir / "process-test" / "state.json"
        target_path.parent.mkdir(parents=True)
        target_path.write_text('{"value": "old"}', encoding="utf-8")
        journal_dir = paths.manifests_dir / "_review_mutations"
        journal_dir.mkdir(parents=True)
        journal_path = journal_dir / "mutation_interrupted.json"
        journal_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "prepared",
                    "updates": [
                        {
                            "relative_path": "process-test/state.json",
                            "payload": {"value": "new"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        assert recover_review_mutations(paths) == 1
        assert json.loads(target_path.read_text(encoding="utf-8")) == {
            "value": "new"
        }
        assert not journal_path.exists()
    finally:
        _clean_workspace(workspace)


def test_reprocess_review_batch_updates_existing_review_in_place() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def first_processor(
            _source_path: Path,
            output_dir: Path,
            first_invoice_number: int,
        ):
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
                                "title": "Fattura vecchia 1",
                                "page_numbers": [1],
                                "fields": [],
                            },
                            {
                                "invoice_id": f"invoice-{first_invoice_number + 1:03d}",
                                "title": "Fattura vecchia 2",
                                "page_numbers": [2],
                                "fields": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=first_processor,
            review_batch_size=15,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        promote_batch_sources(imported_batch, run_index.process_id, paths)
        review_base_path = run_index.review_batches[0].base_path

        def reprocessor(
            _source_path: Path,
            output_dir: Path,
            first_invoice_number: int,
        ):
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
                                "title": "Fattura nuova 1",
                                "page_numbers": [1],
                                "fields": [{"kind": "numero_fattura", "value": "A1"}],
                            },
                            {
                                "invoice_id": f"invoice-{first_invoice_number + 1:03d}",
                                "title": "Fattura nuova 2",
                                "page_numbers": [2],
                                "fields": [{"kind": "numero_fattura", "value": "A2"}],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        result = reprocess_review_batch(
            review_base_path,
            runtime_paths=paths,
            document_processor=reprocessor,
        )

        review_index_path = (
            review_batch_dir_from_base_path(review_base_path, paths)
            / "review_batch_index.json"
        )
        review_index = json.loads(review_index_path.read_text(encoding="utf-8"))
        manifest_path = (
            paths.manifests_dir
            / run_index.process_id
            / "documents"
            / "doc_000001"
            / "viewer_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert result.review_batch_id == "review_batch_0001"
        assert result.review_batch_base_path == review_base_path
        assert result.reprocessed_document_ids == ["doc_000001"]
        assert result.updated_invoice_ids == ["invoice-001", "invoice-002"]
        assert review_index["review_batch_id"] == "review_batch_0001"
        assert [invoice["title"] for invoice in review_index["invoices"]] == [
            "Fattura nuova 1",
            "Fattura nuova 2",
        ]
        assert [invoice["title"] for invoice in manifest["invoices"]] == [
            "Fattura nuova 1",
            "Fattura nuova 2",
        ]
        assert (manifest_path.parent / DOCUMENT_COMPLETE_FILE_NAME).is_file()
    finally:
        _clean_workspace(workspace)


def test_reprocess_review_maps_shifted_invoice_ids_by_document_order() -> None:
    old_invoice_ids = ["invoice-031", "invoice-032"]
    new_invoice_refs = [
        processing_storage.ProcessedInvoiceRef(
            invoice_id="invoice-032",
            title="Fattura 032",
            document_id="doc_000016",
            source_batch_id="batch-1",
            source_file_name="source.pdf",
        ),
        processing_storage.ProcessedInvoiceRef(
            invoice_id="invoice-033",
            title="Fattura 033",
            document_id="doc_000016",
            source_batch_id="batch-1",
            source_file_name="source.pdf",
        ),
    ]

    mapped_refs = [
        processing_storage._mapped_reprocessed_invoice_ref(
            old_invoice_id=old_invoice_id,
            old_invoice_ids=old_invoice_ids,
            new_invoice_refs=new_invoice_refs,
        )
        for old_invoice_id in old_invoice_ids
    ]

    assert [ref.invoice_id for ref in mapped_refs if ref is not None] == [
        "invoice-032",
        "invoice-033",
    ]


def test_reprocess_review_preserves_stable_manifest_invoice_ids() -> None:
    manifest = {
        "invoices": [
            {"invoice_id": "invoice-032", "title": "Fattura 032"},
            {"invoice_id": "invoice-033", "title": "Fattura 033"},
        ]
    }

    processing_storage._preserve_reprocessed_manifest_invoice_ids(
        manifest,
        old_invoice_ids=["invoice-031", "invoice-032"],
    )

    assert [invoice["invoice_id"] for invoice in manifest["invoices"]] == [
        "invoice-031",
        "invoice-032",
    ]


def test_reprocess_review_rebases_draft_without_losing_manual_values() -> None:
    rebased_fields = processing_storage._rebase_review_draft_fields(
        old_fields=[
            {"kind": "nome_emittente", "value": "OCR SRL"},
            {"kind": "economic_summary.totale_fattura.value", "value": ""},
            {"kind": "economic_summary.ritenuta.value", "value": ""},
        ],
        new_fields=[
            {"kind": "nome_emittente", "value": "OCR SRL"},
            {"kind": "economic_summary.totale_fattura.value", "value": "82.50"},
            {"kind": "economic_summary.ritenuta.value", "value": "4.00"},
        ],
        draft_fields=[
            {"kind": "nome_emittente", "value": "NOME CORRETTO SRL"},
            {"kind": "economic_summary.totale_fattura.value", "value": ""},
            {"kind": "economic_summary.ritenuta.value", "value": ""},
        ],
    )

    assert {field["kind"]: field["value"] for field in rebased_fields} == {
        "nome_emittente": "NOME CORRETTO SRL",
        "economic_summary.totale_fattura.value": "82.50",
        "economic_summary.ritenuta.value": "4.00",
    }


def test_reprocess_review_batch_can_target_invoice_document() -> None:
    workspace = _workspace()
    try:
        source_a = workspace / "a.pdf"
        source_b = workspace / "b.pdf"
        source_a.write_bytes(b"%PDF-a")
        source_b.write_bytes(b"%PDF-b")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_a, source_b],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def processor(source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            title_prefix = "A" if source_path.name == "doc_000001.pdf" else "B"
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": source_path.name,
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"{title_prefix} vecchia",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=processor,
            review_batch_size=15,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        promote_batch_sources(imported_batch, run_index.process_id, paths)

        def reprocessor(source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            title_prefix = "A" if source_path.name == "doc_000001.pdf" else "B"
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": source_path.name,
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"{title_prefix} nuova",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        result = reprocess_review_batch(
            run_index.review_batches[0].base_path,
            invoice_id="invoice-002",
            runtime_paths=paths,
            document_processor=reprocessor,
        )

        review_index_path = (
            review_batch_dir_from_base_path(run_index.review_batches[0].base_path, paths)
            / "review_batch_index.json"
        )
        review_index = json.loads(review_index_path.read_text(encoding="utf-8"))
        viewer_index_path = (
            review_batch_dir_from_base_path(run_index.review_batches[0].base_path, paths)
            / "viewer_index.json"
        )
        viewer_index = json.loads(viewer_index_path.read_text(encoding="utf-8"))

        assert result.reprocessed_document_ids == ["doc_000002"]
        assert [invoice["title"] for invoice in review_index["invoices"]] == [
            "A vecchia",
            "B nuova",
        ]
        assert viewer_index["documents"] == [
            {
                "id": "doc_000001",
                "base_path": (
                    f"/runtime/manifests/{run_index.process_id}/documents/doc_000001"
                ),
                "review_invoice_ids": ["invoice-001"],
            },
            {
                "id": "doc_000002",
                "base_path": (
                    f"/runtime/manifests/{run_index.process_id}/documents/doc_000002"
                ),
                "review_invoice_ids": ["invoice-002"],
            },
        ]
    finally:
        _clean_workspace(workspace)


def test_reprocess_review_batch_resolves_saved_invoice_lineage() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF-source")
        paths = get_runtime_paths(workspace / "app-data")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def first_processor(
            _source_path: Path,
            output_dir: Path,
            first_invoice_number: int,
        ):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": "Fattura vecchia",
                                "page_numbers": [1, 2],
                                "fields": [],
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
            document_processor=first_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        promote_batch_sources(imported_batch, run_index.process_id, paths)
        review_base_path = run_index.review_batches[0].base_path
        review_dir = review_batch_dir_from_base_path(review_base_path, paths)
        review_index_path = review_dir / "review_batch_index.json"
        review_index = json.loads(review_index_path.read_text(encoding="utf-8"))
        review_entry = review_index["invoices"][0]
        review_entry["source_batch_id"] = "batch_reprocessed_invoice"
        review_entry["parent_invoice_record_id"] = "saved-parent"
        review_entry["source_page_numbers"] = [7, 8]
        review_index_path.write_text(json.dumps(review_index), encoding="utf-8")

        archived_invoice = paths.archive_invoices_dir / "saved-parent.pdf"
        archived_invoice.write_bytes(b"%PDF-invoice")
        paths.invoice_registry_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [
                        {
                            "record_id": "saved-parent",
                            "status": "saved",
                            "archived_pdf_path": "archive/invoices/saved-parent.pdf",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        def reprocessor(
            source_path: Path,
            output_dir: Path,
            first_invoice_number: int,
        ):
            assert source_path == archived_invoice.resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": "Fattura nuova",
                                "page_numbers": [1, 2],
                                "fields": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        reprocess_review_batch(
            review_base_path,
            invoice_id="invoice-001",
            runtime_paths=paths,
            document_processor=reprocessor,
        )

        updated_index = json.loads(review_index_path.read_text(encoding="utf-8"))
        updated_entry = updated_index["invoices"][0]
        assert updated_entry["parent_invoice_record_id"] == "saved-parent"
        assert updated_entry["source_page_numbers"] == [7, 8]
        assert updated_entry["source_batch_id"] == "batch_reprocessed_invoice"
        with closing(sqlite3.connect(paths.feedback_database_file)) as connection:
            runs = connection.execute(
                "SELECT run_id, status FROM extraction_runs ORDER BY rowid"
            ).fetchall()
            reprocessed_invoice = connection.execute(
                """
                SELECT parent_invoice_record_id, source_page_numbers_json
                FROM invoice_instances ORDER BY rowid DESC LIMIT 1
                """
            ).fetchone()
        assert len(runs) == 2
        assert len({run[0] for run in runs}) == 2
        assert [run[1] for run in runs] == ["completed", "completed"]
        assert reprocessed_invoice[0] == "saved-parent"
        assert json.loads(reprocessed_invoice[1]) == [7, 8]
    finally:
        _clean_workspace(workspace)


def test_review_batch_base_path_by_number_uses_global_review_number() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
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
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        first_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        first_run = process_imported_batch(
            first_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        second_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )
        second_run = process_imported_batch(
            second_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 13, 0, 0),
        )

        assert first_run.review_batches[0].base_path.endswith("/review_batch_0001")
        assert second_run.review_batches[0].base_path.endswith("/review_batch_0002")
        assert review_batch_base_path_by_number(1, paths) == first_run.review_batches[0].base_path
        assert review_batch_base_path_by_number(2, paths) == second_run.review_batches[0].base_path
    finally:
        _clean_workspace(workspace)


def test_list_review_batches_is_global_and_progressive_across_processes() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_files = []
        for index in range(3):
            source_file = workspace / f"source-{index}.pdf"
            source_file.write_bytes(b"%PDF")
            source_files.append(source_file)

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
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
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        old_batch = import_pdf_batch(
            [source_files[0]],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        old_run = process_imported_batch(
            old_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        next_batch = import_pdf_batch(
            source_files[1:],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )
        next_run = process_imported_batch(
            next_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=1,
            now=datetime(2026, 8, 10, 13, 0, 0),
        )

        listed_batches = list_review_batches(paths)

        assert [batch.base_path for batch in listed_batches] == [
            old_run.review_batches[0].base_path,
            next_run.review_batches[0].base_path,
            next_run.review_batches[1].base_path,
        ]
        assert [batch.label for batch in listed_batches] == [
            "Revisione 001",
            "Revisione 002",
            "Revisione 003",
        ]
    finally:
        _clean_workspace(workspace)


def test_list_review_batches_ignores_stale_process_references() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_files = []
        for index in range(2):
            source_file = workspace / f"source-{index}.pdf"
            source_file.write_bytes(b"%PDF")
            source_files.append(source_file)

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
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
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        imported_batch = import_pdf_batch(
            source_files,
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )
        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=1,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )

        stale_batch = run_index.review_batches[0]
        shutil.rmtree(review_batch_dir_from_base_path(stale_batch.base_path, paths))

        listed_batches = list_review_batches(paths)

        assert [batch.id for batch in listed_batches] == [
            run_index.review_batches[1].id
        ]
        assert [batch.label for batch in listed_batches] == ["Revisione 002"]
    finally:
        _clean_workspace(workspace)


def test_missing_review_queue_is_rebuilt_from_completed_process_manifests() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "fatture origine.pdf"
        source_file.write_bytes(b"%PDF")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_file": str(_source_path),
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": f"Fattura {first_invoice_number:03d}",
                                "page_numbers": [1],
                                "fields": [],
                            },
                            {
                                "invoice_id": f"invoice-{first_invoice_number + 1:03d}",
                                "title": f"Fattura {first_invoice_number + 1:03d}",
                                "page_numbers": [2],
                                "fields": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return [manifest_path]

        run_index = process_imported_batch(
            imported_batch.batch_id,
            runtime_paths=paths,
            document_processor=fake_processor,
            review_batch_size=1,
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        promote_batch_sources(imported_batch, run_index.process_id, paths)
        first_batch, second_batch = run_index.review_batches
        paths.invoice_registry_dir.mkdir(parents=True, exist_ok=True)
        paths.invoice_registry_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "records": [
                        {
                            "status": "saved",
                            "review_batch_base_path": first_batch.base_path,
                            "invoice": {
                                "manifestInvoiceId": "invoice-001",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        shutil.rmtree(paths.manifests_dir / "_review_queue")

        assert recover_missing_review_batches(paths) == 2
        assert recover_missing_review_batches(paths) == 0
        listed = list_review_batches(paths)

        assert [batch.id for batch in listed] == [second_batch.id]
        recovered_review = json.loads(
            (
                review_batch_dir_from_base_path(second_batch.base_path, paths)
                / "review_batch_index.json"
            ).read_text(encoding="utf-8")
        )
        assert recovered_review["invoices"][0]["source_file_name"] == source_file.name

        clear_all_review_batches(paths)

        assert recover_missing_review_batches(paths) == 0
        assert list_review_batches(paths) == []
    finally:
        _clean_workspace(workspace)


def test_missing_review_queue_is_not_guessed_when_invoice_counts_disagree() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        imported_batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 10, 0, 0),
        )

        def fake_processor(_source_path: Path, output_dir: Path, first_invoice_number: int):
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "viewer_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "source_file": str(_source_path),
                        "pages": [],
                        "invoices": [
                            {
                                "invoice_id": f"invoice-{first_invoice_number:03d}",
                                "title": "Fattura 001",
                                "page_numbers": [1],
                                "fields": [],
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
            now=datetime(2026, 8, 10, 11, 0, 0),
        )
        promote_batch_sources(imported_batch, run_index.process_id, paths)
        index_path = paths.manifests_dir / run_index.process_id / "processing_run_index.json"
        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        index_payload["review_batches"][0]["invoice_count"] = 2
        index_path.write_text(json.dumps(index_payload), encoding="utf-8")
        shutil.rmtree(paths.manifests_dir / "_review_queue")

        assert recover_missing_review_batches(paths) == 0
        assert not (paths.manifests_dir / "_review_queue").exists()
    finally:
        _clean_workspace(workspace)
