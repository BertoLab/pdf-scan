"""Exercise the real installer policy with tiny, isolated fixture releases.

Does not validate OCR, native runtime contents or clean-machine prerequisites.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import winreg
from pathlib import Path
from uuid import uuid4

from prepare_inno import BUILD_DIR, ROOT, prepare_compiler


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(
            f"Command exited {result.returncode}: {command[0]}\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )
    return result


def main():
    compiler = prepare_compiler()
    with tempfile.TemporaryDirectory(prefix="installer-policy-", dir=BUILD_DIR) as directory:
        work = Path(directory).resolve()
        if BUILD_DIR.resolve() not in work.parents:
            raise RuntimeError("Test outside build directory")
        identity = f"EstrattoreScanFatture.Test.{uuid4().hex}"
        shortcut = f"Estrattore Test {uuid4().hex}"
        app, data, bundle = work / "app", work / "data", work / "bundle"
        runtime = bundle / "_internal" / "vendor" / "webview2"
        runtime.mkdir(parents=True)
        (runtime / "msedgewebview2.exe").write_bytes(b"fixture-not-executable")
        (bundle / "EstrattoreScanFatture.exe").write_bytes(b"fixture-not-executable")
        for relative in (
            "apps/viewer/dist/index.html",
            "vendor/poppler/bin/pdfinfo.exe",
            "vendor/tesseract/tesseract.exe",
        ):
            target = bundle / "_internal" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fixture")
        data.mkdir()
        (data / "business-sentinel.txt").write_text("preserve on upgrade")
        installers = {}
        releases = {
            "A": "0.1.0-aaaaaaaaaaaa",
            "B": "0.1.0-bbbbbbbbbbbb",
            "C": "0.1.0-cccccccccccc",
        }
        try:
            for label, release in releases.items():
                marker = work / f"{label}.marker"
                marker.write_text(f"version=0.1.0\nrelease={release}\n", encoding="ascii")
                defines = {
                    "BundleRoot": bundle, "ReleaseId": release, "Version": "0.1.0",
                    "MarkerFile": marker, "ProductId": identity, "DataRoot": data,
                    "InstallRoot": app, "SmokeBuild": "1", "ShortcutName": shortcut,
                    "ArtifactBaseName": f"EstrattoreScanFatture-Setup-{release}",
                }
                run([str(compiler), *(f"/D{k}={v}" for k, v in defines.items()),
                     f"/O{work}", str(ROOT / "packaging" / "EstrattoreScanFatture.iss")])
                installers[label] = work / f"EstrattoreScanFatture-Setup-{release}.exe"
            broken_release = "0.1.0-dddddddddddd"
            broken_bundle = work / "broken-bundle"
            broken_bundle.mkdir()
            (broken_bundle / "EstrattoreScanFatture.exe").write_bytes(b"fixture-not-executable")
            broken_marker = work / "broken.marker"
            broken_marker.write_text(
                f"version=0.1.0\nrelease={broken_release}\n", encoding="ascii"
            )
            broken_defines = {
                "BundleRoot": broken_bundle, "ReleaseId": broken_release,
                "Version": "0.1.0", "MarkerFile": broken_marker,
                "ProductId": identity, "DataRoot": data, "InstallRoot": app,
                "SmokeBuild": "1", "ShortcutName": shortcut,
                "ArtifactBaseName": f"EstrattoreScanFatture-Setup-{broken_release}",
            }
            run([str(compiler), *(f"/D{k}={v}" for k, v in broken_defines.items()),
                 f"/O{work}", str(ROOT / "packaging" / "EstrattoreScanFatture.iss")])
            for step, label in enumerate(("A", "B", "B", "C")):
                release = releases[label]
                if step == 1:
                    broken_result = subprocess.run(
                        [str(work / f"EstrattoreScanFatture-Setup-{broken_release}.exe"),
                         "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                         f"/LOG={work / 'broken-install.log'}"],
                        capture_output=True, text=True, timeout=120,
                    )
                    if broken_result.returncode != 10:
                        log = (work / "broken-install.log").read_text(errors="replace")
                        raise AssertionError(
                            f"Broken install returned {broken_result.returncode}, expected 10. Releases: "
                            f"{[p.name for p in (app / 'releases').iterdir()]}\n"
                            f"{log[-5000:]}"
                        )
                    assert (app / "releases" / releases["A"]).exists()
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\{identity}") as key:
                        # Inno does not roll back shortcuts or registry after post-install validation.
                        assert winreg.QueryValueEx(key, "ActiveRelease")[0] == broken_release
                run([str(installers[label]), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
                assert {p.name for p in (app / "releases").iterdir()} == {release}
                assert (data / "business-sentinel.txt").read_text() == "preserve on upgrade"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\{identity}") as key:
                    assert winreg.QueryValueEx(key, "ActiveRelease")[0] == release
                    try:
                        winreg.QueryValueEx(key, "PreviousRelease")
                    except FileNotFoundError:
                        pass
                    else:
                        raise AssertionError("Obsolete PreviousRelease registry value remains")
            # Inspect the real generated shortcut, not just the .iss template.
            link = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / f"{shortcut}.lnk"
            escaped_link = str(link).replace("'", "''")
            output = run(["powershell", "-NoProfile", "-Command",
                          f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{escaped_link}'); $s.Arguments"]).stdout.strip()
            assert output == f'--data-dir "{data}"', output
            print("Installer A -> B -> B -> C, immediate pruning and data retention: OK")
        finally:
            uninstallers = sorted(app.glob("unins*.exe")) if app.exists() else []
            if uninstallers:
                run([str(uninstallers[-1]), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/REMOVEAPPDATA"])
                # Inno's uninstaller may delegate to a temporary executable.
                deadline = time.monotonic() + 30
                while data.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert not data.exists(), "Fixture data was not removed by explicit uninstall"
    print("Isolated fixture uninstall: OK")


if __name__ == "__main__":
    main()
