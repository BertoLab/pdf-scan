# Learning from reviewed invoices

Date: 2026-09-09. Status: theoretical design; no runtime implementation authorized.
Audience: Codex/project engineers. Product explanations and controls remain Italian.

## Problem and conclusion

Turn ordinary accounting review into reliable evidence for improving extraction,
without requiring operators to annotate full pages or locate every corrected value.
This is feasible with partial, noisy supervision. It does not establish that every
final value appears on the page, that an unchanged field was checked, or that an
old bounding box locates a newly entered value.

Separate four tasks: reading characters, locating/choosing document fields,
resolving business entities against the registry, and producing accepted accounting
records. A correct result at the last stage is not ground truth for every earlier
stage. This separation is the central design invariant.

## Current implementation evidence

- `model.py`: OCR tokens carry text, bounding boxes, confidence, source/source_id;
  document pages include lines, spans, table regions and cell polygons.
- `apps/viewer/src/types.ts`: manifests/pages expose OCR tokens, candidates/targets,
  scores and geometry; invoices expose the selected fields. ViewerToken does not
  currently carry the backend token source/source_id.
- Field groups: issuer name/CF/VAT, condominium name/CF, invoice number and date,
  payment date, gross total, withholding amount and net payable. Table/line
  structure and split/merge decisions provide additional observations.
- Fields can carry role, registry_id, registry_source_row, match group and
  registry_replaced_value. These are useful fragments, not a complete event history.
- `App.tsx:updateFieldValue` changes value by spreading the previous field. Old
  bbox, score and registry attributes can therefore survive a manual edit.
- `invoice_registry_storage.py:_updated_invoice_payload` likewise replaces field
  values while retaining prior metadata. Never use this as verified localization.
- `_invoice_payload` stores submitted pages/fields in the registry; therefore some
  OCR evidence survives saving. It does not create an immutable pre-review baseline
  or a complete extraction/configuration/registry version record.
- `registry_enrichment.py` can raise a field score to 1000 for registry completion:
  application scores are not calibrated probabilities or OCR confidence.
- `_cleanup_closed_invoice_working_files` removes a document's OCR working directory
  once it has no pending invoices. Reprocessing also replaces working output. Those
  are valid cleanup behaviors; training data must not depend on temporary outputs.

Historical records can seed investigation, but cannot retroactively prove how a
value was obtained or reviewed. Re-running today's OCR does not reconstruct an old
run. The assessment above is code-based, not a measured error audit of studio data.

## What operator actions teach us

| Observation | Useful supervision | Not established |
| --- | --- | --- |
| Typed invoice date/number/amount and committed | Accepted field value; possible correction/addition | Literal transcription or location |
| Selected registry row | Entity resolution and explicit row preference | Every populated sibling exists in the scan |
| Automatic registry completion | System output and workflow assistance | Independent human confirmation |
| Saved unchanged field | Weak acceptance at document level | Explicit checking or correct extraction |
| Opened the eye control | UI exposure to previous value | Agreement with either value |
| Cleared field | Rejection/blank accepted | Absent on page versus unknown versus not applicable |
| Changed punctuation/decimal display | Formatting preference | Semantic extraction error |
| Derived withholding | Accepted accounting relationship | Printed rate or observed amount location |
| Moved/split/merged pages and saved | Accepted final page grouping | All untouched boundaries verified |
| Discarded invoice | Workflow outcome | Bad OCR or incorrect split |

Store semantic action origin separately from acceptance level and from document
localization evidence. An entity-selection action can change five fields; record
one selection and its dependent updates, not five independent human corrections.
Keyboard navigation or blur without a meaningful change must not manufacture labels.
Preserve blank/unknown/not-applicable distinctions when available; do not infer them
from an empty string. Normalize for comparisons but preserve raw values.

## Evidence to retain before rollout

Use a dedicated, versioned feedback store, not permanent copies of review folders.
Recommended eventual shape: a small transactional local database for records and
events, with compressed immutable OCR evidence blobs. This does not require moving
the current invoice registry away from JSON. Export a documented portable bundle.

For each run retain:

1. Stable source/content ID, extraction-run ID, invoice-instance ID and lineage;
   display number and filename are not identity. Preserve page occurrences and
   original page mapping, including copied payment pages.
