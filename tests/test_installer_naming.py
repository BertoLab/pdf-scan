from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import build_installer


def project(stage: str, revision: int | None = None) -> dict:
    release = {"stage": stage}
    if revision is not None:
        release["revision"] = revision
    return {
        "project": {"version": "0.1.0"},
        "tool": {"estrattore": {"desktop_release": release}},
    }


def test_candidate_and_stable_installer_names() -> None:
    label = build_installer.release_label(project("rc", 2))
    assert build_installer.artifact_basename(label) == "EstrattoreScanFatture-Setup-0.1.0-rc.2"
    assert build_installer.artifact_basename(label, smoke=True, smoke_suffix="Third") == (
        "EstrattoreScanFatture-Setup-0.1.0-rc.2-Smoke-Third"
    )
    assert build_installer.artifact_basename(
        build_installer.release_label(project("stable"))
    ) == "EstrattoreScanFatture-Setup-0.1.0"


@pytest.mark.parametrize("stage,revision", [("rc", None), ("rc", 0), ("rc", True), ("stable", 2)])
def test_invalid_desktop_release_is_rejected(stage: str, revision: int | None) -> None:
    with pytest.raises(ValueError):
        build_installer.release_label(project(stage, revision))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer build")
def test_existing_candidate_is_rejected_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "EstrattoreScanFatture-Setup-0.1.0-rc.2.exe"
    candidate.write_bytes(b"already published candidate")
    monkeypatch.setattr(build_installer, "DIST", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_installer.py"])
    with pytest.raises(SystemExit, match="Increment the desktop release revision"):
        build_installer.main()
    assert candidate.read_bytes() == b"already published candidate"
