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
  text: string
  confidence: number | null
  bbox: BoundingBox
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
}

export type ViewerDocumentIndex = {
  documents: ViewerDocumentIndexEntry[]
}

export type ViewerBatchIndexEntry = {
  id: string
  label: string
  base_path: string
}

export type ViewerBatchIndex = {
  batches: ViewerBatchIndexEntry[]
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
  title: string
  page_numbers: number[]
  fields: InvoiceField[]
}

export type ViewerInvoice = {
  invoiceId: string
  title: string
  documentId?: string
  variantLabel?: string
  sourceFile: string
  ocrLang?: string | null
  ocrProfile?: string | null
  fallbackOcrEventCount?: number
  basePath?: string
  pages: InvoicePage[]
  fields: InvoiceField[]
}