2. App/extractor build, configuration and anchor versions, OCR engine/model/language,
   rendering DPI, transforms and fallback/crop policy. Coordinate systems must be
   explicit so locations can be mapped to the archived original page.
3. Immutable original OCR tokens and provenance; selected values before registry
   enrichment; candidates with features/scores and rejection evidence; segmentation.
4. The actual initial presentation after registry completion, plus referenced
   registry record snapshot/version (only the relevant rows, not the full registry
   on every invoice).
5. Committed field/group changes: old/new raw and comparable values, action source,
   explicit selection versus automatic dependent fill, time and acceptance level.
   Include later register edits, re-review and registry-induced changes as revisions.
6. Final accepted business values and page grouping, linked to the exact run they
   correct. Optional verified page/region links remain separate from predicted ones.

Capture the extraction baseline before enrichment and before review cleanup. Persist
the feedback revision reliably with invoice saving: an atomic boundary when stores
permit it, otherwise an idempotent durable pending event that is recovered on restart.
Never report full capture if only one side of a split write succeeded. Do not turn
each keystroke into a disk write or retain every abandoned draft as training truth.

Keep lightweight provenance for all committed records; sample richer diagnostics
when storage measurements justify it. JSON size is not negligible by definition:
measure bytes/page, number of crop tokens/candidates, and support bounded retention.
Reuse existing archived PDFs by reference; do not create hidden PDF duplicates.
Deleting a PDF must follow an explicit policy for associated feedback evidence,
rather than silently retaining its text/images elsewhere. Retaining records for
product improvement is deliberate data storage, not a cleanup exception.

Developer access needs a studio-approved export workflow. Default to local storage,
no automatic network transfer. Allow aggregate-only reports and selected document
bundles; show their contents. Hashing filenames or fiscal IDs does not anonymize
document text. The exact sharing/retention arrangement is a deployment decision.

## Collection contract v1 - 2026-09-16

Status: Collection Extraction v1 implemented on 2026-09-16. Model training and
automatic label inference remain separate later work. Runtime capture follows the
contract below; known v1 limits are recorded at the end of this section.

### Storage boundary

Proposed runtime location, resolved through RuntimePaths and its existing override:

```text
<app_data_dir>/feedback/
  feedback.sqlite3
  evidence/<content-hash>.json.gz
```

The database contains identities, run metadata, committed events, field snapshots,
asset references and capture status. Immutable compressed evidence contains page OCR,
geometry and candidate records; it is shared across invoices from the same run/page.
Use schema versions independently of application versions. This is a dedicated
collection store, not another operational invoice manifest consumed by the viewer.
SQLite transactions protect related database writes [9]; they do not make writes
to evidence files or the existing invoice-register JSON atomic with the database.

No permanent exports folder or duplicate PDF library is required. A portable export
must include a consistent snapshot, referenced evidence and explicitly selected
available PDFs, with relative bundle paths and checksums. Copying this directory
alone does not constitute a complete image-training dataset. Use a consistent
database backup/export operation, not an arbitrary copy of an open database file.

### Required records

| Record | Required information | Purpose |
| --- | --- | --- |
| Collection | Schema version, installation/dataset UUID, creation timestamp in UTC | Combine exports without colliding local identifiers |
| Source asset | Stable asset ID, SHA-256 of bytes, archive reference, original filename, page count, availability | Locate images, detect exact repeats, preserve lineage |
| Extraction run | Unique run ID, source asset ID, parent run/invoice for reprocessing, completion status and timestamps | Never overwrite an old prediction with a new run |
| Configuration | App/build ID, extractor version, exact effective configuration or immutable snapshot, anchor rules, OCR engine/version, language, traineddata hashes, rendering and fallback settings | Interpret and reproduce the actual extraction conditions |
| Page evidence | Source page index with explicit indexing convention, width/height/units, rotation/deskew/crop transforms, page role when available | Map OCR coordinates back to the document |
| OCR evidence | Token IDs, literal text, bbox, OCR confidence, engine/pass/crop IDs; line/span membership and table/cell polygons | Preserve what was really read, including fallback provenance |
| Candidates | Field kind, raw/comparable value, supporting token IDs, label/value geometry, anchor match, features/scores, selected/rejected status and available reasons | Separate candidate-generation and selection problems |
| Invoice instance | Stable UUID, run ID, ordered page occurrences, source lineage, original segmentation and subsequent grouping revisions | Survive renumbering, splitting, merging and copied pages |
| Prediction | All target fields before registry enrichment, value origin, candidate/token references, score, predicted geometry and derivation dependencies | Immutable extraction baseline |
| Presentation | Fields actually shown after registry/draft reconciliation, linked baseline and registry row versions | Know what the user was correcting |
| Review revision | Event UUID, parent revision, invoice instance, timestamp, action, before/after values, provenance, final full field snapshot, save/discard outcome | Capture accepted corrections and subsequent register edits |

