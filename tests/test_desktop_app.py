from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

import scripts.desktop_app as desktop_app
import scripts.local_app as local_app
import scripts.registry_api as registry_api
from estrattore.runtime_paths import get_runtime_paths


def test_frozen_desktop_requires_private_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(desktop_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_app, "bundled_assets_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="Manca il motore"):
        desktop_app._private_webview_runtime()

    runtime = tmp_path / "vendor" / "webview2"
    runtime.mkdir(parents=True)
    (runtime / "msedgewebview2.exe").touch()
    assert desktop_app._private_webview_runtime() == runtime


def test_desktop_pdf_preview_accepts_only_own_archive(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    pdf = archive / "invoice with spaces.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")

    class Event:
        def __init__(self) -> None:
            self.callback = None

        def __iadd__(self, callback):
            self.callback = callback
            return self

    class Window:
        def __init__(self) -> None:
            self.events = SimpleNamespace(closed=Event())
            self.destroyed = False

        def destroy(self) -> None:
            self.destroyed = True
            self.events.closed.callback()

    created: list[tuple[str, str, Window]] = []

    def create_window(title: str, url: str, **_kwargs):
        window = Window()
        created.append((title, url, window))
        return window

    api = desktop_app.DesktopPreviewApi(SimpleNamespace(create_window=create_window), 8765, archive)
    valid_url = "http://127.0.0.1:8765/runtime/archive/invoice%20with%20spaces.pdf"
    assert api.open_pdf(valid_url, "PDF fattura") is True
    assert created[0][:2] == ("PDF fattura", valid_url)

    for url in (
        "https://example.com/runtime/archive/invoice%20with%20spaces.pdf",
        "http://127.0.0.1:8766/runtime/archive/invoice%20with%20spaces.pdf",
        "http://127.0.0.1:8765/runtime/archive/../outside.pdf",
        "http://127.0.0.1:8765/runtime/archive/missing.pdf",
        "http://127.0.0.1:8765/runtime/archive/invoice%20with%20spaces.pdf?download=1",
    ):
        with pytest.raises(ValueError):
            api.open_pdf(url, "PDF fattura")

    api._close_all()
    assert created[0][2].destroyed is True
    with pytest.raises(RuntimeError, match="chiudendo"):
        api.open_pdf(valid_url, "PDF fattura")


def test_desktop_lock_rejects_second_instance(tmp_path: Path) -> None:
    lock_path = tmp_path / "desktop.lock"
    with desktop_app._single_instance_lock(lock_path):
        kernel32 = desktop_app.ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.argtypes = (desktop_app.ctypes.c_uint32, desktop_app.ctypes.c_bool, desktop_app.ctypes.c_wchar_p)
        kernel32.OpenMutexW.restype = desktop_app.ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (desktop_app.ctypes.c_void_p,)
        kernel32.CloseHandle.restype = desktop_app.ctypes.c_bool
        mutex = kernel32.OpenMutexW(0x00100000, False, desktop_app.APP_MUTEX_NAME)
        assert mutex
        kernel32.CloseHandle(mutex)
        with pytest.raises(desktop_app.AlreadyRunningError):
            with desktop_app._single_instance_lock(lock_path):
                pass
    with desktop_app._single_instance_lock(lock_path):
        pass


def test_worker_mode_dispatches_before_desktop_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import estrattore.document_worker as worker

    monkeypatch.setattr(desktop_app.sys, "argv", ["app.exe", "--document-worker", "--request", "x"])
    monkeypatch.setattr(worker, "main", lambda: 7)
    assert desktop_app.main() == 7
    assert desktop_app.sys.argv == ["app.exe", "--request", "x"]


def test_local_server_serves_real_static_and_health_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = get_runtime_paths(tmp_path / "app-data")
    monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
    monkeypatch.setattr(registry_api, "_REGISTRY_RUNTIME_READY", False)
    monkeypatch.setattr(registry_api, "PROCESSING_SHUTDOWN_REQUESTED", False)
    monkeypatch.setattr(local_app, "RUNTIME_PATHS", paths)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>desktop test</html>", encoding="utf-8")
    monkeypatch.setattr(local_app, "VIEWER_DIST_PATH", dist)
    server = local_app.create_local_app_server(0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/", timeout=5) as response:
            assert b"desktop test" in response.read()
        with urlopen(base + "/health", timeout=5) as response:
            assert json.load(response) == {"ok": True}
        with urlopen(base + "/processing/jobs", timeout=5) as response:
            assert json.load(response)["jobs"] == []
        assert (paths.logs_dir / "app.log").is_file()
    finally:
        registry_api.shutdown_processing_jobs(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shutdown_cancels_active_and_preserves_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = get_runtime_paths(tmp_path / "app-data")
    monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
    monkeypatch.setattr(registry_api, "PROCESSING_JOBS", {
        "active": {"job_id": "active", "status": "processing", "cancel_requested": False},
        "queued": {"job_id": "queued", "status": "queued", "cancel_requested": False},
    })
    monkeypatch.setattr(registry_api, "PROCESSING_JOB_QUEUE", ["queued"])
    monkeypatch.setattr(registry_api, "PROCESSING_ACTIVE_JOB_ID", "active")
    monkeypatch.setattr(registry_api, "PROCESSING_SHUTDOWN_REQUESTED", False)
    monkeypatch.setattr(registry_api, "PROCESSING_JOB_IDLE", threading.Event())

    assert registry_api.shutdown_processing_jobs(timeout=0) is False
    assert registry_api.PROCESSING_JOBS["active"]["cancel_requested"] is True
    assert registry_api.PROCESSING_JOBS["active"]["status"] == "cancelling"
    assert registry_api.PROCESSING_JOBS["queued"]["status"] == "queued"
    assert registry_api.PROCESSING_JOB_QUEUE == ["queued"]
    assert registry_api.PROCESSING_SHUTDOWN_REQUESTED is True


def test_desktop_uses_real_server_with_window_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = get_runtime_paths(tmp_path / "app-data")
    monkeypatch.setattr(registry_api, "RUNTIME_PATHS", paths)
    monkeypatch.setattr(registry_api, "_REGISTRY_RUNTIME_READY", False)
    monkeypatch.setattr(registry_api, "PROCESSING_SHUTDOWN_REQUESTED", False)
    monkeypatch.setattr(local_app, "RUNTIME_PATHS", paths)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>window test</html>", encoding="utf-8")
    monkeypatch.setattr(local_app, "VIEWER_DIST_PATH", dist)
    monkeypatch.setattr(desktop_app, "_private_webview_runtime", lambda: None)

    class ClosingEvent:
        def __init__(self) -> None:
            self.callback = None

        def __iadd__(self, callback):
            self.callback = callback
            return self

        def __call__(self):
            assert self.callback is not None
            return self.callback()

    window = SimpleNamespace(events=SimpleNamespace(closing=ClosingEvent(), loaded=ClosingEvent()))
    settings: dict[str, object] = {}

    def create_window(_title: str, url: str, **_kwargs: object) -> SimpleNamespace:
        window.url = url
        assert isinstance(_kwargs["js_api"], desktop_app.DesktopPreviewApi)
        return window

    def start(**kwargs: object) -> None:
        with urlopen(window.url, timeout=5) as response:
            assert b"window test" in response.read()
        assert kwargs["storage_path"] == str(paths.config_dir / "webview-profile")
        window.events.loaded()
        closed = threading.Event()
        window.evaluate_js = lambda script, callback=None: callback({"ok": True}) if callback else None
        def destroy():
            assert window.events.closing() is None
            closed.set()
        window.destroy = destroy
        assert window.events.closing() is False
        assert closed.wait(5)

    fake_webview = SimpleNamespace(settings=settings, create_window=create_window, start=start)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    desktop_app._run_window(paths)
    assert settings["ALLOW_DOWNLOADS"] is True
