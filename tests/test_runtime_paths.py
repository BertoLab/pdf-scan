from pathlib import Path
import shutil
import uuid

from estrattore.runtime_paths import (
    DATA_DIR_ENV_VAR,
    get_runtime_paths,
    migrate_legacy_registry_files_if_needed,
)


def _test_workspace() -> Path:
    root = Path("runtime-paths-test-manual") / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def _clean_test_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_runtime_paths_can_use_explicit_app_data_dir() -> None:
    workspace = _test_workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")

        assert paths.app_data_dir == (workspace / "app-data").resolve()
        assert paths.registry_input_file == (
            workspace
            / "app-data"
            / "registry"
            / "input"
            / "elenco_anagrafiche.xlsx"
        ).resolve()
        assert paths.registry_cache_file == (
            workspace
            / "app-data"
            / "registry"
            / "cache"
            / "anagrafica_registry.json"
        ).resolve()
        assert paths.invoice_registry_file == (
            workspace
            / "app-data"
            / "invoice_registry"
            / "invoices_registry.json"
        ).resolve()
        assert paths.archive_invoices_dir == (
            workspace / "app-data" / "archive" / "invoices"
        ).resolve()
        assert paths.archive_sources_dir == (
            workspace / "app-data" / "archive" / "sources"
        ).resolve()
        assert paths.logs_dir == (workspace / "app-data" / "logs").resolve()
    finally:
        _clean_test_workspace(workspace)


def test_runtime_paths_can_use_environment_override(
    monkeypatch,
) -> None:
    workspace = _test_workspace()
    try:
        monkeypatch.setenv(DATA_DIR_ENV_VAR, str(workspace / "override-data"))

        paths = get_runtime_paths()

        assert paths.app_data_dir == (workspace / "override-data").resolve()
    finally:
        _clean_test_workspace(workspace)


def test_runtime_paths_ensure_directories() -> None:
    workspace = _test_workspace()
    try:
        paths = get_runtime_paths(workspace / "app-data")

        paths.ensure_directories()

        for directory in paths.as_dict().values():
            assert Path(directory).is_dir()
    finally:
        _clean_test_workspace(workspace)


def test_legacy_registry_migration_copies_only_missing_files(
) -> None:
    workspace = _test_workspace()
    try:
        legacy_data_dir = workspace / "legacy-data"
        legacy_input = legacy_data_dir / "registry" / "input" / "elenco_anagrafiche.xlsx"
        legacy_cache = legacy_data_dir / "registry" / "cache" / "anagrafica_registry.json"
        legacy_input.parent.mkdir(parents=True)
        legacy_cache.parent.mkdir(parents=True)
        legacy_input.write_bytes(b"legacy input")
        legacy_cache.write_text('{"records": []}', encoding="utf-8")

        paths = get_runtime_paths(workspace / "runtime-data")
        paths.registry_input_file.parent.mkdir(parents=True)
        paths.registry_input_file.write_bytes(b"existing input")

        copied_files = migrate_legacy_registry_files_if_needed(
            runtime_paths=paths,
            legacy_data_dir=legacy_data_dir,
        )

        assert paths.registry_input_file.read_bytes() == b"existing input"
        assert paths.registry_cache_file.read_text(encoding="utf-8") == '{"records": []}'
        assert copied_files == [(legacy_cache, paths.registry_cache_file)]
    finally:
        _clean_test_workspace(workspace)
