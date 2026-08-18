from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DATA_DIR_ENV_VAR = "ESTRATTORE_SCAN_DATA_DIR"
VIEWER_DIST_PATH = PROJECT_ROOT / "apps" / "viewer" / "dist"
VIEWER_SOURCE_PATH = PROJECT_ROOT / "apps" / "viewer" / "src"
VIEWER_PUBLIC_PATH = PROJECT_ROOT / "apps" / "viewer" / "public"
VIEWER_PACKAGE_PATH = PROJECT_ROOT / "apps" / "viewer" / "package.json"
VIEWER_PACKAGE_LOCK_PATH = PROJECT_ROOT / "apps" / "viewer" / "package-lock.json"


def main() -> None:
    args = _parse_args()
    if args.data_dir:
        os.environ[DATA_DIR_ENV_VAR] = str(args.data_dir.expanduser().resolve())

    if args.rebuild or _viewer_build_is_missing_or_stale():
        _build_viewer()
    else:
        print("Build viewer aggiornata")

    from scripts.local_app import serve_local_app

    port = _find_available_port(args.port)
    if port != args.port:
        print(f"Porta {args.port} occupata, uso http://{DEFAULT_HOST}:{port}")
    serve_local_app(open_browser=not args.no_browser, port=port)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avvia l'app locale completa")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rigenera sempre la build React prima di avviare l'app",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Non aprire automaticamente il browser",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help=(
            "Cartella dati persistente da usare al posto di LOCALAPPDATA. "
            "Utile per test, beta portabile e diagnostica."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Porta locale preferita. Default: {DEFAULT_PORT}",
    )
    return parser.parse_args()


def _viewer_build_is_missing_or_stale() -> bool:
    index_path = VIEWER_DIST_PATH / "index.html"
    if not index_path.exists():
        return True

    build_mtime = index_path.stat().st_mtime
    watched_paths = [
        VIEWER_SOURCE_PATH,
        VIEWER_PUBLIC_PATH,
        VIEWER_PACKAGE_PATH,
        VIEWER_PACKAGE_LOCK_PATH,
    ]
    newest_source_mtime = max(_newest_mtime(path) for path in watched_paths if path.exists())
    return newest_source_mtime > build_mtime


def _newest_mtime(path: Path) -> float:
    if path.is_file():
        return path.stat().st_mtime

    newest = path.stat().st_mtime
    for child in path.rglob("*"):
        if child.is_file():
            newest = max(newest, child.stat().st_mtime)
    return newest


def _build_viewer() -> None:
    print("Genero build viewer...")
    npm_command = _resolve_npm_command()
    result = subprocess.run(
        [npm_command, "--prefix", "apps/viewer", "run", "build"],
        check=False,
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Build viewer fallita. Correggi l'errore npm prima di avviare l'app."
    )


def _find_available_port(preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 20):
        if _port_is_available(port):
            return port

    raise SystemExit(
        f"Nessuna porta libera trovata tra {preferred_port} e {preferred_port + 19}."
    )


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((DEFAULT_HOST, port)) != 0


def _resolve_npm_command() -> str:
    for command in ("npm.cmd", "npm"):
        resolved = shutil.which(command)
        if resolved:
            return resolved

    raise SystemExit(
        "npm non trovato nel PATH. Installa Node.js oppure apri un terminale con npm disponibile."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nApp locale chiusa")
        sys.exit(0)
