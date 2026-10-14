from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
import tempfile
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
        with _single_instance_lock(
            paths.config_dir / "desktop.lock", _mutex_name_for_paths(paths, args.dev_instance)
        ):
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
    parser.add_argument("--dev-instance", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


class AlreadyRunningError(RuntimeError):
    pass


def _mutex_name_for_paths(paths: RuntimePaths, dev_instance: bool) -> str:
    if not dev_instance:
        return APP_MUTEX_NAME
    if getattr(sys, "frozen", False):
        raise ValueError("Istanza di sviluppo non disponibile nell'app installata")
    if not (paths.app_data_dir / ".ui-demo-v1.json").is_file():
        raise ValueError("Istanza di sviluppo consentita solo con i dati dimostrativi")
    digest = hashlib.sha256(str(paths.app_data_dir).casefold().encode("utf-8")).hexdigest()[:16]
    return f"{APP_MUTEX_NAME}Dev{digest}"


class DesktopPreviewApi:
    def __init__(self, webview, port: int, archive_dir: Path, paths: RuntimePaths | None = None) -> None:
        self._webview = webview
        self._port = port
        self._archive_dir = archive_dir.resolve()
        self._paths = paths
        self._main_window = None
        self._windows = []
        self._lock = threading.Lock()
        self._closed = False

    def bind_window(self, window) -> None:
        self._main_window = window

    def _choose_save_path(self, suggested_name: str) -> Path | None:
        if self._main_window is None or self._closed:
            raise RuntimeError("La finestra dell'app non e' disponibile")
        suffix = Path(suggested_name).suffix.lower()
        if suffix not in {".xlsx", ".zip"}:
            raise ValueError("Formato di esportazione non valido")
        label = "Excel" if suffix == ".xlsx" else "Archivio ZIP"
        selected = self._main_window.create_file_dialog(
            self._webview.FileDialog.SAVE,
            save_filename=suggested_name,
            file_types=(f"{label} (*{suffix})",),
        )
        if not selected:
            return None
        target = Path(selected[0])
        if target.suffix.lower() != suffix:
            raise ValueError(f"Il file deve avere estensione {suffix}")
        return target

    @staticmethod
    def _write_export_file(target: Path, body: bytes) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.stem}-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _save_bytes(self, body: bytes, suggested_name: str) -> str | None:
        target = self._choose_save_path(suggested_name)
        if target is None:
            return None
        self._write_export_file(target, body)
        return target.name

    def export_invoices_native(self, record_ids, mode, history_id=None, include_summary=False) -> dict:
        from estrattore.invoice_export import export_invoices

        if self._paths is None:
            raise RuntimeError("Dati dell'app non disponibili")
        if mode not in {"workbook", "separate"} or not isinstance(include_summary, bool):
            raise ValueError("Formato di esportazione non valido")
        suggested_name = "fatture_condomini.zip" if mode == "separate" or include_summary else "fatture_condomini.xlsx"
        target = self._choose_save_path(suggested_name)
        if target is None:
            return {"ok": False, "cancelled": True}

        def deliver(body: bytes, name: str) -> None:
            if target.suffix.lower() != Path(name).suffix.lower():
                raise ValueError("Formato di esportazione non valido")
            self._write_export_file(target, body)

        return export_invoices(
            record_ids, mode, self._paths, history_id=history_id,
            include_summary=include_summary, deliver=deliver,
        )

    def save_existing_export(self, export_id: str) -> dict:
        import re

        if not isinstance(export_id, str) or not re.fullmatch(r"[0-9a-f]{32}", export_id):
            raise ValueError("Esportazione non valida")
        directory = self._archive_dir / "exports" / export_id
        source = next((path for path in (directory / "fatture_condomini.xlsx", directory / "fatture_condomini.zip") if path.is_file()), None)
        if source is None:
            raise FileNotFoundError("File esportato non piu' disponibile")
        filename = self._save_bytes(source.read_bytes(), source.name)
        return {"ok": filename is not None, "cancelled": filename is None, "file_name": filename}

    def save_named_export(self, kind: str) -> dict:
        from estrattore.invoice_registry_storage import save_invoice_registry_excel
        from estrattore.registry import load_registry_cache, save_registry_cache_excel

        if self._paths is None:
            raise RuntimeError("Dati dell'app non disponibili")
        if kind == "registry":
            cache = load_registry_cache(self._paths.registry_cache_file)
            source = self._paths.registry_export_xlsx_file
            save_registry_cache_excel(cache, source)
            name = "anagrafica_estrattore_scan.xlsx"
        elif kind == "cleaning_report":
            source = self._paths.registry_duplicate_report_xlsx_file
            name = "report_pulizia_anagrafica.xlsx"
        elif kind == "invoice_registry":
            source = save_invoice_registry_excel(runtime_paths=self._paths)
            name = "registro_fatture.xlsx"
        else:
            raise ValueError("Esportazione non valida")
        if not source.is_file():
            raise FileNotFoundError("File da esportare non disponibile")
        filename = self._save_bytes(source.read_bytes(), name)
        return {"ok": filename is not None, "cancelled": filename is None, "file_name": filename}

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
def _single_instance_lock(path: Path, mutex_name: str = APP_MUTEX_NAME) -> Iterator[None]:
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
            mutex = kernel32.CreateMutexW(None, False, mutex_name)
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
        preview_api = DesktopPreviewApi(webview, server.server_port, paths.archive_dir, paths)
        window_title = (
            f"{WINDOW_TITLE} [Demo]"
            if (paths.app_data_dir / ".ui-demo-v1.json").is_file()
            else WINDOW_TITLE
        )
        window = webview.create_window(
            window_title,
            f"http://{HOST}:{server.server_port}/?desktop=1",
            width=1500,
            height=940,
            maximized=True,
            min_size=(1000, 700),
            background_color="#edf2ef",
            text_select=True,
            js_api=preview_api,
        )
        preview_api.bind_window(window)

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
