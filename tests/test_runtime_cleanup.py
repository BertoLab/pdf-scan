from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil
import uuid

import pytest

from estrattore.runtime_cleanup import cleanup_stale_atomic_write_files
from estrattore.runtime_paths import get_runtime_paths


def _workspace() -> Path:
    root = Path("runtime-cleanup-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def test_cleanup_removes_only_stale_app_owned_atomic_write_files() -> None:
    workspace = _workspace()
    try:
        now = datetime(2026, 9, 2, 12, 0, 0)
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()
        stale = paths.manifests_dir / "_review_mutations" / ".mutation.json.writing"
        recent = paths.invoice_registry_dir / ".invoices_registry.json.writing"
        similar_name = paths.manifests_dir / "mutation.json.writing"
        external = workspace / ".external.json.writing"
        stale.parent.mkdir(parents=True)
        for path in (stale, recent, similar_name, external):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("temporary", encoding="utf-8")

        stale_timestamp = (now - timedelta(days=2)).timestamp()
        recent_timestamp = (now - timedelta(minutes=5)).timestamp()
        os.utime(stale, (stale_timestamp, stale_timestamp))
        os.utime(recent, (recent_timestamp, recent_timestamp))
        os.utime(similar_name, (stale_timestamp, stale_timestamp))
        os.utime(external, (stale_timestamp, stale_timestamp))

        removed = cleanup_stale_atomic_write_files(paths, now=now)

        assert removed == [stale]
        assert not stale.exists()
        assert recent.is_file()
        assert similar_name.is_file()
        assert external.is_file()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_cleanup_rejects_negative_grace_period() -> None:
    workspace = _workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")
        paths.ensure_directories()

        with pytest.raises(ValueError, match="periodo di sicurezza"):
            cleanup_stale_atomic_write_files(
                paths,
                grace_period=timedelta(seconds=-1),
            )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
