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


def release_label(project: dict) -> str:
    version = project["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Unsupported desktop product version: {version}")
    release = project["tool"]["estrattore"]["desktop_release"]
    stage = release["stage"]
    if stage == "stable" and "revision" not in release:
        return version
    revision = release.get("revision")
    if stage == "rc" and type(revision) is int and revision > 0:
        return f"{version}-rc.{revision}"
    raise ValueError("Desktop release must be stable or rc with a positive revision")


def artifact_basename(label: str, *, smoke: bool = False, smoke_suffix: str = "") -> str:
    name = f"EstrattoreScanFatture-Setup-{label}"
    if smoke:
        name += "-Smoke"
        if smoke_suffix:
            name += f"-{smoke_suffix}"
    return name


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

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    label = release_label(project)
    artifact_name = artifact_basename(label, smoke=args.smoke, smoke_suffix=args.smoke_suffix)
    output = DIST / f"{artifact_name}.exe"
    if not args.smoke and output.exists():
        raise SystemExit(
            f"Installer candidate already exists: {output}. "
            "Increment the desktop release revision before building changed content."
        )

    if not args.use_existing_bundle:
        print("[1/4] Preparo le dipendenze e compilo l'app desktop...", flush=True)
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "packaging" / "build_desktop.py")],
            cwd=PROJECT_ROOT, check=True,
        )
    else:
        print("[1/4] Riutilizzo il bundle desktop esistente...", flush=True)

    print("[2/4] Verifico il bundle e calcolo la sua impronta...", flush=True)
    _validate_bundle(APP_DIR)
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
        "ArtifactBaseName": artifact_name,
    }
    if args.smoke:
        defines["SmokeBuild"] = "1"
        defines["InstallRoot"] = str(PROJECT_ROOT / "build" / "installer-smoke" / "app")
        defines["ProductId"] = "EstrattoreScanFatture.Desktop.Smoke"
        defines["DataRoot"] = str(PROJECT_ROOT / "build" / "installer-smoke" / "data" / "EstrattoreScanFatture")
        defines["ShortcutName"] = "Estrattore Scan Fatture (Smoke)"
    print("[3/4] Verifico il compilatore dell'installer...", flush=True)
    compiler = prepare_compiler()
    command = [str(compiler), *(f"/D{name}={value}" for name, value in defines.items()),
               f"/O{DIST}", str(SCRIPT)]
    print("[4/4] Compilo l'installer offline...", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    if not output.is_file():
        raise RuntimeError(f"Inno Setup did not create {output}")
    print(f"Completato. Installer: {output}", flush=True)


if __name__ == "__main__":
    main()
