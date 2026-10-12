# Windows desktop packaging plan

Future distribution work belongs here. Active decisions and evidence belong in
`docs/notebook.md`. Keep one repository and build inputs under `packaging/`.

Current state: the private-runtime onedir executable, owned PDF previews and
offline per-user installer are implemented; see packaging points 1-6 in the
notebook. Clean Windows 10/11 acceptance, signing, license and redistribution
review remain release gates.

## Distribution contract

The user installs and uninstalls one application. All application-specific runtime
dependencies must be private files owned by the application, included in offline
delivery and removed by its uninstaller. A bootstrapper installing prerequisites
under separate product entries does not satisfy this requirement.

The supported Windows OS remains a prerequisite. Audit transitive native/managed
runtime requirements against the chosen Windows 10/11 baseline; never assume a
clean machine has the developer's installed frameworks.

```text
Windows launcher
  -> owns desktop window and local Python backend
  -> serves built React assets
  -> runs bundled PDF/OCR tools through explicit paths
  -> stores persistent data in app-owned user folders
```

- Bundle Python and required libraries using PyInstaller.
- Ship built React assets; Node/npm/Vite remain development tools.
- Bundle Tesseract, required DLLs, language models and OCR configuration.
- Bundle PDF rendering (currently Poppler for pdf2image).
- Bundle the web engine; do not require installed Chrome/Edge or shared WebView2.
- Track component versions, provenance, checksums and redistribution notices.
- Release mode must not silently fall back to PATH, MiKTeX, system OCR or browser
  installations. Such fallback would conceal an incomplete package.
- Exclude sample invoices, development data, tests and UI-check tooling.

## Desktop window and lifecycle

External browser mode remains for development. The delivered launcher owns the
window, backend and worker tree. Browser-tab closure is not an app lifecycle signal.

Preferred candidate: pywebview with private WebView2 Fixed Version binaries,
explicitly selected through `WEBVIEW2_RUNTIME_PATH`. This distribution mode is
supported for application-private use. It adds substantial size and requires us
to refresh the engine through app releases; it does not update automatically.

Validate the host's .NET/native requirements before committing to pywebview. If
they require separately installed software outside the supported OS baseline,
change the host or bundle an appropriate private redistributable runtime. The
distribution contract takes precedence over the library preference.

Close handling must account for draft writes, exports and active OCR. Idle close
stops the server/window; active work offers return or cancel-and-close. Background
operation requires a separate decision. Preserve Windows Job Object ownership
and existing queue/crash recovery contracts; verify no orphan OCR subprocesses.

## Roadmap

### 3. Prepare built frontend

- Build fresh assets with locked dependencies as part of the release command.
- Package static frontend resources, excluding sample invoices.
- Fail clearly when required assets are absent.
- Resolve assets independently of the working directory or source checkout.
- Verify cache behavior after replacement, empty runtime behavior and real routes.

### 4. Create executable launcher

- Start with PyInstaller `onedir`; wrap the verified directory in an installer.
- Dispatch internal `--document-worker` before starting the UI/backend. Source
  mode currently uses `python -m estrattore.document_worker`.
- Bind locally, handle single-instance/port behavior and persist startup logs.
- Select the private web engine and app-owned browser profile/cache paths.
- Verify actual file dialogs, PDF windows, downloads and frontend/API calls.
- Test paths with spaces, standard-user execution, cancellation, forced shutdown,
  crash recovery and process cleanup through the frozen entrypoint.

### 5. Bundle PDF/OCR and runtime dependencies

- Include Tesseract and Poppler; neither is an optional external prerequisite.
- Audit all DLLs/models required by enabled OCR profiles and record versions.
- Use explicit package paths for rendering and OCR in release mode.
- Verify redistribution notices and component checksums.
- Exercise real OCR from the EXE on clean Windows 10/11 without development tools
  and with network disabled. An opening window is insufficient evidence.
- Check host-framework dependencies and private-runtime filesystem permissions.

### 6. Installer, updates and uninstall

Use one installer/uninstaller entry, initially considering Inno Setup after the
directory package works. Do not add separate runtime installers, global PATH
changes, services or scheduled updaters for app-private dependencies.

- Replace code and private runtimes together during updates.
- Keep invoices, registry, drafts and feedback outside install binaries.
- Back up before incompatible migrations; interrupted updates must not silently
  launch mixed binaries or destroy persistent data.
- Uninstall removes program files, private runtimes, shortcuts and app caches.
- Default uninstall preserves business data. Offer an explicit, separate option
  to remove all internal app data, identifying its location.
- Never remove shared system components, user-selected original files or exported
  documents outside internal storage.
- Test install, offline launch/OCR/export, upgrade with existing data, uninstall
  with data retention, and explicit full internal-data removal in isolated Windows
  environments. Inspect filesystem and process residue after each scenario.

## References

- [PyInstaller spec files](https://pyinstaller.org/en/stable/spec-files.html)
- [WebView2 distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [WebView2 runtime choices](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version)
- [pywebview runtime path and storage settings](https://pywebview.flowrl.com/api/)
- [pywebview host prerequisites](https://pywebview.flowrl.com/guide/installation.html)