If the pipeline does not expose a reason or feature today, record it as unavailable;
do not invent a retrospective explanation. Preserve actual available candidates,
not only the winning target. Candidate capture must reuse work already performed,
not run the OCR or candidate extraction again for logging.

Store the exact effective configuration once per content hash. A Git commit alone
is insufficient for developer runs with local changes: mark dirty/unversioned builds
and fingerprint the relevant code/configuration or identify a retained build artifact.

### Field-level contract

Apply the same contract to issuer CF/name/VAT, condominium CF/name, invoice number,
invoice date, payment date, gross total, withholding amount and net payable.
Keep raw strings and typed comparable values. Preserve leading zeros in identifiers;
store money as exact decimal strings and rates with explicit percent units. Keep
withholding amount, rate and applicable base distinct even when auxiliary evidence
is not an editable field.

Each field snapshot records:

- `value_semantics`: explicit unit/meaning when a stable field kind has changed
  semantics over time; new withholding revisions use `monetary_amount`, while
  legacy rows without this marker must not be mixed into amount training;
- `extracted_value`: pipeline output before registry enrichment, possibly empty;
- `presented_value`: the value actually offered to the user at that revision;
- `accepted_value`: latest saved business value, possibly empty;
- `value_origin`: document extraction, arithmetic derivation, automatic registry,
  explicit registry selection, dependent registry fill, manual input or unknown;
- `interaction`: untouched, manually changed, registry selected, automatically
  replaced, cleared, reset or formatting-only; group updates share an action ID;
- `review_status`: unreviewed, saved unchanged, saved after edit or explicitly
  confirmed only if an actual confirmation action exists;
- `predicted_location`: run/page/token IDs and geometry supplied by the extractor;
- `verified_location`: null unless an independent localization confirmation exists;
- relevant registry row ID plus the exact relevant row snapshot/hash at selection,
  entity role and alternative rows actually offered for an ambiguous match;
- derivation dependencies and validation outcomes, without treating validation as
  independent human confirmation.

Origin, interaction, acceptance and location are separate dimensions. For example,
a manually selected registry name can replace a scanned CF; the CF then has a
dependent registry-fill origin, not a manually transcribed origin. Registry IDs and
old bounding boxes left in mutable frontend fields are not sufficient evidence.

Persist semantic edits at field commit/selection using the draft persistence flow;
do not log keystrokes, focus, scrolling or eye clicks as labels. These events remain
provisional until invoice save. Reset/undo invalidates the relevant provisional
changes. Preserve valid draft provenance over restart so a later save is not
misclassified as acceptance of untouched extraction. Capture the actually displayed
registry state, including changes caused by reloading or editing the registry.

### Capture moments and lifecycle

1. During extraction, stage evidence before enrichment and temporary-file cleanup.
   Record pass provenance as it is produced; preserve the final merged token set
   and available alternatives without duplicating shared token records.
2. At successful process publication, promote its complete evidence into the store.
   A canceled process contributes no accepted examples; remove only its staged
   capture artifacts. Never remove evidence shared with an earlier completed run.
3. During review, persist semantic provisional changes and grouping lineage. Split
   or merge creates new invoice instances linked to their parents; a display-number
   change does not create a new identity. Copied pages have distinct occurrences.
4. At invoice save, persist the complete accepted field snapshot and its provenance
   before operational cleanup can remove the supporting extraction evidence.
5. At later register save, append a revision with the previous and new values. Do
   not replace the historical prediction or treat repeated HTTP requests as new
   examples. Group save follows the same per-invoice path with a shared action ID.
6. At discard, record the outcome without labeling fields wrong. Remove abandoned
   provisional corrections when the related review is actually deleted; an OCR-only
   record is not a completed training label.

