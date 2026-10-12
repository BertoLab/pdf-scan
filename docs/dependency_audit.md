# Dependency and code hygiene audit

Date: 2026-10-08. Scope: current source tree and developer environment, before
desktop packaging. This is a static/runtime-environment audit, not proof that a
frozen application works on a clean Windows installation.

## Runtime dependency map

| Area | Needed at runtime | Current evidence | Release disposition |
| --- | --- | --- | --- |
| Python app | Python, openpyxl, Pillow, pdf2image, pypdf, pytesseract | `pyproject.toml`; imports under `src/estrattore` | Bundle from a clean, locked build environment |
| Local UI | React/React DOM build output | `apps/viewer/package.json`, `scripts/local_app.py` | Bundle only built assets; Node/Vite are build tools |
| File picker | Python tkinter plus Tcl/Tk resources | `scripts/registry_api.py:_pick_windows_folder/_pick_windows_pdf_files` | Verify Tcl/Tk resources in frozen app and actual dialogs |
| PDF rasterization | pdfinfo and pdftoppm with native dependencies | `src/estrattore/io.py:load_scan` calls pdf2image without `poppler_path`; current PATH resolves to MiKTeX | Bundle a vetted Poppler runtime and pass its path explicitly |
| OCR | Tesseract, DLLs, `ita`/other enabled models | `src/estrattore/tesseract_adapter.py`; local vendor runtime answers `--version` and `--list-langs` | Bundle minimal runtime and models from reproducible inputs |
| Desktop window | host library plus browser engine | No desktop entrypoint or host dependency currently declared | Select and bundle a private engine; test host prerequisites |
| Persistence | SQLite, JSON, files in AppData | `src/estrattore/runtime_paths.py`, `feedback_collection.py` | Standard Python SQLite; retain data outside install directory |

The five Python project dependencies are all used. `pytesseract` is presently
declared as an optional `ocr` extra, but OCR is a required desktop workflow.
`pypdf` is imported inside PDF archive code, so a packaging test must exercise
invoice saving, not only opening the app. `tkinter` is a standard-library module
with non-Python data files; inspect the frozen tree and run the dialogs.

## Findings ordered by packaging impact

1. **P0 - PDF conversion relies on MiKTeX on this PC.**
   `io.py:13` gives pdf2image no Poppler location. `pdfinfo.exe` and
   `pdftoppm.exe` currently resolve under the user's MiKTeX install. A clean
   machine will fail before OCR unless the app includes and selects its own
   converter. Build acceptance: run a real PDF with PATH stripped of MiKTeX.

2. **P0 - A desktop distribution does not yet exist.**
   `scripts/run_app.py` invokes npm as needed and `scripts/local_app.py`
   opens a browser and serves forever. PyInstaller/pywebview are not installed
   here. A frozen worker command is anticipated by `document_supervisor.py`,
   but no launcher dispatches `--document-worker`. The private window engine,
   lifetime and installer remain implementation work.

3. **P1 - OCR inputs are not reproducible from the repository.**
   `.gitignore` excludes `vendor/tesseract/*` except README. The local
   Tesseract 5.4.0 runtime and three fast models work here, but a checkout has
   only the README. The directory is about 261 MB and includes training tools,
   documentation and Java JARs that are not part of normal OCR. Define a
   versioned download/source, checksum, license notices and a minimal allowlist.
   `resolve_tesseract_cmd` must reject system/PATH fallback in frozen mode.

4. **P1 - Python release inputs are not locked or isolated.**
   `pyproject.toml` uses lower bounds; no Python lock/constraints file exists.
   This venv contains previous experiment packages such as OpenCV, pandas and
   ModelScope that are absent from current application imports. `pip check`
   reports no broken requirements, but that does not make the environment a
   reproducible packaging source. Create a clean packaging environment and pin
   its resolved versions, including the desktop host and bundler. Frontend has a
   committed lockfile; use `npm ci` for release builds.

5. **P1 - Feedback provenance loses its code identity when frozen.**
   `feedback_collection.py:_code_fingerprint` reads `src/estrattore/*.py`
   plus `scripts/debug_words.py` from `project_root()`. A frozen installation
   has no such source layout, so the digest can become the same empty hash across
   distinct releases. `ESTRATTORE_SCAN_BUILD_ID` also defaults to
   `development`. Generate a version/build fingerprint during packaging and
   test it in an actual extraction record from the EXE.

6. **P1 - Static resource resolution needs one explicit bundle layout.**
   `local_app.py` looks for `apps/viewer/dist` relative to its module.
   `runtime_paths.project_root()` instead uses the executable parent in frozen
   mode. An asset directory placed under the wrong PyInstaller location will
   make the UI unavailable while the backend still starts. Decide and test one
   asset root; startup should fail clearly if `index.html` or its JS/CSS is
   missing. The current static resolver serves `index.html` for missing JS/CSS
   requests, which hides missing assets behind a later browser error.

7. **P2 - Development sample fallback is still in the production UI path.**
   `App.tsx` requests `/sample/viewer_batches.json` when no review is found
   or the review request fails. The sample directory is currently empty, so
   this falls through to the SPA page and JSON parsing fails. A future release
   must show the correct empty/error state without shipping sample invoices.
   The frontend build must explicitly exclude any later sample outputs.

8. **P2 - Desktop PDF/link behavior and dialogs are unverified.**
   The frontend uses `window.open` and `target="_blank"` for PDF views, while
   server endpoints invoke tkinter file dialogs. These workflows cross the
   chosen window host; test that child PDF views and pickers stay usable and
   that downloads land where the user expects.

9. **P2 - Working-tree history includes generated artifacts.**
   Of 572 tracked paths, 460 are under temporary/test output prefixes.
   Many are currently deleted in the worktree. They are not app dependencies
   and must be excluded from release inputs using an allowlist. Do not reset or
   bulk-delete them as part of packaging; clean repository history separately
   after reviewing ownership.

10. **P3 - Minor starter assets and labels remain.**
    `apps/viewer/src/assets/react.svg`, `vite.svg` and `hero.png` have no
    references in the active frontend. `index.html` still says `lang="en"`
    and `<title>viewer</title>`. These can be tidied without affecting the
    packaging dependency graph. `scripts/debug_words.py` is **not** dead code:
    both worker and review reprocessing import `debug_words_file`.

## Release gate and cleanup sequence

1. Choose and lock a clean packaging environment; create a machine-readable
   component inventory with versions, hashes and redistribution notices.
2. Make bundled asset, Poppler and Tesseract locations explicit. Keep source-mode
   convenience discovery separate from frozen-mode checks.
3. Add the frozen launcher/worker dispatch and private window engine. Include
   Tcl/Tk resources and a build-generated extraction fingerprint.
4. Inspect the produced directory against an allowlist: no source invoices,
   debug outputs, training tools, screenshots, temporary files or unrelated
   packages. Verify real PDF import, OCR, save, export, feedback and dialogs.
5. Repeat from a standard-user Windows 10/11 environment without developer
   tools or network; then verify upgrade and uninstall filesystem/process state.

Audit checks: read import and subprocess call paths, inspected local executable
resolution and Tesseract languages, compared project declarations with installed
packages, checked npm lockfile, counted tracked artifacts and ran `pip check`.
No app code, user data, package environment or temporary files were modified.
The host transitive dependencies and actual frozen behavior remain unverified.
