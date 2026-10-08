# Final Optimization Audit

Audit closed: 2026-10-07. Sections 1-10 preserve the pre-fix audit and proposals.
Implementation status: selected bounded fixes applied; see section 11.
Internal engineering document; observed document labels remain in Italian.

Follow-up: section 10 maps each finding to a concrete anchor/logic correction
only where justified, distinguishing settled behavior from untested tuning.

## 1. Scope And Evidence

Objective: reduce incorrect automatic results, protect user work, and simplify the
implementation before adding more extraction rules. Prefer an empty field over a
plausible but unsupported value. Do not confuse this extraction policy with the
user's right to save a manually verified invoice despite a reconciliation warning.

Read-only census of `%LOCALAPPDATA%/EstrattoreScanFatture`:

| Item | Count |
| --- | ---: |
| Pending invoices | 453 |
| Active review batches | 33 |
| Distinct manifest documents referenced by them | 182 |
| Pages in those full manifest documents | 797 |
| Drafts changing pending invoices | 2 |
| Missing/ambiguous pending manifest references or missing viewer-index membership | 0 |

The 797 pages include entire source documents, not exclusively pending invoice
pages. Review coverage: 003-009, 010-024, 026-036. Each contains 15 pending invoices
except 003 (13), 009 (4), 024 (9), 035 (5), and 036 (2).

Method:

- Inventory every pending invoice, selected fields, date candidates, OCR context,
  draft differences, and index references.
- Replay current date candidate generation and selection on all 453 invoices in
  memory, including the shared-payment linking pass. Retain existing page grouping,
  spans, other targets, and invoice numbers. This is NOT a full pipeline rerun.
- Replay current economic extraction/resolution on 12 representative saved pages.
- Test three observed anchor additions in memory only.
- Inspect existing page images for R022/F193, R005/F062, and R006/F083.
- Review extraction, worker, persistence, API/local-app routing, frontend,
  registry, and export code; run the current automated test suite, build, and lint.

`Rxxx/Fyyy` means the displayed review and invoice title at this snapshot, not a
stable identity. Use the document/page locator in section 9 if numbering changes.

Limits: not a manual visual review of all 797 pages, not an accuracy benchmark,
not a fresh Tesseract run, not a security penetration test, and not a full click-path
audit of every screen. User corrections and registry substitutions are not OCR
ground truth. No production invoice, draft, PDF, review or registry was changed.

## 2. Baseline: Missing Does Not Mean Wrong

Raw stored manifest values:

| Field | Empty / 453 |
| --- | ---: |
| Invoice date | 1 |
| Payment date | 189 |
| Gross total | 131 |
| Net payable | 94 |
| Withholding | 215 |
| Invoice number | 24 |
| Issuer fiscal code | 49 |
| Issuer name | 62 |
| VAT number | 52 |
| Condominium fiscal code | 69 |
| Condominium name | 9 |

All 453 stored withholding labels still use `Ritenuta %`. The frontend's legacy
adapter derives a monetary withholding from gross minus net when possible.
Applying that rule gives 170 empty withholding fields before draft overlays, not
215. Raw manifests and current screen values must therefore be distinguished.
This adapter can also propagate an incorrect total/net pair; migration is not
evidence that the inferred withholding is correct.

Screening found 17 raw gross totals below net and 39 payment dates before invoice
dates. These are investigation queues, NOT 56 proven errors: advance payments and
proforma settlements can legitimately precede an invoice.

The bounded current date replay changes 84 invoice-date selections and 37 final
payment-date selections versus stored values. These counts are differences, not
improvements. Some current rules improve old results; others introduce regressions.

## 3. Priority Extraction Findings

### E1 - P1: Monetary Parsers Silently Truncate Amounts

Evidence, reproduced directly with current functions:

| Input | Segmentation parser | Economic parser |
| --- | ---: | ---: |
| `1.234,56` | 1.23 | 1234.56 |
| `1,234.56` | 1.23 | 1.23 |
| `1,834,66` | 1.83 | 1.83 |
| `1,024,80` | 1.02 | 1.02 |
| `1.452,00` | 1.45 | 1452.00 |
| `1452.00` | 1452.00 | 1452.00 |

`_manifest_money_value` uses an unanchored regular-expression match;
`_clean_money_amount_text` also accepts a valid-looking prefix of an invalid token.
Real stored examples: R010/F007 has gross 1.83 from OCR `1,834,66`; R016/F096 has
gross 1.02 from `1,024,80`. Current economic resolution rejects these totals below
net, which is safer, but does not repair the parser.

Proposed fix: one explicit money parsing contract, Decimal arithmetic, complete
numeric-token validation, and shared fixtures across Python and TypeScript.
Separate canonical stored amounts, user-entered display formats, and uncertain OCR
repair. A damaged separator may generate a marked alternative candidate, never a
silent prefix value. Reject unsupported ambiguity rather than inventing decimals.

