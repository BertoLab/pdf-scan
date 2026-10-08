# Project Notebook - Codex Working Memory

## Processing page layout refinement - 2026-09-21

Status: completed.

### Theoretical problem

The processing page applies a generic three-column page header to only two actual
content groups. This leaves the active/history tabs in an accidental middle position
instead of anchoring navigation to the right edge. The job list has the inverse
problem: each sparse record expands across the full application width, separating
related values by a very large empty interval and weakening row ownership.

The problem is content measure, not missing information. A chronological operational
list must preserve vertical scanning order, keep comparable values on stable axes and
limit the horizontal distance needed to reconstruct one record. Empty states must use
the same bounded content region instead of drawing an almost empty full-width panel.

### Chosen approach

Use a final, responsive list-table pattern rather than adding data or introducing a
decorative card grid:

- give the page header two real columns, with title left and tabs/actions right;
- center the job collection in a bounded `1120px` content region;
- render each job as a compact row with stable identity, status, progress, source and
  action columns;
- retain chronological top-to-bottom reading and all existing interactions;
- collapse rows into a legible two-column arrangement at smaller widths;
- constrain and center the empty state within the same content measure.

### Micro-steps

1. Add a processing-specific header contract without changing shared page headers.
2. Group selection and rows in one bounded content region.
3. Replace elastic card internals with explicit semantic row cells.
4. Add responsive row behavior for reduced widths.
5. Run lint/build and inspect active-empty and populated-history screenshots at all
   standard UI-check widths, scoring each touched state before closure.

### Implementation outcome and closure

The processing header now has two explicit columns, so the view switch and contextual
delete action remain anchored to the right independently of whether history has a
selection. Selection controls and rows share one centered content region capped at
`1120px`. Jobs are rendered as contiguous compact rows with stable axes for identity,
status, progress, source and actions rather than independent full-width cards. At
reduced container widths, the same cells reflow through deliberate two- and three-row
layouts without changing their semantic order.

The empty state is limited to `620px` and centered inside the same region. It therefore
communicates absence without drawing a nearly empty border across the application.

Verification:

- frontend production build: passed;
- frontend lint: passed with no errors or warnings;
- Playwright inspected 1440x900, 1728x972 and 1920x1080 for the real empty-active and
  populated-history states;
- a temporary route mock inspected populated active and queued rows at 1440x900 and
  1920x1080 without reading or modifying persistent user data;
- empty active view: `8.5/10`;
- populated active view: `8.5/10`;
- populated history view: `8.5/10`;
- responsive consistency: `8/10`;
- overall touched page: `8.4/10`.

The result is satisfactory because it removes accidental layout behavior, improves
scan distance without adding information and preserves one consistent chronology
pattern across active and historical processes. The temporary mock and screenshots
were removed after inspection.

## Developer-area feedback export - 2026-09-18

Status: completed after visual refinement.

### Visual correction - 2026-09-18

The first implementation passed rendering checks but failed the stricter design
review introduced afterward. Scores were: closed toolbar `4/10`, developer menu
`7.5/10`, confirmation dialog `8/10`, overall `5.5/10`. The failure was compositional:
the forced full-width toolbar created two disconnected control islands along the
bottom of an oversized header, while the invoice identity remained isolated above.

The correction must replace incidental flex wrapping with a responsive layout owned
by the document panel width. On wide panels, invoice context and the complete toolbar
share one top row. On narrow panels, the toolbar moves below as one explicit region.
Source metadata must truncate visually with full text available as a tooltip. The
developer entry remains textual and visibly secondary, separated from operational
controls without looking disabled.

Verification requires screenshots of the closed toolbar, open developer panel and
confirmation dialog at both standard viewports, followed by explicit per-state scores.

### Final visual outcome

The forced full-width toolbar was replaced by a container-responsive grid. At Full HD
the invoice context and controls share one compact top row. At narrower document-panel
widths the toolbar moves below the context; below 620 px it becomes two intentional
rows pairing destination controls and view controls. Source filenames truncate with a
native full-value tooltip, and the developer area is separated from operational
controls by a restrained divider. A disappearing zoom-out glyph found during the last
1440 px review was replaced by a stable CSS symbol before closure.

Final design scores after inspecting closed, open and modal screenshots:

- closed toolbar at 1920 px: `8/10` - compact hierarchy, aligned control group and
  substantially more PDF space;
- closed toolbar at 1440 px: `7.5/10` - denser but intentionally grouped, with no
  accidental orphan controls;
- open developer panel: `8/10` - clear separation between overlays and export;
- export confirmation dialog: `8/10` - readable purpose, destination and actions;
- touched UI overall: `7.9/10`.

Playwright checks covered 1440x900, 1728x972 and 1920x1080. The screenshots were
temporary and deleted after inspection. This supersedes the earlier rendering-only
approval: the first composition was functionally correct but aesthetically unresolved.

User-directed final header hierarchy: keep exactly three content lines: invoice title,
`File origine:` and page-reorder control. Remove the OCR summary from this header.
On wide panels, preserve the toolbar's horizontal position but align it to the second
line rather than the title line; reduce vertical padding accordingly. Narrow-panel
stacking remains unchanged to protect control readability.

Final verification of this hierarchy passed at 1440x900, 1728x972 and 1920x1080.
At Full HD the operational group is aligned with `File origine:` on the second line,
while the left context remains exactly three lines. At the narrower document-panel
width the same controls wrap into two deliberate rows because a single row cannot fit
without shrinking labels or targets. Scores: Full HD header `8.5/10`, narrow header
`7.5/10`, responsive consistency `8/10`, overall touched header `8/10`.

### Theoretical problem

The invoice toolbar currently exposes OCR overlays as if they were ordinary review
controls. The same area now also needs a deliberate way to export sensitive learning
material. The broader problem is to separate primary accounting actions from
developer-only diagnostics while giving a non-technical studio user a safe,
observable handoff operation when requested by the developer.

The export crosses the complete local-app path:

```text
React toolbar and confirmation dialog
  -> local HTTP endpoint
  -> desktop-destination policy
  -> consistent feedback bundle builder
  -> Windows user Desktop
  -> result reported to the dialog
```

### Resolution theory and chosen approach

This is a final local-app workflow, not a browser-download simulation. Keep zoom,
registry access and document review visible as primary controls. Move OCR overlays
behind an explicitly secondary `Area sviluppatore` menu so their audience is clear.
The export action must state that it contains OCR evidence, corrections and available
invoice PDFs; confirmation is mandatory because these are sensitive fiscal data.

Reuse `export_feedback_bundle` as the only bundle implementation. Add a small service
that resolves the real Windows Desktop known folder, creates a timestamped unique
directory, always includes available PDFs, and returns a user-facing summary. The
frontend must not submit an arbitrary filesystem path. The backend owns destination
selection and therefore prevents path injection and keeps packaging behavior stable.

The operation is synchronous at the request level but runs on the threaded local
server. Its modal progress state blocks duplicate activation and makes completion or
failure explicit. The published directory remains atomic because the existing bundle
builder assembles it under a temporary sibling and renames it only after validation.

### Micro-steps

1. Add and test Desktop resolution, unique naming and feedback export orchestration.
2. Add the local POST contract and an unmocked HTTP integration check.
3. Build the developer menu and export dialog with accessible states.
4. Refine toolbar grouping and responsive layout using temporary screenshots.
5. Run focused Python tests, frontend lint/build and UI checks; critically review the
   result and record closure.

### Outcome

Status: completed.

The invoice toolbar now gives primary space to registry access and a compact grouped
zoom control. OCR words, crops, tables and labels are available from a secondary
`Area sviluppatore` popover. The panel is intentionally neutral rather than using the
primary green treatment; its export action is separated from passive overlays by a
section boundary.

Export requires an explicit confirmation explaining that OCR results, corrections
and available invoice PDFs are included. While the bundle is assembled the dialog
prevents duplicate activation and shows non-technical progress. Success reports the
created Desktop folder, collected invoice count and any unavailable PDFs; failures
remain in the dialog with a retry action.

The frontend calls `POST /feedback/export`. The local backend chooses the destination
instead of accepting a client path, resolves the Windows user Desktop from Explorer's
known user-shell setting, allocates a readable timestamped name with collision
suffixes and calls the existing validated bundle builder with PDFs enabled. The
bundle remains atomically published by temporary-directory rename.

### Verification

- the Desktop export service test created two same-minute bundles and verified unique
  names, SQLite output, manifest output and `includes_pdfs=true`;
- the local-app HTTP integration test exercised React's actual route boundary through
  `LocalAppHandler`, the real export service, SQLite snapshot and bundle filesystem,
  replacing only the physical Desktop location with an isolated test directory;
- the feedback collection, service and local-app suites passed;
- frontend TypeScript/Vite production build passed;
- frontend lint passed with zero errors and the pre-existing
  `applyReviewDraftsToInvoices` dependency warning only;
- temporary Playwright screenshots covered initial toolbar, open developer panel and
  confirmation dialog at 1440x900 and 1728x972. Visual inspection found no clipping,
  overlap or unreadable controls; the narrow document column uses an intentional
  second toolbar row while the wide layout stays on one row.

Critical review: this adds no second export implementation, no browser-specific
download behavior and no client-controlled write location. A hard process kill while
the atomic working directory is being assembled can still leave its hidden temporary
Desktop sibling; it cannot publish a corrupt final bundle. If observed in packaged-app
testing, cleanup should target only stale directories matching the exact owned prefix
and age policy rather than broad Desktop deletion.

## Collection Extraction v1 integrity review - 2026-09-17

Status: completed.

### Theoretical problem

The collection is an audit trail spanning mutable frontend state, the operational
JSON register, SQLite feedback records, OCR evidence files and archived PDFs. Its
correctness therefore depends on event identity, commit ordering and explicit
provenance across storage boundaries. Value equality alone cannot identify a user
event, stale field metadata cannot describe a later action, and a database snapshot
is not portable when its referenced evidence is copied from a different state.

The accounting save remains the primary operation. Collection failures must be
observable and recoverable, but must not prevent a valid invoice from being saved.
Conversely, collection may only claim an accepted value after the operational
register proves that exact value was persisted.

### Resolution theory and chosen approach

This is a final architectural correction, not a temporary compatibility layer:

- treat the persisted register as the commit proof and recover retries from its
  snapshot, never from a newer request body;
- use a fresh UUID for each actual registry mutation, while reusing only a matching
  still-prepared operation after interruption;
- construct provenance at the boundary where the action is known: registry-table
  edits are manual, frontend registry refreshes are automatic;
- verify manifest collection identities against SQLite and recapture missing runs;
- recognize both source and split-invoice archive PDFs as available source assets;
- export a pruned completed-run database and all its evidence as one staged bundle,
  failing rather than publishing dangling evidence references;
- record collection failures and let the operational save finish.

### Micro-steps

1. Add focused regressions for interrupted capture/save, repeated transitions and
   manual-versus-automatic provenance.
2. Repair collection-run recovery and make review capture fail-open for business saves.
3. Replace value-derived registry event identity with occurrence identity plus
   prepared-operation retry matching.
4. Bind archived split PDFs and propagate archive deletion availability.
5. Build exports from one completed-only snapshot using a temporary directory.
6. Run focused Python tests, wider persistence tests, frontend build/lint and the
   provenance browser check; then record closure and residual limits.

### Outcome

All seven review findings were confirmed. The correction establishes these runtime
invariants:

- the accounting register is the primary commit and remains saveable when feedback
  preparation, commit or diagnostics fail;
- a manifest collection ID is trusted only when its extraction run exists in SQLite;
  otherwise the run is recaptured with the same manifest identity;
- an idempotent invoice retry reconciles pending feedback against the invoice already
  persisted in the register, never against newer submitted values;
- each actual registry mutation receives a fresh occurrence UUID; only a matching
  still-prepared operation is reused after an interrupted business write, so
  `A -> B -> A -> B` produces four revisions including the initial save;
- registry-table edits receive fresh manual provenance at the backend boundary, while
  automatic registry refreshes carry explicit automatic provenance and become the
  actual presented snapshot;
- a reprocessed split invoice resolves its archived PDF through parent-record lineage
  and accepts the link only when the archived file hash matches the processed copy;
- export is assembled in a temporary sibling directory, prunes staged/incomplete
  runs, verifies every referenced OCR evidence blob and SQLite foreign key, reports
  missing PDFs as unavailable in the exported snapshot, and publishes atomically.

The direct manifest write before SQLite capture remains recoverable rather than
transactional across filesystems: a failed capture can leave the ID in the manifest,
but the next review verifies SQLite and recaptures it. This is deliberate because a
single transaction cannot cover JSON, compressed evidence and SQLite. Content-addressed
evidence prevents duplicate payloads during recovery.

### Verification

- 54 focused collection, register and queue tests passed, including isolated
  reproductions of all seven findings;
- 97 wider persistence/lifecycle tests passed across collection, register, PDF/source
  archives, processing, queue, local app, cleanup and reprocessing;
- 6 document worker/supervisor tests passed outside the sandbox because Windows denied
  sandbox access to pytest's temporary directory;
- Python compilation and diff whitespace checks passed;
- frontend production build passed;
- frontend lint passed with zero errors and the pre-existing warning for the
  `applyReviewDraftsToInvoices` effect dependency;
- the provenance Playwright check passed against the built frontend. It verifies the
  emitted automatic-registry payload using controlled HTTP fixtures; backend
  persistence is covered separately by the Python integration tests.

No OCR was rerun and no real application data were read or modified. The remaining
product-level decision is the retention/purge policy for textual fiscal feedback after
its PDF is deleted. A source content hash currently retains one active archive path;
supporting multiple interchangeable physical copies would require a separate asset-
location table and is not necessary for the current single-archive workflow.

## Studio feedback collection contract - 2026-09-16

Status: completed.
Problem: retain trustworthy observations during ordinary office use before the
training algorithm exists and before operational cleanup removes their evidence.
Read workflow rules, prior design, runtime paths, review/register save paths and
candidate generation references. Existing register saves overwrite field values;
review completion can immediately trigger cleanup. A final snapshot is insufficient.
Resolution: specify immutable extraction, actual presentation, committed correction
provenance, page lineage and versioned configurations as separate linked records.
Healthy incremental storage proposal: dedicated runtime feedback directory containing
a transactional index/event database and shared compressed OCR evidence; reuse archive
PDFs through stable references, include available assets in a portable export.
SQLite atomicity does not extend to register JSON or external evidence files: capture
needs durable pending operations and recovery, not two unchecked sequential writes.
Critical review: draft provenance must survive restart; registry sibling updates are
dependent actions; unchanged saves are weak acceptance; PDF deletion does not silently
erase or authorize retention of textual feedback. Explicit purge semantics are required.
Added the field contract, lifecycle, cancellation/cleanup behavior, observability and
integration acceptance checks to feedback_learning_design.md, section Collection
contract v1. No runtime user data were read or modified. Reviewed documentation against
current save/cleanup entry points; no runtime tests apply to this specification change.
Next implementation follows this contract; trainer/model work stays deferred.

Implementation micro-steps:

1. Add runtime-owned feedback paths and a versioned transactional store.
2. Preserve immutable pre-registry fields in new manifests and collect completed
   extraction evidence without a second OCR pass.
3. Preserve semantic edit provenance in drafts and registry cascades.
4. Bridge accepted invoice/register saves with idempotent pending operations, then
   append immutable review revisions before review cleanup.
5. Track segmentation/reprocessing lineage and archived-source availability.
6. Add a consistent portable export and collection diagnostics.
7. Verify storage, crash/retry, save/edit, reprocess/segmentation and the real HTTP
   boundary; run Python tests plus frontend lint/build for the payload extension.

Verification refinement after implementation review:

- exercise `POST /invoices/save` through `LocalAppHandler`, with the real router,
  registry persistence and feedback database; only the unrelated PDF archive writer
  may be replaced by an isolated test adapter;
- simulate failure after the business registry write but before feedback commit,
  then prove that the review remains recoverable and retry creates one revision;
- verify archived-source promotion and later deletion change image availability
  without deleting textual extraction/review evidence;
- classify frontend build/lint as static payload-contract checks, not as proof of
  user interaction or HTTP persistence.

Outcome:

- added runtime-owned `feedback/feedback.sqlite3` and content-addressed compressed
  evidence under `feedback/evidence/`, with schema and dataset identities;
- extraction runs are staged per process, promoted only after review publication and
  removed on cancellation/error without touching completed shared evidence;
- manifests retain immutable pre-registry `extraction_fields`, stable token/run/invoice
  identities and available candidate CSV evidence without rerunning OCR;
- review drafts carry semantic manual/registry/dependent-fill provenance; accepted
  invoice saves and later register edits append idempotent revision chains;
- the JSON registry and feedback database are bridged by durable prepared operations;
  a retry after interruption commits one revision and only then closes the review;
- the initial registry-edit operation identity used previous and accepted snapshots.
  The later integrity review above found this insufficient for repeated identical
  transitions and replaced it with occurrence UUIDs plus prepared-operation matching;
- segmentation events and reprocessing lineage are persistent; source archive
  promotion/deletion updates image availability while preserving textual history;
- legacy saved rows are represented explicitly without fabricated OCR evidence;
- the export CLI creates a consistent SQLite backup plus referenced evidence,
  available PDFs and checksums; `--status-only` exposes incomplete/failed collection.

Verification and contract coverage:

- 101 persistence/lifecycle tests passed across worker, processing, archives, registry,
  queue, collection, local HTTP route, cleanup and reprocessing;
- 176 extractor tests passed; 240 existing Pillow deprecation warnings remain;
- the real `LocalAppHandler` HTTP `POST /invoices/save` was exercised against isolated
  registry and feedback storage. The unrelated PDF archive writer was replaced by a
  test adapter; physical PDF archive behavior remains covered by its own tests;
- the crash window after registry write and before feedback commit was simulated;
  retry retained the review, produced one completed revision and no duplicate;
- Playwright drove the built frontend, edited a field, crossed the real save-validation
  UI and asserted the emitted provenance payload. GET APIs and save response were
  controlled browser fixtures, so this proves frontend payload generation but not
  backend persistence; the separate unmocked HTTP test covers that boundary;
- frontend build passed; lint passed with zero errors and the pre-existing
  `applyReviewDraftsToInvoices` dependency warning;
- Python compilation passed. No OCR was rerun and no real application data were read
  or modified.

Critical review:

The collection is append-oriented and separate from operational manifests, so normal
review cleanup cannot erase learning evidence. It introduces no hidden permanent PDF
copy and does not make OCR success depend on telemetry success. The recoverable save
bridge adds justified complexity at the only non-transactional cross-store boundary.
The major residual product decision is deliberate retention/purge of textual fiscal
feedback after a user deletes its PDF. Real multi-thousand-document disk/latency
measurement must precede final retention defaults. Training and automatic label
inference remain deferred by design.

## Learning from studio corrections: design review - 2026-09-09

Status: theoretical investigation completed; implementation not started.
Read current OCR/manifest types, field edits, registry enrichment, archive snapshot
updates and document cleanup. Reviewed primary research on distant supervision,
candidate labeling, document models and OCR training requirements.
Key finding: accepted business values, actual printed text and verified geometry
must be distinct. Current field edits preserve metadata while changing value, and
working-file cleanup removes original diagnostics. Existing records are not a clean
training set despite containing useful page/token evidence.
Decision proposal: capture immutable pre-enrichment evidence and shown values plus
committed correction provenance before studio rollout; use offline error analysis
and a controlled candidate-ranking/layout experiment before considering OCR retraining.
No real user data were read/exported and no runtime code or data were changed.
Detailed reasoning, limits, storage proposal, evaluation and sources:
[Learning from reviewed invoices](feedback_learning_design.md).
The design resolves feasibility; storage format and rollout scope remain proposals.

## Readable saved PDF names - 2026-09-07

Status: completed. The user confirmed full-name hover already works.
Problem: saved filenames expose a SHA-1 prefix derived from registry record IDs.
Approach: separate internal identity (unchanged in registry) from artifact naming.
Use Fattura, invoice number, issuer, condominium and invoice date; preserve existing
Windows-safe sanitization and collision suffixes. Bound components to keep the
filename practical. Missing data yields Fattura.pdf with collision suffixes.
Remove the now-unused ID parameter from the archive helper and its callers.
Verify physical split/save and duplicate handling through existing storage tests.
Existing files are not migrated: changing persisted paths needs an explicit
coordinated migration, rather than presenting a fake filename in the viewer.

Outcome: hash removed from new invoice names; registry record IDs and source links
remain unchanged. Discarded invoice naming still uses source name and page numbers.
15 archive/storage tests passed, including real PDF splitting, collision suffixes,
missing-field fallback, reprocessing lineage and bounded Windows-safe filenames.
The test archive stub was updated for the removed ID parameter. No frontend changes
or screenshots were needed. Existing archived filenames remain unchanged; only new
save operations use the readable naming policy after restarting the Python service.

## Invoice archive filenames and source navigation - 2026-09-07

Status: completed.
Problem: invoice archive rows identify the original upload instead of the saved
invoice artifact, and the source action bypasses the source archive.
Approach: use the basename of the persisted invoice PDF path (including extension),
never reconstruct its name from editable fields. Replace the source open action
with filename navigation using the existing archive reference resolver and focus
workflow. Match by stored path/URL, not filename; missing references use the existing
Italian error dialog. Include saved filenames in archive search.
Steps: scoped table/callback/CSS changes; build/lint; isolated Playwright checks for
saved/discarded filenames, source navigation, missing sources, and temporary visuals.
Only frontend presentation/navigation changes; no new transport/storage contracts.

Outcome: File PDF shows the persisted basename, with the full name on hover.
Sorgente is a filename-styled button that switches to the source archive, clears
filters and focuses the matching row using the shared navigation. Empty references
cannot match each other accidentally. Original PDFs are still opened from that row.

Checks: build passed; lint zero errors with the existing useEffect dependency warning.
Temporary `node checks/archive-source-review.mjs` exercised built React at
1366x900 and 1920x900 with isolated API fixtures: saved/discarded names, new-name
search, duplicate source filenames resolved by path, missing reference message,
filter reset and no new browser tab. Six temporary screenshots were captured and
page families inspected; screenshots and harness removed after evaluation.
Coverage: frontend navigation and visual behavior; mocked transport/storage do not
verify backend persistence or physical file existence. Existing archive data
remains authoritative; no PDF files are renamed or modified by this change.

## Table typography regularization - 2026-09-07

Status: completed. Scope: table readability, excluding invoice review fields.

Problem: the register combines 14px text with 64px single-line rows, while the
customer registry uses 18.2px at the same height. Visual density is inconsistent.
Carbon and Fluent demonstrate that 14px can be valid for dense interfaces; 18px
is our product-specific choice for sustained fiscal-data inspection, not a standard.

Approach: shared 18px/24px table typography, semibold headers, 16px related
controls. Preserve the 64px row/56px header virtualization contract; center cell
contents and allow two-line headers. Widen only columns that need room for
identifiers, dates and labels; retain horizontal scrolling. Let filters wrap.

Micro-steps: update scoped CSS; check build and lint; inspect customer registry,
invoice register (view/edit and discarded), PDF/source archives using temporary
Playwright screenshots. Check long values, headers, horizontal/vertical scrolling.
Controlled browser fixtures may replace API data for visual checks; they do not
verify backend persistence. No API or storage contracts change in this point.

Reference: https://carbondesignsystem.com/components/data-table/style/
Reference: https://fluent2.microsoft.design/typography

Outcome: scoped CSS tokens unify the three table families. Virtual row heights
and rendering calculations remain unchanged. CF columns now accommodate 16-character
identifiers in edit mode. Grid minimum widths cover their actual column tracks;
customer-code inputs no longer exceed narrow cells. The archive search now respects
its grid allocation, fixing a toolbar overlap revealed by the screenshots.

Verification: build passed; lint exited successfully with zero errors and the
pre-existing missing `applyReviewDraftsToInvoices` dependency warning at App.tsx:4256.
Temporary command: `node checks/table-typography-review.mjs` from tools/ui-checks.
Real built React components were served locally with intercepted API fixtures:
100 customers/invoices, missing values, long names, and a discarded record.
Checked eight states at 1366x900 and 1920x900 (16 temporary captures): registry
view/edit, invoice register view/edit/scrolled/discarded, invoice PDF archive and
source archive. Assertions checked 18px typography, header clipping and toolbar
overlap; screenshots were inspected across all page families and edit/scroll states.
Verifies frontend layout/navigation and retained virtualization; does not verify
Python routing, persistence or real user records. No backend contracts changed.
The temporary harness and screenshots were removed after inspection.

Tradeoff: larger text requires horizontal scrolling; long names remain ellipsized
in view mode and editable via their inputs. This point introduces no text-size
setting and does not change invoice review fields. The product-specific 18px base
is now consistent and can be judged before adding customization.

This notebook is written for Codex, not for the end user.

It stores project memory, current plans, important decisions, doubts, attempts, checks, and closures. Stable operating rules live in `.agents/rules_workflow.md`. Future desktop packaging strategy lives in `docs/packaging_plan.md`.

All future entries in this notebook must be written in English. User-facing UI text remains Italian.

This file was compacted from the previous verbose Italian notebook to reduce token usage and make future reads more efficient. Preserve decisions and active context; do not recreate long narrative history unless it is needed for a current point.

