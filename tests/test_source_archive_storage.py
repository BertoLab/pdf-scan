from datetime import datetime
from pathlib import Path
import shutil
import uuid

from estrattore.batch_storage import import_pdf_batch
from estrattore.runtime_paths import get_runtime_paths
from estrattore.source_archive_storage import (
    archived_source_path,
    delete_archived_sources,
    list_archived_sources,
    promote_batch_sources,
    resolve_archived_source_for_origin,
)


def _workspace() -> Path:
    root = Path("source-archive-storage-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def test_promote_batch_sources_is_idempotent_and_preserves_one_copy() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "origine.pdf"
        source.write_bytes(b"%PDF-source")
        batch = import_pdf_batch(
            [source], paths, now=datetime(2026, 8, 21, 10, 0, 0)
        )

        first = promote_batch_sources(batch, "process_001", paths)
        second = promote_batch_sources(batch, "process_001", paths)

        assert first == second
        assert len(list_archived_sources(paths)) == 1
        archived, archived_path = resolve_archived_source_for_origin(
            batch.batch_id, batch.documents[0].document_id, paths
        )
        assert archived.source_id == first[0].source_id
        assert archived_path.read_bytes() == b"%PDF-source"
        assert len(list(paths.archive_sources_dir.glob("*.pdf"))) == 1
        assert not (
            paths.batches_dir / batch.batch_id / batch.documents[0].relative_path
        ).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_reprocess_binding_reuses_archived_source_without_second_copy() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "origine.pdf"
        source.write_bytes(b"%PDF-source")
        first_batch = import_pdf_batch([source], paths)
        first_source = promote_batch_sources(first_batch, "process_001", paths)[0]
        _, archived_path = archived_source_path(first_source.source_id, paths)

        reprocess_batch = import_pdf_batch([archived_path], paths)
        promoted = promote_batch_sources(
            reprocess_batch,
            "process_002",
            paths,
            {reprocess_batch.documents[0].document_id: first_source.source_id},
        )

        assert promoted[0].source_id == first_source.source_id
        assert len(list_archived_sources(paths)) == 1
        assert len(list(paths.archive_sources_dir.glob("*.pdf"))) == 1
        resolved, _ = resolve_archived_source_for_origin(
            reprocess_batch.batch_id, reprocess_batch.documents[0].document_id, paths
        )
        assert resolved.source_id == first_source.source_id
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_missing_reprocess_binding_promotes_temporary_copy_as_new_source() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        source = workspace / "origine.pdf"
        source.write_bytes(b"%PDF-source")
        first_batch = import_pdf_batch([source], paths)
        first_source = promote_batch_sources(first_batch, "process_001", paths)[0]
        _, archived_path = archived_source_path(first_source.source_id, paths)
        reprocess_batch = import_pdf_batch([archived_path], paths)

        delete_archived_sources([first_source.source_id], paths)
        promoted = promote_batch_sources(
            reprocess_batch,
            "process_002",
            paths,
            {reprocess_batch.documents[0].document_id: first_source.source_id},
        )

        assert promoted[0].source_id != first_source.source_id
        assert len(list_archived_sources(paths)) == 1
        assert len(list(paths.archive_sources_dir.glob("*.pdf"))) == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
