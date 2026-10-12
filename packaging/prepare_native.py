from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build"
ARCHIVE_DIR = BUILD_DIR / "downloads"
STAGED_VENDOR_DIR = BUILD_DIR / "desktop-assets" / "vendor"
TESSERACT_SOURCE = PROJECT_ROOT / "vendor" / "tesseract"
POPPLER_VERSION = "26.09.0-0"
POPPLER_URL = (
    "https://github.com/oschwartz10612/poppler-windows/releases/download/"
    f"v{POPPLER_VERSION}/Release-{POPPLER_VERSION}.zip"
)
POPPLER_SHA256 = "7a6f256a0ddf7536182246a5733331bf4677cbcc34f4663774947ad34556c8d0"
POPPLER_PREFIX = f"poppler-{POPPLER_VERSION.removesuffix('-0')}/Library/"
POPPLER_DATA_PREFIX = f"poppler-{POPPLER_VERSION.removesuffix('-0')}/share/poppler/"
TESSERACT_LANGUAGES = ("eng", "ita", "osd")
TESSERACT_SOURCE_SHA256 = "c85aca5f2bf32a91d86d2da48e89549fb4726358794323d5aa157093f67e1046"


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Native engine staging requires Windows")

    archive = _verified_poppler_archive()
    _stage_poppler(archive)
    _stage_tesseract()
    _validate_engines()
    print(f"Native engines: {STAGED_VENDOR_DIR}")


def _verified_poppler_archive() -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive = ARCHIVE_DIR / f"poppler-{POPPLER_VERSION}.zip"
    if archive.is_file() and _sha256(archive) == POPPLER_SHA256:
        return archive

    temporary = archive.with_suffix(".download")
    with urllib.request.urlopen(POPPLER_URL, timeout=120) as response:
        with temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
    if _sha256(temporary) != POPPLER_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Poppler download checksum mismatch")
    temporary.replace(archive)
    return archive


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_poppler(archive: Path) -> None:
    destination = STAGED_VENDOR_DIR / "poppler"
    _recreate_staging_dir(destination)
    with zipfile.ZipFile(archive) as source:
        members = source.namelist()
        binary_members = [
            name for name in members
            if name.startswith(POPPLER_PREFIX + "bin/")
            and (name.lower().endswith(".dll") or name.rsplit("/", 1)[-1] in {"pdfinfo.exe", "pdftoppm.exe"})
        ]
        data_members = [
            name for name in members
            if name.startswith(POPPLER_DATA_PREFIX) and not name.endswith("/")
        ]
        license_members = [
            name for name in data_members
            if name.rsplit("/", 1)[-1].lower().startswith(("license", "copying"))
        ]
        for name in binary_members + data_members:
            if name.startswith(POPPLER_PREFIX):
                relative = Path(name.removeprefix(POPPLER_PREFIX))
            else:
                relative = Path("share/poppler") / name.removeprefix(POPPLER_DATA_PREFIX)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Invalid Poppler archive member: {name}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(name) as input_file, target.open("wb") as output:
                shutil.copyfileobj(input_file, output)

    if not license_members:
        raise RuntimeError("Poppler archive has no redistributable license material")
    for name in ("pdfinfo.exe", "pdftoppm.exe"):
        if not (destination / "bin" / name).is_file():
            raise RuntimeError(f"Poppler archive lacks {name}")


def _stage_tesseract() -> None:
    destination = STAGED_VENDOR_DIR / "tesseract"
    _recreate_staging_dir(destination)
    required = [
        TESSERACT_SOURCE / "tesseract.exe",
        TESSERACT_SOURCE / "doc" / "LICENSE",
        TESSERACT_SOURCE / "doc" / "AUTHORS",
    ]
    required.extend(TESSERACT_SOURCE / "tessdata" / f"{lang}.traineddata" for lang in TESSERACT_LANGUAGES)
    required.append(TESSERACT_SOURCE / "tessdata_best" / "ita.traineddata")
    required.extend(TESSERACT_SOURCE.glob("*.dll"))
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Tesseract runtime incomplete: {', '.join(missing)}")
    digest = hashlib.sha256()
    for path in sorted(required):
        digest.update(path.relative_to(TESSERACT_SOURCE).as_posix().encode())
        digest.update(bytes.fromhex(_sha256(path)))
    if digest.hexdigest() != TESSERACT_SOURCE_SHA256:
        raise RuntimeError("Tesseract source differs from the pinned runtime")

    shutil.copy2(TESSERACT_SOURCE / "tesseract.exe", destination / "tesseract.exe")
    for dll in TESSERACT_SOURCE.glob("*.dll"):
        shutil.copy2(dll, destination / dll.name)
    (destination / "tessdata").mkdir()
    (destination / "tessdata_best").mkdir()
    for lang in TESSERACT_LANGUAGES:
        shutil.copy2(TESSERACT_SOURCE / "tessdata" / f"{lang}.traineddata", destination / "tessdata" / f"{lang}.traineddata")
    shutil.copy2(TESSERACT_SOURCE / "tessdata_best" / "ita.traineddata", destination / "tessdata_best" / "ita.traineddata")
    shutil.copy2(TESSERACT_SOURCE / "doc" / "LICENSE", destination / "LICENSE")
    shutil.copy2(TESSERACT_SOURCE / "doc" / "AUTHORS", destination / "AUTHORS")


def _validate_engines() -> None:
    poppler = STAGED_VENDOR_DIR / "poppler" / "bin"
    tesseract = STAGED_VENDOR_DIR / "tesseract"
    for command in (
        [str(poppler / "pdfinfo.exe"), "-v"],
        [str(poppler / "pdftoppm.exe"), "-v"],
        [str(tesseract / "tesseract.exe"), "--version"],
    ):
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        print((result.stdout or result.stderr).splitlines()[0])
    for profile, required in (("tessdata", set(TESSERACT_LANGUAGES)), ("tessdata_best", {"ita"})):
        result = subprocess.run(
            [str(tesseract / "tesseract.exe"), "--list-langs", "--tessdata-dir", str(tesseract / profile)],
            capture_output=True, text=True, check=True,
        )
        available = set(result.stdout.splitlines()[1:])
        if not required <= available:
            raise RuntimeError(f"Tesseract {profile} is missing: {sorted(required - available)}")
        print(f"Tesseract {profile}: {', '.join(sorted(available))}")


def _recreate_staging_dir(path: Path) -> None:
    if (BUILD_DIR.resolve() / "desktop-assets" / "vendor") not in path.resolve().parents:
        raise ValueError(f"Unexpected staging path: {path}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


if __name__ == "__main__":
    main()