## Core Principles

### Application Code Is Not User Data

Keep application code separate from user data.

Expected runtime root:

```text
%LOCALAPPDATA%\EstrattoreScanFatture\
```

The app may read external files selected by the user and copy them into internal storage. It must never modify, rename, move, or delete files outside its own runtime storage.

### Desktop Direction

Do not rewrite the app as a native desktop application.

Chosen architecture:

```text
Windows launcher
  -> starts local Python backend
  -> serves built React frontend
  -> opens a launcher-controlled app window
  -> stores persistent user data in user folders
```

External browser usage remains development/debug mode. The packaged app must eventually control backend lifetime, OCR jobs, and the app window.

### Language Policy

Internal Codex-facing docs are English:

- `AGENTS.md`;
- `.agents/rules_workflow.md`;
- `docs/notebook.md`;
- future development notes.

User-facing app labels, messages, validation dialogs, and UI copy are Italian.

## Current Development Commands

Run the local app in development mode:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --rebuild
```

Reprocess a full review batch for internal extractor debugging:

```powershell
.\.venv\Scripts\python.exe scripts\reprocess_review.py --review 1
```

Reprocess one invoice/document inside a review batch:

```powershell
.\.venv\Scripts\python.exe scripts\reprocess_review.py --review 1 --invoice 2
```

The invoice selector resolves canonical id/title first, then the displayed invoice
number, and only then the position inside the review as a compatibility fallback.
Review-index duplicates are removed by canonical `invoice_id` before listing or
selection, so a displayed value such as `30` resolves to `invoice-030` even when
it is not the thirtieth row of that review.

List invoice ids in a review batch:

```powershell
.\.venv\Scripts\python.exe scripts\reprocess_review.py --review 1 --list
```

Standard UI checks after frontend changes:

```powershell
npm --prefix apps/viewer run build
npm --prefix apps/viewer run lint
```

## Runtime Data Model

### Source Input

PDFs and folders selected by the user are copied into app-owned storage. Original external files are not touched.

Current workflow decision:

- one user selection creates one processing job;
- a selection is either multiple PDF files or one folder, never an accumulated mix of sources;
- after the Windows picker returns a valid selection, the app imports it and starts OCR immediately;
- there is no intermediate source cart and no separate import confirmation;
- pause, resume, and cancellation belong to the processing-job lifecycle after startup.

Reason: source identity must remain unambiguous in history, retention, reprocessing, and cleanup decisions.

Implementation result:

- the import dialog now exposes exactly two mutually exclusive commands: select PDF files or select one folder;
- a valid picker result is imported as one batch and immediately submitted to the persistent OCR queue;
- the dialog closes only after the backend accepts both the internal batch and processing job;
- the previous multi-source staging list, per-source removal, and final import confirmation were removed from the frontend;
- the backend exposes `POST /batches/import-and-process` as the single orchestration boundary for this workflow.

Verification:

- frontend build passed;
- frontend lint passed;
- Python compile passed for the local API.

Current input/runtime modules:

- `src/estrattore/batch_storage.py` handles imported PDF batches;
- `src/estrattore/runtime_paths.py` owns runtime paths;
- `scripts/registry_api.py` exposes local backend endpoints;
- `scripts/local_app.py` serves the local app.

### Processing And Review

Processing is separate from review.

Conceptual split:

- OCR process/job: technical run with progress, errors, source documents, manifests;
- review queue: user-facing groups of invoices to revise;
- archive/register: validated output.

Review groups are globally progressive and visible as `Revisione 001`, `Revisione 002`, etc. New processing runs append new review groups instead of restarting numbering.

Implemented direction:

- document manifests stay under `manifests/process_*/documents/doc_XXXX`;
- user-facing review queue lives under `manifests/_review_queue/review_batch_XXXX`;
- review entries point back to document manifests through explicit `base_path` references;
- legacy `process_*/review_batches` remain readable during transition.

### Persistence Rules

Working data is persistent. Closing the app must not discard imported files, generated manifests, review groups, or manual edits.

Completed invoices leave the review queue and enter the invoice register/archive. Internal duplicated working PDFs should be removed once saved/discarded when they are no longer needed.

## UI Architecture Notes

The viewer is a Vite/React SPA backed by the local Python API.

Important UI directions:

- keep all user-facing text Italian;
- follow `mystudio-platform` visual language where relevant;
- avoid duplicated information in the same view;
- separate review, archives, invoice register, customer registry, and OCR process history;
- large management tables should share table infrastructure and virtualization where practical;
- after UI changes run build and lint, and use Playwright screenshots when available and useful.

Current major pages/views:

- invoice review;
- customer registry/anagrafica;
- PDF archive;
- invoice register;
- OCR process history.

## Customer Registry / Anagrafica

Implemented direction:

- the user can load one customer registry at a time;
- the app stores it in runtime storage;
- Excel import is converted to JSON for frontend work;
- frontend displays and edits a table based on JSON records, not the original Excel sheet;
- Excel export should match the app JSON-facing shape, excluding operational warning columns unless explicitly needed;
- after saving registry edits, suggestions and invoice enrichment should use the updated in-memory registry.

Important fields currently used:

- `codice_anagrafica`;
- `nominativo`;
- `codice_fiscale`;
- `partita_iva`;
- `tipologia`;
- `ultima_modifica` when present in the original file, because it is user data and must be preserved.

Registry-assisted invoice fields:

- issuer group: `codice_fiscale_emittente`, `nome_emittente`, `p_iva`;
- condominium group: `codice_fiscale`, `condominio`.

When a confident registry match is found, sibling fields in the same group are filled from registry data if valid. Fields replaced by registry enrichment keep the original scan value available through the eye icon.

Conflict behavior:

- if a fiscal code maps to multiple registry rows with different fills, the related field is marked as conflict and the user must choose;
- conflict indicators should explain themselves on hover;
- suggestions must come from the registry only, not from arbitrary already-entered invoice values.

Registry cleaning direction:

- import must stay import only;
- cleaning is an explicit action from the registry page;
- automatic deletion candidates must be shown and confirmed;
- report must group deleted rows with kept rows;
- report export must be real Excel;
- conservative rule: different `codice_anagrafica` usually means review, except very narrow cases where all business identity fields are equivalent and one row is clearly only a code/name artifact.

## Invoice Register And Archive

Invoice register and PDF archive are separate concepts.

Invoice register:

- table of saved invoice data;
- should behave like the customer registry table: readable, editable, searchable/filterable over time;
- should validate fields with the same domain validator used in review;
- rows with missing required fields are highlighted until completed;
- opening the invoice PDF from a register row should use a small preview window, not a new browser tab.

PDF archive:

- stores finalized split invoice PDFs;
- stores archived source PDFs separately;
- source files are visible in the source archive tab/page;
- source archive can allow reprocessing source PDFs, while group-level reprocess belongs to process history/debug workflows.

Current archive naming direction:

```text
f<stable-id>_<issuer>_<condominium>_<date>.pdf
```

If a file name collides, use Windows-style suffixes such as `name(1).pdf`, `name(2).pdf`. Equal source files must still be archived as separate historical extraction events.

## OCR Processing Queue

Implemented direction:

- processing jobs can be queued;
- one active job processes at a time;
- jobs expose progress as `current/total` and estimated remaining time;
- cancellation is cooperative: when the user cancels, warn that the current PDF may finish before the job stops cleanly;
- cancelled/incomplete working outputs must be cleaned so half-generated review data is not presented as complete.

Known UX decisions:

- process cards should not crowd the invoice review sidebar;
- process supervision belongs in a dedicated history/status page;
- the sidebar should show loading/import actions and review navigation, not bulky live job cards;
- more recent or active process information should be easy to see first.

Future process-history direction:

- persistent queue after app close;
- automatic resume policy for interrupted jobs needs careful design;
- dedicated process history page with complete job history.

Open issue for packaging phase:

- if the browser window is closed during development, the Python backend may continue running. The final launcher must own process lifetime and shut down backend/jobs according to the app lifecycle policy.

### Preemptible OCR Worker Boundary

Status: worker isolation and cancellation were implemented and verified on
2026-08-20. The pause/resume portions of this historical design were superseded by
"Remove User Pause And Resume From OCR Jobs": source-PDF checkpoints are too coarse
to present pause as a useful user capability. References to pause below document the
earlier design and are not the current product contract.

#### General problem definition

The problem is not narrowly "how to kill Tesseract". The application needs a reliable control boundary around long-running, persistent document processing that:

- calls external executables;
- may write several intermediate files;
- must survive application restarts;
- must distinguish pause, cancellation, failure, and crash;
- must never expose incomplete results as reviewable invoices;
- must not leave child OCR processes running after their owner has stopped.

This is the broader problem of supervising preemptible work with durable state and atomic output publication. Cancellation latency, process-tree ownership, output integrity, crash recovery, and user-visible state are sibling concerns and must be designed together.

#### Current code diagnosis

The current queue persists `cancel_requested` and `pause_requested`, but `process_imported_batch()` checks these flags only before and after processing one source PDF. The document processor is a synchronous call to `debug_words_file()`. That function loads the full PDF and invokes the OCR/extraction pipeline synchronously. Individual OCR calls use `pytesseract.image_to_data()`, which starts and waits for a Tesseract subprocess internally.

Consequences:

- current cancellation latency is bounded by an entire source PDF, not by a page or OCR call;
- a large multipage PDF or table/crop fallback may delay cancellation for minutes;
- the queue thread cannot safely force-stop the active Python call;
- the existing `.working` directory and completion-marker strategy is already the correct basis for discarding interrupted output.

#### Required invariants

These invariants define correctness independently of implementation:

1. Original imported PDFs are never modified or deleted by cancellation.
2. A cancelled job publishes no review invoices from that job, including documents completed before cancellation.
3. A paused job preserves completed document outputs, discards only the interrupted document attempt, and resumes from that document.
4. A document becomes visible to downstream review logic only after an atomic completion boundary.
5. Cancel and pause requests become durable before the active worker is stopped.
6. Stopping a worker also stops every OCR subprocess it created.
7. A crash or power loss cannot make a partial document look complete.
8. Reopening the app reconciles persisted intent, completion markers, and temporary directories deterministically.
9. Cancellation latency has a measured upper bound suitable for the UI; it must not depend on source PDF length.
10. The design must remain valid if Tesseract is later replaced or supplemented by another OCR engine.

#### Primary-source findings

- Python `Popen.terminate()` uses `TerminateProcess()` on Windows, but it only controls the direct child process.
- Python documents that terminating a `multiprocessing.Process` does not terminate descendants and may corrupt shared pipes, queues, locks, or semaphores.
- Microsoft Job Objects manage a process tree as one unit. Child processes are associated by default, and `TerminateJobObject()` terminates all associated processes. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` also provides orphan prevention when the supervisor loses the job handle.
- Pytesseract already launches Tesseract with `subprocess.Popen` and supports a fixed elapsed-time timeout. That timeout protects against a hung OCR invocation, but it is not an externally controlled user cancellation token.

References:

- https://docs.python.org/3/library/subprocess.html#subprocess.Popen.terminate
- https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Process.terminate
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject
- https://github.com/madmaze/pytesseract/blob/master/pytesseract/pytesseract.py

#### Alternatives considered

**A. Add more cooperative flag checks inside the existing thread**

Check between pages, fallbacks, crops, and extraction phases.

- advantage: small code change and useful checkpoints;
- limitation: an active synchronous Tesseract invocation remains uninterruptible;
- limitation: future OCR engines may have different blocking behavior;
- verdict: useful as a secondary mechanism, insufficient as the ownership boundary.

**B. Replace selected pytesseract calls with a custom cancellable Tesseract runner**

Own each Tesseract `Popen`, poll a cancellation flag, and terminate it directly.

- advantage: fine cancellation around OCR calls;
- limitation: duplicates wrapper responsibilities such as temporary files, TSV output, configuration, cleanup, and packaging behavior;
- limitation: other blocking pipeline stages remain in the backend thread;
- limitation: tightly couples queue control to Tesseract;
- verdict: too narrow and too fragile as the main architecture.

**C. Run the whole queue job in one child worker process**

- advantage: isolates failures from the API process;
- limitation: pause/resume and progress checkpoints become coarse;
- limitation: one corrupt or stuck document owns the entire worker lifecycle;
- limitation: completed-document reuse requires more complicated recovery;
- verdict: better than a thread, but the isolation unit is too large.

**D. Run every OCR call in its own child worker process**

- advantage: finest interruption granularity;
- limitation: repeated Python process startup and serialization are expensive, especially on Windows `spawn`;
- limitation: too much orchestration for crop-heavy OCR;
- verdict: excessive granularity.

**E. Supervise one isolated worker process per source document**

The queue supervisor remains in the backend. It starts one explicit worker subprocess for the current source PDF. The worker owns only that document's `.working` directory and publishes it atomically when complete. On Windows, the worker and its Tesseract descendants are contained in one Job Object.

- advantage: cancellation latency no longer depends on document length;
- advantage: interruption cannot corrupt backend thread state;
- advantage: completed documents are natural pause/resume checkpoints;
- advantage: the boundary is OCR-engine independent;
- cost: requires a worker entry point, process supervision, durable result/error files, and Windows Job Object integration;
- verdict: selected architecture.

#### Selected architecture

Use an explicit **supervisor / document-worker** boundary.

The supervisor:

- owns queue ordering and durable job state;
- launches at most one document worker for the active job;
- records the worker identity and current document;
- reads progress/results from atomic files rather than a shared multiprocessing queue;
- owns the Windows Job Object containing the worker process tree;
- is the only component allowed to publish job-level review batches.

The document worker:

- receives an immutable request containing source path, isolated output path, OCR options, and first invoice number;
- processes exactly one source PDF;
- writes only inside `<document>.working`;
- writes a small atomic result/error record;
- renames the document output to its completed name only after all manifests and split PDFs are valid;
- never edits queue state or publishes global review batches.

The protocol avoids a shared `multiprocessing.Queue`. A force-stopped worker therefore cannot corrupt the supervisor's communication channel. Files are acceptable here because the payload is small, processing is serial, and the application already relies on durable filesystem state.

#### State semantics

**Cancel**

1. Persist `cancel_requested`.
2. Move the job to a visible `cancelling` state.
3. Stop the active worker process tree.
4. Delete the interrupted document `.working` directory.
5. Delete the entire job processing directory, including documents completed by this cancelled run.
6. Persist terminal state `cancelled`.
7. Start the next queued job.

This preserves the already approved all-or-nothing cancellation contract.

**Pause**

1. Persist `pause_requested`.
2. Move the job to `pausing`.
3. Stop the active worker process tree.
4. Delete only the interrupted document `.working` directory.
5. Preserve completed document directories and the run index.
6. Persist terminal scheduler state `paused`.
7. Start the next queued job.

Resume retries the interrupted document using the preserved next invoice number and completed-document markers.

**Application crash or forced shutdown**

- the Job Object must prevent OCR descendants from remaining alive after the owning runtime closes;
- a startup reconciliation pass removes incomplete `.working` directories;
- a job previously persisted as `processing`, `pausing`, or `cancelling` is reconciled from its intent and completion markers before it can run again;
- no result is inferred solely from a stale in-memory or persisted status string.

#### Implementation flow

1. Define the worker request/result schemas and state-transition table without changing OCR behavior.
2. Extract single-document processing behind a callable boundary that preserves current manifest output exactly.
3. Add a developer-only document worker entry point and atomic result/error records.
4. Implement a platform-neutral supervisor interface (`start`, `poll`, `stop_tree`, `wait`).
5. Implement the Windows process-tree owner with Job Objects; keep platform-specific code isolated.
6. Change `process_imported_batch()` to supervise one document worker at a time while preserving numbering, resume, and review grouping.
7. Implement `cancelling` and `pausing` transitions in the API and expose clean Italian UI messages.
8. Add startup reconciliation for interrupted workers and stale temporary directories.
9. Only after correctness is proven, add cooperative checkpoints inside the worker to reduce hard terminations during normal pause/cancel.

The migration must keep the existing in-process `document_processor` injection available for focused unit tests until equivalent worker contract tests exist. It must not create two competing production paths permanently.

#### Verification: build eyes, not only unit tests

Required test scenarios:

- cancel a queued job: no worker starts and no output remains;
- cancel during a deliberately slow Tesseract invocation: UI reaches `cancelled` within the latency budget and no Tesseract process remains;
- cancel after two completed documents: all outputs from that job disappear and no review is published;
- pause during the third document: first two remain complete, third `.working` disappears, next queue job starts;
- resume the paused job: third document is retried, invoice numbering remains progressive, no duplicate reviews appear;
- force-kill the backend while OCR runs: no child OCR process remains after launcher/runtime shutdown;
- simulate power loss by leaving `.working` and stale `processing` state: startup reconciliation produces a deterministic queued, paused, or cancelled state;
- worker failure: error belongs to the current document, the supervisor remains healthy, and partial output is not published;
- compare manifests from old in-process and new worker execution on the fixed extraction corpus;
- measure process startup overhead and cancellation latency on Windows 10 and Windows 11 class environments.

Visual verification must include the process-history states `in lavorazione`, `annullamento in corso`, `in pausa`, `annullato`, and the immediate transition to the next queued job.

#### Critical review pass 1: data integrity

The selected boundary is acceptable because a force-stopped worker cannot write into completed document directories or global review indexes. The remaining risk is a termination between final rename and result publication. Recovery must treat the completion marker and validated document directory as source of truth and make result handling idempotent.

#### Critical review pass 2: Windows and packaging

Killing only the Python worker is rejected because Tesseract can become an orphan. Windows Job Objects are the correct native process-tree primitive for the target platforms. The Job Object wrapper must be small, independently tested, and hidden behind an interface so packaging code and OCR code do not call Win32 APIs directly. The final launcher ownership policy remains a separate packaging concern, but it must close or terminate active worker Job Objects during shutdown.

#### Critical review pass 3: UX, performance, and maintainability

One Python worker startup per source PDF adds overhead, but OCR work dominates that cost and the chosen unit avoids process startup for every crop. This must still be measured before acceptance. The UI can honestly show fast cancellation and pause because the scheduler does not wait for the current PDF. The design remains usable with a different OCR engine because orchestration controls the document worker, not Tesseract internals.

#### Decision and reopening criteria

Proceed with one isolated worker per source document, durable file-based result exchange, atomic document publication, and Windows Job Object containment.

Reopen the decision if measurements show either:

- worker startup materially degrades short-document throughput;
- the packaged runtime cannot reliably assign the worker tree to a Job Object;
- manifest parity cannot be maintained without duplicating the extraction pipeline.

#### Implementation closure

Implemented components:

- `estrattore.document_worker` defines the versioned request/result protocol and owns atomic single-document publication;
- `estrattore.document_supervisor` starts one worker per source PDF, uses a start gate, polls durable pause/cancel intent, and exchanges results through atomic files;
- `estrattore.process_tree` isolates platform process-tree control and uses a Windows Job Object with `KILL_ON_JOB_CLOSE`;
- `process_imported_batch()` uses the isolated worker in production while retaining injected in-process processors only for focused unit tests;
- the queue persists explicit `pausing` and `cancelling` states and reconciles them deterministically after restart;
- the frontend represents active transition states without leaving progress indicators on terminal jobs.

Important implementation details:

- the worker cannot begin OCR until the supervisor has attached it to the process tree;
- a completion marker plus manifest is the source of truth if the worker exits after committing output but before writing its result record;
- completed document output is idempotent and is not processed again;
- cancellation deletes the whole processing run; pause deletes only the interrupted `.working` attempt;
- successful worker control files are removed, while failed attempts retain diagnostics.

Verification completed:

- 215 Python tests passed;
- worker cancellation latency test passed;
- Windows descendant-process termination test passed;
- pause/cancel persistence and restart reconciliation tests passed;
- frontend build passed;
- frontend lint passed;
- Playwright screenshots covered the Cronologia page at desktop and wide viewports, exposed one stale cancellation-indicator bug, and passed after its correction;
- screenshots and isolated UI runtime were deleted after inspection;
- one real one-page PDF from internal app storage was processed through the real worker entry point in 7.8 seconds, producing one invoice, a valid manifest, and a completion marker; its temporary output was deleted.

Critical closure review:

- data integrity is stronger because only complete marked documents can be reused or published;
- queue state is no longer coupled to a blocking OCR call in the backend thread;
- stopping a worker stops Tesseract descendants on Windows rather than orphaning them;
- the OCR extraction code and manifest format were not duplicated or rewritten;
- the additional process startup occurs once per source PDF, not once per page or crop.

Residual limit:

- development mode is complete because it launches workers with `python -m estrattore.document_worker`;
- the future frozen launcher must route its internal `--document-worker` mode before packaging can be considered complete. This belongs to `docs/packaging_plan.md`, not to OCR queue behavior.

This point is complete and does not require a redesign before normal application testing.

## Page Reordering In Review

Chosen minimal workflow:

- provide `Riordina pagine` action;
- inside it expose only the meaningful operations: move first page to previous invoice, move last page to next invoice;
- this avoids building a heavy drag-and-drop page sorter before the real workflow proves it is needed.

These actions must be deliberately placed so they are available but not easy to click accidentally.

## OCR / Table Extraction

Current OCR direction:

- default OCR uses Tesseract with Italian language support where configured;
- quality fallback uses crop/table OCR rather than expensive best-model fallback by default;
- Tesseract best remains useful for future tests but is not the default path;
- table crop visualization exists and table crop tokens can be used by economic extraction.

Table extraction principles:

- avoid document-specific measurements;
- infer table cells from detected/implied line structure;
- prefer general geometric rules over local left/right workarounds;
- polygons/cell crops are used to avoid confusing OCR with table lines;
- table-based OCR should feed extraction only as a fallback or when base extraction lacks needed values.

Known limitation:

- table polygon precision is acceptable but not perfect. Do not keep stacking local exceptions; improve line/cell recognition when needed.

## Extraction Logic Decisions

### Invoice Number

Problem fixed:

- a body reference like `G.U.23` could beat the real invoice number `87` near `NUMERO DOCUMENTO`.

Decision:

- fuzzy matching is fallback evidence;
- exact compound anchors must beat fuzzy/generic anchors;
- selection uses anchor evidence quality before numeric score.

Implemented in `src/estrattore/invoice_number_extraction.py` through `anchor_quality`.

### Invoice Date

Problem fixed:

- legal/reference dates near `Contribuenti forfetari L.190 del ...` could be selected as invoice date;
- billing period dates could look like valid invoice dates.

Decision:

- `data documento` is a strong positive anchor;
- legal/reference context and billing-period context are negative contexts for invoice-date selection;
- old manifests without explicit `negative_context` may infer it from nearby text in the viewer layer.

### Economic Values

Current domain rule:

- `Ritenuta` shown to the user is a percentage, not a free amount;
- allowed visible values are `0%`, `4%`, `10%`;
- the withholding amount is still kept internally because accounting validation needs `totale fattura - importo ritenuta = totale netto fattura`;
- if extracted values do not respect the equation, do not promote them as final invoice fields;
- false positives are worse than missing values because the user can fill missing fields manually.

Implemented direction:

- `economic_summary_extraction` limits withholding percentages to `0`, `4`, `10`;
- `viewer_invoice_segmentation` promotes withholding percentage only from resolved rows with `math_check == ok`;
- direct unresolved amount targets are not accepted as the final withholding field;
- split money fragments from table crops can be repaired conservatively, e.g. `56` + `21` -> `56,21`, only when adjacent on the same visual row and later validated by accounting equations.

## Internal Developer Tooling

### Reprocess Existing Review

Purpose: internal extractor debugging only, not an end-user feature.

Script:

```powershell
.\.venv\Scripts\python.exe scripts\reprocess_review.py --review 1 --invoice 2
```

Behavior:

- reprocesses the source document connected to the selected invoice;
- updates the same review batch instead of creating a new one;
- uses a working directory before swapping generated manifests;
- does not touch saved invoice archive, customer registry, or external original files.

Caution:

- if one large source document generated invoices across many review groups, reprocessing one invoice may regenerate the shared document manifest and affect adjacent invoices from that document.

### Playwright UI Checks

Playwright is development tooling only. It should not be packaged into the final app.

Expected tool location:

```text
tools/ui-checks/
```

Screenshots must be temporary and deleted after inspection unless explicitly kept.

## Verification Baseline

Common checks used recently:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_processing_storage.py tests\test_invoice_registry_storage.py tests\test_registry_api_processing_queue.py
.\.venv\Scripts\python.exe -m pytest tests\test_estrattore.py -k "invoice_date"
.\.venv\Scripts\python.exe -m pytest tests\test_estrattore.py -k "invoice_number"
.\.venv\Scripts\python.exe -m pytest tests\test_estrattore.py -k "economic_summary or ritenuta"
npm --prefix apps/viewer run build
npm --prefix apps/viewer run lint
```

Known environmental issue:

- pytest may warn or fail to write `.pytest_cache` because of Windows permissions. Treat that separately from code correctness.

## Active / Open Work

### Remove User Pause And Resume From OCR Jobs

#### Theoretical problem

The application currently exposes pause/resume as if it had a useful fine-grained
checkpoint. The real durable boundary is one complete source PDF. For a large PDF,
pausing discards the interrupted document attempt and later repeats the whole PDF.
This makes the feature technically correct but misleading and operationally costly.

The broader requirement is that every user-visible process control must correspond
to a persistence guarantee the application can explain and uphold.

#### Resolution theory

Until page-level OCR checkpoints exist, the honest process model is:

```text
queued -> processing -> completed
                     -> cancelling -> cancelled
                     -> error
