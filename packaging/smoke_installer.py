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
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)


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
        data.mkdir()
        (data / "business-sentinel.txt").write_text("preserve on upgrade")
        marker = work / "marker"
        marker.write_text("complete")
        installers = {}
        try:
            for release in ("A", "B", "C"):
                defines = {
                    "BundleRoot": bundle, "ReleaseId": release, "Version": "0.1.0",
                    "MarkerFile": marker, "ProductId": identity, "DataRoot": data,
                    "InstallRoot": app, "SmokeBuild": "1", "ShortcutName": shortcut,
                }
                run([str(compiler), *(f"/D{k}={v}" for k, v in defines.items()),
                     f"/O{work}", str(ROOT / "packaging" / "EstrattoreScanFatture.iss")])
                installers[release] = work / f"EstrattoreScanFatture-Setup-{release}.exe"
            for release, expected in (("A", {"A"}), ("B", {"A", "B"}), ("B", {"A", "B"}), ("C", {"B", "C"})):
                run([str(installers[release]), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
                assert {p.name for p in (app / "releases").iterdir()} == expected
                assert (data / "business-sentinel.txt").read_text() == "preserve on upgrade"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\{identity}") as key:
                    assert winreg.QueryValueEx(key, "ActiveRelease")[0] == release
                    if release != "A":
                        assert winreg.QueryValueEx(key, "PreviousRelease")[0] == ("A" if release == "B" else "B")
            # Inspect the real generated shortcut, not just the .iss template.
            link = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / f"{shortcut}.lnk"
            escaped_link = str(link).replace("'", "''")
            output = run(["powershell", "-NoProfile", "-Command",
                          f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{escaped_link}'); $s.Arguments"]).stdout.strip()
            assert output == f'--data-dir "{data}"', output
            print("Installer A -> B -> B -> C, isolated shortcut and data retention: OK")
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
