from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from estrattore.runtime_paths import (
    DATA_DIR_ENV_VAR,
    RuntimePaths,
    bundled_assets_root,
    get_runtime_paths,
)


WINDOW_TITLE = "Estrattore Scan Fatture"
APP_MUTEX_NAME = r"Local\EstrattoreScanFattureDesktop"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--document-worker":
        sys.argv.pop(1)
        from estrattore.document_worker import main as worker_main

        return worker_main()

    args = _parse_args()
    if args.data_dir:
        os.environ[DATA_DIR_ENV_VAR] = str(args.data_dir.expanduser().resolve())
    paths = get_runtime_paths()
    try:
        paths.config_dir.mkdir(parents=True, exist_ok=True)
        with _single_instance_lock(paths.config_dir / "desktop.lock"):
            _run_window(paths)
    except AlreadyRunningError:
        _show_error("L'app è già aperta su questo computer.")
        return 1
    except Exception:
        error_details = traceback.format_exc()
        try:
            paths.logs_dir.mkdir(parents=True, exist_ok=True)
            with (paths.logs_dir / "desktop-error.log").open("a", encoding="utf-8") as log:
                log.write(error_details + "\n")
        except OSError:
            _show_error("Non riesco ad accedere alla cartella dati dell'app. Controlla spazio e permessi.")
            return 1
        _show_error("L'app non si è avviata. Controlla il file desktop-error.log nei dati dell'app.")
        return 1
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avvia l'app desktop")
    parser.add_argument("--data-dir", type=Path, help="Cartella dati isolata per i test")
    return parser.parse_args()


class AlreadyRunningError(RuntimeError):
    pass


class DesktopPreviewApi:
    def __init__(self, webview, port: int, archive_dir: Path) -> None:
        self._webview = webview
        self._port = port
        self._archive_dir = archive_dir.resolve()
        self._windows = []
        self._lock = threading.Lock()
        self._closed = False

    def open_pdf(self, url: str, title: str) -> bool:
        parsed = urlsplit(url)
        prefix = "/runtime/archive/"
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != self._port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(prefix)
        ):
            raise ValueError("Il PDF richiesto non appartiene all'archivio dell'app")

        relative_path = unquote(parsed.path[len(prefix):])
        candidate = (self._archive_dir / relative_path).resolve()
        if (
            self._archive_dir not in candidate.parents
            or candidate.suffix.lower() != ".pdf"
            or not candidate.is_file()
        ):
            raise ValueError("Il PDF non e' piu' disponibile nell'archivio")

        with self._lock:
            if self._closed:
                raise RuntimeError("L'app si sta chiudendo")
            window = self._webview.create_window(
                title if title in {"PDF fattura", "PDF origine"} else "PDF",
                url,
                width=980,
                height=860,
                min_size=(500, 500),
                text_select=True,
            )
            self._windows.append(window)

        def on_closed() -> None:
            with self._lock:
                if window in self._windows:
                    self._windows.remove(window)

        window.events.closed += on_closed
        return True

    def _close_all(self) -> None:
        with self._lock:
            self._closed = True
            windows = self._windows[:]
            self._windows.clear()
        for window in windows:
            window.destroy()


@contextmanager
def _single_instance_lock(path: Path) -> Iterator[None]:
    if os.name != "nt":
        raise RuntimeError("Il launcher desktop richiede Windows")
    import msvcrt

    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise AlreadyRunningError() from error
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        mutex = None
        try:
            mutex = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
            if not mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            yield
        finally:
            if mutex:
                kernel32.CloseHandle(mutex)
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _private_webview_runtime() -> Path | None:
    runtime = bundled_assets_root() / "vendor" / "webview2"
    if (runtime / "msedgewebview2.exe").is_file():
        return runtime
    if getattr(sys, "frozen", False):
        raise RuntimeError("Manca il motore della finestra nel pacchetto dell'app")
    return None


def _run_window(paths: RuntimePaths) -> None:
    runtime = _private_webview_runtime()
    import webview

    if runtime:
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime)
    webview.settings["ALLOW_DOWNLOADS"] = True

    from scripts.local_app import create_local_app_server
    from scripts.registry_api import (
        HOST,
        has_active_processing_job,
        resume_processing_after_failed_shutdown,
        shutdown_processing_jobs,
    )

    from scripts.desktop_lifecycle import DesktopCloseCoordinator

    server = create_local_app_server(0, require_session=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    preview_api: DesktopPreviewApi | None = None
    try:
        loaded = threading.Event()
        preview_api = DesktopPreviewApi(webview, server.server_port, paths.archive_dir)
        window = webview.create_window(
            WINDOW_TITLE,
            f"http://{HOST}:{server.server_port}/?desktop=1",
            width=1500,
            height=940,
            min_size=(1000, 700),
            background_color="#edf2ef",
            text_select=True,
            js_api=preview_api,
        )

        closer = DesktopCloseCoordinator(
            window, server, has_ocr=has_active_processing_job,
            stop_ocr=shutdown_processing_jobs, resume_ocr=resume_processing_after_failed_shutdown,
            close_previews=preview_api._close_all, show_error=_show_error,
        )

        def on_loaded() -> None:
            loaded.set()
            with (paths.logs_dir / "app.log").open("a", encoding="utf-8") as log:
                log.write("window_loaded=true\n")

        window.events.closing += closer.on_closing
        window.events.loaded += on_loaded
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(paths.config_dir / "webview-profile"),
        )
        if not loaded.is_set() and not closer.allowed:
            raise RuntimeError("La finestra desktop non ha caricato l'app")
    finally:
        if preview_api is not None:
            preview_api._close_all()
        shutdown_processing_jobs(timeout=30)
        server.drain_mutations(timeout=30)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
    else:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
