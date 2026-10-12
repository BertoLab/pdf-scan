from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.request import Request, build_opener, HTTPCookieProcessor
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from estrattore.document_worker import (
    DOCUMENT_WORKER_SCHEMA_VERSION,
    DocumentWorkerRequest,
    load_worker_result,
    save_worker_request,
)


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"
SOURCE_BUNDLE = BUILD_DIR / "desktop-dist" / "EstrattoreScanFatture"
WINDOW_TITLE = "Estrattore Scan Fatture"
WM_CLOSE = 0x0010
urlopen = build_opener(HTTPCookieProcessor(CookieJar())).open


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Frozen desktop smoke requires Windows")
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed-dir", type=Path)
    args = parser.parse_args()
    source_bundle = args.installed_dir.resolve() if args.installed_dir else SOURCE_BUNDLE
    if not (source_bundle / "EstrattoreScanFatture.exe").is_file():
        raise SystemExit("Run packaging/build_desktop.py first")

    with tempfile.TemporaryDirectory(prefix="frozen desktop smoke ", dir=BUILD_DIR) as temporary:
        test_root = Path(temporary).resolve()
        if BUILD_DIR.resolve() not in test_root.parents:
            raise RuntimeError("Smoke directory is outside the project build directory")
        bundle = source_bundle if args.installed_dir else test_root / "relocated app with spaces"
        if not args.installed_dir:
            shutil.copytree(source_bundle, bundle)
        executable = bundle / "EstrattoreScanFatture.exe"
        data_dir = test_root / "isolated user data"
        try:
            _smoke_worker(executable, data_dir, test_root)
            _smoke_window(executable, data_dir, bundle, test_root / "test invoice.pdf")
        except Exception:
            for log in (data_dir / "logs").glob("*.log"):
                print(f"{log.name}:\n{log.read_text(encoding='utf-8')[-4000:]}")
            raise

    print("Frozen upload -> OCR queue -> review, private runtimes, window and close: OK")


def _smoke_worker(executable: Path, data_dir: Path, test_root: Path) -> None:
    source = test_root / "test invoice.pdf"
    image = Image.new("RGB", (1200, 400), "white")
    font = ImageFont.truetype("arial.ttf", 60)
    ImageDraw.Draw(image).text((70, 100), "FATTURA 123", fill="black", font=font)
    image.save(source, "PDF", resolution=200)

    request = test_root / "worker-request.json"
    result = test_root / "worker-result.json"
    gate = test_root / "worker-start.ok"
    output = test_root / "document-output"
    save_worker_request(
        DocumentWorkerRequest(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            source_path=str(source),
            working_output_dir=str(test_root / "document-output.working"),
            output_dir=str(output),
            document_id="smoke-document",
            first_invoice_number=1,
        ),
        request,
    )
    gate.touch()
    environment = dict(os.environ, ESTRATTORE_SCAN_DATA_DIR=str(data_dir))
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
    completed = subprocess.run(
        [
            str(executable), "--document-worker", "--request", str(request),
            "--result", str(result), "--start-gate", str(gate),
        ],
        cwd=test_root,
        env=environment,
        timeout=300,
        capture_output=True,
        text=True,
    )
    if not result.is_file():
        raise RuntimeError(f"Frozen OCR worker wrote no result (exit {completed.returncode})")
    worker_result = load_worker_result(result)
    if completed.returncode != 0 or worker_result.status != "completed":
        raise RuntimeError(f"Frozen OCR worker failed: {worker_result.error}\n{worker_result.traceback}")
    if not (output / "viewer_manifest.json").is_file() or not (output / "document_complete.ok").is_file():
        raise RuntimeError("Frozen OCR worker did not commit the document")