queued -> removed
```

Crash recovery remains an internal durability mechanism and must not be confused
with user-controlled pause/resume. Completed source-document markers may still be
used to avoid repeating documents after an unexpected application interruption.

Legacy `paused` or `pausing` jobs must not restart automatically after upgrade.
They are migrated conservatively to `cancelled`, with their incomplete processing
run removed through the same app-owned cleanup boundary used by cancellation.

#### Chosen approach

This is a final choice for the current checkpoint model:

- remove pause/resume from the worker, storage service, API contract, and UI;
- keep cancellation interruptible at the source-PDF worker boundary;
- keep queue removal available only before processing starts;
- keep internal crash resume through completed-document markers;
- normalize persisted legacy pause states to cancelled during startup reconciliation.

Page-level OCR caching can reintroduce a truthful pause feature later as a separate,
explicitly designed capability.

#### Micro-steps

1. Remove pause callbacks and exceptions from document supervision and processing.
2. Remove pause state, endpoints, and transitions from the persistent queue service.
3. Remove pause/resume controls and copy from the React application.
4. Replace pause tests with cancellation, queue-removal, and legacy-migration tests.
5. Run targeted Python tests, frontend build/lint, and inspect the processing page.

#### Status

Completed on 2026-08-21.

#### Implementation result

- document supervision now accepts only a cancellation signal;
- processing storage no longer defines or raises pause-specific exceptions;
- the local API no longer exposes pause/resume endpoints, fields, or normal states;
- active work can only be cancelled and queued work can only be removed;
- the frontend no longer displays pause/resume controls, progress, labels, or styles;
- `Riprocessa gruppo` is available only for terminal jobs, never while cancellation
  is pending;
- old persisted `pausing` and `paused` jobs are migrated to `cancelled`, their
  app-owned incomplete run is removed, and they are never restarted automatically;
- internal crash recovery still reuses complete source-document markers and is not
  exposed as user pause/resume.

#### Verification

```powershell
.\.venv\Scripts\python.exe -m py_compile src\estrattore\document_supervisor.py src\estrattore\processing_storage.py scripts\registry_api.py
.\.venv\Scripts\python.exe -m pytest tests\test_document_worker.py tests\test_processing_storage.py tests\test_registry_api_processing_queue.py -q -p no:cacheprovider
npm --prefix apps/viewer run build
npm --prefix apps/viewer run lint
npm --prefix tools/ui-checks run check -- --url http://127.0.0.1:8765 --scope "cronologia-senza-pausa" --click "Cronologia"
```

Results:

- Python compilation passed;
- 35 targeted backend tests passed;
- frontend production build passed;
- frontend lint passed;
- Playwright covered Cronologia at desktop and wide viewports;
- temporary screenshots were inspected and deleted;
- no pause/resume control remains in the current UI or API contract.

#### Critical closure review

The resulting state machine is smaller and matches the guarantees provided by the
current source-PDF commit boundary. Cancellation still stops the isolated worker
process tree quickly and removes the whole incomplete processing run. Queue removal
does not start OCR. No external user file is deleted by either operation.

The deliberate residual limitation is that an unexpected app crash can recover from
completed source documents, while a user cannot suspend work intentionally. A pause
feature should be reconsidered only after page-level OCR checkpointing has a versioned
cache and explicit invalidation rules.

### Packaging Plan

Packaging is intentionally moved to `docs/packaging_plan.md`. Do not keep future packaging detail in this notebook unless it is the current active task.

### Runtime/User Workflow

Open items:

- define final automatic resume policy for interrupted OCR jobs;
- finish dedicated process history page behavior if not already completed;
- ensure all destructive actions use app-styled confirmation dialogs;
- ensure working directories are cleaned after save/discard/cancel where duplication is no longer useful.

### Manifest And Review Queue Contract

Current problem:

- the app still contains multiple generations of the review manifest model;
- old runtime data uses legacy `process_*/review_batches/*` folders;
- the newer direction expects a single user-facing review queue under `manifests/_review_queue`;
- saved invoice records can contain manifest field kinds such as `economic_summary.totale_fattura.value`, while registry/export code may still look for canonical app fields such as `totale_fattura`;
- this makes a value look visible in review but empty in the invoice register/export.

Theoretical framing:

- this is not only a manifest bug;
- it is a data-contract problem between extraction output, review UI state, saved invoice registry, and archive/export views;
- extraction manifests may stay rich and technical, but the application layer must expose stable canonical invoice fields.

Chosen incremental approach:

- keep `viewer_manifest.json` as the technical extraction artifact for now;
- keep review batches as pointers to document manifests, but add a user-facing action to delete all review queues when stale generations must be discarded;
- normalize field aliases at the invoice registry boundary, so saved/exported records can read both old simple fields and current manifest economic fields;
- do not delete invoice registry records, archived PDFs, source PDFs, imported batches, or external user files when deleting review queues.

Micro-steps:

1. Add canonical field alias reading for invoice registry values.
2. Add backend review-queue cleanup limited to app-owned manifest review folders.
3. Add an app-styled confirmation action near `Da revisionare`.
4. Reload review state after deletion.
5. Verify with targeted Python tests and frontend build/lint.

Implementation result:

- invoice registry export/read paths now resolve economic field aliases from both canonical app names and technical manifest names;
- the frontend invoice register does the same when displaying saved rows;
- backend exposes review cleanup through a dedicated review-batch clearing function;
- the UI exposes the cleanup behind a three-dot menu near `Da revisionare` and an app-styled destructive confirmation dialog;
- cleanup removes review queues and clears review references from processing indexes, but preserves document manifests, invoice registry, archives, imported batches, and external files.

Checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_processing_storage.py::test_clear_all_review_batches_removes_review_queue_without_documents tests\test_invoice_registry_storage.py::test_invoice_registry_excel_reads_manifest_economic_field_aliases -q
.\.venv\Scripts\python.exe -m py_compile src\estrattore\processing_storage.py src\estrattore\invoice_registry_storage.py scripts\registry_api.py
npm --prefix apps/viewer run build
npm --prefix apps/viewer run lint
```

Outcome:

- targeted Python tests passed;
- Python compile passed;
- frontend build passed;
- frontend lint passed.

Residual architectural issue:

- the app still has legacy review batches in existing user runtime data until the user explicitly clears them;
- future extraction runs should converge on one review-queue model and avoid creating new legacy `process_*/review_batches` folders.

### Invoice Register

Open items:

- keep invoice register and customer registry table behavior coherent;
- move domain validation toward a shared backend/frontend model when practical;
- ensure edited register rows validate fiscal codes, dates, missing required fields, and economic consistency.

### Economic Extraction

Open items:

- evaluate real reprocessed invoices after the percentage/strict-math change;
- consider a unified evidence-quality model for economic anchors, similar to invoice-number `anchor_quality`.

Latest fix:

- `invoice-003` had `totale=56,22`, withholding amount `1,84`, and net `54,38`, so the math was correct;
- the visible withholding percentage was empty because the percentage was not inside the label text `RITENUTA D'ACCONTO`;
- the OCR had the percentage as a same-row value `4,00` under a `%` column;
- `economic_summary_extraction` now reads allowed withholding percentages from a same-row percent column when the withholding amount is otherwise mathematically coherent;
- runtime review 1 / invoice 3 was reprocessed and now shows `Ritenuta % = 4%`.

Generalization:

- if the withholding percentage is still unknown after direct anchors and same-row percent-column reading, the resolver now tries to infer it from accounting values;
- allowed percentages remain strictly limited to `0`, `4`, `10`;
- inference is accepted only when the withholding amount is known or derivable from `totale fattura - totale netto`;
- non-zero percentage inference requires an `imponibile` base, because `totale fattura` usually includes VAT and is not the withholding base;
- this avoids inventing `4%` from any arbitrary total/net difference while still recovering cases where the accounting base is available.

Latest follow-up:

- `invoice-004` had the same visible symptom as another invoice: missing withholding percentage and duplicated net amount as total;
- OCR had split the true total `56,21` into two adjacent tokens: `56` and `21`;
- the previous split-money repair existed only for table-crop tokens, so full-page extraction could still choose the nearby net value `54,37` twice;
- `economic_summary_extraction` now applies conservative split-money repair to page-level economic candidates too;
- the repair is accepted through normal accounting validation, not as a standalone value guess;
- when the withholding percent column is unreadable, the resolver may infer `0%`, `4%`, or `10%` from a real nearby base amount token if applying that percentage reproduces the known withholding amount;
- this handles cases where the taxable base token is present in the row but was not promoted as a formal `totale_imponibile` field because the selected economic region was too narrow;
- runtime review 1 / invoice 4 was reprocessed and now shows `Totale fattura = 56,21`, `Ritenuta % = 4%`, `Totale netto fattura = 54,37`, `math_check = ok`.

### OCR Improvement

Open items:

- do not switch to PaddleOCR by default because previous tests suggested it is too heavy;
- explore Tesseract preprocessing/config improvements before considering retraining;
- keep expensive OCR modes behind quality gates.

### Archive Deletion Contract

The archive UI now supports selecting one or more rows and deleting them from:

- invoice PDF archive;
- source PDF archive;
- invoice register.

The UI uses a checkbox at the start of each row and one disabled/enabled trash action in the page toolbar. The header checkbox selects or clears every currently filtered row. It does not add a separate actions column to the data tables.

Virtualized rows are explicitly positioned from `top: 0`; the header height is applied only by the row transform. This prevents the first row from receiving the header offset twice.

The backend owns deletion semantics through one endpoint and never touches user-selected originals outside the application runtime directory.

Deletion rules:

- deleting register rows hides only the accounting/export projection and preserves both PDF archives;
- deleting invoice PDF rows removes only the selected split PDFs and preserves register rows and source PDFs;
- deleting source PDF rows removes only the selected source PDFs and preserves register rows and split invoice PDFs;
- projection visibility is persisted independently on the shared technical record, so each page remains queryable after a sibling projection is deleted;
- discarded review records, imported source files, manifests, and external originals are not removed by archive deletion.

The confirmation dialog must explain the scope before every destructive action. The frontend refreshes the archive/register from the backend after completion instead of mutating only the visible table.

Verification:

- targeted invoice registry tests passed, including explicit non-cascade checks for register deletion;
- frontend build passed;
- frontend lint passed.

Residual point:

- the archive deletion actions still need a visual check at narrow window sizes and with multiple invoices sharing one source PDF.

## Last Completed Notebook Maintenance

This notebook and `.agents/rules_workflow.md` were converted to English as Codex-facing documents.

Reason:

- reduce token overhead in future sessions;
- make internal reasoning documents easier for Codex to parse;
- keep Italian only where it matters: user-facing product experience and chat with the user.

No application code behavior was changed by this notebook maintenance.

## Point 0 Part XIII - Separate Process Supervision, Source Ownership, And Review Output

Status: completed.

## Point: Economic values in review-003 invoice 032 and safe reprocessing identity

### Problem definition

The persisted invoice displayed as `Fattura 032` contained correct OCR evidence
for the complete accounting sequence but exposed only the net amount. The same
investigation also revealed an independent persistence invariant: reprocessing a
document must never map two existing review entries to the same regenerated
invoice identity when global invoice numbers shift.

### Evidence and diagnosis

The saved OCR representation contains, on the same page and aligned value column:

- `Imponibile 75,00`;
- `Imposta 10% 7,50`;
- `Totale 82,50`;
- `Ritenuta d'acconto -3,00`;
- `Netto da pagare 79,50`.

The old manifest predated the existing equation-based total recovery. Running the
current resolver against the saved tokens, without OCR, produced `82.50 - 3.00 =
79.50`, a `4%` withholding rate, and `math_check=ok`. No new extraction rule was
therefore needed.

During the targeted reprocess, the historical document had old technical IDs
`invoice-031, invoice-032`, while regeneration correctly produced
`invoice-032, invoice-033` from display number 32. The mapper preferred exact ID
equality before document order, so both old entries converged on `invoice-032`.

### Chosen correction

Within one source document, regenerated invoices are associated with existing
invoices by stable document order. The regenerated manifest is then rewritten to
retain the previous technical IDs. Display number and title may change, but the
technical identity used by drafts and review references must not change. Exact
technical-ID matching remains only a fallback when an old ID is absent from the
old manifest ordering.

Existing drafts use a three-way rebase: a field that differs from the old
manifest is treated as a user edit and retained; a field equal to the old
manifest is refreshed from the new extraction. This prevents stale extracted
blanks from hiding corrected results without discarding real manual work.

The affected revision-003 projection was repaired atomically:

- the manifest, review index, and viewer index retain technical IDs
  `invoice-031` and `invoice-032`, displayed as Fattura 032 and Fattura 033;
- the existing draft remains attached to stable ID `invoice-031`;
- user-entered draft fields were retained;
- the three economic fields were refreshed from the verified new manifest.

### Verification and closure

- focused shifted-ID mapping, stable-ID, and draft-rebase regressions passed;
- the complete processing-storage and reprocess-script suites passed: `35 passed`;
- all repaired JSON files parse with the same UTF-8 JSON parser used by Python;
- review-003 contains 15 entries and 15 distinct technical IDs;
- the draft for displayed Fattura 032 contains total `82.50`, withholding `4%`,
  and net total `79,50`.

The result is satisfactory because the OCR evidence was not distorted with a
duplicate rule, the persisted user draft was preserved, and the identity bug was
fixed at its general cause rather than patched only in the affected JSON.

Status: completed.

## Strong Invoice Identity Changes In Recurring Documents

### Theoretical problem

Recurring invoices can legitimately repeat supplier, customer, description, and
amount across periods. A segmentation rule based only on the number of changed
fields therefore treats stable business attributes as if they had the same
identity value as the invoice number. Conversely, any number merely mentioned in
a payment description must not become a split boundary.

The general problem is evidence hierarchy in document segmentation: a trusted
document identifier must dominate several repeated descriptive attributes, while
an unanchored or fuzzy numeric mention must remain weak evidence.

### Resolution theory and chosen approach

Classification: **final choice**.

Use the invoice-number extractor's existing anchor quality as provenance. A page
starts a new invoice independently of the generic changed-field threshold when:

- both the current invoice and page have different invoice numbers;
- the page candidate was selected as the invoice number;
- its multi-word invoice anchor is exact and strongly positioned
  (`anchor_quality >= 4`).

A changed date corroborates the decision but is not required. Fuzzy, generic,
or unanchored numbers cannot activate this rule. After automatic or manual
segmentation, shared-payment reconciliation is rerun so a bank page explicitly
referencing both invoice numbers belongs to both invoices.

### Micro-steps

1. Preserve invoice-number anchor quality in the viewer manifest.
2. Pass page-level candidate provenance to the start-boundary decision.
3. Add a strong identity-change reason that bypasses only the generic change
   count, not candidate validation.
4. Reconcile shared payment pages after a manual split as well as automatic
   segmentation.
5. Test recurring equal-value invoices, weak-number non-splits, and a shared
   bank page.
6. Apply the split to review 002 using the atomic review mutation path and
   inspect numbering, drafts, and page membership.

### Implementation outcome

- invoice-number `anchor_quality` is now retained in the viewer manifest instead
  of being discarded at serialization;
- a changed invoice number with selected, exact, strongly positioned anchor
  quality starts a new invoice without requiring three generic field changes;
- weak or fuzzy number changes retain the previous conservative threshold;
- explicitly anchored short references such as `n.202 del 09-12-24` are linked
  to both `202` and the year-qualified identity `202/2024`;
- shared-payment reconciliation runs after manual splits as well as automatic
  segmentation;
- source-page preservation is validated before shared page membership is added,
  retaining strict split safety without rejecting intentional shared pages.

### Verification and closure

- viewer segmentation family: `10 passed`;
- segmentation edit family: `8 passed`;
- persistent review mutation segmentation family: `4 passed`;
- Python compilation passed;
- an independent OCR run on the real five-page source produced page groups
  `[1, 2]`, `[3, 5]`, and `[4, 5]`;
- the real page-4 decision reports two changed signals and the decisive reason
  `numero_fattura_forte_diverso`, with selected anchor quality `4`;
- the temporary diagnostic output was deleted;
- review 002 was updated through the atomic mutation path: invoice 018 now owns
  pages 3 and 5, the new invoice 019 owns pages 4 and 5, and both use debit date
  `2025-01-16`;
- all later display numbers are contiguous, with no duplicate or missing number;
- no draft belonging to the split document remained to be invalidated.

Residual limit: shared page memberships can be non-contiguous by design. The
current boundary editor validates contiguous source partitions before adding the
shared membership; a later edit of an already shared non-contiguous membership
still requires the dedicated shared-page model planned elsewhere.

Status: completed.

## Canonical Payment Date From Bank Records

### Theoretical problem

A bank receipt may expose several valid dates for the same transfer. A generic
proximity search cannot decide their business meaning, and it may also miss a
label placed at the opposite end of a wide row. The extraction contract must
therefore distinguish the payer-side payment event from beneficiary-side credit
and infer row membership from layout rather than a fixed horizontal radius.

### Chosen approach

Classification: **final choice**.

Define `pagata_il` as the debit date on the payer's account. An exact
`Data di addebito` label on the candidate row is first-class positive evidence;
an exact `Data di accredito` label is an exclusion for this field. Existing
execution-date and transfer-context rules remain fallbacks when the receipt does
not expose a debit date.

### Micro-steps

1. Build payment-date context from the candidate text and vertically aligned OCR
   spans across the full row.
2. Prioritize debit-date candidates and reject credit-date candidates.
3. Apply the same scoring contract to direct and shared-payment resolution.
4. Add regressions for receipts containing both dates and credit-only evidence.
5. Reprocess review 002 invoice 017 and inspect the persisted result.

### Implementation outcome

- payment-date scoring now receives the complete page layout;
- vertically overlapping spans provide full-row context even when the label and
  value are at opposite horizontal extremes;
- `Data di addebito` receives explicit priority;
- `Data di accredito` is rejected as a `pagata_il` source;
- direct invoice resolution and shared-payment resolution use the same rule;
- the existing execution-date logic remains available when no explicit debit
  date exists.

### Verification and closure

- four focused payment-date regressions passed, including both-date and
  credit-only cases;
- the broader extractor run completed 161 tests before seven unrelated tests
  failed because the local pytest temporary directories were inaccessible;
- Python compilation passed;
- applying the resolver to the real review-002 source selected `2025-01-17` on
  page 2 with the bbox aligned to the debit-date value;
- review 002 document `doc_000009` was reprocessed atomically; because invoice
  017 and invoice 018 share that source document, both manifest entries were
  regenerated;
- the persisted invoice-017 manifest now contains `pagata_il = 2025-01-17`;
- the existing frontend draft reconciliation allows this value to replace the
  old unresolved blank placeholder while preserving actual user edits.

Residual limit: wording variants caused by degraded OCR are not added until
they are observed in real documents; the current rule intentionally recognizes
the explicit normalized phrases rather than speculative synonyms.

Status: completed.

## Runtime Storage Lifecycle And Residue Collection

Status: completed.

### Theoretical problem definition

Long-running desktop applications continuously create durable business data,
derived operational projections, transaction journals, and temporary write
files. These object classes have different ownership and retention rules. If a
temporary or derived object has no explicit terminal transition, normal use and
interrupted writes accumulate storage indefinitely even when the user-facing
workflow is complete.

The general problem is lifecycle-aware garbage collection for app-owned local
storage. Deletion must be based on positive evidence about object type and
terminal state, never merely on age, location, or a missing neighboring file.
The collector must also distinguish a review projection intentionally retired
after all invoices were finalized from one lost during a crash and eligible for
recovery.

### Resolution theory and best practice

Use explicit lifecycle state for durable objects and disposable naming contracts
for temporary objects:

- canonical archives and accounting records are durable and never collected by
  operational cleanup;
- review directories are derived projections and are retired after their last
  pending invoice is saved or discarded;
- process indexes retain a minimal closed-review ledger, preserving numbering and
  preventing recovery from recreating intentionally retired projections;
- atomic-write files named `.<target>.writing` are uncommitted by definition;
- stale temporary writes are collected only inside the app-owned runtime root,
  only when their filename matches the exact contract, and only after a grace
  period that excludes a concurrent or recently interrupted write;
- startup reconciliation is idempotent and completes lifecycle transitions that
  may have stopped between durable state publication and physical deletion.

Classification: **final choice**. The process index is the durable lifecycle
authority; review directories remain rebuildable operational projections. The
age threshold is only a concurrency guard for files whose temporary type is
already established by name, not the evidence used to classify arbitrary data
as disposable.

### Crash boundaries and invariants

Review retirement must tolerate:

1. crash before the final invoice state is written: the review remains active;
2. crash after zero pending invoices but before closed state is recorded: startup
   reconciliation records closure and retires the directory;
3. crash after closed state but before directory deletion: startup removes the
   already-retired projection;
4. startup after retirement: recovery skips the explicitly closed review;
5. residual drafts: drafts not owned by any pending invoice are removed without
   touching active user work.

Atomic-write collection must tolerate another recent writer by leaving every
recent `.writing` file untouched. It must ignore symlinks, directories, files
outside the runtime root, and similarly named non-temporary files.

### Micro-steps

1. Extend process state with explicit retired review IDs while preserving review
   references for numbering and history.
2. Retire a review after its final pending invoice closes and remove its orphaned
   drafts.
3. Reconcile already-completed and partially retired reviews at startup.
4. Skip retired reviews during missing-projection recovery.
5. Add a conservative app-owned stale atomic-write collector and call it during
   startup before recovery.
6. Test normal closure, every relevant crash boundary, active draft protection,
   idempotence, stale/recent files, and recovery exclusion.
7. Run focused and extended persistence/API suites, then inspect and reconcile the
   real runtime without modifying archives or accounting records.

### Decision revision after real-runtime measurement

The first collector implementation recursively walked the entire runtime root.
The real cleanup took roughly thirteen minutes because it traversed large PDF and
OCR archives to find one 27 MB transaction temporary file. This is unacceptable
startup behavior and unnecessary filesystem exposure.

The collector is therefore restricted to exact directory-depth patterns beside
the application's known atomic JSON writers: process state, document state,
review projections and journals, staging indexes, invoice registry, source index,
and configuration. New atomic-write locations must add their target pattern to
this explicit ownership map. The collector no longer traverses PDF archives or
OCR output trees generically.

### Implementation outcome

- process indexes now persist `retired_review_batch_ids` while retaining their
  immutable review references for global numbering and history;
- a review projection is retired only when every contained invoice has the exact
  terminal status `saved` or `discarded` and exactly one process index owns its
  full base path;
- retirement records durable state first, removes drafts owned by the finalized
  invoices, and only then deletes the review directory;
- startup reconciliation completes zero-pending reviews left behind by an
  interruption, while missing-review recovery skips explicitly retired IDs;
- save and discard are idempotent after retirement, so retrying a request whose
  HTTP response was lost returns the existing durable record without requiring
  the deleted review projection or duplicating an archive;
- stale atomic-write collection uses a 24-hour grace period and exact app-owned
  path patterns; recent writes, external files, symlinks, and similarly named
  files are preserved;
- review reprocessing now republishes `document_complete.ok` inside the atomic
  replacement, restoring the document completion contract.

### Verification and real-runtime reconciliation

- focused lifecycle, persistence, and API suite: `58 passed`;
- extended worker, processing, PDF archive, invoice registry, and queue suite:
  `65 passed` outside the sandbox; the sandbox run exposed only the pre-existing
  pytest temporary-directory ACL failure;
- Python compile check: passed;
- frontend lint and production build: passed; no UI code changed;
- post-optimization real collector measurement: about `0.02 s` with no stale
  candidates, instead of the rejected recursive traversal;
- real review 001 was retired: its directory is absent, its process index records
  `review_batch_0001` as retired, and all 15 saved records and invoice PDFs remain;
- the 27 MB interrupted transaction `.writing` file was removed;
- reviews 002 through 024 remain active and review 002 still has 15 invoices and
  its two pre-existing user drafts;
- all eight review-002 manifests were checked against their review references and
  their missing completion markers were restored without rerunning OCR;
- temporary pytest storage created during verification was deleted.

### Critical review and residual limits

The result is satisfactory because operational projections now have a terminal
lifecycle, recovery can distinguish loss from intentional retirement, and HTTP
retries remain safe after physical cleanup. Durable archives and accounting data
stay outside every new deletion path.

The 24-hour grace period intentionally means that a recent interrupted
`.writing` file can survive until a later startup. This favors concurrent-write
safety over immediate disk reclamation. The path map must be extended whenever a
new atomic writer is introduced; this explicit maintenance cost is preferable to
recursively scanning or deleting unknown runtime content.

Status: completed.

## Accounting Equation Completion From Direct Regional Evidence

### Theoretical problem

Generic labels such as `Totale` and `Imposta` are unsafe as global anchors, but
become meaningful inside an already established accounting summary. The
resolver must recover an unlabeled member of the accounting equation without
turning every nearby amount into a field candidate.

### Chosen approach

