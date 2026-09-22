export type BoundingBox = {
  x1: number
  y1: number
  x2: number
  y2: number
}

export type Point = {
  x: number
  y: number
}

export type ViewerToken = {
  token_id?: string
  text: string
  confidence: number | null
  bbox: BoundingBox
  source?: string
  source_id?: string
}

export type ViewerSpan = {
  span_id: number
  line_id: number
  text: string
  bbox: BoundingBox
}

export type OcrHoleRegion = {
  bbox: BoundingBox
  reason: string
  ink_density: number
  token_density: number
  token_count: number
}

export type DetectedTableRegion = {
  bbox: BoundingBox
  horizontal_line_count: number
  vertical_line_count: number
}

export type DetectedTableCellRegion = {
  bbox: BoundingBox
  polygon?: Point[]
  table_index: number
  row_index: number
  column_index: number
}

export type ViewerTarget = {
  kind: string
  label: string
  role: string
  page?: number
  value: string
  score: number
  ocr_confidence?: number | null
  bbox: BoundingBox
  confidence_sensitive: boolean
  has_low_confidence_token: boolean
}

export type ViewerPage = {
  page: number
  width: number
  height: number
  image: string
  tokens: ViewerToken[]
  spans: ViewerSpan[]
  ocr_hole_regions?: OcrHoleRegion[]
  targets: ViewerTarget[]
  detected_table_regions?: DetectedTableRegion[]
  detected_table_cell_regions?: DetectedTableCellRegion[]
}

export type ViewerManifest = {
  schema_version: number
  source_file: string
  ocr_lang?: string | null
  ocr_profile?: string | null
  fallback_ocr_events?: unknown[]
  quality_fallback_candidates?: unknown[]
  pages: ViewerPage[]
  invoices?: ViewerManifestInvoice[]
}

export type ViewerDocumentIndexEntry = {
  id: string
  base_path: string
  review_invoice_ids?: string[]
}

export type ViewerDocumentIndex = {
  documents: ViewerDocumentIndexEntry[]
}

export type ViewerBatchIndexEntry = {
  id: string
  label: string
  base_path: string
  invoice_count?: number
}

export type ViewerBatchIndex = {
  batches: ViewerBatchIndexEntry[]
}

export type ImportedBatchDocument = {
  document_id: string
  original_file_name: string
  stored_file_name: string
  relative_path: string
  size_bytes: number
  status: string
}

export type ImportedBatch = {
  batch_id: string
  label: string
  source_label?: string
  created_at: string
  document_count: number
  documents: ImportedBatchDocument[]
}

export type ImportedBatchListPayload = {
  ok: boolean
  batch_count: number
  batches: ImportedBatch[]
}

export type ImportedBatchImportPayload = {
  ok: boolean
  batch: ImportedBatch
  batch_count: number
}

export type PendingImportSource = {
  source_id: string
  kind: 'files' | 'folder'
  label: string
  document_count: number
  duplicate_key: string
}

export type PendingImportSourcePayload = {
  ok: boolean
  cancelled?: boolean
  message?: string
  source?: PendingImportSource
}

export type InvoiceField = {
  kind: string
  label: string
  value: string
  score: number
  ocr_confidence?: number | null
  page?: number | null
  bbox?: BoundingBox | null
  role?: string | null
  registry_id?: string | null
  registry_source_row?: number | null
  registry_match_group?: string | null
  registry_replaced_value?: string | null
  review_interaction?:
    | 'manual_input'
    | 'formatting_only'
    | 'registry_selection'
    | 'dependent_registry_fill'
    | 'suggestion_selection'
    | 'dependent_suggestion_fill'
    | 'automatic_replacement'
    | 'cleared'
    | 'reset'
  review_value_origin?:
    | 'manual_input'
    | 'explicit_registry'
    | 'dependent_registry'
    | 'suggestion'
    | 'dependent_suggestion'
    | 'automatic_registry'
    | 'document_extraction'
  review_action_id?: string
  review_source_field_kind?: string
  review_registry_snapshot?: Record<string, unknown> | null
}

export type InvoicePage = {
  page: number
  width: number
  height: number
  image?: string
  tokens?: ViewerToken[]
  ocr_hole_regions?: OcrHoleRegion[]
  targets?: ViewerTarget[]
  detected_table_regions?: DetectedTableRegion[]
  detected_table_cell_regions?: DetectedTableCellRegion[]
}

export type ViewerManifestInvoice = {
  invoice_id: string
  display_number?: number
  title: string
  page_numbers: number[]
  fields: InvoiceField[]
  extraction_fields?: InvoiceField[]
}

export type ViewerInvoice = {
  invoiceId: string
  manifestInvoiceId?: string
  title: string
  displayNumber?: number
  documentId?: string
  variantLabel?: string
  sourceFile: string
  ocrLang?: string | null
  ocrProfile?: string | null
  fallbackOcrEventCount?: number
  basePath?: string
  pageNumbers?: number[]
  pages: InvoicePage[]
  fields: InvoiceField[]
}
