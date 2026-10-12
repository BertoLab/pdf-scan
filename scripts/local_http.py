"""Local HTTP trust boundary and accepted-mutation lifetime."""
from __future__ import annotations

import json
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class LocalHTTPServer(ThreadingHTTPServer):
    # An accepted write must survive the window loop returning unexpectedly too.
    daemon_threads = False

    def __init__(self, address, handler, *, require_session=False, dev_origins=()):
        self.require_session = require_session
        self.dev_origins = frozenset(dev_origins)
        self.session_token = secrets.token_urlsafe(32)
        self._condition = threading.Condition()
        self._mutations = 0
        self._draining = False
        super().__init__(address, handler)
        self.cookie_name = f"estrattore_{self.server_port}"

    def admit_mutation(self) -> bool:
        with self._condition:
            if self._draining:
                return False
            self._mutations += 1
            return True

    def finish_mutation(self) -> None:
        with self._condition:
            self._mutations -= 1
            self._condition.notify_all()

    def drain_mutations(self, timeout: float = 30) -> bool:
        with self._condition:
            self._draining = True
            return self._condition.wait_for(lambda: self._mutations == 0, timeout)

    def resume_mutations(self) -> None:
        with self._condition:
            self._draining = False


class LocalRequestHandler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        self.request.settimeout(30)
        super().setup()

    def handle_one_request(self) -> None:
        self._mutation_admitted = False
        self._set_session_cookie = False
        try:
            super().handle_one_request()
        finally:
            if self._mutation_admitted:
                self.server.finish_mutation()

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        origins = {f"http://{entry}" for entry in hosts}
        origins.update(getattr(self.server, "dev_origins", ()))
        origin = self.headers.get("Origin")
        if host not in hosts or (origin is not None and origin not in origins):
            return self._reject(403, "Accesso all'app non autorizzato.")
        if origin is None and self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            return self._reject(403, "Accesso all'app non autorizzato.")
        if getattr(self.server, "require_session", False):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                return self._reject(403, "Sessione non valida. Riapri l'app.")
            value = cookie.get(self.server.cookie_name)
            authenticated = value is not None and secrets.compare_digest(
                value.value, self.server.session_token,
            )
            # Only the trusted app landing page can establish a session.
            bootstrap = self.command == "GET" and urlsplit(self.path).path == "/" and origin is None
            if not authenticated and not bootstrap:
                return self._reject(403, "Sessione non valida. Riapri l'app.")
            self._set_session_cookie = bootstrap
        if self.command in {"POST", "PUT", "PATCH", "DELETE"} and hasattr(self.server, "admit_mutation"):
            if not self.server.admit_mutation():
                return self._reject(503, "Chiusura in corso. Attendi o riprova.")
            self._mutation_admitted = True
        return True

    def end_headers(self) -> None:
        if getattr(self, "_set_session_cookie", False):
            self.send_header("Set-Cookie", (
                f"{self.server.cookie_name}={self.server.session_token}; "
                "HttpOnly; SameSite=Strict; Path=/"
            ))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        super().end_headers()

    def _reject(self, status: int, message: str) -> bool:
        self.close_connection = True
        body = json.dumps({"ok": False, "error": message}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            # parse_request already checked this origin before dispatch.
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
