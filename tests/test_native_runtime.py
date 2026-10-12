from __future__ import annotations

import os
from pathlib import Path

import pytest

from estrattore import io, runtime_paths, tesseract_adapter


def test_frozen_assets_root_is_pyinstaller_bundle_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert runtime_paths.bundled_assets_root() == tmp_path.resolve()


def test_frozen_poppler_uses_only_private_binaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(io.sys, "frozen", True, raising=False)
    monkeypatch.setattr(io, "bundled_assets_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="manca Poppler"):
        io.resolve_poppler_path()

    bin_dir = tmp_path / "vendor" / "poppler" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("pdfinfo.exe", "pdftoppm.exe"):
        (bin_dir / name).touch()
    assert io.resolve_poppler_path() == bin_dir


def test_source_poppler_may_use_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(io.sys, "frozen", False, raising=False)
    monkeypatch.setattr(io, "bundled_assets_root", lambda: tmp_path)
    assert io.resolve_poppler_path() is None


def test_frozen_tesseract_never_falls_back_to_system(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tesseract_adapter.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tesseract_adapter, "bundled_assets_root", lambda: tmp_path)
    monkeypatch.setattr(tesseract_adapter.shutil, "which", lambda _: "C:/Tools/tesseract.exe")
    with pytest.raises(RuntimeError, match="manca Tesseract"):
        tesseract_adapter.resolve_tesseract_cmd()

    command = tmp_path / "vendor" / "tesseract" / "tesseract.exe"
    command.parent.mkdir(parents=True)
    command.touch()
    assert tesseract_adapter.resolve_tesseract_cmd() == command


@pytest.mark.skipif(os.name != "nt", reason="Windows pytesseract argument parsing")
def test_tessdata_path_with_spaces_uses_environment_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    command = tmp_path / "Program Files" / "vendor" / "tesseract" / "tesseract.exe"
    (command.parent / "tessdata").mkdir(parents=True)
    (command.parent / "tessdata_best").mkdir()
    command.touch()
    monkeypatch.setattr(tesseract_adapter, "resolve_tesseract_cmd", lambda: command)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    assert tesseract_adapter._tessdata_config("fast") == ""
    assert Path(os.environ["TESSDATA_PREFIX"]) == command.parent / "tessdata"
    assert tesseract_adapter._tessdata_config("best") == ""
    assert Path(os.environ["TESSDATA_PREFIX"]) == command.parent / "tessdata_best"
