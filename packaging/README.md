# Packaging build preparation

Run on 64-bit Windows with Python 3.12 and Node.js/npm:

```powershell
py -3.12 packaging/prepare_build.py
```

The command creates `build/packaging-venv` and
`build/desktop-assets/apps/viewer/dist`. It rebuilds a clean frontend copy in
`build/frontend-work`, using `npm ci` and the committed lockfile. It does not
change the development `.venv` or the viewer's `node_modules`.

Native engines can be staged and tested independently:

```powershell
py -3.12 packaging/prepare_native.py
.\build\packaging-venv\Scripts\python.exe packaging/smoke_native.py
```

The full preparation command also runs `prepare_native.py`. It downloads the
pinned Poppler 26.09.0-0 Windows archive, verifies its SHA-256, and stages the
PDF tools, DLLs, data and included notices under
`build/desktop-assets/vendor/poppler`. The cached archive is reused only if its
hash matches. The local `vendor/tesseract` source must match the pinned
Tesseract 5.4.0.20240606 runtime fingerprint. Only `tesseract.exe`, DLLs,
Italian/English/orientation models, Italian best model and license/author files
are staged under `build/desktop-assets/vendor/tesseract`.

The `onedir` bundle places `desktop-assets` contents at the PyInstaller bundle
root (`sys._MEIPASS`). Release-mode PDF/OCR resolution fails if the private
tools are missing; it never searches PATH or Program Files. Source mode keeps
existing system-tool fallback for development.

The preparation command also downloads Microsoft's x64 Fixed Version WebView2
153.0.4234.48 CAB into ignored `build/downloads`, checks its pinned size and
SHA-256 plus Microsoft Authenticode signature, and stages the signed engine
under `build/desktop-assets/vendor/webview2`. The frozen launcher fails if this
private engine is absent. The fixed engine must be updated deliberately with
application releases; it is not automatically updated.

The desktop launcher exists and can be exercised from source after the build:

```powershell
.\build\packaging-venv\Scripts\python.exe scripts\desktop_app.py
```

It owns a local server, app window, process lock and OCR shutdown. The source
smoke check `packaging/smoke_desktop_window.py` uses temporary data and the
staged private WebView2 runtime, opens a PDF through the real JavaScript bridge,
and closes both windows automatically. Ordinary source mode may use installed
WebView2.

Build and test the frozen directory application:

```powershell
py -3.12 packaging/build_desktop.py
.\build\packaging-venv\Scripts\python.exe packaging\smoke_frozen.py
```

The executable is `build/desktop-dist/EstrattoreScanFatture/EstrattoreScanFatture.exe`.
The Windows product icon is `packaging/assets/Scan_icona_trasparente.ico`.
PyInstaller embeds it in the executable; Inno Setup uses the same source for
the installer. Desktop and Start shortcuts inherit the executable icon.
The frozen smoke copies the entire bundle to a temporary path with spaces,
processes a PDF both directly and through the live HTTP/OCR queue, checks the
private WebView2 process, and deletes its isolated test data. For an iterative
developer rebuild after preparation, pass `--skip-preparation`; release builds
must use the full command.

Build the offline per-user installer from clean inputs:

```powershell
py -3.12 packaging/build_installer.py
```

The command rebuilds the frontend and `onedir` bundle, verifies the pinned and
signed Inno Setup 7 compiler in ignored `build/`, and produces
`build/installer-dist/EstrattoreScanFatture-Setup-<version>-rc.<revision>.exe`
for a release candidate. The version comes from `[project]` and the release
stage/revision from `[tool.estrattore.desktop_release]` in `pyproject.toml`.
Use `stage = "stable"` with no revision for the final `<version>.exe` name.
The bundle hash remains in the installed release directory and marker, not in
the user-facing filename. A non-smoke installer name is immutable: increment
the RC revision before building a new candidate; an existing filename is never
overwritten. GitHub assets must be renamed explicitly when publishing; local
renames do not change already published releases.
`--use-existing-bundle` skips the clean rebuild for local iteration only.
`--smoke` builds a separate AppId with isolated code and data under
`build/installer-smoke/app` and `build/installer-smoke/data`. Its generated
shortcuts explicitly select the isolated data root and setup rejects a different
installation directory (including a stale previous smoke destination);
it is never the release installer. The compiler is a build tool, not a product
dependency. The installer includes Python, OCR/PDF engines, React assets and a
private WebView2 runtime. No external runtime installer is launched.

The setup installs code under `%LOCALAPPDATA%/Programs/EstrattoreScanFatture`
and stores business data separately under `%LOCALAPPDATA%/EstrattoreScanFatture`.
It validates the installed executable, frontend, PDF/OCR engines, WebView2 and
completion marker, then applies WebView2 AppContainer read/execute ACLs. After
successful validation, updates remove all previous code releases immediately;
they do not wait for the app's first launch. If validation or deletion fails,
Setup returns an error and retains the old code, but does not automatically
restore the old shortcut or registry entry.
The default uninstall preserves business data. Interactive uninstall asks before
full removal; silent uninstall preserves data unless explicitly passed
`/REMOVEAPPDATA`. This switch only deletes the application's internal data
root, never selected source files or exports outside it.

An installed-bundle smoke can be run against an isolated test installation:

```powershell
.\build\packaging-venv\Scripts\python.exe packaging\smoke_frozen.py --installed-dir build\installer-smoke\app\releases\<release-id>
```

Additional isolated regression checks:

```powershell
.\.venv\Scripts\python.exe packaging\smoke_installer.py
node tools/ui-checks/checks/check-desktop-close.mjs
```

The installer-policy check compiles tiny non-executable fixtures with the real
Inno script and tests A -> B -> B -> C, generated shortcut arguments and explicit
uninstall. It does not certify runtime payloads. The desktop-close check uses the
actual frozen executable, a generated PDF, real OCR, delayed draft HTTP traffic,
native close and reopen; screenshots/data are temporary. `--keep` retains only
the screenshot for immediate inspection and manual removal afterwards.

Desktop HTTP access uses a per-run HttpOnly/SameSite session established on the
trusted landing page. Programmatic smoke clients must use a cookie jar and open
`/` before API calls. Foreign origins/Host headers are rejected before dispatch.
The standalone development API explicitly allows localhost Vite on port 5173;
the packaged desktop does not enable that development exception.

The installer is not yet a clean-machine release. Before delivery, test native
file dialogs, PDF preview and export Save dialogs interactively on clean Windows
10 and 11 PCs without developer tools, OCR tools, shared WebView2 or network.
Review third-party redistribution notices, Inno Setup commercial licensing and
Windows code signing. The current installer is unsigned.