When withholding and net total are both directly linked to their labels, derive
the expected gross total and promote it only if an equal money token exists
inside the same selected economic region. Preserve that token bbox and OCR text.
This combines semantic links, regional geometry, and the invariant
`gross - withholding = net`; no document-specific coordinates or generic global
anchor are introduced.

### Micro-steps

1. Match a derived gross total against regional OCR money tokens.
2. Preserve the matched token as the gross-total provenance.
3. Let the existing equation check promote withholding percentage and all three
   final fields only when the equation is exact.
4. Reproduce review-001 invoice-014 and inspect the real document without
   overwriting its persisted review.

### Implementation outcome

- the resolver now derives the expected gross total only when net total and
  withholding amount are both directly linked to their labels;
- the derived amount is accepted only when the same amount exists as an OCR
  money token inside the selected accounting region;
- the accepted value preserves the real OCR text and bbox, so the viewer can
  focus the evidence instead of displaying a location-free calculation;
- generic labels such as `Totale` and `Imposta` were not added as global
  anchors, avoiding collisions with taxable amount, VAT, subtotals, and other
  document totals.

### Verification and closure

- focused economic extraction tests: `15 passed`;
- Python compile check: passed;
- real review-001 invoice-014 diagnostic, without persistent writes, resolves
  gross `104,50`, withholding `4%`, and net `100,70` with `math_check=ok`;
- manifest-layer diagnostic creates a resolved gross-total target on the real
  `104,50` token bbox and exposes the resolved withholding percentage;
- review-001 invoice-014 was reprocessed persistently; its stored manifest now
  exposes gross `104.50`, withholding `4%`, and net `100,70`, while the review
  still contains all 15 invoices;
- the printed VAT amount `Imposta 10% 9,50` is not currently part of the final
  invoice field contract. Adding it is a separate schema/UI decision, not an
  anchor-only correction.

Status: completed.

## Invoice Date Semantics Must Be Row-Local

### Theoretical problem

Invoice pages often place several dates in a compact header: issue date, due
date, billing period, and legal references. A purely radial OCR neighborhood can
mix labels from adjacent rows and assign the wrong semantic role to an otherwise
valid date token. The general problem is therefore not date parsing alone, but
preserving row-local label/value relationships under imperfect OCR.

### Resolution theory and chosen approach

Row-local context is authoritative for negative semantic roles. In particular,
`entro il` marks a due date and must exclude that candidate unless the same row
also contains the issue-date marker `del`. Broader spatial context remains a
fallback for positive anchors that may be printed above a value, but it must not
override a contradictory same-row role.

The real review-001 invoice-008 also exposes a sibling OCR defect: the issue
date `22-05-2024` was read as `2205-2024`. Recover the missing day/month
separator only when the malformed token is on a row with a strong issue-date
anchor (`del`, `data documento`, or `data fattura`) and the reconstructed date is
calendar-valid. This keeps generic numeric parsing strict.

This is a healthy incremental choice: it improves semantic locality without
hardcoding the document geometry or weakening the global date normalizer.

### Micro-steps

1. Derive a bounded same-row token context from OCR geometry.
2. Use row context first for anchors and exclusively for negative date roles.
3. Recognize OCR-degraded forms of `entro il` within that row.
4. Recover compact issue dates only under a strong same-row anchor.
5. Add extraction and viewer-selection regression tests, including the explicit
   `del` plus `entro il` same-row exception.
6. Inspect the real invoice-008 candidates without overwriting the review.

### Implementation outcome

- candidate semantics now use a bounded same-row OCR context before falling
  back to the broader spatial neighborhood for positive anchors;
- negative date roles are row-local, and degraded OCR such as `ENmROlI` is
  matched conservatively to `entro il`;
- `entro il` excludes a viewer candidate completely unless `del` is present on
  the same row;
- compact `DDMM-YYYY` OCR is reconstructed only beside a strong issue-date
  anchor and only when it yields a valid calendar date;
- exact `del` now has enough weight to select a valid issue date by itself;
- the viewer no longer selects a negative-only date merely because no better
  candidate exists.

### Verification and closure

- focused invoice-date tests: `12 passed`;
- Python compile check: passed;
- real review-001 invoice-008 diagnostic, without persistent writes:
  `2205-2024 -> 2024-05-22`, score `130`, selected;
- the due date `21-06-2024` receives `entro il`, score `40`, not selected;
- rebuilding viewer selection over the real page data returns `2024-05-22` and
  the bbox of the `DEL` row;
- the wider extractor suite reached `155 passed`; seven unrelated temporary-file
  tests could not start because this execution environment denied access to the
  pytest temporary directory.

The persisted review manifest and its saved draft were intentionally not
overwritten by diagnostics. A document-level reprocess is still required before
the corrected value appears in the existing review, and the current full-snapshot
draft model may preserve an old non-empty extracted value. Treat draft rebasing
across developer reprocessing as a separate persistence concern rather than
weakening date extraction to accommodate stale UI state.

Status: completed.

## Review Draft Merge Against Newly Extracted Manifest Values

Status: reopened after design review.

### Theoretical problem definition

A review draft and an OCR manifest represent different layers of truth. The
manifest is the latest extraction baseline; the draft contains operator changes
that must survive reloads and reprocessing. Blindly replacing every manifest
field with the corresponding draft snapshot lets an old unresolved placeholder
erase a value discovered by a newer extractor version.

The visible symptom is internally contradictory but explainable: page targets
still expose the new token bbox, while the invoice input is blank because an old
full-field draft snapshot overwrites only the invoice-field projection.

### Chosen approach

Classification: **healthy incremental choice**.

Merge fields according to intent evidence:

- a non-empty draft value remains authoritative;
- an empty draft field with registry or conflict provenance remains authoritative
  because it can represent an intentional operator decision;
- extraction provenance is authoritative only while it still identifies the
  current manifest baseline; changed page, bbox, or score indicates stale OCR
  state after developer reprocessing;
- an empty draft field with no score, page, bbox, OCR confidence, role, registry
  identity, or replaced value is an unresolved placeholder, not an edit;
- when a newer manifest contains a value for that placeholder, retain the new
  manifest field instead of applying the stale empty snapshot.

This does not require a migration and repairs existing schema-1 drafts at load
time. A future draft-patch schema may record edited field kinds explicitly, but
that larger persistence change is unnecessary for the currently distinguishable
case.

### Micro-steps

1. Isolate the field merge rule in a pure frontend helper.
2. Apply it in the only draft-to-manifest composition point.
3. Build and lint the viewer.
4. Load the real invoice 008 draft and verify that total and withholding come
   from the new manifest while existing registry/user values remain preserved.
5. Inspect the touched UI state and close the point with residual limits.

### Implementation outcome

- draft fields are no longer spread blindly over their matching manifest fields;
- the merge is isolated in `mergeManifestFieldWithReviewDraft`;
- a provenance-free empty placeholder yields to a newly non-empty manifest field;
- a blank extraction draft also yields when its page/bbox/score no longer match
  the newly non-empty manifest target;
- non-empty draft values, registry/conflict states, and intentionally cleared
  extracted fields from the current baseline continue to override the manifest;
- the existing schema-1 draft for review 001 invoice 008 required no destructive
  migration and remains on disk.

Verification:

- frontend production build: passed;
- frontend lint: no errors; one existing `react-hooks/exhaustive-deps` warning
  remains on the effect that calls `applyReviewDraftsToInvoices`;
- Playwright loaded the real local app and the persisted invoice 008 draft;
- existing registry-enriched draft values remained visible;
- direct DOM assertions confirmed `104,50`, `4%`, and `100,70` in the three
  economic inputs;
- a later real-app regression on review 001 invoice 012 confirmed that a stale
  blank draft tied to the former `1` bbox no longer hides the reprocessed `13`;
- temporary screenshots covered desktop and wide layouts and were deleted after
  inspection;
- no draft, invoice, or registry write action was executed during verification.

Residual limit:

- schema 1 stores full field snapshots rather than explicit edited-field patches.
  The current provenance rule distinguishes placeholders and changed extraction
  baselines, but an explicit edited-field set remains the final way to represent
  operator intent across arbitrary reprocessing changes.

Status: completed.

## Economic Field Resolution Across OCR Tables And Invoice Pages

### Theoretical problem definition

OCR recognition and accounting-field resolution are separate responsibilities.
When OCR already contains the correct labels and values, rerunning OCR cannot fix
an incorrect semantic association. The resolver must first preserve the local
layout relation between a label and its value, then validate the resulting fields
as one accounting system across every page assigned to the invoice.

The observed failure in review 001, invoice 008 exposes three general defects:

- a specific gross-total phrase can be confused with the shorter tax-total phrase
  contained inside it;
- a missing or malformed amount in one table column lets that column steal the
  valid amount belonging to its neighbour;
- individually uncertain page rows are not reconciled even when candidates from
  different pages form a complete and exact accounting equation.

### Resolution theory and best practice

Structured extraction should apply evidence in this order:

1. exact or highly specific lexical evidence before fuzzy alternatives;
2. local geometric ownership before unrestricted proximity;
3. cross-field domain constraints before final promotion;
4. preservation of the original token provenance for every accepted value.

Global fuzzy thresholds must not be lowered to fix one OCR variant because that
would enlarge the candidate space for every invoice. Missing observed variants
may be added when they represent real document text. A longer, more specific
phrase must suppress an incompatible interpretation of one of its substrings.

For aligned table headers, neighbouring anchors define local horizontal ownership
intervals. If no valid amount exists in an anchor's interval, that field remains
missing; crossing into the next column creates fabricated context and is worse
than an empty field.

Invoice-level reconciliation may combine direct candidates from different pages
only when the accounting equations agree to the cent. It must not invent an
amount merely because a plausible percentage exists. Accepted values retain the
page, bbox, OCR confidence, and direct token text from their original candidates.

### Chosen technical approach

Classification: **healthy incremental choice**.

- recognise the observed gross-total phrase `totale iva inclusa` and abbreviated
  `tot fattura` form;
- make the specific gross-total phrase incompatible with the tax-total target;
- constrain amount linking with ownership boundaries derived from neighbouring
  economic anchors on the same header row;
- reconcile total, withholding amount/percentage, net, and taxable amount across
  all pages of a viewer invoice;
- promote a result only when `total - withholding = net` and, when taxable amount
  is available, the allowed withholding percentage also agrees;
- retain the existing safe behaviour when the evidence remains incomplete.

No crop or OCR fallback is added because the required OCR evidence already
exists. No rule contains coordinates or supplier-specific text.

### Risks and defenses

- **Specific phrase classified twice:** explicit target incompatibility and anchor
  specificity tests prevent `totale iva inclusa` from becoming total tax.
- **Irregular non-tabular layouts constrained incorrectly:** ownership boundaries
  are applied only when neighbouring economic anchors form the same visual row.
- **False cross-page combination:** require exact cent-level equations and allowed
  withholding percentages; otherwise retain the existing unresolved result.
- **Calculated field loses visual provenance:** select and return the actual source
  target/token for every displayed field, never a synthetic union bbox.
- **Regression in existing extraction:** preserve the current scoring path when no
  credible neighbouring column exists and run the economic extraction suite.

### Micro-steps

1. Add focused tests for the two observed anchors and specific-anchor precedence.
2. Add a three-column table test where the middle amount is malformed and verify
   that the middle anchor cannot steal the right-column amount.
3. Implement local column ownership without document-specific dimensions.
4. Add an invoice-level test combining total, direct withholding, net, and taxable
   evidence across pages.
5. Implement strict reconciliation while preserving source page and bbox.
6. Run focused tests, the broader extraction suite, and a targeted reprocessing of
   review 001 invoice 008; compare the resulting fields with the prior manifest.

### Implementation outcome

Adopted solution:

- `totale iva inclusa` and `tot fattura` are now observed gross-total anchors;
- `totale iva inclusa` is explicitly incompatible with the total-tax target, so
  the more specific phrase keeps its accounting meaning;
- aligned economic anchors define local ownership intervals using the midpoints
  between their actual bounding boxes;
- an anchor with no valid amount in its own interval remains unresolved instead
  of stealing a neighbouring column's amount;
- invoice fields are jointly reconciled from direct total, withholding, net, and
  taxable candidates whenever exact cent-level equations and an allowed
  withholding percentage agree;
- cross-page promotion preserves the original page, bbox, OCR confidence, and
  token value; same-page resolved rows retain their existing manifest roles.

Critical review changed the initial implementation. The first version skipped
invoice reconciliation whenever any page already contained a complete resolved
row. Real document verification showed why that guard was wrong: invoice 009 had
a valid row on page 4, but independent field selection still chose the higher
scoring incompatible total `660,00` from page 3. Reconciliation now takes
precedence whenever a valid direct combination exists, irrespective of whether a
single page is already complete. The legacy selector remains the fallback when no
strict combination can be formed.

Why the result is satisfactory:

- no supplier coordinates, names, or document-specific dimensions were added;
- no global fuzzy threshold was weakened;
- no OCR or crop computation was added;
- false context is removed before mathematical validation rather than corrected
  afterward with a document-specific exception;
- final displayed values remain inspectable at their real source tokens;
- sibling invoices touched by the document-level reprocessing were inspected, not
  assumed unchanged.

### Verification completed

- focused anchor, column-ownership, and cross-page reconciliation tests: passed;
- economic extraction selection: `22 passed`;
- full extractor test file: `160 passed`;
- Python compile check: passed;
- the first sandboxed full-suite attempts could not create or inspect pytest
  temporary directories because of Windows ACL restrictions; the complete suite
  passed outside the sandbox;
- the temporary directory created by the failed sandboxed attempt was removed;
- review 001 invoice 008 was reprocessed through the real developer command and
  its published manifest now contains:
  - total `104,50`, page 1, bbox on the actual amount token;
  - withholding `4%`, page 1, bbox on the actual `3,80` token;
  - net `100,70`, page 2, bbox on the actual amount token;
- page 2 no longer publishes `104,50` as total tax when OCR reads its own amount
  as malformed `950`;
- the same document-level run also updated invoices 009 and 010; invoice 009 now
  selects the coherent `667,32 - 24,24 = 643,08` combination instead of the
  incompatible higher-score total `660,00`.

Residual limits:

- the percentage validation remains intentionally restricted to `0`, `4`, and
  `10`, matching the current business contract;
- cross-page reconciliation requires a directly extracted withholding amount and
  does not promote a value from percentage alone;
- malformed amounts such as `950` are left unresolved here; generic OCR numeric
  repair remains a separate concern and is not needed to recover the gross total.

Status: completed.

## Point 0 Part XX - Registry Replacement Review Semantics

### Theoretical problem

Registry enrichment has two separate responsibilities that must not be represented
by one comparison rule:

1. preserve the OCR value that was replaced, so the operator can inspect the
   source extraction;
2. call for human review only when the registry value and OCR value materially
   disagree.

Exact string inequality is appropriate for preserving history, but it is too
strict for organization names. OCR often extracts a meaningful proper-name
fragment while the registry contains the same name plus legal form or descriptive
suffixes. Fiscal identifiers are different: a missing or changed character can
identify another subject or expose an error in the invoice and must remain visible.

The second concern is row-level intent. Once the operator selects or exactly
identifies a registry row through any field, that choice applies to the complete
semantic group, not only to empty siblings:

```text
issuer: name + tax code + VAT number
condominium: name + tax code
```

Existing OCR values replaced by that row remain available for review.

### Chosen approach

This is a final UI-domain rule:

- preserve a non-empty OCR value whenever the registry changes its normalized
  representation;
- for `nome_emittente` and `condominio`, suppress the yellow review state whenever
  the non-empty normalized OCR name is a contiguous fragment of the registry name,
  without a minimum length;
- do not apply partial compatibility to C.F. or P.IVA: normalized equality remains
  mandatory;
- continue applying the selected registry record to every field in its semantic
  group, including non-empty fields;
- render a closed-eye icon while the registry value is shown and an open-eye icon
  only while the preserved OCR value is shown.

This deliberately treats even a one-character OCR fragment as compatible when it
is contained in the registry denomination. Empty values never create a
replacement review.

### Verification plan

1. Build and lint the viewer.
2. Exercise a real registry selection that replaces an entire issuer group.
3. Visually inspect closed/open eye states and yellow-state transitions.
4. Verify these comparison cases directly in the running UI:
   - partial organization name: history available, no yellow state;
   - incompatible organization name: yellow until inspected;
   - one-character fiscal mismatch: yellow until inspected;
   - choosing a registry name overwrites and preserves a conflicting fiscal OCR
     value.

### Implementation outcome

- OCR replacement history remains stored in `registry_replaced_value` whenever a
  non-empty normalized value changes;
- yellow review state now uses a separate semantic comparison;
- issuer and condominium names accept any non-empty normalized OCR fragment
  contained in the full registry name as a compatible partial extraction;
- fiscal codes and VAT numbers still require normalized exact equality;
- selecting a registry record continues to replace the complete related field
  group, including non-empty fields, and resets any stale reviewed state for that
  group;
- the control shows a closed eye while the registry value is visible and an open
  eye while the preserved OCR value is visible, with matching accessible labels.

### Verification completed

- frontend production build: passed;
- frontend lint: passed;
- Playwright exercised the real viewer and real registry read endpoints on the
  first review invoice;
- an intentionally wrong issuer tax code was entered, then the issuer was selected
  by exact registry name: tax code and VAT number were both replaced from the same
  row, the wrong tax code remained inspectable, and its field became yellow;
- `PRIMO SOLE` was then used as a partial extraction against the full registry
  denomination: history remained available but the name field did not become
  yellow;
- the closed and open eye states were asserted and visually inspected;
- the persisted draft case `La fam` -> `LA FAMIGLIA SRL` was loaded through the
  real draft endpoint after an app restart: the OCR value remained available under
  the eye control and the field was not marked yellow;
- the persisted condominium case `COND.` -> `COND. VIA DUE GIUGNO 1` was also
  loaded through the real draft endpoint: the OCR abbreviation remained
  inspectable and the field was not marked yellow;
- the initial interaction check mocked draft GET/POST/DELETE requests to isolate
  the UI-domain behavior; the persisted-case regression check used the real draft
  GET endpoint and mocked only writes to protect user data;
- registry reads, manifests, and rendered review data were real in both checks;
- temporary Playwright script and screenshots were deleted.

Why the result is satisfactory:

- audit history and human-attention state no longer share an overly strict rule;
- selecting one registry row has consistent row-level semantics regardless of the
  field used to identify it;
- fiscal identifiers retain conservative review behavior;
- no manifest, draft schema, backend route, or persistence migration was needed.

Residual limit:

- partial-name compatibility covers normalized contiguous text without a minimum
  length, including `L` -> `LA FAMIGLIA SRL`, `La fam` -> `LA FAMIGLIA SRL`, and
  `COND.` -> `COND. VIA DUE GIUGNO 1`;
- this product rule intentionally favors fewer review warnings for denomination
  replacements; the preserved OCR value remains inspectable through the eye even
  for very short compatible fragments;
- abbreviations such as `COND.` versus `CONDOMINIO` or written numbers versus
  digits remain reviewable differences instead of being guessed equivalent.

Status: completed.

### Theoretical problem definition

The app currently lets one persistence object, the imported batch, represent three
different concepts:

- temporary input required by an OCR worker;
- permanent source-document archive;
- historical evidence used to display or restart a past process.

These concepts have different ownership and retention rules. Keeping them coupled
causes duplicated PDFs, prevents safe cleanup, makes process history operational,
and leaves `discarded` invoice payloads in a registry that should represent saved
accounting data.

The general problem is therefore not a page rename. It is to establish explicit
data ownership and terminal-state transitions for an asynchronous document system.
The system must distinguish:

- **staging data**: mutable and temporary worker input;
- **process metadata**: small, persistent supervision and audit information;
- **source archive**: the single durable app-owned copy of every successfully
  processed source PDF;
- **review output**: extracted invoices waiting for a user decision;
- **final output**: saved invoice data and split invoice PDFs.

### Resolution theory and best practice

Modern asynchronous processing systems do not use their work queue as an archive.
A job owns immutable input references and progress metadata; a storage domain owns
the files. Terminal transitions move or promote data exactly once and then release
staging resources.

Relevant principles:

- store a stable source identifier independently from a temporary batch path;
- make finalization idempotent, so retrying it after a crash does not duplicate a
  source or lose it;
- retain source filenames in the process record before staging can disappear;
- delete history as metadata only, never as a cascading document operation;
- allow reprocessing from the source archive by creating one new process that
  references all selected sources;
- keep cancellation safe: external originals are never touched, partial OCR output
  and app-owned staging are removed;
- preserve failures as history, but do not retain large worker inputs merely to
  explain that a failure happened;
- keep rejected review items out of the accounting registry. Their review status is
  enough while cleanup is performed; a full permanent `discarded` invoice is not a
  useful domain record.

A process history row must remain understandable after all temporary folders have
been removed. Therefore its source-file snapshot is part of the job record, not
derived later from a batch.

### Chosen technical approach

Classification: **healthy incremental choice**.

The OCR processor can continue receiving an `ImportedBatchIndex`, but the batch is
redefined as internal staging rather than user-visible persistent data.

The chosen ownership contract is:

```text
new external selection
  -> app staging batch (one app-owned copy)
  -> OCR process
  -> successful terminal finalization
       move sources to source archive
       persist independent source records
       remove staging batch
       keep compact process history

source archive selection
  -> metadata-only staging references where possible
  -> one new OCR process for all selected sources
  -> no second permanent source copy

invoice review save
  -> save accounting record
  -> create split invoice PDF from archived source

invoice review discard
  -> mark review item closed long enough to perform cleanup
  -> do not add a full discarded record to the invoice registry
```

On cancellation, queued removal, or unrecoverable startup cleanup, the app removes
only its own staging and partial process output. The process metadata remains as a
cancelled or failed history item until the user deletes that history row.

The user-facing `Processi` page has two sibling views:

- `In corso`: active and queued processes, with cancel/remove controls;
- `Cronologia`: completed, cancelled, and failed processes, with selection and
  metadata-only deletion.

Both views use compact rows. Every row exposes an information action that opens a
scrollable modal listing the source filenames captured by that process. There is no
reprocess action in process history.

The source archive owns reprocessing. Its existing row selection is reused to start
one process from one or more selected source PDFs.

### Risks and defenses

- **Crash between OCR completion and source promotion.** Finalization must use
  stable source identifiers and be safely repeatable before a job becomes
  `completed`.
- **Legacy jobs lack filename snapshots.** Reconciliation should derive them from
  an existing batch when possible and otherwise show an explicit unavailable list,
  without inventing names.
- **Legacy saved invoices point to source paths created at save time.** The source
  resolver must support those paths while new runs use the independent source
  index.
- **Fatal OCR errors still need recoverable sources.** App-owned input should be
  promoted to the source archive before staging cleanup where possible; process
  failure must not force the user to reselect external files.
- **Source deletion can break later inspection.** It remains an explicit confirmed
  archive operation and does not cascade to saved invoice PDFs or register rows.

### Implementation micro-steps

1. Introduce an independent source-archive model with stable identifiers, atomic
   index writes, path validation, idempotent promotion, deletion, and lookup.
2. Add stable source IDs or archive references to batch documents and processing
   invoice references while preserving legacy index loading.
3. Snapshot source metadata into each job at creation; key frontend job state by
   job ID rather than batch ID.
4. Finalize terminal jobs: promote sources after OCR, remove staging, and clean
   partial output on cancellation or queued removal.
5. Resolve invoice PDF splitting and internal review reprocessing from the source
   archive instead of a retained batch copy.
6. Stop writing discarded invoices to the accounting registry and migrate legacy
   discarded-only entries out of that projection.
7. Add process-history deletion APIs that affect metadata only, plus multi-source
   reprocessing APIs that create one new process.
8. Replace `Cronologia` with compact `Processi` views, filename modal, history
   selection/deletion, and no historical reprocess control.
9. Make the source archive read from its own index and expose selected-source
   reprocessing.
10. Reconcile legacy runtime state conservatively and idempotently at startup.
11. Verify storage transitions, cancellation, history deletion, source
    reprocessing, discard semantics, frontend build/lint, and touched UI flows.

### Verification eyes

- unit tests for source promotion, lookup, deletion, and idempotence;
- integration tests for completed/cancelled/queued job cleanup;
- tests proving history deletion cannot delete PDFs or invoice records;
- tests proving multiple selected sources produce one process;
- tests proving invoice save can split from the archived source after staging is
  gone;
- tests proving discard creates no accounting-registry payload;
- frontend build and lint;
- Playwright screenshots and click paths for both Processi views, the source list
  modal, history selection, and archive reprocessing controls;
- inspection that temporary screenshots are deleted.

### Working doubts

- The exact migration behavior for incomplete legacy batches must be based on the
  files actually present, not only their stale document count.
- Existing source archive rows are currently projected from saved invoice records.
  During migration, both formats may need to be read until the independent index is
  authoritative.

### Decision update - crash-safe finalization

The first implementation review found a crash window after OCR completion but
before the source archive and job status were both committed. A persistent
`finalizing` state is now part of the process state machine:

```text
processing -> finalizing -> completed
```

The processing result is persisted before source promotion. Startup reconciliation
retries idempotent promotion for `finalizing` jobs and then marks them completed.
This prevents an already processed document from being queued again merely because
the app closed during source archival.