Capture is automatic for all completed runs and saved invoices, not just edited
fields: otherwise both successful cases and the denominator for error analysis are
lost. Unchanged saves remain weak acceptance. Historical records predating capture
must be marked legacy/unknown rather than given fabricated extraction provenance.

### Durability, deletion and cost

Use a durable operation ID and recoverable pending-save record to bridge the existing
JSON register and feedback database. Only mark a review event accepted after the
business save commits; retry capture idempotently after interruption. Pending events
must contain enough evidence to survive operational cleanup. If storage fails, never
silently claim the capture succeeded or manufacture a completed training example.
Prefer an observable recoverable capture failure over losing a valid accounting save.

Retain feedback across ordinary review completion, rebuild and application restart.
It is purposeful user data, not a leftover process directory. PDF references follow
archive IDs/content identities, not display names or temporary manifest paths.
Retain no hidden image/PDF copies. Export available source or split PDFs once, with
the exact page mapping; absent PDFs make examples ineligible for image-based training.
OCR-only analysis can remain possible when its retention is intentional.

Deleting a PDF and deleting its collected textual feedback are distinct operations:
the latter also contains invoice data. Before deployment, integrate an explicit
feedback purge policy/control with archive deletion rather than assuming that deleting
one file erased its OCR. Purges must respect shared source pages and unrelated examples.
The previously agreed independence of archive/register deletions remains applicable;
do not introduce cross-archive cascades through the collection feature.

No additional OCR or model inference is required for capture. Write compressed page
evidence once; append small review revisions. Measure bytes/page, bytes/invoice,
capture duration and save latency on representative multi-page PDFs. Report complete,
pending, failed and image-unavailable counts so collection failures are discoverable
before months of office use. No performance or disk-space estimate is asserted yet.

### Implementation acceptance checks

- Manual correction, registry cascade, untouched save and formatting-only edit retain
  different provenance; editing a value never verifies its old bbox.
- Save, restart, register edit, duplicate request and reset preserve the intended
  revision chain without duplicate accepted samples.
- Split/merge/renumbering and reprocessing preserve identity and source-page lineage.
- Cancel/crash before publication, crash between register and feedback writes, and
  normal review cleanup leave recoverable complete records or explicit incomplete
  states, never misleading accepted examples or unbounded staging residue.
- Export resolves available PDF/evidence references after the operational review
  folder is gone; deletion behavior is tested independently of training logic.
- Exercise the real local-app route, frontend provenance payload and backend storage
  together. Unit tests alone cannot establish that users actually generate this data.

### V1 implementation outcome and limits

The runtime now stages extraction evidence, promotes only published processing runs,
records immutable predictions and review revisions, and keeps reprocessing and
segmentation lineage. Source PDFs are referenced through the existing archive; their
deletion marks image evidence unavailable without inventing or deleting textual
history. Reprocessed split invoices resolve their archived PDF through parent-record
lineage and a content-hash check even though OCR runs on a temporary batch copy.
Collection failures are observable but cannot invalidate a valid accounting save.

The frontend records semantic manual, registry, automatic-registry and dependent-fill
provenance in the existing draft/save payload. The backend reconstructs registry-table
edits as manual actions at the boundary where that fact is known. The save bridge is
idempotent and recoverable when the business registry write succeeds before feedback
commit: retries use the persisted business snapshot, not a newer request body.
Historical register rows without a captured extraction baseline are marked as
`legacy_import`; no OCR location is fabricated.

Each actual registry mutation receives a fresh occurrence UUID. A retry reuses an ID
only while an operation with the same previous and accepted snapshots is still
prepared; completed transitions are never deduplicated by value. Recovery commits a
prepared operation only when the current business JSON equals its accepted snapshot,
which is the durability proof that the non-transactional business write succeeded.

Portable export is built in a temporary sibling directory from a pruned SQLite backup.
It contains completed runs only, verifies referenced compressed evidence and foreign
keys, records missing archived PDFs as unavailable in the exported snapshot, supports
legacy rows with intentionally absent OCR evidence, and becomes visible only through
an atomic final rename.

Intentional v1 limits:

- verified field locations remain null until an explicit localization-confirmation
  workflow exists;
- candidate features unavailable in current CSV/manifest output remain unavailable;
- traineddata hashes and rendering DPI are null until the extraction manifest exposes
  their exact effective values;
