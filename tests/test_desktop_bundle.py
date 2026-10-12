from __future__ import annotations

import importlib
import hashlib
from pathlib import Path

import pytest


@pytest.fixture
def build_module(monkeypatch: pytest.MonkeyPatch):
    packaging_dir = Path(__file__).resolve().parents[1] / "packaging"
    monkeypatch.syspath_prepend(str(packaging_dir))
    return importlib.import_module("build_desktop")


def _write_required_assets(root: Path) -> None:
    for relative in (
        "apps/viewer/dist/index.html",
        "vendor/poppler/bin/pdfinfo.exe",
        "vendor/tesseract/tesseract.exe",
        "vendor/webview2/msedgewebview2.exe",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_build_requires_all_private_runtime_inputs(
    build_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(build_module, "BUILD_DIR", tmp_path)
    assets = tmp_path / "desktop-assets"
    _write_required_assets(assets)
    build_module._validate_inputs()

    (assets / "vendor" / "webview2" / "msedgewebview2.exe").unlink()
    with pytest.raises(RuntimeError, match="webview2"):
        build_module._validate_inputs()


def test_bundle_rejects_sample_invoices(build_module, tmp_path: Path) -> None:
    app = tmp_path / "EstrattoreScanFatture"
    _write_required_assets(app / "_internal")
    (app / "EstrattoreScanFatture.exe").touch()
    build_module._validate_bundle(app)

    (app / "_internal" / "apps" / "viewer" / "dist" / "sample").mkdir()
    with pytest.raises(RuntimeError, match="sample"):
        build_module._validate_bundle(app)


def test_cached_webview_cab_must_match_pinned_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packaging_dir = Path(__file__).resolve().parents[1] / "packaging"
    monkeypatch.syspath_prepend(str(packaging_dir))
    stage = importlib.import_module("prepare_webview")
    archive = tmp_path / "runtime.cab"
    archive.write_bytes(b"verified CAB bytes")
    monkeypatch.setattr(stage, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(stage, "ARCHIVE_NAME", archive.name)
    monkeypatch.setattr(stage, "ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(stage, "ARCHIVE_SIZE", archive.stat().st_size)
    monkeypatch.setattr(stage, "_verify_microsoft_signature", lambda _path: None)
    assert stage._verified_archive() == archive

    archive.write_bytes(b"other CAB bytes")
    with pytest.raises(RuntimeError, match="size mismatch"):
        stage._verified_archive()

    archive.write_bytes(b"x" * stage.ARCHIVE_SIZE)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        stage._verified_archive()
