from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from prepare_build import BUILD_DIR, PROJECT_ROOT, VENV_DIR, main as prepare_build


APP_NAME = "EstrattoreScanFatture"
SPEC_FILE = PROJECT_ROOT / "packaging" / f"{APP_NAME}.spec"
DIST_DIR = BUILD_DIR / "desktop-dist"
APP_DIR = DIST_DIR / APP_NAME


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Desktop bundling requires Windows")
    args = _parse_args()
    if not args.skip_preparation:
        prepare_build()

    python = VENV_DIR / "Scripts" / "python.exe"
    if not python.is_file():
        raise SystemExit("Run packaging/prepare_build.py before --skip-preparation")
    _validate_inputs()
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(BUILD_DIR / "pyinstaller-cache"))
    subprocess.run(
        [
            str(python), "-m", "PyInstaller", "--noconfirm",
            "--distpath", str(DIST_DIR),
            "--workpath", str(BUILD_DIR / "pyinstaller-work"),
            str(SPEC_FILE),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )
    _validate_bundle(APP_DIR)
    _grant_webview_app_container_access(APP_DIR / "_internal" / "vendor" / "webview2")
    print(f"Desktop bundle: {APP_DIR}")


def _validate_inputs() -> None:
    assets = BUILD_DIR / "desktop-assets"
    required = (
        assets / "apps" / "viewer" / "dist" / "index.html",
        assets / "vendor" / "poppler" / "bin" / "pdfinfo.exe",
        assets / "vendor" / "tesseract" / "tesseract.exe",
        assets / "vendor" / "webview2" / "msedgewebview2.exe",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Desktop build inputs are missing: {', '.join(missing)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Windows onedir application")
    parser.add_argument(
        "--skip-preparation", action="store_true",
        help="Reuse already verified build inputs for an iterative developer build",
    )
    return parser.parse_args()


def _validate_bundle(directory: Path) -> None:
    internal = directory / "_internal"
    required = (
        directory / f"{APP_NAME}.exe",
        internal / "apps" / "viewer" / "dist" / "index.html",
        internal / "vendor" / "poppler" / "bin" / "pdfinfo.exe",
        internal / "vendor" / "tesseract" / "tesseract.exe",
        internal / "vendor" / "webview2" / "msedgewebview2.exe",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Incomplete onedir bundle: {', '.join(missing)}")
    if (internal / "apps" / "viewer" / "dist" / "sample").exists():
        raise RuntimeError("Development sample invoices entered the onedir bundle")


def _grant_webview_app_container_access(runtime: Path) -> None:
    if not runtime.is_dir():
        raise RuntimeError(f"Missing private WebView2 directory: {runtime}")
    for sid in ("S-1-15-2-2", "S-1-15-2-1"):
        subprocess.run(
            ["icacls", str(runtime), "/grant", f"*{sid}:(OI)(CI)(RX)", "/T"],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    main()