### Implementation outcome

Adopted solution:

- `batches` is now internal temporary staging and is no longer shown as a
  user-facing archive concept;
- every process snapshots its source filenames before staging can disappear;
- successful OCR enters `finalizing`, promotes source PDFs into an independent
  indexed archive, and removes its staging batch;
- startup reconciles legacy terminal jobs, including partially missing old batches,
  and retries interrupted finalization without restarting OCR;
- cancellation and queued removal delete app-owned staging and partial process
  output while keeping a compact cancelled history record;
- `Processi` now has `In corso` and `Cronologia` sibling views, compact rows, a
  source-file information modal, selection, and metadata-only history deletion;
- process history no longer offers reprocessing;
- source archive rows are independent from saved invoice records and support one
  multi-selection reprocess action that creates one new process;
- source reprocessing uses temporary worker copies but rebinds to the existing
  archived source, so it creates no second permanent copy;
- deleting a source still used by pending review invoices is rejected;
- an archived source cannot be deleted while a queued, processing, cancelling, or
  finalizing job depends on its temporary reprocessing copy; the user must first
  remove or cancel that process, which also removes the temporary staging copy;
- saving a reviewed invoice splits its PDF from the source archive, not from a
  retained batch;
- discarding a review invoice closes review state without adding a full tombstone
  to the accounting registry;
- legacy source PDFs referenced by saved invoice records are indexed at startup;
- legacy discarded registry records are removed from the accounting projection.

Why the result is satisfactory:

- each durable file now has one owner and one retention rule;
- process history can be deleted without touching source PDFs, split invoice PDFs,
  reviews, or accounting records;
- source documents survive process-history deletion and are the only entry point
  for reprocessing;
- finalization has an explicit recoverable commit boundary;
- the user sees operational processes, permanent source files, review invoices,
  and saved invoices as separate concepts;
- external user-selected files remain read-only and outside every cleanup path.

### Verification completed

- full Python suite: `218 passed`;
- targeted lifecycle suite after the final concurrency changes: `44 passed`;
- frontend production build: passed;
- frontend lint: passed;
- Python compile check: passed;
- Playwright checked desktop and wide layouts for:
  - Processi empty active view;
  - process history rows;
  - source-file information modal;
  - PDF archive sibling navigation;
  - independent source archive table;
- temporary Playwright screenshots and the controlled runtime were deleted;
- no write action was executed against real user runtime data.

Known warnings:

- the complete test suite reports existing Pillow deprecation warnings for
  `Image.getdata`; they are unrelated to this point and should become a separate
  maintenance item before Pillow 14.

Residual limits:

- old history entries whose staging batch and source metadata were already both
  missing can only show that their file list is unavailable;
- real user runtime migration will occur idempotently on the next app start and
  should be inspected once with the actual legacy data volume;
- Playwright inspected the active view in its empty state. Active cancellation and
  queued removal are covered by backend tests and share the visually inspected row
  component, but one live OCR smoke test remains appropriate;
- the source archive index assumes the current single local backend writer. If the
  final desktop architecture later allows multiple backend processes, cross-process
  locking must be added rather than relying on atomic replacement alone.

Status: completed.

## Point 0 Part XIX - Persistent Review Drafts Across Structural Mutations

Status: completed after integration correction.

### Integration correction - local app routing

The first visual check mocked the draft endpoint to protect real runtime data. This
verified the React interaction but concealed an integration defect: the registry
handler exposed `GET /viewer/drafts`, while the local app GET allowlist did not
forward that path. The SPA fallback consequently returned `index.html` with HTTP
200, and the frontend attempted to read a draft collection from a null JSON
payload.

Correction plan:

1. Add the draft GET endpoint to the local app backend routing contract.
2. Reject non-JSON or structurally invalid draft responses with a controlled
   Italian user-facing error rather than a JavaScript exception.
3. Add an integration regression test against `LocalAppHandler` so registry and
   local-app routes cannot silently diverge again.
4. Repeat the UI check with the real draft GET endpoint and temporary runtime data.

Correction outcome:

- `LocalAppHandler` now forwards `GET /viewer/drafts` to the backend;
- the frontend validates that the response contains a draft array before using it,
  preventing raw JavaScript null-access messages from reaching the user;
- a real HTTP regression test proves that the local app returns JSON for the route
  instead of falling through to `index.html`;
- 26 targeted persistence and local-server tests passed, together with frontend
  lint, Python compilation, and the production frontend build;
- Playwright loaded the updated local app without intercepting the draft GET route,
  validated its JSON response, asserted that no draft error banner was present,
  and produced a screenshot that was manually inspected before deletion.

Process correction:

- mocking persistence writes is still appropriate when protecting real user data,
  but future UI checks must leave read-only integration endpoints real;
- a mocked interaction check cannot be reported as full local-app integration
  coverage. The check description must state exactly which routes were mocked.

### Problem definition

Review fields currently combine two different kinds of state in one React object:

- authoritative OCR evidence loaded from manifests;
- unsaved user corrections that exist only in browser memory.

Any authoritative reload therefore destroys every correction, even when only one
invoice changed structurally. The same weakness affects app restart and invoices
that move to another review because display numbering changes.

The general problem is durable draft ownership: temporary user work must survive
navigation and restart without contaminating source extraction evidence or being
applied to an invoice whose page composition changed.

### Resolution theory and chosen approach

Use a persistent draft overlay keyed by stable technical lineage, not by title,
display number, or review position. The key consists of process, document, and
manifest invoice identity. OCR manifests remain immutable extraction state; drafts
store the current editable fields separately and are overlaid after registry
enrichment whenever a review is loaded.

Draft lifecycle:

- field edits schedule a short debounced atomic save;
- blur and structural actions flush pending saves;
- save, discard, and reset remove the corresponding draft;
- split invalidates the source invoice and newly created invoice;
- boundary movement invalidates source and recipient because both page sets change;
- all other drafts survive display renumbering and review overflow;
- app startup loads the draft index before loading reviews.

The draft index belongs under the runtime manifest area because it is operational
review state. Segmentation invalidation must be part of the existing durable
mutation journal, preventing a crash from committing new page groups while leaving
stale drafts eligible for overlay.

### Risks and defenses

- **Stale draft on changed pages:** backend returns and transactionally removes the
  complete affected identity set, including the move recipient.
- **Autosave/segmentation race:** both operations share the review mutation lock;
  the frontend flushes pending edits before requesting segmentation.
- **Display renumbering:** draft identity never contains `display_number` or title.
- **Review overflow:** drafts are global operational state rather than children of
  one review directory.
- **OCR evidence corruption:** drafts never rewrite manifest fields.
- **Residual drafts after finalization:** save/discard/reset explicitly delete them;
  stale entries are harmless because only pending invoice identities are overlaid.

### Micro-steps

1. Add validated, atomically written draft storage under the manifest runtime.
2. Expose list/upsert/delete endpoints and affected IDs from segmentation.
3. Include affected-draft invalidation in the segmentation journal.
4. Load and overlay drafts by stable identity in the frontend.
5. Track user-originated field changes and debounce persistence with explicit flush.
6. Remove drafts on reset, save, and discard.
7. Test restart persistence, renumbering, split/move invalidation, review overflow,
   endpoint behavior, lint/build, and the real review interaction visually.

### Implementation outcome

Adopted solution:

- unsaved review fields are stored in a dedicated atomic
  `_review_drafts.json` overlay under the runtime manifest root;
- draft identity uses process, document, and manifest invoice IDs, so display
  renumbering and movement to another review do not change ownership;
- the viewer loads drafts before reviews and overlays them only after rebuilding
  authoritative OCR and registry-derived fields;
- user edits are saved after a short debounce and explicitly flushed on blur,
  save, discard, reset, and segmentation;
- save and discard remove the finalized invoice draft; reset removes drafts before
  restoring authoritative values;
- split and boundary movement invalidate drafts only for invoices whose page sets
  changed, while unrelated drafts survive reload and review overflow;
- draft invalidation is included in the durable segmentation mutation journal, so
  page structure and draft eligibility cannot commit as two unrelated operations;
- the blocking segmentation dialog now includes a preliminary user-facing phase
  while pending edits are made durable.

Why this is satisfactory:

- OCR evidence remains immutable and can still be inspected independently;
- unsaved user work survives navigation, display renumbering, page reload, and app
  restart;
- structural edits deliberately discard only values whose source page composition
  is no longer valid;
- the implementation extends the existing filesystem transaction model instead
  of creating a second persistence architecture;
- no key depends on Italian labels, invoice titles, or review positions.

Verification performed:

- `python -m pytest tests/test_invoice_segmentation_edit.py
  tests/test_processing_storage.py tests/test_invoice_registry_storage.py
  tests/test_registry_api_processing_queue.py -q -p no:cacheprovider`: 62 passed;
- Python compilation of `processing_storage.py` and `registry_api.py`: passed;
- `npm --prefix apps/viewer run lint`: passed;
- `npm --prefix apps/viewer run build`: passed;
- Playwright interaction check against the real viewer with intercepted write
  endpoints: an edit on invoice 001 was autosaved, survived a simulated split of
  invoice 007, and was restored after a full page reload;
- temporary screenshot inspection confirmed that the blocking segmentation state
  covers the touched review UI without overlap; the screenshot was then removed;
- `git diff --check` on the touched files reported no whitespace errors.

Residual limits:

- `beforeunload` persistence is best effort by browser design; normal debounce and
  blur saves are the primary durability mechanism;
- the current lock is process-local. The selected desktop architecture uses one
  backend process, but a future multi-process backend would require a filesystem
  lock around the draft store and mutation journal;
- an invoice whose pages are structurally changed intentionally loses its draft,
  because retaining values without field-level provenance would risk applying
  corrections to different evidence.

## Point 0 Part XVIII - Protect And Recover The Review Projection

### Theoretical problem definition

The application stores process-owned canonical OCR manifests and a global review
queue derived from those manifests. Startup cleanup currently assumes that every
directory directly below `manifests` is a processing run. That assumption became
false when `_review_queue` and other technical directories were introduced.

The general problem is ownership-aware lifecycle management: cleanup must delete
only objects whose type and lifecycle it can positively identify. Absence of a
process index is not proof that an arbitrary sibling directory is a corrupt
process.

The incident left two completed process indexes and all document manifests intact,
but deleted the global review projection. Therefore prevention alone is
insufficient; the projection must also be recoverable without rerunning OCR.

### Resolution theory and best practice

Destructive cleanup should use a positive type contract, not a blacklist of known
exceptions. In this filesystem model a process directory is application-owned as
a process only when its name matches the `process_*` namespace. Technical siblings
such as `_review_queue` and `_review_mutations` are outside that lifecycle.

Derived projections should be reconstructible from canonical state. Recovery must
be idempotent and fail closed:

- run only for completed processes that still declare review references;
- preserve intentional deletion because the supported clear action removes those
  references from process indexes;
- require complete document manifests, exact invoice counts, and resolvable source
  provenance;
- rebuild only missing or structurally invalid review directories;
- exclude invoices already persisted as saved or discarded;
- publish each rebuilt review through a temporary directory replacement;
- never rerun OCR and never modify document manifests or source PDFs.

### Chosen technical approach

Classification: **final choice** for cleanup ownership and **healthy recovery
choice** for the existing damaged runtime.

The cleanup iterator will accept only directory names in the process namespace.
Startup reconciliation will rebuild missing declared projections once, immediately
after cleanup. Regular review polling therefore has no added filesystem scan.
Existing valid reviews remain untouched. A process whose canonical data cannot
prove an exact reconstruction remains hidden and unchanged rather than being
guessed.

### Micro-steps

1. Add a regression test proving startup cleanup preserves technical manifest
   directories.
2. Add recovery tests for a deleted global queue, intentional clear, finalized
   invoices, and inconsistent canonical counts.
3. Restrict cleanup to positively identified process directories.
4. Implement idempotent review-projection reconstruction from completed process
   manifests and archived source provenance.
5. Run focused and extended persistence/API suites.
6. Exercise recovery against the real runtime and verify all expected reviews and
   invoice counts without rerunning OCR.
7. Record the incident cause, repaired state, and remaining limits.

### Implementation outcome

Adopted solution:

- startup cleanup now applies only to positively identified `process_*`
  directories;
- `_review_queue`, `_review_mutations`, and future technical projections are not
  part of the process cleanup lifecycle;
- startup reconciliation runs once after incomplete-process cleanup;
- missing or structurally invalid declared reviews are rebuilt from completed
  document manifests without OCR;
- recovery validates the process completion marker, exact process invoice count,
  exact sum of declared review counts, complete document outputs, and archived
  source provenance before publishing anything;
- saved and discarded invoice IDs are read from the durable invoice registry and
  excluded from reconstructed pending reviews;
- each recovered review is assembled in a temporary sibling directory and then
  published by directory replacement;
- regular review-list polling performs no recovery scan;
- the supported `Elimina tutte le revisioni` workflow remains authoritative
  because it clears process review references, leaving nothing eligible for
  reconstruction.

Incident conclusion:

- the user did not trigger the supported clear workflow;
- startup cleanup treated `_review_queue` as a malformed processing run because it
  iterated every direct child of `manifests` and interpreted a missing
  `processing_run_index.json` as corruption;
- the two process indexes and 144 completed document manifests remained intact;
- 24 review projections containing 343 pending invoices were reconstructed from
  that canonical state;
- no OCR, source PDF, document manifest, or process index was rewritten.

### Verification completed

- focused processing-storage suite: `25 passed`;
- focused processing and queue API suite after startup integration: `45 passed`;
- extended document worker, processing, invoice archive, invoice registry, queue
  API, and source archive suite: `66 passed`;
- Python compile check: passed;
- the sandboxed extended run exposed only a Windows temporary-directory permission
  failure; the identical suite passed outside the sandbox;
- real runtime inspection after recovery: 24 review directories, 343 pending
  invoices, zero invalid directories, zero temporary recovery directories,
  labels `Revisione 001` through `Revisione 024`.

### Critical review and residual limits

The cleanup correction is satisfactory because deletion is now based on positive
ownership rather than assumptions about directory position. The recovery is
idempotent, bounded, and refuses inconsistent state instead of guessing.

Recovery intentionally requires source-archive provenance. A historical
invoice-level reprocessing job whose original review projection and parent
lineage metadata were both lost may not be reconstructible by this helper; it is
left unchanged. The corrected cleanup prevents that loss for future jobs. If
recovery requirements expand, process indexes should persist a canonical immutable
invoice-reference ledger rather than teaching recovery to infer more lineage from
unrelated stores.

Status: completed.

## Point 0 Part XVII - Transactional Manual Invoice Segmentation Editing

Status: theoretical design and critical review in progress.

### Theoretical problem definition

The requested feature is not generic page reordering. It is manual editing of an
ordered invoice segmentation produced by OCR.

Two domain operations are required:

- **boundary merge:** move the first page to the previous invoice or the last
  page to the next invoice; if the source segment becomes empty, the
  source invoice ceases to exist;
- **split:** choose a boundary after position `N` inside one invoice and create a
  new invoice from the pages after that boundary.

The operation must update one coherent domain state, even though the current
storage projects that state into several JSON files:

- the document `viewer_manifest.json`;
- one or two `review_batch_index.json` files;
- one or two `viewer_index.json` files;
- the process index when a new review container is required;
- user-facing procedural numbering.

Shared-payment logic may intentionally copy the same source page into more than
one invoice. Once copied, each list membership is an independent invoice page.
The shared `page_number` records only provenance inside the immutable source PDF;
it does not make either copy subordinate to the other and does not create a
special page role.

### Domain invariants

Every accepted mutation must preserve all of the following:

1. Every source page remains represented by at least one active invoice segment.
2. Two invoices may independently contain copies with the same source page
   number.
3. Pages inside an invoice remain ordered and contiguous.
4. No active invoice may contain zero pages.
5. A split boundary is a position inside the current invoice, not an absolute PDF
   page number. For four pages, valid boundaries are 1, 2, and 3.
6. Boundary movement is allowed only between adjacent pending invoices from the
   same source document, including when they are displayed in adjacent review
   groups.
7. The set of represented source pages before and after a successful operation is
   identical; the number of independent memberships may change when a copy is
   moved into an invoice that already contains that source page.
8. OCR page evidence is immutable. Invoice-level fields are projections and must
   be rebuilt from the pages assigned after the mutation.
9. Internal identity and visible procedural numbering are different concepts.
10. A review group contains at most 15 pending invoices.

### Resolution theory and best practice

#### Stable identity versus visible numbering

An invoice's technical identifier must not be its mutable position. Deleting
`Fattura 063` must not require changing every foreign key from `invoice-064` to
`invoice-063`. Existing numeric IDs remain valid legacy identities, while newly
created split segments receive a non-positional stable identifier.

Add an explicit display ordinal to the review projection. After a merge or split,
recompute visible titles from the affected position onward. This produces the
required user result (`64 -> 63`, or `63 -> 64`) without mutating technical IDs.

Any code that currently derives semantics from the numeric suffix of
`invoice_id` must be migrated to the explicit display ordinal before split IDs
become non-numeric.

#### Canonical state and projections

The ordered invoice list in the document manifest remains the canonical mutable
segmentation for this incremental architecture. Page OCR data remains immutable.
Review indexes and viewer indexes are derived operational projections.

The mutation service must:

1. load every affected payload;
2. calculate the complete next state in memory;
3. validate page conservation, contiguity, adjacency, pending status, and review
   capacity;
4. persist the complete next state as one recoverable transaction;
5. return the new selected review and invoice identity to the frontend.

Do not let the frontend independently reproduce the mutation. After success it
must reload the authoritative review state returned by the backend.

#### Crash and concurrency behavior

Per-file atomic replacement is insufficient when one action updates multiple
files. A process-local lock prevents concurrent mutation requests, but it does not
protect against application or power failure between replacements.

Use a small write-ahead mutation journal under the runtime manifest root:

- write and fsync a prepared transaction containing validated relative targets
  and their next payloads;
- atomically replace each target;
- mark/remove the transaction only after every replacement succeeds;
- on startup, safely roll forward any prepared transaction after validating every
  target remains inside the runtime manifest root.

This is an incremental JSON equivalent of a database transaction. If review
editing grows substantially, SQLite should replace the journal rather than
expanding custom transactional infrastructure indefinitely.

#### Review boundaries

Review groups are operational windows with a maximum capacity of 15, not invoice
ownership boundaries. A document neighbor may live in the preceding or following
review.

- merging may leave a review with fewer than 15 invoices; do not pull unrelated
  work from the next review merely for visual symmetry;
- splitting a full review cascades its last pending invoice into the next review
  for the same process;
- if no following review exists, create one with the next global review identity;
- if merging removes the last pending invoice from a review, remove the empty
  operational review and return navigation to the recipient review.

#### Rebuilding fields

Moving only `page_numbers` is incorrect because invoice-level fields may still
point to a page that no longer belongs to the invoice. Expose a public pure helper
that builds invoice fields from an explicitly supplied page group without running
automatic split detection again.

After every mutation:

- rebuild both affected invoice projections from immutable page targets;
- rerun shared-payment linking on the fixed segmentation only where required;
- let current registry enrichment be applied again by the normal frontend load;
- never rerun Tesseract for a manual segmentation edit.

#### Independent copied-page memberships

No `primary` or `supporting` role is required. Automatic extraction may copy one
source page into several invoice segments, but after that decision each occurrence
is edited exactly like every other invoice page. Its source page number is retained
only so the app can read the correct immutable OCR evidence and source-PDF image.

Consequently, segmentation validation preserves source-page coverage rather than
the global count of page-number occurrences. Removing or moving one copied
membership must not mutate the same source-page membership in another invoice.

### Alternatives rejected

#### Renumber technical IDs

Rejected because it creates cascading foreign-key updates across manifests,
review indexes, archived records, and reprocessing lineage. A display concern must
not mutate identity.

#### Frontend-only array editing

Rejected because a refresh would restore the old segmentation and because local
state cannot safely coordinate review indexes, process lineage, or crash recovery.

#### Permit 16 invoices in a review after split

Rejected because 15 was chosen as the maximum usable viewport size. A structural
edit must not silently break that UI contract.

#### Re-run automatic OCR segmentation after every manual edit

Rejected because the user decision must be authoritative and deterministic. OCR
could immediately recreate the split the user just corrected.

#### Encode primary/supporting roles for duplicated pages

Rejected because it would preserve a relationship that the domain does not need
after duplication. It would make ordinary manual editing depend on extraction
history and introduce avoidable special cases.

### UX approach

Keep `Riordina pagine` as the explicit, low-prominence structural tool.

- Enable `Prima pagina alla precedente` and `Ultima pagina alla successiva` for a
  single-page invoice when an eligible neighbor exists.
- When that move removes the current invoice, show an app-styled confirmation that
  states the invoice will disappear and later procedural numbers will change.
- Add `Dividi fattura` with a compact selector containing boundaries such as
  `Dopo pagina 1 di 4`, never a free-form absolute page input.
- Confirm split with a concise summary of the two resulting page counts.
- State in the confirmation that unsaved field edits are recalculated from OCR
  evidence. Never discard local edits silently.
- After success, reload the authoritative affected review and select the recipient
  invoice for merge or the left invoice for split.

### Critical review

Strengths:

- the domain rules are independent from the current buttons;
- IDs remain stable while user numbering behaves as requested;
- cross-review boundaries do not leak storage structure into user behavior;
- page evidence and derived invoice fields have explicit ownership;
- crash recovery is designed before multi-file writes are introduced.

Costs:

- this is materially larger than removing the existing single-page guard;
- review overflow and crash recovery add backend work;
- copied source pages remain independently editable without a schema migration;
- explicit display ordinals require removing a few numeric-ID assumptions from
  reprocessing code.

The cost is justified because the smaller implementation would permit empty
invoices, stale review references, incorrect fields, or partial state after a
crash. Those failures would directly corrupt the user's review workflow.

### Planned micro-steps

1. Inventory and remove positional assumptions based on `invoice_id` suffixes.
2. Add an explicit display ordinal with backward-compatible legacy inference.
3. Add pure segmentation-domain tests for singleton merge, normal boundary move,
   split, invalid boundary, source-page conservation, contiguity, and independent
   copied-page membership.
4. Expose field reconstruction from fixed page groups and test that bbox/page
   evidence follows the new segment.
5. Implement the recoverable mutation journal and startup roll-forward tests.
6. Implement backend merge/move and split orchestration across review boundaries.
7. Implement review overflow cascading and empty-review removal.
8. Replace frontend local mutation with backend-authoritative reload and app-style
   confirmations.
9. Add API integration tests and verify save/discard/reprocess lineage after both
   operations.
10. Run build, lint, Python suites, and temporary Playwright checks covering a
    singleton merge, normal move, split, copied-page cases, review
    overflow, and navigation after the selected invoice disappears.

### Inventory result

The abandonment criterion was evaluated before implementation.

Production positional assumptions are currently limited to:

- frontend review sorting extracts a number from the invoice title;
- review reprocessing derives its first OCR number from numeric `invoice_id`
  suffixes.

Invoice archive and accounting registry code already treat `invoice_id` as an
opaque string scoped by its review path. The mutation does not need to rewrite
saved accounting records or archived PDFs. Its persistence boundary remains the
active manifest/review projection, so the bounded JSON journal is appropriate and
SQLite is not required for this point.

### Abandonment criteria

Stop before implementation and redesign around SQLite if the inventory shows that
one mutation must atomically rewrite archived accounting records or a broad set of
unrelated process files. The JSON journal is appropriate only while the canonical
mutation remains bounded to active review state and its direct process projection.

### Implementation outcome

Adopted solution:

- introduced a pure segmentation domain module with boundary move, singleton
  merge, split, source-page conservation, and contiguity guards;
- added explicit `display_number` while preserving existing `invoice_id` values as
  stable technical identities;
- new split invoices receive non-positional stable IDs;
- reprocessing now derives its starting ordinal from `display_number`, with title
  and numeric-ID fallback only for legacy manifests;
- invoice fields are rebuilt from immutable OCR targets belonging to the resulting
  page groups without rerunning OCR or automatic split detection;
- the backend resolves adjacent invoices from the complete document manifest, so
  review boundaries do not block a valid move;
- singleton merge removes the empty invoice reference and decrements following
  display ordinals throughout the active process;
- split inserts a new review reference, increments following display ordinals, and
  cascades overflow into the following review while preserving the 15-item limit;
- process invoice and review counts are updated with the mutation;
- multi-file state is committed through a durable prepared journal and incomplete
  journals roll forward idempotently on startup/listing;
- the frontend only submits the user decision and reloads the authoritative batch
  and selected invoice returned by the backend;
- both singleton merge and split use app-styled confirmation dialogs and explicitly
  warn that unsaved field edits are recalculated;
- the obsolete partial `/viewer/move-page` path and its helpers were removed.

Why the result is satisfactory:

- the requested singleton workflow works without leaving an empty invoice;
- split and merge produce dense user numbering without mutating technical keys;
- manifest, review index, viewer index, and process index are committed as one
  recoverable state transition;
- the UI storage grouping remains invisible to document-level adjacency;
- no OCR computation is added to a manual correction;
- copied shared-payment pages are ordinary independent invoice memberships while
  retaining their immutable source-page provenance.