Code: `viewer_invoice_segmentation.py::_manifest_money_value` (line 1157),
`economic_summary_extraction.py::_clean_money_amount_text` (1976).
Acceptance: the table above; currencies, negative adjustments, split integer/cents,
thousands, malformed separators, and identical values through extraction/save/export.

### E2 - P1: Arithmetic Can Legitimize The Wrong Semantic Roles

R022/F193, visually checked:

- Document gross 1452.00, withholding 52.80, net 1399.20.
- OCR contains `Importo pagamento: 1.399,20`, taxable 1320.00 and VAT 132.00.
- Current economic replay resolves gross 1452.00, withholding deduction -1320.00,
  net 132.00. It uses the taxable amount as withholding and computes a net equal
  to VAT. The equation balances but the roles are wrong.

R006/F083, visually checked:

- Document gross 1568.60, withholding 57.04, net 1511.56.
- Current replay resolves 1568.60, deduction -1.00, net 1567.60.
- The resolver accepts a weak `1,00` candidate and a combinatorial net despite
  the actual printed total payable and the 57.04 deduction.

Proposed fix: validate role evidence before using combinations. Respect table
columns, same-row/column ownership, percentage versus amount, item quantities,
and source provenance. A mathematical relation supports two independently credible
amounts; it must not turn arbitrary nearby numbers into a believable triplet.
Allow abstention. Do not restore a UI rule limiting withholding to 0/4/10 percent:
the product now stores an amount, and percentage evidence is only supporting data.

Also correct provenance when a value is derived: the R022 anchor trial derives
52.80 but leaves `ritenuta_text` as `1.320,00`. A derived amount must not claim an
unrelated observed token as its evidence or training label.

Code: `economic_summary_extraction.py::resolve_economic_summary_rows` (345),
combinatorial selection around 1105; final target selection in
`viewer_invoice_segmentation.py`.
Acceptance: both cases; quantity 1 beside a summary; VAT beside net; correct
zero-withholding cases; derived values with no false bounding-box attribution.

### E3 - P1: A Generic Bank Date Can Defeat The Invoice Header

R005/F062, visually checked invoice date 10/01/2024:

- Invoice OCR: `Fattura nr 182 dell 10/01/2024`.
- Bank page: `Data: 03.01.2025`; execution is also 03/01/2025.
- Stored invoice date is correct; current date replay selects 03/01/2025 instead.
- Row priority requires the exact word `del`. OCR joined the nearby border into
  `dell`. The real invoice row receives priority 0, generic bank `Data` gets 1.
- When row text exists, the selector returns without using some of the stronger
  candidate anchor context. This is not simply a missing `Data` anchor.

Proposed fix: coherent role-aware ranking combining invoice-header evidence,
anchor quality, geometry, page role, and negative context. A generic bank timestamp
must remain weak. Handle OCR-corrupted `del` only within a corroborated invoice
reference, not as a broad new date anchor. Apply negative rules in fallback paths
as well as primary selection. Prefer abstention when competing roles remain unclear.

Code: `viewer_invoice_segmentation.py::_invoice_date_candidate_priority` (857),
`_best_invoice_date_target`, `invoice_date_extraction.py`.
Acceptance: this regression, genuine separate-line `Data`, law/service-period/due
dates, bank creation/printing/execution timestamps, and invoice references on receipts.

### E4 - P2: Same Calendar Date Is Not The Same Evidence Occurrence

`_best_paid_date_target` (945) skips every candidate whose normalized value equals
the invoice date, even if it belongs to a separate `Data Esecuzione` occurrence.
The shared-payment pass can rescue some cases: R027/F028 is restored by that pass,
so it must NOT be reported as a final missing payment date.

In the bounded replay, 27 invoices have positive-score same-day evidence and an
empty direct paid selection; 23 remain empty after the linking pass. These are
screening hits, not 23 confirmed legitimate same-day payments: some have the wrong
invoice date because of E3, and some scores concern weak bank timestamps.

Proposed fix: distinguish occurrences by page/bbox and role, not by value inequality.
Keep payment-event linkage separate from date recognition. Test equal issue/paid
dates both with and without shared invoice references and reject mere print dates.

### E5 - P2: A Textual Italian Date Never Becomes A Candidate

R022/F193: `Data documento: 21 Gennaio 2025` is visible and present across OCR tokens,
but invoice date is empty. `data documento` is already recognized; adding it again
cannot help. Candidate normalization supports numeric dates, not this token sequence.

Proposed fix: bounded same-line day/month-name/year assembly, actual Italian month
mapping, calendar validation, and the union bbox of the supporting tokens. Preserve
numeric date behavior and negative legal/date-range context.