def _smoke_window(executable: Path, data_dir: Path, bundle: Path, source_pdf: Path) -> None:
    process = subprocess.Popen(
        [str(executable), "--data-dir", str(data_dir)],
        cwd=data_dir.parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = _wait_for_url(data_dir / "logs" / "app.log", process)
        with urlopen(url + "/?desktop=1", timeout=10) as response:
            if response.status != 200 or b"/assets/" not in response.read():
                raise RuntimeError("Frozen frontend was not served")
        for route in ("/health", "/viewer/review-batches", "/processing/jobs", "/registry/status"):
            with urlopen(url + route, timeout=10) as response:
                json.load(response)
        window = _wait_for_window(process)
        _wait_for_window_loaded(data_dir / "logs" / "app.log", process)
        _verify_private_webview_process(bundle)
        _smoke_processing_route(url, source_pdf)
        if not ctypes.windll.user32.PostMessageW(window, WM_CLOSE, 0, 0):
            raise RuntimeError("Could not close the desktop window")
        if process.wait(timeout=30) != 0:
            raise RuntimeError("Desktop launcher returned an error on close")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def _wait_for_url(log_path: Path, process: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Desktop launcher exited early: {process.returncode}")
        if log_path.is_file():
            urls = [line[4:] for line in log_path.read_text(encoding="utf-8").splitlines() if line.startswith("url=")]
            if urls:
                return urls[-1]
        time.sleep(0.2)
    raise TimeoutError("Frozen desktop server did not start")


def _wait_for_window(process: subprocess.Popen[bytes], *, include_hidden: bool = False) -> int:
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL
    windows: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL

    def collect(handle: int, _parameter: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == process.pid and (include_hidden or user32.IsWindowVisible(handle)):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, len(title))
            if title.value == WINDOW_TITLE:
                windows.append(handle)
        return True

    callback = callback_type(collect)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        windows.clear()
        user32.EnumWindows(callback, 0)
        if windows:
            return windows[0]
        if process.poll() is not None:
            raise RuntimeError(f"Desktop window exited early: {process.returncode}")
        time.sleep(0.2)
    raise TimeoutError("Frozen desktop window did not appear")


def _wait_for_window_loaded(log_path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if "window_loaded=true" in log_path.read_text(encoding="utf-8"):
            return
        if process.poll() is not None:
            raise RuntimeError(f"Desktop window exited before loading: {process.returncode}")
        time.sleep(0.2)
    raise TimeoutError("Frozen desktop window did not load its frontend")


def _verify_private_webview_process(bundle: Path) -> None:
    runtime_exe = bundle / "_internal" / "vendor" / "webview2" / "msedgewebview2.exe"
    script = (
        "$p = Get-CimInstance Win32_Process -Filter \"name='msedgewebview2.exe'\" | "
        "Where-Object { $_.ExecutablePath -eq $env:ESTRATTORE_WEBVIEW_EXE }; "
        "if (-not $p) { exit 1 }"
    )
    environment = dict(os.environ, ESTRATTORE_WEBVIEW_EXE=str(runtime_exe))
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Private WebView2 renderer not observed: {result.stderr}")


def _smoke_processing_route(url: str, source_pdf: Path) -> None:
    boundary = uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
        'filename="test invoice.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
    ).encode("ascii") + source_pdf.read_bytes() + f"\r\n--{boundary}--\r\n".encode("ascii")
    import_request = Request(
        url + "/batches/import",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(import_request, timeout=30) as response:
        imported = json.load(response)
    batch_id = imported["batch"]["batch_id"]
    process_request = Request(
        url + "/batches/process",
        data=json.dumps({"batch_id": batch_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(process_request, timeout=30) as response:
        job_id = json.load(response)["job_id"]

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with urlopen(url + f"/processing/jobs/{job_id}", timeout=10) as response:
            job = json.load(response)
        if job["status"] == "completed":
            with urlopen(url + "/viewer/review-batches", timeout=10) as response:
                if not json.load(response)["batches"]:
                    raise RuntimeError("Completed OCR process created no review batch")
            return
        if job["status"] in {"error", "cancelled", "removed"}:
            raise RuntimeError(f"Frozen app OCR process failed: {job}")
        time.sleep(0.5)
    raise TimeoutError("Frozen app OCR process did not finish")


if __name__ == "__main__":
    main()
