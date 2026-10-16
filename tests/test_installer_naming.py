from __future__ import annotations

import sys
import tomllib
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
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    candidate = tmp_path / f"{build_installer.artifact_basename(build_installer.release_label(config))}.exe"
    candidate.write_bytes(b"already published candidate")
    monkeypatch.setattr(build_installer, "DIST", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_installer.py"])
    with pytest.raises(SystemExit, match="Increment the desktop release revision"):
        build_installer.main()
    assert candidate.read_bytes() == b"already published candidate"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer build")
def test_installer_build_reports_progress_without_buffering_child_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    artifact = tmp_path / f"{build_installer.artifact_basename(build_installer.release_label(config))}.exe"
    compiler = tmp_path / "ISCC.exe"
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == str(compiler):
            artifact.write_bytes(b"installer fixture")

    monkeypatch.setattr(build_installer, "DIST", tmp_path)
    monkeypatch.setattr(build_installer, "WORK", tmp_path / "work")
    monkeypatch.setattr(build_installer, "_validate_bundle", lambda _path: None)
    monkeypatch.setattr(build_installer, "bundle_digest", lambda _path: "a" * 64)
    monkeypatch.setattr(build_installer, "prepare_compiler", lambda: compiler)
    monkeypatch.setattr(build_installer.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["build_installer.py"])

    build_installer.main()

    output = capsys.readouterr().out
    assert all(f"[{step}/4]" in output for step in range(1, 5))
    assert "Completato. Installer:" in output
    assert len(calls) == 2
    assert all(kwargs["check"] is True and "capture_output" not in kwargs for _, kwargs in calls)
    assert artifact.read_bytes() == b"installer fixture"