Code: `invoice_date_extraction.py::find_invoice_date_candidates` (44),
`normalize_invoice_date` (163). Acceptance: 21/01/2025 here, invalid calendar dates,
month text in service descriptions, and incomplete year evidence.

### E6 - P2: Supported Anchor, Unsupported Invoice Number Format

R022/F193: actual `Numero documento: 000002-2025`. Stored number is postal code
58022; current isolated number extraction picks day `21` instead. The anchor exists,
but `_invoice_number_format_kind` (351) rejects a digits-only hyphenated identifier.
Its broad alphanumeric branch requires a letter.

Proposed fix: accept observed numeric-hyphen identifiers under appropriate anchor
evidence, retaining leading zeros. Keep dates, fiscal identifiers and amounts out;
do not grant a special scoring advantage merely for being alphanumeric.
Acceptance: this case, slash numbers, one-digit numbers, dates and postal codes.

### E7 - P2: Issuer Detection Prefers Software Branding Over The Issuer

R022/F193: issuer is `BERLUGI YURI`; selected name is
`CON FOGLIO DI STILE DOCUM SRL`, from the page-footer rendering-software notice.
Current replay reproduces this. The generator looks for an SRL pattern and ranks
frequency across the source document plus OCR confidence, without sufficient role
evidence. A sole trader is not even generated by that path.

Proposed fix: separate candidate coverage from issuer-role scoring. Use issuer
header/fiscal-identifier relationships, reject software/footer roles, and avoid
letting repetition across unrelated invoices confer authority. Registry enrichment
may resolve a name but must remain distinct from document extraction provenance.
Do not replace this with unconstrained guesses about any capitalized name.

Code: `issuer_name_extraction.py::find_issuer_name_candidates` (25),
`_issuer_name_from_text`. Test sole traders, SRL/SNC/cooperatives, bank names,
recipient names and footer branding in multi-invoice sources.

## 4. Observed Anchor Proposals, Not Invented Synonyms

Configuration: `economic_summary_extraction.py::_anchor_definitions` (1820).
Only in-memory trials were made; no configuration was changed.

| Field | Observed missing anchor | Example | Current economic replay | With only this anchor added |
| --- | --- | --- | --- | --- |
| Net payable | `Importo pagamento` | R022/F193 | 132.00; wrong withholding 1320.00 | 1399.20; derived withholding 52.80 |
| Net payable | `Netto vs. debito` (`netto vs debito`) | R005/F062 | Empty net and withholding; gross 198.00 | Net 190.80; derived withholding 7.20 |
| Net payable | `Totale a pagare` | R006/F083 | Wrong net 1567.60 | Still wrong net 1567.60 |

First two: high-confidence lexical gaps with a successful isolated counterfactual.
They correspond to existing `importo da pagare` / `netto a pagare` concepts.
Risk: partial payments, installments, bank payment summaries, or multiple totals.
Restrict role/context and test every occurrence, not just the successful example.

`netto vs` occurs in OCR context for 23 pending invoices: R004/F057-061,
R005/F062, R011/F017-018, R012/F038-039, R015/F089-090, R016/F091-092,
R019/F143-145, R030/F063-067 and F069. This is occurrence coverage, not a promise
to correct 23 invoices. `Importo pagamento` occurs in the inspected context corpus
for R022/F193. Minimum isolated demonstrated recovery: one invoice per anchor.

`Totale a pagare`: valid observed wording, but do not sell it as an anchor-only fix;
E2 and overlapping/duplicate OCR geometry dominate the tested case.

Do NOT add generic `Data`, `Totale`, `Imposta`, or `Importo` as unconditional anchors.
Do NOT re-add `Numero documento`, `Data documento`, or `Totale documento`: they
already exist. `SALDATI IN DATA` on R008/F113 refers to a partial payment and is
not sufficient evidence for full invoice settlement without additional context.

## 5. Existing Results That Need Careful Re-evaluation

### Historical Gross-Below-Net Queue

Raw values, for targeted comparison after E1/E2, not automatic correction:

| Review / invoice | Gross | Net |
| --- | ---: | ---: |
| 003 / 046 | 1100.00 | 4060.00 |
| 004 / 055 | 31.80 | 37.53 |
| 004 / 056 | 15.50 | 18.29 |
| 006 / 080 | 31.00 | 36.58 |
| 007 / 105 | 1.00 | 26.50 |
| 008 / 113 | 479.24 | 947.18 |
| 010 / 007 | 1.83 | 1774.51 |
| 012 / 034 | 356.50 | 420.67 |
| 012 / 037 | 15.50 | 18.29 |
| 014 / 065 | 31.80 | 37.53 |
| 014 / 066 | 15.50 | 18.29 |
| 016 / 094 | 31.80 | 37.53 |
| 016 / 095 | 15.50 | 18.29 |
| 016 / 096 | 1.02 | 991.20 |
| 019 / 136 | 2800.00 | 2968.00 |
| 020 / 160 | 170.50 | 201.19 |
| 023 / 203 | 132.98 | 156.92 |