- no trainer, automatic weak-label inference or model activation is part of v1;
- deleting an archived PDF retains collected textual fiscal feedback and marks the
  image unavailable; an explicit feedback purge UI and retention policy must be
  agreed before final studio deployment;
- real-document storage size and latency still need a representative workload
  benchmark; capture performs no second OCR pass.

## Automatically reconstructing useful labels

For a committed change, compare the accepted value with the original evidence:

1. Search compatible OCR token spans with field-specific canonicalization (dates,
   monetary separators, number fragments). Keep occurrence identities and original
   text; repeated identical values are not the same occurrence.
2. Inspect semantic context, entity role, table cell, neighboring anchor and page
   type. A unique string match is evidence, not proof of the field's location.
3. If one occurrence is well supported, record a weak field-to-span label with its
   derivation and confidence category. Preserve multiple hypotheses when ambiguous.
4. If the value occurs multiple times, retain a set of possible locations or leave
   localization unknown. Value-level supervision can still be used.
5. If no match exists, distinguish candidate-recall failure from OCR failure only
   when image inspection or independent recognition provides evidence. Absence in
   tokens alone cannot distinguish unreadable text, handwriting, external input,
   or a value absent from the document.

Example (illustrative): gross changed from 108.50 to 132.37. If 132.37 exists near
the relevant total label, this can teach candidate selection. If it is in tokens
but not the candidate list, investigate candidate generation. If absent from OCR,
the correction alone cannot identify a crop or prove the characters were printed.

Example: selecting the correct supplier in the registry replaces an incorrect CF
printed on the invoice. The accepted CF is a business truth, while the OCR may have
read the erroneous printed CF perfectly. Training OCR against the accepted CF would
teach it to misread the document. Canonical company names have the same problem.

Unlabeled tokens are not automatically negatives. A repeated value valid for both
gross and net is not a contradiction. Arithmetic consistency is supporting evidence,
not independent truth; generated amounts and rates must preserve their dependencies.
Keep withholding amount distinct from percentage and its applicable base.

This is distant/weak supervision. CloudScan demonstrates generating invoice labels
from validated field values and document n-grams, while acknowledging noisy labels.
Our partial review and registry data require stricter unknown/abstention handling
than treating every unmatched token as background [1, 2].

## Improvement options and selection

### First: automatic error analysis and proposed changes

Generate reports by field, supplier/layout family, scan quality and extractor version:
wrong selected candidate; correct candidate rejected; missing candidate; possible OCR
failure; wrong entity/group; wrong date role; accounting normalization; split error.
Unresolved causes stay unresolved. Prioritize recurring, high-impact patterns.

Suggest anchor variants only from actual observed contexts with traceable examples
and counterexamples. Replay a proposed configuration before activation. This is useful
without ML and turns developer debugging into evidence-based work.

### Second: a small model to rank existing candidates

Start with an interpretable/logistic baseline or small boosted-tree candidate scorer,
trained offline. Features: lexical anchor quality, relative alignment/distance,
table membership, field shape, OCR confidence and page/layout context. Include a
no-reliable-candidate outcome. Do not silently invert the existing exact/fuzzy
anchor policy or bypass validity/accounting constraints as part of introducing ML.

Value labels can supervise candidate sets even without unique positions. Do not
force one location simply to satisfy a token classifier. A learned scorer cannot
recover text that is absent from all generated candidates: measure candidate recall
first. Compare against the current rules and lightweight alternatives before choosing
a library/model. CPU inference should be benchmarked, not assumed free.

### Layout memory as supporting context

The finite-layout hypothesis is plausible but must be measured. Supplier is a useful
hint, not a template ID: suppliers change software and different suppliers share
templates. Separate invoices from payment/remittance pages and recognize page roles.

Cluster stable labels, relative geometry and table structure while masking variable
amounts, dates and identifiers. Learn field neighborhoods and anchor relationships,
not fixed page coordinates or last month's values. Layout confidence can prioritize
a region or candidate; unfamiliar/shifted layouts use the generic pipeline. Use
multiple documents and held-out dates to validate each family. Do not reuse a previous
invoice number, amount or date just because two pages resemble each other.

### Stronger document models and OCR retraining

LayoutLM-style models combine OCR text, positions and image features [3]. They can
improve contextual extraction but need appropriate field supervision and deployment
benchmarks. Image-to-JSON models such as Donut can learn without token-box labels [4],
but labels must distinguish text on the page from externally completed business data.
Masked losses are needed for partial targets; missing labels must not teach empty
output. Generative results need grounding and abstention; compare false positives.

