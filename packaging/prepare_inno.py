from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"
VERSION = "7.1.0"
INSTALLER_URL = (
    "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/"
    "innosetup-7.1.0-x64.exe"
)
INSTALLER_SHA256 = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f"
INSTALLER_PATH = BUILD_DIR / "downloads" / "innosetup-7.1.0-x64.exe"
COMPILER_PATH = BUILD_DIR / f"inno-setup-{VERSION}" / "ISCC.exe"


def prepare_compiler() -> Path:
    if COMPILER_PATH.is_file():
        _check_signature(COMPILER_PATH)
        _check_compiler()
        return COMPILER_PATH

    INSTALLER_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not INSTALLER_PATH.is_file() or _sha256(INSTALLER_PATH) != INSTALLER_SHA256:
        partial = INSTALLER_PATH.with_suffix(".partial")
        with urlopen(INSTALLER_URL, timeout=120) as source, partial.open("wb") as target:
            while block := source.read(1024 * 1024):
                target.write(block)
        if _sha256(partial) != INSTALLER_SHA256:
            raise RuntimeError("Inno Setup download failed checksum verification")
        partial.replace(INSTALLER_PATH)

    _check_signature(INSTALLER_PATH)

    COMPILER_PATH.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(INSTALLER_PATH), "/PORTABLE=1", "/VERYSILENT", "/CURRENTUSER",
            f"/DIR={COMPILER_PATH.parent}", "/NOICONS", "/NORESTART",
        ],
        check=True,
        timeout=180,
    )
    _check_signature(COMPILER_PATH)
    _check_compiler()
    return COMPILER_PATH


def _check_signature(path: Path) -> None:
    environment = dict(os.environ, INNO_SETUP_FILE=str(path))
    signature = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$s = Get-AuthenticodeSignature -LiteralPath $env:INNO_SETUP_FILE; "
            "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*CN=Pyrsys B.V.*') "
            "{ throw 'Inno Setup signature is not valid' }",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if signature.returncode:
        raise RuntimeError(signature.stderr.strip() or "Inno Setup signature verification failed")

def _check_compiler() -> None:
    result = subprocess.run(
        [str(COMPILER_PATH), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    if result.stdout.strip() != VERSION:
        raise RuntimeError(f"Expected Inno Setup {VERSION}, got {result.stdout.strip()}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    print(prepare_compiler())
