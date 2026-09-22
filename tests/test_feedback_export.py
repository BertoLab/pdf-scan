from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from estrattore.feedback_export import export_feedback_to_desktop
from estrattore.runtime_paths import get_runtime_paths


def test_feedback_export_uses_readable_unique_desktop_folders() -> None:
    workspace = Path("feedback-export-test-manual") / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    try:
        paths = get_runtime_paths(workspace / "app-data")
        desktop = workspace / "Desktop"
        now = datetime(2026, 9, 18, 14, 30)

        first = export_feedback_to_desktop(
            runtime_paths=paths,
            desktop_dir=desktop,
            now=now,
        )
        second = export_feedback_to_desktop(
            runtime_paths=paths,
            desktop_dir=desktop,
            now=now,
        )

        assert first.folder_name == (
            "EstrattoreScanFatture_DatiMiglioramento_2026-09-18_1430"
        )
        assert second.folder_name == (
            "EstrattoreScanFatture_DatiMiglioramento_2026-09-18_1430 (2)"
        )
        assert first.output_path.is_dir()
        assert (first.output_path / "feedback.sqlite3").is_file()
        manifest = json.loads(
            (first.output_path / "bundle_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["includes_pdfs"] is True
        assert first.missing_pdf_count == 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