LLM/VLM use is more attractive initially as an offline assistant proposing field
locations/error explanations on selected difficult examples. It is not independent
ground truth and should not self-label the evaluation set unchecked. CPU/GPU cost,
licenses and privacy must be evaluated before selecting a current model.

Tesseract fine-tuning is a separate project. Its training workflow expects line
images and corresponding literal transcriptions [5]. Accepted accounting values
and registry completions do not supply that automatically. Consider it only if
measured character/number recognition errors dominate and verified crops/transcripts
can be collected. No need to train an OCR model from scratch for this proposal.

### Targeted human confirmation

Do not burden every accountant with annotation. Sample uncertain or new-layout cases
for a developer/advanced reviewer: confirm a highlighted candidate, choose among
occurrences, mark a region or say that the value is external/not visible. Select
diverse cases, not only the most uncertain copies of the same template. Keep a small
random audit too, because correction logs miss confidently wrong unnoticed fields.
Selective labeling research directly supports candidate yes/no confirmation [6].

## Evaluation and controlled deployment

- Freeze the current extractor and an explicitly checked evaluation subset before
  tuning. Use only independently supported labels for claims of accuracy.
- Keep reprocessings, shared pages and duplicates of one source out of both training
  and test. Never split random pages from the same PDF across those partitions.
- Evaluate future invoices from known layouts separately from unseen suppliers/layouts.
  Temporal holdout and grouped splitting answer different generalization questions [7, 8].
- Fit layout clustering, alias mining, thresholds and other learned preprocessing on
  training data only; evaluate with the registry information available at that time.
- Report extraction-only versus post-registry completion separately. Track field
  precision, recall/coverage, candidate recall, wrong autofills, abstentions, accepted
  corrections per invoice, segmentation quality, and latency percentiles.
- Raw OCR score averages are not extraction accuracy. Correction rate is workflow
  evidence, not accuracy, until missed errors are assessed by an independent audit.
- Report per field and layout and macro as well as volume-weighted aggregates.
  Estimate uncertainty; zero observed errors in a tiny sample is not proof of safety.
- Prefer improved coverage at the same acceptable false-positive level, consistent
  with the user's preference for blanks over wrong values. Do not optimize fill rate.
- Run challengers offline or in bounded background/shadow evaluation; do not double
  expensive OCR for every live invoice merely to test a new scorer.
- Publish a versioned rules/model artifact only after regression review, with rollback.
  Automatic capture/reporting/retraining is plausible; automatic promotion needs
  explicit gates and enough evidence. Never change production logic after each edit.

## Recommended sequence

Before studio rollout: capture immutable baseline, actual shown values, committed
corrections with provenance, exact run/config IDs, page lineage and controlled export.
This is the time-sensitive work; it keeps all later learning options available.

After representative data arrives: build the audited benchmark and failure report;
measure layout diversity and candidate recall; mine supported anchor/ranking changes.
Then compare a small candidate scorer plus layout context against the rule baseline.
Escalate to OCR or image models only when the measured remaining failure type requires it.

No guaranteed gain, model size, annotation count or speed estimate is justified yet.
Thousands of near-identical invoices can be less informative than a smaller diverse,
well-verified sample. The viable outcome is controlled learning from business review,
not treating every saved field as an image annotation.

## Sources

1. CloudScan, invoice training from validated fields (2017): https://arxiv.org/pdf/1708.07403
2. Modeling Missing Data in Distant Supervision (2013): https://aclanthology.org/Q13-1030/
3. LayoutLMv3 (2022): https://arxiv.org/abs/2204.08387
4. Donut, OCR-free Document Understanding Transformer: https://arxiv.org/abs/2111.15664
5. Tesseract tesstrain: https://github.com/tesseract-ocr/tesstrain/blob/main/README.md
6. Selective Labeling (EMNLP 2023): https://aclanthology.org/2023.emnlp-main.233/
7. Google Rules of ML: https://developers.google.com/machine-learning/guides/rules-of-ml/
8. GroupKFold: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html
9. SQLite atomic commit and application-file storage: https://www.sqlite.org/atomiccommit.html and https://www.sqlite.org/appfileformat.html