Current economic replay already rejects the gross in tested R003/F046, R007/F105,
R008/F113, R010/F007, R012/F034, R016/F096, and R019/F136. Do not implement the
same guard again. Rejecting gross alone does not prove the remaining net is correct.

Other distinctions:

- R006/F083 stored invoice date 31/12/2072 becomes 31/12/2024 in current replay;
  the image confirms 2024. Conflicting OCR passes and year plausibility still merit
  tests; do not silently repair years solely from today's date.
- R012/F034 stored invoice/payment dates are reversed in role. Current date replay
  chooses 03/01/2024 and execution 19/02/2025 respectively. Existing date rules help.
- R008/F113 current replay prefers issue 16/01/2025 and execution 14/01/2025 instead
  of dates in older proforma references. A payment before issue is not automatically
  invalid. This example is OCR-context reviewed, not visually certified here.
- R003/F035 groups pages showing invoice numbers 159/2024 and 128/2024 plus a
  transfer referencing both. Stored grouping needs review. Current segmentation
  was NOT rerun, so this is not proof that the present splitter remains defective.

Track extractor/rule version separately from schema version and preserve the
baseline extraction. A UI label-based legacy migration is not a durable semantic
versioning strategy. Reprocessing must compare by stable invoice/page identity and
preserve user drafts; never replace all pending work merely to update this audit.

## 6. Persistence And Application Boundaries

### A1 - P1 Risk: Multiple Backends Can Share One Writable Runtime

`scripts/run_app.py::_find_available_port` (113) moves to another free port.
`invoice_registry_storage.py` uses a process-local `RLock` (30), not an interprocess
runtime lock. Atomic replacement protects one file write, not concurrent stale
read/modify/write sequences. Two launches can therefore race on the same data root.

Action: one backend owner per runtime directory, with a real Windows-compatible
interprocess lock and explicit second-launch behavior. Different isolated runtimes
remain allowed. Verify competing launches and crash recovery using test data.
Evidence level: code-established concurrency risk; no race induced on real data.

### A2 - P1 Risk: Client Registry Save Can Truncate The Current JSON

`registry.py::save_registry_cache` (783) writes directly to the destination.
Normal edit and cleaning handlers call it directly (`registry_api.py`, 2283/2303).
An interrupted write can damage the active registry; import's temporary-file path
does not protect these other operations.

Action: a shared atomic JSON writer, temporary sibling file, flush/replace, proper
writer serialization, and explicit recovery of an interrupted replacement. Test
failure before/during replacement and readers during save. No crash was induced.

### A3 - P2: Route Ownership Is Duplicated

`LocalAppHandler.do_GET` has its own API allowlist before delegating to
`RegistryApiHandler`; unknown paths fall back to SPA HTML. This structurally permits
the familiar JSON parser error receiving `<!doctype ...>` when an API route is
omitted. Query-path normalization also differs between layers.

Action: single API routing ownership, explicit JSON 404 for API URLs, separate
static fallback, consistent path/query parsing. Contract tests through the actual
local-app server, not only handler functions or Vite proxy.

### A4 - P1 Hardening: Local API Trust Boundary Is Too Permissive

`RegistryApiHandler._send_cors_headers` (875) allows origin `*` and mutating methods.
The inspected API exposes mutations without an app-session authentication boundary.
Loopback binding is not a complete authorization model. Browser local-network
protections vary; this audit did not demonstrate a browser exploit.

Action: define allowed app origins, validate Origin/Host for browser mutations,
use an appropriate session capability for sensitive API access, and explicitly
support trusted internal scripts. Test foreign-origin requests on an isolated
runtime. Do not break local file preview/export routes while tightening access.

### A5 - P2: Export File And Registry Commit Have A Crash Window

`invoice_export.py::export_invoices` (170) writes the artifact before recording it
in the registry. Its exception cleanup handles ordinary exceptions, not process
termination between the two operations. That can leave an unreferenced export.

Action: staged artifact plus committed ownership metadata, with narrowly scoped
startup recovery. Keep intentionally referenced historic exports. Do not treat all
old files as garbage or reuse the previous unsafe manifest-cleanup assumptions.
Evidence level: code-established crash window, not observed production residue.

### A6 - P2: Registry Excel Text Is Not Explicitly Protected As Text

`registry.py::save_registry_cache_excel` (747) appends user strings directly through
openpyxl. Leading `=` can become a formula instead of literal data. Invoice export
already has `_text` (76), which explicitly writes strings.

