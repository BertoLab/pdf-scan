from __future__ import annotations

import mimetypes
import sys
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.registry_api import (
    HOST,
    PORT,
    RUNTIME_PATHS,
    RegistryApiHandler,
    ensure_registry_runtime_ready,
)


VIEWER_DIST_PATH = PROJECT_ROOT / "apps" / "viewer" / "dist"


class LocalAppHandler(RegistryApiHandler):
    def do_GET(self) -> None:
        request_path = urlparse(self.path).path
        if request_path.startswith("/processing/jobs/"):
            super().do_GET()
            return

        if request_path in {
            "/health",
            "/runtime/paths",
            "/batches",
            "/processing/jobs",
            "/viewer/review-batches",
            "/invoices/registry",
            "/invoices/export.xlsx",
            "/registry/status",
            "/registry/file",
            "/registry/cache.json",
            "/registry/export.xlsx",
            "/registry/records",
            "/registry/cleaning-report.xlsx",
        }:
            super().do_GET()
            return

        if request_path.startswith("/runtime/manifests/"):
            self._send_runtime_manifest_file(request_path)
            return

        if request_path.startswith("/runtime/archive/"):
            self._send_runtime_archive_file(request_path)
            return

        self._send_static_viewer_file(request_path)

    def _send_runtime_manifest_file(self, request_path: str) -> None:
        relative_path = unquote(request_path).removeprefix("/runtime/manifests/").lstrip("/")
        candidate = RUNTIME_PATHS.manifests_dir / relative_path
        if not _is_inside_runtime_manifests(candidate) or not candidate.is_file():
            self._send_json_response(404, {"ok": False, "error": "File runtime non trovato"})
            return

        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_runtime_archive_file(self, request_path: str) -> None:
        relative_path = unquote(request_path).removeprefix("/runtime/archive/").lstrip("/")
        candidate = RUNTIME_PATHS.archive_dir / relative_path
        if not _is_inside_runtime_archive(candidate) or not candidate.is_file():
            self._send_json_response(404, {"ok": False, "error": "File archivio non trovato"})
            return

        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def _is_inside_runtime_manifests(path: Path) -> bool:
    resolved_root = RUNTIME_PATHS.manifests_dir.resolve()
    resolved_path = path.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _is_inside_runtime_archive(path: Path) -> bool:
    resolved_root = RUNTIME_PATHS.archive_dir.resolve()
    resolved_path = path.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def serve_local_app(open_browser: bool = True, port: int = PORT) -> None:
    ensure_registry_runtime_ready()
    server = ThreadingHTTPServer((HOST, port), LocalAppHandler)
    app_url = f"http://{HOST}:{port}"
    _append_startup_log(app_url)
    print(f"App locale attiva su {app_url}")
    print("Backend anagrafica incluso nello stesso processo")
    print(f"Dati app: {RUNTIME_PATHS.app_data_dir}")
    print(f"Log app: {RUNTIME_PATHS.logs_dir}")
    if open_browser:
        webbrowser.open(app_url)
    server.serve_forever()


def _append_startup_log(app_url: str) -> None:
    RUNTIME_PATHS.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = RUNTIME_PATHS.logs_dir / "app.log"
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    log_path.open("a", encoding="utf-8").write(
        "\n".join(
            [
                f"[{timestamp}] App locale avviata",
                f"url={app_url}",
                f"app_data_dir={RUNTIME_PATHS.app_data_dir}",
                f"viewer_dist={VIEWER_DIST_PATH}",
                "",
            ]
        )
    )


def main() -> None:
    serve_local_app()


if __name__ == "__main__":
    main()
