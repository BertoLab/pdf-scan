from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from build_desktop import APP_DIR, PROJECT_ROOT, _validate_bundle
from prepare_inno import prepare_compiler


SCRIPT = PROJECT_ROOT / "packaging" / "EstrattoreScanFatture.iss"
WORK = PROJECT_ROOT / "build" / "installer-work"
DIST = PROJECT_ROOT / "build" / "installer-dist"


def bundle_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Windows installer build requires Windows")
    parser = argparse.ArgumentParser(description="Build the offline Windows installer")
    parser.add_argument("--use-existing-bundle", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Use an isolated test product and data root")
    parser.add_argument("--smoke-suffix", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_suffix and (not args.smoke or not re.fullmatch(r"[A-Za-z0-9]{1,16}", args.smoke_suffix)):
        parser.error("--smoke-suffix requires --smoke and 1-16 ASCII alphanumeric characters")

    if not args.use_existing_bundle:
        build = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "packaging" / "build_desktop.py")],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        if build.returncode:
            raise RuntimeError(f"Desktop bundle build failed:\n{build.stdout[-6000:]}\n{build.stderr[-2000:]}")
        print("\n".join(build.stdout.splitlines()[-5:]))
    _validate_bundle(APP_DIR)
    version = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    release_id = f"{version}-{bundle_digest(APP_DIR)[:12]}"
    if args.smoke_suffix:
        release_id += f"-{args.smoke_suffix}"
    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    marker = WORK / f"{release_id}.complete"
    marker.write_text(f"version={version}\nrelease={release_id}\n", encoding="ascii")
    defines = {
        "BundleRoot": str(APP_DIR),
        "ReleaseId": release_id,
        "Version": version,
        "MarkerFile": str(marker),
    }
    if args.smoke:
        defines["SmokeBuild"] = "1"
        defines["InstallRoot"] = str(PROJECT_ROOT / "build" / "installer-smoke" / "app")
        defines["ProductId"] = "EstrattoreScanFatture.Desktop.Smoke"
        defines["DataRoot"] = str(PROJECT_ROOT / "build" / "installer-smoke" / "data" / "EstrattoreScanFatture")
        defines["BuildSuffix"] = "-Smoke"
        defines["ShortcutName"] = "Estrattore Scan Fatture (Smoke)"
    compiler = prepare_compiler()
    command = [str(compiler), *(f"/D{name}={value}" for name, value in defines.items()),
               f"/O{DIST}", str(SCRIPT)]
    result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Inno Setup failed:\n{result.stdout[-6000:]}\n{result.stderr[-2000:]}")
    print("\n".join(result.stdout.splitlines()[-7:]))
    output = DIST / f"EstrattoreScanFatture-Setup-{release_id}{'-Smoke' if args.smoke else ''}.exe"
    if not output.is_file():
        raise RuntimeError(f"Inno Setup did not create {output}")
    print(f"Installer: {output}")


if __name__ == "__main__":
    main()
