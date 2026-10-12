from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

from prepare_native import main as prepare_native
from prepare_webview import main as prepare_webview


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build"
VENV_DIR = BUILD_DIR / "packaging-venv"
FRONTEND_WORK_DIR = BUILD_DIR / "frontend-work"
STAGED_DIST_DIR = BUILD_DIR / "desktop-assets" / "apps" / "viewer" / "dist"
VIEWER_DIR = PROJECT_ROOT / "apps" / "viewer"
REQUIREMENTS_FILE = PROJECT_ROOT / "packaging" / "requirements-app.txt"
BUILD_REQUIREMENTS_FILE = PROJECT_ROOT / "packaging" / "requirements-build.txt"
SETUPTOOLS_VERSION = "82.0.1"

FRONTEND_FILES = (
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "eslint.config.js",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "index.html",
)
PUBLIC_FILES = ("favicon.svg", "icons.svg")


class _AssetReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"link", "script"}:
            return
        for name, value in attrs:
            if name in {"href", "src"} and value and value.startswith("/"):
                self.paths.append(value.split("?", 1)[0].lstrip("/"))


def main() -> None:
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise SystemExit("Build requires 64-bit Windows and Python 3.12")

    BUILD_DIR.mkdir(exist_ok=True)
    _recreate_generated_dir(VENV_DIR)
    _run([sys.executable, "-m", "venv", str(VENV_DIR)])
    python = VENV_DIR / "Scripts" / "python.exe"
    _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", f"setuptools=={SETUPTOOLS_VERSION}"])
    _run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check",
        "--no-build-isolation", "-r", str(REQUIREMENTS_FILE),
    ])
    _run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check",
        "-r", str(BUILD_REQUIREMENTS_FILE),
    ])
    _run([str(python), "-m", "pip", "check"])
    _run([
        str(python), "-c",
        "import estrattore, openpyxl, PIL, pdf2image, pypdf, pytesseract, tkinter, webview, PyInstaller",
    ])

    _recreate_generated_dir(FRONTEND_WORK_DIR)
    _stage_frontend_sources()
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("npm.cmd is required on the build machine")
    _run([npm, "ci"], cwd=FRONTEND_WORK_DIR)
    _run([npm, "run", "lint"], cwd=FRONTEND_WORK_DIR)
    _run([npm, "run", "build"], cwd=FRONTEND_WORK_DIR)
    _stage_built_frontend()
    prepare_native()
    prepare_webview()

    print(f"Python environment: {VENV_DIR}")
    print(f"Frontend assets: {STAGED_DIST_DIR}")
    print(f"Native engines: {BUILD_DIR / 'desktop-assets' / 'vendor'}")


def _stage_frontend_sources() -> None:
    for name in FRONTEND_FILES:
        shutil.copy2(VIEWER_DIR / name, FRONTEND_WORK_DIR / name)
    shutil.copytree(VIEWER_DIR / "src", FRONTEND_WORK_DIR / "src")
    public_dir = FRONTEND_WORK_DIR / "public"
    public_dir.mkdir()
    for name in PUBLIC_FILES:
        shutil.copy2(VIEWER_DIR / "public" / name, public_dir / name)


def _stage_built_frontend() -> None:
    dist_dir = FRONTEND_WORK_DIR / "dist"
    if not (dist_dir / "index.html").is_file() or not (dist_dir / "assets").is_dir():
        raise RuntimeError("Frontend build is missing index.html or assets")

    _recreate_generated_dir(STAGED_DIST_DIR)
    shutil.copy2(dist_dir / "index.html", STAGED_DIST_DIR / "index.html")
    shutil.copytree(dist_dir / "assets", STAGED_DIST_DIR / "assets")
    for name in PUBLIC_FILES:
        shutil.copy2(dist_dir / name, STAGED_DIST_DIR / name)

    references = _AssetReferences()
    references.feed((STAGED_DIST_DIR / "index.html").read_text(encoding="utf-8"))
    if not references.paths or any(
        not (STAGED_DIST_DIR / path).is_file() for path in references.paths
    ):
        raise RuntimeError("Packaged frontend has missing static references")
    if (STAGED_DIST_DIR / "sample").exists():
        raise RuntimeError("Sample invoices must not enter the desktop package")


def _recreate_generated_dir(path: Path) -> None:
    resolved_build_dir = BUILD_DIR.resolve()
    resolved_path = path.resolve()
    if resolved_build_dir not in resolved_path.parents:
        raise ValueError(f"Generated path outside build directory: {path}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _run(command: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    print(f"Running: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


if __name__ == "__main__":
    main()
