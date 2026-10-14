# Version 0.1.0 installation notes

Release under test: `v0.1.0-rc.1` (published GitHub asset:
`EstrattoreScanFatture-Setup-0.1.0-0bf30b602f2c.exe`; local retained copy:
`EstrattoreScanFatture-Setup-0.1.0-rc.1.exe`).

Record observations against this published installer here. Fixes belong in a later build and release so test results remain attributable to the exact artifact used.

## 1. Redundant Start menu folder page

- **Status:** Fixed in source; needs verification in the next installer.
- **Observed:** Setup asks for a Start menu folder and displays `(Default)`, with a Browse button.
- **Expected:** The user should not be asked to choose a folder when the installer always creates a direct Start menu shortcut.
- **Cause:** `packaging/EstrattoreScanFatture.iss` does not disable the program-group page, while its `[Icons]` entry uses `{autoprograms}\{#ShortcutName}` instead of the selected `{group}`. Changing the field therefore cannot affect the shortcut location.
- **Impact:** Confusing, ineffective installation choice. It does not change the application files or business data location.
- **Fix:** Set `DisableProgramGroupPage=yes` and retain the direct Start menu shortcut. Verify that setup skips the page and the shortcut launches the installed application.
- **Verification so far:** The real Inno script compiles with this directive. The isolated install smoke could not complete while the installed desktop app was running (setup exited with code 1); rerun the full smoke and inspect the interactive wizard when the app can be closed safely.

## 2. Generic desktop shortcut icon

- **Status:** Fixed in source; needs visual verification in the next installer.
- **Observed:** The shortcut uses the default executable icon instead of an application icon.
- **Expected:** The desktop shortcut, Start menu entry, running window/taskbar, and installer should present a coherent application identity.
- **Cause:** The previous PyInstaller `EXE` had no `icon` setting, and the Inno Setup script had no setup icon.
- **Impact:** The installed app looks unfinished and is harder to identify among desktop shortcuts.
- **Fix:** Use the supplied multi-resolution `packaging/assets/Scan_icona_trasparente.ico` in PyInstaller and Inno Setup. Existing shortcuts and uninstall metadata inherit the executable icon. The ICO has sizes from 16 through 256 pixels; the rebuilt executable contains nine `RT_ICON` resources and one `RT_GROUP_ICON`.
- **Verification so far:** Full desktop bundle build passed. The isolated real-Inno installer policy test compiled and ran A -> B -> B -> C with the icon setting. Desktop, Start, taskbar and Programs & Features still need visual acceptance with the next installed candidate; Windows may temporarily cache the old icon.

## 3. Main window should open maximized

- **Status:** Fixed in source; needs verification in the next installer.
- **Observed:** The main window opens at its configured fixed initial size of 1500 x 940 pixels (`scripts/desktop_app.py`) instead of filling the usable screen.
- **Expected:** Open the main application maximized by default while retaining the Windows title bar, taskbar and standard minimize/restore/close controls. PDF preview windows remain separately sized.
- **Impact:** The first view wastes available display area and makes the document and invoice fields harder to compare.
- **Fix:** Create the main native window with pywebview's `maximized=True`; keep 1500 x 940 as its restored size and leave PDF preview windows separately sized. Verify restore/remaximize behavior and layout on the office display and at common Windows scaling settings in the next installer.
- **Verification so far:** Seven isolated launcher tests passed; the packaged pywebview WinForms backend sets the maximized state before the form is shown. Visual verification in the installed app remains pending.

## 4. Main window proportions need review

- **Status:** Open. Reported during the first installed-app visual check.
- **Observed:** Some layout proportions in the desktop window need adjustment; exact elements will be identified with the user during a later visual pass.
- **Expected:** Balanced space allocation and readable controls across the actual office display sizes and Windows scaling settings.
- **Planned fix:** Collect concrete screenshots, viewport dimensions and affected elements from the user before making targeted layout changes. Recheck all impacted views after each adjustment.

## 5. Console windows flash during PDF processing

- **Status:** Fixed in source and verified in a new development bundle; the published 0.1.0 installer still has the defect.
- **Observed:** Two or three small windows appear in quick succession every few seconds throughout processing, then disappear. The user sees them over the main application.
- **Expected:** Background PDF conversion and OCR should never create visible console windows or steal focus from the desktop app.
- **Cause confirmed with a Windows window-event observer:** The installed release displayed transient `PseudoConsoleWindow` events owned by `pdfinfo.exe`, `pdftoppm.exe`, and `tesseract.exe`. Both library metadata/version probes and repeated OCR child launches need a no-console creation policy. The worker itself was already hidden.
- **Impact:** Disruptive visual flicker and possible focus loss, especially for batches containing many PDFs. No evidence so far of altered OCR output or lost data.
- **Fix:** `src/estrattore/ocr_child_processes.py` installs a worker-only `Popen` subclass before third-party OCR imports and adds `CREATE_NO_WINDOW` only to the four known PDF/OCR executables. No dependency files, OCR arguments, renderer, or frontend were changed.
- **Verification:** Old installed worker: four vendor-tool window events on a two-page PDF. New bundle: zero on that PDF and zero on a four-page PDF. Paired source/new-bundle runs yielded identical OCR tokens, geometry, invoice grouping, and extraction fields (435 tokens/one invoice and 1010 tokens/two invoices). Focused tests passed. The old OCR-only local candidate `EstrattoreScanFatture-Setup-0.1.0-8030e921c32c.exe` is superseded by the full new installer noted below. The current installed release cannot inherit a source-code fix.

