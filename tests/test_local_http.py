from __future__ import annotations

import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from scripts.local_http import LocalHTTPServer, LocalRequestHandler
from scripts.desktop_lifecycle import DesktopCloseCoordinator
from types import SimpleNamespace


class Handler(LocalRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_POST = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET


@pytest.fixture
def server():
    instance = LocalHTTPServer(("127.0.0.1", 0), Handler, require_session=True)
    thread = threading.Thread(target=instance.serve_forever)
    thread.start()
    yield instance, f"http://127.0.0.1:{instance.server_port}"
    instance.shutdown()
    instance.server_close()
    thread.join(5)


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "OPTIONS"])
def test_foreign_origin_and_forged_host_are_rejected(server, method):
    _, base = server
    for headers in (
        {"Origin": "https://untrusted.example"},
        {"Host": "attacker.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/", method=method, headers=headers), timeout=5)
        assert error.value.code == 403


def test_session_required_and_renewed_on_each_launch(server):
    instance, base = server
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    with pytest.raises(HTTPError) as error:
        client.open(base + "/private", timeout=5)
    assert error.value.code == 403
    with client.open(base + "/?desktop=1", timeout=5) as response:
        assert "HttpOnly; SameSite=Strict" in response.headers["Set-Cookie"]
    with client.open(Request(base + "/private", method="DELETE", headers={"Origin": base}), timeout=5) as response:
        assert response.status == 200
    instance.session_token = "new-session"
    with pytest.raises(HTTPError) as error:
        client.open(base + "/private", timeout=5)
    assert error.value.code == 403


def test_real_routes_protect_deletion_and_serve_owned_pdf(server, monkeypatch, tmp_path):
    import scripts.local_app as local
    import scripts.registry_api as api
    from estrattore.runtime_paths import get_runtime_paths

    instance, base = server
    instance.RequestHandlerClass = local.LocalAppHandler
    paths = get_runtime_paths(tmp_path / "data")
    paths.ensure_directories()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    pdf = paths.archive_dir / "invoice.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    deleted = []
    monkeypatch.setattr(local, "RUNTIME_PATHS", paths)
    monkeypatch.setattr(local, "VIEWER_DIST_PATH", dist)
    monkeypatch.setattr(api, "ensure_registry_runtime_ready", lambda: None)
    monkeypatch.setattr(api, "_delete_registry", lambda: deleted.append(True))
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    with client.open(base + "/", timeout=5):
        pass
    with pytest.raises(HTTPError) as error:
        client.open(Request(base + "/registry", method="DELETE", headers={"Origin": "https://untrusted.example"}))
    assert error.value.code == 403
    assert deleted == []
    with client.open(base + "/runtime/archive/invoice.pdf", timeout=5) as response:
        assert response.headers["Content-Type"] == "application/pdf"
        assert response.read() == b"%PDF-fixture"
    with client.open(Request(base + "/registry", method="DELETE", headers={"Origin": base}), timeout=5) as response:
        assert response.status == 200
    assert deleted == [True]


def test_drain_keeps_accepted_request_alive_and_rejects_new_one(server, monkeypatch):
    instance, base = server
    instance.require_session = False
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def post(handler):
        entered.set()
        release.wait(5)
        Handler.do_GET(handler)
        finished.set()

    monkeypatch.setattr(Handler, "do_POST", post)
    def request():
        try:
            with urlopen(Request(base + "/write", method="POST"), timeout=5) as response:
                assert response.status == 200
        except Exception as error:
            errors.append(error)
    client = threading.Thread(target=request)
    client.start()
    try:
        assert entered.wait(5)
        assert not instance.daemon_threads
        assert not instance.drain_mutations(timeout=0)
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/write", method="POST"), timeout=5)
        assert error.value.code == 503
    finally:
        release.set()
        client.join(5)
    assert finished.wait(5)
    assert instance.drain_mutations(timeout=5)
    instance.resume_mutations()
    assert not errors


@pytest.mark.parametrize("result,confirm,drained,expected", [
    ({"ok": True}, True, True, True),
    ({"ok": False, "message": "failed"}, True, True, False),
    ({"ok": True, "unsavedEditor": True}, False, True, False),
    ({"ok": True}, True, False, False),
])
def test_close_requires_frontend_and_backend_acknowledgement(result, confirm, drained, expected):
    destroyed, resumed, errors = [], [], []
    window = SimpleNamespace(
        evaluate_js=lambda script, callback=None: callback(result) if callback else None,
        create_confirmation_dialog=lambda *args: confirm,
        destroy=lambda: destroyed.append(True),
    )
    http = SimpleNamespace(drain_mutations=lambda **kw: drained, resume_mutations=lambda: resumed.append(True))
    closer = DesktopCloseCoordinator(window, http, has_ocr=lambda: False,
        stop_ocr=lambda **kw: True, resume_ocr=lambda: None,
        close_previews=lambda: None, show_error=errors.append)
    closer._lock.acquire()
    closer._attempt_close()
    assert closer.allowed == expected
    assert bool(destroyed) == expected
    assert bool(resumed) != expected