Action: share the safe text-cell convention across exports, preserving leading
zeros and literal formula-looking names. Test export/reimport of `=...` strings.
This is a code-review finding; no actual customer's workbook was altered/tested.

## 7. Performance And Simpler Code

### O1 - High Value: Compute Extraction Once, Serialize It Many Times

The runtime worker imports `scripts.debug_words.debug_words_file`.
`_extract_debug_candidates` (375) computes candidates/resolution; multiple CSV
writers compute them again. Economic fields and resolved rows themselves repeat
region/anchor discovery. Production behavior and debug output are tightly coupled.

Action: move orchestration behind a package API returning a typed extraction result.
Viewer manifest, feedback evidence and optional debug serializers consume that
same result. Reuse normalized spans/anchor hits within a document. Preserve debug
capabilities, but do not pay repeated inference cost just to write diagnostics.
Measure stage durations and invocation counts first; no speedup factor is claimed.

### O2 - High Value For Large PDFs: Bound Raster Memory

`io.py::load_scan` (11) calls `convert_from_path` for the complete PDF and returns a
list of images. At approximately A4/200 dpi RGB, 200 pages alone represent roughly
2.3 GB of uncompressed pixel data, before copies and OCR structures. This is a
capacity estimate, not a measured resident-memory result.

Action: render/process bounded page windows or use a managed disk-backed image
cache. Keep the chosen whole-source commit/cancel semantics; streaming internal
pages does not require reintroducing pause/resume checkpoints per invoice.
Benchmark peak memory, cancellation latency and output equivalence on long PDFs.

### O3 - Profile Then Prune Combinatorial Economic Search

Nested candidate combinations in `economic_summary_extraction.py` around 1105
multiply five role lists. First establish plausible column/role ownership, then
prune combinations and join compatible amounts rather than taking broad products.
An isolated twelve-page sample took roughly 0.15-0.70 seconds per page for field
generation plus resolution, excluding OCR. This is not an OCR performance profile.
Avoid weakening quality gates merely to reduce candidate counts.

### O4 - Split By Responsibility, Not By Arbitrary File Length

`App.tsx` is about 9,500 lines; API, processing storage and extraction modules also
carry many responsibilities. Prioritize seams already exposed by this audit:

- canonical field parsing/formatting and provenance contracts;
- registry/review/archive API services;
- invoice-editor draft lifecycle and registry enrichment;
- export workflow and shared table selection;
- pure extraction results versus storage/debug adapters.

Keep existing virtualized-table infrastructure; do not create a second table stack.
First fix the specific behavior with tests, then extract cohesive modules. Avoid a
large rewrite mixing UI, storage migration and OCR scoring in one change.

### O5 - Small Maintenance Work

- Lint currently warns about missing `applyReviewDraftsToInvoices` dependency in
  `App.tsx:4629`. Inspect state ownership/closure behavior; do not blindly add an
  unstable callback and trigger repeated draft application. This warning alone is
  not proof of lost data.
- Replace deprecated Pillow `getdata()` calls in table detection/Tesseract helpers
  with a supported equivalent and image-result tests before the dependency removes it.
- Remove unreachable code after `_looks_like_money_amount` returns, as part of E1.
- Clean tracked test artifacts separately, with an explicit ownership/ignore review.
  The working tree already contains numerous historical test-file deletions and
  unrelated changes; this audit deliberately does not revert or delete them.

## 8. Implementation Order And Release Gates

1. Build sanitized regression fixtures from E1-E7; retain raw tokens, geometry,
   expected fields, negative alternatives and extraction-versus-enrichment provenance.
2. Fix monetary parsing and semantic amount selection (E1/E2). Test extraction,
   manifest, displayed values, draft, saved register, feedback and Excel together.
3. Fix date-role ranking, same-day occurrences and textual dates (E3-E5).
4. Add the two demonstrated net anchors; evaluate all observed occurrences.
   Address numeric-hyphen invoice IDs and issuer-role coverage (E6/E7).
5. Close runtime ownership, atomic registry-write and API trust-boundary risks
   before handing the app to another workstation (A1/A2/A4).
6. Consolidate routing, export recovery and safe workbook text (A3/A5/A6).
7. Profile and remove repeated computation; then address large-PDF raster memory.
8. Re-evaluate historical pending results using staged comparisons, preserving drafts
   and invoice identity. Do not silently replace user-edited fields.

For every extraction change report: correct-to-wrong, wrong-to-correct,
wrong-to-empty, empty-to-correct, and unchanged. Count false positives separately
from coverage. A wrong-to-empty change can be a meaningful improvement here.
Do not declare accuracy without independently checked expected values.

No automatic approval to change anchors, delete data, or mass-reprocess is implied
by this document. Keep each implementation point small enough for causal comparison.

