
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent, ReactNode, RefObject } from 'react'
import './App.css'
import { normalizeMoneyValueForInput } from './moneyFormatting'
import type {
  BoundingBox,
  ImportedBatchImportPayload,
  InvoiceField,
  PendingImportSource,
  PendingImportSourcePayload,
  ViewerBatchIndex,
  ViewerBatchIndexEntry,
  ViewerDocumentIndex,
  ViewerDocumentIndexEntry,
  ViewerInvoice,
  ViewerManifest,
} from './types'

const sampleBatchIndexPath = '/sample/viewer_batches.json'
const sampleIndexPath = '/sample/viewer_index.json'
const registryApiBaseUrl =
  import.meta.env.VITE_REGISTRY_API_URL ??
  (window.location.port === '5173' ? 'http://127.0.0.1:8765' : window.location.origin)
const manifestCacheKey = String(Date.now())
const BASE_PAGE_SCALE = 0.45
const FIELD_FOCUS_SCALE = 1.4
const REGISTRY_TABLE_ROW_HEIGHT = 64
const REGISTRY_TABLE_HEADER_HEIGHT = 56
const REGISTRY_TABLE_OVERSCAN_ROWS = 8
const dateFieldKinds = new Set(['data_fattura', 'pagata_il'])
const moneyFieldKinds = new Set([
  'totale_fattura',
  'ritenuta',
  'totale_netto_fattura',
  'economic_summary.totale_fattura.value',
  'economic_summary.ritenuta.value',
  'economic_summary.totale_netto_fattura.value',
])
const withholdingFieldKinds = new Set([
  'ritenuta',
  'economic_summary.ritenuta.value',
])
const isoDatePattern = /^(\d{4})-(\d{2})-(\d{2})$/

function userErrorMessage(error: unknown, fallbackMessage: string) {
  const rawMessage =
    error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  const normalizedMessage = rawMessage.trim()

  if (!normalizedMessage) {
    return fallbackMessage
  }
  if (
    normalizedMessage.includes('Unexpected token') ||
    normalizedMessage.includes('is not valid JSON') ||
    normalizedMessage.includes('<!doctype')
  ) {
    return fallbackMessage
  }
  if (
    normalizedMessage.includes('Failed to fetch') ||
    normalizedMessage.includes('NetworkError') ||
    normalizedMessage.includes('Network request failed')
  ) {
    return "Il servizio locale dell'app non e raggiungibile. Chiudi e riapri l'app."
  }
  if (/^HTTP\s+\d+/.test(normalizedMessage)) {
    return fallbackMessage
  }

  return normalizedMessage.replace(/^Error:\s*/i, '')
}

const expectedInvoiceFields: Pick<InvoiceField, 'kind' | 'label'>[] = [
  { kind: 'codice_fiscale_emittente', label: 'Codice fiscale emittente' },
  { kind: 'nome_emittente', label: 'Nome emittente' },
  { kind: 'p_iva', label: 'P.IVA' },
  { kind: 'codice_fiscale', label: 'C.F. Condominio' },
  { kind: 'condominio', label: 'Condominio' },
  { kind: 'numero_fattura', label: 'Numero fattura' },
  { kind: 'data_fattura', label: 'Data fattura' },
  { kind: 'pagata_il', label: 'Pagata il' },
  { kind: 'economic_summary.totale_fattura.value', label: 'Totale fattura' },
  { kind: 'economic_summary.ritenuta.value', label: 'Ritenuta' },
  {
    kind: 'economic_summary.totale_netto_fattura.value',
    label: 'Totale netto fattura',
  },
]
const expectedInvoiceFieldOrder = new Map(
  expectedInvoiceFields.map((field, index) => [field.kind, index]),
)
const expectedInvoiceFieldValidationGroups = [
  { label: 'Codice fiscale emittente', kinds: ['codice_fiscale_emittente'] },
  { label: 'Nome emittente', kinds: ['nome_emittente'] },
  { label: 'P.IVA', kinds: ['p_iva'] },
  { label: 'C.F. Condominio', kinds: ['codice_fiscale'] },
  { label: 'Condominio', kinds: ['condominio'] },
  { label: 'Numero fattura', kinds: ['numero_fattura'] },
  { label: 'Data fattura', kinds: ['data_fattura'] },
  { label: 'Pagata il', kinds: ['pagata_il'] },
  {
    label: 'Totale fattura',
    kinds: ['economic_summary.totale_fattura.value', 'totale_fattura'],
  },
  { label: 'Ritenuta', kinds: ['economic_summary.ritenuta.value', 'ritenuta'] },
  {
    label: 'Totale netto fattura',
    kinds: [
      'economic_summary.totale_netto_fattura.value',
      'totale_netto_fattura',
    ],
  },
]
const invoiceRegistryEditableFields = [
  { kind: 'codice_fiscale_emittente', label: 'C.F. emittente' },
  { kind: 'nome_emittente', label: 'Nome emittente' },
  { kind: 'p_iva', label: 'P.IVA' },
  { kind: 'codice_fiscale', label: 'C.F. condominio' },
  { kind: 'condominio', label: 'Condominio' },
  { kind: 'numero_fattura', label: 'Numero' },
  { kind: 'data_fattura', label: 'Data' },
  { kind: 'totale_fattura', label: 'Totale' },
  { kind: 'ritenuta', label: 'Ritenuta' },
  { kind: 'totale_netto_fattura', label: 'Netto' },
  { kind: 'pagata_il', label: 'Pagamento' },
] as const
type InvoiceRegistryEditableFieldKind =
  (typeof invoiceRegistryEditableFields)[number]['kind']

const invoiceFieldAliases: Record<string, string[]> = {
  totale_fattura: ['totale_fattura', 'economic_summary.totale_fattura.value'],
  ritenuta: ['ritenuta', 'economic_summary.ritenuta.value'],
  totale_netto_fattura: [
    'totale_netto_fattura',
    'economic_summary.totale_netto_fattura.value',
  ],
}

type FieldEvidence = {
  page: number
  bbox: BoundingBox
  value: string
  score: number
  ocr_confidence?: number | null
}

type RegistryStatus = {
  ok: boolean
  has_file: boolean
  file_name?: string
  file_url?: string
  json_file_name?: string
  json_file_url?: string
  export_file_name?: string
  export_file_url?: string
  record_count?: number
}

type RegistryPreviewRecord = {
  registry_id: string
  source_row: number
  codice_anagrafica: string | null
  nominativo: string
  codice_fiscale: string | null
  partita_iva: string | null
  tipologia: string
  warnings: string[]
}

type RegistryEditableFieldName = keyof Pick<
  RegistryPreviewRecord,
  'codice_anagrafica' | 'nominativo' | 'codice_fiscale' | 'partita_iva' | 'tipologia'
>

type RegistryRecordsPayload = {
  ok: boolean
  record_count: number
  records: RegistryPreviewRecord[]
  cleaning_report?: RegistryCleaningReport
  cleaning_report_file_url?: string
}

type AppView = 'review' | 'registry' | 'archive' | 'invoiceRegistry' | 'processing'
type PdfArchiveViewMode = 'invoices' | 'sources'
type InvoiceRegistryViewMode = 'saved' | 'exported' | 'discarded'

type InvoiceArchiveRegistryPayload = {
  ok: boolean
  registry: {
    records?: InvoiceArchiveRecord[]
  }
}

type InvoiceArchiveRecord = {
  record_id: string
  status: string
  saved_at?: string
  discarded_at?: string
  updated_at?: string
  source_batch_id?: string
  document_id?: string
  source_file_name?: string
  display_name?: string
  archived_pdf_path?: string
  archived_pdf_url?: string
  archived_pdf_page_numbers?: number[]
  archived_source_pdf_path?: string
  archived_source_pdf_url?: string
  registry_visible?: boolean
  invoice_archive_visible?: boolean
  source_archive_visible?: boolean
  invoice?: {
    title?: string
    fields?: InvoiceField[]
  }
}

type ArchivedSourceRecord = {
  source_id: string
  source_file_name: string
  archived_at: string
  size_bytes: number
  archived_source_pdf_path: string
  archived_source_pdf_url: string
  linked_invoice_count: number
}

type SourceArchivePayload = {
  ok: boolean
  records: ArchivedSourceRecord[]
}

type InvoiceArchiveFilters = {
  dateFrom: string
  dateTo: string
}

type InvoiceArchiveSortKey =
  | 'saved_at_desc'
  | 'saved_at_asc'
  | 'data_fattura_desc'
  | 'data_fattura_asc'
  | 'emittente_asc'
  | 'condominio_asc'
  | 'totale_desc'
  | 'totale_asc'

type ArchiveDeleteTarget = {
  scope: 'invoice' | 'source' | 'registry' | 'all'
  recordId?: string
  recordIds?: string[]
  archivedSourcePdfPath?: string
  archivedSourcePdfPaths?: string[]
  title: string
  message: string
}

type ArchiveReferenceKind = 'invoice' | 'source'

type ArchiveReferenceAction = {
  record: InvoiceArchiveRecord
  kind: ArchiveReferenceKind
  error: string | null
}

type ArchiveReprocessDialogState = {
  target: 'invoices' | 'sources'
  phase: 'confirm' | 'starting' | 'success'
  recordIds: string[]
}

type RegistryCleaningReportRecord = {
  registry_id: string
  source_row: number
  codice_anagrafica: string | null
  nominativo: string
  normalized_name: string
  codice_fiscale: string | null
  partita_iva: string | null
  tipologia: string
}

type RegistryCleaningReportGroup = {
  group_key: string
  status: 'merged' | 'conflict'
  fiscal_identifiers: string[]
  source_rows: number[]
  kept_records: RegistryCleaningReportRecord[]
  deleted_records: RegistryCleaningReportRecord[]
  source_records: RegistryCleaningReportRecord[]
}

type RegistryCleaningReport = {
  merged_count: number
  conflict_count: number
  deleted_count: number
  groups: RegistryCleaningReportGroup[]
}

type RegistryCleaningReportMode = 'preview' | 'applied'

type RegistryImportState = {
  kind: 'loading' | 'success' | 'error'
  message: string
} | null

type PdfImportState = {
  kind: 'loading' | 'success' | 'error'
  message: string
} | null

type InvoiceReviewActionState = {
  kind: 'saving' | 'discarding' | 'success' | 'error'
  message: string
} | null

type FeedbackExportState =
  | { kind: 'confirm' }
  | { kind: 'exporting' }
  | {
      kind: 'success'
      folderName: string
      invoiceCount: number
      missingPdfCount: number
    }
  | { kind: 'error'; message: string }
  | null

type PendingSegmentationAction =
  | {
      operation: 'move_boundary'
      direction: 'first_to_previous' | 'last_to_next'
      removesInvoice: boolean
      title: string
      message: string
    }
  | {
      operation: 'split'
      afterPosition: number
      title: string
      message: string
    }

type SegmentationProgressState = {
  operation: PendingSegmentationAction['operation']
  phase: 'saving' | 'reorganizing' | 'reloading'
}

type ReviewInvoiceDraft = {
  draft_id: string
  process_id: string
  document_id: string
  document_base_path: string
  invoice_id: string
  fields: InvoiceField[]
  updated_at: string
}

type ReviewDraftsPayload = {
  ok: boolean
  drafts: ReviewInvoiceDraft[]
}

type PendingReviewDraftSave = {
  invoice: ViewerInvoice
  reviewBatchBasePath: string
  version: number
}

type ProcessingJobPayload = {
  ok: boolean
  job_id: string
  batch_id: string
  status:
    | 'queued'
    | 'processing'
    | 'finalizing'
    | 'cancelling'
    | 'completed'
    | 'cancelled'
    | 'removed'
    | 'error'
  message: string
  progress_current?: number
  progress_total?: number
  current_file?: string
  elapsed_seconds?: number
  estimated_remaining_seconds?: number | null
  cancel_requested?: boolean
  created_at?: string
  source_kind?:
    | 'external_upload'
    | 'source_archive'
    | 'saved_invoice'
    | 'discarded_invoice'
  archive_sources?: boolean
  files?: Array<{
    document_id: string
    source_id?: string
    name: string
    size_bytes: number
  }>
  result?: {
    invoice_count: number
    review_batch_count: number
    errors?: string[]
  }
}

type ProcessingViewMode = 'active' | 'history'

type ProcessingJobsPayload = {
  ok: boolean
  jobs: ProcessingJobPayload[]
  active_job_id?: string | null
  queued_job_ids?: string[]
}

type PendingRegistryRecord = {
  sourceFieldKind: string
  record: RegistryPreviewRecord
}

type InvoiceSaveValidationIssue = {
  kind:
    | 'empty_fields'
    | 'invalid_fiscal'
    | 'invalid_date'
    | 'invalid_amount'
    | 'economic_mismatch'
    | 'new_registry_record'
    | 'registry_conflict'
  title: string
  detail: string
  record?: RegistryPreviewRecord
  conflicts?: RegistryPreviewRecord[]
}

type PendingInvoiceSaveValidation = {
  issues: InvoiceSaveValidationIssue[]
  registryRecordToAdd: RegistryPreviewRecord | null
}

type PendingBatchReviewAction = {
  action: 'save' | 'discard'
  invoices: ViewerInvoice[]
  validationIssueCount: number
  emptyFieldIssueCount: number
  invalidFiscalIssueCount: number
  invalidDateIssueCount: number
  invalidAmountIssueCount: number
  economicMismatchIssueCount: number
  newRegistryIssueCount: number
  registryConflictIssueCount: number
}

type RegistrySearchSuggestion = {
  recordIndex: number
  label: string
  detail: string
  score: number
}

type RegistrySearchIndexEntry = {
  recordIndex: number
  label: string
  detail: string
  searchValues: string[]
}

type FieldSuggestion = {
  value: string
  count: number
  score: number
  relatedFields: Record<string, string>
  registryRecord?: RegistryPreviewRecord
}

type RegistryFieldConflict = {
  fieldKind: string
  sourceFieldKind: string
  fiscalCode: string
  options: RegistryPreviewRecord[]
}

type SuggestionIndex = Record<
  string,
  { value: string; normalized: string; count: number; relatedFields: Record<string, string> }[]
>

type SuggestionRecord = {
  fields: Record<string, string>
}

const suggestionFieldKinds = new Set([
  'nome_emittente',
  'condominio',
  'codice_fiscale',
  'codice_fiscale_emittente',
  'p_iva',
])

const relatedFieldGroups = [
  ['nome_emittente', 'codice_fiscale_emittente', 'p_iva'],
  ['condominio', 'codice_fiscale'],
]

function getBboxStyle(bbox: BoundingBox, pageWidth: number, pageHeight: number) {
  return {
    left: `${(bbox.x1 / pageWidth) * 100}%`,
    top: `${(bbox.y1 / pageHeight) * 100}%`,
    width: `${((bbox.x2 - bbox.x1) / pageWidth) * 100}%`,
    height: `${((bbox.y2 - bbox.y1) / pageHeight) * 100}%`,
  }
}

function getPolygonPoints(points: { x: number; y: number }[]) {
  return points.map((point) => `${point.x},${point.y}`).join(' ')
}

function getOcrConfidenceClassName(ocrConfidence: number | null | undefined) {
  if (ocrConfidence === null || ocrConfidence === undefined) {
    return 'score-unknown'
  }

  if (ocrConfidence >= 0.9) {
    return 'score-high'
  }

  if (ocrConfidence >= 0.75) {
    return 'score-medium'
  }

  if (ocrConfidence > 0) {
    return 'score-low'
  }

  return 'score-unknown'
}

function formatOcrConfidence(ocrConfidence: number | null | undefined) {
  if (ocrConfidence === null || ocrConfidence === undefined) {
    return ''
  }

  return ocrConfidence.toFixed(2)
}

function formatItalianDateValue(value: string) {
  const match = isoDatePattern.exec(value)

  if (!match) {
    return value
  }

  const [, year, month, day] = match
  return `${day}-${month}-${year}`
}

