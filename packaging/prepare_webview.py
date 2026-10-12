from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build"
ARCHIVE_DIR = BUILD_DIR / "downloads"
DESTINATION = BUILD_DIR / "desktop-assets" / "vendor" / "webview2"
VERSION = "153.0.4234.48"
ARCHIVE_NAME = f"Microsoft.WebView2.FixedVersionRuntime.{VERSION}.x64.cab"
ARCHIVE_URL = (
    "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/"
    "files/08cd33ee-d109-49b8-9301-9f0bea43c575/"
    + ARCHIVE_NAME
)
ARCHIVE_SHA256 = "11e8240cb0bc56dcd3e4498907203c251346f65107fe35a3a13e152c7d51c79e"
ARCHIVE_SIZE = 308509880


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("WebView2 staging requires Windows")
    archive = _verified_archive()
    BUILD_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="webview2-extract-", dir=BUILD_DIR) as temporary:
        extracted = Path(temporary)
        subprocess.run(
            ["expand.exe", str(archive), "-F:*", str(extracted)],
            check=True,
            capture_output=True,
            text=True,
        )
        candidates = list(extracted.rglob("msedgewebview2.exe"))
        if len(candidates) != 1 or VERSION not in candidates[0].parent.name:
            raise RuntimeError("WebView2 CAB has an unexpected runtime layout or version")
        runtime = candidates[0].parent
        _verify_microsoft_signature(candidates[0])
        _recreate_destination()
        shutil.copytree(runtime, DESTINATION, dirs_exist_ok=True)

    if not (DESTINATION / "msedgewebview2.exe").is_file():
        raise RuntimeError("WebView2 runtime staging is incomplete")
    print(f"Private WebView2 {VERSION}: {DESTINATION}")


def _verified_archive() -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive = ARCHIVE_DIR / ARCHIVE_NAME
    if archive.is_file():
        _verify_hash(archive)
        _verify_microsoft_signature(archive)
        return archive

    temporary = archive.with_name(f"{archive.stem}.partial.cab")
    with urllib.request.urlopen(ARCHIVE_URL, timeout=120) as response:
        with temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
    try:
        _verify_hash(temporary)
        _verify_microsoft_signature(temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(archive)
    return archive


def _verify_hash(path: Path) -> None:
    if path.stat().st_size != ARCHIVE_SIZE:
        raise RuntimeError(f"WebView2 CAB size mismatch: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError(f"WebView2 CAB checksum mismatch: {path}")


def _verify_microsoft_signature(path: Path) -> None:
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:ESTRATTORE_SIGNED_BINARY; "
        "if ($s.Status -ne 'Valid' -or "
        "$s.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { exit 1 }"
    )
    environment = dict(os.environ, ESTRATTORE_SIGNED_BINARY=str(path))
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _recreate_destination() -> None:
    vendor = (BUILD_DIR / "desktop-assets" / "vendor").resolve()
    if vendor not in DESTINATION.resolve().parents:
        raise ValueError(f"WebView2 staging path outside build directory: {DESTINATION}")
    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    DESTINATION.mkdir(parents=True)


if __name__ == "__main__":
    main()
