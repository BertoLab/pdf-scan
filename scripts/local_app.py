from __future__ import annotations

import mimetypes
import sys
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.registry_api import HOST, PORT, RegistryApiHandler


VIEWER_DIST_PATH = Path("apps") / "viewer" / "dist"


class LocalAppHandler(RegistryApiHandler):
    def do_GET(self) -> None:
        request_path = urlparse(self.path).path
        if request_path in {
            "/health",
            "/registry/status",
            "/registry/file",
            "/registry/records",
        }:
            super().do_GET()
            return

        self._send_static_viewer_file(request_path)

    def _send_static_viewer_file(self, request_path: str) -> None:
        static_path = _resolve_static_path(request_path)
        if static_path is None:
            self._send_json_response(
                500,
                {
                    "ok": False,
                    "error": "Build viewer mancante. Esegui: cd apps/viewer; npm run build",
                },
            )
            return

        body = static_path.read_bytes()
        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _resolve_static_path(request_path: str) -> Path | None:
    if not VIEWER_DIST_PATH.exists():
        return None

    relative_path = unquote(request_path).lstrip("/")
    candidate = VIEWER_DIST_PATH / relative_path if relative_path else VIEWER_DIST_PATH / "index.html"
    if candidate.is_file() and _is_inside_viewer_dist(candidate):
        return candidate

    index_path = VIEWER_DIST_PATH / "index.html"
    if index_path.is_file():
        return index_path

    return None


def _is_inside_viewer_dist(path: Path) -> bool:
    resolved_dist = VIEWER_DIST_PATH.resolve()
    resolved_path = path.resolve()
    return resolved_path == resolved_dist or resolved_dist in resolved_path.parents


def serve_local_app(open_browser: bool = True, port: int = PORT) -> None:
    server = ThreadingHTTPServer((HOST, port), LocalAppHandler)
    app_url = f"http://{HOST}:{port}"
    print(f"App locale attiva su {app_url}")
    print("Backend anagrafica incluso nello stesso processo")
    if open_browser:
        webbrowser.open(app_url)
    server.serve_forever()


def main() -> None:
    serve_local_app()


if __name__ == "__main__":
    main()