### Verification completed

- pure segmentation tests cover normal move, singleton merge, split, invalid
  boundaries, field reconstruction, and independent copied-page behavior;
- storage integration tests cover manifest/review/viewer consistency, visible
  renumbering, review overflow, process counts, and journal recovery;
- extended processing, queue API, invoice registry, and PDF archive suite:
  `61 passed`;
- Python compile check: passed;
- frontend production build: passed;
- frontend lint: passed;
- a controlled isolated runtime executed a real HTTP singleton merge followed by
  a split after position 2; final page groups were `1,2 | 3,4` and titles were
  `Fattura 001 | Fattura 002`;
- Playwright inspected singleton controls, multipage split controls, and both
  confirmation dialogs at 1440 and 1728 pixels;
- the visual pass found and corrected an empty singleton split selector and a
  truncated split option;
- all controlled runtime data and temporary screenshots were deleted;
- no real user invoice or archive was modified during verification.

### Follow-up correction: split control discoverability

Real review data exposed a usability ambiguity rather than a missing domain
feature. The first six invoices in Review 001 are single-page invoices, and the
frontend previously removed the split controls entirely when fewer than two pages
were present. A user checking those invoices could therefore reasonably conclude
that manual split had never been implemented.

The split row is now always visible inside `Riordina pagine`:

- a single-page invoice shows a disabled numeric boundary and explains that at
  least two pages are required;
- a multipage invoice accepts the page position after which the split must occur;
- the numeric input is bounded to `1..page_count - 1`, and the existing backend
  validation remains authoritative;
- confirmation and persistence behavior are unchanged.

This deliberately replaces the original compact selector decision. It is not a
free-form source-PDF page reference: it is a constrained position inside the
currently displayed invoice, which matches the user's requested mental model
without accepting an invalid terminal boundary.

Verification:

- real manifests confirmed invoices 001-006 have one page and invoice 007 has two;
- frontend lint: passed;
- frontend production build: passed;
- segmentation and storage tests: `32 passed`;
- Playwright inspected the open reorder menu for both invoice 001 and invoice 007
  at 1440 and 1728 pixels;
- no segmentation action was executed against real user data.

### Follow-up: blocking progress and explicit crash recovery

#### Problem definition

A manual segmentation mutation is a short multi-file transaction. While it is in
flight, the user must not start navigation or editing actions against the old
invoice projection. Independently, terminating the frontend, backend, or whole app
must never leave a partially applied set of manifest and review files.

These are two separate responsibilities:

- the frontend owns interaction exclusion and understandable progress feedback;
- the backend owns durable atomicity and restart recovery.

#### Resolution theory and chosen approach

The frontend must use a modal, non-dismissible busy state from confirmation until
the authoritative review has been reloaded. A determinate percentage would be
false precision because the synchronous endpoint exposes no measurable units.
Use an indeterminate bar and two user-facing phases instead: reorganizing pages,
then preparing the updated review. Keyboard shortcuts must observe the same lock;
a visual backdrop alone is insufficient.

The existing prepared journal is the correct durability mechanism. There are
three safe interruption boundaries:

1. before the durable journal exists, no persistent mutation has started;
2. after journal creation but during file replacement, restart reapplies every
   intended payload idempotently;
3. after all replacements but before journal deletion, restart reapplies the same
   final state and removes the journal.

Recovery should run explicitly during backend runtime initialization, before
cleanup and review reconstruction, rather than relying on the first review-list
request to trigger it indirectly.

#### Micro-steps

1. Add a dedicated segmentation busy state and non-dismissible progress dialog.
2. Keep the overlay active through authoritative manifest reload.
3. Make invoice keyboard shortcuts no-op while the mutation is active.
4. Recover prepared review mutations at backend startup before cleanup/recovery.
5. Verify frontend lint/build, crash-recovery tests, startup ordering, and both
   visual phases without mutating real invoice data.

#### Implementation outcome

- confirmation now transitions into a non-dismissible modal busy state;
- the first phase describes page reorganization, with wording specific to split
  or boundary movement;
- the second phase starts only after the mutation endpoint succeeds and remains
  visible while the authoritative review index and manifests are reloaded;
- the indeterminate bar communicates activity without inventing a percentage;
- mouse interaction is intercepted by the modal backdrop and a capture-phase
  keyboard guard prevents invoice shortcuts or focus changes behind it;
- the old inline segmentation status was removed during progress to avoid showing
  the same state twice;
- `loadBatch` and `loadDocumentEntries` now return their existing asynchronous
  work, allowing callers to wait for the complete reload rather than only the
  review-index request;
- backend initialization explicitly recovers prepared review mutations before
  incomplete-process cleanup and missing-review reconstruction.

#### Interruption semantics

- closing only the browser window does not cancel a request already executing in
  the local backend; the next UI load reads the resulting authoritative state;
- stopping the whole app before journal preparation leaves the previous state
  unchanged;
- stopping it after journal preparation causes startup to roll the complete
  intended state forward;
- replay is idempotent, so interruption after all target files were replaced but
  before journal deletion is also safe;
- concurrent segmentation requests are serialized by the backend mutation lock.

#### Verification completed

- frontend lint: passed;
- frontend production build: passed;
- segmentation, storage, queue, and startup-order tests: `53 passed`;
- a new startup test proves mutation recovery precedes cleanup and review rebuild;
- Playwright intercepted and delayed the real endpoint path, then inspected both
  progress phases at 1440 pixels without applying a real segmentation mutation;
- the visual pass identified and removed the duplicated inline progress message;
- all temporary scripts and screenshots were deleted.

Why the result is satisfactory:

- interaction locking and data durability are separate and explicit;
- progress copy is user-facing and corresponds to observable client/backend
  boundaries;
- restart safety no longer depends on which page the frontend requests first;
- no polling, fake percentage, or additional persistent operation model was added
  for a short synchronous transaction.

Residual limit:

- an operating-system or disk failure that corrupts the journal itself cannot be
  repaired automatically; atomic replacement and durable flush make this a narrow
  storage-failure case rather than a normal app-close case.

Status: completed.

### Follow-up correction: copied pages are ordinary memberships

The initial implementation conservatively blocked edits whenever the same source
page number occurred in more than one invoice. That interpretation was incorrect.
Shared-payment extraction performs a copy, not a persistent relationship between
an owner page and a subordinate page.

The segmentation domain now applies these rules:

- each occurrence of a source page number in an invoice is an independent page
  membership;
- the number is retained only as immutable source-file provenance and as the key
  for retrieving OCR/page evidence;
- moving or splitting one membership never mutates the occurrence held by another
  invoice;
- validation preserves the set of represented source pages, not the global count
  of copied memberships;
- if a copied page is moved into an adjacent invoice that already contains that
  same source page, only the source membership disappears: the destination does
  not gain a visually duplicated copy of an identical page.

The obsolete shared-page refusal and its `primary/supporting` schema proposal were
removed. Targeted and extended tests pass (`61 passed`).

### Residual limits

- shared-payment linking is not recomputed after a manual split; existing copied
  memberships remain intact, while the user's manual segmentation remains
  authoritative;
- the JSON journal is appropriate for the current bounded active-review mutation.
  If future editing spans accounting records or archives, migrate the workflow to
  SQLite instead of extending the journal.

Status: completed.

## Point 0 Part XVI - Keep The Review Queue Free Of Stale Process References

Status: in progress.

### Theoretical problem definition

The process index is historical metadata, while the review queue is current
operational state. A process may retain a reference to a review batch whose
queue directory has been removed by cleanup, migration, or an interrupted
transition. If the operational listing trusts that historical reference, the UI
offers a review that cannot be opened and may select it as the default empty
view.

The general problem is referential integrity between durable history and a
materialized work queue. Historical metadata must not be treated as proof that
an operational resource still exists.

### Resolution theory and chosen approach

Classification: **final choice**.

Keep process history immutable for this repair, but make the review listing
authoritative on the materialized review index. A batch is listable only when:

- its base path resolves inside the runtime manifest root;
- its review directory exists;
- `review_batch_index.json` exists and is valid;
- it still contains at least one pending invoice.

Preserve the batch's global identity and label. Filtering deleted queue entries
must not renumber surviving reviews, because display numbers, CLI targeting, and
persisted paths refer to the same global review identity.

### Micro-steps

1. Add a regression test with a complete process index that references a deleted
   review directory and a second valid review.
2. Make `list_review_batches` exclude unresolved or invalid queue references.
3. Preserve the original progressive label of surviving batches.
4. Run the focused storage and API tests, then inspect the real runtime listing.
5. Build and lint the viewer because its startup selection consumes this API,
   even though the repair is backend-only.

### Implementation outcome

Root cause:

- the completed process index still referenced review batches 001-009;
- their materialized directories had already been removed from `_review_queue`;
- `list_review_batches` treated a read failure as permission to reuse the stale
  historical invoice count;
- the frontend selected revision 001 by default, then failed to load its missing
  `viewer_index.json`, producing an empty review surface.

Adopted solution:

- unresolved, malformed, empty, or non-materialized review batches are excluded
  from the operational listing;
- both `review_batch_index.json` and `viewer_index.json` must be readable before a
  review is exposed;
- surviving reviews retain the global number encoded in their persistent identity
  instead of being renumbered after filtering;
- process history is not destructively rewritten by a read operation.

Why the result is satisfactory:

- the UI can no longer select a known-missing review as its default;
- valid OCR output and manifests are left untouched;
- historical process metadata and current operational state keep separate roles;
- the rule also handles future interrupted cleanup or migration states.

### Verification completed

- new stale-reference regression test: passed;
- processing storage suite: `19 passed`;
- processing storage and registry API queue suite: `39 passed`;
- real runtime inspection lists only revisions 010-024, with 219 visible pending
  invoices in total;
- every listed review resolves to readable document manifests and the expected
  invoice IDs;
- frontend production build: passed;
- frontend lint: passed.

Observed test-suite warning:

- one existing asynchronous queue test emitted a Windows `PermissionError` while
  its background thread replaced a temporary jobs file after test cleanup;
- all assertions passed and the warning is unrelated to review listing, but the
  test teardown race should be addressed in a dedicated concurrency cleanup point.

Operational note:

- an already running backend must be restarted to load this Python change;
- OCR does not need to be rerun because revisions 010-024 and their manifests are
  already complete on disk.

Status: completed.

## Point 0 Part XIV - Prevent Source Deletion During Reprocessing

Status: in progress.

### Theoretical problem definition

An archived source PDF has one permanent owner: the source archive. Reprocessing
may create a temporary staging copy, but that copy must never become an independent
retention path. Otherwise deleting the archived source could appear successful and
the worker could silently recreate it from staging at finalization.

The problem is therefore a lifecycle conflict between a destructive archive action
and a non-terminal process that still depends on the same source.

### Resolution theory and chosen approach

Destructive operations should not implicitly cancel unrelated asynchronous work,
and an active worker must not resurrect data explicitly deleted by the user. The
clean boundary is to reject source deletion while any non-terminal job references
that source through `source_archive_bindings`.

The user must first cancel the active process or remove the queued process. Existing
job cleanup then removes its temporary batch. Source deletion becomes available
only after no temporary processing dependency remains.

Classification: **final choice**.

### Micro-steps

1. Detect selected source IDs referenced by non-terminal processing jobs.
2. Reject archive deletion with a short user-facing Italian message.
3. Cover queued, processing, cancelling, and finalizing jobs, while allowing
   deletion after terminal states.
4. Run the targeted queue/archive tests and review the resulting lifecycle.

### Implementation outcome

- source deletion now checks every non-terminal job's
  `source_archive_bindings` before touching the archive;
- queued, processing, cancelling, and finalizing dependencies reject deletion;
- the user-facing error instructs the user to cancel the active process or remove
  the queued process first;
- terminal process history does not block deletion;
- no implicit cancellation or cascading process mutation was introduced;
- after process cleanup removes the temporary batch, deleting the archived source
  removes the only permanent copy of that original PDF.

This preserves the intended ownership model:

```text
batch staging = temporary processing copy
source archive = only permanent original-PDF copy
process history = metadata and filename snapshot only
```

Verification:

- focused source archive and processing queue tests: `18 passed`;
- worker, processing storage, queue, and source archive lifecycle suite:
  `42 passed`;
- the first sandboxed extended run could not initialize pytest worker temporary
  directories due to Windows sandbox permissions; the same suite passed outside
  the sandbox;
- no real runtime files were modified.

Status: completed.

## Point 0 Part XV - Persist Discarded Invoices And Reprocess Library Items

Status: in progress.

### Theoretical problem definition

The application has three different kinds of OCR input:

- external PDFs selected by the user;
- original PDFs already owned by the source archive;
- page subsets already owned by saved or discarded invoice history.

Treating all three as new source documents duplicates archives and loses the
identity of an invoice page subset. Treating discard as deletion also removes a
useful review decision and prevents targeted OCR regression testing.

The general problem is therefore provenance-aware document processing: every job
must know who owns its durable input, whether finalization may create a source
archive record, and how local pages map back to the parent document pages.

### Resolution theory and best practice

Processing staging and durable ownership must remain separate:

- staging may contain temporary copies required by workers;
- external upload owns no internal durable source yet, so successful finalization
  promotes it into the source archive;
- source-library reprocessing already has a durable owner, so finalization reuses
  the existing source identity;
- invoice-library reprocessing uses the archived invoice PDF as its durable input
  and must never promote that page subset as a new original source.

Page lineage must be explicit metadata. A page numbered `1` in a split invoice PDF
may represent page `37` of its original source. Names and positional assumptions
are not reliable lineage.

Discarded invoices are durable review-history records, not accounting records.
They retain their split invoice PDF and extracted snapshot, but are excluded from
the accounting Excel export and from saved-invoice editing workflows.

File-name presentation rule:

- a direct reference to a real file keeps its extension, for example
  `documento.pdf`;
- a generated label that combines the file identity with other information uses
  the stem, because the extension would no longer appear at the end;
- therefore a discarded invoice label is `<original source stem> - pagina N` or
  `<original source stem> - pagine N-M`.

### Chosen technical approach

Classification: **final choice**.

Persist both saved and discarded invoice records in the invoice history store.
Each record owns a split invoice PDF in `archive/invoices`; only saved records are
part of the accounting projection and Excel export.

Add explicit process provenance:

```text
external_upload
source_archive
saved_invoice
discarded_invoice
```

The first kind promotes staging PDFs into the source archive. The second reuses
existing source IDs. Invoice kinds delete staging after OCR and never create a
source archive record.

Invoice reprocessing carries its parent record ID and source-page map through the
batch document, processed invoice reference, and review index. A later save splits
from the durable parent invoice PDF and composes the page map, preserving lineage
without requiring the full original PDF to still exist.

The invoice register page gains sibling views `Fatture salvate` and
`Fatture scartate`. Both use the same virtualized table and PDF inspection flow.
Discarded rows use their generated source/page label and expose targeted
reprocessing. Saved rows also expose targeted reprocessing.

### Risks and defenses

- **Wrong page after nested reprocessing:** compose local page numbers with the
  stored parent-page map and test non-contiguous pages.
- **Accidental source duplication:** make source finalization policy explicit in
  the job instead of inferring it from paths or labels.
- **Deleted original source:** invoice reprocessing remains possible from the
  archived split invoice PDF; the new job still creates no source archive item.
- **Deleting an invoice while it is being reprocessed:** reject deletion using the
  same dependency principle used for active source reprocessing.
- **Legacy discarded tombstones:** retain only records that own a valid archived
  invoice PDF; old marker-only records remain excluded rather than being presented
  as usable history.
- **Accounting pollution:** editing and Excel export continue to operate only on
  `saved` records.

### Micro-steps

1. Extend batch and review lineage metadata without breaking legacy indexes.
2. Archive a split PDF and durable history record for discarded invoices.
3. Stop purging valid discarded records at startup while ignoring legacy
   marker-only tombstones.
4. Add invoice-library reprocessing and provenance-aware finalization.
5. Add process provenance labels to active and historical process rows.
6. Add saved/discarded sibling views and reprocessing actions to the virtualized
   invoice table.
7. Keep extensions on direct file references and omit them only from composed
   invoice/page labels.
8. Test save/discard lineage, nested page mapping, no-source-promotion behavior,
   cancellation cleanup, deletion guards, backend APIs, frontend build/lint, and
   touched Playwright flows.

### Implementation outcome

Adopted solution:

- discarding an invoice now creates a durable `discarded` history record and a
  split invoice PDF instead of a registry marker that is later purged;
- the accounting Excel export and editing workflow remain restricted to saved
  invoices;
- the virtualized invoice register has sibling `Fatture salvate` and
  `Fatture scartate` views with the same inspection, selection, deletion, and
  targeted reprocessing model;
- discarded invoice labels combine the original source stem and original page
  numbers, while direct references to actual files keep `.pdf`;
- batch documents and review references preserve the parent invoice record and
  original page map through nested reprocessing;
- reprocessing saved or discarded invoices reads their archived split PDFs,
  creates temporary staging, and never promotes those inputs into new source
  archive records;
- process rows explicitly distinguish uploaded files, original-file library,
  saved-invoice library, and discarded-invoice library;
- deleting an invoice PDF is blocked while a non-terminal reprocessing job or a
  pending review still depends on it;
- failed job creation removes the staging batch immediately;
- legacy schema-1 discarded markers without a PDF are removed once, while
  schema-2 discarded records with a PDF remain durable.

Why the result is satisfactory:

- discard is now a reversible, inspectable business decision rather than hidden
  deletion;
- permanent ownership is unambiguous and reprocessing does not duplicate source
  archives;
- page lineage survives source deletion and repeated invoice-level processing;
- saved and discarded records share infrastructure without mixing accounting
  semantics;
- the UI exposes provenance without introducing technical storage terminology.

### Verification completed

- Python compile check: passed;
- targeted persistence, PDF archive, and queue suite: `32 passed`;
- extended worker, processing, archive, registry, and queue suite: `56 passed`;
- frontend production build: passed;
- frontend lint: passed;
- Playwright checked the saved/discarded register, PDF archive, and process view at
  1440 and 1728 pixels;
- the first visual pass found a 1440-pixel header overlap; the responsive grid was
  corrected and the repeated screenshots were clean;
- a final screenshot confirmed that direct process file references retain `.pdf`;
- all temporary screenshots were deleted;
- no destructive UI action was executed against real user data.

Residual limits:

- the migration of actual legacy runtime records should be inspected once after
  the next application restart;
- populated discarded-history rows are covered by backend and component logic,
  while the current real runtime visual check only exposed the empty discarded
  state;
- restoring a discarded invoice directly to the accounting register is not part
  of this point: reprocessing creates a new review decision instead.

Status: completed.

## OCR Consensus For Fragmented Invoice Numbers

### Theoretical problem

The OCR pipeline can retain overlapping hypotheses from different passes: one
pass may read a complete token while a table crop splits the same glyph region
into adjacent fragments. Allowing every hypothesis to compete only by anchor
score can select a fragment even when the OCR evidence already agrees on the
complete value.

### Chosen approach

Treat exact geometric composition as stronger structural evidence than the
individual fragment scores. A complete invoice-number candidate dominates only
when at least two smaller, horizontally ordered candidates are contained in its
bbox and their exact concatenation equals the complete normalized value. Do not
create a combined value when no OCR pass produced it, and do not alter anchor
weights to compensate for this separate evidence problem.

This is a final, general rule for reconciling overlapping OCR hypotheses rather
than a document-specific exception.

### Micro-steps

1. Detect exact complete-candidate/fragment compositions per page.
2. Remove only the proven fragments from final target competition.
3. Reproduce review-001 invoice-012 geometry in a regression test.
4. Verify the real persisted `document.json` without overwriting the review.

### Implementation outcome

- exact fragment groups are detected only when their ordered values concatenate
  to an existing complete OCR hypothesis and their geometry fills its bbox;
- proven fragments are removed from final target competition;
- the complete candidate inherits the strongest anchor quality and score from
  the fragments it represents, preserving useful evidence from crop OCR;
- a genuinely stronger exact anchor remains able to win, so composition does not
  bypass the established exact-over-fuzzy selection rule.

### Verification and closure

- invoice-number regression family: `20 passed`;
- Python compile check: passed;
- real review-001 invoice-012 diagnostic: `13` selected, overlapping `1` and `3`
  retained for diagnostics but not selected;
- the current persisted manifest was intentionally not modified and still
  contains its old materialized `1` target until reprocessing.

Status: completed.

## Canonical Monetary Field Display

### Problem and decision

Monetary values entered or displayed in different numeric conventions must not
remain visually inconsistent. The UI now uses one canonical representation:
no thousands separator, a dot as decimal separator, and exactly two decimal
digits. The withholding field is excluded because it represents a percentage,
not an amount.

Normalization runs when a review or register field loses focus and when Enter
causes that focus change. Existing extracted monetary values are normalized for
display as well. The conversion accepts comma-decimal and the two common grouped
forms, while malformed text remains unchanged instead of being guessed.

Examples:

```text
85,79      -> 85.79
1.234,56   -> 1234.56
1,234.56   -> 1234.56
1,234      -> 1234.00
```

### Verification and closure

- focused normalization test: passed;
- frontend production build: passed;
- frontend lint: no errors; one pre-existing `useEffect` dependency warning
  remains in `App.tsx`;
- Playwright could start only outside the sandbox, but the already running local
  server was not reachable from that process, so no visual assertion was claimed;
- no persistent user data was changed during verification.

Status: completed.

## Blocking Withholding Format Validation

### Problem and decision

The withholding value is a percentage and must have a narrow, visible input
contract. Any integer percentage from 0 to 99 is valid; the `%` suffix is
optional. A populated value with three digits or malformed text is invalid.
Empty values remain part of the existing non-blocking missing-field review.

Invalid withholding is a blocking validation issue, unlike the existing quality
warnings that users may explicitly override. The review field and editable
invoice-register cell show an immediate red error state. Single-invoice,
whole-review, and invoice-register save flows cannot continue until the value is
corrected. Python persistence repeats the same check so bypassing the frontend
cannot store an invalid value.

The obsolete fallback that interpreted withholding as a monetary amount was
removed. It produced an unrelated accounting mismatch alongside the actual
format error.

### Verification and closure

- frontend production build: passed;
- frontend lint: no errors; the pre-existing `useEffect` dependency warning
  remains;
- invoice registry persistence suite: `11 passed`, including rejection during
  initial invoice save and register editing;
- isolated Playwright interaction confirmed the red field state and that the
  force-save action is absent for `100`;
- Playwright draft writes were intercepted, no real user data was persisted;
- temporary UI script and screenshots were deleted.

Status: completed.

## Registry-To-Archive Document Navigation

Status: completed.

### Theoretical problem

An accounting record references two different archived resources: its final
invoice PDF and its original source PDF. A direct hyperlink conflates two user
intentions: inspecting the document immediately and locating it in the archive
for further operations. Navigation must also fail explicitly when retention or
deletion has removed the referenced archive item.

### Chosen approach

Use one reference-action dialog for both document types. Each Registry button
offers `Open in window` and `Go to file in Archive`. Archive navigation resolves
stable identities already stored by the application, clears filters that could
hide the destination, opens the correct sibling archive view, selects the row,
and scrolls it into view. It must not resolve by display filename because names
are not unique.

Missing references remain in the dialog and produce concise Italian user-facing
feedback. This is a final UI ownership model: the Registry references documents,
while the PDF Archive owns document access and archive-level operations.

### Micro-steps

1. Replace direct Registry links with consistent action buttons.
2. Add the shared choice dialog and missing-reference state.
3. Resolve invoice and source destinations by their archive identities.
4. Add deterministic archive row focus and scrolling.
5. Verify build, lint, both interaction paths, and temporary screenshot cleanup.

### Implementation outcome

- both Registry document references are consistent buttons and open the same
  choice dialog;
- invoice PDFs and original source PDFs can be opened in a controlled secondary
  window or located in their respective PDF Archive view;
- archive navigation resolves `record_id` for invoices and the persisted source
  archive path for source files, never the display filename;
- navigation clears search and filters, selects the destination, scrolls it into
  view, and applies a restrained row highlight;
- deleted or unavailable archive references remain actionable in the Registry
  so the user receives the explicit missing-file message instead of a dead link;
- manually opening the archive or changing selection clears navigation focus.

### Verification and closure

- frontend production build: passed;
- frontend lint: no errors; the pre-existing `useEffect` dependency warning
  remains;
- Playwright exercised source-dialog opening, source archive navigation, invoice
  archive navigation, destination selection, scrolling, and missing-reference
  feedback;
- the missing-reference check mocked only the registry read response and did not
  modify persisted user data;
- visual inspection at 1440 pixels confirmed that the dialog, table controls,
  destination highlight, and error state are readable without overlap;
- the temporary Playwright script and screenshots were removed.

## Processing History Timestamp

Status: completed.

### Problem and decision

Process history omitted its primary chronological information. New processing
jobs already persist `created_at`, while legacy records retain the real start
timestamp inside their stable `process_id`. The UI now shows the processing date
and time in history instead of the redundant completion detail. At startup,
legacy timestamps are recovered only from the strict
`process_YYYYMMDD_HHMMSS_*` format and persisted; no filesystem-date heuristic
is used. Records with no authoritative timestamp remain explicitly unavailable.

