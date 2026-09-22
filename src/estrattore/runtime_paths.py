from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "EstrattoreScanFatture"
DATA_DIR_ENV_VAR = "ESTRATTORE_SCAN_DATA_DIR"


@dataclass(frozen=True)
class RuntimePaths:
    app_data_dir: Path
    registry_input_dir: Path
    registry_cache_dir: Path
    registry_uploads_dir: Path
    uploads_dir: Path
    batches_dir: Path
    manifests_dir: Path
    invoice_registry_dir: Path
    archive_dir: Path
    archive_invoices_dir: Path
    archive_sources_dir: Path
    logs_dir: Path
    config_dir: Path
    feedback_dir: Path
    feedback_evidence_dir: Path

    @property
    def registry_input_file(self) -> Path:
        return self.registry_input_dir / "elenco_anagrafiche.xlsx"

    @property
    def registry_cache_file(self) -> Path:
        return self.registry_cache_dir / "anagrafica_registry.json"

    @property
    def registry_duplicate_report_json_file(self) -> Path:
        return self.registry_cache_dir / "anagrafica_registry_duplicates.json"

    @property
    def registry_duplicate_report_xlsx_file(self) -> Path:
        return self.registry_cache_dir / "anagrafica_registry_duplicates.xlsx"

    @property
    def registry_export_xlsx_file(self) -> Path:
        return self.registry_cache_dir / "anagrafica_estrattore_scan.xlsx"

    @property
    def invoice_registry_file(self) -> Path:
        return self.invoice_registry_dir / "invoices_registry.json"

    @property
    def invoice_registry_export_xlsx_file(self) -> Path:
        return self.invoice_registry_dir / "registro_fatture.xlsx"

    @property
    def source_archive_index_file(self) -> Path:
        return self.archive_sources_dir / "source_archive_index.json"

    @property
    def feedback_database_file(self) -> Path:
        return self.feedback_dir / "feedback.sqlite3"

    def ensure_directories(self) -> None:
        for directory in (
            self.app_data_dir,
            self.registry_input_dir,
            self.registry_cache_dir,
            self.registry_uploads_dir,
            self.uploads_dir,
            self.batches_dir,
            self.manifests_dir,
            self.invoice_registry_dir,
            self.archive_dir,
            self.archive_invoices_dir,
            self.archive_sources_dir,
            self.logs_dir,
            self.config_dir,
            self.feedback_dir,
            self.feedback_evidence_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, str]:
        return {
            "app_data_dir": str(self.app_data_dir),
            "registry_input_dir": str(self.registry_input_dir),
            "registry_cache_dir": str(self.registry_cache_dir),
            "registry_uploads_dir": str(self.registry_uploads_dir),
            "uploads_dir": str(self.uploads_dir),
            "batches_dir": str(self.batches_dir),
            "manifests_dir": str(self.manifests_dir),
            "invoice_registry_dir": str(self.invoice_registry_dir),
            "archive_dir": str(self.archive_dir),
            "archive_invoices_dir": str(self.archive_invoices_dir),
            "archive_sources_dir": str(self.archive_sources_dir),
            "logs_dir": str(self.logs_dir),
            "config_dir": str(self.config_dir),
            "feedback_dir": str(self.feedback_dir),
            "feedback_evidence_dir": str(self.feedback_evidence_dir),
        }


def get_runtime_paths(app_data_dir: Path | None = None) -> RuntimePaths:
    base_dir = _resolve_app_data_dir(app_data_dir)
    registry_dir = base_dir / "registry"
    return RuntimePaths(
        app_data_dir=base_dir,
        registry_input_dir=registry_dir / "input",
        registry_cache_dir=registry_dir / "cache",
        registry_uploads_dir=registry_dir / "uploads",
        uploads_dir=base_dir / "uploads",
        batches_dir=base_dir / "batches",
        manifests_dir=base_dir / "manifests",
        invoice_registry_dir=base_dir / "invoice_registry",
        archive_dir=base_dir / "archive",
        archive_invoices_dir=base_dir / "archive" / "invoices",
        archive_sources_dir=base_dir / "archive" / "sources",
        logs_dir=base_dir / "logs",
        config_dir=base_dir / "config",
        feedback_dir=base_dir / "feedback",
        feedback_evidence_dir=base_dir / "feedback" / "evidence",
    )


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parents[2]


def legacy_project_data_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "data"


def migrate_legacy_registry_files_if_needed(
    runtime_paths: RuntimePaths | None = None,
    legacy_data_dir: Path | None = None,
) -> list[tuple[Path, Path]]:
    paths = runtime_paths or get_runtime_paths()
    legacy_registry_dir = (legacy_data_dir or legacy_project_data_dir()) / "registry"
    migrations = [
        (
            legacy_registry_dir / "input" / "elenco_anagrafiche.xlsx",
            paths.registry_input_file,
        ),
        (
            legacy_registry_dir / "cache" / "anagrafica_registry.json",
            paths.registry_cache_file,
        ),
        (
            legacy_registry_dir / "cache" / "anagrafica_registry_duplicates.json",
            paths.registry_duplicate_report_json_file,
        ),
        (
            legacy_registry_dir / "cache" / "anagrafica_registry_duplicates.xlsx",
            paths.registry_duplicate_report_xlsx_file,
        ),
        (
            legacy_registry_dir / "cache" / "anagrafica_estrattore_scan.xlsx",
            paths.registry_export_xlsx_file,
        ),
    ]

    copied_files: list[tuple[Path, Path]] = []
    for source_path, target_path in migrations:
        if not source_path.exists() or target_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied_files.append((source_path, target_path))

    return copied_files


def _resolve_app_data_dir(app_data_dir: Path | None) -> Path:
    if app_data_dir is not None:
        return app_data_dir.expanduser().resolve()

    env_value = os.environ.get(DATA_DIR_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / APP_NAME).resolve()

    if os.name == "nt":
        return (Path.home() / "AppData" / "Local" / APP_NAME).resolve()

    return (Path.home() / f".{APP_NAME}").resolve()
