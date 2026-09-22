from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from estrattore.feedback_collection import export_feedback_bundle
from estrattore.runtime_paths import RuntimePaths


EXPORT_FOLDER_PREFIX = "EstrattoreScanFatture_DatiMiglioramento"


@dataclass(frozen=True)
class DesktopFeedbackExport:
    output_path: Path
    stats: dict[str, int]
    missing_pdf_count: int

    @property
    def folder_name(self) -> str:
        return self.output_path.name


def export_feedback_to_desktop(
    *,
    runtime_paths: RuntimePaths | None = None,
    desktop_dir: Path | None = None,
    now: datetime | None = None,
) -> DesktopFeedbackExport:
    destination_root = desktop_dir or resolve_user_desktop()
    destination_root.mkdir(parents=True, exist_ok=True)
    output_path = _unique_export_path(destination_root, now or datetime.now())
    export_feedback_bundle(
        output_path,
        runtime_paths=runtime_paths,
        include_pdfs=True,
    )
    manifest = _read_bundle_manifest(output_path)
    return DesktopFeedbackExport(
        output_path=output_path,
        stats={
            str(key): int(value)
            for key, value in (manifest.get("stats") or {}).items()
            if isinstance(value, int)
        },
        missing_pdf_count=len(manifest.get("missing_pdf_asset_ids") or []),
    )


def resolve_user_desktop() -> Path:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                raw_path, _ = winreg.QueryValueEx(key, "Desktop")
            resolved = Path(os.path.expandvars(str(raw_path))).expanduser()
            if resolved.is_absolute():
                return resolved
        except (OSError, ValueError):
            pass

    return Path.home() / "Desktop"


def _unique_export_path(desktop_dir: Path, now: datetime) -> Path:
    base_name = f"{EXPORT_FOLDER_PREFIX}_{now:%Y-%m-%d_%H%M}"
    candidate = desktop_dir / base_name
    duplicate_number = 2
    while candidate.exists():
        candidate = desktop_dir / f"{base_name} ({duplicate_number})"
        duplicate_number += 1
    return candidate


def _read_bundle_manifest(output_path: Path) -> dict[str, Any]:
    payload = json.loads(
        (output_path / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict):
        raise ValueError("Manifest export non valido")
    return payload