## 9. Reproduction Locators And Verification

Paths below are relative to the runtime `manifests` directory. Each document has
`viewer_manifest.json`, `document.json`, and existing page images.

| Example | Process | Document / page |
| --- | --- | --- |
| R003/F035 | process_20260821_135710_job_23d290c2467f | doc_000017 / 3-5 |
| R005/F062 | process_20260821_135710_job_23d290c2467f | doc_000031 / 1-2 |
| R006/F083 | process_20260821_135710_job_23d290c2467f | doc_000038 / 5-6 |
| R008/F113 | process_20260821_135710_job_23d290c2467f | doc_000047 / 4-5 |
| R022/F193 | process_20260821_135809_job_ab4d2be34e93 | doc_000076 / 1 |
| R027/F028 | process_20260907_170523_job_6bf0c4ce6e41 | doc_000014 / 6 |

Pure-function reproduction: reconstruct DocumentPage tokens/spans from the saved
document; call `find_economic_summary_field_candidates` and
`resolve_economic_summary_rows`; compare original anchors with only the named
additional normalized anchor. This bypasses OCR, segmentation, registry enrichment,
frontend mapping, and save persistence. Negative internal deduction values in this
report describe resolver output, not a proposed negative UI withholding format.

Checks completed:

- `python -m pytest -q -p no:cacheprovider`: **326 passed**, 240 Pillow deprecation
  warnings, 18.27 seconds. Tests used isolated data, not the production runtime.
- `npm --prefix apps/viewer run build`: passed.
- `npm --prefix apps/viewer run lint`: zero errors, one existing hook warning.
- Read-only census and bounded current-date replay: all 453 pending invoices.
- Economic replay: R003/F034,F035,F046; R005/F062; R006/F083; R007/F105;
  R008/F113; R010/F007; R012/F034; R016/F096; R019/F136; R022/F193.
- Existing document images visually inspected for the three cases named in scope.

No new UI was built, no full-app screenshot audit was performed, and no new OCR
process was started. Temporary census/replay data and scripts are removed after
consolidating this report to avoid retaining a duplicate personal-data corpus.

## 10. Concrete Correction Map

Documentation-only follow-up, 2026-10-07. These are implementation directions,
not changes already applied. "Clear" means the intended behavior is established;
it does not waive regression tests. Do not invent weights, geometric thresholds,
synonyms, or automatic repairs to make every item appear resolved.

### Extraction: Clear Corrections And Explicit Boundaries

| Finding | Layer | Clear correction | Not yet justified |
| --- | --- | --- | --- |
| E1: truncated money | Parsing logic | Parse the complete numeric token after removing permitted currency wrappers. Canonical `1452.00`, Italian `1.452,00`, and English `1,452.00` must produce Decimal 1452.00. Invalid tokens must not yield a valid prefix. Reuse the Python parser in extraction and manifest selection, with equivalent frontend contract fixtures. | Do not automatically turn `1,834,66` into 1834.66. Reject it initially; a separately marked OCR repair needs corroboration and its own tests. Ambiguous single separators need an explicit policy. |
| E2: false arithmetic consistency | Candidate role and derivation logic | Derive the third amount only from two independently accepted, role-supported amounts. A derived amount cannot serve as independent evidence to validate the same combination. Keep source candidate identities and mark derived results as derived. Clear unrelated text/bbox attribution when the result has no observed matching token. | The general ownership/scoring change for R006/F083 is not settled. Inspect how `1,00` becomes withholding and why 1511.56 loses before selecting weights or changing region construction. Do not add a rule banning withholding 1.00: it can be legitimate. |
| E3: bank date beats invoice date | Context/ranking logic | Carry invoice-header, anchor and negative-context evidence through the final selector even when row text is available; apply the same exclusions in its fallback. Treat bank creation/print timestamps separately from issue/execution roles. | Do not globally replace `dell` with `del`, or ban every date on bank pages. The exact ranking adjustment requires paired invoice/receipt regressions. A receipt's invoice reference can legitimately recover a missing invoice date. |
| E4: same-day payment rejected | Occurrence identity logic | Remove calendar-value inequality as an eligibility condition for payment dates. Require payment-role evidence for the occurrence; matching issue and payment values is allowed. Preserve the shared-payment linking pass. | Do not accept every same-day date, or count the 23 screening hits as automatic recoveries. Some arise from E3. |
| E5: textual date absent | Candidate generation | Assemble a nearby same-line day + Italian month name + four-digit year, validate it as a calendar date, and retain all supporting token IDs and their union bbox. `21 Gennaio 2025` becomes `2025-01-21`, then uses the existing date-role selector. | No fuzzy month guessing, implicit year, or joining across table columns in this first correction. Geometry tolerance must follow existing line conventions and be tested. |
| E6: numeric-hyphen number rejected | Candidate format logic | Add support for the observed digits-hyphen-year invoice-number family, including `000002-2025`, preserving its zeros and using existing anchor/position ranking without a format bonus. Keep date, fiscal-ID and amount checks. | Do not broadly accept every hyphenated numeric string. Confirm rejection of date-shaped and unrelated identifiers. |
| E7: footer selected as issuer | Role logic | The observed rendering notice `Versione semplificata prodotta con foglio di stile ...` is not issuer evidence. Exclude that notice from issuer-name candidacy based on its role, not the specific company name. | Recovering arbitrary sole-trader names and replacing source-wide frequency scoring are not solved by this exclusion. Do not hardcode BERLUGI YURI or infer all uppercase text as issuer names. Test invoice-local fiscal/header associations before choosing the broader algorithm. |