History sorting now places valid timestamps in descending order and leaves
undated records last, with a deterministic job-ID fallback.

### Verification and closure

- frontend production build: passed;
- frontend lint: no errors; the pre-existing `useEffect` dependency warning
  remains;
- processing queue suite: `22 passed`, including strict legacy timestamp
  recovery and persistence loading;
- Playwright verified the history at desktop and wide viewports: dated jobs are
  newest-first, the Italian date-time is readable, and undated legacy jobs are
  placed after them;
- temporary screenshots were removed.

## Geometric Date-Role Resolution

Status: completed.

### Theoretical problem

An OCR date has no intrinsic accounting role. Its role comes from a spatially
related label: invoice issue date, payment execution date, due date, legal
reference, or an invoice date merely quoted inside a bank-transfer description.
Using one broad nearby-text window for both discovery and role assignment lets
labels from adjacent rows contaminate each other and can swap invoice and
payment dates.

### Chosen approach

Use row-level evidence as the authoritative role context whenever page geometry
is available. Broader nearby text remains supporting evidence for document type
and generic bank context, but it cannot override a contradictory row label.

- `Data doc.` is an observed invoice-date anchor equivalent to
  `Data documento`;
- `Data esecuzione` is explicit payment evidence and negative invoice-date
  evidence;
- a row such as `Pagamento fattura n. ... del ...` quotes the referenced
  invoice date and must not classify that date as the payment event;
- legacy manifests or isolated tests without spans retain conservative fallback
  behavior based on their stored candidate context.

This is a general geometric role-resolution rule, not a supplier- or
document-specific exception.

### Micro-steps

1. Preserve the observed abbreviated issue-date anchor during OCR candidate
   generation.
2. Mark explicit payment-event rows as invalid invoice-date contexts.
3. Resolve invoice and payment roles from the candidate's own row when spans
   are available.
4. Add an end-to-end segmentation regression reproducing review 025 invoice 1.
5. Reprocess only that invoice and compare its manifest after automated tests.

### Reprocessing lineage finding

The targeted review was created by reprocessing a saved invoice. Both the final
invoice PDF and its original source PDF exist, but the development reprocessing
tool tried to resolve only the transient reprocessing batch/document origin.
That staging origin is intentionally removed after completion, so the lookup
failed despite intact archives.

For this workflow the correct executable unit is the archived invoice PDF,
resolved through `parent_invoice_record_id`; using the original source PDF could
reintroduce unrelated invoices from a multi-invoice source. The original source
remains historical lineage. The fallback is restricted to a validated path
inside the application's invoice archive, and the parent record plus original
page numbers are preserved in the rebuilt review entries.

### Implementation outcome

- OCR date candidates recognize the observed `Data doc.` abbreviation;
- `Data esecuzione` and debit-date labels mark payment evidence rather than
  invoice-date evidence;
- viewer selection uses vertically overlapping row spans as authoritative role
  evidence when they are available;
- a bank description that quotes `Pagamento fattura n. ... del ...` cannot use
  the quoted invoice date as the payment event;
- broad nearby text remains only supporting bank evidence and no longer moves a
  role label across rows;
- development reprocessing resolves archived invoice lineage when the transient
  batch origin has correctly been retired.

### Verification and closure

- 69 date-related extraction and segmentation tests passed;
- all 27 processing-storage tests passed;
- five focused regression tests passed, including previous in-place and
  targeted reprocessing behavior;
- review 025 invoice 1 was reprocessed in place from its archived invoice PDF;
- the resulting manifest contains invoice date `2024-01-03` on page 1 at the
  `Data doc.` value and payment date `2025-01-27` on page 2 at
  `Data esecuzione`;
- `parent_invoice_record_id`, source pages `[1, 2]`, and both archived PDFs were
  verified after the update.

The result is satisfactory because date roles now follow explicit geometry and
document semantics instead of document-specific wording or broad-window score
accumulation.

## Fail-Closed Economic Field Resolution

Status: completed.

### Theoretical problem

Economic OCR extraction has three distinct stages: observing tokens, assigning
semantic roles, and publishing user-facing fields. A high-confidence OCR token
or an exact label does not prove that the token has been assigned to the correct
accounting role. When the expected value next to an exact anchor is missing,
nearest-neighbour geometry can attach a different amount from the same table.

The application must preserve all observed candidates for diagnostics while
refusing to publish an accounting combination that violates hard invariants.
Missing data is recoverable by the user; confidently publishing an unrelated
amount is materially worse.

The observed regression contains a standard tax-summary structure:

```text
taxable amount 108.50
tax 23.87
gross document total 132.37 (not observed by base OCR)
withholding 4.34
net payable 128.03
```

The exact `Totale documento` anchor was linked to `108.50`. That assignment is
invalid because the same value belongs to the `Imponibile` column and because a
non-negative withholding cannot make gross total `108.50` become net total
`128.03`.

### Resolution theory and chosen approach

Use a fail-closed semantic resolver:

1. retain base OCR tokens and field candidates unchanged as evidence;
2. recognize values below explicit economic column headers as structural role
   candidates, independently from broad nearest-anchor linking;
3. evaluate hard accounting constraints after candidate selection;
4. invalidate only the roles contradicted by those constraints;
5. publish economic invoice fields exclusively through the resolved result;
6. leave the field empty when no coherent observed candidate exists.

An exact anchor remains strong role evidence, but it cannot waive an arithmetic
invariant. Targeted cell OCR is intentionally deferred. If the same missing-cell
pattern recurs, it can be added later as a bounded fallback after invalidation,
without changing the ordinary extraction path.

This is a final safety policy and a healthy incremental recovery strategy. It is
general to invoice accounting layouts and does not depend on supplier identity,
fixed page coordinates, or an expected literal value.

### Micro-steps

1. Add geometric header-to-value recovery for an explicit `Imponibile` column.
2. Reject a gross total that is lower than a separately observed net payable.
3. Ensure rejected direct candidates remain diagnostic but cannot populate the
   invoice field.
4. Preserve valid direct and mathematically reconciled existing cases.
5. Add focused regressions and reprocess only the affected invoice.

### Implementation outcome

- an exact `Imponibile` header is treated as a structural column only when its
  visual row also contains an economic companion such as `Percentuale`,
  `Ritenuta`, or `Imposta`;
- the closest amount below that aligned header becomes internal taxable evidence;
- deriving withholding from gross and net no longer uses an absolute difference
  when gross is below net, because that would legitimize an impossible ordering;
- a directly linked gross total below an independently observed net total is
  explicitly rejected and recorded as `rejected_gross_below_net`;
- rejected values remain in `economic_summary_fields` for diagnostics, but their
  `.value` target is not published and therefore cannot populate the invoice;
- no targeted OCR or new crop path was added.

### Verification and closure

- all 16 economic-summary tests passed, including the new structural and
  fail-closed regression;
- Python compilation passed for the changed extraction, manifest, and test files;
- the broader extraction file reached `166 passed`; seven unrelated filesystem
  fixture setups could not access existing Windows pytest temporary directories;
- review 025 invoice 1 was reprocessed in place from its archived invoice PDF;
- the real manifest now keeps `108.50` as internal taxable evidence, keeps net
  payable `128.03`, leaves gross invoice total empty, and retains the rejected
  `108.50` candidate only in diagnostics;
- no review draft exists for this invoice, so the corrected manifest is not
  masked by stale user edits.

The result is satisfactory because it reduces false positives without spending
additional OCR time or discarding source evidence. Targeted cell OCR remains the
next recovery layer only if repeated real documents justify its complexity.

## Directional Anchor Geometry For Invoice Numbers

Status: in progress.

### Theoretical problem

Invoice-number recognition must separate three independent questions: whether an
OCR token is a syntactically possible document number, whether a label actually
owns that token, and which owned token should populate a multi-page invoice.
Token shape is not semantic evidence: numeric, slash, and alphanumeric invoice
numbers are all legitimate, so preferring one shape can let an unrelated company
or registry identifier beat the real value.

Spatial proximity alone is also insufficient. A candidate above a later label
must not inherit that label merely because both fall inside a broad neighbourhood.
Label ownership requires directional reading geometry: either label and value are
aligned on the same row with the label first, or the label is above a value in the
same column. Alignment tolerances must derive from the observed bounding boxes,
not from supplier-specific page coordinates.

The observed review-002 invoice-030 case contains `N. fattura` followed by the
correct OCR token `8`. The unrelated `Rea GR-135284` appears above the label but
currently inherits it, receives an alphanumeric format bonus, and wins both page
selection and final invoice aggregation. A later payment page independently
observes `n.8`, confirming that the OCR itself is not the failure.

### Chosen approach

1. Keep format classification only as an admissibility and diagnostic property;
   give every accepted format the same neutral candidate base.
2. Build a bbox for each candidate anchor phrase and accept it only when its words
   form a coherent visual row.
3. Classify ownership as same-row-after or below-aligned using centre/interval
   alignment with bbox-relative tolerance; reject reverse reading order.
4. Keep invoice-level aggregation unchanged unless a real case still conflicts:
   corrected page ownership should remove the false target at its source instead
   of adding a compensating downstream rule.
5. Preserve all rejected/unselected OCR observations for diagnostics.

This is a final, general correction to evidence ordering. It does not add a REA
special case, a supplier name, a fixed coordinate, or a literal expected value.

### Micro-steps

1. Extend nearby anchor words with full geometry and derive phrase geometry.
2. Replace the one-dimensional `anchor_is_before_candidate` check with a
   directional relation usable by quality and score.
3. Remove format-dependent base scores.
4. Add focused same-row, above-label, below-label, and format-neutral regressions.
5. Run the invoice-number/segmentation tests and inspect the real persisted case
   without overwriting it.

### Implementation outcome

- all admissible invoice-number formats now use the same neutral base score;
- `format_kind` remains available for validation and diagnostics but cannot make
  an alphanumeric identifier outrank a numeric invoice number;
- anchor phrases must form a coherent visual row before they can own a value;
- valid ownership distinguishes a value to the right on the same row, a value
  below and aligned on X, and a bounded right-aligned value below a table header;
- the bounded table-header case has lower anchor quality than direct alignment;
- candidates above their alleged anchor receive no anchor evidence;
- no supplier, `REA` label, expected invoice value, or absolute page coordinate
  was introduced.

### Verification and closure

- 22 focused invoice-number tests passed;
- the wider invoice-number and viewer-segmentation family passed: `38 passed`;
- Python compilation and diff integrity checks passed;
- a read-only run against the persisted review-002 source tokens changed
  `GR-135284` from score `172.94`, false `n fattura` ownership, and selected state
  to score `74.0`, no anchor, and unselected state;
- the same real run selected `8` with exact `n fattura` ownership, anchor quality
  `4`, and score `178.51`;
- the persisted manifest was not overwritten, so the review must be reprocessed
  before the UI displays the corrected value.

The result is satisfactory because syntax, spatial ownership, and final evidence
remain separate concerns. The correction removes the false relationship where it
originates and preserves legitimate right-aligned table layouts with explicitly
lower confidence rather than relying on a document-specific exclusion.

### Design correction: continuous geometry

The first implementation made directional geometry categorical. It solved the
observed false positive, but it incorrectly converted evidence strength into
admissibility: candidates above an anchor or outside a bounded alignment region
lost the anchor entirely. No document invariant justifies that hard boundary.

Replace the categorical model with continuous evidence:

- lexical similarity determines whether text is an anchor at all;
- accepted number formats share one neutral base and never influence ranking;
- normalized proximity decreases continuously;
- horizontal layouts score vertical-axis alignment and expected reading order;
- vertical layouts score horizontal projection overlap, with below-anchor
  direction strongly preferred;
- above-anchor and reverse-order relationships retain small, non-zero evidence;
- multi-word anchor row coherence scales evidence instead of deleting it;
- exact/specific lexical quality remains a separate selection tier, preventing a
  fuzzy match from buying lexical certainty through geometric bonuses.

The score must remain monotonic: improving alignment or proximity while all other
features are fixed cannot reduce the result. Only syntactic impossibility, invalid
fiscal/amount/date shape, degenerate OCR boxes, and missing lexical similarity
remain hard exclusions.

Revised verification must demonstrate relative ordering, not only the winning
candidate: centred below > merely below > above; same-row after > same-row before;
and numeric/alphanumeric formats have equal base evidence.

### Revised implementation outcome

- categorical spatial admission was removed from anchor matching;
- every lexically accepted anchor window now retains a continuous geometry score;
- geometry combines normalized proximity, axis projection alignment, expected
  reading direction, and multi-word row coherence;
- expected relations (`same_row_after` and `below`) use the full evidence range;
- reverse relations (`same_row_before` and `above`) retain a deliberately small,
  non-zero range rather than being discarded;
- exactness is recorded separately as lexical quality and cannot be manufactured
  by geometry;
- all admissible invoice-number formats retain the same base score of `50`;
- the previous categorical implementation and its closure are superseded by this
  revised outcome.

### Revised verification and closure

- the focused invoice-number family passed: `23 passed`;
- the wider invoice-number and viewer segmentation family passed: `39 passed`;
- the geometry regression proves the relative score ordering for centred below,
  offset below, above, same-row after, and same-row before relationships;
- the format-neutral regression proves equal unanchored scores for numeric and
  alphanumeric candidates;
- read-only evaluation of the real review-002 invoice-030 tokens retains weak
  `n fattura` context on `GR-135284` but scores it `99.90`, below selection;
- the correctly aligned `8` scores `179.38` and is selected;
- Python compilation and diff integrity checks passed;
- the existing persisted manifest was not overwritten.

The revised result is satisfactory because the model now represents evidence as
evidence: direction and alignment change confidence monotonically without erasing
observations. Hard rules remain limited to true type/validity constraints.

Status: completed.
# Current point - Withholding becomes a monetary amount

## Problem definition

The user-facing `ritenuta` field currently represents a percentage, while the
accounting workflow requires the withheld monetary amount. This is a data
contract change across extraction, review, validation, persistence and export;
renaming the label alone would silently corrupt meaning.

## Resolution theory

The canonical reviewed value must be the monetary amount because it participates
directly in the invariant:

```text
totale fattura - ritenuta = totale netto fattura
```

The printed percentage remains useful extraction evidence and may stay in OCR
diagnostics, but it must no longer be exposed or validated as the final field.
Legacy percentage fields should be converted only when the amount can be derived
unambiguously from gross and net totals; otherwise they must not be reinterpreted
as money.

## Chosen approach

Final choice:

- expose `ritenuta_value` as the viewer field and label it `Ritenuta`;
- format and validate it with the shared monetary rules;
- enforce the accounting invariant when all three amounts are present;
- retain `ritenuta_percentuale` only as internal extraction evidence;
- migrate legacy percentage-shaped fields in memory from gross minus net;
- update backend persistence validation so frontend checks cannot be bypassed.

## Micro-steps

1. Update manifest field resolution and labels.
2. Update frontend formatting, validation and legacy adaptation.
3. Update backend validation and persisted-record migration.
4. Update focused tests and documentation contracts.
5. Run Python tests, frontend build and lint; review remaining percentage uses.

## Implementation outcome and closure

Status: completed.

The viewer manifest now exposes the resolved absolute withholding amount and labels
the field `Ritenuta`. Percentage recognition remains inside economic-summary
resolution only as supporting evidence for taxable-base and arithmetic checks.

The React review and registry editors format withholding through the same money
normalizer as gross and net totals. A non-monetary or negative withholding is
blocking, and a complete triplet is blocking unless `gross - withholding = net`
within one cent. The Python persistence boundary repeats both checks so API callers
cannot bypass the UI.

Invoice registry schema version 3 migrates legacy percentage-labelled fields from
the unambiguous gross/net difference. The frontend applies the same adaptation to
legacy manifests and drafts in memory. If gross and net are unavailable, a legacy
percentage is cleared rather than misrepresented as money. New feedback revisions
mark withholding with `value_semantics = monetary_amount`, allowing future training
code to exclude older percentage-semantic observations.

Verification:

- 36 economic-summary/viewer tests passed;
- 10 focused extraction, validation, export, migration and draft tests passed;
- 64 persistence, processing-storage and feedback-collection tests passed;
- frontend production build passed;
- frontend lint passed with no errors and one pre-existing exhaustive-deps warning;
- the broader extractor run had no logic failures, but seven unrelated `tmp_path`
  cases could not run because Windows denied access to pytest's temporary directory.

The result is satisfactory because one field kind now has one user-facing accounting
meaning across extraction, editing, storage, export and learning data, while useful
percentage evidence remains available internally without leaking into the contract.

## Post-implementation audit - monetary withholding invariants

### Problem redefinition

The first implementation changed the public field type but retained two percentage-
driven decisions inside extraction. One path could synthesize withholding from a
default 4% assumption; another required a recognized percentage before accepting an
otherwise exact cross-page accounting triplet. These are opposite symptoms of the
same obsolete model.

The canonical rule is now stricter:

```text
gross and net observed -> withholding may be derived as gross - net
gross, withholding and net observed -> the equation must hold
percentage observed -> optional OCR evidence only
```

A percentage must never create the reviewed withholding amount by itself and must
never veto an exact monetary equation. All non-empty gross, withholding and net
values must be valid unsigned monetary amounts before save; malformed values cannot
disable the equation by failing parsing.

The feedback collection is currently empty, so v1 adopts monetary semantics directly.
No mixed percentage/amount training migration is needed. Legacy operational manifests
and registry rows may still be adapted at the UI/storage boundary, but every newly
captured feedback value uses the monetary contract.

### Micro-steps

1. Remove default-percentage withholding synthesis from combinatorial extraction.
2. Derive a missing withholding candidate only from observed gross and net amounts.
3. Make cross-page percentage matching an optional score bonus, never a gate.
4. Validate all three accounting fields as unsigned money in frontend and backend.
5. Add regressions for no-percentage reconciliation, no invented 4%, malformed
   accounting values and monetary feedback semantics.
6. Run focused Python tests, frontend tests, build and lint, then review all remaining
   percentage references as diagnostics versus executable constraints.

### Implementation outcome and closure

Status: completed.

Percentage evidence no longer synthesizes a withholding amount. The combinatorial
resolver derives withholding only from two distinct, strongly identified gross and
net observations. It also rejects weakly relabelled accounting candidates from
closing the equation by themselves. A recognized percentage can add confidence when
it agrees, but its absence or disagreement cannot veto an exact monetary triplet.

Cross-page reconciliation now accepts direct monetary withholding without requiring
a percentage. Taxable-base/percentage agreement remains an optional score bonus.

Frontend and backend validation now apply the same unsigned-money contract to gross,
withholding and net: non-empty values containing signs, letters, percentages or an
invalid monetary representation are blocking. Batch save also blocks both malformed
amounts and an accounting mismatch; previously only malformed withholding blocked
that path.

The feedback collection adopts `monetary_amount` directly for newly collected
withholding values. The dataset is empty, so no historical mixed-semantics migration
is required. Operational legacy manifests and registry rows keep their conservative
gross-minus-net adapter independently of the training dataset.

Verification:

- 20 focused economic extraction and reconciliation tests passed;
- 7 focused accounting validation tests passed;
- all 23 invoice-registry storage tests passed sequentially;
- all 15 feedback collection/export tests passed;
- 169 extractor tests passed; 7 unrelated `tmp_path` setups remain blocked by the
  existing Windows pytest-directory ACL problem;
- frontend money-formatting test passed with Node test isolation disabled because
  process spawning is restricted in the current environment;
- frontend production build passed;
- frontend lint has zero errors and the existing single exhaustive-deps warning;
- Python compilation and diff integrity checks passed.

The result is satisfactory because the final accounting value is now governed by
observed monetary evidence and the accounting invariant, not by the former percentage
contract. Missing evidence remains missing instead of being completed from a default
rate.

## Current point - Consolidate the invoice registry toolbar

### Problem definition

The invoice registry separates primary controls from a second filter panel. The
second panel consumes vertical space and duplicates filtering already covered by the
main search. The page should expose one coherent toolbar before the data table.

### Chosen approach

- remove the separate filter panel entirely;
- remove issuer and condominium filter state rather than leaving invisible filters;
- place date start, date end and sort controls in the main header between the page
  identity and search;
- preserve the agreed order: identity, `Da`, `A`, `Ordina`, search, actions;
- keep a single-row desktop layout and use an intentional two-row arrangement only
  when the viewport cannot contain every control without compression.

### Verification plan

1. Build and lint the viewer.
2. Inspect the registry page at wide and narrower desktop widths.
3. Confirm that date filtering, sorting, search and actions remain operable and that
   no invisible issuer/condominium filter survives.

### Implementation outcome and closure

Status: completed.

The registry now has one toolbar. Issuer and condominium filters were removed from
both the interface and component state, so no invisible filter can survive after
the second row disappears. Date range and sorting sit between the page identity and
the existing search; view and record actions remain at the end.

The toolbar stays on one row at 1728 and 1920 pixels. At 1440 pixels it uses an
intentional two-row arrangement without overlap: identity, dates and sorting remain
primary, while search and actions occupy the second row. The table starts directly
after this single responsive header.

Verification:

- frontend production build passed;
- frontend lint passed with no reported warnings or errors;
- visual checks passed at 1440x900, 1728x972 and 1920x1080;
- date, sorting, search and action controls remain present in the agreed order.

The result is satisfactory because it removes redundant vertical structure and
state while preserving every useful registry operation at desktop widths.

## Current point - Move sibling-page navigation to the sidebar

### Problem definition

Archive, registry, and process pages currently mix two different responsibilities
inside their headers: navigation between sibling views and operations on the active
view. This consumes scarce horizontal space and weakens the information hierarchy,
while the dedicated page sidebar contains only the return action.

### Chosen approach

- treat `PDF fatture / File origine`, `Fatture salvate / Fatture scartate`, and
  `In corso / Cronologia` as sibling-page navigation;
- move all three switches into one shared vertical sidebar pattern;
- keep search, filters, selection actions, editing, export, reprocessing, and delete
  controls in the page header because they act on the current content;
- preserve every existing state reset when changing views;
- remove the obsolete horizontal tab styling and rebalance each header after the
  navigation controls leave it.

### Verification plan

1. Build and lint the viewer.
2. Exercise every sidebar switch and verify active state plus selection resets.
3. Inspect Archive PDF, Invoice Registry, and Processes at supported desktop widths.
4. Confirm that headers have no empty or misaligned regions after the move.

### Implementation outcome and closure

Status: completed.

All sibling-page navigation now lives in a shared vertical sidebar pattern. The
active page is rendered as a white selection against the green sidebar; inactive
siblings remain quieter but clearly interactive. Active styling also remains stable
under hover, avoiding ambiguity immediately after navigation.

The three headers now contain only current-view information and operations:

- PDF Archive retains search, reprocess, and delete;
- Invoice Registry retains dates, sorting, search, edit/export, reprocess, and delete;
- Processes retains only the history delete action when applicable.

The original view-change side effects were preserved: archive focus and selections
are cleared, registry selections and edit mode are cleared, and process history
keeps its existing state contract. The obsolete horizontal tab CSS was removed.

Verification:

- frontend production build passed;
- frontend lint has no errors and retains one pre-existing exhaustive-deps warning;
- all six sibling views were exercised through the running local app;
- visual checks passed at 1440x900, 1728x972, and 1920x1080;
- a DOM assertion confirmed that the header contains no remaining tab list.

The result is satisfactory because navigation, content controls, and row actions now
have distinct visual locations, while the implementation reuses one sidebar pattern
without changing persistence or business behavior.

## Current point - Make batch-save validation accurate and actionable

### Problem definition

Batch-save validation currently mixes three different states: real unresolved
problems, already resolved registry choices, and zero-count categories. This makes
the confirmation dialog noisy and can block a valid save without identifying the
invoice that needs attention. Legacy review drafts created while withholding meant
a percentage can also reach the new monetary validator before canonicalization.

The general requirement is that validation must run on the exact canonical payload
that will be persisted, and every reported issue must still require user action.

### Chosen approach

- canonicalize legacy percentage-labelled withholding at the frontend persistence
  boundary and repeat the migration in the Python persistence boundary;
- preserve the accounting comparison as a warning, but do not treat a document's
  own inconsistent figures as invalid user input;
- consider a multi-row registry match resolved when the invoice carries a valid
  explicit registry row selection;
- show only non-zero issue categories and include the affected invoice plus the
  concrete reason, so blocking feedback is actionable;
- snapshot the same canonical invoices for validation and subsequent batch save.

### Verification plan

1. Add backend regressions for legacy draft normalization and true monetary mismatch.
2. Build and lint the viewer.
3. Audit the current persisted drafts against the canonical monetary equation and
   confirm that selected registry rows no longer count as unresolved conflicts.
4. Exercise the batch confirmation through the local app and inspect the dialog.

### Implementation outcome and closure

Status: completed.

Batch validation now runs on the same canonical invoice snapshots that are later
persisted. A draft carrying the historical `Ritenuta %` label is converted from the
observed gross/net difference before validation, while a newly entered percentage
under the current `Ritenuta` label remains invalid. The Python persistence boundary
repeats this normalization, so a stale frontend cannot reintroduce the old semantic.

Registry conflicts are no longer reported after the user has selected one of the
actual matching registry rows. Unresolved ambiguous fiscal codes still produce a
warning. The batch dialog omits zero-count categories and expands every remaining
category with the invoice title and concrete reason; long reports scroll inside the
dialog instead of exceeding the viewport.

