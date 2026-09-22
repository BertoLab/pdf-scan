# Viewer Manifest

`viewer_manifest.json` is the stable contract between the Python extraction
engine and the future local viewer app.

The file is written by `scripts/debug_words.py` into each debug output folder:

```text
apps/viewer/public/sample/<document-name>/viewer_manifest.json
```

The viewer should prefer this file over reading the individual CSV files
directly. CSV files remain useful for manual inspection and debugging.

When the browser viewer needs to load multiple processed documents, it reads a
static index file:

```text
apps/viewer/public/sample/viewer_index.json
```

Example:

```json
{
  "documents": [
    {
      "id": "20250203163221123",
      "base_path": "/sample/20250203163221123"
    }
  ]
}
```

The browser cannot list arbitrary folders from a path, so every processed debug
folder must be listed explicitly in this index for now.

## Top-Level Shape

```json
{
  "schema_version": 2,
  "source_file": "data/input/example.pdf",
  "pages": [],
  "invoices": []
}
```

- `schema_version`: integer manifest schema version. Increment this when the
  viewer contract changes in a breaking way.
- `source_file`: original input path.
- `pages`: page manifests in document order.
- `invoices`: invoice groups detected inside the source file. A single PDF can
  produce multiple invoices, and each invoice can contain multiple pages.

## Page Shape

```json
{
  "page": 1,
  "width": 1654,
  "height": 2339,
  "image": "page-001-boxes.png",
  "tokens": [],
  "spans": [],
  "targets": [],
  "invoice_signature": {
    "fields": {},
    "signal_count": 0
  },
  "invoice_start_decision": {
    "starts_invoice": false,
    "signal_count": 0,
    "changed_signal_count": 0,
    "changed_signals": [],
    "reasons": []
  },
  "invoice_header_regions": [],
  "candidate_tables": {}
}
```

- `page`: 1-based page number.
- `width`, `height`: OCR coordinate space. Every bbox uses this coordinate
  system.
- `image`: page debug image filename relative to the manifest folder.
- `tokens`: OCR tokens with confidence and bbox.
- `spans`: grouped text spans with bbox.
- `targets`: selected overlays ready for drawing.
- `invoice_signature`: fixed invoice fields found on this page. For the current
  segmentation rules all fixed invoice fields count as strong signals.
- `invoice_start_decision`: diagnostic decision explaining whether the page
  starts a new invoice.
- `invoice_header_regions`: aligned invoice-header regions detected from page
  spans. These are structural signals for multi-invoice PDFs.
- `candidate_tables`: full candidate data grouped by extractor.

## Invoice Segmentation Rules

Invoice segmentation is document-level and based on page signatures, not on a
single extracted value.

Current hardcoded rules:

- Page 1 always starts the first invoice.
- Every fixed invoice field counts as a strong signal.
- A later page can start a new invoice only when it has at least 5 strong
  signals.
- A later page must also have at least 3 strong signals different from the
  current invoice signature.
- Pages that do not satisfy both thresholds stay in the current invoice.

These thresholds are intentionally conservative: a single changed invoice
number, date, or total is not enough to split the document.

`invoice_header_regions` can contribute one strong signal named
`invoice_header`. A region is emitted when at least 4 invoice-header labels are
aligned on the same x-position and at least 2 related values are available from
the already extracted targets. Current header labels include:

```text
fattura_numero
del
importo_da_pagare
entro_il
condizioni_pagamento
codice_fiscale_piva
```

## Invoice Shape

```json
{
  "invoice_id": "invoice-001",
  "title": "Fattura 001",
  "page_numbers": [1, 2],
  "fields": []
}
```

- `invoice_id`: stable identifier inside the manifest.
- `title`: display label for the viewer sidebar.
- `page_numbers`: 1-based pages belonging to this invoice.
- `fields`: fixed invoice field list populated from the best target inside the
  invoice pages. Missing fields are emitted with an empty value.
- Current fixed fields include `pagata_il`, the printed payment/execution date
  when a bank summary exposes it. It remains empty when the payment date is
  missing or handwritten.

Each invoice field is self-contained enough for the viewer to highlight its
evidence without searching the page targets:

```json
{
  "kind": "numero_fattura",
  "label": "Numero fattura",
  "value": "227",
  "score": 159.89,
  "ocr_confidence": 0.94,
  "page": 7,
  "bbox": { "x1": 100, "y1": 200, "x2": 160, "y2": 220 },
  "role": "target"
}
```

- `score`: internal extractor score used to compare candidates.
- `ocr_confidence`: OCR reading confidence for the selected evidence, in the
  `0..1` range, or `null` when unavailable. This is the value intended for
  user-facing confidence display.
- `page`: source page of the selected evidence, or `null` when the field is
  missing.
- `bbox`: selected evidence bounding box in page OCR coordinates, or `null`
  when the field is missing.
- `role`: evidence role copied from the selected target, for example `target`,
  `value`, `label`, or `pair`.

The invoice field `economic_summary.ritenuta.value` stores the monetary
withholding amount, for example `4,20`. The printed percentage remains available
only in economic-summary diagnostics as auxiliary extraction evidence. The
reviewed accounting fields must satisfy `gross total - withholding = net total`
whenever all three values are present.

The invoice field `pagata_il` is selected from invoice date candidates with a
bank/payment context such as `Data esecuzione`, `Data/Ora`, `bonifico`,
`Credit Transfer`, `disposizione`, or payment status labels. It must differ
from `data_fattura`.

## Bounding Boxes

All bounding boxes use the same shape:

```json
{ "x1": 10, "y1": 20, "x2": 110, "y2": 45 }
```

Coordinates are integer pixels in page OCR space. The viewer should scale them
proportionally to the rendered page image.

## Tokens

```json
{
  "text": "SSSMRC81RO3F032J",
  "confidence": 0.55,
  "bbox": {}
}
```

- `confidence` can be `null` when OCR confidence is unavailable.
- Current low-confidence threshold is `0.9`.

## Targets

Targets are selected entities intended for overlay rendering.

```json
{
  "kind": "codice_fiscale_emittente",
  "label": "codice_fiscale_emittente",
  "role": "target",
  "value": "SSSMRC81R03F032J",
  "score": 253.6,
  "ocr_confidence": 0.55,
  "bbox": {},
  "confidence_sensitive": true,
  "has_low_confidence_token": true
}
```

- `kind`: stable semantic identifier.
- `label`: visible overlay label. Empty string means draw a box without label.
- `role`: target role, useful for styling.
- `value`: selected normalized/display value.
- `score`: internal extractor score used to compare candidates.
- `ocr_confidence`: OCR reading confidence for the target bbox, in the `0..1`
  range. It is computed from the tokens inside the bbox and intentionally uses
  the lowest token confidence as a cautious value.
- `confidence_sensitive`: if true, the viewer may render orange when
  `has_low_confidence_token` is true.
- `has_low_confidence_token`: true when a token inside the bbox has confidence
  below threshold.

Suggested viewer colors:

- normal selected target: yellow `#ffd400`
- low-confidence selected target: orange `#ff9f1a`

## Economic Summary Targets

Economic summary intentionally has multiple overlay levels:

```text
economic_summary
economic_summary.<target>
economic_summary.<target>.label
economic_summary.<target>.value
```

Example:

```text
economic_summary.ritenuta
economic_summary.ritenuta.label
economic_summary.ritenuta.value
```

Rendering convention:

- `economic_summary`: macro region, always yellow.
- `economic_summary.<target>`: pair box around description + value, labeled,
  always yellow.
- `.label`: description-only box, no label, confidence-sensitive.
- `.value`: value-only box, no label, confidence-sensitive.

The label/value boxes use the pair box height for cleaner visual overlap.

## Candidate Tables

`candidate_tables` contains extractor-specific candidates. These are for side
panels, diagnostics, and row-to-overlay interactions.

Current keys:

```text
vat
fiscal_identifiers
invoice_numbers
invoice_dates
condominiums
economic_summary_regions
economic_summary_fields
economic_summary_resolved_rows
```

The viewer should treat table rows as diagnostic data. Selected overlays should
come from `targets`.

## Stability Rule

Additive fields are allowed without increasing `schema_version`.
Breaking changes to existing field meaning, target kind names, or bbox shape
must increment `schema_version`.

Schema version 2 changes `economic_summary.ritenuta.value` from a percentage
to the monetary withholding amount. Schema version 1 manifests require the
gross/net compatibility adaptation described above or reprocessing.