For E2, the derivation condition applies to automatic extraction, not to a new
blocking accounting rule in the editor. Preserve the user's ability to save a
manually verified exception after a warning. For E3, prefer corroborated ranking
over a universal hard rule such as "invoice page always wins".

### Exact Anchor Changes Supported By The Audit

Location: `_anchor_definitions()` in `economic_summary_extraction.py`.
The two additions to test independently are:

```python
("totale_netto_fattura", "importo pagamento")
("totale_netto_fattura", "netto vs debito")
```

Retain the original OCR text, punctuation and geometry as evidence; normalization
is only for matching. No new fuzzy variant or score increase is required by these
examples. Keep the same selection safeguards used by existing net-payable anchors.

| Observed wording | Decision | Required outcome / limit |
| --- | --- | --- |
| `Importo pagamento` | Clear anchor addition | R022/F193: net 1399.20, gross unchanged at 1452.00, derived withholding 52.80, no false taxable-amount bbox. Check partial-payment contexts before accepting the change generally. |
| `Netto vs. debito` | Clear anchor addition | R005/F062: net 190.80, gross unchanged at 198.00, derived withholding 7.20. Replay the other 22 observed occurrences and check regressions; improvement there is not yet established. |
| `Totale a pagare` | Defer as a remedy for R006/F083 | Adding it alone was tested and failed to fix the result. Resolve E2 candidate ownership first; do not bundle it with other additions and attribute success to the anchor. |
| `Numero documento`, `Data documento`, `Totale documento` | No addition | Already supported. E6, E5 and monetary selection/parsing address the demonstrated failures. |
| `SALDATI IN DATA` | No unconditional addition | The inspected context concerns partial payment. A complete-settlement interpretation needs additional evidence. |

### Historical Cases: Do Not Create New Rules Without A Current Failure

| Cases | Supported next action | Unsupported shortcut |
| --- | --- | --- |
| All 17 gross-below-net entries in section 5 | Apply E1/E2 comparisons by stable invoice identity. Retain the existing contradiction guard where it already works; inspect both amounts, not just gross. | Swapping gross/net or calculating a replacement from an assumed tax rate. |
| R010/F007 and R016/F096 | E1 must stop accepting 1.83/1.02 from malformed tokens. Empty is an acceptable immediate outcome. | Claiming the full amount is recovered merely because prefix parsing is fixed. |
| R006/F083 date 2072 | Add the visually checked 2024 example to regression fixtures; current replay already recovers it. | Clamping years to the current year or blindly preferring the smaller year. |
| R012/F034 swapped date roles | Preserve the current replay's improvement in a regression test before E3 changes. | Adding duplicate date anchors. |
| R008/F113 payment before issue | Keep advance/proforma payment possible; review payment linkage independently of calendar ordering. | Enforcing payment date greater than or equal to invoice date. |
| R003/F035 combined invoices | Run the existing splitter on an isolated fixture with both invoice numbers and the shared receipt; change logic only if it still fails. | Claiming a new split rule is necessary before exercising the current implementation. |
| Legacy withholding labels and two drafts | Specify semantic amount/version metadata for future writes; preserve user edits and compare migrated results explicitly. | Recalculating every withholding from whatever pair happens to be stored and declaring it verified. |

### Non-Extraction Findings: Clear Direction, No Anchor Change

These remain application work, not extraction-rule additions. The audit's existing
acceptance criteria still apply.

