from __future__ import annotations

import os
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import webview
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from estrattore.runtime_paths import DATA_DIR_ENV_VAR, get_runtime_paths
from scripts import desktop_app


BUILD_DIR = PROJECT_ROOT / "build"


def main() -> None:
    if not (PROJECT_ROOT / "apps/viewer/dist/index.html").is_file():
        raise SystemExit("Build the viewer before running the window smoke test")

    loaded = threading.Event()
    failures: list[str] = []
    original_start = webview.start
    private_runtime = BUILD_DIR / "desktop-assets" / "vendor" / "webview2"
    if not (private_runtime / "msedgewebview2.exe").is_file():
        raise SystemExit("Run packaging/prepare_build.py before the window smoke test")
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(private_runtime)

    def start_and_check(**kwargs: object) -> None:
        window = webview.windows[0]
        window.events.loaded += lambda: loaded.set()

        def close_after_load() -> None:
            if not loaded.wait(timeout=15):
                failures.append("The desktop window did not load its frontend")
            else:
                url = f"http://127.0.0.1:{urlsplit(window.real_url).port}/runtime/archive/preview.pdf"
                preview_opened = threading.Event()
                preview_result: list[object] = []

                def on_preview_result(result: object) -> None:
                    preview_result.append(result)
                    preview_opened.set()

                window.evaluate_js(
                    f"window.pywebview.api.open_pdf({json.dumps(url)}, 'PDF fattura')",
                    callback=on_preview_result,
                )
                if not preview_opened.wait(timeout=15) or preview_result != [True]:
                    failures.append(f"Desktop PDF bridge failed: {preview_result}")
                elif len(webview.windows) != 2:
                    failures.append("Desktop PDF preview did not create a child window")
                elif not webview.windows[1].events.loaded.wait(timeout=15):
                    failures.append("Desktop PDF preview did not finish loading")
                else:
                    time.sleep(0.5)
            window.destroy()

        original_start(func=close_after_load, **kwargs)

    webview.start = start_and_check
    temporary = tempfile.TemporaryDirectory(prefix="desktop smoke ", dir=BUILD_DIR)
    try:
        test_root = Path(temporary.name).resolve()
        if BUILD_DIR.resolve() not in test_root.parents:
            raise RuntimeError("Smoke data directory is outside the project build directory")
        os.environ[DATA_DIR_ENV_VAR] = str(test_root / "app-data")
        paths = get_runtime_paths()
        paths.archive_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 200), "white").save(paths.archive_dir / "preview.pdf", "PDF")
        desktop_app._run_window(paths)
    finally:
        for attempt in range(20):
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.5)
    if failures:
        raise RuntimeError("; ".join(failures))
    print("Desktop window loaded, opened an owned PDF preview and closed cleanly")


if __name__ == "__main__":
    main()