function formatProcessingDateTime(value: string | undefined) {
  if (!value) {
    return 'Data non disponibile'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return 'Data non disponibile'
  }

  const day = String(date.getDate()).padStart(2, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const year = date.getFullYear()
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${day}-${month}-${year}, ${hours}:${minutes}`
}

function compareProcessingJobsNewestFirst(
  left: ProcessingJobPayload,
  right: ProcessingJobPayload,
) {
  const leftTimestamp = left.created_at ? Date.parse(left.created_at) : Number.NaN
  const rightTimestamp = right.created_at ? Date.parse(right.created_at) : Number.NaN
  const leftHasDate = Number.isFinite(leftTimestamp)
  const rightHasDate = Number.isFinite(rightTimestamp)

  if (leftHasDate && rightHasDate && leftTimestamp !== rightTimestamp) {
    return rightTimestamp - leftTimestamp
  }
  if (leftHasDate !== rightHasDate) {
    return leftHasDate ? -1 : 1
  }
  return right.job_id.localeCompare(left.job_id, undefined, { numeric: true })
}

function normalizeDateValueForInput(value: string) {
  const trimmedValue = value.trim()
  if (!trimmedValue) {
    return trimmedValue
  }

  const isoMatch = isoDatePattern.exec(trimmedValue)
  if (isoMatch) {
    return formatItalianDateValue(trimmedValue)
  }

  const separatedMatch = /^(\d{1,2})[\s./-]+(\d{1,2})[\s./-]+(\d{2}|\d{4})$/.exec(
    trimmedValue,
  )
  const compactMatch = /^(\d{2})(\d{2})(\d{2}|\d{4})$/.exec(
    trimmedValue.replace(/\D/g, ''),
  )
  const match = separatedMatch ?? compactMatch
  if (!match) {
    return value
  }

  const [, rawDay, rawMonth, rawYear] = match
  const day = Number(rawDay)
  const month = Number(rawMonth)
  const fullYear = rawYear.length === 2 ? `20${rawYear}` : rawYear
  const year = Number(fullYear)
  const candidateDate = new Date(year, month - 1, day)

  if (
    candidateDate.getFullYear() !== year ||
    candidateDate.getMonth() !== month - 1 ||
    candidateDate.getDate() !== day
  ) {
    return value
  }

  return `${String(day).padStart(2, '0')}-${String(month).padStart(2, '0')}-${fullYear}`
}

function normalizeInvoiceFieldValueForInput(fieldKind: string, value: string) {
  if (dateFieldKinds.has(fieldKind)) {
    return normalizeDateValueForInput(value)
  }
  if (moneyFieldKinds.has(fieldKind)) {
    return normalizeMoneyValueForInput(value)
  }
  return value
}

function formatFieldValueForDisplay(field: Pick<InvoiceField, 'kind' | 'value'>) {
  if (dateFieldKinds.has(field.kind)) {
    return formatItalianDateValue(field.value)
  }
  if (moneyFieldKinds.has(field.kind)) {
    return normalizeMoneyValueForInput(field.value)
  }
  return field.value
}

function DownloadIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M4 7h16M9 7V4h6v3m-9 0 1 13h8l1-13M10 11v5m4-5v5" />
    </svg>
  )
}

function EyeIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function EyeClosedIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m2 2 20 20" />
      <path d="M6.7 6.7C3.8 8.6 2 12 2 12s3.5 7 10 7c1.8 0 3.4-.5 4.7-1.2" />
      <path d="M10.7 5.1A8.2 8.2 0 0 1 12 5c6.5 0 10 7 10 7a14.2 14.2 0 0 1-2.1 3" />
      <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" />
    </svg>
  )
}

function RegistryRecordIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 5h16" />
      <path d="M4 12h16" />
      <path d="M4 19h16" />
      <path d="M8 5v14" />
      <path d="M16 5v14" />
    </svg>
  )
}

function getDisplayInvoiceField(field: InvoiceField): InvoiceField {
  return {
    ...field,
    value: formatFieldValueForDisplay(field),
  }
}

function sortInvoiceFieldsForDisplay(fields: InvoiceField[]) {
  return [...fields].sort((leftField, rightField) => {
    const leftOrder = expectedInvoiceFieldOrder.get(leftField.kind)
    const rightOrder = expectedInvoiceFieldOrder.get(rightField.kind)

    if (leftOrder !== undefined && rightOrder !== undefined) {
      return leftOrder - rightOrder
    }
    if (leftOrder !== undefined) {
      return -1
    }
    if (rightOrder !== undefined) {
      return 1
    }
    return 0
  })
}

function formatTargetValueForDisplay(kind: string, value: string) {
  if (withholdingFieldKinds.has(kind)) {
    const normalizedValue = normalizeMoneyValueForInput(value)
    return normalizedValue.startsWith('-') ? normalizedValue.slice(1) : normalizedValue
  }
  if (!dateFieldKinds.has(kind)) {
    return value
  }

  return formatItalianDateValue(value)
}

function normalizeSuggestionValue(kind: string, value: string) {
  const trimmedValue = value.trim()

  if (kind === 'p_iva' || kind.includes('codice_fiscale')) {
    return trimmedValue.replace(/[^0-9a-zA-Z]/g, '').toUpperCase()
  }

  return trimmedValue
    .toLowerCase()
    .replace(/[^0-9a-zàèéìòùA-Z\s]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function buildSuggestionIndex(records: RegistryPreviewRecord[]): SuggestionIndex {
  const groupedValues = new Map<
    string,
    Map<
      string,
      {
        value: string
        count: number
        relatedValueCounts: Map<string, Map<string, number>>
      }
    >
  >()

  for (const registryRecord of records) {
    for (const record of suggestionRecordsFromRegistryRecord(registryRecord)) {
      for (const [kind, value] of Object.entries(record.fields)) {
        if (!suggestionFieldKinds.has(kind)) {
          continue
        }

        const normalizedValue = normalizeSuggestionValue(kind, value)
        if (!normalizedValue) {
          continue
        }

        const valuesByKind = groupedValues.get(kind) ?? new Map()
        const existingValue = valuesByKind.get(normalizedValue) ?? {
          value: value.trim(),
          count: 0,
          relatedValueCounts: new Map<string, Map<string, number>>(),
        }
        existingValue.count += 1

        for (const [relatedKind, relatedValue] of Object.entries(record.fields)) {
          if (relatedKind === kind || !relatedValue.trim()) {
            continue
          }

          const valueCounts = existingValue.relatedValueCounts.get(relatedKind) ?? new Map()
          valueCounts.set(relatedValue.trim(), (valueCounts.get(relatedValue.trim()) ?? 0) + 1)
          existingValue.relatedValueCounts.set(relatedKind, valueCounts)
        }

        valuesByKind.set(normalizedValue, existingValue)
        groupedValues.set(kind, valuesByKind)
      }
    }
  }

  return Object.fromEntries(
    [...groupedValues.entries()].map(([kind, valuesByNormalizedText]) => [
      kind,
      [...valuesByNormalizedText.entries()]
        .map(([normalized, item]) => ({
          value: item.value,
          normalized,
          count: item.count,
          relatedFields: bestRelatedFields(item.relatedValueCounts),
        }))
        .sort((left, right) => right.count - left.count || left.value.localeCompare(right.value)),
    ]),
  )
}

function suggestionRecordsFromRegistryRecord(record: RegistryPreviewRecord): SuggestionRecord[] {
  const nominativo = record.nominativo.trim()
  const codiceFiscale = record.codice_fiscale?.trim() ?? ''
  const partitaIva = record.partita_iva?.trim() ?? ''
  const isCondominium = normalizeRegistrySearchText(record.tipologia).includes('condominio')
  const records: SuggestionRecord[] = []

  if (nominativo || codiceFiscale) {
    records.push({
      fields: {
        condominio: isCondominium ? nominativo : '',
        codice_fiscale: isCondominium ? codiceFiscale : '',
      },
    })
  }

  if (!isCondominium && (nominativo || codiceFiscale || partitaIva)) {
    records.push({
      fields: {
        nome_emittente: nominativo,
        codice_fiscale_emittente: codiceFiscale,
        p_iva: partitaIva,
      },
    })
  }

  return records
    .map((suggestionRecord) => ({
      fields: Object.fromEntries(
        Object.entries(suggestionRecord.fields).filter(([, value]) => value.trim()),
      ),
    }))
    .filter((suggestionRecord) => Object.keys(suggestionRecord.fields).length > 0)
}

function bestRelatedFields(
  relatedValueCounts: Map<string, Map<string, number>>,
): Record<string, string> {
  return Object.fromEntries(
    [...relatedValueCounts.entries()]
      .map(([kind, valueCounts]) => {
        const selectedValue = [...valueCounts.entries()].sort(
          ([leftValue, leftCount], [rightValue, rightCount]) =>
            rightCount - leftCount || leftValue.localeCompare(rightValue),
        )[0]?.[0]

        return [kind, selectedValue ?? ''] as const
      })
      .filter(([, value]) => value),
  )
}

function registryFieldValueForInvoiceKind(
  record: RegistryPreviewRecord,
  fieldKind: string,
) {
  switch (fieldKind) {
    case 'nome_emittente':
    case 'condominio':
      return record.nominativo
    case 'codice_fiscale':
    case 'codice_fiscale_emittente':
      return record.codice_fiscale ?? ''
    case 'p_iva':
      return record.partita_iva ?? ''
    default:
      return ''
  }
}

function registryRecordKey(record: Pick<RegistryPreviewRecord, 'registry_id' | 'source_row'>) {
  return `${record.registry_id}-${record.source_row}`
}

function registryConflictKey(invoiceId: string, fieldKind: string) {
  return `${invoiceId}:${fieldKind}`
}

function registryConflictTargetFieldKind(fieldKind: string) {
  if (fieldKind === 'codice_fiscale') {
    return 'condominio'
  }
  if (fieldKind === 'codice_fiscale_emittente') {
    return 'nome_emittente'
  }
  return null
}

function registryConflictSourceFieldKind(fieldKind: string) {
  if (fieldKind === 'condominio') {
    return 'codice_fiscale'
  }
  if (fieldKind === 'nome_emittente') {
    return 'codice_fiscale_emittente'
  }
  return null
}

function registryFieldGroupForFiscalField(fieldKind: string) {
  if (fieldKind === 'codice_fiscale' || fieldKind === 'condominio') {
    return ['condominio', 'codice_fiscale']
  }
  if (
    fieldKind === 'codice_fiscale_emittente' ||
    fieldKind === 'nome_emittente' ||
    fieldKind === 'p_iva'
  ) {
    return ['nome_emittente', 'codice_fiscale_emittente', 'p_iva']
  }
  return []
}

function registryRecordsForFiscalCode(
  records: RegistryPreviewRecord[],
  fieldKind: string,
  fiscalCode: string,
) {
  const normalizedFiscalCode = normalizeSuggestionValue(fieldKind, fiscalCode)
  if (!normalizedFiscalCode) {
    return []
  }

  const matches = records.filter(
    (record) =>
      normalizeSuggestionValue('codice_fiscale', record.codice_fiscale ?? '') ===
      normalizedFiscalCode,
  )

  if (fieldKind === 'codice_fiscale') {
    return matches.filter((record) =>
      normalizeRegistrySearchText(record.tipologia).includes('condominio'),
    )
  }

  if (fieldKind === 'codice_fiscale_emittente') {
    return matches.filter(
      (record) => !normalizeRegistrySearchText(record.tipologia).includes('condominio'),
    )
  }

  return []
}

function registryRecordsForExactFieldValue(
  records: RegistryPreviewRecord[],
  fieldKind: string,
  value: string,
) {
  const normalizedValue = normalizeSuggestionValue(fieldKind, value)
  if (!normalizedValue) {
    return []
  }

  if (fieldKind === 'codice_fiscale' || fieldKind === 'codice_fiscale_emittente') {
    return registryRecordsForFiscalCode(records, fieldKind, value)
  }

  if (fieldKind === 'p_iva') {
    return records.filter(
      (record) =>
        !normalizeRegistrySearchText(record.tipologia).includes('condominio') &&
        normalizeSuggestionValue('p_iva', record.partita_iva ?? '') === normalizedValue,
    )
  }

  if (fieldKind === 'condominio') {
    return records.filter(
      (record) =>
        normalizeRegistrySearchText(record.tipologia).includes('condominio') &&
        normalizeSuggestionValue('condominio', record.nominativo) === normalizedValue,
    )
  }

  if (fieldKind === 'nome_emittente') {
    return records.filter(
      (record) =>
        !normalizeRegistrySearchText(record.tipologia).includes('condominio') &&
        normalizeSuggestionValue('nome_emittente', record.nominativo) === normalizedValue,
    )
  }

  return []
}

function registryRecordsHaveDifferentFill(records: RegistryPreviewRecord[]) {
  const signatures = new Set(
    records.map((record) =>
      [
        normalizeSuggestionValue('nome_emittente', record.nominativo),
        normalizeSuggestionValue('codice_fiscale', record.codice_fiscale ?? ''),
        normalizeSuggestionValue('p_iva', record.partita_iva ?? ''),
      ].join('|'),
    ),
  )
  return signatures.size > 1
}

function newRegistryDraftRecord(values: Partial<RegistryPreviewRecord> = {}): RegistryPreviewRecord {
  const tempId = `new_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`

  return {
    registry_id: values.registry_id ?? tempId,
    source_row: values.source_row ?? -Date.now(),
    codice_anagrafica: values.codice_anagrafica ?? null,
    nominativo: values.nominativo ?? '',
    codice_fiscale: values.codice_fiscale ?? null,
    partita_iva: values.partita_iva ?? null,
    tipologia: values.tipologia ?? 'Altro soggetto',
    warnings: values.warnings ?? [],
  }
}

function normalizeVatNumberForValidation(value: string) {
  const normalized = value.trim().toUpperCase().startsWith('IT')
    ? value.trim().slice(2)
    : value.trim()

  return normalized.replace(/\D/g, '')
}

function isValidItalianVatNumberForUi(value: string) {
  const vatNumber = normalizeVatNumberForValidation(value)
  if (!/^\d{11}$/.test(vatNumber)) {
    return false
  }

  const total = vatNumber
    .slice(0, 10)
    .split('')
    .reduce((currentTotal, char, index) => {
      const digit = Number(char)
      if (index % 2 === 0) {
        return currentTotal + digit
      }

      const doubled = digit * 2
      return currentTotal + (doubled > 9 ? doubled - 9 : doubled)
    }, 0)
  const expectedCheckDigit = (10 - (total % 10)) % 10
  return Number(vatNumber[10]) === expectedCheckDigit
}

function isValidItalianTaxCodeForUi(value: string) {
  const normalized = normalizeSuggestionValue('codice_fiscale', value)
  if (/^\d{11}$/.test(normalized)) {
    return isValidItalianVatNumberForUi(normalized)
  }

  return /^[A-Z0-9]{16}$/.test(normalized)
}

function isValidInvoiceDateValue(value: string) {
  const normalizedValue = normalizeDateValueForInput(value)
  const match = /^(\d{2})-(\d{2})-(\d{4})$/.exec(normalizedValue)
  if (!match) {
    return false
  }

  const [, rawDay, rawMonth, rawYear] = match
  const day = Number(rawDay)
  const month = Number(rawMonth)
  const year = Number(rawYear)
  const candidateDate = new Date(year, month - 1, day)
  return (
    candidateDate.getFullYear() === year &&
    candidateDate.getMonth() === month - 1 &&
    candidateDate.getDate() === day
  )
}

function parseInvoiceMoneyValue(value: string) {
  const normalizedValue = normalizeMoneyValueForInput(value)
  if (!/^\d+\.\d{2}$/.test(normalizedValue)) {
    return null
  }

  const amount = Number(normalizedValue)
  return Number.isFinite(amount) ? amount : null
}

function normalizeLegacyWithholdingFields(fields: InvoiceField[]) {
  const fieldsByKind = new Map(fields.map((field) => [field.kind, field]))
  const total = parseInvoiceMoneyValue(
    fieldsByKind.get('economic_summary.totale_fattura.value')?.value ??
      fieldsByKind.get('totale_fattura')?.value ??
      '',
  )
  const net = parseInvoiceMoneyValue(
    fieldsByKind.get('economic_summary.totale_netto_fattura.value')?.value ??
      fieldsByKind.get('totale_netto_fattura')?.value ??
      '',
  )
  const derivedWithholding =
    total !== null && net !== null && total >= net
      ? (total - net).toFixed(2)
      : ''

  return fields.map((field) => {
    if (!withholdingFieldKinds.has(field.kind)) {
      return field
    }

    const isLegacyPercentage =
      field.value.includes('%') || field.label.includes('%')
    return {
      ...field,
      label: 'Ritenuta',
      value: isLegacyPercentage ? derivedWithholding : field.value,
    }
  })
}

function isInvalidMoneyField(fieldKind: string, value: string) {
  const amount = parseInvoiceMoneyValue(value)
  return (
    moneyFieldKinds.has(fieldKind) &&
    Boolean(value.trim()) &&
    (amount === null || amount < 0)
  )
}

function validationHasBlockingIssue(validation: PendingInvoiceSaveValidation) {
  return validation.issues.some(
    (issue) => issue.kind === 'invalid_amount' || issue.kind === 'economic_mismatch',
  )
}

function fieldValueByAnyKind(fieldsByKind: Map<string, InvoiceField>, kinds: string[]) {
  for (const kind of kinds) {
    const value = fieldsByKind.get(kind)?.value.trim()
    if (value) {
      return value
    }
  }

  return ''
}

function validateInvoiceFields(fields: InvoiceField[]): InvoiceSaveValidationIssue[] {
  const issues: InvoiceSaveValidationIssue[] = []
  const fieldsByKind = new Map(fields.map((field) => [field.kind, field]))
  const emptyFields = expectedInvoiceFieldValidationGroups
    .filter((expectedField) => !fieldValueByAnyKind(fieldsByKind, expectedField.kinds))
    .map((expectedField) => expectedField.label)

  if (emptyFields.length > 0) {
    issues.push({
      kind: 'empty_fields',
      title: 'Campi mancanti',
      detail: emptyFields.join(', '),
    })
  }

  for (const field of fields) {
    const value = field.value.trim()
    if (!value) {
      continue
    }

    if (field.kind === 'p_iva' && !isValidItalianVatNumberForUi(value)) {
      issues.push({
        kind: 'invalid_fiscal',
        title: 'P.IVA da controllare',
        detail: `${field.label}: ${value}`,
      })
    }

    if (
      (field.kind === 'codice_fiscale' || field.kind === 'codice_fiscale_emittente') &&
      !isValidItalianTaxCodeForUi(value)
    ) {
      issues.push({
        kind: 'invalid_fiscal',
        title: 'Codice fiscale da controllare',
        detail: `${field.label}: ${value}`,
      })
    }

    if (dateFieldKinds.has(field.kind) && !isValidInvoiceDateValue(value)) {
      issues.push({
        kind: 'invalid_date',
        title: 'Data da controllare',
        detail: `${field.label}: ${value}`,
      })
    }

    if (moneyFieldKinds.has(field.kind) && parseInvoiceMoneyValue(value) === null) {
      issues.push({
        kind: 'invalid_amount',
        title: 'Importo non valido',
        detail: `${field.label}: inserisci un valore numerico senza segni o lettere`,
      })
    }
  }

  const totaleFattura = parseInvoiceMoneyValue(
    fieldsByKind.get('economic_summary.totale_fattura.value')?.value ??
      fieldsByKind.get('totale_fattura')?.value ??
      '',
  )
  const ritenutaRaw =
    fieldsByKind.get('economic_summary.ritenuta.value')?.value ??
    fieldsByKind.get('ritenuta')?.value ??
    ''
  const ritenuta = parseInvoiceMoneyValue(ritenutaRaw)
  const totaleNetto = parseInvoiceMoneyValue(
    fieldsByKind.get('economic_summary.totale_netto_fattura.value')?.value ??
      fieldsByKind.get('totale_netto_fattura')?.value ??
      '',
  )

  if (totaleFattura !== null && ritenuta !== null && totaleNetto !== null) {
    const expectedNet = totaleFattura - ritenuta
    if (Math.abs(expectedNet - totaleNetto) > 0.01) {
      issues.push({
        kind: 'economic_mismatch',
        title: 'Totali da controllare',
        detail: `Totale ${totaleFattura.toFixed(2)} - ritenuta ${ritenuta.toFixed(2)} non corrisponde al netto ${totaleNetto.toFixed(2)}`,
      })
    }
  }

  return issues
}

function buildRegistryRecordFromInvoiceFields(
  field: InvoiceField,
  fields: InvoiceField[],
) {
  const fieldValue = field.value.trim()
  const fieldByKind = new Map(fields.map((invoiceField) => [invoiceField.kind, invoiceField]))

  if (field.kind === 'codice_fiscale') {
    return newRegistryDraftRecord({
      nominativo: fieldByKind.get('condominio')?.value.trim() ?? '',
      codice_fiscale: fieldValue,
      tipologia: 'Condominio',
    })
  }

  if (field.kind === 'codice_fiscale_emittente') {
    return newRegistryDraftRecord({
      nominativo: fieldByKind.get('nome_emittente')?.value.trim() ?? '',
      codice_fiscale: fieldValue,
      partita_iva: fieldByKind.get('p_iva')?.value.trim() || null,
      tipologia: 'Altro soggetto',
    })
  }

  return null
}

function buildInvoiceSaveValidation(
  invoice: ViewerInvoice,
  registryRecords: RegistryPreviewRecord[],
): PendingInvoiceSaveValidation | null {
  const issues = validateInvoiceFields(invoice.fields)

  let registryRecordToAdd: RegistryPreviewRecord | null = null
  if (registryRecords.length > 0) {
    for (const field of invoice.fields) {
      if (field.kind !== 'codice_fiscale' && field.kind !== 'codice_fiscale_emittente') {
        continue
      }

      const value = field.value.trim()
      if (!value || !isValidItalianTaxCodeForUi(value)) {
        continue
      }

      const matchingRecords = registryRecordsForFiscalCode(registryRecords, field.kind, value)
      if (matchingRecords.length === 0) {
        const record = buildRegistryRecordFromInvoiceFields(field, invoice.fields)
        if (record && !registryRecordToAdd) {
          registryRecordToAdd = record
          issues.push({
            kind: 'new_registry_record',
            title: 'Valore non presente in anagrafica',
            detail: `${field.label}: ${value}`,
            record,
          })
        }
        continue
      }

      if (matchingRecords.length > 1 && registryRecordsHaveDifferentFill(matchingRecords)) {
        issues.push({
          kind: 'registry_conflict',
          title: 'Piu righe anagrafica compatibili',
          detail: `${field.label}: ${value}`,
          conflicts: matchingRecords,
        })
      }
    }
  }

  return issues.length > 0 ? { issues, registryRecordToAdd } : null
}

function invoiceRegistryRecordValidationLabel(record: InvoiceArchiveRecord, rowIndex: number) {
  const invoiceNumber = archiveFieldValue(record, 'numero_fattura')
  if (invoiceNumber) {
    return `Fattura ${invoiceNumber}`
  }

  return `Riga ${rowIndex + 1}`
}

function buildInvoiceRegistrySaveValidation(
  records: InvoiceArchiveRecord[],
): PendingInvoiceSaveValidation | null {
  const issues = records.flatMap((record, recordIndex) => {
    const recordLabel = invoiceRegistryRecordValidationLabel(record, recordIndex)
    return validateInvoiceFields(record.invoice?.fields ?? []).map((issue) => ({
      ...issue,
      detail: `${recordLabel}: ${issue.detail}`,
    }))
  })

  return issues.length > 0 ? { issues, registryRecordToAdd: null } : null
}

function fieldValueDiffersForRegistry(fieldKind: string, leftValue: string, rightValue: string) {
  return normalizeSuggestionValue(fieldKind, leftValue) !== normalizeSuggestionValue(fieldKind, rightValue)
}

function normalizedNameIsPartialOfRegistryValue(extractedValue: string, registryValue: string) {
  const normalizedExtracted = normalizeSuggestionValue('nome_emittente', extractedValue)
  const normalizedRegistry = normalizeSuggestionValue('nome_emittente', registryValue)
  if (!normalizedExtracted || !normalizedRegistry) {
    return false
  }

  return normalizedRegistry.includes(normalizedExtracted)
}

function fieldNeedsRegistryReplacementReview(field: InvoiceField) {
  const replacedValue = field.registry_replaced_value?.trim() ?? ''
  const registryValue = field.value.trim()
  if (!replacedValue || !registryValue) {
    return false
  }

  if (field.kind === 'nome_emittente' || field.kind === 'condominio') {
    return !normalizedNameIsPartialOfRegistryValue(replacedValue, registryValue)
  }

  return fieldValueDiffersForRegistry(field.kind, replacedValue, registryValue)
}

function fieldWithRegistryValue(
  field: InvoiceField,
  nextValue: string,
  record: RegistryPreviewRecord,
  review?: {
    interaction: 'registry_selection' | 'dependent_registry_fill'
    actionId: string
    sourceFieldKind: string
  },
): InvoiceField {
  const currentValue = field.value.trim()
  const replacedValue =
    field.registry_replaced_value ??
    (currentValue && fieldValueDiffersForRegistry(field.kind, currentValue, nextValue)
      ? currentValue
      : null)
  const automaticReplacement = !review && currentValue !== nextValue

  return {
    ...field,
    value: nextValue,
    role: 'registry_enriched',
    registry_id: record.registry_id,
    registry_source_row: record.source_row,
    registry_replaced_value: replacedValue,
    review_interaction:
      review?.interaction ??
      (automaticReplacement ? 'automatic_replacement' : field.review_interaction),
    review_value_origin: review
      ? review.interaction === 'registry_selection'
        ? 'explicit_registry'
        : 'dependent_registry'
      : 'automatic_registry',
    review_action_id:
      review?.actionId ??
      (automaticReplacement ? newReviewActionId() : field.review_action_id),
    review_source_field_kind:
      review?.sourceFieldKind ??
      (automaticReplacement ? field.kind : field.review_source_field_kind),
    review_registry_snapshot: registryFeedbackSnapshot(record),
  }
}

function registryFeedbackSnapshot(record: RegistryPreviewRecord) {
  return {
    registry_id: record.registry_id,
    source_row: record.source_row,
    codice_anagrafica: record.codice_anagrafica,
    nominativo: record.nominativo,
    codice_fiscale: record.codice_fiscale,
    partita_iva: record.partita_iva,
    tipologia: record.tipologia,
  }
}

function newReviewActionId() {
  return globalThis.crypto?.randomUUID?.() ?? `review-${Date.now()}-${Math.random()}`
}

function buildRegistrySearchIndex(records: RegistryPreviewRecord[]): RegistrySearchIndexEntry[] {
  return records.map((record, recordIndex) => {
    const label = record.nominativo || record.codice_fiscale || record.partita_iva || ''
    const detail = [record.codice_anagrafica, record.codice_fiscale, record.partita_iva, record.tipologia]
      .filter(Boolean)
      .join(' / ')
    const searchValues = [
      record.nominativo,
      record.codice_anagrafica ?? '',
      record.codice_fiscale ?? '',
      record.partita_iva ?? '',
      record.tipologia,
    ]
      .map(normalizeRegistrySearchText)
      .filter(Boolean)

    return {
      recordIndex,
      label,
      detail,
      searchValues,
    }
  })
}

function getRegistrySearchSuggestions(
  searchIndex: RegistrySearchIndexEntry[],
  query: string,
): RegistrySearchSuggestion[] {
  const normalizedQuery = normalizeRegistrySearchText(query)
  if (normalizedQuery.length < 1) {
    return []
  }

  const suggestions: RegistrySearchSuggestion[] = []

  for (const entry of searchIndex) {
    const score = scoreRegistrySearchEntry(entry, normalizedQuery)
    if (score < 45) {
      continue
    }

    suggestions.push({
      recordIndex: entry.recordIndex,
      label: entry.label,
      detail: entry.detail,
      score,
    })
    suggestions.sort(compareRegistrySearchSuggestions)
    if (suggestions.length > 8) {
      suggestions.pop()
    }
  }

  return suggestions
}

function compareRegistrySearchSuggestions(
  left: RegistrySearchSuggestion,
  right: RegistrySearchSuggestion,
) {
  return right.score - left.score || left.label.localeCompare(right.label)
}

function scoreRegistrySearchEntry(
  entry: RegistrySearchIndexEntry,
  normalizedQuery: string,
) {
  return Math.max(
    0,
    ...entry.searchValues.map((value) => scoreRegistrySearchValue(value, normalizedQuery)),
  )
}

function scoreRegistrySearchValue(value: string, normalizedQuery: string) {
  if (value === normalizedQuery) {
    return 130
  }

  if (value.startsWith(normalizedQuery)) {
    return 112
  }

  if (value.includes(normalizedQuery)) {
    return 92
  }

  if (normalizedQuery.length < 5) {
    return 0
  }

  if (Math.abs(value.length - normalizedQuery.length) > Math.max(8, normalizedQuery.length)) {
    return 0
  }

  return Math.round(levenshteinSimilarity(normalizedQuery, value) * 78)
}

function normalizeRegistrySearchText(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^0-9a-zàèéìòùA-Z\s]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

type VirtualDataTableProps<Row> = {
  rows: Row[]
  headerCells: ReactNode[]
  rowKey: (row: Row, rowIndex: number) => string
  renderRow: (row: Row, rowIndex: number) => ReactNode[]
  viewportRef?: RefObject<HTMLDivElement | null>
  className?: string
  tableClassName?: string
  rowClassName?: (row: Row, rowIndex: number) => string
  rowAttributes?: (row: Row, rowIndex: number) => Record<string, string>
  onMouseDown?: () => void
}

function VirtualDataTable<Row>({
  rows,
  headerCells,
  rowKey,
  renderRow,
  viewportRef,
  className = '',
  tableClassName = '',
  rowClassName,
  rowAttributes,
  onMouseDown,
}: VirtualDataTableProps<Row>) {
  const [scrollState, setScrollState] = useState({ scrollTop: 0, viewportHeight: 620 })
  const firstVisibleRow = Math.max(
    0,
    Math.floor(scrollState.scrollTop / REGISTRY_TABLE_ROW_HEIGHT) -
      REGISTRY_TABLE_OVERSCAN_ROWS,
  )
  const visibleRowCount =
    Math.ceil(scrollState.viewportHeight / REGISTRY_TABLE_ROW_HEIGHT) +
    REGISTRY_TABLE_OVERSCAN_ROWS * 2
  const lastVisibleRow = Math.min(rows.length, firstVisibleRow + visibleRowCount)
  const visibleRows = rows.slice(firstVisibleRow, lastVisibleRow)

  return (
    <div
      className={['virtual-data-table-wrap', className].filter(Boolean).join(' ')}
      ref={viewportRef}
      onMouseDown={onMouseDown}
      onScroll={(event) => {
        setScrollState({
          scrollTop: event.currentTarget.scrollTop,
          viewportHeight: event.currentTarget.clientHeight,
        })
      }}
    >
      <div
        className={['virtual-data-table', tableClassName].filter(Boolean).join(' ')}
        style={{
          height: REGISTRY_TABLE_HEADER_HEIGHT + rows.length * REGISTRY_TABLE_ROW_HEIGHT,
        }}
      >
        <div className="virtual-data-table-head">
          {headerCells.map((cell, cellIndex) => (
            <div key={cellIndex}>{cell}</div>
          ))}
        </div>
        {visibleRows.map((row, visibleRowIndex) => {
          const rowIndex = firstVisibleRow + visibleRowIndex

          return (
            <div
              key={rowKey(row, rowIndex)}
              className={['virtual-data-table-row', rowClassName?.(row, rowIndex)]
                .filter(Boolean)
                .join(' ')}
              data-row-index={rowIndex}
              style={{
                transform: `translateY(${
                  REGISTRY_TABLE_HEADER_HEIGHT + rowIndex * REGISTRY_TABLE_ROW_HEIGHT
                }px)`,
              }}
              {...(rowAttributes?.(row, rowIndex) ?? {})}
            >
              {renderRow(row, rowIndex).map((cell, cellIndex) => (
                <div key={cellIndex}>{cell}</div>
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}

type RegistryTableProps = {
  records: RegistryPreviewRecord[]
  isEditMode: boolean
  highlightedRecordKey: string | null
  tableViewportRef: RefObject<HTMLDivElement | null>
  onUpdateRecord: (
    recordIndex: number,
    fieldName: RegistryEditableFieldName,
    nextValue: string,
  ) => void
  onDeleteRecord: (recordIndex: number) => void
  onClearHighlight: () => void
}

const RegistryTable = memo(function RegistryTable({
  records,
  isEditMode,
  highlightedRecordKey,
  tableViewportRef,
  onUpdateRecord,
  onDeleteRecord,
  onClearHighlight,
}: RegistryTableProps) {
  return (
    <VirtualDataTable
      rows={records}
      headerCells={[
        'Codice anagrafica',
        'Nominativo',
        'Codice fiscale',
        'Partita IVA',
        'Tipologia',
        'Avvisi',
        ...(isEditMode ? ['Azioni'] : []),
      ]}
      rowKey={(record, recordIndex) =>
        `${record.registry_id}-${record.source_row}-${recordIndex}`
      }
      renderRow={(record, recordIndex) => [
        isEditMode ? (
          <input
            value={record.codice_anagrafica ?? ''}
            onChange={(event) =>
              onUpdateRecord(recordIndex, 'codice_anagrafica', event.target.value)
            }
          />
        ) : (
          record.codice_anagrafica ?? ''
        ),
        isEditMode ? (
          <input
            value={record.nominativo}
            onChange={(event) =>
              onUpdateRecord(recordIndex, 'nominativo', event.target.value)
            }
          />
        ) : (
          record.nominativo
        ),
        isEditMode ? (
          <input
            value={record.codice_fiscale ?? ''}
            onChange={(event) =>
              onUpdateRecord(recordIndex, 'codice_fiscale', event.target.value)
            }
          />
        ) : (
          record.codice_fiscale ?? ''
        ),
        isEditMode ? (
          <input
            value={record.partita_iva ?? ''}
            onChange={(event) =>
              onUpdateRecord(recordIndex, 'partita_iva', event.target.value)
            }
          />
        ) : (
          record.partita_iva ?? ''
        ),
        isEditMode ? (
          <input
            value={record.tipologia}
            onChange={(event) =>
              onUpdateRecord(recordIndex, 'tipologia', event.target.value)
            }
          />
        ) : (
          record.tipologia
        ),
        record.warnings.join(', '),
        ...(isEditMode
          ? [
              <button
                className="registry-row-delete-button"
                type="button"
                title="Cancella riga"
                onMouseDown={(event) => event.stopPropagation()}
                onClick={() => onDeleteRecord(recordIndex)}
              >
                Elimina
              </button>,
            ]
          : []),
      ]}
      viewportRef={tableViewportRef}
      className="registry-preview-table-wrap registry-preview-table-wrap-virtual"
      tableClassName={[
        'registry-preview-table',
        'registry-preview-table-virtual',
        isEditMode ? 'registry-preview-table-editable' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      rowClassName={(record) =>
        highlightedRecordKey === registryRecordKey(record)
          ? 'registry-search-highlight'
          : ''
      }
      rowAttributes={(record) => ({
        'data-registry-record-key': registryRecordKey(record),
      })}
      onMouseDown={onClearHighlight}
    />
  )
})

type RegistrySearchProps = {
  searchIndex: RegistrySearchIndexEntry[]
  onSelectSuggestion: (suggestion: RegistrySearchSuggestion) => void
}

const RegistrySearch = memo(function RegistrySearch({
  searchIndex,
  onSelectSuggestion,
}: RegistrySearchProps) {
  const [inputValue, setInputValue] = useState('')
  const [debouncedInputValue, setDebouncedInputValue] = useState('')
  const [isSuggestionListOpen, setIsSuggestionListOpen] = useState(false)
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(0)
  const suggestions = useMemo(
    () => getRegistrySearchSuggestions(searchIndex, debouncedInputValue),
    [searchIndex, debouncedInputValue],
  )

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedInputValue(inputValue)
    }, 180)

    return () => window.clearTimeout(timeoutId)
  }, [inputValue])

  const selectSuggestion = useCallback(
    (suggestion: RegistrySearchSuggestion) => {
      setInputValue(suggestion.label)
      setDebouncedInputValue(suggestion.label)
      setIsSuggestionListOpen(false)
      onSelectSuggestion(suggestion)
    },
    [onSelectSuggestion],
  )

  const submitSearch = useCallback(() => {
    const currentSuggestions = getRegistrySearchSuggestions(searchIndex, inputValue)
    const suggestion =
      currentSuggestions[
        Math.min(activeSuggestionIndex, Math.max(0, currentSuggestions.length - 1))
      ]
    if (suggestion) {
      selectSuggestion(suggestion)
    }
  }, [activeSuggestionIndex, inputValue, searchIndex, selectSuggestion])

  return (
    <div className="registry-search">
      <input
        value={inputValue}
        placeholder="Cerca nominativo, C.F. o P.IVA"
        onChange={(event) => {
          setInputValue(event.target.value)
          setIsSuggestionListOpen(true)
          setActiveSuggestionIndex(0)
        }}
        onFocus={() => {
          setIsSuggestionListOpen(true)
        }}
        onBlur={() => {
          setIsSuggestionListOpen(false)
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && suggestions.length > 0) {
            event.preventDefault()
            setActiveSuggestionIndex((currentIndex) =>
              Math.min(currentIndex + 1, suggestions.length - 1),
            )
          }
          if (event.key === 'ArrowUp' && suggestions.length > 0) {
            event.preventDefault()
            setActiveSuggestionIndex((currentIndex) =>
              Math.max(currentIndex - 1, 0),
            )
          }
          if (event.key === 'Enter') {
            event.preventDefault()
            submitSearch()
          }
          if (event.key === 'Escape') {
            setInputValue('')
            setDebouncedInputValue('')
            setIsSuggestionListOpen(false)
          }
        }}
      />
      {isSuggestionListOpen && suggestions.length > 0 ? (
        <div className="registry-search-suggestions" role="listbox">
          {suggestions.map((suggestion, suggestionIndex) => (
            <button
              key={`${suggestion.recordIndex}-${suggestion.label}`}
              className={suggestionIndex === activeSuggestionIndex ? 'active' : ''}
              type="button"
              onMouseDown={(event) => {
                event.preventDefault()
                selectSuggestion(suggestion)
              }}
            >
              <span>{suggestion.label}</span>
              <small>{suggestion.detail}</small>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
})

const InvoicePdfArchiveTable = memo(function InvoicePdfArchiveTable({
  records,
  selectedKeys,
  focusedKey,
  onToggle,
  onToggleAll,
  onGoToSource,
}: {
  records: InvoiceArchiveRecord[]
  selectedKeys: Set<string>
  focusedKey: string | null
  onToggle: (record: InvoiceArchiveRecord) => void
  onToggleAll: () => void
  onGoToSource: (record: InvoiceArchiveRecord) => void
}) {
  if (records.length === 0) {
    return (
      <div className="registry-empty-state">
        <h2>Nessuna fattura archiviata</h2>
        <p>Le fatture compariranno qui dopo il salvataggio dalla revisione.</p>
      </div>
    )
  }

  return (
    <div className="invoice-archive-table-wrap">
      <table className="invoice-archive-table">
        <thead>
          <tr>
            <th className="archive-selection-column">
              <input
                type="checkbox"
                checked={records.length > 0 && records.every((record) => selectedKeys.has(record.record_id))}
                onChange={onToggleAll}
                aria-label="Seleziona tutte le fatture"
              />
            </th>
            <th>Fattura</th>
            <th>Archiviata il</th>
            <th>Nome file</th>
            <th>Emittente</th>
            <th>Condominio</th>
            <th>Numero</th>
            <th>Origine</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr
              key={record.record_id}
              data-archive-key={record.record_id}
              className={focusedKey === record.record_id ? 'archive-focused-row' : ''}
            >
              <td className="archive-selection-column">
                <input
                  type="checkbox"
                  checked={selectedKeys.has(record.record_id)}
                  onChange={() => onToggle(record)}
                  aria-label={`Seleziona ${fileNameFromPath(record.archived_pdf_path) || 'fattura'}`}
                />
              </td>
              <td>
                {record.archived_pdf_url ? (
                  <a
                    className="invoice-archive-pdf-link"
                    href={runtimeApiUrl(record.archived_pdf_url)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Apri PDF
                  </a>
                ) : (
                  '-'
                )}
              </td>
              <td>
                {formatItalianDateValue(
                  (record.saved_at ?? record.discarded_at ?? '').slice(0, 10),
                ) || '-'}
              </td>
              <td title={fileNameFromPath(record.archived_pdf_path) ?? undefined}>
                {fileNameFromPath(record.archived_pdf_path) || '-'}
              </td>
              <td>{archiveFieldValue(record, 'nome_emittente') || '-'}</td>
              <td>{archiveFieldValue(record, 'condominio') || '-'}</td>
              <td>{archiveFieldValue(record, 'numero_fattura') || '-'}</td>
              <td>
                {record.source_file_name ? (
                  <button
                    type="button"
                    className="invoice-archive-pdf-link secondary"
                    onClick={() => onGoToSource(record)}
                    title={`Vai al file origine: ${fileNameFromPath(record.source_file_name)}`}
                  >
                    Vai al file origine
                  </button>
                ) : (
                  '-'
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})

const SourcePdfArchiveTable = memo(function SourcePdfArchiveTable({
  records,
  selectedKeys,
  focusedKey,
  onToggle,
  onToggleAll,
}: {
  records: ArchivedSourceRecord[]
  selectedKeys: Set<string>
  focusedKey: string | null
  onToggle: (record: ArchivedSourceRecord) => void
  onToggleAll: () => void
}) {
  const sourceRecords = records

  if (sourceRecords.length === 0) {
    return (
      <div className="registry-empty-state">
        <h2>Nessun file origine archiviato</h2>
        <p>I file origine compariranno qui al termine dell'elaborazione OCR.</p>
      </div>
    )
  }

  return (
    <div className="source-archive-list">
      <label className="source-archive-select-all">
        <input
          type="checkbox"
          checked={sourceRecords.every((record) => selectedKeys.has(record.source_id))}
          onChange={onToggleAll}
        />
        Seleziona tutti i file origine
      </label>
      <div className="source-archive-records">
        {sourceRecords.map((record) => (
          <article
            key={record.source_id}
            data-archive-key={record.source_id}
            className={`source-archive-record ${
              focusedKey === record.source_id ? 'archive-focused-row' : ''
            }`}
          >
            <input
              className="source-archive-checkbox"
              type="checkbox"
              checked={selectedKeys.has(record.source_id)}
              onChange={() => onToggle(record)}
              aria-label={`Seleziona ${record.source_file_name || 'file origine'}`}
            />
            <div className="source-archive-identity">
              <strong title={fileNameFromPath(record.source_file_name) || undefined}>
                {fileNameFromPath(record.source_file_name) || '-'}
              </strong>
              <small>
                Archiviato il {formatItalianDateValue(record.archived_at.slice(0, 10)) || '-'}
              </small>
            </div>
            <div className="source-archive-record-action">
              {record.archived_source_pdf_url ? (
                <a
                  className="invoice-archive-pdf-link secondary"
                  href={runtimeApiUrl(record.archived_source_pdf_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Apri origine
                </a>
              ) : (
                <span>-</span>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  )
})

function ArchiveReferenceDialog({
  action,
  onClose,
  onOpenWindow,
  onGoToArchive,
}: {
  action: ArchiveReferenceAction
  onClose: () => void
  onOpenWindow: () => void
  onGoToArchive: () => void
}) {
  const isSource = action.kind === 'source'
  const title = isSource ? 'File origine' : 'Fattura'
  const fileName = isSource
    ? fileNameFromPath(action.record.source_file_name) || 'File origine'
    : `Fattura ${archiveFieldValue(action.record, 'numero_fattura') || ''}`.trim()

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section
        className="registry-dialog archive-reference-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="archive-reference-title"
      >
        <header>
          <div>
            <h2 id="archive-reference-title">{title}</h2>
            <p>{fileName}</p>
          </div>
          <button onClick={onClose} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="archive-reference-dialog-body">
          <p>Scegli come visualizzare il documento.</p>
          {action.error ? (
            <p className="archive-reference-error" role="alert">
              {action.error}
            </p>
          ) : null}
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onClose}>Annulla</button>
          <button onClick={onGoToArchive}>Vai al file in Archivio</button>
          <button className="registry-save-button" onClick={onOpenWindow}>
            Apri in finestra
          </button>
        </div>
      </section>
    </div>
  )
}

const InvoiceRegistryTable = memo(function InvoiceRegistryTable({
  records,
  viewMode,
  isEditMode,
  onUpdateRecordField,
  selectedKeys,
  onToggle,
  onToggleAll,
  onOpenArchiveReference,
}: {
  records: InvoiceArchiveRecord[]
  viewMode: InvoiceRegistryViewMode
  isEditMode: boolean
  onUpdateRecordField: (
    recordId: string,
    fieldKind: InvoiceRegistryEditableFieldKind,
    nextValue: string,
  ) => void
  selectedKeys: Set<string>
  onToggle: (record: InvoiceArchiveRecord) => void
  onToggleAll: () => void
  onOpenArchiveReference: (
    record: InvoiceArchiveRecord,
    kind: ArchiveReferenceKind,
  ) => void
}) {
  if (records.length === 0) {
    const emptyState =
      viewMode === 'saved'
        ? {
            title: 'Nessuna fattura da esportare',
            description: 'I dati compariranno qui dopo il salvataggio dalla revisione.',
          }
        : viewMode === 'exported'
          ? {
              title: 'Nessuna fattura esportata',
              description: 'Le fatture compariranno qui dopo la loro esportazione.',
            }
          : {
              title: 'Nessuna fattura scartata',
              description: 'Le fatture compariranno qui dopo lo scarto dalla revisione.',
            }
    return (
      <div className="registry-empty-state">
        <h2>{emptyState.title}</h2>
        <p>{emptyState.description}</p>
      </div>
    )
  }

  return (
    <VirtualDataTable
      rows={records}
      headerCells={[
        <input
          type="checkbox"
          checked={records.length > 0 && records.every((record) => selectedKeys.has(record.record_id))}
          onChange={onToggleAll}
          aria-label="Seleziona tutte le righe"
        />,
        viewMode === 'saved' ? 'Salvata il' : 'Scartata il',
        ...invoiceRegistryEditableFields.map((field) => field.label),
        viewMode === 'saved' ? 'File origine' : 'File origine e pagine',
        'Fattura',
      ]}
      rowKey={(record) => record.record_id}
      className="invoice-registry-table-wrap"
      tableClassName="invoice-registry-table"
      rowClassName={(record) =>
        viewMode === 'saved' && invoiceRegistryRecordHasEmptyFields(record)
          ? 'invoice-registry-row-incomplete'
          : ''
      }
      renderRow={(record) => {
          return [
            <input
              type="checkbox"
              checked={selectedKeys.has(record.record_id)}
              onChange={() => onToggle(record)}
              aria-label={`Seleziona ${record.source_file_name || 'fattura'}`}
            />,
            formatItalianDateValue(
              (
                viewMode === 'saved'
                  ? record.saved_at ?? ''
                  : record.discarded_at ?? ''
              ).slice(0, 10),
            ) || '-',
            ...invoiceRegistryEditableFields.map((field) => {
                const value = archiveFieldValue(record, field.kind)
                const displayValue = isEditMode
                  ? dateFieldKinds.has(field.kind)
                    ? formatItalianDateValue(value)
                    : value
                  : normalizeInvoiceFieldValueForInput(field.kind, value)

                return (
                  <span
                    className={[
                      !value.trim() ? 'invoice-registry-empty-cell' : '',
                      isInvalidMoneyField(field.kind, value)
                        ? 'invoice-registry-invalid-cell'
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    {isEditMode ? (
                      <input
                        value={displayValue}
                        onChange={(event) =>
                          onUpdateRecordField(
                            record.record_id,
                            field.kind,
                            event.target.value,
                          )
                        }
                        onBlur={(event) => {
                          const normalizedValue = normalizeInvoiceFieldValueForInput(
                            field.kind,
                            event.currentTarget.value,
                          )
                          if (normalizedValue !== event.currentTarget.value) {
                            onUpdateRecordField(record.record_id, field.kind, normalizedValue)
                          }
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            event.currentTarget.blur()
                          }
                        }}
                      />
                    ) : (
                      displayValue || '-'
                    )}
                  </span>
                )
              }),
            <>
                {record.source_file_name || record.archived_source_pdf_path ? (
                  <button
                    type="button"
                    className="invoice-archive-pdf-link secondary"
                    onClick={() => onOpenArchiveReference(record, 'source')}
                  >
                    {record.status === 'discarded'
                      ? record.display_name || 'Apri origine'
                      : fileNameFromPath(record.source_file_name) || 'Apri origine'}
                  </button>
                ) : (
                  (record.status === 'discarded'
                    ? record.display_name
                    : fileNameFromPath(record.source_file_name)) || '-'
                )}
            </>,
            <>
                {record.archived_pdf_url || record.archived_pdf_path ? (
                  <button
                    type="button"
                    className="invoice-archive-pdf-link"
                    onClick={() => onOpenArchiveReference(record, 'invoice')}
                  >
                    Apri fattura
                  </button>
                ) : (
                  '-'
                )}
            </>,
          ]
      }}
    />
  )
})

type RegistryCleaningReportDialogProps = {
  report: RegistryCleaningReport
  mode: RegistryCleaningReportMode
  isRunning: boolean
  reportFileUrl: string | null
  onClose: () => void
  onApply: (selectedMergeGroupKeys: string[]) => void
  onDownload: () => void
}

function RegistryCleaningReportDialog({
  report,
  mode,
  isRunning,
  reportFileUrl,
  onClose,
  onApply,
  onDownload,
}: RegistryCleaningReportDialogProps) {
  const mergedGroups = useMemo(
    () => report.groups.filter((group) => group.status === 'merged'),
    [report.groups],
  )
  const conflictGroups = useMemo(
    () => report.groups.filter((group) => group.status === 'conflict'),
    [report.groups],
  )
  const allMergedGroupKeys = useMemo(
    () => mergedGroups.map((group) => group.group_key),
    [mergedGroups],
  )
  const [selectedMergeGroupKeys, setSelectedMergeGroupKeys] = useState<Set<string>>(
    () => new Set(allMergedGroupKeys),
  )
  const selectedMergedGroupsCount = selectedMergeGroupKeys.size

  function toggleMergedGroup(groupKey: string) {
    setSelectedMergeGroupKeys((currentKeys) => {
      const nextKeys = new Set(currentKeys)
      if (nextKeys.has(groupKey)) {
        nextKeys.delete(groupKey)
      } else {
        nextKeys.add(groupKey)
      }
      return nextKeys
    })
  }

  function selectAllMergedGroups() {
    setSelectedMergeGroupKeys(new Set(allMergedGroupKeys))
  }

  function deselectAllMergedGroups() {
    setSelectedMergeGroupKeys(new Set())
  }

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section
        className="registry-dialog registry-cleaning-report-dialog"
        role="dialog"
        aria-modal="true"
      >
        <header>
          <h2>Report pulizia anagrafica</h2>
        </header>
        <div className="registry-cleaning-report-body">
          <p>
            Gruppi uniti: {report.merged_count} / righe eliminate:{' '}
            {report.deleted_count} / conflitti da controllare: {report.conflict_count}
          </p>
          {mode === 'preview' ? (
            <p>
              Questa e una proposta: l'anagrafica verra modificata solo dopo
              conferma.
            </p>
          ) : null}
          {mergedGroups.length > 0 ? (
            <>
              {mode === 'preview' ? (
                <div className="registry-cleaning-selection-toolbar">
                  <span>
                    Righe selezionate per eliminazione: {selectedMergedGroupsCount} /{' '}
                    {mergedGroups.length}
                  </span>
                  <button onClick={selectAllMergedGroups} disabled={isRunning}>
                    Seleziona tutte
                  </button>
                  <button onClick={deselectAllMergedGroups} disabled={isRunning}>
                    Deseleziona tutte
                  </button>
                </div>
              ) : null}
              <RegistryCleaningReportGroupList
                title="Righe unite"
                groups={mergedGroups}
                mode={mode}
                selectedMergeGroupKeys={selectedMergeGroupKeys}
                onToggleMergedGroup={toggleMergedGroup}
              />
            </>
          ) : (
            <p>Nessuna riga eliminata automaticamente.</p>
          )}
          {conflictGroups.length > 0 ? (
            <RegistryCleaningReportGroupList
              title="Duplicati non uniti per dati diversi"
              groups={conflictGroups}
              mode={mode}
              selectedMergeGroupKeys={selectedMergeGroupKeys}
              onToggleMergedGroup={toggleMergedGroup}
            />
          ) : null}
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onDownload} disabled={!reportFileUrl || isRunning}>
            Scarica Excel
          </button>
          {mode === 'preview' ? (
            <>
              <button onClick={onClose} disabled={isRunning}>Annulla</button>
              <button
                className="registry-save-button"
                onClick={() => onApply([...selectedMergeGroupKeys])}
                disabled={isRunning}
              >
                {isRunning ? 'Pulizia in corso...' : 'Applica pulizia'}
              </button>
            </>
          ) : (
            <button className="registry-save-button" onClick={onClose}>OK</button>
          )}
        </div>
      </section>
    </div>
  )
}

function RegistryCleaningReportGroupList({
  title,
  groups,
  mode,
  selectedMergeGroupKeys,
  onToggleMergedGroup,
}: {
  title: string
  groups: RegistryCleaningReportGroup[]
  mode: RegistryCleaningReportMode
  selectedMergeGroupKeys: Set<string>
  onToggleMergedGroup: (groupKey: string) => void
}) {
  return (
    <section className="registry-cleaning-report-section">
      <h3>{title}</h3>
      {groups.map((group, groupIndex) => (
        <article
          className="registry-cleaning-report-group"
          key={`${group.group_key}-${group.status}-${groupIndex}`}
        >
          {mode === 'preview' && group.status === 'merged' ? (
            <label className="registry-cleaning-group-selector">
              <input
                type="checkbox"
                checked={selectedMergeGroupKeys.has(group.group_key)}
                onChange={() => onToggleMergedGroup(group.group_key)}
              />
              <span>Elimina le righe duplicate di questo gruppo</span>
            </label>
          ) : null}
          <p>Identificativi: {group.fiscal_identifiers.join(', ')}</p>
          {group.deleted_records.length > 0 ? (
            <div>
              <strong>Righe eliminate</strong>
              {group.deleted_records.map((record) => (
                <RegistryCleaningReportRecordRow
                  key={`deleted-${record.registry_id}-${record.source_row}`}
                  record={record}
                />
              ))}
            </div>
          ) : null}
          <div>
            <strong>
              {group.status === 'merged' ? 'Riga tenuta' : 'Righe da controllare'}
            </strong>
            {(group.status === 'merged' ? group.kept_records : group.source_records).map(
              (record) => (
                <RegistryCleaningReportRecordRow
                  key={`kept-${record.registry_id}-${record.source_row}`}
                  record={record}
                />
              ),
            )}
          </div>
        </article>
      ))}
    </section>
  )
}

function RegistryCleaningReportRecordRow({
  record,
}: {
  record: RegistryCleaningReportRecord
}) {
  return (
    <div className="registry-cleaning-report-record">
      <span>Riga {record.source_row}</span>
      <span>{record.codice_anagrafica ?? '-'}</span>
      <span>{record.normalized_name || record.nominativo}</span>
      <span>C.F. {record.codice_fiscale ?? '-'}</span>
      <span>P.IVA {record.partita_iva ?? '-'}</span>
      <span>{record.tipologia || '-'}</span>
    </div>
  )
}

function RegistryImportErrorDialog({
  message,
  onClose,
}: {
  message: string
  onClose: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog registry-import-error-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>File anagrafica non valido</h2>
          <button onClick={onClose} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="registry-import-error-body">
          <p>{message}</p>
        </div>
        <div className="registry-cleaning-actions">
          <button className="registry-save-button" onClick={onClose}>OK</button>
        </div>
      </section>
    </div>
  )
}

function FeedbackExportDialog({
  state,
  onCancel,
  onConfirm,
}: {
  state: Exclude<FeedbackExportState, null>
  onCancel: () => void
  onConfirm: () => void
}) {
  const isExporting = state.kind === 'exporting'

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section
        className="registry-dialog feedback-export-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-export-title"
      >
        <header>
          <h2 id="feedback-export-title">
            {state.kind === 'success'
              ? 'Pacchetto completato'
              : state.kind === 'error'
                ? 'Esportazione non completata'
                : 'Esporta dati per miglioramento'}
          </h2>
          {!isExporting ? (
            <button onClick={onCancel} aria-label="Chiudi">
              x
            </button>
          ) : null}
        </header>
        <div className="feedback-export-dialog-body">
          {state.kind === 'confirm' ? (
            <>
              <p>
                Verrà creato un pacchetto contenente i risultati dell'estrazione,
                le correzioni effettuate e i PDF disponibili.
              </p>
              <p>
                Questi dati servono esclusivamente allo sviluppatore per analizzare
                e migliorare il riconoscimento delle fatture.
              </p>
              <div className="feedback-export-destination">
                <strong>Destinazione</strong>
                <span>Desktop</span>
              </div>
            </>
          ) : state.kind === 'exporting' ? (
            <div className="feedback-export-progress" role="status">
              <strong>Preparazione del pacchetto...</strong>
              <div className="registry-import-progress-bar" aria-hidden="true">
                <span />
              </div>
              <p>Verifica dei dati e copia dei PDF disponibili.</p>
            </div>
          ) : state.kind === 'success' ? (
            <>
              <p>Il pacchetto è stato salvato sul Desktop.</p>
              <div className="feedback-export-result">
                <strong>{state.folderName}</strong>
                <span>{state.invoiceCount} fatture raccolte</span>
                {state.missingPdfCount > 0 ? (
                  <small>
                    {state.missingPdfCount} PDF non erano più disponibili e sono
                    segnalati nel pacchetto.
                  </small>
                ) : (
                  <small>I PDF disponibili sono stati inclusi.</small>
                )}
              </div>
            </>
          ) : (
            <p className="feedback-export-error">{state.message}</p>
          )}
        </div>
        {!isExporting ? (
          <div className="registry-cleaning-actions">
            {state.kind === 'confirm' ? (
              <>
                <button onClick={onCancel}>Annulla</button>
                <button className="registry-save-button" onClick={onConfirm}>
                  Esporta
                </button>
              </>
            ) : state.kind === 'error' ? (
              <>
                <button onClick={onCancel}>Chiudi</button>
                <button className="registry-save-button" onClick={onConfirm}>
                  Riprova
                </button>
              </>
            ) : (
              <button className="registry-save-button" onClick={onCancel}>
                Chiudi
              </button>
            )}
          </div>
        ) : null}
      </section>
    </div>
  )
}

function RegistryDeleteConfirmDialog({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog registry-delete-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Cancella anagrafica</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="registry-delete-dialog-body">
          <p>
            L'anagrafica caricata verra rimossa da questa app.
          </p>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button className="registry-delete-button" onClick={onConfirm}>
            Cancella
          </button>
        </div>
      </section>
    </div>
  )
}

function AddRegistryRecordConfirmDialog({
  record,
  onCancel,
  onConfirm,
}: {
  record: RegistryPreviewRecord
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog registry-add-record-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Aggiungi in anagrafica</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="registry-add-record-dialog-body">
          <p>
            Vuoi aggiungere il codice fiscale e i campi collegati di questa
            fattura all'anagrafica?
          </p>
          <div className="registry-add-record-preview">
            <span>{record.nominativo || '-'}</span>
            <span>C.F. {record.codice_fiscale ?? '-'}</span>
            <span>P.IVA {record.partita_iva ?? '-'}</span>
            <span>{record.tipologia || '-'}</span>
          </div>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button className="registry-save-button" onClick={onConfirm}>
            Conferma
          </button>
        </div>
      </section>
    </div>
  )
}

function InvoiceSaveValidationDialog({
  validation,
  onCancel,
  onContinue,
  onAddRegistryRecord,
}: {
  validation: PendingInvoiceSaveValidation
  onCancel: () => void
  onContinue: () => void
  onAddRegistryRecord: () => void
}) {
  const hasBlockingIssue = validationHasBlockingIssue(validation)

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog invoice-save-validation-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Controllo prima del salvataggio</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="invoice-save-validation-body">
          <p>
            {hasBlockingIssue
              ? 'Correggi la ritenuta prima di salvare.'
              : 'La fattura contiene dati da controllare. Puoi continuare comunque oppure sistemare prima le informazioni.'}
          </p>
          <div className="invoice-save-validation-issues">
            {validation.issues.map((issue, issueIndex) => (
              <article key={`${issue.kind}-${issue.detail}-${issueIndex}`}>
                <strong>{issue.title}</strong>
                <span>{issue.detail}</span>
                {issue.record ? (
                  <div className="registry-add-record-preview">
                    <span>{issue.record.nominativo || '-'}</span>
                    <span>C.F. {issue.record.codice_fiscale ?? '-'}</span>
                    <span>P.IVA {issue.record.partita_iva ?? '-'}</span>
                    <span>{issue.record.tipologia || '-'}</span>
                  </div>
                ) : null}
                {issue.conflicts ? (
                  <div className="invoice-save-validation-conflicts">
                    {issue.conflicts.map((record) => (
                      <span key={`${record.registry_id}-${record.source_row}`}>
                        Riga {record.source_row}: {record.nominativo || '-'} / P.IVA{' '}
                        {record.partita_iva ?? '-'}
                      </span>
                    ))}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          {validation.registryRecordToAdd ? (
            <button onClick={onAddRegistryRecord}>Aggiungi in anagrafica</button>
          ) : null}
          {!hasBlockingIssue ? (
            <button className="registry-save-button" onClick={onContinue}>
              Continua e salva
            </button>
          ) : null}
        </div>
      </section>
    </div>
  )
}

function BatchReviewActionConfirmDialog({
  action,
  invoiceCount,
  validationIssueCount,
  emptyFieldIssueCount,
  invalidFiscalIssueCount,
  invalidDateIssueCount,
  invalidAmountIssueCount,
  economicMismatchIssueCount,
  newRegistryIssueCount,
  registryConflictIssueCount,
  onCancel,
  onConfirm,
}: PendingBatchReviewAction & {
  invoiceCount: number
  onCancel: () => void
  onConfirm: () => void
}) {
  const isDiscard = action === 'discard'
  const hasBlockingIssue =
    !isDiscard && (invalidAmountIssueCount > 0 || economicMismatchIssueCount > 0)

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog batch-review-action-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>{isDiscard ? 'Scarta gruppo' : 'Salva gruppo'}</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="batch-review-action-body">
          <p>
            {isDiscard
              ? `Sei sicuro di voler scartare ${invoiceCount} fatture dalla revisione?`
              : `Stai per salvare ${invoiceCount} fatture nel registro.`}
          </p>
          {!isDiscard && validationIssueCount > 0 ? (
            <div className="batch-review-validation-summary">
              <strong>Controlli da verificare</strong>
              <span>Campi mancanti: {emptyFieldIssueCount}</span>
              <span>C.F. o P.IVA sospetti: {invalidFiscalIssueCount}</span>
              <span>Date da controllare: {invalidDateIssueCount}</span>
              <span>Importi non validi: {invalidAmountIssueCount}</span>
              <span>Quadrature contabili sospette: {economicMismatchIssueCount}</span>
              <span>Nuovi valori non presenti in anagrafica: {newRegistryIssueCount}</span>
              <span>Possibili conflitti anagrafica: {registryConflictIssueCount}</span>
            </div>
          ) : null}
          {!isDiscard && validationIssueCount === 0 ? (
            <div className="batch-review-validation-summary clean">
              <strong>Nessuna anomalia rilevata dai controlli automatici.</strong>
            </div>
          ) : null}
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button
            className={isDiscard ? 'registry-delete-button' : 'registry-save-button'}
            onClick={onConfirm}
            disabled={hasBlockingIssue}
          >
            {isDiscard
              ? 'Scarta gruppo'
              : hasBlockingIssue
                ? 'Correggi gli importi'
                : 'Continua e salva gruppo'}
          </button>
        </div>
      </section>
    </div>
  )
}

function ProcessingCancelConfirmDialog({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog processing-cancel-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Annulla processamento</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="batch-review-action-body">
          <p>
            Sei sicuro di voler annullare il processamento? L'app completera' il PDF
            in corso e poi chiudera' il processo in modo sicuro.
          </p>
          <p>
            Potrebbero servire alcuni secondi prima che lo stato diventi annullato.
          </p>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button className="registry-delete-button" onClick={onConfirm}>
            Conferma
          </button>
        </div>
      </section>
    </div>
  )
}

function DestructiveActionConfirmDialog({
  title,
  message,
  confirmLabel = 'Elimina',
  onCancel,
  onConfirm,
}: {
  title: string
  message: string
  confirmLabel?: string
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog destructive-action-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>{title}</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="batch-review-action-body">
          <p>{message}</p>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button className="registry-delete-button" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </section>
    </div>
  )
}

function ArchiveReprocessDialog({
  state,
  onCancel,
  onConfirm,
  onClose,
}: {
  state: ArchiveReprocessDialogState
  onCancel: () => void
  onConfirm: () => void
  onClose: () => void
}) {
  const isSuccess = state.phase === 'success'
  const isStarting = state.phase === 'starting'
  const isSourceReprocess = state.target === 'sources'

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>
            {isSuccess
              ? 'Riprocessamento avviato'
              : isSourceReprocess
                ? 'Riprocessa file origine'
                : 'Riprocessa fatture'}
          </h2>
          {state.phase === 'confirm' ? (
            <button onClick={onCancel} aria-label="Chiudi">
              x
            </button>
          ) : null}
        </header>
        <div className="batch-review-action-body">
          {isSuccess ? (
            <>
              <p>
                {isSourceReprocess
                  ? 'I file origine selezionati sono stati aggiunti alle lavorazioni OCR.'
                  : 'Le fatture selezionate sono state aggiunte alle lavorazioni OCR.'}
              </p>
              <p>Puoi seguirne lo stato nella pagina Processi dalla schermata principale.</p>
            </>
          ) : (
            <p>
              {isStarting
                ? 'Avvio del riprocessamento in corso...'
                : isSourceReprocess
                  ? `Vuoi riprocessare ${state.recordIds.length} file origine selezionati?`
                  : `Vuoi riprocessare ${state.recordIds.length} fatture selezionate?`}
            </p>
          )}
        </div>
        <div className="registry-cleaning-actions">
          {isSuccess ? (
            <button className="registry-save-button" onClick={onClose}>
              OK
            </button>
          ) : (
            <>
              <button onClick={onCancel} disabled={isStarting}>Annulla</button>
              <button className="registry-save-button" onClick={onConfirm} disabled={isStarting}>
                {isStarting ? 'Avvio...' : 'Procedi'}
              </button>
            </>
          )}
        </div>
      </section>
    </div>
  )
}

function SegmentationConfirmDialog({
  action,
  onCancel,
  onConfirm,
}: {
  action: PendingSegmentationAction
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog segmentation-confirm-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>{action.title}</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="batch-review-action-body">
          <p>{action.message}</p>
          <p className="segmentation-confirm-note">
            I campi non ancora salvati verranno ricalcolati dai dati OCR delle pagine risultanti.
          </p>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button className="registry-save-button" onClick={onConfirm}>
            Conferma
          </button>
        </div>
      </section>
    </div>
  )
}

function SegmentationProgressDialog({
  state,
}: {
  state: SegmentationProgressState
}) {
  const message =
    state.phase === 'saving'
      ? 'Sto mettendo al sicuro le modifiche gia compilate.'
      : state.phase === 'reloading'
      ? 'Sto preparando la revisione aggiornata.'
      : state.operation === 'split'
        ? 'Sto dividendo la fattura e aggiornando l\'ordine delle pagine.'
        : 'Sto spostando le pagine e aggiornando l\'ordine delle fatture.'

  return (
    <div className="registry-dialog-backdrop segmentation-progress-backdrop">
      <section
        className="registry-dialog segmentation-progress-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-busy="true"
        aria-live="polite"
      >
        <div className="segmentation-progress-content">
          <h2>Modifica delle pagine in corso</h2>
          <p>{message}</p>
          <div className="segmentation-progress-bar" aria-hidden="true">
            <span />
          </div>
          <small>Attendi il completamento prima di continuare.</small>
        </div>
      </section>
    </div>
  )
}

function ProcessingFilesDialog({
  job,
  onClose,
}: {
  job: ProcessingJobPayload
  onClose: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="registry-dialog processing-files-dialog"
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2>File del processo</h2>
          <button onClick={onClose} aria-label="Chiudi">x</button>
        </header>
        <div className="processing-files-list">
          {(job.files ?? []).length > 0 ? (
            <ol>
              {(job.files ?? []).map((file) => (
                <li key={`${file.document_id}-${file.name}`}>
                  {fileNameFromPath(file.name) || file.name}
                </li>
              ))}
            </ol>
          ) : (
            <p>Elenco dei file non disponibile per questo processo precedente.</p>
          )}
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onClose}>Chiudi</button>
        </div>
      </section>
    </div>
  )
}

function DocumentImportDialog({
  isImporting,
  onSelectFiles,
  onSelectFolder,
  onCancel,
}: {
  isImporting: boolean
  onSelectFiles: () => void
  onSelectFolder: () => void
  onCancel: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog document-import-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Carica documenti</h2>
          <button onClick={onCancel} aria-label="Chiudi" disabled={isImporting}>
            x
          </button>
        </header>
        <div className="document-import-body">
          <p className="document-import-empty">
            Scegli più PDF oppure una cartella. La lavorazione OCR partirà subito.
          </p>
          <div className="document-import-actions">
            <button onClick={onSelectFiles} disabled={isImporting}>
              File
            </button>
            <button onClick={onSelectFolder} disabled={isImporting}>
              Cartella
            </button>
          </div>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel} disabled={isImporting}>
            Chiudi
          </button>
        </div>
      </section>
    </div>
  )
}

function getFieldSuggestions(
  suggestionIndex: SuggestionIndex,
  kind: string,
  inputValue: string,
): FieldSuggestion[] {
  if (!suggestionFieldKinds.has(kind)) {
    return []
  }

  const normalizedInput = normalizeSuggestionValue(kind, inputValue)
  if (normalizedInput.length < (kind === 'p_iva' || kind.includes('codice_fiscale') ? 3 : 2)) {
    return []
  }

  return (suggestionIndex[kind] ?? [])
    .map((candidate) => ({
      value: candidate.value,
      count: candidate.count,
      score: scoreSuggestion(normalizedInput, candidate.normalized),
      relatedFields: candidate.relatedFields,
    }))
    .filter((candidate) => candidate.score >= 45)
    .sort(
      (left, right) =>
        right.score - left.score ||
        right.count - left.count ||
        left.value.localeCompare(right.value),
    )
    .slice(0, 6)
}

function getOrderedFieldSuggestions(
  suggestionIndex: SuggestionIndex,
  records: RegistryPreviewRecord[],
  invoice: ViewerInvoice | undefined,
  field: InvoiceField,
) {
  const genericSuggestions = getFieldSuggestions(
    suggestionIndex,
    field.kind,
    field.value,
  )
  const sourceFieldKind = registryConflictSourceFieldKind(field.kind)
  const selectedRecordKey =
    field.registry_id &&
    field.registry_source_row !== null &&
    field.registry_source_row !== undefined
      ? `${field.registry_id}-${field.registry_source_row}`
      : null
  if (!invoice || !sourceFieldKind || !selectedRecordKey) {
    return genericSuggestions
  }

  const fiscalCode =
    invoice.fields.find((candidate) => candidate.kind === sourceFieldKind)?.value ?? ''
  const matchingRecords = registryRecordsForFiscalCode(
    records,
    sourceFieldKind,
    fiscalCode,
  )
  if (
    matchingRecords.length < 2 ||
    !registryRecordsHaveDifferentFill(matchingRecords) ||
    !matchingRecords.some((record) => registryRecordKey(record) === selectedRecordKey)
  ) {
    return genericSuggestions
  }

  const contextualSuggestions = [...matchingRecords]
    .sort((left, right) => {
      const leftSelected = registryRecordKey(left) === selectedRecordKey ? 1 : 0
      const rightSelected = registryRecordKey(right) === selectedRecordKey ? 1 : 0
      return rightSelected - leftSelected
    })
    .map((record, index): FieldSuggestion => ({
      value: registryFieldValueForInvoiceKind(record, field.kind),
      count: 1,
      score: 200 - index,
      relatedFields: {},
      registryRecord: record,
    }))
  const contextualValues = new Set(
    contextualSuggestions.map((suggestion) =>
      normalizeSuggestionValue(field.kind, suggestion.value),
    ),
  )

  return [
    ...contextualSuggestions,
    ...genericSuggestions.filter(
      (suggestion) =>
        !contextualValues.has(normalizeSuggestionValue(field.kind, suggestion.value)),
    ),
  ].slice(0, 6)
}

function scoreSuggestion(normalizedInput: string, normalizedCandidate: string) {
  if (normalizedCandidate === normalizedInput) {
    return 120
  }

  if (normalizedCandidate.startsWith(normalizedInput)) {
    return 105
  }

  if (normalizedCandidate.includes(normalizedInput)) {
    return 86
  }

  return Math.round(levenshteinSimilarity(normalizedInput, normalizedCandidate) * 78)
}

function levenshteinSimilarity(left: string, right: string) {
  const maxLength = Math.max(left.length, right.length)
  if (maxLength === 0) {
    return 1
  }

  return 1 - levenshteinDistance(left, right) / maxLength
}

function levenshteinDistance(left: string, right: string) {
  const previousRow = Array.from({ length: right.length + 1 }, (_, index) => index)

  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    let previousDiagonal = previousRow[0]
    previousRow[0] = leftIndex

    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const insertionCost = previousRow[rightIndex] + 1
      const deletionCost = previousRow[rightIndex - 1] + 1
      const substitutionCost =
        previousDiagonal + (left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1)
      previousDiagonal = previousRow[rightIndex]
      previousRow[rightIndex] = Math.min(insertionCost, deletionCost, substitutionCost)
    }
  }

  return previousRow[right.length]
}

function fileNameFromPath(filePath: string | null | undefined) {
  if (!filePath) {
    return null
  }

  return filePath.split(/[\\/]/).pop() || filePath
}

function getInvoiceNumberForSort(invoice: ViewerInvoice) {
  if (typeof invoice.displayNumber === 'number' && invoice.displayNumber > 0) {
    return invoice.displayNumber
  }
  const match = /(\d+)/.exec(invoice.title)

  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER
}

function getVariantRank(invoice: ViewerInvoice) {
  switch (invoice.variantLabel) {
    case 'no fallback':
      return 0
    case 'standard':
      return 0
    case 'best+crop':
      return 1
    case 'crop only':
      return 2
    default:
      return 9
  }
}

function getVariantLabel(documentId: string) {
  if (documentId.endsWith('__no_fallback')) {
    return 'no fallback'
  }

  if (documentId.endsWith('__fallback_best_crop')) {
    return 'best+crop'
  }

  if (documentId.endsWith('__fallback_crop_only')) {
    return 'crop only'
  }

  return 'standard'
}

function sortInvoicesForReview(invoices: ViewerInvoice[]) {
  return [...invoices].sort((leftInvoice, rightInvoice) => {
    const invoiceNumberDelta =
      getInvoiceNumberForSort(leftInvoice) - getInvoiceNumberForSort(rightInvoice)

    if (invoiceNumberDelta !== 0) {
      return invoiceNumberDelta
    }

    const sourceDelta = leftInvoice.sourceFile.localeCompare(
      rightInvoice.sourceFile,
    )
    if (sourceDelta !== 0) {
      return sourceDelta
    }

    const variantDelta =
      getVariantRank(leftInvoice) - getVariantRank(rightInvoice)
    if (variantDelta !== 0) {
      return variantDelta
    }

    return leftInvoice.invoiceId.localeCompare(rightInvoice.invoiceId)
  })
}

function archiveFieldValue(record: InvoiceArchiveRecord, fieldKind: string) {
  const candidateKinds = invoiceFieldAliases[fieldKind] ?? [fieldKind]
  for (const candidateKind of candidateKinds) {
    const value = record.invoice?.fields
      ?.find((field) => field.kind === candidateKind)
      ?.value?.trim()
    if (value) {
      return value
    }
  }

  return ''
}

function openArchivedPdfWindow(pdfUrl: string, windowName: string) {
  const previewWindow = window.open(
    runtimeApiUrl(pdfUrl),
    windowName,
    'width=980,height=860,left=120,top=80,resizable=yes,scrollbars=yes',
  )
  previewWindow?.focus()
}

function invoiceRegistryRecordHasEmptyFields(record: InvoiceArchiveRecord) {
  return invoiceRegistryEditableFields.some(
    (field) => !archiveFieldValue(record, field.kind).trim(),
  )
}

function invoiceRegistryRecordsWithUpdatedField(
  records: InvoiceArchiveRecord[],
  recordId: string,
  fieldKind: InvoiceRegistryEditableFieldKind,
  nextValue: string,
) {
  return records.map((record) => {
    if (record.record_id !== recordId) {
      return record
    }

    const currentFields = record.invoice?.fields ?? []
    const hasField = currentFields.some((field) => field.kind === fieldKind)
    const nextFields = hasField
      ? currentFields.map((field) =>
          field.kind === fieldKind ? { ...field, value: nextValue } : field,
        )
      : [
          ...currentFields,
          {
            kind: fieldKind,
            label:
              invoiceRegistryEditableFields.find((field) => field.kind === fieldKind)
                ?.label ?? fieldKind,
            value: nextValue,
            score: 0,
            page: null,
            bbox: null,
            role: 'manual',
          },
        ]

    return {
      ...record,
      invoice: {
        ...(record.invoice ?? {}),
        fields: nextFields,
      },
    }
  })
}

function archiveRecordSearchText(record: InvoiceArchiveRecord) {
  return [
    record.invoice?.title,
    record.source_file_name,
    fileNameFromPath(record.archived_pdf_path),
    archiveFieldValue(record, 'nome_emittente'),
    archiveFieldValue(record, 'codice_fiscale_emittente'),
    archiveFieldValue(record, 'p_iva'),
    archiveFieldValue(record, 'condominio'),
    archiveFieldValue(record, 'codice_fiscale'),
    archiveFieldValue(record, 'numero_fattura'),
    archiveFieldValue(record, 'data_fattura'),
    archiveFieldValue(record, 'totale_fattura'),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

function invoicePdfArchiveSearchText(record: InvoiceArchiveRecord) {
  return [
    archiveFieldValue(record, 'nome_emittente'),
    archiveFieldValue(record, 'condominio'),
    archiveFieldValue(record, 'numero_fattura'),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

function filterInvoiceArchiveRecords(
  records: InvoiceArchiveRecord[],
  searchValue: string,
  filters: InvoiceArchiveFilters,
  sortKey: InvoiceArchiveSortKey,
) {
  const normalizedSearch = searchValue.trim().toLowerCase()
  const filteredRecords = records.filter((record) =>
    archiveRecordMatchesFilters(record, normalizedSearch, filters),
  )

  return [...filteredRecords].sort((leftRecord, rightRecord) =>
    compareInvoiceArchiveRecords(leftRecord, rightRecord, sortKey),
  )
}

function archiveRecordMatchesFilters(
  record: InvoiceArchiveRecord,
  normalizedSearch: string,
  filters: InvoiceArchiveFilters,
) {
  if (
    normalizedSearch &&
    !archiveRecordSearchText(record).includes(normalizedSearch)
  ) {
    return false
  }

  const invoiceDate = archiveComparableDate(archiveFieldValue(record, 'data_fattura'))
  const dateFrom = archiveComparableDate(filters.dateFrom)
  const dateTo = archiveComparableDate(filters.dateTo)
  if (dateFrom && (!invoiceDate || invoiceDate < dateFrom)) {
    return false
  }
  if (dateTo && (!invoiceDate || invoiceDate > dateTo)) {
    return false
  }

  return true
}

function compareInvoiceArchiveRecords(
  leftRecord: InvoiceArchiveRecord,
  rightRecord: InvoiceArchiveRecord,
  sortKey: InvoiceArchiveSortKey,
) {
  switch (sortKey) {
    case 'saved_at_asc':
      return archiveSavedAt(leftRecord).localeCompare(archiveSavedAt(rightRecord))
    case 'data_fattura_desc':
      return archiveComparableDate(archiveFieldValue(rightRecord, 'data_fattura')).localeCompare(
        archiveComparableDate(archiveFieldValue(leftRecord, 'data_fattura')),
      )
    case 'data_fattura_asc':
      return archiveComparableDate(archiveFieldValue(leftRecord, 'data_fattura')).localeCompare(
        archiveComparableDate(archiveFieldValue(rightRecord, 'data_fattura')),
      )
    case 'emittente_asc':
      return archiveFieldValue(leftRecord, 'nome_emittente').localeCompare(
        archiveFieldValue(rightRecord, 'nome_emittente'),
      )
    case 'condominio_asc':
      return archiveFieldValue(leftRecord, 'condominio').localeCompare(
        archiveFieldValue(rightRecord, 'condominio'),
      )
    case 'totale_desc':
      return (
        archiveAmount(archiveFieldValue(rightRecord, 'totale_fattura')) -
        archiveAmount(archiveFieldValue(leftRecord, 'totale_fattura'))
      )
    case 'totale_asc':
      return (
        archiveAmount(archiveFieldValue(leftRecord, 'totale_fattura')) -
        archiveAmount(archiveFieldValue(rightRecord, 'totale_fattura'))
      )
    case 'saved_at_desc':
    default:
      return archiveSavedAt(rightRecord).localeCompare(archiveSavedAt(leftRecord))
  }
}

function archiveSavedAt(record: InvoiceArchiveRecord) {
  return record.saved_at ?? record.discarded_at ?? record.updated_at ?? ''
}

function archiveComparableDate(value: string) {
  const trimmedValue = value.trim()
  const italianMatch = /^(\d{2})-(\d{2})-(\d{4})$/.exec(trimmedValue)
  if (italianMatch) {
    const [, day, month, year] = italianMatch
    return `${year}-${month}-${day}`
  }

  const isoMatch = /^(\d{4})-(\d{2})-(\d{2})/.exec(trimmedValue)
  return isoMatch ? `${isoMatch[1]}-${isoMatch[2]}-${isoMatch[3]}` : ''
}

function archiveAmount(value: string) {
  const normalizedValue = value.replace(/\./g, '').replace(',', '.')
  const amount = Number.parseFloat(normalizedValue)
  return Number.isFinite(amount) ? amount : 0
}

function sourceArchiveProcessingStatusLabel(job: ProcessingJobPayload) {
  if (job.status === 'queued') {
    return 'in coda'
  }
  if (job.status === 'processing') {
    return 'in corso'
  }
  if (job.status === 'finalizing') {
    return job.archive_sources === false ? 'completamento' : 'archiviazione'
  }
  if (job.status === 'cancelling') {
    return 'annullamento in corso'
  }
  if (job.status === 'completed') {
    return 'completato'
  }
  if (job.status === 'cancelled') {
    return 'annullato'
  }
  if (job.status === 'removed') {
    return 'rimossa dalla coda'
  }
  return 'errore'
}

function processingSourceKindLabel(job: ProcessingJobPayload) {
  switch (job.source_kind) {
    case 'source_archive':
      return 'Libreria - File origine'
    case 'saved_invoice':
      return 'Libreria - Fatture salvate'
    case 'discarded_invoice':
      return 'Libreria - Fatture scartate'
    case 'external_upload':
    default:
      return 'File caricati'
  }
}

function runtimeApiUrl(relativeUrl: string) {
  if (/^https?:\/\//i.test(relativeUrl)) {
    return relativeUrl
  }

  return `${registryApiBaseUrl}${relativeUrl.startsWith('/') ? '' : '/'}${relativeUrl}`
}

function reviewBatchDisplayLabel(label: string) {
  return label.replace(/\s*\([^)]*\)\s*$/, '')
}

function clampScrollPosition(value: number, maxValue: number) {
  return Math.min(Math.max(value, 0), Math.max(maxValue, 0))
}

function getFieldsFromManifest(manifest: ViewerManifest): InvoiceField[] {
  const targets = manifest.pages.flatMap((page) =>
    page.targets.map((target) => ({ ...target, page: target.page ?? page.page })),
  )

  return normalizeLegacyWithholdingFields(expectedInvoiceFields.map((expectedField) => {
    const bestTarget = targets
      .filter((target) => target.kind === expectedField.kind)
      .sort((leftTarget, rightTarget) => rightTarget.score - leftTarget.score)[0]

    return {
      kind: expectedField.kind,
      label: expectedField.label,
      value: formatTargetValueForDisplay(
        expectedField.kind,
        bestTarget?.value ?? '',
      ),
      score: bestTarget?.score ?? 0,
      ocr_confidence: bestTarget?.ocr_confidence ?? null,
      page: bestTarget?.page ?? null,
      bbox: bestTarget?.bbox ?? null,
      role: bestTarget?.role ?? null,
    }
  }))
}

function getFieldEvidence(
  field: InvoiceField | undefined,
  currentInvoice: ViewerInvoice | undefined,
  useRegistryReplacedValue = false,
): FieldEvidence | null {
  if (!field || !currentInvoice) {
    return null
  }

  if (field.page && field.bbox) {
    return {
      page: field.page,
      bbox: field.bbox,
      value:
        useRegistryReplacedValue && field.registry_replaced_value
          ? field.registry_replaced_value
          : formatFieldValueForDisplay(field),
      score: field.score,
      ocr_confidence: field.ocr_confidence,
    }
  }

  const fallbackTarget = currentInvoice.pages
    .flatMap((page) =>
      (page.targets ?? []).map((target) => ({
        ...target,
        page: target.page ?? page.page,
      })),
    )
    .filter((target) => target.kind === field.kind)
    .sort((leftTarget, rightTarget) => rightTarget.score - leftTarget.score)[0]

  if (!fallbackTarget) {
    return null
  }

  return {
    page: fallbackTarget.page,
    bbox: fallbackTarget.bbox,
    value: formatTargetValueForDisplay(fallbackTarget.kind, fallbackTarget.value),
    score: fallbackTarget.score,
    ocr_confidence: fallbackTarget.ocr_confidence,
  }
}

function getInvoicesFromManifest(
  manifest: ViewerManifest,
  documentEntry: ViewerDocumentIndexEntry,
): ViewerInvoice[] {
  const reviewInvoiceIds = new Set(documentEntry.review_invoice_ids ?? [])
  if (!manifest.invoices?.length) {
    if (reviewInvoiceIds.size > 0) {
      return []
    }
    return [
      {
        invoiceId: `${documentEntry.id}:invoice-001`,
        manifestInvoiceId: 'invoice-001',
        title: 'Fattura 001',
        documentId: documentEntry.id,
        variantLabel: getVariantLabel(documentEntry.id),
        sourceFile: manifest.source_file,
        ocrLang: manifest.ocr_lang,
        ocrProfile: manifest.ocr_profile,
        fallbackOcrEventCount: manifest.fallback_ocr_events?.length ?? 0,
        basePath: documentEntry.base_path,
        pageNumbers: manifest.pages.map((page) => page.page),
        pages: manifest.pages,
        fields: getFieldsFromManifest(manifest),
      },
    ]
  }

  return manifest.invoices
    .filter(
      (invoice) =>
        reviewInvoiceIds.size === 0 || reviewInvoiceIds.has(invoice.invoice_id),
    )
    .map((invoice) => ({
    invoiceId: `${documentEntry.id}:${invoice.invoice_id}`,
    manifestInvoiceId: invoice.invoice_id,
    title: invoice.title,
    displayNumber: invoice.display_number,
    documentId: documentEntry.id,
    variantLabel: getVariantLabel(documentEntry.id),
    sourceFile: manifest.source_file,
    ocrLang: manifest.ocr_lang,
    ocrProfile: manifest.ocr_profile,
    fallbackOcrEventCount: (manifest.fallback_ocr_events ?? []).filter(
      (event) =>
        typeof event === 'object' &&
        event !== null &&
        'page' in event &&
        invoice.page_numbers.includes(Number(event.page)),
    ).length,
    basePath: documentEntry.base_path,
    pageNumbers: invoice.page_numbers,
    pages: manifest.pages.filter((page) =>
      invoice.page_numbers.includes(page.page),
    ),
    fields: sortInvoiceFieldsForDisplay(
      normalizeLegacyWithholdingFields(invoice.fields).map(getDisplayInvoiceField),
    ),
  }))
}

function reviewDraftIdForInvoice(invoice: ViewerInvoice) {
  const basePathMatch = invoice.basePath?.match(
    /^\/runtime\/manifests\/([^/]+)\/documents\/([^/]+)$/,
  )
  if (
    !basePathMatch ||
    !invoice.documentId ||
    basePathMatch[2] !== invoice.documentId ||
    !invoice.manifestInvoiceId
  ) {
    return null
  }
  return `${basePathMatch[1]}:${invoice.documentId}:${invoice.manifestInvoiceId}`
}

function reviewDraftIdForRelatedInvoice(
  invoice: ViewerInvoice,
  manifestInvoiceId: string,
) {
  const basePathMatch = invoice.basePath?.match(
    /^\/runtime\/manifests\/([^/]+)\/documents\/([^/]+)$/,
  )
  if (!basePathMatch || !invoice.documentId || basePathMatch[2] !== invoice.documentId) {
    return null
  }
  return `${basePathMatch[1]}:${invoice.documentId}:${manifestInvoiceId}`
}

function App() {
  const [invoices, setInvoices] = useState<ViewerInvoice[]>([])
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<string | null>(null)
  const [pageScale, setPageScale] = useState(BASE_PAGE_SCALE)
  const [zoomInput, setZoomInput] = useState('45')
  const [selectedFieldKind, setSelectedFieldKind] = useState<string | null>(null)
  const [originalScanFieldKind, setOriginalScanFieldKind] = useState<string | null>(null)
  const [showAllTargetBoxes, setShowAllTargetBoxes] = useState(false)
  const [showTokenBoxes, setShowTokenBoxes] = useState(false)
  const [showCropBoxes, setShowCropBoxes] = useState(false)
  const [showTableBoxes, setShowTableBoxes] = useState(false)
  const [showBoxLabels, setShowBoxLabels] = useState(false)
  const [isDeveloperMenuOpen, setIsDeveloperMenuOpen] = useState(false)
  const [feedbackExportState, setFeedbackExportState] =
    useState<FeedbackExportState>(null)
  const documentViewportRef = useRef<HTMLDivElement | null>(null)
  const invoiceFieldsPanelRef = useRef<HTMLElement | null>(null)
  const developerMenuRef = useRef<HTMLDivElement | null>(null)
  const registryTableViewportRef = useRef<HTMLDivElement | null>(null)
  const registryFileInputRef = useRef<HTMLInputElement | null>(null)
  const [registryImportState, setRegistryImportState] =
    useState<RegistryImportState>(null)
  const [pdfImportState, setPdfImportState] = useState<PdfImportState>(null)
  const [isDocumentImportDialogOpen, setIsDocumentImportDialogOpen] = useState(false)
  const [isDocumentPickerOpen, setIsDocumentPickerOpen] = useState(false)
  const [isDocumentImportRunning, setIsDocumentImportRunning] = useState(false)
  const [invoiceReviewActionState, setInvoiceReviewActionState] =
    useState<InvoiceReviewActionState>(null)
  const [pendingSegmentationAction, setPendingSegmentationAction] =
    useState<PendingSegmentationAction | null>(null)
  const [segmentationProgressState, setSegmentationProgressState] =
    useState<SegmentationProgressState | null>(null)
  const reviewDraftsRef = useRef<Map<string, ReviewInvoiceDraft>>(new Map())
  const pendingReviewDraftSavesRef = useRef<Map<string, PendingReviewDraftSave>>(
    new Map(),
  )
  const reviewDraftSaveTimersRef = useRef<Map<string, number>>(new Map())
  const dirtyReviewInvoiceIdsRef = useRef<Set<string>>(new Set())
  const [reviewDraftChangeVersion, setReviewDraftChangeVersion] = useState(0)
  const [splitAfterPosition, setSplitAfterPosition] = useState(1)
  const [processingJobsById, setProcessingJobsById] = useState<
    Record<string, ProcessingJobPayload>
  >({})
  const [processingViewMode, setProcessingViewMode] =
    useState<ProcessingViewMode>('active')
  const [selectedProcessingHistoryIds, setSelectedProcessingHistoryIds] =
    useState<Set<string>>(() => new Set())
  const [processingJobInfo, setProcessingJobInfo] =
    useState<ProcessingJobPayload | null>(null)
  const [isProcessingHistoryDeleteConfirmOpen, setIsProcessingHistoryDeleteConfirmOpen] =
    useState(false)
  const processingJobsPollIntervalRef = useRef<number | null>(null)
  const [processingJobIdToCancel, setProcessingJobIdToCancel] = useState<string | null>(null)
  const [isProcessingCancelConfirmOpen, setIsProcessingCancelConfirmOpen] =
    useState(false)
  const [processingJobIdToRemove, setProcessingJobIdToRemove] = useState<string | null>(null)
  const [registryDraftRecordIndexToDelete, setRegistryDraftRecordIndexToDelete] =
    useState<number | null>(null)
  const [isInvoiceDiscardConfirmOpen, setIsInvoiceDiscardConfirmOpen] = useState(false)
  const [isRegistryDeleteConfirmOpen, setIsRegistryDeleteConfirmOpen] = useState(false)
  const [isReviewActionsMenuOpen, setIsReviewActionsMenuOpen] = useState(false)
  const [reviewActionsMenuPosition, setReviewActionsMenuPosition] = useState<{
    top: number
    left: number
  } | null>(null)
  const [isClearReviewBatchesConfirmOpen, setIsClearReviewBatchesConfirmOpen] =
    useState(false)
  const [registryStatus, setRegistryStatus] = useState<RegistryStatus | null>(null)
  const [registryStatusError, setRegistryStatusError] = useState<string | null>(null)
  const [registryPreviewRecords, setRegistryPreviewRecords] = useState<
    RegistryPreviewRecord[]
  >([])
  const [registryDraftRecords, setRegistryDraftRecords] = useState<
    RegistryPreviewRecord[]
  >([])
  const [isRegistryEditMode, setIsRegistryEditMode] = useState(false)
  const [isRegistrySaving, setIsRegistrySaving] = useState(false)
  const [registrySaveStatus, setRegistrySaveStatus] = useState<string | null>(null)
  const [registryPreviewError, setRegistryPreviewError] = useState<string | null>(null)
  const [isRegistryCleaningRunning, setIsRegistryCleaningRunning] = useState(false)
  const [registryCleaningReport, setRegistryCleaningReport] =
    useState<RegistryCleaningReport | null>(null)
  const [registryCleaningReportMode, setRegistryCleaningReportMode] =
    useState<RegistryCleaningReportMode>('preview')
  const [registryCleaningReportFileUrl, setRegistryCleaningReportFileUrl] =
    useState<string | null>(null)
  const archiveMenuTriggerRef = useRef<HTMLButtonElement | null>(null)
  const archiveMenuCloseTimeoutRef = useRef<number | null>(null)
  const [isArchiveMenuOpen, setIsArchiveMenuOpen] = useState(false)
  const [archiveMenuPosition, setArchiveMenuPosition] = useState({ top: 0, left: 0 })
  const [highlightedRegistryRecordKey, setHighlightedRegistryRecordKey] = useState<string | null>(null)
  const [pendingRegistryRecord, setPendingRegistryRecord] =
    useState<PendingRegistryRecord | null>(null)
  const [pendingInvoiceSaveValidation, setPendingInvoiceSaveValidation] =
    useState<PendingInvoiceSaveValidation | null>(null)
  const [pendingInvoiceRegistrySaveValidation, setPendingInvoiceRegistrySaveValidation] =
    useState<PendingInvoiceSaveValidation | null>(null)
  const [pendingBatchReviewAction, setPendingBatchReviewAction] =
    useState<PendingBatchReviewAction | null>(null)
  const [reviewedOriginalScanFields, setReviewedOriginalScanFields] = useState<
    Record<string, boolean>
  >({})
  const currentInvoice = invoices.find(
    (invoice) => invoice.invoiceId === selectedInvoiceId,
  )
  const currentInvoiceIndex = invoices.findIndex(
    (invoice) => invoice.invoiceId === selectedInvoiceId,
  )
  const displayedFields = currentInvoice?.fields ?? []
  const selectedField = displayedFields.find(
    (field) => field.kind === selectedFieldKind,
  )
  const selectedFieldIndex = displayedFields.findIndex(
    (field) => field.kind === selectedFieldKind,
  )
  const selectedFieldEvidence = getFieldEvidence(
    selectedField,
    currentInvoice,
    selectedField?.kind === originalScanFieldKind,
  )
  const zoomPercent = Math.round(pageScale * 100)
  const [manifestSourceFile, setManifestSourceFile] = useState<string | null>(null)
  const [loadedManifests, setLoadedManifests] = useState<
    { manifest: ViewerManifest; documentEntry: ViewerDocumentIndexEntry }[]
  >([])
  const [manifestError, setManifestError] = useState<string | null>(null)
  const [batches, setBatches] = useState<ViewerBatchIndexEntry[]>([])
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [appView, setAppView] = useState<AppView>('review')
  const [invoiceArchiveRecords, setInvoiceArchiveRecords] = useState<
    InvoiceArchiveRecord[]
  >([])
  const [sourceArchiveRecords, setSourceArchiveRecords] = useState<
    ArchivedSourceRecord[]
  >([])
  const [invoiceRegistryDraftRecords, setInvoiceRegistryDraftRecords] = useState<
    InvoiceArchiveRecord[]
  >([])
  const [isInvoiceRegistryEditMode, setIsInvoiceRegistryEditMode] = useState(false)
  const [invoiceRegistrySaveStatus, setInvoiceRegistrySaveStatus] = useState<string | null>(null)
  const [invoiceArchiveError, setInvoiceArchiveError] = useState<string | null>(null)
  const [invoiceArchiveSearch, setInvoiceArchiveSearch] = useState('')
  const [invoiceArchiveFilters, setInvoiceArchiveFilters] =
    useState<InvoiceArchiveFilters>({
      dateFrom: '',
      dateTo: '',
    })
  const [invoiceArchiveSortKey, setInvoiceArchiveSortKey] =
    useState<InvoiceArchiveSortKey>('saved_at_desc')
  const [pdfArchiveViewMode, setPdfArchiveViewMode] =
    useState<PdfArchiveViewMode>('invoices')
  const [invoiceRegistryViewMode, setInvoiceRegistryViewMode] =
    useState<InvoiceRegistryViewMode>('saved')
  const [archiveDeleteTarget, setArchiveDeleteTarget] =
    useState<ArchiveDeleteTarget | null>(null)
  const [archiveReferenceAction, setArchiveReferenceAction] =
    useState<ArchiveReferenceAction | null>(null)
  const [archiveReprocessDialog, setArchiveReprocessDialog] =
    useState<ArchiveReprocessDialogState | null>(null)
  const [focusedArchiveKey, setFocusedArchiveKey] = useState<string | null>(null)
  const [selectedArchiveKeys, setSelectedArchiveKeys] = useState<Set<string>>(
    () => new Set(),
  )
  const [activeSuggestionFieldKind, setActiveSuggestionFieldKind] = useState<string | null>(null)
  const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(-1)
  const [registryFieldConflicts, setRegistryFieldConflicts] = useState<
    Record<string, RegistryFieldConflict>
  >({})
  const suggestionIndex = useMemo(
    () => buildSuggestionIndex(registryPreviewRecords),
    [registryPreviewRecords],
  )
  const registryTableRecords = isRegistryEditMode ? registryDraftRecords : registryPreviewRecords
  const registrySearchIndex = useMemo(
    () => buildRegistrySearchIndex(registryPreviewRecords),
    [registryPreviewRecords],
  )
  const selectedBatch = batches.find((batch) => batch.base_path === selectedBatchId)
  const processingJobs = useMemo(
    () =>
      Object.values(processingJobsById).sort(compareProcessingJobsNewestFirst),
    [processingJobsById],
  )
  const activeProcessingJobs = processingJobs
    .filter((job) =>
      ['queued', 'processing', 'finalizing', 'cancelling'].includes(job.status),
    )
    .sort((left, right) => {
      const leftActive = left.status === 'queued' ? 1 : 0
      const rightActive = right.status === 'queued' ? 1 : 0
      return leftActive - rightActive
    })
  const processingHistoryJobs = processingJobs.filter((job) =>
    ['completed', 'cancelled', 'removed', 'error'].includes(job.status),
  )
  const hasActiveProcessingJob = processingJobs.some(
    (job) =>
      job.status === 'queued' ||
      job.status === 'processing' ||
      job.status === 'finalizing' ||
      job.status === 'cancelling',
  )
  const shouldShowPdfImportState =
    pdfImportState !== null &&
    (pdfImportState.kind === 'error' || !hasActiveProcessingJob)
  const processingJobToRemove = processingJobs.find(
    (job) => job.job_id === processingJobIdToRemove,
  )
  const registryDraftRecordToDelete =
    registryDraftRecordIndexToDelete === null
      ? null
      : registryDraftRecords[registryDraftRecordIndexToDelete]
  const hasSelectedBatchWithInvoices = Boolean(selectedBatch) && invoices.length > 0
  const canPersistCurrentInvoice =
    Boolean(currentInvoice) &&
    Boolean(selectedBatch?.base_path?.startsWith('/runtime/manifests/'))
  const canPersistCurrentBatch =
    hasSelectedBatchWithInvoices &&
    Boolean(selectedBatch?.base_path?.startsWith('/runtime/manifests/'))
  const filteredInvoiceArchiveRecords = useMemo(
    () => {
      const normalizedSearch = invoiceArchiveSearch.trim().toLowerCase()
      return filterInvoiceArchiveRecords(
        invoiceArchiveRecords.filter(
          (record) =>
            record.invoice_archive_visible !== false &&
            Boolean(record.archived_pdf_url || record.archived_pdf_path) &&
            (!normalizedSearch ||
              invoicePdfArchiveSearchText(record).includes(normalizedSearch)),
        ),
        '',
        invoiceArchiveFilters,
        invoiceArchiveSortKey,
      )
    },
    [
      invoiceArchiveRecords,
      invoiceArchiveSearch,
      invoiceArchiveFilters,
      invoiceArchiveSortKey,
    ],
  )
  const filteredSourceArchiveRecords = useMemo(
    () => {
      const query = invoiceArchiveSearch.trim().toLocaleLowerCase('it')
      return sourceArchiveRecords.filter((record) =>
        !query || record.source_file_name.toLocaleLowerCase('it').includes(query),
      )
    },
    [
      sourceArchiveRecords,
      invoiceArchiveSearch,
    ],
  )
  useEffect(() => {
    if (appView !== 'archive' || !focusedArchiveKey) {
      return
    }

    const frameId = window.requestAnimationFrame(() => {
      const destinationRow = Array.from(
        document.querySelectorAll<HTMLElement>('[data-archive-key]'),
      ).find((row) => row.dataset.archiveKey === focusedArchiveKey)
      destinationRow?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [appView, pdfArchiveViewMode, focusedArchiveKey])
  const filteredInvoiceRegistryRecords = useMemo(
    () =>
      invoiceRegistryViewMode === 'exported'
        ? []
        : filterInvoiceArchiveRecords(
            (isInvoiceRegistryEditMode ? invoiceRegistryDraftRecords : invoiceArchiveRecords).filter(
              (record) =>
                record.status === invoiceRegistryViewMode &&
                record.registry_visible !== false,
            ),
            invoiceArchiveSearch,
            invoiceArchiveFilters,
            invoiceArchiveSortKey,
          ),
    [
      invoiceArchiveRecords,
      invoiceRegistryDraftRecords,
      isInvoiceRegistryEditMode,
      invoiceRegistryViewMode,
      invoiceArchiveSearch,
      invoiceArchiveFilters,
      invoiceArchiveSortKey,
    ],
  )

  function applyReviewDraftsToInvoices(sourceInvoices: ViewerInvoice[]) {
    return sourceInvoices.map((invoice) => {
      const draftId = reviewDraftIdForInvoice(invoice)
      const draft = draftId ? reviewDraftsRef.current.get(draftId) : null
      if (!draft) {
        return invoice
      }
      const draftFieldsByKind = new Map(
        draft.fields.map((field) => [field.kind, field]),
      )
      return {
        ...invoice,
        fields: normalizeLegacyWithholdingFields(
          invoice.fields.map((field) =>
            mergeManifestFieldWithReviewDraft(
              field,
              draftFieldsByKind.get(field.kind),
            ),
          ),
        ),
      }
    })
  }

  function mergeManifestFieldWithReviewDraft(
    manifestField: InvoiceField,
    draftField: InvoiceField | undefined,
  ) {
    if (!draftField) {
      return manifestField
    }

    const isUnresolvedDraftPlaceholder =
      !draftField.value.trim() &&
      Number(draftField.score || 0) <= 0 &&
      (draftField.ocr_confidence === null ||
        draftField.ocr_confidence === undefined) &&
      (draftField.page === null || draftField.page === undefined) &&
      !draftField.bbox &&
      !draftField.role &&
      !draftField.registry_id &&
      (draftField.registry_source_row === null ||
        draftField.registry_source_row === undefined) &&
      !draftField.registry_match_group &&
      !draftField.registry_replaced_value

    const isStaleBlankExtraction =
      !draftField.value.trim() &&
      Boolean(manifestField.value.trim()) &&
      draftField.role === 'target' &&
      manifestField.role === 'target' &&
      Boolean(draftField.bbox) &&
      Boolean(manifestField.bbox) &&
      (
        draftField.page !== manifestField.page ||
        draftField.score !== manifestField.score ||
        draftField.bbox?.x1 !== manifestField.bbox?.x1 ||
        draftField.bbox?.y1 !== manifestField.bbox?.y1 ||
        draftField.bbox?.x2 !== manifestField.bbox?.x2 ||
        draftField.bbox?.y2 !== manifestField.bbox?.y2
      )

    if (
      (isUnresolvedDraftPlaceholder || isStaleBlankExtraction) &&
      manifestField.value.trim()
    ) {
      return manifestField
    }

    return { ...manifestField, ...draftField }
  }

  function loadReviewDrafts() {
    return fetch(`${registryApiBaseUrl}/viewer/drafts`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        if (!payload || !Array.isArray(payload.drafts)) {
          throw new Error('Risposta bozze non valida')
        }
        return payload as ReviewDraftsPayload
      })
      .then((payload) => {
        reviewDraftsRef.current = new Map(
          (payload.drafts ?? []).map((draft) => [draft.draft_id, draft]),
        )
      })
  }

  function markSelectedInvoiceDraftDirty() {
    if (!selectedInvoiceId) {
      return
    }
    dirtyReviewInvoiceIdsRef.current.add(selectedInvoiceId)
    setReviewDraftChangeVersion((version) => version + 1)
  }

  function persistPendingReviewDraftSave(draftId: string) {
    const pendingSave = pendingReviewDraftSavesRef.current.get(draftId)
    if (!pendingSave) {
      return Promise.resolve()
    }
    const { invoice, reviewBatchBasePath, version } = pendingSave
    return fetch(`${registryApiBaseUrl}/viewer/drafts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        review_batch_base_path: reviewBatchBasePath,
        document_id: invoice.documentId,
        invoice_id: invoice.manifestInvoiceId,
        fields: invoice.fields,
      }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload.draft as ReviewInvoiceDraft
      })
      .then((draft) => {
        const currentPending = pendingReviewDraftSavesRef.current.get(draftId)
        if (currentPending?.version === version) {
          pendingReviewDraftSavesRef.current.delete(draftId)
          reviewDraftsRef.current.set(draftId, draft)
        }
      })
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Bozza non aggiornata. ${userErrorMessage(
            error,
            'Non riesco a conservare le ultime modifiche.',
          )}`,
        })
        throw error
      })
  }

  function flushReviewDraftSaves() {
    for (const timerId of reviewDraftSaveTimersRef.current.values()) {
      window.clearTimeout(timerId)
    }
    reviewDraftSaveTimersRef.current.clear()
    return Promise.all(
      Array.from(pendingReviewDraftSavesRef.current.keys()).map((draftId) =>
        persistPendingReviewDraftSave(draftId),
      ),
    )
  }

  function removeReviewDraftsLocally(draftIds: string[]) {
    for (const draftId of draftIds) {
      const timerId = reviewDraftSaveTimersRef.current.get(draftId)
      if (timerId !== undefined) {
        window.clearTimeout(timerId)
      }
      reviewDraftSaveTimersRef.current.delete(draftId)
      pendingReviewDraftSavesRef.current.delete(draftId)
      reviewDraftsRef.current.delete(draftId)
    }
  }

  function deleteReviewDrafts(draftIds: string[]) {
    const uniqueDraftIds = Array.from(new Set(draftIds.filter(Boolean)))
    if (uniqueDraftIds.length === 0) {
      return Promise.resolve()
    }
    return fetch(`${registryApiBaseUrl}/viewer/drafts`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_ids: uniqueDraftIds }),
    }).then(async (response) => {
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.error ?? `HTTP ${response.status}`)
      }
      removeReviewDraftsLocally(uniqueDraftIds)
    })
  }

  const loadProcessedReviewBatchesRef = useRef(loadProcessedReviewBatches)
  const loadSampleViewerBatchesRef = useRef(loadSampleViewerBatches)
  const pollProcessingJobsRef = useRef(pollProcessingJobs)
  loadProcessedReviewBatchesRef.current = loadProcessedReviewBatches
  loadSampleViewerBatchesRef.current = loadSampleViewerBatches
  pollProcessingJobsRef.current = pollProcessingJobs

  useEffect(() => {
    loadReviewDrafts()
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Bozze non disponibili. ${userErrorMessage(
            error,
            'Non riesco a recuperare le compilazioni non ancora salvate.',
          )}`,
        })
      })
      .then(() => loadProcessedReviewBatchesRef.current())
      .then((hasReviewBatches) => {
        if (!hasReviewBatches) {
          loadSampleViewerBatchesRef.current()
        }
      })
      .catch(() => {
        loadSampleViewerBatchesRef.current()
      })
  }, [])

  useEffect(() => {
    const dirtyInvoiceIds = Array.from(dirtyReviewInvoiceIdsRef.current)
    dirtyReviewInvoiceIdsRef.current.clear()
    if (!selectedBatch?.base_path) {
      return
    }

    for (const invoiceId of dirtyInvoiceIds) {
      const invoice = invoices.find((candidate) => candidate.invoiceId === invoiceId)
      if (!invoice) {
        continue
      }
      const draftId = reviewDraftIdForInvoice(invoice)
      if (!draftId) {
        continue
      }
      const previousVersion =
        pendingReviewDraftSavesRef.current.get(draftId)?.version ?? 0
      pendingReviewDraftSavesRef.current.set(draftId, {
        invoice,
        reviewBatchBasePath: selectedBatch.base_path,
        version: previousVersion + 1,
      })
      const existingTimer = reviewDraftSaveTimersRef.current.get(draftId)
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer)
      }
      const timerId = window.setTimeout(() => {
        reviewDraftSaveTimersRef.current.delete(draftId)
        void persistPendingReviewDraftSave(draftId).catch(() => undefined)
      }, 350)
      reviewDraftSaveTimersRef.current.set(draftId, timerId)
    }
  }, [invoices, reviewDraftChangeVersion, selectedBatch?.base_path])

  useEffect(() => {
    function persistDraftsBeforeClose() {
      for (const pendingSave of pendingReviewDraftSavesRef.current.values()) {
        navigator.sendBeacon(
          `${registryApiBaseUrl}/viewer/drafts`,
          new Blob(
            [
              JSON.stringify({
                review_batch_base_path: pendingSave.reviewBatchBasePath,
                document_id: pendingSave.invoice.documentId,
                invoice_id: pendingSave.invoice.manifestInvoiceId,
                fields: pendingSave.invoice.fields,
              }),
            ],
            { type: 'application/json' },
          ),
        )
      }
    }

    window.addEventListener('beforeunload', persistDraftsBeforeClose)
    return () => {
      window.removeEventListener('beforeunload', persistDraftsBeforeClose)
    }
  }, [])

  function loadSampleViewerBatches() {
    fetch(`${sampleBatchIndexPath}?v=${manifestCacheKey}`, { cache: 'no-store' })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        return response.json()
      })
      .then((index: ViewerBatchIndex) => {
        const batchEntries = index.batches ?? []
        setBatches(batchEntries)
        setSelectedBatchId(batchEntries[0]?.base_path ?? null)
        if (batchEntries[0]) {
          loadBatch(batchEntries[0])
          return
        }
        setManifestError('Nessun gruppo di fatture disponibile')
      })
      .catch(() => {
        loadLegacyDocumentIndex()
      })
  }

  useEffect(() => {
    pollProcessingJobsRef.current()
    return () => {
      stopProcessingJobsPolling()
    }
  }, [])

  useEffect(() => {
    return () => {
      if (archiveMenuCloseTimeoutRef.current !== null) {
        window.clearTimeout(archiveMenuCloseTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!isDeveloperMenuOpen) {
      return
    }

    function closeDeveloperMenu(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !developerMenuRef.current?.contains(event.target)
      ) {
        setIsDeveloperMenuOpen(false)
      }
    }

    function closeDeveloperMenuWithKeyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsDeveloperMenuOpen(false)
      }
    }

    document.addEventListener('pointerdown', closeDeveloperMenu)
    document.addEventListener('keydown', closeDeveloperMenuWithKeyboard)
    return () => {
      document.removeEventListener('pointerdown', closeDeveloperMenu)
      document.removeEventListener('keydown', closeDeveloperMenuWithKeyboard)
    }
  }, [isDeveloperMenuOpen])

  useEffect(() => {
    if (!segmentationProgressState) {
      return
    }

    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur()
    }

    function blockKeyboardInteraction(event: KeyboardEvent) {
      event.preventDefault()
      event.stopImmediatePropagation()
    }

    window.addEventListener('keydown', blockKeyboardInteraction, true)
    return () => {
      window.removeEventListener('keydown', blockKeyboardInteraction, true)
    }
  }, [segmentationProgressState])

  useEffect(() => {
    loadRegistryStatus()
    loadRegistryRecords()
  }, [])

  useEffect(() => {
    if (registryPreviewRecords.length === 0) {
      return
    }

    const applyRegistryMatchesTimeout = window.setTimeout(() => {
      setInvoices((currentInvoices) => {
        if (currentInvoices.length === 0) {
          return currentInvoices
        }

        const registryMatchedInvoices = applyRegistryMatchesToInvoices(
          currentInvoices,
          registryPreviewRecords,
        )
        setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
        return registryMatchedInvoices.invoices
      })
    }, 0)

    return () => {
      window.clearTimeout(applyRegistryMatchesTimeout)
    }
  }, [registryPreviewRecords])

  useEffect(() => {
    if (loadedManifests.length === 0) {
      return
    }

    const applyLoadedManifestsTimeout = window.setTimeout(() => {
      const manifestInvoices = sortInvoicesForReview(
        loadedManifests.flatMap(({ manifest, documentEntry }) =>
          getInvoicesFromManifest(manifest, documentEntry),
        ),
      )
      if (registryPreviewRecords.length === 0) {
        setInvoices(applyReviewDraftsToInvoices(manifestInvoices))
        setRegistryFieldConflicts({})
        setSelectedInvoiceId((currentInvoiceId) =>
          currentInvoiceId ?? manifestInvoices[0]?.invoiceId ?? null,
        )
        return
      }

      const registryMatchedInvoices = applyRegistryMatchesToInvoices(
        manifestInvoices,
        registryPreviewRecords,
      )
      setInvoices(applyReviewDraftsToInvoices(registryMatchedInvoices.invoices))
      setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
      setSelectedInvoiceId((currentInvoiceId) =>
        currentInvoiceId ?? registryMatchedInvoices.invoices[0]?.invoiceId ?? null,
      )
    }, 0)

    return () => {
      window.clearTimeout(applyLoadedManifestsTimeout)
    }
  }, [loadedManifests, registryPreviewRecords])

  function loadLegacyDocumentIndex() {
    fetch(`${sampleIndexPath}?v=${manifestCacheKey}`, { cache: 'no-store' })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        return response.json()
      })
      .then((index: ViewerDocumentIndex) => {
        setBatches([])
        setSelectedBatchId(null)
        loadDocumentEntries(index.documents)
      })
      .catch((error) => {
        setManifestError(
          userErrorMessage(
            error,
            "Non ho trovato fatture elaborate da visualizzare.",
          ),
        )
      })
  }

  function loadBatch(
    batch: ViewerBatchIndexEntry,
    preferredManifestInvoiceId?: string,
  ) {
    return fetch(`${batch.base_path}/viewer_index.json?v=${manifestCacheKey}`, {
      cache: 'no-store',
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`${batch.id}: HTTP ${response.status}`)
        }

        return response.json()
      })
      .then((index: ViewerDocumentIndex) => {
        return loadDocumentEntries(index.documents, preferredManifestInvoiceId)
      })
      .catch((error) => {
        setInvoices([])
        setSelectedInvoiceId(null)
        setManifestSourceFile(null)
        setLoadedManifests([])
        setManifestError(
          userErrorMessage(error, "Non riesco ad aprire questo gruppo di fatture."),
        )
        return false
      })
  }

  function loadProcessedReviewBatches(
    preferredBatchBasePath?: string,
    preferredManifestInvoiceId?: string,
  ) {
    return fetch(`${registryApiBaseUrl}/viewer/review-batches`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ViewerBatchIndex
      })
      .then((index) => {
        const batchEntries = index.batches ?? []
        if (batchEntries.length === 0) {
          setBatches([])
          setSelectedBatchId(null)
          return false
        }

        const selectedBatch =
          batchEntries.find(
            (batch) => batch.base_path === preferredBatchBasePath,
          ) ?? batchEntries[0]
        setBatches(batchEntries)
        setSelectedBatchId(selectedBatch.base_path)
        return loadBatch(selectedBatch, preferredManifestInvoiceId).then(() => true)
      })
  }

  function requestClearReviewBatches() {
    setIsReviewActionsMenuOpen(false)
    setIsClearReviewBatchesConfirmOpen(true)
  }

  function toggleReviewActionsMenu(event: MouseEvent<HTMLButtonElement>) {
    if (isReviewActionsMenuOpen) {
      setIsReviewActionsMenuOpen(false)
      return
    }

    const buttonRect = event.currentTarget.getBoundingClientRect()
    const menuWidth = 220
    const gap = 8
    const left =
      buttonRect.right + gap + menuWidth <= window.innerWidth
        ? buttonRect.right + gap
        : Math.max(8, buttonRect.left - menuWidth - gap)
    const top = Math.min(
      Math.max(8, buttonRect.top),
      Math.max(8, window.innerHeight - 76),
    )

    setReviewActionsMenuPosition({ top, left })
    setIsReviewActionsMenuOpen(true)
  }

  function confirmClearReviewBatches() {
    setIsClearReviewBatchesConfirmOpen(false)
    setInvoiceReviewActionState({
      kind: 'discarding',
      message: 'Eliminazione revisioni...',
    })

    fetch(`${registryApiBaseUrl}/viewer/review-batches/clear`, {
      method: 'POST',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload
      })
      .then(() => {
        setBatches([])
        setSelectedBatchId(null)
        setInvoices([])
        setSelectedInvoiceId(null)
        setLoadedManifests([])
        setManifestSourceFile(null)
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setManifestError('Nessuna revisione da mostrare')
        setInvoiceReviewActionState({
          kind: 'success',
          message: 'Revisioni eliminate.',
        })
        window.setTimeout(() => {
          setInvoiceReviewActionState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1400)
      })
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Revisioni non eliminate. ${userErrorMessage(
            error,
            'Non riesco a eliminare le revisioni.',
          )}`,
        })
      })
  }

  function selectBatch(batchId: string) {
    const batch = batches.find((candidate) => candidate.base_path === batchId)
    if (!batch) {
      return
    }

    setSelectedBatchId(batch.base_path)
    setSelectedFieldKind(null)
    setActiveSuggestionFieldKind(null)
    resetDocumentView()
    loadBatch(batch)
  }

  function openDocumentImportDialog() {
    setIsDocumentImportDialogOpen(true)
    setPdfImportState(null)
  }

  function closeDocumentImportDialog() {
    if (isDocumentImportRunning) {
      return
    }
    setIsDocumentImportDialogOpen(false)
  }

  function discardPendingImportSources(sources: PendingImportSource[]) {
    if (sources.length === 0) {
      return
    }

    fetch(`${registryApiBaseUrl}/batches/import-sources/discard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_ids: sources.map((source) => source.source_id),
      }),
    }).catch(() => {
      // Pulizia non critica: al riavvio dell'app queste sorgenti temporanee spariscono.
    })
  }

  function addPdfImportSource() {
    setIsDocumentPickerOpen(true)
    setPdfImportState({
      kind: 'loading',
      message: 'Seleziona i PDF nella finestra Windows...',
    })

    fetch(`${registryApiBaseUrl}/batches/pick-files`, {
      method: 'POST',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as PendingImportSourcePayload
      })
      .then((payload) => {
        if (payload.cancelled) {
          setPdfImportState(null)
          return
        }
        if (payload.source) {
          importAndStartSource(payload.source)
        }
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `PDF non aggiunti. ${userErrorMessage(
            error,
            'Non riesco ad aggiungere i PDF selezionati.',
          )}`,
        })
      })
      .finally(() => {
        setIsDocumentPickerOpen(false)
      })
  }

  function addFolderImportSource() {
    setIsDocumentPickerOpen(true)
    setPdfImportState({
      kind: 'loading',
      message: 'Seleziona una cartella PDF nella finestra Windows...',
    })

    fetch(`${registryApiBaseUrl}/batches/pick-folder`, {
      method: 'POST',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as PendingImportSourcePayload
      })
      .then((payload) => {
        if (payload.cancelled) {
          setPdfImportState(null)
          return
        }
        if (payload.source) {
          importAndStartSource(payload.source)
        }
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `Cartella non aggiunta. ${userErrorMessage(
            error,
            'Controlla che la cartella contenga PDF validi.',
          )}`,
        })
      })
      .finally(() => {
        setIsDocumentPickerOpen(false)
      })
  }

  function importAndStartSource(source: PendingImportSource) {
    if (isDocumentImportRunning) {
      return
    }

    setIsDocumentImportRunning(true)
    setPdfImportState({
      kind: 'loading',
      message: `Caricamento di ${source.document_count} PDF e avvio OCR...`,
    })

    fetch(`${registryApiBaseUrl}/batches/import-and-process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_ids: [source.source_id],
      }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ImportedBatchImportPayload & { job: ProcessingJobPayload }
      })
      .then((payload) => {
        setProcessingJobsById((currentJobs) => ({
          ...currentJobs,
          [payload.job.job_id]: payload.job,
        }))
        setIsDocumentImportDialogOpen(false)
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(
            payload.job,
            `OCR avviato su ${payload.batch.document_count} PDF`,
          ),
        })
        startProcessingJobsPolling()
      })
      .catch((error) => {
        discardPendingImportSources([source])
        setPdfImportState({
          kind: 'error',
          message: `Lavorazione non avviata. ${userErrorMessage(
            error,
            'Controlla che i PDF selezionati siano validi.',
          )}`,
        })
      })
      .finally(() => {
        setIsDocumentImportRunning(false)
      })
  }

  function requestReprocessSelectedArchivedSources() {
    const sourceIds = Array.from(selectedArchiveKeys)
    if (sourceIds.length === 0) {
      setPdfImportState({
        kind: 'error',
        message: 'Seleziona almeno un file origine da riprocessare.',
      })
      return
    }

    setArchiveReprocessDialog({
      target: 'sources',
      phase: 'confirm',
      recordIds: sourceIds,
    })
  }

  function confirmReprocessSelectedArchivedSources() {
    const sourceIds = archiveReprocessDialog?.recordIds ?? []
    if (sourceIds.length === 0) {
      setArchiveReprocessDialog(null)
      return
    }

    setArchiveReprocessDialog({
      target: 'sources',
      phase: 'starting',
      recordIds: sourceIds,
    })
    setPdfImportState({
      kind: 'loading',
      message: 'Riprocessamento OCR avviato...',
    })

    fetch(`${registryApiBaseUrl}/archive/sources/reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_ids: sourceIds,
      }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ProcessingJobPayload
      })
      .then((job) => {
        setProcessingJobsById((currentJobs) => ({
          ...currentJobs,
          [job.job_id]: job,
        }))
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, 'Riprocessamento OCR avviato...'),
        })
        setSelectedArchiveKeys(new Set())
        setArchiveReprocessDialog({
          target: 'sources',
          phase: 'success',
          recordIds: sourceIds,
        })
        startProcessingJobsPolling()
      })
      .catch((error) => {
        setArchiveReprocessDialog(null)
        setPdfImportState({
          kind: 'error',
          message: `Riprocessamento non avviato. ${userErrorMessage(
            error,
            'Non riesco ad avviare la nuova lavorazione OCR.',
          )}`,
        })
      })
  }

  function requestReprocessSelectedArchivedInvoices() {
    const recordIds = Array.from(selectedArchiveKeys)
    if (recordIds.length === 0) {
      setInvoiceArchiveError('Seleziona almeno una fattura da riprocessare.')
      return
    }

    const selectedStatuses = new Set(
      invoiceArchiveRecords
        .filter((record) => selectedArchiveKeys.has(record.record_id))
        .map((record) => record.status),
    )
    if (selectedStatuses.size > 1) {
      setInvoiceArchiveError(
        'Seleziona solo fatture salvate oppure solo fatture scartate.',
      )
      return
    }

    setInvoiceArchiveError(null)
    setArchiveReprocessDialog({ target: 'invoices', phase: 'confirm', recordIds })
  }

  function confirmReprocessSelectedArchivedInvoices() {
    const recordIds = archiveReprocessDialog?.recordIds ?? []
    if (recordIds.length === 0) {
      setArchiveReprocessDialog(null)
      return
    }

    setArchiveReprocessDialog({ target: 'invoices', phase: 'starting', recordIds })
    setInvoiceArchiveError('Avvio del riprocessamento OCR...')
    fetch(`${registryApiBaseUrl}/archive/invoices/reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_ids: recordIds }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ProcessingJobPayload
      })
      .then((job) => {
        setProcessingJobsById((currentJobs) => ({
          ...currentJobs,
          [job.job_id]: job,
        }))
        setSelectedArchiveKeys(new Set())
        setInvoiceArchiveError(null)
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, 'Riprocessamento OCR avviato...'),
        })
        setArchiveReprocessDialog({ target: 'invoices', phase: 'success', recordIds })
        startProcessingJobsPolling()
      })
      .catch((error) => {
        setArchiveReprocessDialog(null)
        setInvoiceArchiveError(
          `Riprocessamento non avviato. ${userErrorMessage(
            error,
            'Non riesco ad avviare la nuova lavorazione OCR.',
          )}`,
        )
      })
  }

  function startProcessingJobsPolling() {
    if (processingJobsPollIntervalRef.current !== null) {
      return
    }
    pollProcessingJobs()
    processingJobsPollIntervalRef.current = window.setInterval(() => {
      pollProcessingJobs()
    }, 1200)
  }

  function stopProcessingJobsPolling() {
    if (processingJobsPollIntervalRef.current === null) {
      return
    }
    window.clearInterval(processingJobsPollIntervalRef.current)
    processingJobsPollIntervalRef.current = null
  }

  function pollProcessingJobs() {
    fetch(`${registryApiBaseUrl}/processing/jobs`, { cache: 'no-store' })
        .then(async (response) => {
          const payload = await response.json().catch(() => null)
          if (!response.ok) {
            throw new Error(payload?.error ?? `HTTP ${response.status}`)
          }
          return payload as ProcessingJobsPayload
        })
        .then((payload) => {
          if (payload.jobs.length === 0) {
            setProcessingJobsById({})
            stopProcessingJobsPolling()
            return
          }
          const jobsById = Object.fromEntries(
            payload.jobs.map((job) => [job.job_id, job]),
          )
          setProcessingJobsById(jobsById)
          const activeJobs = payload.jobs.filter(
            (job) =>
              job.status === 'queued' ||
              job.status === 'processing' ||
              job.status === 'finalizing' ||
              job.status === 'cancelling',
          )
          const latestActiveJob = activeJobs.at(-1)
          if (latestActiveJob) {
            setPdfImportState({
              kind: 'loading',
              message: processingJobMessage(
                latestActiveJob,
                latestActiveJob.status === 'queued'
                  ? 'Processamento in coda'
                  : 'Processamento OCR in corso...',
              ),
            })
            return
          }

          stopProcessingJobsPolling()
          const completedJobs = payload.jobs.filter((job) => job.status === 'completed')
          if (completedJobs.length > 0) {
            const invoiceCount = completedJobs.reduce(
              (total, job) => total + (job.result?.invoice_count ?? 0),
              0,
            )
            const reviewBatchCount = completedJobs.reduce(
              (total, job) => total + (job.result?.review_batch_count ?? 0),
              0,
            )
            setPdfImportState({
              kind: 'success',
              message: `Elaborazione completata: ${invoiceCount} fatture in ${reviewBatchCount} gruppi.`,
            })
            loadProcessedReviewBatches()
            window.setTimeout(() => {
              setPdfImportState((currentState) =>
                currentState?.kind === 'success' ? null : currentState,
              )
            }, 2000)
            return
          }

          const cancelledJob = payload.jobs.find((job) => job.status === 'cancelled')
          if (cancelledJob) {
            setPdfImportState({
              kind: 'success',
              message: processingJobMessage(cancelledJob, 'Processamento annullato.'),
            })
            window.setTimeout(() => {
              setPdfImportState((currentState) =>
                currentState?.kind === 'success' ? null : currentState,
              )
            }, 2500)
            return
          }

          const failedJob = payload.jobs.find((job) => job.status === 'error')
          setPdfImportState({
            kind: 'error',
            message: `Processamento non completato. ${userErrorMessage(
              failedJob?.message,
              'Alcuni PDF non sono stati elaborati correttamente.',
            )}`,
          })
        })
        .catch((error) => {
          stopProcessingJobsPolling()
          setPdfImportState({
            kind: 'error',
            message: `Stato processamento non disponibile. ${userErrorMessage(
              error,
              'Non riesco ad aggiornare lo stato delle lavorazioni.',
            )}`,
          })
        })
  }

  function processingJobMessage(job: ProcessingJobPayload, fallbackMessage: string) {
    const current = job.progress_current
    const total = job.progress_total
    if (
      typeof current === 'number' &&
      typeof total === 'number' &&
      total > 0
    ) {
      const estimatedTimeText = formatEstimatedProcessingTime(
        job.estimated_remaining_seconds,
        current,
      )
      return `${job.message || fallbackMessage} (${current}/${total} file elaborati)${estimatedTimeText}`
    }
    return job.message || fallbackMessage
  }

  function processingJobDetailMessage(job: ProcessingJobPayload) {
    const cleanMessage = (job.message || '').trim()
    if (job.status === 'error') {
      return userErrorMessage(cleanMessage, 'lavorazione non completata')
    }
    if (job.status === 'processing' && job.current_file) {
      return `File corrente: ${fileNameFromPath(job.current_file) ?? job.current_file}`
    }
    if (job.status === 'cancelling') {
      return 'Annullamento della lavorazione in corso.'
    }
    if (job.status === 'finalizing') {
      return 'Archiviazione dei file origine in corso.'
    }
    if (cleanMessage && !/\d+\s*\/\s*\d+\s+file elaborati/i.test(cleanMessage)) {
      return cleanMessage
    }
    if (job.status === 'completed') {
      return 'Elaborazione completata.'
    }
    if (job.status === 'queued') {
      return 'In attesa di avvio.'
    }
    if (job.status === 'cancelled') {
      return 'Lavorazione annullata.'
    }
    if (job.status === 'removed') {
      return 'Rimossa dalla coda.'
    }
    return 'Elaborazione in corso.'
  }

  function processingJobTimeMessage(job: ProcessingJobPayload) {
    if (job.status !== 'processing') {
      return ''
    }
    return formatEstimatedProcessingTime(
      job.estimated_remaining_seconds,
      job.progress_current ?? 0,
    ).replace(/^ \/ /, '')
  }

  function formatEstimatedProcessingTime(
    estimatedRemainingSeconds: number | null | undefined,
    processedFileCount: number,
  ) {
    if (processedFileCount < 3) {
      return ' / tempo stimato in calcolo'
    }
    if (
      typeof estimatedRemainingSeconds !== 'number' ||
      !Number.isFinite(estimatedRemainingSeconds)
    ) {
      return ''
    }

    const roundedSeconds = Math.max(0, Math.round(estimatedRemainingSeconds))
    if (roundedSeconds < 60) {
      return ' / tempo stimato: meno di 1 minuto'
    }

    const hours = Math.floor(roundedSeconds / 3600)
    const minutes = Math.max(1, Math.round((roundedSeconds % 3600) / 60))
    if (hours > 0) {
      return ` / tempo stimato: circa ${hours}h ${minutes}m`
    }
    return ` / tempo stimato: circa ${minutes}m`
  }

  function isProcessingCancellationPending(job: ProcessingJobPayload) {
    return (
      job.status === 'cancelling' ||
      (job.status === 'processing' && job.cancel_requested === true)
    )
  }

  function requestCancelProcessing(jobId: string) {
    if (!jobId) {
      return
    }
    setProcessingJobIdToCancel(jobId)
    setIsProcessingCancelConfirmOpen(true)
  }

  function confirmCancelProcessing() {
    if (!processingJobIdToCancel) {
      setIsProcessingCancelConfirmOpen(false)
      return
    }

    const jobId = processingJobIdToCancel
    setIsProcessingCancelConfirmOpen(false)
    setProcessingJobIdToCancel(null)
    setPdfImportState({
      kind: 'loading',
      message: 'Annullamento richiesto...',
    })

    fetch(`${registryApiBaseUrl}/processing/jobs/${jobId}/cancel`, {
      method: 'POST',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ProcessingJobPayload
      })
      .then((job) => {
        setProcessingJobsById((currentJobs) => ({
          ...currentJobs,
          [job.job_id]: job,
        }))
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, 'Annullamento richiesto...'),
        })
        startProcessingJobsPolling()
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `Annullamento non riuscito. ${userErrorMessage(
            error,
            'Non riesco a inviare la richiesta di annullamento.',
          )}`,
        })
      })
  }

  function requestRemoveProcessingJobFromQueue(jobId: string) {
    setProcessingJobIdToRemove(jobId)
  }

  function confirmRemoveProcessingJobFromQueue() {
    if (!processingJobIdToRemove) {
      return
    }
    const jobId = processingJobIdToRemove
    setProcessingJobIdToRemove(null)
    removeProcessingJobFromQueue(jobId)
  }

  function removeProcessingJobFromQueue(jobId: string) {
    updateProcessingJobAction(jobId, 'remove', 'Lavorazione rimossa dalla coda...')
  }

  function updateProcessingJobAction(
    jobId: string,
    action: 'remove',
    fallbackMessage: string,
  ) {
    fetch(`${registryApiBaseUrl}/processing/jobs/${jobId}/${action}`, {
      method: 'POST',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ProcessingJobPayload
      })
      .then((job) => {
        setProcessingJobsById((currentJobs) => ({
          ...currentJobs,
          [job.job_id]: job,
        }))
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, fallbackMessage),
        })
        if (
          job.status === 'queued' ||
          job.status === 'processing' ||
          job.status === 'finalizing' ||
          job.status === 'cancelling'
        ) {
          startProcessingJobsPolling()
        }
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `Operazione non riuscita. ${userErrorMessage(
            error,
            'Non riesco ad aggiornare questa lavorazione.',
          )}`,
        })
      })
  }

  function saveCurrentInvoice() {
    if (!currentInvoice) {
      return
    }

    const validation = buildInvoiceSaveValidation(currentInvoice, registryPreviewRecords)
    if (validation) {
      setPendingInvoiceSaveValidation(validation)
      return
    }

    persistCurrentInvoice('save')
  }

  function discardCurrentInvoice() {
    setIsInvoiceDiscardConfirmOpen(true)
  }

  function confirmDiscardCurrentInvoice() {
    setIsInvoiceDiscardConfirmOpen(false)
    persistCurrentInvoice('discard')
  }

  function persistCurrentInvoice(action: 'save' | 'discard') {
    if (!currentInvoice || !selectedBatch?.base_path) {
      return
    }

    const isDiscard = action === 'discard'
    setInvoiceReviewActionState({
      kind: isDiscard ? 'discarding' : 'saving',
      message: isDiscard ? 'Archiviazione fattura scartata...' : 'Salvataggio fattura...',
    })

    flushReviewDraftSaves()
      .then(() => persistInvoiceReviewAction(currentInvoice, action))
      .then(() => {
        removeInvoiceFromReview(currentInvoice.invoiceId)
        setInvoiceReviewActionState({
          kind: 'success',
          message: isDiscard ? 'Fattura spostata tra le scartate.' : 'Fattura salvata.',
        })
        window.setTimeout(() => {
          setInvoiceReviewActionState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1400)
        loadProcessedReviewBatches()
      })
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Operazione non completata. ${userErrorMessage(
            error,
            'Non riesco ad aggiornare questa fattura.',
          )}`,
        })
      })
  }

  function persistInvoiceReviewAction(invoice: ViewerInvoice, action: 'save' | 'discard') {
    if (!selectedBatch?.base_path) {
      return Promise.reject(new Error('gruppo non selezionato'))
    }

    return fetch(`${registryApiBaseUrl}/invoices/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        selected_batch_id: selectedBatchId,
        review_batch_base_path: selectedBatch.base_path,
        invoice,
      }),
    }).then(async (response) => {
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.error ?? `HTTP ${response.status}`)
      }
      const draftId = reviewDraftIdForInvoice(invoice)
      if (draftId) {
        removeReviewDraftsLocally([draftId])
      }
      return payload
    })
  }

  function continueInvoiceSaveAfterValidation() {
    if (
      pendingInvoiceSaveValidation &&
      validationHasBlockingIssue(pendingInvoiceSaveValidation)
    ) {
      return
    }
    setPendingInvoiceSaveValidation(null)
    persistCurrentInvoice('save')
  }

  function addValidationRegistryRecord() {
    const record = pendingInvoiceSaveValidation?.registryRecordToAdd
    if (!record) {
      return
    }

    setPendingInvoiceSaveValidation(null)
    openNewRegistryRecord(record)
  }

  function removeInvoiceFromReview(invoiceId: string) {
    setInvoices((currentInvoices) => {
      const nextInvoices = currentInvoices.filter(
        (invoice) => invoice.invoiceId !== invoiceId,
      )
      setSelectedInvoiceId(nextInvoices[0]?.invoiceId ?? null)
      setSelectedFieldKind(null)
      setActiveSuggestionFieldKind(null)
      return nextInvoices
    })
  }

  function requestBatchReviewAction(action: 'save' | 'discard') {
    if (!canPersistCurrentBatch) {
      return
    }

    const validations =
      action === 'save'
        ? invoices
            .map((invoice) => buildInvoiceSaveValidation(invoice, registryPreviewRecords))
            .filter((validation): validation is PendingInvoiceSaveValidation =>
              validation !== null,
            )
        : []
    const issues = validations.flatMap((validation) => validation.issues)

    setPendingBatchReviewAction({
      action,
      invoices: [...invoices],
      validationIssueCount: issues.length,
      emptyFieldIssueCount: issues.filter((issue) => issue.kind === 'empty_fields').length,
      invalidFiscalIssueCount: issues.filter((issue) => issue.kind === 'invalid_fiscal').length,
      invalidDateIssueCount: issues.filter((issue) => issue.kind === 'invalid_date').length,
      invalidAmountIssueCount: issues.filter(
        (issue) => issue.kind === 'invalid_amount',
      ).length,
      economicMismatchIssueCount: issues.filter((issue) => issue.kind === 'economic_mismatch')
        .length,
      newRegistryIssueCount: issues.filter((issue) => issue.kind === 'new_registry_record').length,
      registryConflictIssueCount: issues.filter((issue) => issue.kind === 'registry_conflict').length,
    })
  }

  function confirmBatchReviewAction() {
    if (!pendingBatchReviewAction) {
      return
    }

    if (
      pendingBatchReviewAction.action === 'save' &&
      (pendingBatchReviewAction.invalidAmountIssueCount > 0 ||
        pendingBatchReviewAction.economicMismatchIssueCount > 0)
    ) {
      return
    }

    const { action, invoices: targetInvoices } = pendingBatchReviewAction
    const isDiscard = action === 'discard'
    setPendingBatchReviewAction(null)
    setInvoiceReviewActionState({
      kind: isDiscard ? 'discarding' : 'saving',
      message: isDiscard
        ? `Archiviazione di ${targetInvoices.length} fatture scartate...`
        : `Salvataggio di ${targetInvoices.length} fatture...`,
    })

    ;(async () => {
      await flushReviewDraftSaves()
      for (const invoice of targetInvoices) {
        await persistInvoiceReviewAction(invoice, action)
      }
    })()
      .then(() => {
        setInvoices((currentInvoices) => {
          const persistedIds = new Set(targetInvoices.map((invoice) => invoice.invoiceId))
          return currentInvoices.filter((invoice) => !persistedIds.has(invoice.invoiceId))
        })
        setSelectedInvoiceId(null)
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setInvoiceReviewActionState({
          kind: 'success',
          message: isDiscard
            ? `${targetInvoices.length} fatture spostate tra le scartate.`
            : `${targetInvoices.length} fatture salvate.`,
        })
        window.setTimeout(() => {
          setInvoiceReviewActionState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1600)
        loadProcessedReviewBatches()
      })
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Operazione sul gruppo non completata. ${userErrorMessage(
            error,
            'Alcune fatture potrebbero non essere state aggiornate.',
          )}`,
        })
      })
  }

  function openRegistryFilePicker() {
    registryFileInputRef.current?.click()
  }

  function openRegistryPreview() {
    setAppView('registry')
    setRegistryPreviewError(null)
    setHighlightedRegistryRecordKey(null)
    loadRegistryStatus()
    loadRegistryRecords()
  }

  function requestFeedbackExport() {
    setIsDeveloperMenuOpen(false)
    setFeedbackExportState({ kind: 'confirm' })
  }

  function exportFeedbackToDesktop() {
    setFeedbackExportState({ kind: 'exporting' })
    fetch(`${registryApiBaseUrl}/feedback/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok || !payload?.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload
      })
      .then((payload) => {
        setFeedbackExportState({
          kind: 'success',
          folderName: String(payload.folder_name ?? 'Dati per miglioramento'),
          invoiceCount: Number(payload.stats?.invoices ?? 0),
          missingPdfCount: Number(payload.missing_pdf_count ?? 0),
        })
      })
      .catch((error) => {
        setFeedbackExportState({
          kind: 'error',
          message: userErrorMessage(
            error,
            'Non è stato possibile creare il pacchetto. Riprova o contatta lo sviluppatore.',
          ),
        })
      })
  }

  function toggleArchiveMenu() {
    setIsArchiveMenuOpen((isOpen) => {
      if (!isOpen) {
        if (archiveMenuCloseTimeoutRef.current !== null) {
          window.clearTimeout(archiveMenuCloseTimeoutRef.current)
          archiveMenuCloseTimeoutRef.current = null
        }
        const rect = archiveMenuTriggerRef.current?.getBoundingClientRect()
        if (rect) {
          setArchiveMenuPosition({
            top: rect.top + rect.height / 2,
            left: rect.right + 12,
          })
        }
      }
      return !isOpen
    })
  }

  function keepArchiveMenuOpen() {
    if (archiveMenuCloseTimeoutRef.current !== null) {
      window.clearTimeout(archiveMenuCloseTimeoutRef.current)
      archiveMenuCloseTimeoutRef.current = null
    }
  }

  function scheduleArchiveMenuClose() {
    if (archiveMenuCloseTimeoutRef.current !== null) {
      window.clearTimeout(archiveMenuCloseTimeoutRef.current)
    }
    archiveMenuCloseTimeoutRef.current = window.setTimeout(() => {
      setIsArchiveMenuOpen(false)
      archiveMenuCloseTimeoutRef.current = null
    }, 180)
  }

  function openInvoiceArchive() {
    setIsArchiveMenuOpen(false)
    setPdfArchiveViewMode('invoices')
    setSelectedArchiveKeys(new Set())
    setFocusedArchiveKey(null)
    setAppView('archive')
    setInvoiceArchiveError(null)
    loadInvoiceArchive()
  }

  function requestArchiveReference(
    record: InvoiceArchiveRecord,
    kind: ArchiveReferenceKind,
  ) {
    setArchiveReferenceAction({ record, kind, error: null })
  }

  function resolveArchiveReference(action: ArchiveReferenceAction) {
    if (action.kind === 'invoice') {
      const record = invoiceArchiveRecords.find(
        (candidate) =>
          candidate.record_id === action.record.record_id &&
          candidate.invoice_archive_visible !== false &&
          Boolean(candidate.archived_pdf_url),
      )
      return record?.archived_pdf_url
        ? { key: record.record_id, url: record.archived_pdf_url, mode: 'invoices' as const }
        : null
    }

    const record = sourceArchiveRecords.find(
      (candidate) =>
        (Boolean(action.record.archived_source_pdf_path) &&
          candidate.archived_source_pdf_path === action.record.archived_source_pdf_path) ||
        (Boolean(action.record.archived_source_pdf_url) &&
          candidate.archived_source_pdf_url === action.record.archived_source_pdf_url),
    )
    return record?.archived_source_pdf_url
      ? { key: record.source_id, url: record.archived_source_pdf_url, mode: 'sources' as const }
      : null
  }

  function showMissingArchiveReference() {
    setArchiveReferenceAction((currentAction) =>
      currentAction
        ? {
            ...currentAction,
            error: "Scusami, il file non è stato trovato nell'archivio.",
          }
        : currentAction,
    )
  }

  function openArchiveReferenceWindow() {
    if (!archiveReferenceAction) {
      return
    }
    const reference = resolveArchiveReference(archiveReferenceAction)
    if (!reference) {
      showMissingArchiveReference()
      return
    }

    openArchivedPdfWindow(
      reference.url,
      `${archiveReferenceAction.kind}-${reference.key}`,
    )
    setArchiveReferenceAction(null)
  }

  function goToArchiveReference() {
    if (!archiveReferenceAction) {
      return
    }
    navigateToArchiveReference(archiveReferenceAction)
  }

  function navigateToArchiveReference(action: ArchiveReferenceAction) {
    const reference = resolveArchiveReference(action)
    if (!reference) {
      setArchiveReferenceAction({
        ...action,
        error: "Il file non è stato trovato nell'archivio.",
      })
      return
    }

    setInvoiceArchiveSearch('')
    setInvoiceArchiveFilters({
      dateFrom: '',
      dateTo: '',
    })
    setPdfArchiveViewMode(reference.mode)
    setSelectedArchiveKeys(new Set([reference.key]))
    setFocusedArchiveKey(reference.key)
    setInvoiceArchiveError(null)
    setAppView('archive')
    setArchiveReferenceAction(null)
  }

  function openInvoiceRegistry() {
    setIsArchiveMenuOpen(false)
    setInvoiceRegistryViewMode('saved')
    setIsInvoiceRegistryEditMode(false)
    setSelectedArchiveKeys(new Set())
    setAppView('invoiceRegistry')
    setInvoiceArchiveError(null)
    loadInvoiceArchive()
  }

  function openProcessingJobsPage() {
    setIsArchiveMenuOpen(false)
    setProcessingViewMode('active')
    setAppView('processing')
    pollProcessingJobs()
  }

  function toggleProcessingHistorySelection(jobId: string) {
    setSelectedProcessingHistoryIds((current) => {
      const next = new Set(current)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  function toggleAllProcessingHistory() {
    const ids = processingHistoryJobs.map((job) => job.job_id)
    setSelectedProcessingHistoryIds((current) =>
      ids.length > 0 && ids.every((id) => current.has(id))
        ? new Set()
        : new Set(ids),
    )
  }

  function deleteProcessingHistory(deleteAll = false) {
    fetch(`${registryApiBaseUrl}/processing/history/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        all: deleteAll,
        job_ids: deleteAll ? [] : Array.from(selectedProcessingHistoryIds),
      }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) throw new Error(payload?.error ?? `HTTP ${response.status}`)
      })
      .then(() => {
        setSelectedProcessingHistoryIds(new Set())
        setIsProcessingHistoryDeleteConfirmOpen(false)
        pollProcessingJobs()
      })
      .catch((error) => {
        setIsProcessingHistoryDeleteConfirmOpen(false)
        setPdfImportState({
          kind: 'error',
          message: `Cronologia non eliminata. ${userErrorMessage(
            error,
            'Non riesco a eliminare le righe selezionate.',
          )}`,
        })
      })
  }

  function loadInvoiceArchive() {
    return Promise.all([
      fetch(`${registryApiBaseUrl}/invoices/registry`, { cache: 'no-store' }),
      fetch(`${registryApiBaseUrl}/archive/sources`, { cache: 'no-store' }),
    ])
      .then(async ([invoiceResponse, sourceResponse]) => {
        const invoicePayload = await invoiceResponse.json().catch(() => null)
        const sourcePayload = await sourceResponse.json().catch(() => null)
        if (!invoiceResponse.ok) {
          throw new Error(invoicePayload?.error ?? `HTTP ${invoiceResponse.status}`)
        }
        if (!sourceResponse.ok) {
          throw new Error(sourcePayload?.error ?? `HTTP ${sourceResponse.status}`)
        }
        return {
          invoices: invoicePayload as InvoiceArchiveRegistryPayload,
          sources: sourcePayload as SourceArchivePayload,
        }
      })
      .then(({ invoices: payload, sources }) => {
        const records = payload.registry.records ?? []
        setInvoiceArchiveRecords(records)
        setSourceArchiveRecords(sources.records ?? [])
        if (!isInvoiceRegistryEditMode) {
          setInvoiceRegistryDraftRecords(
            records.filter((record) => record.status === 'saved'),
          )
        }
        setInvoiceArchiveError(null)
      })
      .catch((error) => {
        setInvoiceArchiveRecords([])
        setInvoiceArchiveError(
          `Archivio non disponibile. ${userErrorMessage(
            error,
            'Non riesco a leggere le fatture salvate.',
          )}`,
        )
      })
  }

  function toggleArchiveSelection(key: string) {
    if (!key) {
      return
    }
    setFocusedArchiveKey(null)
    setSelectedArchiveKeys((currentKeys) => {
      const nextKeys = new Set(currentKeys)
      if (nextKeys.has(key)) {
        nextKeys.delete(key)
      } else {
        nextKeys.add(key)
      }
      return nextKeys
    })
  }

  function toggleAllArchiveSelections(keys: string[]) {
    const validKeys = keys.filter(Boolean)
    if (validKeys.length === 0) {
      return
    }
    setFocusedArchiveKey(null)

    setSelectedArchiveKeys((currentKeys) => {
      const nextKeys = new Set(currentKeys)
      const areAllSelected = validKeys.every((key) => currentKeys.has(key))
      for (const key of validKeys) {
        if (areAllSelected) {
          nextKeys.delete(key)
        } else {
          nextKeys.add(key)
        }
      }
      return nextKeys
    })
  }

  function requestDeleteSelectedArchives(scope: 'invoice' | 'source' | 'registry') {
    if (selectedArchiveKeys.size === 0) {
      return
    }

    setArchiveDeleteTarget({
      scope,
      recordIds: scope !== 'source' ? Array.from(selectedArchiveKeys) : undefined,
      archivedSourcePdfPaths: undefined,
      title: 'Elimina elementi selezionati',
      message:
        scope === 'source'
          ? `Vuoi eliminare ${selectedArchiveKeys.size} file origine selezionati dall’archivio? I PDF fattura e il registro non verranno modificati.`
          : scope === 'registry'
            ? `Vuoi eliminare ${selectedArchiveKeys.size} righe selezionate dal registro? I PDF fattura e i file origine non verranno modificati.`
            : `Vuoi eliminare ${selectedArchiveKeys.size} PDF fattura selezionati dall’archivio? Il registro e i file origine non verranno modificati.`,
    })
  }

  function confirmDeleteArchive() {
    const target = archiveDeleteTarget
    if (!target) {
      return
    }

    setArchiveDeleteTarget(null)
    setInvoiceArchiveError('Eliminazione archivio in corso...')
    fetch(`${registryApiBaseUrl}/archive/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: target.scope,
        record_id: target.recordId,
        record_ids: target.recordIds,
        archived_source_pdf_path: target.archivedSourcePdfPath,
        archived_source_pdf_paths: target.archivedSourcePdfPaths,
        source_ids: target.scope === 'source' ? Array.from(selectedArchiveKeys) : undefined,
      }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload
      })
      .then(() => {
        setSelectedArchiveKeys(new Set())
        return loadInvoiceArchive()
      })
      .catch((error) => {
        setInvoiceArchiveError(
          `Archivio non eliminato. ${userErrorMessage(
            error,
            'Non riesco a eliminare le copie interne dell’archivio.',
          )}`,
        )
      })
  }

  function startInvoiceRegistryEdit() {
    setInvoiceRegistryDraftRecords(
      invoiceArchiveRecords.filter((record) => record.status === 'saved'),
    )
    setInvoiceRegistrySaveStatus(null)
    setIsInvoiceRegistryEditMode(true)
  }

  function cancelInvoiceRegistryEdit() {
    setInvoiceRegistryDraftRecords(
      invoiceArchiveRecords.filter((record) => record.status === 'saved'),
    )
    setInvoiceRegistrySaveStatus(null)
    setIsInvoiceRegistryEditMode(false)
  }

  function updateInvoiceRegistryDraftField(
    recordId: string,
    fieldKind: InvoiceRegistryEditableFieldKind,
    nextValue: string,
  ) {
    setInvoiceRegistryDraftRecords((currentRecords) =>
      invoiceRegistryRecordsWithUpdatedField(
        currentRecords,
        recordId,
        fieldKind,
        nextValue,
      ),
    )
  }

  function saveInvoiceRegistryDraft() {
    const validation = buildInvoiceRegistrySaveValidation(invoiceRegistryDraftRecords)
    if (validation) {
      setPendingInvoiceRegistrySaveValidation(validation)
      return
    }

    persistInvoiceRegistryDraft()
  }

  function persistInvoiceRegistryDraft() {
    setInvoiceRegistrySaveStatus('Salvataggio registro in corso...')
    fetch(`${registryApiBaseUrl}/invoices/registry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records: invoiceRegistryDraftRecords }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as InvoiceArchiveRegistryPayload
      })
      .then((payload) => {
        const records = payload.registry.records ?? []
        setInvoiceArchiveRecords(records)
        setInvoiceRegistryDraftRecords(
          records.filter((record) => record.status === 'saved'),
        )
        setIsInvoiceRegistryEditMode(false)
        setInvoiceRegistrySaveStatus('Registro fatture salvato.')
        window.setTimeout(() => {
          setInvoiceRegistrySaveStatus(null)
        }, 1400)
      })
      .catch((error) => {
        setInvoiceRegistrySaveStatus(
          `Registro non salvato. ${userErrorMessage(
            error,
            'Non riesco a salvare le modifiche al registro fatture.',
          )}`,
        )
      })
  }

  function continueInvoiceRegistrySaveAfterValidation() {
    if (
      pendingInvoiceRegistrySaveValidation &&
      validationHasBlockingIssue(pendingInvoiceRegistrySaveValidation)
    ) {
      return
    }
    setPendingInvoiceRegistrySaveValidation(null)
    persistInvoiceRegistryDraft()
  }

  function downloadInvoiceArchiveExcel() {
    const link = document.createElement('a')
    link.href = `${registryApiBaseUrl}/invoices/export.xlsx`
    link.download = 'registro_fatture.xlsx'
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  function loadRegistryRecords() {
    return fetch(`${registryApiBaseUrl}/registry/records`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }

        return payload
      })
      .then((payload: RegistryRecordsPayload) => {
        if (!payload || !Array.isArray(payload.records)) {
          throw new Error("risposta anagrafica non valida")
        }

        setRegistryPreviewRecords(payload.records ?? [])
        setRegistryDraftRecords(payload.records ?? [])
        setIsRegistryEditMode(false)
        setRegistrySaveStatus(null)
      })
      .catch((error) => {
        setRegistryPreviewRecords([])
        setRegistryPreviewError(
          `Anteprima non disponibile. ${userErrorMessage(
            error,
            "Non riesco a leggere l'anagrafica salvata.",
          )}`,
        )
      })
  }

  function closeRegistryPreview() {
    setAppView('review')
    setIsRegistryEditMode(false)
  }

  function closeInvoiceArchive() {
    setAppView('review')
    setInvoiceArchiveError(null)
  }

  function deleteRegistryFile() {
    setIsRegistryDeleteConfirmOpen(false)
    setRegistrySaveStatus('Cancellazione anagrafica...')
    fetch(`${registryApiBaseUrl}/registry`, {
      method: 'DELETE',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
      })
      .then(() => {
        setRegistryStatus({ ok: true, has_file: false })
        setRegistryStatusError(null)
        setRegistryImportState(null)
        setRegistryPreviewRecords([])
        setRegistryDraftRecords([])
        setRegistryCleaningReport(null)
        setRegistryCleaningReportFileUrl(null)
        setIsRegistryEditMode(false)
        setRegistrySaveStatus('Anagrafica cancellata')
      })
      .catch((error) => {
        setRegistrySaveStatus(
          `Anagrafica non cancellata. ${userErrorMessage(
            error,
            "Non riesco a cancellare l'anagrafica.",
          )}`,
        )
      })
  }

  function enterRegistryEditMode() {
    setRegistryDraftRecords(registryPreviewRecords.map((record) => ({ ...record })))
    setRegistrySaveStatus(null)
    setIsRegistryEditMode(true)
  }

  function addBlankRegistryRecord() {
    const record = newRegistryDraftRecord()
    setRegistryDraftRecords((currentRecords) => [record, ...currentRecords])
    setRegistrySaveStatus(null)
    setIsRegistryEditMode(true)
    setHighlightedRegistryRecordKey(registryRecordKey(record))
    requestAnimationFrame(() => {
      registryTableViewportRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    })
  }

  function cancelRegistryEditMode() {
    setRegistryDraftRecords(registryPreviewRecords.map((record) => ({ ...record })))
    setRegistrySaveStatus(null)
    setIsRegistryEditMode(false)
  }

  const updateRegistryDraftRecord = useCallback((
    recordIndex: number,
    fieldName: RegistryEditableFieldName,
    nextValue: string,
  ) => {
    setRegistryDraftRecords((currentRecords) =>
      currentRecords.map((record, currentIndex) =>
        currentIndex === recordIndex
          ? {
              ...record,
              [fieldName]: nextValue,
            }
          : record,
      ),
    )
  }, [])

  const deleteRegistryDraftRecord = useCallback((recordIndex: number) => {
    setRegistryDraftRecords((currentRecords) =>
      currentRecords.filter((_, currentIndex) => currentIndex !== recordIndex),
    )
    setRegistrySaveStatus(null)
  }, [])

  const requestDeleteRegistryDraftRecord = useCallback((recordIndex: number) => {
    setRegistryDraftRecordIndexToDelete(recordIndex)
  }, [])

  function confirmDeleteRegistryDraftRecord() {
    if (registryDraftRecordIndexToDelete === null) {
      return
    }
    const recordIndex = registryDraftRecordIndexToDelete
    setRegistryDraftRecordIndexToDelete(null)
    deleteRegistryDraftRecord(recordIndex)
  }

  const selectRegistrySearchSuggestion = useCallback((suggestion: RegistrySearchSuggestion) => {
    const record = registryTableRecords[suggestion.recordIndex]
    if (!record) {
      return
    }

    const recordKey = registryRecordKey(record)
    setHighlightedRegistryRecordKey(recordKey)
    requestAnimationFrame(() => {
      const viewport = registryTableViewportRef.current
      if (!viewport) {
        return
      }

      viewport.scrollTo({
        top: Math.max(
          0,
          suggestion.recordIndex * REGISTRY_TABLE_ROW_HEIGHT -
            viewport.clientHeight / 2 +
            REGISTRY_TABLE_ROW_HEIGHT / 2,
        ),
        behavior: 'smooth',
      })
    })
  }, [registryTableRecords])

  function openRegistryRecordFromField(field: InvoiceField) {
    if (!field.registry_id || field.registry_source_row === null || field.registry_source_row === undefined) {
      return
    }

    const recordIndex = registryPreviewRecords.findIndex(
      (record) =>
        record.registry_id === field.registry_id &&
        record.source_row === field.registry_source_row,
    )

    if (recordIndex < 0) {
      return
    }

    const record = registryPreviewRecords[recordIndex]
    setActiveSuggestionFieldKind(null)
    setHighlightedSuggestionIndex(-1)
    setOriginalScanFieldKind(null)
    setIsRegistryEditMode(false)
    setHighlightedRegistryRecordKey(registryRecordKey(record))
    setAppView('registry')

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const viewport = registryTableViewportRef.current
        if (!viewport) {
          return
        }

        viewport.scrollTo({
          top: Math.max(
            0,
            recordIndex * REGISTRY_TABLE_ROW_HEIGHT -
              viewport.clientHeight / 2 +
              REGISTRY_TABLE_ROW_HEIGHT / 2,
          ),
          behavior: 'smooth',
        })
      })
    })
  }

  function openNewRegistryRecord(record: RegistryPreviewRecord) {
    setRegistryDraftRecords((currentRecords) => [record, ...currentRecords])
    setRegistrySaveStatus(null)
    setIsRegistryEditMode(true)
    setHighlightedRegistryRecordKey(registryRecordKey(record))
    setActiveSuggestionFieldKind(null)
    setHighlightedSuggestionIndex(-1)
    setOriginalScanFieldKind(null)
    setAppView('registry')

    requestAnimationFrame(() => {
      registryTableViewportRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    })
  }

  function buildRegistryRecordFromInvoiceField(field: InvoiceField) {
    return buildRegistryRecordFromInvoiceFields(field, displayedFields)
  }

  function requestAddRegistryRecordFromField(field: InvoiceField) {
    const record = buildRegistryRecordFromInvoiceField(field)
    if (!record) {
      return
    }

    setActiveSuggestionFieldKind(null)
    setHighlightedSuggestionIndex(-1)
    setPendingRegistryRecord({
      sourceFieldKind: field.kind,
      record,
    })
  }

  function confirmAddRegistryRecordFromField() {
    if (!pendingRegistryRecord) {
      return
    }

    openNewRegistryRecord(pendingRegistryRecord.record)
    setPendingRegistryRecord(null)
  }

  const clearRegistrySearchHighlight = useCallback(() => {
    setHighlightedRegistryRecordKey(null)
  }, [])

  function saveRegistryEdits() {
    if (isRegistrySaving) {
      return
    }

    setIsRegistrySaving(true)
    setRegistrySaveStatus('Salvataggio anagrafica...')
    fetch(`${registryApiBaseUrl}/registry/records`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records: registryDraftRecords }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }

        return payload
      })
      .then((payload: RegistryRecordsPayload) => {
        if (!payload || !Array.isArray(payload.records)) {
          throw new Error("risposta anagrafica non valida")
        }

        setRegistryPreviewRecords(payload.records)
        setRegistryDraftRecords(payload.records)
        setRegistryStatus((currentStatus) =>
          currentStatus
            ? { ...currentStatus, record_count: payload.record_count }
            : currentStatus,
        )
        applyRegistryMatchesToCurrentInvoices(payload.records)
        setIsRegistryEditMode(false)
        setRegistrySaveStatus('Anagrafica salvata')
      })
      .catch((error) => {
        setRegistrySaveStatus(
          `Anagrafica non salvata. ${userErrorMessage(
            error,
            "Non riesco a salvare le modifiche all'anagrafica.",
          )}`,
        )
      })
      .finally(() => {
        setIsRegistrySaving(false)
      })
  }

  function previewRegistryCleaning() {
    setIsRegistryCleaningRunning(true)
    setRegistrySaveStatus('Analisi pulizia anagrafica...')
    fetch(`${registryApiBaseUrl}/registry/clean-duplicates/preview`, {
      method: 'POST',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }

        return payload
      })
      .then((payload: RegistryRecordsPayload) => {
        setRegistryCleaningReport(payload.cleaning_report ?? null)
        setRegistryCleaningReportMode('preview')
        setRegistryCleaningReportFileUrl(payload.cleaning_report_file_url ?? null)
        setRegistrySaveStatus('Report pulizia pronto')
      })
      .catch((error) => {
        setRegistrySaveStatus(
          `Report pulizia non disponibile. ${userErrorMessage(
            error,
            'Non riesco a preparare il report di pulizia.',
          )}`,
        )
      })
      .finally(() => {
        setIsRegistryCleaningRunning(false)
      })
  }

  function runRegistryCleaning(selectedMergeGroupKeys: string[]) {
    setIsRegistryCleaningRunning(true)
    setRegistrySaveStatus('Pulizia anagrafica in corso...')
    fetch(`${registryApiBaseUrl}/registry/clean-duplicates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_merge_group_keys: selectedMergeGroupKeys }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }

        return payload
      })
      .then((payload: RegistryRecordsPayload) => {
        if (!payload || !Array.isArray(payload.records)) {
          throw new Error("risposta anagrafica non valida")
        }

        setRegistryPreviewRecords(payload.records)
        setRegistryDraftRecords(payload.records)
        setRegistryStatus((currentStatus) =>
          currentStatus
            ? { ...currentStatus, record_count: payload.record_count }
            : currentStatus,
        )
        applyRegistryMatchesToCurrentInvoices(payload.records)
        setIsRegistryEditMode(false)
        setRegistryCleaningReport(payload.cleaning_report ?? null)
        setRegistryCleaningReportMode('applied')
        setRegistryCleaningReportFileUrl(payload.cleaning_report_file_url ?? null)
        setRegistrySaveStatus('Pulizia anagrafica completata')
      })
      .catch((error) => {
        setRegistrySaveStatus(
          `Pulizia anagrafica non completata. ${userErrorMessage(
            error,
            "Non riesco a completare la pulizia dell'anagrafica.",
          )}`,
        )
      })
      .finally(() => {
        setIsRegistryCleaningRunning(false)
      })
  }

  function downloadRegistryCleaningReport() {
    if (!registryCleaningReportFileUrl) {
      return
    }

    const link = document.createElement('a')
    link.href = `${registryApiBaseUrl}${registryCleaningReportFileUrl}`
    link.download = 'report_pulizia_anagrafica.xlsx'
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  function applyRegistryMatchesToCurrentInvoices(records: RegistryPreviewRecord[]) {
    setInvoices((currentInvoices) => {
      if (currentInvoices.length === 0) {
        return currentInvoices
      }

      const registryMatchedInvoices = applyRegistryMatchesToInvoices(
        currentInvoices,
        records,
      )
      setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
      return registryMatchedInvoices.invoices
    })
  }

  function downloadRegistryFile() {
    if (!registryStatus?.export_file_url) {
      return
    }

    const link = document.createElement('a')
    link.href = `${registryApiBaseUrl}${registryStatus.export_file_url}`
    link.download = registryStatus.export_file_name ?? 'anagrafica_estrattore_scan.xlsx'
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  function loadRegistryStatus() {
    setRegistryStatusError(null)
    fetch(`${registryApiBaseUrl}/registry/status`, { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 404) {
            throw new Error(
              "backend anagrafica non aggiornato: riavvia l'app con scripts/run_app.py",
            )
          }

          const payload = await response.json().catch(() => null)
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }

        return response.json()
      })
      .then((payload: RegistryStatus) => {
        setRegistryStatus(payload)
      })
      .catch((error) => {
        setRegistryStatus(null)
        setRegistryStatusError(
          `Stato anagrafica non disponibile. ${userErrorMessage(
            error,
            "Non riesco a controllare l'anagrafica caricata.",
          )}`,
        )
      })
  }

  function importRegistryFile(file: File | undefined) {
    if (!file) {
      return
    }

    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setRegistryImportState({
        kind: 'error',
        message: "L'anagrafica non e nel formato aspettato.",
      })
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    setRegistryImportState({
      kind: 'loading',
      message: 'Caricamento anagrafica in corso...',
    })

    fetch(`${registryApiBaseUrl}/registry/import`, {
      method: 'POST',
      body: formData,
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }

        return payload
      })
      .then((payload) => {
        const nextStatus = {
          ok: true,
          has_file: true,
          file_name: 'elenco_anagrafiche.xlsx',
          file_url: '/registry/file',
          json_file_name: 'anagrafica_clienti_modificata.json',
          json_file_url: '/registry/cache.json',
          export_file_name: 'anagrafica_estrattore_scan.xlsx',
          export_file_url: '/registry/export.xlsx',
          record_count: payload.record_count,
        }
        setRegistryImportState({
          kind: 'success',
          message: registryStatus?.has_file
            ? `Anagrafica sostituita: ${payload.record_count} record`
            : `Anagrafica caricata: ${payload.record_count} record`,
        })
        setRegistryStatus(nextStatus)
        setRegistryStatusError(null)
        loadRegistryRecords()
        window.setTimeout(() => {
          setRegistryImportState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1400)
      })
      .catch((error) => {
        setRegistryImportState({
          kind: 'error',
          message: `Anagrafica non caricata. ${userErrorMessage(
            error,
            "Il file non sembra un'anagrafica valida.",
          )}`,
        })
      })
      .finally(() => {
        if (registryFileInputRef.current) {
          registryFileInputRef.current.value = ''
        }
      })
  }

  function loadDocumentEntries(
    documentEntries: ViewerDocumentIndexEntry[],
    preferredManifestInvoiceId?: string,
  ) {
    if (documentEntries.length === 0) {
      setInvoices([])
      setSelectedInvoiceId(null)
      setManifestSourceFile(null)
      setLoadedManifests([])
      setManifestError('Nessun documento nel viewer_index.json')
      return Promise.resolve(false)
    }

    return Promise.all(
      documentEntries.map((documentEntry) =>
        fetch(`${documentEntry.base_path}/viewer_manifest.json?v=${manifestCacheKey}`, {
          cache: 'no-store',
        })
          .then((response) => {
            if (!response.ok) {
              throw new Error(`${documentEntry.id}: HTTP ${response.status}`)
            }

            return response.json()
          })
          .then((manifest: ViewerManifest) => ({ manifest, documentEntry })),
      ),
    )
      .then((loadedDocuments) => {
        const manifestInvoices = sortInvoicesForReview(
          loadedDocuments.flatMap(({ manifest, documentEntry }) =>
            getInvoicesFromManifest(manifest, documentEntry),
          ),
        )
        const registryMatchedInvoices = applyRegistryMatchesToInvoices(
          manifestInvoices,
          registryPreviewRecords,
        )
        const invoicesWithDrafts = applyReviewDraftsToInvoices(
          registryMatchedInvoices.invoices,
        )
        setLoadedManifests(loadedDocuments)
        setManifestSourceFile(
          loadedDocuments.length === 1
            ? loadedDocuments[0].manifest.source_file
            : `${loadedDocuments.length} documenti`,
        )
        setInvoices(invoicesWithDrafts)
        setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
        const preferredInvoice = invoicesWithDrafts.find(
          (invoice) => invoice.manifestInvoiceId === preferredManifestInvoiceId,
        )
        setSelectedInvoiceId(
          preferredInvoice?.invoiceId ?? invoicesWithDrafts[0]?.invoiceId ?? null,
        )
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setManifestError(null)
        return true
      })
      .catch((error) => {
        setInvoices([])
        setSelectedInvoiceId(null)
        setManifestSourceFile(null)
        setLoadedManifests([])
        setManifestError(
          userErrorMessage(error, 'Non riesco a leggere i dati di questa fattura.'),
        )
        return false
      })
  }

  function applyRegistryMatchesToInvoices(
    sourceInvoices: ViewerInvoice[],
    records: RegistryPreviewRecord[],
  ): { invoices: ViewerInvoice[]; conflicts: Record<string, RegistryFieldConflict> } {
    if (records.length === 0) {
      return { invoices: sourceInvoices, conflicts: {} }
    }

    const conflicts: Record<string, RegistryFieldConflict> = {}
    const invoicesWithRegistryMatches = sourceInvoices.map((invoice) => {
      let nextFields = invoice.fields

      for (const sourceFieldKind of ['codice_fiscale', 'codice_fiscale_emittente']) {
        const sourceField = nextFields.find((field) => field.kind === sourceFieldKind)
        const fiscalCode = sourceField?.value.trim() ?? ''
        if (!fiscalCode) {
          continue
        }

        const targetFieldKind = registryConflictTargetFieldKind(sourceFieldKind)
        if (!targetFieldKind) {
          continue
        }

        const matchingRecords = registryRecordsForFiscalCode(
          records,
          sourceFieldKind,
          fiscalCode,
        )
        if (matchingRecords.length === 0) {
          continue
        }

        if (
          matchingRecords.length > 1 &&
          registryRecordsHaveDifferentFill(matchingRecords)
        ) {
          conflicts[registryConflictKey(invoice.invoiceId, targetFieldKind)] = {
            fieldKind: targetFieldKind,
            sourceFieldKind,
            fiscalCode,
            options: matchingRecords,
          }
          nextFields = nextFields.map((field) =>
            field.kind === targetFieldKind
              ? { ...field, value: '', role: 'registry_conflict' }
              : field,
          )
          continue
        }

        const selectedRecord = matchingRecords[0]
        const fieldGroup = registryFieldGroupForFiscalField(sourceFieldKind)
        nextFields = nextFields.map((field) => {
          if (!fieldGroup.includes(field.kind)) {
            return field
          }

          const nextValue =
            field.kind === sourceFieldKind
              ? registryFieldValueForInvoiceKind(selectedRecord, sourceFieldKind)
              : registryFieldValueForInvoiceKind(selectedRecord, field.kind)

          return fieldWithRegistryValue(field, nextValue, selectedRecord)
        })
      }

      return { ...invoice, fields: nextFields }
    })

    return { invoices: invoicesWithRegistryMatches, conflicts }
  }

  function updateFieldValue(
    fieldKind: string,
    nextValue: string,
    interaction: 'manual_input' | 'formatting_only' | 'suggestion_selection' =
      'manual_input',
  ) {
    setActiveSuggestionFieldKind(fieldKind)
    setHighlightedSuggestionIndex(-1)
    markSelectedInvoiceDraftDirty()
    setInvoices((currentInvoices) =>
      currentInvoices.map((invoice) => {
        if (invoice.invoiceId !== selectedInvoiceId) {
          return invoice
        }

        return {
          ...invoice,
          fields: invoice.fields.map((field) => {
            if (field.kind !== fieldKind) {
              return field
            }
            const effectiveInteraction =
              interaction === 'formatting_only' && field.review_interaction
                ? field.review_interaction
                : !nextValue && field.value
                  ? 'cleared'
                  : interaction
            return {
              ...field,
              value: nextValue,
              review_interaction: effectiveInteraction,
              review_value_origin:
                effectiveInteraction === 'suggestion_selection'
                  ? 'suggestion'
                  : effectiveInteraction === 'formatting_only'
                    ? field.review_value_origin
                    : 'manual_input',
              review_action_id:
                field.review_action_id &&
                effectiveInteraction === field.review_interaction
                  ? field.review_action_id
                  : newReviewActionId(),
              review_source_field_kind: fieldKind,
            }
          }),
        }
      }),
    )
  }

  function normalizeInvoiceFieldValue(fieldKind: string, value: string) {
    const normalizedValue = normalizeInvoiceFieldValueForInput(fieldKind, value)
    if (normalizedValue !== value) {
      updateFieldValue(fieldKind, normalizedValue, 'formatting_only')
    }
    return normalizedValue
  }

  function acceptFieldSuggestion(fieldKind: string, suggestion: FieldSuggestion) {
    if (suggestion.registryRecord) {
      applyRegistryRecordToSelectedInvoice(fieldKind, suggestion.registryRecord)
      setActiveSuggestionFieldKind(null)
      setHighlightedSuggestionIndex(-1)
      return
    }
    const matchingRecords = registryRecordsForExactFieldValue(
      registryPreviewRecords,
      fieldKind,
      suggestion.value,
    )
    if (matchingRecords.length === 1) {
      applyRegistryRecordToSelectedInvoice(fieldKind, matchingRecords[0])
    } else if (fieldKind === 'codice_fiscale' || fieldKind === 'codice_fiscale_emittente') {
      applyRegistryFiscalCodeMatch(fieldKind, suggestion.value)
    } else if (relatedFieldGroups.some((group) => group.includes(fieldKind))) {
      completeRelatedFieldValues(fieldKind, suggestion)
    } else {
      updateFieldValue(fieldKind, suggestion.value, 'suggestion_selection')
    }
    setActiveSuggestionFieldKind(null)
    setHighlightedSuggestionIndex(-1)
  }

  function applyExactRegistryFieldMatch(fieldKind: string, value: string) {
    const matchingRecords = registryRecordsForExactFieldValue(
      registryPreviewRecords,
      fieldKind,
      value,
    )
    if (matchingRecords.length !== 1) {
      return false
    }

    applyRegistryRecordToSelectedInvoice(fieldKind, matchingRecords[0])
    return true
  }

  function applyRegistryFiscalCodeMatch(fieldKind: string, fiscalCode: string) {
    if (!selectedInvoiceId) {
      updateFieldValue(fieldKind, fiscalCode)
      return
    }

    const targetFieldKind = registryConflictTargetFieldKind(fieldKind)
    const fieldGroup = registryFieldGroupForFiscalField(fieldKind)
    const matchingRecords = registryRecordsForFiscalCode(
      registryPreviewRecords,
      fieldKind,
      fiscalCode,
    )
    const conflictKey = targetFieldKind
      ? registryConflictKey(selectedInvoiceId, targetFieldKind)
      : null

    if (
      targetFieldKind &&
      matchingRecords.length > 1 &&
      registryRecordsHaveDifferentFill(matchingRecords)
    ) {
      markSelectedInvoiceDraftDirty()
      setRegistryFieldConflicts((currentConflicts) => ({
        ...currentConflicts,
        [conflictKey as string]: {
          fieldKind: targetFieldKind,
          sourceFieldKind: fieldKind,
          fiscalCode,
          options: matchingRecords,
        },
      }))
      setInvoices((currentInvoices) =>
        currentInvoices.map((invoice) =>
          invoice.invoiceId !== selectedInvoiceId
            ? invoice
            : {
                ...invoice,
                fields: invoice.fields.map((field) => {
                  if (field.kind === fieldKind) {
                    return { ...field, value: fiscalCode }
                  }
                  if (field.kind === targetFieldKind) {
                    return {
                      ...field,
                      value: '',
                      role: 'registry_conflict',
                    }
                  }
                  if (fieldGroup.includes(field.kind) && field.kind !== fieldKind) {
                    return field
                  }
                  return field
                }),
              },
        ),
      )
      return
    }

    if (conflictKey) {
      setRegistryFieldConflicts((currentConflicts) => {
        const nextConflicts = { ...currentConflicts }
        delete nextConflicts[conflictKey]
        return nextConflicts
      })
    }

    const selectedRecord = matchingRecords[0]
    if (!selectedRecord) {
      updateFieldValue(fieldKind, fiscalCode)
      return
    }

    applyRegistryRecordToSelectedInvoice(fieldKind, selectedRecord)
  }

  function applyRegistryRecordToSelectedInvoice(
    sourceFieldKind: string,
    record: RegistryPreviewRecord,
  ) {
    if (!selectedInvoiceId) {
      return
    }

    const fieldGroup = registryFieldGroupForFiscalField(sourceFieldKind)
    const reviewActionId = newReviewActionId()
    const sourceValue = registryFieldValueForInvoiceKind(record, sourceFieldKind)
    const targetFieldKind = registryConflictTargetFieldKind(sourceFieldKind)
    const conflictKey = targetFieldKind
      ? registryConflictKey(selectedInvoiceId, targetFieldKind)
      : null

    if (conflictKey) {
      setRegistryFieldConflicts((currentConflicts) => {
        const nextConflicts = { ...currentConflicts }
        delete nextConflicts[conflictKey]
        return nextConflicts
      })
    }

    setReviewedOriginalScanFields((currentFields) => {
      const nextFields = { ...currentFields }
      for (const fieldKind of fieldGroup) {
        delete nextFields[registryConflictKey(selectedInvoiceId, fieldKind)]
      }
      return nextFields
    })

    markSelectedInvoiceDraftDirty()
    setInvoices((currentInvoices) =>
      currentInvoices.map((invoice) =>
        invoice.invoiceId !== selectedInvoiceId
          ? invoice
          : {
              ...invoice,
              fields: invoice.fields.map((field) => {
                if (!fieldGroup.includes(field.kind)) {
                  return field
                }

                const nextValue =
                  field.kind === sourceFieldKind
                    ? sourceValue
                    : registryFieldValueForInvoiceKind(record, field.kind)

                return fieldWithRegistryValue(field, nextValue, record, {
                  interaction:
                    field.kind === sourceFieldKind
                      ? 'registry_selection'
                      : 'dependent_registry_fill',
                  actionId: reviewActionId,
                  sourceFieldKind,
                })
              }),
            },
      ),
    )
  }

  function completeRelatedFieldValues(fieldKind: string, suggestion: FieldSuggestion) {
    const relatedGroup = relatedFieldGroups.find((group) => group.includes(fieldKind))
    if (!relatedGroup) {
      return
    }
    const reviewActionId = newReviewActionId()

    markSelectedInvoiceDraftDirty()
    setInvoices((currentInvoices) =>
      currentInvoices.map((invoice) => {
        if (invoice.invoiceId !== selectedInvoiceId) {
          return invoice
        }

        return {
          ...invoice,
          fields: invoice.fields.map((field) => {
            if (field.kind === fieldKind) {
              return {
                ...field,
                value: suggestion.value,
                review_interaction: 'suggestion_selection',
                review_value_origin: 'suggestion',
                review_action_id: reviewActionId,
                review_source_field_kind: fieldKind,
              }
            }

            if (!relatedGroup.includes(field.kind)) {
              return field
            }

            const relatedValue = suggestion.relatedFields[field.kind]
            if (!relatedValue || field.value.trim()) {
              return field
            }

            return {
              ...field,
              value: relatedValue,
              role: 'suggestion_enriched',
              review_interaction: 'dependent_suggestion_fill',
              review_value_origin: 'dependent_suggestion',
              review_action_id: reviewActionId,
              review_source_field_kind: fieldKind,
            }
          }),
        }
      }),
    )
  }

  function resetFieldValues() {
    if (!selectedInvoiceId || loadedManifests.length === 0) {
      return
    }

    const manifestInvoices = sortInvoicesForReview(
      loadedManifests.flatMap(({ manifest, documentEntry }) =>
        getInvoicesFromManifest(manifest, documentEntry),
      ),
    )
    const registryMatchedInvoices = applyRegistryMatchesToInvoices(
      manifestInvoices,
      registryPreviewRecords,
    )
    const restoredInvoice = registryMatchedInvoices.invoices.find(
      (invoice) => invoice.invoiceId === selectedInvoiceId,
    )
    const selectedInvoice = invoices.find(
      (invoice) => invoice.invoiceId === selectedInvoiceId,
    )
    if (!restoredInvoice || !selectedInvoice) {
      return
    }

    const draftId = reviewDraftIdForInvoice(selectedInvoice)
    flushReviewDraftSaves()
      .then(() => deleteReviewDrafts(draftId ? [draftId] : []))
      .then(() => {
        setInvoices((currentInvoices) =>
          currentInvoices.map((invoice) =>
            invoice.invoiceId === selectedInvoiceId ? restoredInvoice : invoice,
          ),
        )
        setRegistryFieldConflicts((currentConflicts) => {
          const conflictPrefix = `${selectedInvoiceId}:`
          const nextConflicts = Object.fromEntries(
            Object.entries(currentConflicts).filter(
              ([conflictKey]) => !conflictKey.startsWith(conflictPrefix),
            ),
          )
          for (const [conflictKey, conflict] of Object.entries(
            registryMatchedInvoices.conflicts,
          )) {
            if (conflictKey.startsWith(conflictPrefix)) {
              nextConflicts[conflictKey] = conflict
            }
          }
          return nextConflicts
        })
        setReviewedOriginalScanFields((currentFields) => {
          const fieldPrefix = `${selectedInvoiceId}:`
          return Object.fromEntries(
            Object.entries(currentFields).filter(
              ([fieldKey]) => !fieldKey.startsWith(fieldPrefix),
            ),
          )
        })
        setSelectedFieldKind(null)
        setOriginalScanFieldKind(null)
        setActiveSuggestionFieldKind(null)
      })
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Ripristino non completato. ${userErrorMessage(
            error,
            'Non riesco a eliminare la bozza salvata.',
          )}`,
        })
      })
  }

  function resetDocumentViewportScroll() {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        documentViewportRef.current?.scrollTo({ left: 0, top: 0 })
      })
    })
  }

  function resetDocumentView() {
    setZoomScale(BASE_PAGE_SCALE)
    resetDocumentViewportScroll()
  }

  function selectInvoice(invoiceId: string) {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur()
    }

    setSelectedInvoiceId(invoiceId)
    setSelectedFieldKind(null)
    setActiveSuggestionFieldKind(null)
    resetDocumentView()
  }

  function selectInvoiceByOffset(offset: number) {
    if (!selectedInvoiceId || invoices.length === 0) {
      return
    }

    const currentIndex = invoices.findIndex(
      (invoice) => invoice.invoiceId === selectedInvoiceId,
    )
    if (currentIndex < 0) {
      return
    }

    const nextIndex = currentIndex + offset
    if (nextIndex < 0 || nextIndex >= invoices.length) {
      return
    }

    selectInvoice(invoices[nextIndex].invoiceId)
  }

  const selectInvoiceByOffsetRef = useRef(selectInvoiceByOffset)
  selectInvoiceByOffsetRef.current = selectInvoiceByOffset

  useEffect(() => {
    invoiceFieldsPanelRef.current?.scrollTo({ top: 0, behavior: 'auto' })
  }, [selectedInvoiceId])

  function currentInvoiceBoundaryPageMoveState(
    direction: 'first_to_previous' | 'last_to_next',
  ) {
    if (!canPersistCurrentBatch) {
      return {
        enabled: false,
        reason: "Puoi riordinare le pagine solo su fatture caricate e processate dall'app.",
      }
    }
    if (!currentInvoice || currentInvoiceIndex < 0) {
      return { enabled: false, reason: 'Nessuna fattura selezionata.' }
    }
    const loadedDocument = loadedManifests.find(
      ({ documentEntry }) => documentEntry.id === currentInvoice.documentId,
    )
    const manifestInvoices = loadedDocument?.manifest.invoices ?? []
    const manifestInvoiceIndex = manifestInvoices.findIndex(
      (invoice) => invoice.invoice_id === currentInvoice.manifestInvoiceId,
    )
    const neighborIndex =
      direction === 'first_to_previous'
        ? manifestInvoiceIndex - 1
        : manifestInvoiceIndex + 1
    if (manifestInvoiceIndex < 0 || !manifestInvoices[neighborIndex]) {
      return {
        enabled: false,
        reason:
          direction === 'first_to_previous'
            ? 'Non esiste una fattura precedente.'
            : 'Non esiste una fattura successiva.',
      }
    }
    if (!currentInvoice.manifestInvoiceId) {
      return {
        enabled: false,
        reason: 'Questa revisione non contiene i riferimenti necessari per spostare pagine.',
      }
    }

    return { enabled: true, reason: '' }
  }

  function requestCurrentInvoiceBoundaryMove(
    direction: 'first_to_previous' | 'last_to_next',
  ) {
    if (!currentInvoice) {
      return
    }
    const pageCount = currentInvoice.pageNumbers?.length ?? 0
    if (pageCount < 1) {
      return
    }
    const movesToPrevious = direction === 'first_to_previous'
    const removesInvoice = pageCount === 1
    setPendingSegmentationAction({
      operation: 'move_boundary',
      direction,
      removesInvoice,
      title: removesInvoice ? 'Unisci fattura' : 'Sposta pagina',
      message: removesInvoice
        ? `${currentInvoice.title} contiene una sola pagina: verra unita alla fattura ${movesToPrevious ? 'precedente' : 'successiva'} e le fatture successive verranno rinumerate.`
        : `La pagina ${movesToPrevious ? 'iniziale' : 'finale'} verra spostata nella fattura ${movesToPrevious ? 'precedente' : 'successiva'}.`,
    })
  }

  function requestCurrentInvoiceSplit() {
    if (!currentInvoice) {
      return
    }
    const pageCount = currentInvoice.pageNumbers?.length ?? 0
    const afterPosition = Math.min(Math.max(splitAfterPosition, 1), pageCount - 1)
    if (pageCount < 2 || afterPosition < 1) {
      return
    }
    setPendingSegmentationAction({
      operation: 'split',
      afterPosition,
      title: 'Dividi fattura',
      message: `${currentInvoice.title} verra divisa in due fatture da ${afterPosition} e ${pageCount - afterPosition} pagine. Le fatture successive verranno rinumerate.`,
    })
  }

  function confirmSegmentationAction() {
    if (
      !pendingSegmentationAction ||
      !currentInvoice?.documentId ||
      !currentInvoice.manifestInvoiceId ||
      !selectedBatch?.base_path
    ) {
      return
    }

    const operation = pendingSegmentationAction.operation
    const segmentationInvoice = currentInvoice
    const requestBody =
      pendingSegmentationAction.operation === 'move_boundary'
        ? {
            operation: 'move_boundary',
            direction: pendingSegmentationAction.direction,
          }
        : {
            operation: 'split',
            after_position: pendingSegmentationAction.afterPosition,
          }
    setPendingSegmentationAction(null)
    setSegmentationProgressState({ operation, phase: 'saving' })
    setInvoiceReviewActionState(null)

    flushReviewDraftSaves()
      .then(() => {
        setSegmentationProgressState({ operation, phase: 'reorganizing' })
        return fetch(`${registryApiBaseUrl}/viewer/segmentation`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            review_batch_base_path: selectedBatch.base_path,
            document_id: segmentationInvoice.documentId,
            invoice_id: segmentationInvoice.manifestInvoiceId,
            ...requestBody,
          }),
        })
      })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload
      })
      .then((payload) => {
        const affectedDraftIds = (payload.affected_invoice_ids ?? [])
          .map((invoiceId: string) =>
            reviewDraftIdForRelatedInvoice(segmentationInvoice, invoiceId),
          )
          .filter((draftId: string | null): draftId is string => Boolean(draftId))
        removeReviewDraftsLocally(affectedDraftIds)
        setSegmentationProgressState({ operation, phase: 'reloading' })
        return loadProcessedReviewBatches(
          payload.selected_batch_id,
          payload.selected_invoice_id,
        )
      })
      .then(() => {
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setOriginalScanFieldKind(null)
        resetDocumentView()
        setInvoiceReviewActionState({
          kind: 'success',
          message: 'Segmentazione aggiornata.',
        })
        window.setTimeout(() => {
          setInvoiceReviewActionState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1400)
      })
      .catch((error) => {
        setInvoiceReviewActionState({
          kind: 'error',
          message: `Segmentazione non aggiornata. ${userErrorMessage(
            error,
            'Non riesco a modificare le pagine della fattura.',
          )}`,
        })
      })
      .finally(() => {
        setSegmentationProgressState(null)
      })
  }

  function setZoomScale(nextScale: number) {
    setPageScale(nextScale)
    setZoomInput(String(Math.round(nextScale * 100)))
  }

  function zoomOut() {
    setZoomScale(Math.max(0.35, Number((pageScale - 0.1).toFixed(2))))
  }

  function zoomIn() {
    setZoomScale(Math.min(1.5, Number((pageScale + 0.1).toFixed(2))))
  }

  function applyZoomInput() {
    const parsedValue = Number(zoomInput)
    if (Number.isNaN(parsedValue)) {
      setZoomInput(String(zoomPercent))
      return
    }

    const clampedValue = Math.min(150, Math.max(35, parsedValue))
    setZoomScale(Number((clampedValue / 100).toFixed(2)))
  }

  function scrollToFieldEvidence(evidence: FieldEvidence) {
    const viewport = documentViewportRef.current
    const page = currentInvoice?.pages.find(
      (invoicePage) => invoicePage.page === evidence.page,
    )

    if (!viewport || !page) {
      return
    }

    const pageElement = viewport.querySelector<HTMLElement>(
      `[data-page="${evidence.page}"]`,
    )

    if (!pageElement) {
      return
    }

    const viewportRect = viewport.getBoundingClientRect()
    const pageRect = pageElement.getBoundingClientRect()
    const bboxCenterRatioX = ((evidence.bbox.x1 + evidence.bbox.x2) / 2) / page.width
    const bboxCenterRatioY = ((evidence.bbox.y1 + evidence.bbox.y2) / 2) / page.height
    const bboxCenterX =
      pageRect.left -
      viewportRect.left +
      viewport.scrollLeft +
      bboxCenterRatioX * pageElement.clientWidth
    const bboxCenterY =
      pageRect.top -
      viewportRect.top +
      viewport.scrollTop +
      bboxCenterRatioY * pageElement.clientHeight

    viewport.scrollTo({
      left: clampScrollPosition(
        bboxCenterX - viewport.clientWidth / 2,
        viewport.scrollWidth - viewport.clientWidth,
      ),
      top: clampScrollPosition(
        bboxCenterY - viewport.clientHeight / 2,
        viewport.scrollHeight - viewport.clientHeight,
      ),
      behavior: 'smooth',
    })
  }

  function selectField(field: InvoiceField) {
    setSelectedFieldKind(field.kind)
    setOriginalScanFieldKind(null)

    const evidence = getFieldEvidence(field, currentInvoice)

    if (!evidence) {
      resetDocumentView()
      return
    }

    setZoomScale(FIELD_FOCUS_SCALE)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => scrollToFieldEvidence(evidence))
    })
  }

  function showOriginalScanField(field: InvoiceField) {
    setActiveSuggestionFieldKind(null)
    setHighlightedSuggestionIndex(-1)
    if (selectedInvoiceId && field.registry_replaced_value) {
      setReviewedOriginalScanFields((currentFields) => ({
        ...currentFields,
        [registryConflictKey(selectedInvoiceId, field.kind)]: true,
      }))
    }

    if (originalScanFieldKind === field.kind) {
      setOriginalScanFieldKind(null)
      setSelectedFieldKind(field.kind)
      return
    }

    setSelectedFieldKind(field.kind)
    setOriginalScanFieldKind(field.kind)

    const evidence = getFieldEvidence(field, currentInvoice, true)
    if (!evidence) {
      return
    }

    setZoomScale(FIELD_FOCUS_SCALE)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => scrollToFieldEvidence(evidence))
    })
  }

  function focusFieldByIndex(fieldIndex: number) {
    const nextInput = document.querySelector<HTMLInputElement>(
      `[data-field-index="${fieldIndex}"]`,
    )

    if (!nextInput) {
      return
    }

    nextInput.focus()
    nextInput.scrollIntoView({
      block: 'nearest',
      inline: 'nearest',
      behavior: 'smooth',
    })
  }

  useEffect(() => {
    const viewport = documentViewportRef.current
    if (!viewport) return
    const activeViewport = viewport

    function handleDocumentWheel(event: WheelEvent) {
      if (!event.ctrlKey) {
        return
      }

      event.preventDefault()

      const zoomStep = event.deltaY > 0 ? -0.05 : 0.05
      const nextScale = pageScale + zoomStep
      const clampedScale = Math.min(1.5, Math.max(0.35, nextScale))
      const roundedScale = Number(clampedScale.toFixed(2))
      const scaleRatio = roundedScale / pageScale
      const viewportRect = activeViewport.getBoundingClientRect()
      const cursorX = event.clientX - viewportRect.left
      const cursorY = event.clientY - viewportRect.top
      const nextScrollLeft =
        (activeViewport.scrollLeft + cursorX) * scaleRatio - cursorX
      const nextScrollTop =
        (activeViewport.scrollTop + cursorY) * scaleRatio - cursorY

      setZoomScale(roundedScale)
      requestAnimationFrame(() => {
        activeViewport.scrollLeft = nextScrollLeft
        activeViewport.scrollTop = nextScrollTop
      })
    }

    activeViewport.addEventListener('wheel', handleDocumentWheel, { passive: false })

    return () => {
      activeViewport.removeEventListener('wheel', handleDocumentWheel)
    }
  }, [pageScale])

  useEffect(() => {
    function handleInvoiceShortcut(event: KeyboardEvent) {
      const target = event.target
      const isFieldInput =
        target instanceof HTMLInputElement &&
        target.hasAttribute('data-field-index')
      const isNonFieldInput =
        target instanceof HTMLInputElement &&
        !target.hasAttribute('data-field-index')

      if (event.ctrlKey && event.key === 'ArrowDown') {
        event.preventDefault()
        selectInvoiceByOffsetRef.current(1)
        return
      }

      if (event.ctrlKey && event.key === 'ArrowUp') {
        event.preventDefault()
        selectInvoiceByOffsetRef.current(-1)
        return
      }

      if (
        event.altKey &&
        !isFieldInput &&
        selectedFieldIndex >= 0 &&
        (event.key === 'ArrowDown' || event.key === 'ArrowUp')
      ) {
        event.preventDefault()
        focusFieldByIndex(
          selectedFieldIndex + (event.key === 'ArrowDown' ? 1 : -1),
        )
        return
      }

      if (
        !event.ctrlKey &&
        event.key === 'ArrowDown' &&
        !selectedFieldKind &&
        !isNonFieldInput
      ) {
        event.preventDefault()
        focusFieldByIndex(0)
      }
    }

    window.addEventListener('keydown', handleInvoiceShortcut)

    return () => {
      window.removeEventListener('keydown', handleInvoiceShortcut)
    }
  }, [invoices, selectedFieldIndex, selectedFieldKind, selectedInvoiceId])

  return (
    <main className={appView !== 'review' ? 'registry-page-mode' : ''}>
      <aside>
        {appView === 'registry' ? (
          <div className="registry-sidebar">
            <h2>Anagrafica</h2>
            <button className="registry-sidebar-back-button" onClick={closeRegistryPreview}>
              Torna alle fatture
            </button>
          </div>
        ) : appView === 'archive' || appView === 'invoiceRegistry' || appView === 'processing' ? (
          <div className="registry-sidebar">
            <h2>
              {appView === 'archive'
                ? 'Archivio PDF'
                : appView === 'invoiceRegistry'
                  ? 'Registro'
                  : 'Processi'}
            </h2>
            <button className="registry-sidebar-back-button" onClick={closeInvoiceArchive}>
              Torna alle fatture
            </button>
            {appView === 'archive' ? (
              <div className="registry-sidebar-view-switch" role="tablist" aria-label="Vista archivio PDF">
                <button
                  className={pdfArchiveViewMode === 'invoices' ? 'active' : ''}
                  onClick={() => {
                    setSelectedArchiveKeys(new Set())
                    setFocusedArchiveKey(null)
                    setPdfArchiveViewMode('invoices')
                  }}
                  role="tab"
                  aria-selected={pdfArchiveViewMode === 'invoices'}
                >
                  PDF fatture
                </button>
                <button
                  className={pdfArchiveViewMode === 'sources' ? 'active' : ''}
                  onClick={() => {
                    setSelectedArchiveKeys(new Set())
                    setFocusedArchiveKey(null)
                    setPdfArchiveViewMode('sources')
                  }}
                  role="tab"
                  aria-selected={pdfArchiveViewMode === 'sources'}
                >
                  File origine
                </button>
              </div>
            ) : appView === 'invoiceRegistry' ? (
              <div className="registry-sidebar-view-switch" role="tablist" aria-label="Vista registro fatture">
                <button
                  className={invoiceRegistryViewMode === 'saved' ? 'active' : ''}
                  onClick={() => {
                    setInvoiceRegistryViewMode('saved')
                    setSelectedArchiveKeys(new Set())
                    setIsInvoiceRegistryEditMode(false)
                  }}
                  role="tab"
                  aria-selected={invoiceRegistryViewMode === 'saved'}
                >
                  Fatture da esportare
                </button>
                <button
                  className={invoiceRegistryViewMode === 'exported' ? 'active' : ''}
                  onClick={() => {
                    setInvoiceRegistryViewMode('exported')
                    setSelectedArchiveKeys(new Set())
                    setIsInvoiceRegistryEditMode(false)
                  }}
                  role="tab"
                  aria-selected={invoiceRegistryViewMode === 'exported'}
                >
                  Fatture esportate
                </button>
                <button
                  className={invoiceRegistryViewMode === 'discarded' ? 'active' : ''}
                  onClick={() => {
                    setInvoiceRegistryViewMode('discarded')
                    setSelectedArchiveKeys(new Set())
                    setIsInvoiceRegistryEditMode(false)
                  }}
                  role="tab"
                  aria-selected={invoiceRegistryViewMode === 'discarded'}
                >
                  Fatture scartate
                </button>
              </div>
            ) : (
              <div className="registry-sidebar-view-switch" role="tablist" aria-label="Vista processi">
                <button
                  className={processingViewMode === 'active' ? 'active' : ''}
                  onClick={() => setProcessingViewMode('active')}
                  role="tab"
                  aria-selected={processingViewMode === 'active'}
                >
                  In corso
                </button>
                <button
                  className={processingViewMode === 'history' ? 'active' : ''}
                  onClick={() => setProcessingViewMode('history')}
                  role="tab"
                  aria-selected={processingViewMode === 'history'}
                >
                  Cronologia
                </button>
              </div>
            )}
          </div>
        ) : (
          <>
            <h2>Fatture</h2>
            <div className="pdf-import-panel">
              <span className="sidebar-section-label">Caricamento</span>
              <button className="pdf-import-button" onClick={openDocumentImportDialog}>
                Carica documenti
              </button>
              {shouldShowPdfImportState ? (
                <p className={`pdf-import-status ${pdfImportState.kind}`}>
                  {pdfImportState.message}
                </p>
              ) : null}
            </div>
            <div className="sidebar-archive-menu">
              <button
                ref={archiveMenuTriggerRef}
                className="pdf-import-button sidebar-archive-menu-trigger"
                onClick={toggleArchiveMenu}
                onMouseEnter={keepArchiveMenuOpen}
                onMouseLeave={scheduleArchiveMenuClose}
                aria-expanded={isArchiveMenuOpen}
                aria-haspopup="menu"
              >
                Archivi
              </button>
              {isArchiveMenuOpen ? (
                <div
                  className="sidebar-archive-menu-dropdown mystudio-tools-menu"
                  role="menu"
                  onMouseEnter={keepArchiveMenuOpen}
                  onMouseLeave={scheduleArchiveMenuClose}
                  style={{
                    top: `${archiveMenuPosition.top}px`,
                    left: `${archiveMenuPosition.left}px`,
                  }}
                >
                  <div className="mystudio-tools-grid">
                    <div className="mystudio-tools-section-label">Archivi</div>
                    <button
                      className="mystudio-tool-item mystudio-tool-item-green"
                      onClick={openInvoiceArchive}
                      role="menuitem"
                    >
                      <span className="mystudio-tool-icon" aria-hidden="true">
                        PDF
                      </span>
                      <span className="mystudio-tool-label">
                        Archivio
                        <br />
                        PDF
                      </span>
                    </button>
                    <button
                      className="mystudio-tool-item mystudio-tool-item-blue"
                      onClick={openInvoiceRegistry}
                      role="menuitem"
                    >
                      <span className="mystudio-tool-icon" aria-hidden="true">
                        REG
                      </span>
                      <span className="mystudio-tool-label">
                        Registro
                        <br />
                        fatture
                      </span>
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            <button className="pdf-import-button" onClick={openProcessingJobsPage}>
              Processi
            </button>
            {batches.length > 0 ? (
              <div className="batch-select-row">
                <label className="batch-select">
                  <span>Da revisionare:</span>
                  <select
                    value={selectedBatchId ?? ''}
                    onChange={(event) => selectBatch(event.target.value)}
                  >
                    {batches.map((batch) => (
                      <option key={batch.base_path} value={batch.base_path}>
                        {reviewBatchDisplayLabel(batch.label)}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="review-actions-menu-wrap">
                  <button
                    type="button"
                    className="review-actions-menu-button"
                    aria-label="Azioni revisioni"
                    title="Azioni revisioni"
                    onClick={toggleReviewActionsMenu}
                  >
                    ...
                  </button>
                  {isReviewActionsMenuOpen ? (
                    <div
                      className="review-actions-menu"
                      role="menu"
                      style={reviewActionsMenuPosition ?? undefined}
                    >
                      <button
                        type="button"
                        className="review-actions-menu-danger"
                        onClick={requestClearReviewBatches}
                        role="menuitem"
                      >
                        Elimina tutte le revisioni
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}
            <div className="page-list">
              {invoices.length > 0 ? (
                invoices.map((invoice) => (
                  <button
                    key={invoice.invoiceId}
                    className={invoice.invoiceId === selectedInvoiceId ? 'active' : ''}
                    onClick={() => selectInvoice(invoice.invoiceId)}
                  >
                    {invoice.title}
                  </button>
                ))
              ) : (
                <p>Nessuna fattura caricata</p>
              )}
            </div>
            {hasSelectedBatchWithInvoices ? (
              <div className="batch-review-actions">
                <button
                  className="batch-save-button"
                  onClick={() => requestBatchReviewAction('save')}
                  disabled={
                    !canPersistCurrentBatch ||
                    invoiceReviewActionState?.kind === 'saving' ||
                    invoiceReviewActionState?.kind === 'discarding'
                  }
                >
                  Salva gruppo
                </button>
                <button
                  className="batch-discard-button"
                  onClick={() => requestBatchReviewAction('discard')}
                  disabled={
                    !canPersistCurrentBatch ||
                    invoiceReviewActionState?.kind === 'saving' ||
                    invoiceReviewActionState?.kind === 'discarding'
                  }
                >
                  Scarta gruppo
                </button>
                {!canPersistCurrentBatch ? (
                  <p className="batch-review-actions-note">
                    Gruppo non salvabile: carica e processa PDF dall'app.
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </aside>

      {appView === 'registry' ? (
        <section className="registry-page">
          <header>
            <div>
              <h1>Anagrafica clienti</h1>
              {isRegistrySaving ? (
                <div className="registry-saving-inline" role="status">
                  <span>Salvataggio</span>
                  <div className="registry-saving-bar" aria-hidden="true">
                    <i />
                  </div>
                </div>
              ) : null}
              <p>
                {registryStatus?.has_file
                  ? `File caricato: ${registryStatus.file_name ?? 'non disponibile'}`
                  : 'Nessuna anagrafica presente'}
                {registryStatus?.record_count !== undefined
                  ? ` / ${registryStatus.record_count} record`
                  : ''}
              </p>
            </div>
            <RegistrySearch
              searchIndex={registrySearchIndex}
              onSelectSuggestion={selectRegistrySearchSuggestion}
            />
            <div className="registry-page-actions">
              {isRegistryEditMode ? (
                <>
                  <button
                    className="registry-save-button"
                    onClick={saveRegistryEdits}
                    disabled={isRegistrySaving}
                  >
                    {isRegistrySaving ? 'Salvataggio...' : 'Salva'}
                  </button>
                  <button onClick={cancelRegistryEditMode} disabled={isRegistrySaving}>
                    Annulla
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="registry-page-add-record-button"
                    onClick={addBlankRegistryRecord}
                    disabled={!registryStatus?.has_file}
                    title="Aggiungi nuova riga"
                  >
                    +
                  </button>
                  <button onClick={enterRegistryEditMode}>Modifica</button>
                </>
              )}
              <button
                onClick={previewRegistryCleaning}
                disabled={isRegistryEditMode || registryPreviewRecords.length === 0}
              >
                {isRegistryCleaningRunning ? 'Analisi...' : 'Pulizia'}
              </button>
              <button className="registry-add-button" onClick={openRegistryFilePicker}>
                {registryStatus?.has_file ? 'Sostituisci anagrafica' : 'Aggiungi anagrafica'}
              </button>
              <button
                className="registry-delete-button"
                onClick={() => setIsRegistryDeleteConfirmOpen(true)}
                disabled={!registryStatus?.has_file}
              >
                Cancella
              </button>
              <button
                className="registry-download-button"
                onClick={downloadRegistryFile}
                aria-label="Scarica anagrafica modificata"
                title="Scarica anagrafica modificata"
                disabled={!registryStatus?.export_file_url}
              >
                <DownloadIcon />
              </button>
              <input
                ref={registryFileInputRef}
                className="hidden-file-input"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(event) => importRegistryFile(event.target.files?.[0])}
              />
            </div>
          </header>
          {registryImportState?.kind === 'loading' || registryImportState?.kind === 'success' ? (
            <div className={`registry-import-progress-state ${registryImportState.kind}`}>
              <h2>{registryImportState.message}</h2>
              {registryImportState.kind === 'loading' ? (
                <div className="registry-import-progress-bar" aria-hidden="true">
                  <span />
                </div>
              ) : (
                <p>Operazione completata.</p>
              )}
            </div>
          ) : registryStatusError ? (
            <p className="registry-page-message">{registryStatusError}</p>
          ) : registryPreviewError ? (
            <p className="registry-page-message">{registryPreviewError}</p>
          ) : registryStatus && !registryStatus.has_file ? (
            <div className="registry-empty-state">
              <h2>Nessuna anagrafica presente</h2>
              <p>Usa Aggiungi anagrafica per selezionare un file Excel.</p>
            </div>
          ) : (
            <RegistryTable
              records={registryTableRecords}
              isEditMode={isRegistryEditMode}
              highlightedRecordKey={highlightedRegistryRecordKey}
              tableViewportRef={registryTableViewportRef}
              onUpdateRecord={updateRegistryDraftRecord}
              onDeleteRecord={requestDeleteRegistryDraftRecord}
              onClearHighlight={clearRegistrySearchHighlight}
            />
          )}
          {registrySaveStatus ? (
            <p className="registry-import-status">{registrySaveStatus}</p>
          ) : null}
          {registryImportState?.kind === 'error' ? (
            <RegistryImportErrorDialog
              message={registryImportState.message}
              onClose={() => setRegistryImportState(null)}
            />
          ) : null}
          {isRegistryDeleteConfirmOpen ? (
            <RegistryDeleteConfirmDialog
              onCancel={() => setIsRegistryDeleteConfirmOpen(false)}
              onConfirm={deleteRegistryFile}
            />
          ) : null}
          {registryCleaningReport ? (
            <RegistryCleaningReportDialog
              key={`${registryCleaningReportMode}-${registryCleaningReport.groups.map((group) => group.group_key).join('|')}`}
              report={registryCleaningReport}
              mode={registryCleaningReportMode}
              isRunning={isRegistryCleaningRunning}
              reportFileUrl={registryCleaningReportFileUrl}
              onClose={() => setRegistryCleaningReport(null)}
              onApply={runRegistryCleaning}
              onDownload={downloadRegistryCleaningReport}
            />
          ) : null}
        </section>
      ) : appView === 'archive' ? (
        <section className="registry-page archive-page pdf-archive-page">
          <header>
            <div>
              <h1>Archivio PDF</h1>
              <p>
                {pdfArchiveViewMode === 'invoices'
                  ? `${filteredInvoiceArchiveRecords.length} PDF fatture archiviate`
                  : `${filteredSourceArchiveRecords.length} file origine archiviati`}
              </p>
            </div>
            <div className="registry-page-actions pdf-archive-page-actions">
              <div className="archive-search">
                <input
                  value={invoiceArchiveSearch}
                  placeholder={
                    pdfArchiveViewMode === 'invoices'
                      ? 'Cerca emittente, condominio, numero'
                      : 'Cerca file origine'
                  }
                  onChange={(event) => setInvoiceArchiveSearch(event.target.value)}
                />
              </div>
              <button
                type="button"
                onClick={
                  pdfArchiveViewMode === 'sources'
                    ? requestReprocessSelectedArchivedSources
                    : requestReprocessSelectedArchivedInvoices
                }
                disabled={selectedArchiveKeys.size === 0}
              >
                Riprocessa
              </button>
              <button
                type="button"
                className="archive-trash-button"
                onClick={() =>
                  requestDeleteSelectedArchives(
                    pdfArchiveViewMode === 'sources' ? 'source' : 'invoice',
                  )
                }
                disabled={selectedArchiveKeys.size === 0}
                aria-label="Elimina elementi selezionati"
                title="Elimina elementi selezionati"
              >
                <TrashIcon />
              </button>
            </div>
          </header>
          {invoiceArchiveError ? (
            <p className="registry-page-message">{invoiceArchiveError}</p>
          ) : pdfArchiveViewMode === 'sources' ? (
            <SourcePdfArchiveTable
              records={filteredSourceArchiveRecords}
              selectedKeys={selectedArchiveKeys}
              focusedKey={focusedArchiveKey}
              onToggle={(record) =>
                toggleArchiveSelection(record.source_id)
              }
              onToggleAll={() =>
                toggleAllArchiveSelections(
                  filteredSourceArchiveRecords.map((record) => record.source_id),
                )
              }
            />
          ) : (
            <InvoicePdfArchiveTable
              records={filteredInvoiceArchiveRecords}
              selectedKeys={selectedArchiveKeys}
              focusedKey={focusedArchiveKey}
              onGoToSource={(record) =>
                navigateToArchiveReference({ record, kind: 'source', error: null })
              }
              onToggle={(record) => toggleArchiveSelection(record.record_id)}
              onToggleAll={() =>
                toggleAllArchiveSelections(
                  filteredInvoiceArchiveRecords.map((record) => record.record_id),
                )
              }
            />
          )}
        </section>
      ) : appView === 'invoiceRegistry' ? (
        <section className="registry-page archive-page invoice-registry-page">
          <header>
            <div>
              <h1>Registro fatture</h1>
              <p>
                {filteredInvoiceRegistryRecords.length}{' '}
                {invoiceRegistryViewMode === 'saved'
                  ? 'fatture da esportare'
                  : invoiceRegistryViewMode === 'exported'
                    ? 'fatture esportate'
                    : 'fatture scartate'}
                {invoiceArchiveSearch.trim()
                  ? ` / ${invoiceRegistryViewMode === 'exported' ? 0 : invoiceArchiveRecords.filter((record) => record.status === invoiceRegistryViewMode && record.registry_visible !== false).length} totali`
                  : ''}
              </p>
              {invoiceRegistrySaveStatus ? (
                <div className="registry-saving-inline" role="status">
                  <span>{invoiceRegistrySaveStatus}</span>
                  {invoiceRegistrySaveStatus.includes('corso') ? (
                    <div className="registry-saving-bar" aria-hidden="true">
                      <i />
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
            <label className="invoice-registry-toolbar-control">
              <span>Da</span>
              <input
                type="date"
                value={invoiceArchiveFilters.dateFrom}
                onChange={(event) =>
                  setInvoiceArchiveFilters((currentFilters) => ({
                    ...currentFilters,
                    dateFrom: event.target.value,
                  }))
                }
              />
            </label>
            <label className="invoice-registry-toolbar-control">
              <span>A</span>
              <input
                type="date"
                value={invoiceArchiveFilters.dateTo}
                onChange={(event) =>
                  setInvoiceArchiveFilters((currentFilters) => ({
                    ...currentFilters,
                    dateTo: event.target.value,
                  }))
                }
              />
            </label>
            <label className="invoice-registry-toolbar-control invoice-registry-sort-control">
              <span>Ordina</span>
              <select
                value={invoiceArchiveSortKey}
                onChange={(event) =>
                  setInvoiceArchiveSortKey(event.target.value as InvoiceArchiveSortKey)
                }
              >
                <option value="saved_at_desc">
                  {invoiceRegistryViewMode === 'saved'
                    ? 'Salvataggio recente'
                    : invoiceRegistryViewMode === 'exported'
                      ? 'Esportazione recente'
                      : 'Scarto recente'}
                </option>
                <option value="saved_at_asc">
                  {invoiceRegistryViewMode === 'saved'
                    ? 'Salvataggio meno recente'
                    : invoiceRegistryViewMode === 'exported'
                      ? 'Esportazione meno recente'
                      : 'Scarto meno recente'}
                </option>
                <option value="data_fattura_desc">Data fattura decrescente</option>
                <option value="data_fattura_asc">Data fattura crescente</option>
                <option value="emittente_asc">Emittente A-Z</option>
                <option value="condominio_asc">Condominio A-Z</option>
                <option value="totale_desc">Totale decrescente</option>
                <option value="totale_asc">Totale crescente</option>
              </select>
            </label>
            <div className="archive-search">
              <input
                value={invoiceArchiveSearch}
                placeholder="Cerca emittente, condominio, numero, totale"
                onChange={(event) => setInvoiceArchiveSearch(event.target.value)}
              />
            </div>
            <div className="registry-page-actions">
              {invoiceRegistryViewMode === 'saved' && isInvoiceRegistryEditMode ? (
                <>
                  <button
                    className="registry-save-button"
                    onClick={saveInvoiceRegistryDraft}
                    disabled={invoiceRegistrySaveStatus?.includes('corso')}
                  >
                    Salva
                  </button>
                  <button onClick={cancelInvoiceRegistryEdit}>Annulla</button>
                </>
              ) : invoiceRegistryViewMode === 'saved' ? (
                <button onClick={startInvoiceRegistryEdit}>Modifica</button>
              ) : null}
              {invoiceRegistryViewMode === 'saved' ? (
                <button
                  className="registry-download-button"
                  onClick={downloadInvoiceArchiveExcel}
                  aria-label="Esporta registro fatture in Excel"
                  title="Esporta registro fatture in Excel"
                  disabled={
                    invoiceArchiveRecords.filter(
                      (record) =>
                        record.status === 'saved' && record.registry_visible !== false,
                    ).length === 0
                  }
                >
                  <DownloadIcon />
                </button>
              ) : null}
              <button
                type="button"
                onClick={requestReprocessSelectedArchivedInvoices}
                disabled={selectedArchiveKeys.size === 0}
              >
                Riprocessa
              </button>
              <button
                type="button"
                className="archive-trash-button"
                onClick={() => requestDeleteSelectedArchives('registry')}
                disabled={selectedArchiveKeys.size === 0}
                aria-label="Elimina righe selezionate"
                title="Elimina righe selezionate"
              >
                <TrashIcon />
              </button>
            </div>
          </header>
          {invoiceArchiveError ? (
            <p className="registry-page-message">{invoiceArchiveError}</p>
          ) : (
            <InvoiceRegistryTable
              records={filteredInvoiceRegistryRecords}
              viewMode={invoiceRegistryViewMode}
              isEditMode={
                invoiceRegistryViewMode === 'saved' && isInvoiceRegistryEditMode
              }
              onUpdateRecordField={updateInvoiceRegistryDraftField}
              selectedKeys={selectedArchiveKeys}
              onToggle={(record) => toggleArchiveSelection(record.record_id)}
              onToggleAll={() =>
                toggleAllArchiveSelections(
                  filteredInvoiceRegistryRecords.map((record) => record.record_id),
                )
              }
              onOpenArchiveReference={requestArchiveReference}
            />
          )}
        </section>
      ) : appView === 'processing' ? (
        <section className="registry-page archive-page processing-page">
          <header>
            <div>
              <h1>Processi</h1>
              <p>
                {processingViewMode === 'active'
                  ? `${activeProcessingJobs.length} in corso o in coda`
                  : `${processingHistoryJobs.length} processi conclusi`}
              </p>
            </div>
            {processingViewMode === 'history' ? (
              <div className="registry-page-actions processing-page-actions">
                <button
                  type="button"
                  className="archive-trash-button"
                  onClick={() => setIsProcessingHistoryDeleteConfirmOpen(true)}
                  disabled={selectedProcessingHistoryIds.size === 0}
                  aria-label="Elimina processi selezionati"
                  title="Elimina processi selezionati"
                >
                  <TrashIcon />
                </button>
              </div>
            ) : null}
          </header>
          <div className="processing-page-content">
            {processingViewMode === 'history' && processingHistoryJobs.length > 0 ? (
              <label className="processing-history-select-all">
                <input
                  type="checkbox"
                  checked={processingHistoryJobs.every((job) =>
                    selectedProcessingHistoryIds.has(job.job_id),
                  )}
                  onChange={toggleAllProcessingHistory}
                />
                Seleziona tutta la cronologia
              </label>
            ) : null}
            <div className="processing-jobs-list">
              {(processingViewMode === 'active' ? activeProcessingJobs : processingHistoryJobs).length > 0 ? (
                (processingViewMode === 'active' ? activeProcessingJobs : processingHistoryJobs).map((job) => {
                  const canRemoveJob = job.status === 'queued'
                  const canCancelJob = job.status === 'processing'
                  const isCancellationPending = isProcessingCancellationPending(job)
                  return (
                    <article
                      className={`processing-job-card ${processingViewMode}`}
                      key={job.job_id}
                    >
                      {processingViewMode === 'history' ? (
                        <input
                          className="processing-history-checkbox"
                          type="checkbox"
                          checked={selectedProcessingHistoryIds.has(job.job_id)}
                          onChange={() => toggleProcessingHistorySelection(job.job_id)}
                          aria-label="Seleziona processo"
                        />
                      ) : null}
                      <div className="processing-job-card-header">
                        <strong>{job.files?.length ?? job.progress_total ?? 0} PDF</strong>
                        <small>
                          {processingViewMode === 'history'
                            ? formatProcessingDateTime(job.created_at)
                            : processingJobDetailMessage(job)}
                        </small>
                      </div>
                      <span className={`processing-status-pill ${job.status}`}>
                        {sourceArchiveProcessingStatusLabel(job)}
                      </span>
                      {processingViewMode === 'active' ? (
                        <div className="processing-progress-summary">
                          <span>
                            {job.progress_current ?? 0} / {job.progress_total ?? 0}
                          </span>
                        </div>
                      ) : null}
                      <div className="processing-job-details">
                        <small>Provenienza: {processingSourceKindLabel(job)}</small>
                        {processingJobTimeMessage(job) ? (
                          <small>{processingJobTimeMessage(job)}</small>
                        ) : null}
                      </div>
                      <div className="processing-job-card-actions">
                        <button
                          className="processing-info-button"
                          onClick={() => setProcessingJobInfo(job)}
                          aria-label="Mostra file del processo"
                          title="Mostra file del processo"
                        >
                          i
                        </button>
                        {canRemoveJob ? (
                          <button
                            className="imported-batch-process-button secondary-action"
                            onClick={() => requestRemoveProcessingJobFromQueue(job.job_id)}
                          >
                            Rimuovi dalla coda
                          </button>
                        ) : null}
                        {canCancelJob && !isCancellationPending ? (
                          <button
                            className="imported-batch-cancel-button"
                            onClick={() => requestCancelProcessing(job.job_id)}
                          >
                            Annulla
                          </button>
                        ) : null}
                      </div>
                      {isCancellationPending ? (
                        <div className="processing-cancel-progress">
                          <span>Annullamento in corso...</span>
                          <div aria-hidden="true">
                            <i />
                          </div>
                        </div>
                      ) : null}
                    </article>
                  )
                })
              ) : (
                <div className="registry-empty-state">
                  <h2>{processingViewMode === 'active' ? 'Nessun processo attivo' : 'Nessuna cronologia'}</h2>
                  <p>
                    {processingViewMode === 'active'
                      ? 'I processi avviati o in attesa compariranno qui.'
                      : 'I processi conclusi compariranno qui.'}
                  </p>
                </div>
              )}
            </div>
          </div>
        </section>
      ) : (
      <section className="document-panel">
        <header>
          <div className="document-context">
            <h1>{currentInvoice?.title ?? 'Nessuna fattura selezionata'}</h1>
            <div className="document-metadata">
              <p
                className="document-source-file"
                title={
                  fileNameFromPath(
                    currentInvoice?.sourceFile ?? manifestSourceFile,
                  ) ?? 'non disponibile'
                }
              >
                <span>File origine:</span>
                <strong>
                  {fileNameFromPath(
                    currentInvoice?.sourceFile ?? manifestSourceFile,
                  ) ?? 'non disponibile'}
                </strong>
              </p>
            </div>
            {currentInvoice ? (() => {
              const firstToPreviousState =
                currentInvoiceBoundaryPageMoveState('first_to_previous')
              const lastToNextState = currentInvoiceBoundaryPageMoveState('last_to_next')
              const disabledReason =
                !firstToPreviousState.enabled && !lastToNextState.enabled
                  ? firstToPreviousState.reason
                  : ''
              const pageCount = currentInvoice.pageNumbers?.length ?? 0
              const safeSplitPosition = Math.min(
                Math.max(splitAfterPosition, 1),
                Math.max(pageCount - 1, 1),
              )

              return (
                <details className="invoice-page-reorder-menu">
                  <summary>Riordina pagine</summary>
                  <div className="invoice-page-boundary-actions">
                    <button
                      type="button"
                      onClick={() => requestCurrentInvoiceBoundaryMove('first_to_previous')}
                      disabled={!firstToPreviousState.enabled}
                      title={
                        firstToPreviousState.enabled
                          ? 'Sposta la prima pagina di questa fattura nella fattura precedente'
                          : firstToPreviousState.reason
                      }
                    >
                      Prima pagina alla precedente
                    </button>
                    <button
                      type="button"
                      onClick={() => requestCurrentInvoiceBoundaryMove('last_to_next')}
                      disabled={!lastToNextState.enabled}
                      title={
                        lastToNextState.enabled
                          ? "Sposta l'ultima pagina di questa fattura nella fattura successiva"
                          : lastToNextState.reason
                      }
                    >
                      Ultima pagina alla successiva
                    </button>
                    {disabledReason ? (
                      <p className="invoice-page-boundary-disabled-reason">
                        {disabledReason}
                      </p>
                    ) : null}
                    <div className="invoice-page-split-actions">
                      <label>
                        Dividi dopo la pagina
                        <input
                          type="number"
                          min={1}
                          max={Math.max(pageCount - 1, 1)}
                          value={safeSplitPosition}
                          disabled={pageCount < 2}
                          onChange={(event) =>
                            setSplitAfterPosition(Number(event.target.value))
                          }
                          aria-label="Pagina dopo cui dividere la fattura"
                        />
                      </label>
                      <button
                        type="button"
                        onClick={requestCurrentInvoiceSplit}
                        disabled={pageCount < 2}
                      >
                        Dividi fattura
                      </button>
                      {pageCount < 2 ? (
                        <p className="invoice-page-boundary-disabled-reason">
                          Servono almeno due pagine per dividere la fattura.
                        </p>
                      ) : null}
                    </div>
                  </div>
                </details>
              )
            })() : null}

          </div>

          <div className="zoom-controls">
            <button className="registry-import-button" onClick={openRegistryPreview}>
              Anagrafica clienti
            </button>
            <div className="zoom-stepper" aria-label="Controlli zoom">
              <button
                className="zoom-out-button"
                onClick={zoomOut}
                aria-label="Riduci zoom"
                title="Riduci zoom"
              />
              <label>
                <input
                  type="number"
                  min="35"
                  max="150"
                  step="5"
                  value={zoomInput}
                  aria-label="Percentuale zoom"
                  onChange={(event) => setZoomInput(event.target.value)}
                  onBlur={applyZoomInput}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      applyZoomInput()
                      event.currentTarget.blur()
                    }
                  }}
                />
                %
              </label>
              <button onClick={zoomIn} aria-label="Aumenta zoom" title="Aumenta zoom">
                +
              </button>
            </div>
            <button className="document-reset-button" onClick={resetDocumentView}>
              Ripristina
            </button>
            <div className="developer-tools" ref={developerMenuRef}>
              <button
                type="button"
                className="developer-tools-trigger"
                aria-controls="developer-tools-panel"
                aria-expanded={isDeveloperMenuOpen}
                onClick={() => setIsDeveloperMenuOpen((isOpen) => !isOpen)}
              >
                Area sviluppatore
              </button>
              {isDeveloperMenuOpen ? (
                <div
                  id="developer-tools-panel"
                  className="developer-tools-menu"
                  role="region"
                  aria-label="Area sviluppatore"
                >
                  <p className="developer-tools-section-label">Visualizzazione OCR</p>
                  <div className="developer-overlay-options">
                    <label>
                      <input
                        type="checkbox"
                        checked={showTokenBoxes}
                        onChange={(event) => setShowTokenBoxes(event.target.checked)}
                      />
                      <span>Parole</span>
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={showCropBoxes}
                        onChange={(event) => setShowCropBoxes(event.target.checked)}
                      />
                      <span>Ritagli</span>
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={showTableBoxes}
                        onChange={(event) => setShowTableBoxes(event.target.checked)}
                      />
                      <span>Tabelle</span>
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={showBoxLabels}
                        onChange={(event) => setShowBoxLabels(event.target.checked)}
                      />
                      <span>Etichette</span>
                    </label>
                  </div>
                  <div className="developer-export-section">
                    <p className="developer-tools-section-label">
                      Dati per lo sviluppatore
                    </p>
                    <button
                      type="button"
                      className="developer-export-button"
                      onClick={requestFeedbackExport}
                    >
                      Esporta dati per miglioramento
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <div className="document-viewport" ref={documentViewportRef}>
          {manifestError && !currentInvoice ? (
            <div className="document-empty-message" role="status">
              {manifestError}
            </div>
          ) : null}
          {(currentInvoice?.pages ?? []).map((page) => (
            <article
              className="document-page"
              data-page={page.page}
              key={page.page}
              style={{
                width: page.width * pageScale,
                height: page.height * pageScale,
              }}
            >
              {page.image ? (
                <img
                  src={`${currentInvoice?.basePath}/${page.image}`}
                  alt={`Pagina ${page.page}`}
                />
              ) : (
                <p className="mock-page-label">
                  Pagina {page.page}
                  <br />
                  {page.width} x {page.height}
                </p>
              )}
              {selectedFieldEvidence?.page === page.page ? (
                <div
                  className={`selected-bbox ${getOcrConfidenceClassName(
                    selectedFieldEvidence.ocr_confidence,
                  )}`}
                  style={getBboxStyle(
                    selectedFieldEvidence.bbox,
                    page.width,
                    page.height,
                  )}
                >
                  {showBoxLabels ? (
                    <span className="bbox-label">{selectedFieldEvidence.value}</span>
                  ) : null}
                </div>
              ) : null}
              {showAllTargetBoxes
                ? (page.targets ?? []).map((target, targetIndex) => (
                    <div
                      className={`all-target-bbox ${getOcrConfidenceClassName(
                        target.ocr_confidence,
                      )}`}
                      key={`${target.kind}-${target.role}-${targetIndex}`}
                      style={getBboxStyle(target.bbox, page.width, page.height)}
                    >
                      {showBoxLabels ? (
                        <span className="bbox-label">
                          {formatTargetValueForDisplay(target.kind, target.value)}
                        </span>
                      ) : null}
                    </div>
                  ))
                : null}
              {showTokenBoxes
                ? (page.tokens ?? []).map((token, tokenIndex) => (
                    <div
                      className="token-bbox"
                      key={`${token.text}-${tokenIndex}`}
                      style={getBboxStyle(token.bbox, page.width, page.height)}
                    >
                      {showBoxLabels ? (
                        <span className="bbox-label">{token.text}</span>
                      ) : null}
                    </div>
                  ))
                : null}
              {showCropBoxes
                ? (page.ocr_hole_regions ?? []).map((region, regionIndex) => (
                    <div
                      className="crop-bbox"
                      key={`${region.reason}-${regionIndex}`}
                      style={getBboxStyle(region.bbox, page.width, page.height)}
                    >
                      {showBoxLabels ? (
                        <span className="bbox-label">
                          {region.reason} / ink {region.ink_density.toFixed(3)} /
                          tokens {region.token_count}
                        </span>
                      ) : null}
                    </div>
                  ))
                : null}
              {showTableBoxes
                ? (
                    <>
                      {(page.detected_table_cell_regions ?? []).some(
                        (cell) => cell.polygon && cell.polygon.length >= 4,
                      ) ? (
                        <svg
                          className="table-cell-polygon-layer"
                          viewBox={`0 0 ${page.width} ${page.height}`}
                          preserveAspectRatio="none"
                        >
                          {(page.detected_table_cell_regions ?? []).map(
                            (cell, cellIndex) =>
                              cell.polygon && cell.polygon.length >= 4 ? (
                                <g
                                  key={`table-cell-polygon-${cell.table_index}-${cell.row_index}-${cell.column_index}-${cellIndex}`}
                                >
                              <polygon
                                className="table-cell-polygon"
                                points={getPolygonPoints(cell.polygon)}
                              />
                              {showBoxLabels ? (
                                <foreignObject
                                  x={cell.bbox.x1}
                                  y={cell.bbox.y1}
                                  width={Math.max(1, cell.bbox.x2 - cell.bbox.x1)}
                                  height={20}
                                >
                                  <span className="bbox-label table-cell-label">
                                    cell t{cell.table_index} r{cell.row_index} c
                                    {cell.column_index}
                                  </span>
                                </foreignObject>
                              ) : null}
                                </g>
                              ) : null,
                          )}
                        </svg>
                      ) : null}
                      {(page.detected_table_cell_regions ?? []).map(
                        (cell, cellIndex) =>
                          cell.polygon && cell.polygon.length >= 4 ? null : (
                            <div
                              className="table-cell-bbox"
                              key={`table-cell-${cell.table_index}-${cell.row_index}-${cell.column_index}-${cellIndex}`}
                              style={getBboxStyle(
                                cell.bbox,
                                page.width,
                                page.height,
                              )}
                            >
                              {showBoxLabels ? (
                                <span className="bbox-label">
                                  cell t{cell.table_index} r{cell.row_index} c
                                  {cell.column_index}
                                </span>
                              ) : null}
                            </div>
                          ),
                      )}
                    </>
                  )
                : null}
            </article>
          ))}
        </div>
      </section>
      )}

      <aside ref={invoiceFieldsPanelRef} className="invoice-fields-panel">
        <div className="fields-header">
          <h2>Campi fattura</h2>
          <label className="checkbox-control">
            <input
              type="checkbox"
              checked={showAllTargetBoxes}
              onChange={(event) => setShowAllTargetBoxes(event.target.checked)}
            />
            Tutti i campi
          </label>
          <div className="invoice-review-actions">
            {canPersistCurrentInvoice ? (
              <button
                className="invoice-save-button"
                onClick={saveCurrentInvoice}
                disabled={
                  invoiceReviewActionState?.kind === 'saving' ||
                  invoiceReviewActionState?.kind === 'discarding'
                }
              >
                Salva
              </button>
            ) : null}
            <button onClick={resetFieldValues}>Ripristina</button>
            {canPersistCurrentInvoice ? (
              <button
                className="invoice-discard-button"
                onClick={discardCurrentInvoice}
                disabled={
                  invoiceReviewActionState?.kind === 'saving' ||
                  invoiceReviewActionState?.kind === 'discarding'
                }
              >
                Elimina
              </button>
            ) : null}
          </div>
        </div>
        {invoiceReviewActionState ? (
          <p className={`invoice-review-action-status ${invoiceReviewActionState.kind}`}>
            {invoiceReviewActionState.message}
          </p>
        ) : null}
        <div>
          <ul>
            {displayedFields.map((field, fieldIndex) => {
              const fieldConflict = selectedInvoiceId
                ? registryFieldConflicts[
                    registryConflictKey(selectedInvoiceId, field.kind)
                  ]
                : undefined
              const isViewingOriginalScan = field.kind === originalScanFieldKind
              const originalScanReviewKey = selectedInvoiceId
                ? registryConflictKey(selectedInvoiceId, field.kind)
                : null
              const needsOriginalScanReview =
                fieldNeedsRegistryReplacementReview(field) &&
                Boolean(originalScanReviewKey) &&
                !reviewedOriginalScanFields[originalScanReviewKey as string]
              const fieldSuggestions = getOrderedFieldSuggestions(
                suggestionIndex,
                registryPreviewRecords,
                currentInvoice,
                field,
              )
              const canOpenRegistryRecord =
                field.registry_id &&
                field.registry_source_row !== null &&
                field.registry_source_row !== undefined
              const canAddRegistryRecord =
                registryPreviewRecords.length > 0 &&
                (field.kind === 'codice_fiscale' ||
                  field.kind === 'codice_fiscale_emittente') &&
                field.value.trim() &&
                registryRecordsForFiscalCode(
                  registryPreviewRecords,
                  field.kind,
                  field.value,
                ).length === 0
              const isSuggestionOpen =
                !isViewingOriginalScan &&
                activeSuggestionFieldKind === field.kind &&
                fieldSuggestions.length > 0
              const isConflictOpen =
                !isViewingOriginalScan &&
                activeSuggestionFieldKind === field.kind &&
                fieldConflict !== undefined
              const activeChoiceCount = isConflictOpen
                ? fieldConflict.options.length
                : fieldSuggestions.length
              const safeHighlightedSuggestionIndex =
                highlightedSuggestionIndex < 0
                  ? -1
                  : Math.min(
                      highlightedSuggestionIndex,
                      Math.max(0, activeChoiceCount - 1),
                    )
              const hasInvalidMoney = isInvalidMoneyField(
                field.kind,
                field.value,
              )

              return (
                <li
                  key={field.kind}
                  className={[
                    field.kind === selectedFieldKind ? 'selected-field' : '',
                    field.role === 'registry_enriched' ? 'registry-enriched-field' : '',
                    field.role === 'suggestion_enriched' ? 'suggestion-enriched-field' : '',
                    needsOriginalScanReview ? 'original-scan-review-needed-field' : '',
                    fieldConflict ? 'registry-conflict-field' : '',
                    isViewingOriginalScan ? 'original-scan-view-field' : '',
                    hasInvalidMoney ? 'invalid-money-field' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <strong className="field-label-row">
                    <span>{field.label}</span>
                    {canOpenRegistryRecord ? (
                      <button
                        className="field-registry-record-button"
                        type="button"
                        aria-label="Apri questa riga in anagrafica"
                        title="Apri questa riga in anagrafica"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          openRegistryRecordFromField(field)
                        }}
                      >
                        <RegistryRecordIcon />
                      </button>
                    ) : null}
                    {canAddRegistryRecord ? (
                      <button
                        className="field-add-registry-record-button"
                        type="button"
                        aria-label="Aggiungi questo codice fiscale in anagrafica"
                        title="Aggiungi questo codice fiscale in anagrafica"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          requestAddRegistryRecordFromField(field)
                        }}
                      >
                        +
                      </button>
                    ) : null}
                    {field.registry_replaced_value ? (
                      <button
                        className="field-original-scan-button"
                        type="button"
                        aria-pressed={isViewingOriginalScan}
                        aria-label={
                          isViewingOriginalScan
                            ? 'Torna al valore anagrafica'
                            : 'Mostra il valore letto dalla fattura'
                        }
                        title={
                          isViewingOriginalScan
                            ? 'Torna al valore anagrafica'
                            : `Mostra il valore letto dalla fattura: ${field.registry_replaced_value}`
                        }
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          showOriginalScanField(field)
                        }}
                      >
                        {isViewingOriginalScan ? <EyeIcon /> : <EyeClosedIcon />}
                      </button>
                    ) : null}
                    {fieldConflict ? (
                      <button
                        className="field-conflict-badge"
                        type="button"
                        aria-label="Piu possibilita in anagrafica: clicca per scegliere quale riga usare"
                        onClick={() => setActiveSuggestionFieldKind(field.kind)}
                      >
                        !
                      </button>
                    ) : null}
                  </strong>
                  <span className="field-input-row">
                    <span className="field-input-shell">
                      {isViewingOriginalScan && field.registry_replaced_value ? (
                        <div className="original-scan-value-view">
                          <span>{field.registry_replaced_value}</span>
                        </div>
                      ) : (
                        <input
                          data-field-index={fieldIndex}
                          value={field.value}
                          aria-autocomplete="list"
                          aria-expanded={isSuggestionOpen}
                          onFocus={() => {
                            selectField(field)
                            setActiveSuggestionFieldKind(field.kind)
                            setHighlightedSuggestionIndex(-1)
                          }}
                          onClick={() => {
                            selectField(field)
                            setActiveSuggestionFieldKind(field.kind)
                            setHighlightedSuggestionIndex(-1)
                          }}
                          onChange={(event) =>
                            updateFieldValue(field.kind, event.target.value)
                          }
                          onBlur={(event) => {
                            const normalizedValue = normalizeInvoiceFieldValue(
                              field.kind,
                              event.currentTarget.value,
                            )
                            applyExactRegistryFieldMatch(field.kind, normalizedValue)
                            setActiveSuggestionFieldKind(null)
                            window.setTimeout(() => {
                              void flushReviewDraftSaves().catch(() => undefined)
                            }, 0)
                          }}
                          onKeyDown={(event) => {
                            const hasOpenChoices = isSuggestionOpen || isConflictOpen
                            if (
                              event.ctrlKey &&
                              (event.key === 'ArrowDown' || event.key === 'ArrowUp')
                            ) {
                              return
                            }
                            if (event.altKey && event.key === 'ArrowDown') {
                              event.preventDefault()
                              focusFieldByIndex(fieldIndex + 1)
                              return
                            }
                            if (event.altKey && event.key === 'ArrowUp') {
                              event.preventDefault()
                              focusFieldByIndex(fieldIndex - 1)
                              return
                            }
                            if (hasOpenChoices && event.key === 'ArrowDown') {
                              event.preventDefault()
                              setHighlightedSuggestionIndex((currentIndex) =>
                                Math.min(currentIndex + 1, activeChoiceCount - 1),
                              )
                              return
                            }
                            if (hasOpenChoices && event.key === 'ArrowUp') {
                              event.preventDefault()
                              setHighlightedSuggestionIndex((currentIndex) =>
                                Math.max(currentIndex - 1, -1),
                              )
                              return
                            }
                            if (
                              isSuggestionOpen &&
                              event.key === 'Enter' &&
                              highlightedSuggestionIndex >= 0
                            ) {
                              event.preventDefault()
                              acceptFieldSuggestion(
                                field.kind,
                                fieldSuggestions[safeHighlightedSuggestionIndex],
                              )
                              return
                            }
                            if (
                              isConflictOpen &&
                              event.key === 'Enter' &&
                              highlightedSuggestionIndex >= 0 &&
                              fieldConflict
                            ) {
                              event.preventDefault()
                              applyRegistryRecordToSelectedInvoice(
                                fieldConflict.sourceFieldKind,
                                fieldConflict.options[safeHighlightedSuggestionIndex],
                              )
                              setActiveSuggestionFieldKind(null)
                              setHighlightedSuggestionIndex(-1)
                              return
                            }
                            if (hasOpenChoices && event.key === 'Escape') {
                              event.preventDefault()
                              setActiveSuggestionFieldKind(null)
                              setHighlightedSuggestionIndex(-1)
                              return
                            }
                            if (event.key === 'Enter') {
                              const normalizedValue = normalizeInvoiceFieldValue(
                                field.kind,
                                event.currentTarget.value,
                              )
                              if (applyExactRegistryFieldMatch(field.kind, normalizedValue)) {
                                event.preventDefault()
                                setActiveSuggestionFieldKind(null)
                                setHighlightedSuggestionIndex(-1)
                                event.currentTarget.blur()
                                return
                              }
                              if (
                                field.kind === 'codice_fiscale' ||
                                field.kind === 'codice_fiscale_emittente'
                              ) {
                                event.preventDefault()
                                applyRegistryFiscalCodeMatch(field.kind, normalizedValue)
                                setActiveSuggestionFieldKind(null)
                                setHighlightedSuggestionIndex(-1)
                              }
                              event.currentTarget.blur()
                            }
                          }}
                        />
                      )}
                      {isSuggestionOpen ? (
                        <div className="field-suggestions" role="listbox">
                          {fieldSuggestions.map((suggestion, suggestionIndex) => (
                            <button
                              key={
                                suggestion.registryRecord
                                  ? `${field.kind}-${registryRecordKey(suggestion.registryRecord)}`
                                  : `${field.kind}-${suggestion.value}`
                              }
                              type="button"
                              className={[
                                suggestionIndex === safeHighlightedSuggestionIndex
                                  ? 'active'
                                  : '',
                                suggestion.registryRecord ? 'registry-row-suggestion' : '',
                              ]
                                .filter(Boolean)
                                .join(' ')}
                              onMouseDown={(event) => {
                                event.preventDefault()
                                acceptFieldSuggestion(field.kind, suggestion)
                              }}
                            >
                              {suggestion.registryRecord ? (
                                <>
                                  <span className="registry-conflict-option-name">
                                    {suggestion.registryRecord.nominativo || '-'}
                                  </span>
                                  <span className="registry-conflict-option-type">
                                    {suggestion.registryRecord.tipologia || '-'}
                                  </span>
                                  <small>
                                    Riga {suggestion.registryRecord.source_row} / C.F.{' '}
                                    {suggestion.registryRecord.codice_fiscale ?? '-'} / P.IVA{' '}
                                    {suggestion.registryRecord.partita_iva ?? '-'}
                                  </small>
                                </>
                              ) : (
                                <>
                                  <span>{suggestion.value}</span>
                                  <small>{suggestion.count}x</small>
                                </>
                              )}
                            </button>
                          ))}
                        </div>
                      ) : null}
                      {isConflictOpen ? (
                        <div className="field-suggestions registry-conflict-options" role="listbox">
                          {fieldConflict.options.map((option, optionIndex) => (
                            <button
                              key={`${field.kind}-${option.registry_id}-${option.source_row}`}
                              type="button"
                              className={
                                optionIndex === safeHighlightedSuggestionIndex
                                  ? 'active'
                                  : ''
                              }
                              onMouseDown={(event) => {
                                event.preventDefault()
                                applyRegistryRecordToSelectedInvoice(
                                  fieldConflict.sourceFieldKind,
                                  option,
                                )
                                setActiveSuggestionFieldKind(null)
                              }}
                            >
                              <span className="registry-conflict-option-name">
                                {option.nominativo || '-'}
                              </span>
                              <span className="registry-conflict-option-type">
                                {option.tipologia || '-'}
                              </span>
                              <small>
                                Riga {option.source_row} / C.F.{' '}
                                {option.codice_fiscale ?? '-'} / P.IVA{' '}
                                {option.partita_iva ?? '-'}
                              </small>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </span>
                    {field.ocr_confidence !== null &&
                    field.ocr_confidence !== undefined ? (
                      <em>({formatOcrConfidence(field.ocr_confidence)})</em>
                    ) : null}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      </aside>
      {pendingRegistryRecord ? (
        <AddRegistryRecordConfirmDialog
          record={pendingRegistryRecord.record}
          onCancel={() => setPendingRegistryRecord(null)}
          onConfirm={confirmAddRegistryRecordFromField}
        />
      ) : null}
      {pendingInvoiceSaveValidation ? (
        <InvoiceSaveValidationDialog
          validation={pendingInvoiceSaveValidation}
          onCancel={() => setPendingInvoiceSaveValidation(null)}
          onContinue={continueInvoiceSaveAfterValidation}
          onAddRegistryRecord={addValidationRegistryRecord}
        />
      ) : null}
      {pendingInvoiceRegistrySaveValidation ? (
        <InvoiceSaveValidationDialog
          validation={pendingInvoiceRegistrySaveValidation}
          onCancel={() => setPendingInvoiceRegistrySaveValidation(null)}
          onContinue={continueInvoiceRegistrySaveAfterValidation}
          onAddRegistryRecord={() => undefined}
        />
      ) : null}
      {pendingBatchReviewAction ? (
        <BatchReviewActionConfirmDialog
          {...pendingBatchReviewAction}
          invoiceCount={pendingBatchReviewAction.invoices.length}
          onCancel={() => setPendingBatchReviewAction(null)}
          onConfirm={confirmBatchReviewAction}
        />
      ) : null}
      {isProcessingCancelConfirmOpen ? (
        <ProcessingCancelConfirmDialog
          onCancel={() => {
            setProcessingJobIdToCancel(null)
            setIsProcessingCancelConfirmOpen(false)
          }}
          onConfirm={confirmCancelProcessing}
        />
      ) : null}
      {processingJobToRemove ? (
        <DestructiveActionConfirmDialog
          title="Rimuovi lavorazione"
          message="Sei sicuro di voler rimuovere questa lavorazione dalla coda?"
          confirmLabel="Rimuovi"
          onCancel={() => setProcessingJobIdToRemove(null)}
          onConfirm={confirmRemoveProcessingJobFromQueue}
        />
      ) : null}
      {processingJobInfo ? (
        <ProcessingFilesDialog
          job={processingJobInfo}
          onClose={() => setProcessingJobInfo(null)}
        />
      ) : null}
      {isProcessingHistoryDeleteConfirmOpen ? (
        <DestructiveActionConfirmDialog
          title="Elimina cronologia"
          message={`Vuoi eliminare ${selectedProcessingHistoryIds.size} processi dalla cronologia? I PDF e le fatture non verranno modificati.`}
          confirmLabel="Elimina"
          onCancel={() => setIsProcessingHistoryDeleteConfirmOpen(false)}
          onConfirm={() => deleteProcessingHistory(false)}
        />
      ) : null}
      {isInvoiceDiscardConfirmOpen ? (
        <DestructiveActionConfirmDialog
          title="Scarta fattura"
          message={`Sei sicuro di voler spostare ${currentInvoice?.title ?? 'questa fattura'} tra le fatture scartate?`}
          confirmLabel="Scarta"
          onCancel={() => setIsInvoiceDiscardConfirmOpen(false)}
          onConfirm={confirmDiscardCurrentInvoice}
        />
      ) : null}
      {pendingSegmentationAction ? (
        <SegmentationConfirmDialog
          action={pendingSegmentationAction}
          onCancel={() => setPendingSegmentationAction(null)}
          onConfirm={confirmSegmentationAction}
        />
      ) : null}
      {segmentationProgressState ? (
        <SegmentationProgressDialog state={segmentationProgressState} />
      ) : null}
      {isClearReviewBatchesConfirmOpen ? (
        <DestructiveActionConfirmDialog
          title="Elimina revisioni"
          message="Sei sicuro di voler eliminare tutte le revisioni da completare? Registro fatture, archivi PDF e file sorgenti non verranno cancellati."
          confirmLabel="Elimina revisioni"
          onCancel={() => setIsClearReviewBatchesConfirmOpen(false)}
          onConfirm={confirmClearReviewBatches}
        />
      ) : null}
      {archiveReferenceAction ? (
        <ArchiveReferenceDialog
          action={archiveReferenceAction}
          onClose={() => setArchiveReferenceAction(null)}
          onOpenWindow={openArchiveReferenceWindow}
          onGoToArchive={goToArchiveReference}
        />
      ) : null}
      {archiveDeleteTarget ? (
        <DestructiveActionConfirmDialog
          title={archiveDeleteTarget.title}
          message={archiveDeleteTarget.message}
          confirmLabel="Elimina"
          onCancel={() => setArchiveDeleteTarget(null)}
          onConfirm={confirmDeleteArchive}
        />
      ) : null}
      {archiveReprocessDialog ? (
        <ArchiveReprocessDialog
          state={archiveReprocessDialog}
          onCancel={() => setArchiveReprocessDialog(null)}
          onConfirm={
            archiveReprocessDialog.target === 'sources'
              ? confirmReprocessSelectedArchivedSources
              : confirmReprocessSelectedArchivedInvoices
          }
          onClose={() => setArchiveReprocessDialog(null)}
        />
      ) : null}
      {registryDraftRecordToDelete ? (
        <DestructiveActionConfirmDialog
          title="Elimina riga anagrafica"
          message={`Sei sicuro di voler eliminare la riga ${registryDraftRecordToDelete.source_row}? La modifica sara' definitiva solo dopo Salva.`}
          confirmLabel="Elimina"
          onCancel={() => setRegistryDraftRecordIndexToDelete(null)}
          onConfirm={confirmDeleteRegistryDraftRecord}
        />
      ) : null}
      {isDocumentImportDialogOpen ? (
        <DocumentImportDialog
          isImporting={isDocumentImportRunning || isDocumentPickerOpen}
          onSelectFiles={addPdfImportSource}
          onSelectFolder={addFolderImportSource}
          onCancel={closeDocumentImportDialog}
        />
      ) : null}
      {feedbackExportState ? (
        <FeedbackExportDialog
          state={feedbackExportState}
          onCancel={() => setFeedbackExportState(null)}
          onConfirm={exportFeedbackToDesktop}
        />
      ) : null}
    </main>
  )
}

export default App