## 6. Old code release remains after an update

- **Status:** Fixed in source and in the new local installer; real installed-app upgrade still to be accepted by the user.
- **Observed policy:** The original installer retained the previous complete code release indefinitely, duplicating the bundled runtime even after a successful new startup.
- **Expected:** After a completed, validated installation, keep only the new code release. Do not wait for first app launch. Never remove the separate business-data directory.
- **Fix:** The installer verifies installed executable, frontend, OCR/PDF runtimes, WebView2 permissions and completion marker, then removes older code directories. Validation or deletion failure returns a nonzero setup exit code and retains old code. No runtime cleanup or automatic rollback is provided.
- **Verification:** Isolated installer upgrades A -> B -> B -> C leave one code release immediately and preserve data. A deliberately incomplete upgrade returns exit code 10 and retains old code. A new frozen bundle passes startup, OCR queue, review and clean close in isolation.
- **Candidate:** `build/installer-dist/EstrattoreScanFatture-Setup-0.1.0-rc.2.exe` (390,318,214 bytes; SHA-256 `7FCE28A681321C7FEDCC2D65249DA84FEDD734FCDC6430E1D31606894D72531E`). Its internal release ID remains `0.1.0-929e14e85496`. This supersedes the earlier local candidates. It has not been installed or uploaded.
- **Limit:** If validation fails after files and shortcuts have been written, the old code remains but Inno does not restore the old shortcut automatically; repair/reinstallation is manual.

## 7. Show progress while discarding a review group

- **Status:** Fixed in source; needs verification in the next installer.
- **Observed:** After confirming `Scarta gruppo`, the app does not clearly show that invoice records are still being removed.
- **Expected:** Display a blocking progress window with the Italian message `Stiamo eliminando i record delle fatture. Attendere...` and a moving progress bar until the operation finishes. Prevent duplicate actions while it runs; close the window on success and show a clear error if removal fails.
- **Fix:** After confirmation, a blocking dialog shows the requested message and an indeterminate moving bar until all discard requests and the review-list refresh finish. Duplicate submissions and keyboard actions are blocked; a partial failure refreshes the remaining review and shows the error.
- **Verification so far:** Frontend build and lint passed (one pre-existing lint warning); two backend discard tests passed. Playwright verified loading, success and partial failure at desktop and compact widths using isolated mocked API data. The screenshots were inspected and removed. Real installed-app behavior remains to be checked in the next installer.

## 8. Choose where to save generated files

- **Status:** Fixed in source; needs verification in the next installer.
- **Observed:** Invoice exports and registry/report downloads previously went to the WebView downloads folder without an app-controlled destination or name.
- **Expected:** The desktop app shows a native Windows `Save as` dialog for invoice exports, re-exports, registry Excel and cleaning reports. The developer-feedback bundle remains an automatically named Desktop folder by explicit user choice.
- **Fix:** The desktop bridge restricts requests to known export kinds and uses a native Save dialog. Invoice cancellation precedes export-history changes; a failed delivery prevents the history commit. Existing invoice exports can be saved again under a new name without creating a second history event. Browser development mode retains ordinary downloads.
- **Verification so far:** 32 focused Python tests passed, including cancel, rename, XLSX/ZIP generation, existing-export copy and failed file replacement. Frontend build and lint passed (one existing lint warning). Interactive native-dialog verification remains for the next installer.

## 9. Closing the app must retain active OCR work

- **Status:** Fixed in source; not present in the installed 0.1.0 candidate under test.
- **Observed:** Queued jobs survive app close, but the active job uses the same cancellation path as the explicit `Annulla` command and is discarded.
- **Expected:** Confirming app close interrupts the current worker and returns its job to the front of the persisted queue. On next launch, completed source-PDF checkpoints are reused; only the interrupted PDF is reprocessed. Explicit `Annulla` still discards the job.
- **Fix:** Separate shutdown interruption from user cancellation in the worker, storage and persistent queue. The close prompt now describes resume rather than cancellation.
- **Verification:** Isolated Python tests cover checkpoint reuse, queue recovery/order, explicit cancellation and failed-close recovery. Source frontend build and lint pass. Installed-app restart after a real OCR interruption still needs a user acceptance test with a new installer.