| Finding | Established direction | Decision still requiring design or measurement |
| --- | --- | --- |
| A1 | Exclusive backend ownership per resolved runtime root, released on process exit. | Windows lock mechanism and second-launch activation protocol. A persistent lock-file's existence alone is insufficient. |
| A2 | Shared atomic JSON replacement plus writer serialization for edit/cleaning paths. | Recovery/durability contract under Windows failures and interaction with related artifacts. |
| A3 | One API routing authority; API misses return JSON, never the SPA page. | Compatible route namespace and migration of current callers. |
| A4 | Explicit local-app trust boundary; do not rely solely on loopback or wildcard CORS. | Session capability, origin/host policy, and trusted CLI access. CORS tightening alone is not authentication. |
| A5 | Commit ownership for export artifacts and recover only demonstrably uncommitted app-owned artifacts. | Crash-state protocol and concurrent-startup protection; no blanket cleanup. |
| A6 | Use explicit string cells for textual registry export fields, as invoice export already does. | Verify round-trip behavior; do not convert numeric/date columns indiscriminately. |
| O1 | Reuse an extraction result for serializers rather than recomputing it. | Result boundaries, fallback invalidation, and measured cost savings. |
| O2 | Bound the number of simultaneously retained rendered pages while preserving source-level commit semantics. | Windowed rendering versus disk-backed caching, measured on long documents. |
| O3 | Measure candidate counts/time before pruning combinations. | Search strategy and pruning conditions that preserve valid results. No arbitrary candidate cap. |
| O4 | Extract cohesive responsibilities behind tested contracts. | Module boundaries should follow the next focused fix, not a wholesale rewrite. |
| O5 | Remove proven unreachable code; replace deprecated image iteration compatibly. | Hook dependency correction needs state-flow analysis; artifact cleanup needs ownership verification. |

Documentation check: every E1-E7, A1-A6 and O1-O5 finding has a disposition;
the observed anchor variants and historical anomaly groups are covered separately.
No new benchmark, extraction run, application change or test pass is claimed by
this follow-up. The verification results in section 9 belong to the original audit.

## 11. Implemented Bounded Fixes

Date: 2026-10-07. User authorized obvious defects with small corrections, not the
broader redesign. No stored manifest, draft, registry data or original PDF was
rewritten. Extraction changes apply to future processing/reprocessing only.

| Item | Applied change | Verification |
| --- | --- | --- |
| Net anchors | Added `importo pagamento` and `netto vs debito` to existing net-payable definitions. No scoring change or additional synonyms. | Synthetic OCR through the real economic resolver for each anchor: gross 198.00, direct net 190.80 and deduction -7.20. Existing economic tests pass. |
| E6 | Accept numeric identifiers matching `digits{1,6}-digits{4}`, preserving zeros and using existing anchor scoring. | Candidate extraction accepts `000002-2025`, retains one-digit/slash formats, and rejects tested dates, amounts, long numeric IDs and unsupported multi-hyphen strings. |
| E4, conservative subset | Equal issue/payment dates no longer cause unconditional rejection. Same-day payment requires a distinct occurrence and explicit execution/debit context using existing helpers; other-date behavior is unchanged. | Real viewer invoice construction accepts same-day execution/addebito and rejects printing/reference contexts. A selector test rejects reuse of the issue-date occurrence. |
| A6 | Registry Excel text cells are explicitly serialized as strings, including formula-looking input and leading zeros. | Actual XLSX write/read/reimport preserves `=1+1`, `=SUM(1,2)` and `000001` as literal data. |
| O5, cleanup only | Removed an unreachable statement after `_looks_like_money_amount` returns. | Full extraction suite; no intended behavior change. |

Implementation files: `economic_summary_extraction.py`,
`invoice_number_extraction.py`, `viewer_invoice_segmentation.py`, `registry.py`.
New regression file: `tests/test_audit_corrections.py` (16 parametrized cases).

Critical check during implementation: do not access worksheet `max_row`/row slices
for every appended record to enforce text, which would repeatedly scan a large
registry. Set cell types during the existing column-sizing traversal instead.

Verification after the final edits:

- Full Python suite: **342 passed**, 240 existing Pillow deprecation warnings.
- Frontend build: passed; lint: zero errors, one existing dependency warning.
- New checks exercise real extraction/resolver/viewer functions and real workbook
  serialization/reimport; no mocks. They do not exercise OCR subprocesses, HTTP,
  browser interactions, feedback export, or the packaged launcher.
- No UI changes, screenshots or production reprocessing. The 23 historical
  `netto vs` occurrences were not rerun in this implementation step. The audit's
  real-page anchor counterfactuals remain evidence for the proposal, not a new
  end-to-end production result or a quantified corpus accuracy improvement.

Deferred explicitly: E1 shared money-parser contract, E2 role/derivation/provenance,
E3 ranking, E5 multi-token textual dates, E7 broader issuer-role detection,
`totale a pagare`, legacy migrations, A1-A5, and larger O1-O5 work. Although some
desired outcomes are clear, they require more than a bounded local edit. In
particular, this step does not claim to fix all wrong amounts or the stale derived
withholding evidence described in E2. Same-day date handling remains conservative
for payment wording other than explicit execution/debit labels.