The live-data audit found that all five multi-row registry matches represented in
the current drafts have a valid selected registry row. It also identified one real
remaining accounting mismatch in `Fattura 020`: `132.27 - 4.34` does not equal
`128.03`. The revised dialog exposes that exact invoice and equation, but the user
can still save because invoices may legitimately contain internally inconsistent
figures that must be recorded as printed.

Verification:

- all 25 invoice-registry storage tests passed, including legacy-label migration,
  rejection of newly entered percentage syntax, and acceptance of inconsistent but
  formally valid accounting figures in both review and registry-edit saves;
- frontend production build passed;
- frontend lint passed with zero errors and the pre-existing exhaustive-deps warning;
- the batch-save dialog was exercised through the running local app at 1440, 1728,
  and 1920 pixel widths;
- temporary screenshots were inspected and deleted;
- visual score: 8.5/10 for hierarchy, actionable detail, proportions, and consistency;
  no touched state scored below 7.

The result is satisfactory because false warnings are removed, malformed monetary
input remains blocked, and accounting inconsistencies stay visible without changing
or preventing the user's faithful transcription of the document.

## Current point - Enforce stable review-invoice identity

### Problem definition

A review is a projection of invoices coming from one or more processed documents.
The current close operation identifies an invoice only by its manifest-local ID.
After reprocessing produced duplicate projection entries, saving the visible invoice
closed only one entry while removing every equal ID from the viewer projection. The
result was a review reported as pending but containing no visible invoices.

The general invariant is:

```text
review invoice identity = review + process + document + manifest invoice ID
```

Exact duplicate projection rows are corruption of one logical identity, not separate
invoices. They must be coalesced, with a terminal saved/discarded state taking
precedence over a stale pending copy.

### Chosen approach

- pass the document identity through load and close operations;
- update only the matching document in the viewer projection;
- close every exact duplicate of that composite identity atomically;
- deduplicate review projections when they are created or reprocessed;
- make review listing repair historical exact duplicates and retire a completed
  projection, so existing empty reviews heal without reprocessing or duplicate saves;
- preserve invoice-registry record IDs, which already contain the composite frontend
  invoice identity; fix only the review layer that had discarded the document part.

### Verification plan

1. Reproduce duplicate IDs within one document and equal local IDs across different
   documents in isolated processing-storage tests.
2. Verify that closing one logical invoice never hides another document's invoice.
3. Verify that a historical saved-plus-pending duplicate is repaired and retired.
4. Run invoice-registry, processing-storage, and API queue tests.
5. Inspect the live Revisione 002 state after invoking the non-destructive listing
   reconciliation.

### Implementation outcome and closure

Status: completed.

Review load and close operations now accept the document identity in addition to the
manifest-local invoice ID. Closing an invoice updates only its document projection;
exact duplicate rows for the same composite identity are closed together, while an
equal local ID belonging to another document remains pending and visible.

Projection creation rejects duplicate composite identities. Reprocessing and
segmentation refresh coalesce historical duplicates. Review listing also performs an
idempotent repair: terminal `saved` or `discarded` state wins over a stale pending
copy, the viewer index is rebuilt transactionally, released OCR working documents
are cleaned, and a fully terminal review is retired. Recovery keys now include the
document ID, with a wildcard fallback only for legacy registry rows that never
stored it.

Invoice-registry record IDs remain unchanged because their frontend `invoiceId`
already contains `document + manifest invoice ID`. Avoiding a second document prefix
keeps retries idempotent and prevents needless identifier migration.

Verification:

- 79 processing-storage, invoice-registry, and registry-API tests passed;
- focused regressions cover equal local IDs in different documents and historical
  exact duplicates with `saved + pending` states;
- live reconciliation removed Revisione 002 from the API and deleted its review
  projection directory;
- the three stale OCR document directories (`doc_000010`, `doc_000014`, and
  `doc_000015`) were released;
- the registry still contains the expected 13 outcomes for Revisione 002: 12 saved
  and one discarded; reconciliation created no additional records;
- diff integrity checks passed.

The result is satisfactory because review visibility and persistence now share one
composite identity, historical corruption heals deterministically, and cleanup no
longer depends on a manifest-local ID being globally unique.

## Current point - Move removed export rows to discarded invoices

### Problem definition

The invoice registry currently uses the same delete projection for saved and
discarded rows. Removing a row from `Fatture da esportare` merely hides it from the
registry, although the user's intent at that stage is to classify the invoice as not
exportable. Hiding and discarding are different domain transitions.

### Chosen approach

- treat removal from `Fatture da esportare` as a status transition from `saved` to
  `discarded`;
- preserve invoice and source PDFs, invoice fields, lineage, and original save time;
- record the discard timestamp and keep the row visible in `Fatture scartate`;
- retain the existing remove-from-view behavior when deleting an already discarded
  row;
- make confirmation and progress copy describe the actual operation rather than a
  generic deletion.

### Verification plan

1. Add a storage regression proving the status transition and PDF preservation.
2. Build and lint the viewer.
3. Exercise the confirmation from both saved and discarded registry views.

### Implementation outcome and closure

Status: completed.

The registry trash action now follows the semantics of the active view. In
`Fatture da esportare`, selected saved records transition to `discarded`; their
compiled fields, invoice PDF, source PDF, lineage, and original save timestamp are
preserved, while a discard timestamp is added. The records consequently disappear
from the export queue and become visible in `Fatture scartate`.

In `Fatture scartate`, the same control retains the existing explicit
remove-from-view behavior. The confirmation title, message, progress state, tooltip,
and error copy distinguish the two operations, so the UI no longer describes a
classification change as destructive deletion.

Verification:

- all 26 invoice-registry storage tests passed;
- the new regression proves the state transition and preservation of both PDF
  archives and invoice data;
- frontend production build passed;
- frontend lint passed with zero errors and the pre-existing exhaustive-deps
  warning at `App.tsx:4588`.

The result is satisfactory because the export queue now has a reversible domain
destination for unwanted invoices instead of silently hiding them, and no archive
asset is deleted by that transition.

## Current point - Condominium invoice exports and delivery history

### Problem and resolution theory

Export is a durable accounting delivery, not just table serialization. It must
preserve grouping identity, fiscal identifiers, numeric types, and the exact set
of delivered rows. Failed generation must not remove rows from the pending list.
Browser download completion cannot prove that a user retained the file: retain
the generated artifact internally and provide a repeatable download instead.

Use established XLSX features: explicit text identifiers, native dates/amounts,
frozen headers, filters, controlled widths, and repeatable print titles. References:
https://openpyxl.readthedocs.io/en/stable/print_settings.html and
https://openpyxl.readthedocs.io/en/latest/styles.html.

### Chosen approach and micro-steps

Final choice: keep accounting status `saved`; track delivery separately through
`last_exported_at` and `last_export_url`. This avoids changing PDF archive and
reprocessing semantics. Pending/exported registry views partition saved records.

1. Add an export service: validate selected IDs, group by condominium fiscal code
   (name fallback, isolate unknown identities), resolve registry codes without
   ambiguous guesses, generate multi-sheet XLSX or ZIP of individual workbooks.
2. Persist an immutable internal artifact before atomically updating registry
   delivery metadata. Retrying download never changes the export timestamp.
3. Wire POST through the real local-app handler; preserve the existing read-only
   legacy XLSX route. Add mutually exclusive radio options in a confirmation modal,
   progress, success download and error states.
4. Activate exported view, last-export timestamp, re-export and date filters based
   on last export in that view. Export uses selected visible rows, or all filtered
   rows when none are selected; editing must be saved before export.
5. Verify XLSX/ZIP contents and styles, grouping/name collisions, failure behavior,
   repeated exports, real HTTP transport, frontend build/lint, minimal screenshots.

Primary columns remain white: client code, issuer name, document date, number,
gross compensation. Remaining invoice fields and provenance use light gray.
Condominium code/name/CF appear once in a green-accented header. Identifiers remain
text (leading zeros intact); untrusted text is never interpreted as Excel formulas.

Status: implementing. No production invoice data may be changed by verification.

### Critical review during implementation

The first UI inspection caught a real presentation defect: full date/time plus a
download link overflowed the existing date column. Keep the visible date and the
repeat-download link on separate lines; expose the exact time on hover. Modal
choices use radio buttons because selecting both formats simultaneously is invalid.

The delivery artifact is a deliberate persistent output, not a disposable temp
file. The commit point is successful artifact creation plus atomic registry write;
it does not claim that the user accepted a browser save dialog. Previously generated
artifacts remain immutable after re-export. A process killed between artifact write
and registry commit can leave an unreferenced export, but cannot mark a missing file
as delivered. Normal exceptions remove the failed artifact.

Serialization now covers registry writers and export, preventing a simultaneous
save from overwriting delivery metadata. This is an in-process lock, consistent
with the application's single backend; multiple independent backend instances
sharing one runtime are not supported by this change.

The HTTP review found that archive downloads were served only by the composed local
app. Export download routing now lives in the API handler and is delegated by the
local app, with an allowlisted immutable artifact path. Tests cover both composition
roots without mocking transport or storage. Registry-code lookups are indexed once
per export rather than scanning the full anagrafica for each invoice.

### Outcome, verification and closure

Status: completed.

Exports now offer one workbook with a sheet per condominium, or a ZIP containing
one workbook per condominium. Saved records without delivery metadata populate
`Fatture da esportare`; delivered records populate `Fatture esportate`. Re-export
updates the latest delivery date only for the selected/filtered target set. In the
exported view, date-range controls explicitly filter the export date. The exact
time is available on hover and the last generated file can be downloaded again.

Workbook checks confirm condominium header/code/CF, issuer client code, requested
column order, numeric compensation before withholding, native dates, leading-zero
preservation, white primary columns, gray supplemental columns, collision-safe
sheet/file names, and literal (non-formula) text. Unknown codes remain empty;
ambiguous fiscal matches are resolved only when an explicit registry row exists.

Checks completed:

- 34 Python tests passed (26 registry tests plus 8 export tests), including failed
  generation, failed registry commit, partial selection, repeated delivery,
  ambiguous/explicit registry codes, and real HTTP export/download through both
  RegistryApiHandler and LocalAppHandler;
- production build passed; lint has zero errors and the pre-existing dependency
  warning for applyReviewDraftsToInvoices;
- temporary Playwright check `node tools/ui-checks/checks/check-invoice-export.mjs`
  exercised the actual built frontend at localhost:8779 against an isolated seeded
  runtime: workbook download, persisted view transition, date exclusion, selected
  ZIP re-export, and reload. No HTTP or storage mocks were used. Production data
  were not modified. The temporary script and fixture runtime were removed;
- two temporary screenshot paths were inspected, then reused once after correcting
  the date-column overflow. Modal score: hierarchy 8, spacing 8, readability 8,
  consistency 8, overall 8. Exported table: hierarchy 8, spacing 7, readability 8,
  consistency 8, overall 7.5. The existing wide table still needs horizontal scroll;
  this change retains its shared virtualized layout. Screenshots were deleted.

Limits: workbook contents/styles were inspected with openpyxl, not rendered by
Microsoft Excel. Browser download completion does not prove a file was retained by
the user; the internal artifact provides recovery. Export artifacts are retained
as deliberate delivery snapshots, including older re-exports; no automatic retention
policy or export-file management UI is introduced here. A hard termination between
artifact creation and registry commit can leave an orphan artifact, while invoices
remain pending and can safely be exported again.

### Follow-up - Consistent invoice-date filtering

User clarification supersedes the export-date filtering described above: the
From/To range must always refer to the invoice date, including the exported view.
Removed the export-date branch from the shared row filter. Latest-export timestamps
remain visible and their explicit sort options remain available independently.
Both initial export and re-export already consume the filtered rows, so they now
share the corrected date semantics. Build passed; lint reports only the existing
useEffect dependency warning.

## Current point - Persistent export groups

### Problem, theory and chosen approach

Delivery membership must outlive changes to individual invoices. Last-export
metadata cannot reconstruct an earlier batch after partial re-export or deletion.
Use immutable operation receipts with stable IDs and member summaries. Store each
receipt in the same registry commit as delivery metadata, avoiding a second JSON
transaction. This is an established event/snapshot pattern, not a novel approach.

Final choice: a top-level export_history list in the existing registry JSON. Each
successful export appends an ID, timestamp, format, download reference, and immutable
invoice summaries. A history re-export resolves membership server-side under the
registry lock and validates every member before generating anything. It uses current
invoice data and creates a new history entry; original membership stays unchanged.

Availability means saved and visible in the accounting registry. PDF removal alone
does not make spreadsheet data unavailable. Missing, hidden or discarded rows block
the entire re-export, including stale UI requests. History deletion removes only
the receipt, with confirmation; invoice records and their exported status survive.
An export artifact still referenced by an invoice stays available for download.

### Micro-steps and checks

1. Extend the export commit with receipts; implement server-owned group re-export
   and history deletion. Preserve existing registry schema compatibility.
2. Add the sidebar subpage, newest-first compact virtualized list, invoice-info
   dialog, deletion confirmation, and reuse the format-selection dialog.
3. Test repeated/overlapping exports, unavailable member atomicity, durable info
   after removal, delete isolation, and real local-app HTTP contracts.
4. Build/lint and exercise the UI against an isolated runtime. Keep screenshots
   limited to the history list and member dialog, inspect and delete afterwards.

Legacy limit: older exports did not store complete membership. Do not fabricate
historical groups from last_export_url, which may contain only surviving members.
The new history starts with exports made after this update; previous invoice export
timestamps and downloads stay intact.

Status: implementing.

### Critical review and verification progress

The first screenshots showed unnecessary empty white height for a two-row history
and truncated member-table headings. Let both virtualized viewports shrink to their
content with a bounded maximum height, retaining scrolling for long histories; use
the existing concise document labels. Availability is based on registry records,
not PDF files, since an accounting re-export consumes saved field values.

Deleting a receipt keeps its artifact when an invoice still references it as its
latest download; an unreferenced artifact is removed only within the app-owned
export directory. File cleanup failure is logged after the receipt commit and never
deletes accounting data. Re-export is all-or-nothing under the registry lock.

### Outcome and closure

Status: completed. The registry sidebar now includes Storico esportazioni. Receipts
are displayed newest-first using actual timestamps (including offset), with a
date/time label, invoice count, original format, info, replay and confirmed delete.
Info uses immutable member summaries, including unavailable invoices. Replay offers
the existing two formats, validates server-owned membership, uses current values,
and creates a new receipt. Previous receipts do not shrink when members disappear.

Verification:

- 39 tests passed across invoice export and registry storage; new checks cover
  overlapping exports, replay ID tampering, missing/hidden/discarded members,
  receipt deletion isolation, artifact references and both real HTTP entry points;
- build passed; lint has zero errors and the existing useEffect dependency warning;
- temporary Playwright script check-export-history.mjs exercised the actual local
  app on an isolated runtime at port 8779 without HTTP/storage mocks: ordering,
  member info, alternate-format download, receipt creation, cancel/confirm deletion,
  persistent reload, and blocked replay after a registry row was removed;
- two screenshot paths inspected, then recaptured once after compacting the layout.
  History: hierarchy 8, spacing 8, readability 8, consistency 8, overall 8.
  Info/unavailable state: hierarchy 8, spacing 8, readability 8, consistency 8,
  overall 8. Shared virtualization remains; narrower windows can scroll the table
  horizontally. Visual inspection was at 1440x900, not an exhaustive viewport audit;
- screenshot files, temporary UI scripts and seeded runtime removed; test server
  stopped. No production invoices or exports were modified.

The existing atomic JSON registry can represent this operation cleanly without
introducing a second persistence system. Remaining limits are legacy membership
unavailability and the already documented export artifact/registry crash window.

## Current point - Simpler invoice workbook layout

Problem: frozen rows and columns made the accounting workbook behave as separate
scrolling regions. The heavy partial-width banner and filter controls added visual
complexity to a file intended for straightforward reading.

Choice: retain the established data columns and native Excel date/number types,
but use one full-width pale-green title, compact condominium metadata, a restrained
header row, white primary columns, and very light gray supporting columns. Remove
freeze panes and visible filter controls, so the entire sheet scrolls together.
The same sheet builder serves both export formats. Verify the generated workbook
structure and both modes with focused tests; existing downloaded files remain
unchanged, while future exports use the new styling.

Status: complete. All 13 invoice-export tests pass, including checks for no
frozen panes in both delivery formats, retained values and native amount/date
types. The workbook was inspected structurally with openpyxl; it has not been
visually opened in Excel in this session. Existing downloads are intentionally
not rewritten. The simpler formatting removes controls without changing the
export storage contract or invoice data.

Follow-up: remove the three administrative/file-reference columns from both
workbook modes. Put the condominium registry code and fiscal code in separate
plain-text cells beside their labels so exact values (including leading zeros)
can be copied alone. Center all visible cell contents and derive the title/print
extent from the remaining ten columns. Accounting values and native Excel
date/amount formats are unchanged. All 13 focused export tests pass; no old
export artifact was rewritten.

## Current point - Application and pending extraction audit

Problem: accumulated changes need an evidence-based audit of extraction quality,
runtime integrity, and maintenance cost before further optimization. Missing values
alone are not errors; user drafts are not OCR ground truth; old manifests may
precede current rules.

Approach: read-only inventory of the actual user runtime, trace pending invoices
through review indexes and manifests, compare selected fields with OCR candidates
and nearby labels, visually inspect high-confidence date/amount anomalies against
page images. Review storage/queue/export/frontend boundaries and hot paths in code.
Use focused isolated tests or pure-function reproductions where they establish a
specific finding. Do not reprocess or modify production invoices. This audit will
produce docs/ultime_ottimizzazioni.md in English with observed Italian OCR excerpts,
priorities, exact examples, proposed minimal fixes, and verification criteria.

Micro-steps: inventory and coverage; extraction triage across all pending invoices;
confirm representative cases; review architecture and integrity; consolidate a
prioritized actionable report separating proven defects, hypotheses, and refactors.
Status: complete, 2026-10-07. Report: [Final Optimization Audit](ultime_ottimizzazioni.md).

Critical decisions and qualifications:

- Census covers 453 pending invoices in 33 reviews, referencing 182 manifests.
  No missing/ambiguous pending invoice references or viewer membership were found.
  All stored withholding labels are legacy percentage labels; raw missing counts
  cannot be presented as current UI counts. Two drafts also differ from extraction.
- Existing OCR was sufficient for bounded pure-function comparisons; no new OCR or
  production mutation was needed. Current date selection changes 84 invoice dates
  and 37 payment dates, but these are differences, not measured improvements.
- Challenged the first same-day-payment finding by including the shared-payment
  linking pass. It restores some cases, including R027/F028. The report explicitly
  avoids presenting an intermediate selector failure as that invoice's final result.
- Challenged the anchor-only explanation with isolated counterfactuals. Adding
  `importo pagamento` or `netto vs debito` resolves the selected examples; adding
  `totale a pagare` does not repair R006/F083. Arithmetic consistency alone accepts
  incorrect role assignments, so selection/provenance needs work before more rules.
- Visually checked R022/F193, R005/F062 and R006/F083 against existing page images.
  Other census hits are not represented as manually certified ground truth.
- Current economic resolution already rejects several old gross-below-net values.
  Repeating that guard would not solve parser truncation or incorrect net selection.
- Architecture findings distinguish reproduced parser/extraction defects from
  code-established crash/concurrency risks. No destructive crash or race test was
  performed against production. Performance proposals have no invented speedup claim.

Verification: 326 Python tests passed (240 Pillow deprecation warnings); frontend
build passed; lint has zero errors and one existing hook dependency warning.
No UI change, screenshot session, or app lifecycle change was required. Temporary
audit scripts and duplicate OCR diagnostic JSON are removed after report creation.

Closure: the evidence supports small regression-driven corrections rather than a
blanket anchor expansion or full rewrite. The report contains concrete examples,
code entry points, priorities, reproduction locators and acceptance criteria.
It is an audit/backlog, not authorization to mass-reprocess or overwrite drafts.
Application code and user runtime data remain unchanged by this audit.

### Audit follow-up - Concrete correction map

User requested an anchor/logic solution for each finding only where the solution
is clear. Added section 10 to `ultime_ottimizzazioni.md`: E1-E7 corrections,
exact proposed net-anchor tuples, historical-case dispositions, and A/O work.
Separated established behavior from untested ranking/geometry choices. In
particular, no invented weights, broad `dell` replacement, automatic malformed
money repair, or claim that `totale a pagare` fixes its example. Some fixes prevent
a false positive without recovering the missing value; that distinction is explicit.

Status: documentation complete. Checked coverage against the report's finding IDs
and prior evidence. No code/data changes or new extraction/test runs; prior audit
test results remain identified as prior results rather than follow-up verification.

### Audit implementation - Bounded corrections

Problem: apply demonstrated small corrections without mixing them with unsettled
ranking, monetary parsing, or persistence redesign. Use the existing extraction
pipeline and regression fixtures rather than introducing new abstractions.
Scope: two net anchors, numeric-hyphen-year invoice IDs, distinct explicit same-day
payment evidence, literal registry Excel text, and unreachable-code removal.
Same-day payment must not reuse the invoice-date occurrence or a print timestamp.
Plan: minimal edits; synthetic positive/negative integration tests; full Python
suite; update the audit with actual coverage and remaining limits. No production
reprocessing, UI change, or data migration. Status: in progress.

Closure: complete for this bounded scope. Added the two observed net anchors,
numeric-hyphen-year candidate format, distinct explicit same-day payment support,
literal registry Excel cells, and removed unreachable code. Sixteen new regression
cases cover positive/negative extraction, viewer selection, and real XLSX reimport.
During review, avoided a per-record worksheet scan by setting text types in the
existing column traversal. No score weights or general parsing rules changed.

Final checks: 342 Python tests pass (240 existing Pillow warnings); build passes;
lint reports zero errors and the existing hook warning. Sandbox blocked pytest's
temporary directory on the first attempt; rerun with approval completed normally.
Verification bypasses OCR, HTTP/browser and packaging, and does not establish
corpus-wide accuracy. No production data was reprocessed. Report section 11 records
implemented behavior, coverage and explicitly deferred nontrivial work.

## Current point - Optional withholding export summary

Problem: provide an aggregate check for an export's exact invoice membership,
without adding another competing detail format or changing date filters/history.
Choice: existing mutually exclusive formats in a bordered fieldset, independent
unchecked summary checkbox below. Optional separate XLSX in the same ZIP delivery;
no summary-only mode or historical-value snapshots (user rejected that complexity).
Reuse condominium grouping/code lookup and current-value history replay. Sum stored
withholding with Decimal, expose missing/invalid counts instead of assuming zero.
No tax-code inference or claims of fiscal payment. Preserve existing artifacts when
unchecked. Persist the option in history, default false for older receipts.
Steps: backend summary/bundle, API boolean, dialog state/style, unit and actual
local-app transport tests, build/lint, isolated Playwright export/reexport and
temporary screenshots. No writes to production. Status: in progress.

Closure: implemented. `include_summary` is a validated optional API boolean,
recorded in export history and restored by the replay dialog (legacy false).
Both detail formats can include `riepilogo_ritenute.xlsx` in the existing ZIP
artifact; unchecked delivery is unchanged. Summary groups exactly the exported
membership, uses Decimal sums, shows missing/invalid/negative amounts as excluded
with a count, keeps unknown totals blank, and does not infer tax payment or period.
The workbook uses centered text, restrained green headers, highlighted incomplete
rows, numeric amounts, literal codes and no frozen panes. No extra artifact lifecycle
was introduced; existing ZIP download/cleanup ownership still applies.

Verification: 348 Python tests passed (240 existing Pillow warnings); build passed;
lint zero errors, one existing hook warning. Added both-format aggregation, missing
values, strict option validation, and actual API/local-app HTTP download coverage.
HTTP tests patch the runtime location only: real handlers, persistence and XLSX/ZIP
serialization run. They do not exercise OCR or packaging.

Playwright: `node check-summary.mjs` in tools/ui-checks, isolated local-app server
at 127.0.0.1:8789. Real navigation, export/download, history preference restoration,
mode change and replay/download passed with no route mocks. Initial script attempts
used button selectors for menuitem/tab roles; corrected the test, not the app.
Inspected temporary export screenshot at 1440x900 and replay at 1024x768. Each:
hierarchy 8, spacing 8, typography 8, consistency 8, responsiveness 8, overall 8.
The bordered radio group is clearly separate from the additive checkbox; no clipping.
No mobile or native Excel visual inspection was performed; workbook structure and
values were checked with openpyxl. Test server stopped; temporary screenshots,
scripts and seeded runtime removed. Production data untouched. No summary-only mode
was added, in accordance with the user's simplified three-control design.

Follow-up (2026-10-08): simplified the summary workbook as requested. Removed the
explanatory sentence and the "Da verificare" column, renamed the amount header to
"Ritenute" and its total to "Totale ritenute". The table now begins directly below
the title and spans five columns. The existing calculation and incomplete-row
highlight remain. Updated workbook position/label assertions; full Python suite:
348 passed, 240 existing Pillow deprecation warnings. No frontend or runtime data
change; package work can proceed from this state.
