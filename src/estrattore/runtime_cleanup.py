from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from estrattore.runtime_paths import RuntimePaths, get_runtime_paths


STALE_ATOMIC_WRITE_GRACE_PERIOD = timedelta(hours=24)
_ATOMIC_WRITE_FILE_PATTERN = re.compile(r"^\..+\.writing$")


def cleanup_stale_atomic_write_files(
    runtime_paths: RuntimePaths | None = None,
    *,
    now: datetime | None = None,
    grace_period: timedelta = STALE_ATOMIC_WRITE_GRACE_PERIOD,
) -> list[Path]:
    """Remove abandoned atomic-write files owned by the application.

    Atomic writes publish data by replacing ``.<target>.writing`` with the
    target path. A file that still has this temporary name is not committed
    state. The grace period protects recent writes if another app instance is
    active or startup follows immediately after an interruption.
    """
    paths = runtime_paths or get_runtime_paths()
    runtime_root = paths.app_data_dir.resolve()
    if not runtime_root.is_dir():
        return []
    if grace_period < timedelta(0):
        raise ValueError("Il periodo di sicurezza non puo essere negativo")

    cutoff_timestamp = (
        (now or datetime.now()).timestamp() - grace_period.total_seconds()
    )
    removed_paths: list[Path] = []
    for candidate in _atomic_write_candidates(paths):
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved_candidate = candidate.resolve()
            if not resolved_candidate.is_relative_to(runtime_root):
                continue
            if candidate.stat().st_mtime > cutoff_timestamp:
                continue
            candidate.unlink()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        removed_paths.append(candidate)

    return removed_paths


def _atomic_write_candidates(runtime_paths: RuntimePaths) -> list[Path]:
    patterns = (
        (runtime_paths.manifests_dir, ".*.writing"),
        (runtime_paths.manifests_dir, "process_*/.*.writing"),
        (runtime_paths.manifests_dir, "process_*/documents/*/.*.writing"),
        (runtime_paths.manifests_dir, "process_*/review_batches/*/.*.writing"),
        (runtime_paths.manifests_dir, "_review_queue/*/.*.writing"),
        (runtime_paths.manifests_dir, "_review_mutations/.*.writing"),
        (runtime_paths.batches_dir, "*/.*.writing"),
        (runtime_paths.invoice_registry_dir, ".*.writing"),
        (runtime_paths.archive_sources_dir, ".*.writing"),
        (runtime_paths.config_dir, ".*.writing"),
    )
    candidates = {
        candidate
        for root, pattern in patterns
        if root.is_dir()
        for candidate in root.glob(pattern)
        if _ATOMIC_WRITE_FILE_PATTERN.fullmatch(candidate.name) is not None
    }
    return sorted(candidates, key=lambda path: path.as_posix())
