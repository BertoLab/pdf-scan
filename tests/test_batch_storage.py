from datetime import datetime, timedelta
from pathlib import Path
import shutil
import uuid

import pytest

from estrattore.batch_storage import (
    PdfBatchSource,
    delete_imported_batch,
    import_pdf_batch,
    import_pdf_folder,
    list_imported_batches,
)
from estrattore.runtime_paths import get_runtime_paths


def _workspace() -> Path:
    root = Path("batch-storage-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _clean_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_import_pdf_batch_copies_files_and_writes_index() -> None:
    workspace = _workspace()
    try:
        source_dir = workspace / "source"
        source_dir.mkdir()
        (source_dir / "fattura a.pdf").write_bytes(b"%PDF-a")
        (source_dir / "fattura b.pdf").write_bytes(b"%PDF-b")
        paths = get_runtime_paths(workspace / "app-data")

        batch = import_pdf_batch(
            [source_dir / "fattura a.pdf", source_dir / "fattura b.pdf"],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 15, 30, 0),
        )

        assert batch.batch_id == "batch_20260810_153000"
        assert batch.source_label == "2 PDF selezionati"
        assert batch.document_count == 2
        assert [document.original_file_name for document in batch.documents] == [
            "fattura a.pdf",
            "fattura b.pdf",
        ]
        assert (paths.batches_dir / batch.batch_id / "batch_index.json").is_file()
        assert (
            paths.batches_dir
            / batch.batch_id
            / "input"
            / batch.documents[0].stored_file_name
        ).read_bytes() == b"%PDF-a"
    finally:
        _clean_workspace(workspace)


def test_import_pdf_batch_rejects_non_pdf_files() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.txt"
        source_file.write_text("not pdf", encoding="utf-8")
        paths = get_runtime_paths(workspace / "app-data")

        with pytest.raises(ValueError, match="solo file PDF"):
            import_pdf_batch([source_file], runtime_paths=paths)

        assert not any(paths.batches_dir.glob("batch_*"))
    finally:
        _clean_workspace(workspace)


def test_import_pdf_folder_flattens_nested_pdfs_and_ignores_non_pdf() -> None:
    workspace = _workspace()
    try:
        folder = workspace / "selected"
        nested_folder = folder / "nested"
        nested_folder.mkdir(parents=True)
        first_pdf = folder / "a.pdf"
        second_pdf = nested_folder / "b.pdf"
        first_pdf.write_bytes(b"%PDF-a")
        second_pdf.write_bytes(b"%PDF-b")
        (nested_folder / "notes.txt").write_text("ignore", encoding="utf-8")
        paths = get_runtime_paths(workspace / "app-data")

        batch = import_pdf_folder(folder, runtime_paths=paths)

        assert batch.document_count == 2
        assert batch.source_label == "selected"
        assert batch.label.startswith("selected - 2 PDF")
        assert [document.original_file_name for document in batch.documents] == [
            "a.pdf",
            "nested/b.pdf",
        ]
        assert (
            paths.batches_dir / batch.batch_id / "input" / "doc_000001.pdf"
        ).read_bytes() == b"%PDF-a"
        assert (
            paths.batches_dir / batch.batch_id / "input" / "doc_000002.pdf"
        ).read_bytes() == b"%PDF-b"
    finally:
        _clean_workspace(workspace)


def test_import_pdf_batch_preserves_original_file_name_from_upload_source() -> None:
    workspace = _workspace()
    try:
        temporary_upload = workspace / "upload-random-name.tmp"
        temporary_upload.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")

        batch = import_pdf_batch(
            [
                PdfBatchSource(
                    path=temporary_upload,
                    original_file_name="fattura originale.pdf",
                )
            ],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 16, 0, 0),
        )

        assert batch.documents[0].original_file_name == "fattura originale.pdf"
        assert batch.documents[0].stored_file_name == "doc_000001.pdf"
    finally:
        _clean_workspace(workspace)


def test_list_imported_batches_returns_newest_first() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        older = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )
        newer = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0) + timedelta(seconds=1),
        )

        batches = list_imported_batches(paths)

        assert [batch.batch_id for batch in batches] == [newer.batch_id, older.batch_id]
    finally:
        _clean_workspace(workspace)


def test_delete_imported_batch_removes_internal_copy() -> None:
    workspace = _workspace()
    try:
        source_file = workspace / "source.pdf"
        source_file.write_bytes(b"%PDF")
        paths = get_runtime_paths(workspace / "app-data")
        batch = import_pdf_batch(
            [source_file],
            runtime_paths=paths,
            now=datetime(2026, 8, 10, 12, 0, 0),
        )

        delete_imported_batch(batch.batch_id, runtime_paths=paths)

        assert not (paths.batches_dir / batch.batch_id).exists()
        assert list_imported_batches(paths) == []
    finally:
        _clean_workspace(workspace)


def test_delete_imported_batch_rejects_unsafe_id() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")

        with pytest.raises(ValueError, match="non valido"):
            delete_imported_batch("../batch_20260810_120000", runtime_paths=paths)
    finally:
        _clean_workspace(workspace)
