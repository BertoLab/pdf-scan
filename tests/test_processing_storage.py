import json
from datetime import datetime
from pathlib import Path
import shutil
import uuid

from estrattore.batch_storage import import_pdf_batch
from estrattore.processing_storage import (
    DOCUMENT_COMPLETE_FILE_NAME,
    PROCESSING_COMPLETE_FILE_NAME,
    ProcessingCancelled,
    ProcessingPaused,
    cleanup_incomplete_processing_runs,
    load_processing_run_index,
    list_review_batches,
    mark_review_invoice,
    move_review_invoice_page,
    process_imported_batch,
)
from estrattore.runtime_paths import get_runtime_paths


def _workspace() -> Path:
    root = Path("processing-storage-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _clean_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


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
            paths.manifests_dir
            / run_index.process_id
            / "review_batches"
            / "review_batch_0001"
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
            batch.id for batch in reversed(run_index.review_batches)
        ]
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


def test_process_imported_batch_can_be_paused_between_documents_without_deleting_run() -> None:
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
        process_id = "process_pause_test"
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
                should_pause=lambda: processed_count >= 1,
                process_id=process_id,
                now=datetime(2026, 8, 10, 11, 0, 0),
            )
        except ProcessingPaused:
            pass
        else:
            raise AssertionError("Expected processing pause")

        process_dir = paths.manifests_dir / process_id
        assert process_dir.is_dir()
        assert (
            process_dir / "documents" / "doc_000001" / DOCUMENT_COMPLETE_FILE_NAME
        ).is_file()
        assert not (process_dir / PROCESSING_COMPLETE_FILE_NAME).exists()
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

        mark_review_invoice(
            run_index.review_batches[0].base_path,
            "invoice-001",
            "saved",
            paths,
        )

        assert document_working_dir.is_dir()
        assert source_working_pdf.is_file()

        mark_review_invoice(
            run_index.review_batches[1].base_path,
            "invoice-002",
            "discarded",
            paths,
        )

        assert not document_working_dir.exists()
        assert not source_working_pdf.exists()
    finally:
        _clean_workspace(workspace)


def test_move_review_invoice_page_updates_document_manifest() -> None:
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

        result = move_review_invoice_page(
            {
                "review_batch_base_path": run_index.review_batches[0].base_path,
                "document_id": "doc_000001",
                "from_invoice_id": "invoice-001",
                "to_invoice_id": "invoice-002",
                "page_number": 2,
            },
            runtime_paths=paths,
        )

        manifest_path = (
            paths.manifests_dir
            / run_index.process_id
            / "documents"
            / "doc_000001"
            / "viewer_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert result["from_page_numbers"] == [1]
        assert result["to_page_numbers"] == [2, 3]
        assert [invoice["page_numbers"] for invoice in manifest["invoices"]] == [
            [1],
            [2, 3],
        ]
    finally:
        _clean_workspace(workspace)


def test_move_review_invoice_page_rejects_empty_source_invoice() -> None:
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

        try:
            move_review_invoice_page(
                {
                    "review_batch_base_path": run_index.review_batches[0].base_path,
                    "document_id": "doc_000001",
                    "from_invoice_id": "invoice-001",
                    "to_invoice_id": "invoice-002",
                    "page_number": 1,
                },
                runtime_paths=paths,
            )
        except ValueError as error:
            assert "senza pagine" in str(error)
        else:
            raise AssertionError("Lo spostamento doveva essere rifiutato")
    finally:
        _clean_workspace(workspace)
