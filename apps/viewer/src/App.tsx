
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import './App.css'
import type {
  BoundingBox,
  ImportedBatch,
  ImportedBatchImportPayload,
  ImportedBatchListPayload,
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
  updated_at?: string
  source_batch_id?: string
  document_id?: string
  source_file_name?: string
  archived_pdf_path?: string
  archived_pdf_url?: string
  archived_source_pdf_path?: string
  archived_source_pdf_url?: string
  invoice?: {
    title?: string
    fields?: InvoiceField[]
  }
}

type ArchivedSourceRecord = InvoiceArchiveRecord & {
  linked_invoice_count: number
}

type InvoiceArchiveFilters = {
  issuer: string
  condominium: string
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

type ProcessingJobPayload = {
  ok: boolean
  job_id: string
  batch_id: string
  status: 'queued' | 'processing' | 'paused' | 'completed' | 'cancelled' | 'removed' | 'error'
  message: string
  progress_current?: number
  progress_total?: number
  current_file?: string
  elapsed_seconds?: number
  estimated_remaining_seconds?: number | null
  cancel_requested?: boolean
  pause_requested?: boolean
  result?: {
    invoice_count: number
    review_batch_count: number
    errors?: string[]
  }
}

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
  kind: 'empty_fields' | 'invalid_fiscal' | 'new_registry_record' | 'registry_conflict'
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

function formatFieldValueForDisplay(field: Pick<InvoiceField, 'kind' | 'value'>) {
  if (!dateFieldKinds.has(field.kind)) {
    return field.value
  }

  return formatItalianDateValue(field.value)
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
  const issues: InvoiceSaveValidationIssue[] = []
  const fieldsByKind = new Map(invoice.fields.map((field) => [field.kind, field]))
  const emptyFields = expectedInvoiceFields
    .filter((expectedField) => !fieldsByKind.get(expectedField.kind)?.value.trim())
    .map((expectedField) => expectedField.label)

  if (emptyFields.length > 0) {
    issues.push({
      kind: 'empty_fields',
      title: 'Campi mancanti',
      detail: emptyFields.join(', '),
    })
  }

  for (const field of invoice.fields) {
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
  }

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

function fieldValueDiffersForRegistry(fieldKind: string, leftValue: string, rightValue: string) {
  return normalizeSuggestionValue(fieldKind, leftValue) !== normalizeSuggestionValue(fieldKind, rightValue)
}

function fieldWithRegistryValue(
  field: InvoiceField,
  nextValue: string,
  record: RegistryPreviewRecord,
): InvoiceField {
  const currentValue = field.value.trim()
  const replacedValue =
    field.registry_replaced_value ??
    (currentValue && fieldValueDiffersForRegistry(field.kind, currentValue, nextValue)
      ? currentValue
      : null)

  return {
    ...field,
    value: nextValue,
    role: 'registry_enriched',
    registry_id: record.registry_id,
    registry_source_row: record.source_row,
    registry_replaced_value: replacedValue,
  }
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
}: {
  records: InvoiceArchiveRecord[]
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
            <th>Salvata il</th>
            <th>Origine</th>
            <th>Fattura</th>
            <th>Sorgente</th>
            <th>Emittente</th>
            <th>Condominio</th>
            <th>Numero</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={record.record_id}>
              <td>
                {formatItalianDateValue((record.saved_at ?? '').slice(0, 10)) || '-'}
              </td>
              <td>{record.source_file_name || '-'}</td>
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
                  '-'
                )}
              </td>
              <td>{archiveFieldValue(record, 'nome_emittente') || '-'}</td>
              <td>{archiveFieldValue(record, 'condominio') || '-'}</td>
              <td>{archiveFieldValue(record, 'numero_fattura') || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})

const SourcePdfArchiveTable = memo(function SourcePdfArchiveTable({
  records,
  onReprocessSource,
}: {
  records: InvoiceArchiveRecord[]
  onReprocessSource: (record: ArchivedSourceRecord) => void
}) {
  const sourceRecords = uniqueArchivedSourceRecords(records)

  if (sourceRecords.length === 0) {
    return (
      <div className="registry-empty-state">
        <h2>Nessun file origine archiviato</h2>
        <p>I file origine compariranno qui dopo il salvataggio delle fatture.</p>
      </div>
    )
  }

  return (
    <div className="source-archive-sections">
      {sourceRecords.length > 0 ? (
        <section className="source-archive-section">
          <h2>File origine archiviati</h2>
          <div className="invoice-archive-table-wrap">
            <table className="invoice-archive-table">
              <thead>
                <tr>
                  <th>Archiviato il</th>
                  <th>File origine</th>
                  <th>PDF origine</th>
                  <th>Fatture collegate</th>
                  <th>Gruppo origine</th>
                  <th>Azioni</th>
                </tr>
              </thead>
              <tbody>
                {sourceRecords.map((record) => (
                  <tr key={record.archived_source_pdf_url ?? record.record_id}>
                    <td>
                      {formatItalianDateValue((record.saved_at ?? '').slice(0, 10)) || '-'}
                    </td>
                    <td>{record.source_file_name || '-'}</td>
                    <td>
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
                        '-'
                      )}
                    </td>
                    <td>{record.linked_invoice_count}</td>
                    <td>{record.source_batch_id || '-'}</td>
                    <td>
                      <button
                        className="invoice-archive-pdf-link"
                        onClick={() => onReprocessSource(record)}
                        disabled={!record.archived_source_pdf_path}
                      >
                        Riprocessa
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  )
})

const InvoiceRegistryTable = memo(function InvoiceRegistryTable({
  records,
  isEditMode,
  onUpdateRecordField,
}: {
  records: InvoiceArchiveRecord[]
  isEditMode: boolean
  onUpdateRecordField: (
    recordId: string,
    fieldKind: InvoiceRegistryEditableFieldKind,
    nextValue: string,
  ) => void
}) {
  if (records.length === 0) {
    return (
      <div className="registry-empty-state">
        <h2>Nessuna riga nel registro fatture</h2>
        <p>I dati compariranno qui dopo il salvataggio dalla revisione.</p>
      </div>
    )
  }

  return (
    <VirtualDataTable
      rows={records}
      headerCells={[
        'Salvata il',
        ...invoiceRegistryEditableFields.map((field) => field.label),
        'Origine',
        'PDF origine',
      ]}
      rowKey={(record) => record.record_id}
      className="invoice-registry-table-wrap"
      tableClassName="invoice-registry-table"
      rowClassName={(record) =>
        invoiceRegistryRecordHasEmptyFields(record)
          ? 'invoice-registry-row-incomplete'
          : ''
      }
      renderRow={(record) => {
          return [
            formatItalianDateValue((record.saved_at ?? '').slice(0, 10)) || '-',
            ...invoiceRegistryEditableFields.map((field) => {
                const value = archiveFieldValue(record, field.kind)
                const displayValue = dateFieldKinds.has(field.kind)
                  ? formatItalianDateValue(value)
                  : value

                return (
                  <span className={!value.trim() ? 'invoice-registry-empty-cell' : ''}>
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
                          const normalizedValue = normalizeDateValueForInput(
                            event.currentTarget.value,
                          )
                          if (dateFieldKinds.has(field.kind)) {
                            onUpdateRecordField(record.record_id, field.kind, normalizedValue)
                          }
                        }}
                      />
                    ) : (
                      displayValue || '-'
                    )}
                  </span>
                )
              }),
            record.source_file_name || '-',
            <>
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

  useEffect(() => {
    setSelectedMergeGroupKeys(new Set(allMergedGroupKeys))
  }, [allMergedGroupKeys])

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
            La fattura contiene dati da controllare. Puoi continuare comunque
            oppure sistemare prima le informazioni.
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
          <button className="registry-save-button" onClick={onContinue}>
            Continua e salva
          </button>
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

  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog batch-review-action-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>{isDiscard ? 'Elimina gruppo' : 'Salva gruppo'}</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="batch-review-action-body">
          <p>
            {isDiscard
              ? `Sei sicuro di voler eliminare ${invoiceCount} fatture dalla revisione?`
              : `Stai per salvare ${invoiceCount} fatture nel registro.`}
          </p>
          {!isDiscard && validationIssueCount > 0 ? (
            <div className="batch-review-validation-summary">
              <strong>Controlli da verificare</strong>
              <span>Campi mancanti: {emptyFieldIssueCount}</span>
              <span>C.F. o P.IVA sospetti: {invalidFiscalIssueCount}</span>
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
          >
            {isDiscard ? 'Elimina gruppo' : 'Continua e salva gruppo'}
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

function ImportedBatchDeleteConfirmDialog({
  batch,
  onCancel,
  onConfirm,
}: {
  batch: ImportedBatch
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog imported-batch-delete-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Elimina PDF caricati</h2>
          <button onClick={onCancel} aria-label="Chiudi">
            x
          </button>
        </header>
        <div className="batch-review-action-body">
          <p>
            Sei sicuro di voler eliminare questo gruppo di PDF caricati?
          </p>
          <div className="imported-batch-delete-summary">
            <strong>{batch.label}</strong>
            <span>Origine: {batch.source_label ?? batch.label}</span>
            <span>{batch.document_count} PDF caricati</span>
          </div>
          <p>
            Verranno rimossi solo i file copiati dentro l'app, non i file originali sul disco.
          </p>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel}>Annulla</button>
          <button className="registry-delete-button" onClick={onConfirm}>
            Elimina
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

function DocumentImportDialog({
  sources,
  isImporting,
  onAddFiles,
  onAddFolder,
  onRemoveSource,
  onCancel,
  onConfirm,
}: {
  sources: PendingImportSource[]
  isImporting: boolean
  onAddFiles: () => void
  onAddFolder: () => void
  onRemoveSource: (sourceId: string) => void
  onCancel: () => void
  onConfirm: () => void
}) {
  const totalPdfCount = sources.reduce(
    (total, source) => total + source.document_count,
    0,
  )

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
          <div className="document-import-actions">
            <button onClick={onAddFiles} disabled={isImporting}>
              Aggiungi PDF
            </button>
            <button onClick={onAddFolder} disabled={isImporting}>
              Aggiungi cartella
            </button>
          </div>
          {sources.length > 0 ? (
            <div className="document-import-source-list">
              {sources.map((source) => (
                <div className="document-import-source-item" key={source.source_id}>
                  <span>{source.kind === 'folder' ? 'Cartella' : 'PDF'}</span>
                  <strong title={source.label}>{source.label}</strong>
                  <span>{source.document_count} PDF</span>
                  <button
                    onClick={() => onRemoveSource(source.source_id)}
                    disabled={isImporting}
                    aria-label="Rimuovi sorgente"
                  >
                    x
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="document-import-empty">
              Aggiungi PDF o cartelle prima di importare.
            </p>
          )}
          {sources.length > 0 ? (
            <p className="document-import-total">
              Totale: {totalPdfCount} PDF da importare
            </p>
          ) : null}
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel} disabled={isImporting}>
            Annulla
          </button>
          <button
            className="registry-save-button"
            onClick={onConfirm}
            disabled={isImporting || sources.length === 0}
          >
            {isImporting ? 'Importazione...' : `Importa ${totalPdfCount} PDF`}
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

function formatOcrDisplay(invoice: ViewerInvoice | undefined) {
  const parts = [invoice?.ocrLang, invoice?.ocrProfile].filter(Boolean)

  if (parts.length === 0) {
    return 'non disponibile'
  }

  const variant =
    invoice?.variantLabel && invoice.variantLabel !== 'standard'
      ? ` / ${invoice.variantLabel}`
      : ''

  return `${parts.join('/')}${variant}`
}

function getInvoiceNumberForSort(invoice: ViewerInvoice) {
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

function sortedUniqueNumbers(values: number[]) {
  return [...new Set(values)].sort((left, right) => left - right)
}

function archiveFieldValue(record: InvoiceArchiveRecord, fieldKind: string) {
  return (
    record.invoice?.fields?.find((field) => field.kind === fieldKind)?.value?.trim() ??
    ''
  )
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

function filterInvoiceArchiveRecords(
  records: InvoiceArchiveRecord[],
  searchValue: string,
  filters: InvoiceArchiveFilters,
  sortKey: InvoiceArchiveSortKey,
) {
  const normalizedSearch = searchValue.trim().toLowerCase()
  const savedRecords = records.filter((record) => record.status === 'saved')
  const filteredRecords = savedRecords.filter((record) =>
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

  const issuer = archiveFieldValue(record, 'nome_emittente').toLowerCase()
  if (filters.issuer.trim() && !issuer.includes(filters.issuer.trim().toLowerCase())) {
    return false
  }

  const condominium = archiveFieldValue(record, 'condominio').toLowerCase()
  if (
    filters.condominium.trim() &&
    !condominium.includes(filters.condominium.trim().toLowerCase())
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
  return record.saved_at ?? record.updated_at ?? ''
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

function uniqueArchivedSourceRecords(records: InvoiceArchiveRecord[]) {
  const recordsBySource = new Map<string, ArchivedSourceRecord>()

  for (const record of records) {
    const key =
      record.archived_source_pdf_url ||
      record.archived_source_pdf_path ||
      `${record.source_batch_id ?? ''}:${record.document_id ?? ''}`
    if (!key.trim()) {
      continue
    }

    const existingRecord = recordsBySource.get(key)
    if (existingRecord) {
      existingRecord.linked_invoice_count += 1
      continue
    }

    recordsBySource.set(key, {
      ...record,
      linked_invoice_count: 1,
    })
  }

  return Array.from(recordsBySource.values()).sort((leftRecord, rightRecord) =>
    archiveSavedAt(rightRecord).localeCompare(archiveSavedAt(leftRecord)),
  )
}

function sourceArchiveProcessingStatusLabel(job: ProcessingJobPayload) {
  if (job.status === 'queued') {
    return 'in coda'
  }
  if (job.status === 'processing') {
    return job.pause_requested ? 'pausa richiesta' : 'in corso'
  }
  if (job.status === 'paused') {
    return 'in pausa'
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

  return expectedInvoiceFields.map((expectedField) => {
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
  })
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
    fields: sortInvoiceFieldsForDisplay(invoice.fields.map(getDisplayInvoiceField)),
  }))
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
  const documentViewportRef = useRef<HTMLDivElement | null>(null)
  const registryTableViewportRef = useRef<HTMLDivElement | null>(null)
  const registryFileInputRef = useRef<HTMLInputElement | null>(null)
  const [registryImportState, setRegistryImportState] =
    useState<RegistryImportState>(null)
  const [pdfImportState, setPdfImportState] = useState<PdfImportState>(null)
  const [isDocumentImportDialogOpen, setIsDocumentImportDialogOpen] = useState(false)
  const [pendingImportSources, setPendingImportSources] = useState<
    PendingImportSource[]
  >([])
  const [isDocumentImportRunning, setIsDocumentImportRunning] = useState(false)
  const [invoiceReviewActionState, setInvoiceReviewActionState] =
    useState<InvoiceReviewActionState>(null)
  const [processingJobsByBatchId, setProcessingJobsByBatchId] = useState<
    Record<string, ProcessingJobPayload>
  >({})
  const processingJobsPollIntervalRef = useRef<number | null>(null)
  const [processingJobIdToCancel, setProcessingJobIdToCancel] = useState<string | null>(null)
  const [isProcessingCancelConfirmOpen, setIsProcessingCancelConfirmOpen] =
    useState(false)
  const [processingJobIdToRemove, setProcessingJobIdToRemove] = useState<string | null>(null)
  const [batchIdToDelete, setBatchIdToDelete] = useState<string | null>(null)
  const [pendingImportSourceIdToRemove, setPendingImportSourceIdToRemove] =
    useState<string | null>(null)
  const [registryDraftRecordIndexToDelete, setRegistryDraftRecordIndexToDelete] =
    useState<number | null>(null)
  const [isInvoiceDiscardConfirmOpen, setIsInvoiceDiscardConfirmOpen] = useState(false)
  const [isRegistryDeleteConfirmOpen, setIsRegistryDeleteConfirmOpen] = useState(false)
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
  const [importedBatches, setImportedBatches] = useState<ImportedBatch[]>([])
  const [appView, setAppView] = useState<AppView>('review')
  const [invoiceArchiveRecords, setInvoiceArchiveRecords] = useState<
    InvoiceArchiveRecord[]
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
      issuer: '',
      condominium: '',
      dateFrom: '',
      dateTo: '',
    })
  const [invoiceArchiveSortKey, setInvoiceArchiveSortKey] =
    useState<InvoiceArchiveSortKey>('saved_at_desc')
  const [pdfArchiveViewMode, setPdfArchiveViewMode] =
    useState<PdfArchiveViewMode>('invoices')
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
  const selectedBatch = batches.find((batch) => batch.id === selectedBatchId)
  const processingJobs = useMemo(
    () =>
      Object.values(processingJobsByBatchId).sort((left, right) =>
        right.job_id.localeCompare(left.job_id),
      ),
    [processingJobsByBatchId],
  )
  const hasActiveProcessingJob = processingJobs.some(
    (job) => job.status === 'queued' || job.status === 'processing',
  )
  const shouldShowPdfImportState =
    pdfImportState !== null &&
    (pdfImportState.kind === 'error' || !hasActiveProcessingJob)
  const batchToDelete = importedBatches.find((batch) => batch.batch_id === batchIdToDelete)
  const processingJobToRemove = processingJobs.find(
    (job) => job.job_id === processingJobIdToRemove,
  )
  const pendingImportSourceToRemove = pendingImportSources.find(
    (source) => source.source_id === pendingImportSourceIdToRemove,
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
    () =>
      filterInvoiceArchiveRecords(
        invoiceArchiveRecords,
        invoiceArchiveSearch,
        invoiceArchiveFilters,
        invoiceArchiveSortKey,
      ),
    [
      invoiceArchiveRecords,
      invoiceArchiveSearch,
      invoiceArchiveFilters,
      invoiceArchiveSortKey,
    ],
  )
  const filteredInvoiceRegistryRecords = useMemo(
    () =>
      filterInvoiceArchiveRecords(
        isInvoiceRegistryEditMode ? invoiceRegistryDraftRecords : invoiceArchiveRecords,
        invoiceArchiveSearch,
        invoiceArchiveFilters,
        invoiceArchiveSortKey,
      ),
    [
      invoiceArchiveRecords,
      invoiceRegistryDraftRecords,
      isInvoiceRegistryEditMode,
      invoiceArchiveSearch,
      invoiceArchiveFilters,
      invoiceArchiveSortKey,
    ],
  )

  useEffect(() => {
    loadProcessedReviewBatches()
      .then((hasReviewBatches) => {
        if (!hasReviewBatches) {
          loadSampleViewerBatches()
        }
      })
      .catch(() => {
        loadSampleViewerBatches()
      })
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
        setSelectedBatchId(batchEntries[0]?.id ?? null)
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
    loadImportedBatches()
  }, [])

  useEffect(() => {
    pollProcessingJobs()
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
    loadRegistryStatus()
    loadRegistryRecords()
  }, [])

  useEffect(() => {
    if (registryPreviewRecords.length === 0) {
      return
    }

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
  }, [registryPreviewRecords])

  useEffect(() => {
    if (loadedManifests.length === 0) {
      return
    }

    const manifestInvoices = sortInvoicesForReview(
      loadedManifests.flatMap(({ manifest, documentEntry }) =>
        getInvoicesFromManifest(manifest, documentEntry),
      ),
    )
    if (registryPreviewRecords.length === 0) {
      setInvoices(manifestInvoices)
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
    setInvoices(registryMatchedInvoices.invoices)
    setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
    setSelectedInvoiceId((currentInvoiceId) =>
      currentInvoiceId ?? registryMatchedInvoices.invoices[0]?.invoiceId ?? null,
    )
  }, [loadedManifests])

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

  function loadBatch(batch: ViewerBatchIndexEntry) {
    fetch(`${batch.base_path}/viewer_index.json?v=${manifestCacheKey}`, {
      cache: 'no-store',
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`${batch.id}: HTTP ${response.status}`)
        }

        return response.json()
      })
      .then((index: ViewerDocumentIndex) => {
        loadDocumentEntries(index.documents)
      })
      .catch((error) => {
        setInvoices([])
        setSelectedInvoiceId(null)
        setManifestSourceFile(null)
        setLoadedManifests([])
        setManifestError(
          userErrorMessage(error, "Non riesco ad aprire questo gruppo di fatture."),
        )
      })
  }

  function loadProcessedReviewBatches() {
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

        setBatches(batchEntries)
        setSelectedBatchId(batchEntries[0].id)
        loadBatch(batchEntries[0])
        return true
      })
  }

  function selectBatch(batchId: string) {
    const batch = batches.find((candidate) => candidate.id === batchId)
    if (!batch) {
      return
    }

    setSelectedBatchId(batch.id)
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
    discardPendingImportSources(pendingImportSources)
    setIsDocumentImportDialogOpen(false)
    setPendingImportSources([])
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

  function addPendingImportSource(source: PendingImportSource) {
    if (
      pendingImportSources.some(
        (currentSource) => currentSource.duplicate_key === source.duplicate_key,
      )
    ) {
      discardPendingImportSources([source])
      setPdfImportState({
        kind: 'error',
        message: 'Questa sorgente e gia presente nella lista.',
      })
      return
    }

    setPdfImportState(null)
    setPendingImportSources((currentSources) => [...currentSources, source])
  }

  function addPdfImportSource() {
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
          addPendingImportSource(payload.source)
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
  }

  function addFolderImportSource() {
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
          addPendingImportSource(payload.source)
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
  }

  function removePendingImportSource(sourceId: string) {
    const removedSource = pendingImportSources.find(
      (source) => source.source_id === sourceId,
    )
    if (removedSource) {
      discardPendingImportSources([removedSource])
    }
    setPendingImportSources((currentSources) =>
      currentSources.filter((source) => source.source_id !== sourceId),
    )
    setPdfImportState(null)
  }

  function loadImportedBatches() {
    return fetch(`${registryApiBaseUrl}/batches`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ImportedBatchListPayload
      })
      .then((payload) => {
        setImportedBatches(payload.batches ?? [])
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `Gruppi PDF non disponibili. ${userErrorMessage(
            error,
            "Non riesco a leggere i PDF caricati nell'app.",
          )}`,
        })
      })
  }

  function confirmImportSources() {
    if (pendingImportSources.length === 0 || isDocumentImportRunning) {
      return
    }

    const totalPdfCount = pendingImportSources.reduce(
      (total, source) => total + source.document_count,
      0,
    )
    setIsDocumentImportRunning(true)
    setPdfImportState({
      kind: 'loading',
      message: `Importazione di ${totalPdfCount} PDF in corso...`,
    })

    fetch(`${registryApiBaseUrl}/batches/import-sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_ids: pendingImportSources.map((source) => source.source_id),
      }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ImportedBatchImportPayload
      })
      .then((payload) => {
        setImportedBatches((currentBatches) => [
          payload.batch,
          ...currentBatches.filter((batch) => batch.batch_id !== payload.batch.batch_id),
        ])
        setPendingImportSources([])
        setIsDocumentImportDialogOpen(false)
        setPdfImportState({
          kind: 'success',
          message: `PDF caricati: ${payload.batch.document_count}`,
        })
        window.setTimeout(() => {
          setPdfImportState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1400)
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `PDF non caricati. ${userErrorMessage(
            error,
            'Controlla che i file selezionati siano PDF validi.',
          )}`,
        })
      })
      .finally(() => {
        setIsDocumentImportRunning(false)
      })
  }

  function cancelDeleteImportedBatch() {
    setBatchIdToDelete(null)
  }

  function confirmDeleteImportedBatch() {
    const batchId = batchIdToDelete
    if (!batchId) {
      return
    }

    setPdfImportState({
      kind: 'loading',
      message: 'Eliminazione PDF caricati in corso...',
    })

    fetch(`${registryApiBaseUrl}/batches/${encodeURIComponent(batchId)}`, {
      method: 'DELETE',
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ImportedBatchListPayload
      })
      .then((payload) => {
        setImportedBatches(payload.batches ?? [])
        setProcessingJobsByBatchId((currentJobs) => {
          const nextJobs = { ...currentJobs }
          delete nextJobs[batchId]
          return nextJobs
        })
        setBatchIdToDelete(null)
        setPdfImportState({
          kind: 'success',
          message: 'PDF caricati eliminati.',
        })
        window.setTimeout(() => {
          setPdfImportState((currentState) =>
            currentState?.kind === 'success' ? null : currentState,
          )
        }, 1400)
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `PDF caricati non eliminati. ${userErrorMessage(
            error,
            "Non riesco a eliminare la copia interna dei PDF.",
          )}`,
        })
      })
  }

  function processImportedBatch(batchId: string) {
    setPdfImportState({
      kind: 'loading',
      message: 'Processamento OCR avviato...',
    })

    fetch(`${registryApiBaseUrl}/batches/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch_id: batchId }),
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(payload?.error ?? `HTTP ${response.status}`)
        }
        return payload as ProcessingJobPayload
      })
      .then((job) => {
        setProcessingJobsByBatchId((currentJobs) => ({
          ...currentJobs,
          [job.batch_id]: job,
        }))
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, 'Processamento OCR avviato...'),
        })
        startProcessingJobsPolling()
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `Processamento non avviato. ${userErrorMessage(
            error,
            'Non riesco ad avviare la lavorazione OCR.',
          )}`,
        })
      })
  }

  function reprocessArchivedSource(record: ArchivedSourceRecord) {
    if (!record.archived_source_pdf_path) {
      setPdfImportState({
        kind: 'error',
        message: 'PDF origine non disponibile per il riprocessamento.',
      })
      return
    }

    setPdfImportState({
      kind: 'loading',
      message: 'Riprocessamento OCR avviato...',
    })

    fetch(`${registryApiBaseUrl}/archive/sources/reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        archived_source_pdf_path: record.archived_source_pdf_path,
        source_file_name: record.source_file_name,
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
        setProcessingJobsByBatchId((currentJobs) => ({
          ...currentJobs,
          [job.batch_id]: job,
        }))
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, 'Riprocessamento OCR avviato...'),
        })
        startProcessingJobsPolling()
      })
      .catch((error) => {
        setPdfImportState({
          kind: 'error',
          message: `Riprocessamento non avviato. ${userErrorMessage(
            error,
            'Non riesco ad avviare la nuova lavorazione OCR.',
          )}`,
        })
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
            setProcessingJobsByBatchId({})
            stopProcessingJobsPolling()
            return
          }
          const jobsByBatchId = Object.fromEntries(
            payload.jobs.map((job) => [job.batch_id, job]),
          )
          setProcessingJobsByBatchId(jobsByBatchId)
          const activeJobs = payload.jobs.filter((job) =>
            job.status === 'queued' || job.status === 'processing',
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
      return `File corrente: ${job.current_file}`
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
    if (job.status === 'paused') {
      return 'Lavorazione in pausa.'
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
    return job.status === 'processing' && job.cancel_requested === true
  }

  function isProcessingPausePending(job: ProcessingJobPayload) {
    return job.status === 'processing' && job.pause_requested === true
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
        setProcessingJobsByBatchId((currentJobs) => ({
          ...currentJobs,
          [job.batch_id]: job,
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

  function pauseProcessingJob(jobId: string) {
    updateProcessingJobAction(jobId, 'pause', 'Pausa richiesta...')
  }

  function resumeProcessingJob(jobId: string) {
    updateProcessingJobAction(jobId, 'resume', 'Lavorazione rimessa in coda...')
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
    action: 'pause' | 'resume' | 'remove',
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
        setProcessingJobsByBatchId((currentJobs) => ({
          ...currentJobs,
          [job.batch_id]: job,
        }))
        setPdfImportState({
          kind: 'loading',
          message: processingJobMessage(job, fallbackMessage),
        })
        if (job.status === 'queued' || job.status === 'processing') {
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
      message: isDiscard ? 'Eliminazione fattura...' : 'Salvataggio fattura...',
    })

    persistInvoiceReviewAction(currentInvoice, action)
      .then(() => {
        removeInvoiceFromReview(currentInvoice.invoiceId)
        setInvoiceReviewActionState({
          kind: 'success',
          message: isDiscard ? 'Fattura eliminata dalla revisione.' : 'Fattura salvata.',
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
      return payload
    })
  }

  function continueInvoiceSaveAfterValidation() {
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
      newRegistryIssueCount: issues.filter((issue) => issue.kind === 'new_registry_record').length,
      registryConflictIssueCount: issues.filter((issue) => issue.kind === 'registry_conflict').length,
    })
  }

  function confirmBatchReviewAction() {
    if (!pendingBatchReviewAction) {
      return
    }

    const { action, invoices: targetInvoices } = pendingBatchReviewAction
    const isDiscard = action === 'discard'
    setPendingBatchReviewAction(null)
    setInvoiceReviewActionState({
      kind: isDiscard ? 'discarding' : 'saving',
      message: isDiscard
        ? `Eliminazione di ${targetInvoices.length} fatture...`
        : `Salvataggio di ${targetInvoices.length} fatture...`,
    })

    ;(async () => {
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
            ? `${targetInvoices.length} fatture eliminate dalla revisione.`
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

  function requestRemovePendingImportSource(sourceId: string) {
    setPendingImportSourceIdToRemove(sourceId)
  }

  function confirmRemovePendingImportSource() {
    if (!pendingImportSourceIdToRemove) {
      return
    }
    const sourceId = pendingImportSourceIdToRemove
    setPendingImportSourceIdToRemove(null)
    removePendingImportSource(sourceId)
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
    setAppView('archive')
    setInvoiceArchiveError(null)
    loadInvoiceArchive()
  }

  function openInvoiceRegistry() {
    setIsArchiveMenuOpen(false)
    setAppView('invoiceRegistry')
    setInvoiceArchiveError(null)
    loadInvoiceArchive()
  }

  function openProcessingJobsPage() {
    setIsArchiveMenuOpen(false)
    setAppView('processing')
    pollProcessingJobs()
  }

  function loadInvoiceArchive() {
    return fetch(`${registryApiBaseUrl}/invoices/registry`, { cache: 'no-store' })
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
        if (!isInvoiceRegistryEditMode) {
          setInvoiceRegistryDraftRecords(records)
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

  function startInvoiceRegistryEdit() {
    setInvoiceRegistryDraftRecords(invoiceArchiveRecords)
    setInvoiceRegistrySaveStatus(null)
    setIsInvoiceRegistryEditMode(true)
  }

  function cancelInvoiceRegistryEdit() {
    setInvoiceRegistryDraftRecords(invoiceArchiveRecords)
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
        setInvoiceRegistryDraftRecords(records)
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

  function loadDocumentEntries(documentEntries: ViewerDocumentIndexEntry[]) {
    if (documentEntries.length === 0) {
      setInvoices([])
      setSelectedInvoiceId(null)
      setManifestSourceFile(null)
      setLoadedManifests([])
      setManifestError('Nessun documento nel viewer_index.json')
      return
    }

    Promise.all(
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
        setLoadedManifests(loadedDocuments)
        setManifestSourceFile(
          loadedDocuments.length === 1
            ? loadedDocuments[0].manifest.source_file
            : `${loadedDocuments.length} documenti`,
        )
        setInvoices(registryMatchedInvoices.invoices)
        setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
        setSelectedInvoiceId(registryMatchedInvoices.invoices[0]?.invoiceId ?? null)
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setManifestError(null)
      })
      .catch((error) => {
        setInvoices([])
        setSelectedInvoiceId(null)
        setManifestSourceFile(null)
        setLoadedManifests([])
        setManifestError(
          userErrorMessage(error, 'Non riesco a leggere i dati di questa fattura.'),
        )
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

  function updateFieldValue(fieldKind: string, nextValue: string) {
    setActiveSuggestionFieldKind(fieldKind)
    setHighlightedSuggestionIndex(-1)
    setInvoices((currentInvoices) =>
      currentInvoices.map((invoice) => {
        if (invoice.invoiceId !== selectedInvoiceId) {
          return invoice
        }

        return {
          ...invoice,
          fields: invoice.fields.map((field) =>
            field.kind === fieldKind ? { ...field, value: nextValue } : field,
          ),
        }
      }),
    )
  }

  function normalizeDateFieldValue(fieldKind: string, value: string) {
    if (!dateFieldKinds.has(fieldKind)) {
      return value
    }

    const normalizedValue = normalizeDateValueForInput(value)
    if (normalizedValue !== value) {
      updateFieldValue(fieldKind, normalizedValue)
    }
    return normalizedValue
  }

  function acceptFieldSuggestion(fieldKind: string, suggestion: FieldSuggestion) {
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
      updateFieldValue(fieldKind, suggestion.value)
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

                return fieldWithRegistryValue(field, nextValue, record)
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
            }
          }),
        }
      }),
    )
  }

  function resetFieldValues() {
    if (loadedManifests.length > 0) {
      const manifestInvoices = sortInvoicesForReview(
        loadedManifests.flatMap(({ manifest, documentEntry }) =>
          getInvoicesFromManifest(manifest, documentEntry),
        ),
      )
      const registryMatchedInvoices = applyRegistryMatchesToInvoices(
        manifestInvoices,
        registryPreviewRecords,
      )
      setInvoices(registryMatchedInvoices.invoices)
      setRegistryFieldConflicts(registryMatchedInvoices.conflicts)
      setSelectedInvoiceId(registryMatchedInvoices.invoices[0]?.invoiceId ?? null)
      setSelectedFieldKind(null)
      setActiveSuggestionFieldKind(null)
      return
    }

    setInvoices([])
    setSelectedInvoiceId(null)
    setSelectedFieldKind(null)
    setActiveSuggestionFieldKind(null)
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

  function currentInvoiceBoundaryPageMoveState(
    direction: 'firstToPrevious' | 'lastToNext',
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
    if ((currentInvoice.pageNumbers?.length ?? 0) <= 1) {
      return {
        enabled: false,
        reason: 'Questa fattura ha una sola pagina: spostarla lascerebbe la fattura vuota.',
      }
    }

    const neighborIndex =
      direction === 'firstToPrevious' ? currentInvoiceIndex - 1 : currentInvoiceIndex + 1
    const neighborInvoice = invoices[neighborIndex]
    if (!neighborInvoice) {
      return {
        enabled: false,
        reason:
          direction === 'firstToPrevious'
            ? 'Non esiste una fattura precedente.'
            : 'Non esiste una fattura successiva.',
      }
    }
    if (
      !neighborInvoice.documentId ||
      !currentInvoice.documentId ||
      neighborInvoice.documentId !== currentInvoice.documentId
    ) {
      return {
        enabled: false,
        reason: 'Puoi spostare pagine solo tra fatture consecutive dello stesso file origine.',
      }
    }
    if (!currentInvoice.manifestInvoiceId || !neighborInvoice.manifestInvoiceId) {
      return {
        enabled: false,
        reason: 'Questa revisione non contiene i riferimenti necessari per spostare pagine.',
      }
    }

    return { enabled: true, reason: '' }
  }

  function moveCurrentInvoiceBoundaryPage(direction: 'firstToPrevious' | 'lastToNext') {
    if (!currentInvoice || !selectedBatch?.base_path || currentInvoiceIndex < 0) {
      return
    }

    const pageNumbers = [...(currentInvoice.pageNumbers ?? [])].sort((left, right) => left - right)
    if (pageNumbers.length <= 1) {
      return
    }

    const neighborIndex =
      direction === 'firstToPrevious' ? currentInvoiceIndex - 1 : currentInvoiceIndex + 1
    const neighborInvoice = invoices[neighborIndex]
    if (
      !neighborInvoice ||
      !currentInvoice.documentId ||
      !neighborInvoice.documentId ||
      currentInvoice.documentId !== neighborInvoice.documentId ||
      !currentInvoice.manifestInvoiceId ||
      !neighborInvoice.manifestInvoiceId
    ) {
      return
    }

    const documentId = currentInvoice.documentId
    const fromManifestInvoiceId = currentInvoice.manifestInvoiceId
    const toManifestInvoiceId = neighborInvoice.manifestInvoiceId
    const pageNumber =
      direction === 'firstToPrevious' ? pageNumbers[0] : pageNumbers[pageNumbers.length - 1]
    setInvoiceReviewActionState({
      kind: 'saving',
      message: 'Aggiornamento pagine fattura...',
    })

    fetch(`${registryApiBaseUrl}/viewer/move-page`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        selected_batch_id: selectedBatchId,
        review_batch_base_path: selectedBatch.base_path,
        document_id: documentId,
        from_invoice_id: fromManifestInvoiceId,
        to_invoice_id: toManifestInvoiceId,
        page_number: pageNumber,
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
        applyBoundaryPageMove(
          currentInvoice.invoiceId,
          neighborInvoice.invoiceId,
          fromManifestInvoiceId,
          toManifestInvoiceId,
          pageNumber,
        )
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setOriginalScanFieldKind(null)
        resetDocumentView()
        setInvoiceReviewActionState({
          kind: 'success',
          message: 'Pagine fattura aggiornate.',
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
          message: `Pagine non aggiornate. ${userErrorMessage(
            error,
            'Non riesco a spostare la pagina richiesta.',
          )}`,
        })
      })
  }

  function applyBoundaryPageMove(
    fromInvoiceId: string,
    toInvoiceId: string,
    fromManifestInvoiceId: string,
    toManifestInvoiceId: string,
    pageNumber: number,
  ) {
    setInvoices((currentInvoices) =>
      currentInvoices.map((invoice) => {
        if (invoice.invoiceId === fromInvoiceId) {
          const nextPageNumbers = (invoice.pageNumbers ?? []).filter(
            (currentPageNumber) => currentPageNumber !== pageNumber,
          )
          return {
            ...invoice,
            pageNumbers: nextPageNumbers,
            pages: invoice.pages.filter((page) => page.page !== pageNumber),
          }
        }

        if (invoice.invoiceId === toInvoiceId) {
          const movedPage = currentInvoices
            .find((candidate) => candidate.invoiceId === fromInvoiceId)
            ?.pages.find((page) => page.page === pageNumber)
          const nextPageNumbers = sortedUniqueNumbers([
            ...(invoice.pageNumbers ?? []),
            pageNumber,
          ])
          return {
            ...invoice,
            pageNumbers: nextPageNumbers,
            pages: movedPage
              ? [...invoice.pages, movedPage].sort((left, right) => left.page - right.page)
              : invoice.pages,
          }
        }

        return invoice
      }),
    )
    setLoadedManifests((currentLoadedManifests) =>
      currentLoadedManifests.map((entry) => {
        const manifestInvoices = entry.manifest.invoices
        if (!manifestInvoices) {
          return entry
        }

        return {
          ...entry,
          manifest: {
            ...entry.manifest,
            invoices: manifestInvoices.map((invoice) => {
              if (invoice.invoice_id === fromManifestInvoiceId) {
                return {
                  ...invoice,
                  page_numbers: invoice.page_numbers.filter(
                    (currentPageNumber) => currentPageNumber !== pageNumber,
                  ),
                }
              }
              if (invoice.invoice_id === toManifestInvoiceId) {
                return {
                  ...invoice,
                  page_numbers: sortedUniqueNumbers([...invoice.page_numbers, pageNumber]),
                }
              }
              return invoice
            }),
          },
        }
      }),
    )
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
        selectInvoiceByOffset(1)
        return
      }

      if (event.ctrlKey && event.key === 'ArrowUp') {
        event.preventDefault()
        selectInvoiceByOffset(-1)
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
                  : 'Cronologia'}
            </h2>
            <button className="registry-sidebar-back-button" onClick={closeInvoiceArchive}>
              Torna alle fatture
            </button>
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
              {importedBatches.length > 0 ? (
                <p className="pdf-import-summary">
                  {importedBatches.length} gruppi PDF caricati
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
              Cronologia
            </button>
            {batches.length > 0 ? (
              <label className="batch-select">
                <span>Da revisionare:</span>
                <select
                  value={selectedBatchId ?? ''}
                  onChange={(event) => selectBatch(event.target.value)}
                >
                  {batches.map((batch) => (
                    <option key={batch.id} value={batch.id}>
                      {reviewBatchDisplayLabel(batch.label)}
                    </option>
                  ))}
                </select>
              </label>
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
                  Elimina gruppo
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
        <section className="registry-page archive-page">
          <header>
            <div>
              <h1>Archivio PDF</h1>
              <p>
                {pdfArchiveViewMode === 'invoices'
                  ? `${filteredInvoiceArchiveRecords.length} PDF fatture salvate`
                  : `${uniqueArchivedSourceRecords(filteredInvoiceArchiveRecords).length} file origine archiviati`}
              </p>
            </div>
            <div className="archive-search">
              <input
                value={invoiceArchiveSearch}
                placeholder="Cerca PDF, origine, emittente"
                onChange={(event) => setInvoiceArchiveSearch(event.target.value)}
              />
            </div>
            <div className="registry-page-actions">
              <div className="archive-view-switch" role="tablist" aria-label="Vista archivio PDF">
                <button
                  className={pdfArchiveViewMode === 'invoices' ? 'active' : ''}
                  onClick={() => setPdfArchiveViewMode('invoices')}
                  role="tab"
                  aria-selected={pdfArchiveViewMode === 'invoices'}
                >
                  PDF fatture
                </button>
                <button
                  className={pdfArchiveViewMode === 'sources' ? 'active' : ''}
                  onClick={() => setPdfArchiveViewMode('sources')}
                  role="tab"
                  aria-selected={pdfArchiveViewMode === 'sources'}
                >
                  File origine
                </button>
              </div>
              <button onClick={loadInvoiceArchive}>Aggiorna</button>
            </div>
          </header>
          {invoiceArchiveError ? (
            <p className="registry-page-message">{invoiceArchiveError}</p>
          ) : pdfArchiveViewMode === 'sources' ? (
            <SourcePdfArchiveTable
              records={filteredInvoiceArchiveRecords}
              onReprocessSource={reprocessArchivedSource}
            />
          ) : (
            <InvoicePdfArchiveTable records={filteredInvoiceArchiveRecords} />
          )}
        </section>
      ) : appView === 'invoiceRegistry' ? (
        <section className="registry-page archive-page invoice-registry-page">
          <header>
            <div>
              <h1>Registro fatture</h1>
              <p>
                {filteredInvoiceRegistryRecords.length} righe
                {invoiceArchiveSearch.trim()
                  ? ` / ${invoiceArchiveRecords.filter((record) => record.status === 'saved').length} totali`
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
            <div className="archive-search">
              <input
                value={invoiceArchiveSearch}
                placeholder="Cerca emittente, condominio, numero, totale"
                onChange={(event) => setInvoiceArchiveSearch(event.target.value)}
              />
            </div>
            <div className="registry-page-actions">
              {isInvoiceRegistryEditMode ? (
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
              ) : (
                <button onClick={startInvoiceRegistryEdit}>Modifica</button>
              )}
              <button
                className="registry-download-button"
                onClick={downloadInvoiceArchiveExcel}
                aria-label="Esporta registro fatture in Excel"
                title="Esporta registro fatture in Excel"
                disabled={
                  invoiceArchiveRecords.filter((record) => record.status === 'saved')
                    .length === 0
                }
              >
                <DownloadIcon />
              </button>
              <button onClick={loadInvoiceArchive}>Aggiorna</button>
            </div>
          </header>
          <div className="archive-filter-bar invoice-registry-filter-bar">
            <label>
              <span>Emittente</span>
              <input
                value={invoiceArchiveFilters.issuer}
                onChange={(event) =>
                  setInvoiceArchiveFilters((currentFilters) => ({
                    ...currentFilters,
                    issuer: event.target.value,
                  }))
                }
              />
            </label>
            <label>
              <span>Condominio</span>
              <input
                value={invoiceArchiveFilters.condominium}
                onChange={(event) =>
                  setInvoiceArchiveFilters((currentFilters) => ({
                    ...currentFilters,
                    condominium: event.target.value,
                  }))
                }
              />
            </label>
            <label>
              <span>Da data</span>
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
            <label>
              <span>A data</span>
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
            <label>
              <span>Ordina</span>
              <select
                value={invoiceArchiveSortKey}
                onChange={(event) =>
                  setInvoiceArchiveSortKey(event.target.value as InvoiceArchiveSortKey)
                }
              >
                <option value="saved_at_desc">Salvataggio recente</option>
                <option value="saved_at_asc">Salvataggio meno recente</option>
                <option value="data_fattura_desc">Data fattura decrescente</option>
                <option value="data_fattura_asc">Data fattura crescente</option>
                <option value="emittente_asc">Emittente A-Z</option>
                <option value="condominio_asc">Condominio A-Z</option>
                <option value="totale_desc">Totale decrescente</option>
                <option value="totale_asc">Totale crescente</option>
              </select>
            </label>
            <button
              onClick={() => {
                setInvoiceArchiveSearch('')
                setInvoiceArchiveFilters({
                  issuer: '',
                  condominium: '',
                  dateFrom: '',
                  dateTo: '',
                })
                setInvoiceArchiveSortKey('saved_at_desc')
              }}
            >
              Pulisci filtri
            </button>
          </div>
          {invoiceArchiveError ? (
            <p className="registry-page-message">{invoiceArchiveError}</p>
          ) : (
            <InvoiceRegistryTable
              records={filteredInvoiceRegistryRecords}
              isEditMode={isInvoiceRegistryEditMode}
              onUpdateRecordField={updateInvoiceRegistryDraftField}
            />
          )}
        </section>
      ) : appView === 'processing' ? (
        <section className="registry-page archive-page processing-page">
          <header>
            <div>
              <h1>Cronologia</h1>
              <p>{processingJobs.length} lavorazioni registrate</p>
            </div>
          </header>
          <div className="processing-jobs-list">
            {processingJobs.length > 0 ? (
              processingJobs.map((job) => {
                const batch = importedBatches.find(
                  (candidate) => candidate.batch_id === job.batch_id,
                )
                const canPauseJob =
                  (job.status === 'queued' || job.status === 'processing') &&
                  !isProcessingCancellationPending(job) &&
                  !isProcessingPausePending(job)
                const canResumeJob = job.status === 'paused'
                const canRemoveJob = job.status === 'queued' || job.status === 'paused'
                const canCancelJob = job.status === 'processing' || job.status === 'paused'
                const canReprocessBatch =
                  Boolean(batch) &&
                  !canPauseJob &&
                  !canResumeJob &&
                  !canRemoveJob &&
                  !canCancelJob
                const isCancellationPending = isProcessingCancellationPending(job)
                const isPausePending = isProcessingPausePending(job)
                return (
                  <article className="processing-job-card" key={job.job_id}>
                    <div className="processing-job-card-header">
                      <div>
                        <strong>{batch?.label ?? job.batch_id}</strong>
                        <small>Origine: {batch?.source_label ?? batch?.label ?? job.batch_id}</small>
                      </div>
                      <span className={`processing-status-pill ${job.status}`}>
                        {sourceArchiveProcessingStatusLabel(job)}
                      </span>
                    </div>
                    <div className="processing-job-card-body">
                      <div className="processing-progress-summary">
                        <span>
                          {job.progress_current ?? 0} / {job.progress_total ?? 0}
                        </span>
                      </div>
                      <div className="processing-job-details">
                        <p>{processingJobDetailMessage(job)}</p>
                        {processingJobTimeMessage(job) ? (
                          <small>{processingJobTimeMessage(job)}</small>
                        ) : null}
                      </div>
                    </div>
                    {isCancellationPending ? (
                      <div className="processing-cancel-progress">
                        <span>Annullamento in corso...</span>
                        <div aria-hidden="true">
                          <i />
                        </div>
                      </div>
                    ) : null}
                    {isPausePending ? (
                      <div className="processing-cancel-progress processing-pause-progress">
                        <span>Pausa richiesta...</span>
                        <div aria-hidden="true">
                          <i />
                        </div>
                      </div>
                    ) : null}
                    <div className="processing-job-card-actions">
                      {canReprocessBatch ? (
                        <button
                          className="imported-batch-process-button secondary-action"
                          onClick={() => processImportedBatch(job.batch_id)}
                        >
                          Riprocessa gruppo
                        </button>
                      ) : null}
                      {canPauseJob ? (
                        <button
                          className="imported-batch-process-button secondary-action"
                          onClick={() => pauseProcessingJob(job.job_id)}
                        >
                          Pausa
                        </button>
                      ) : null}
                      {canResumeJob ? (
                        <button
                          className="imported-batch-process-button secondary-action"
                          onClick={() => resumeProcessingJob(job.job_id)}
                        >
                          Riprendi
                        </button>
                      ) : null}
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
                  </article>
                )
              })
            ) : (
              <div className="registry-empty-state">
                <h2>Nessuna cronologia</h2>
                <p>Le lavorazioni compariranno qui dopo l'avvio dall'archivio sorgenti.</p>
              </div>
            )}
          </div>
        </section>
      ) : (
      <section className="document-panel">
        <header>
          <div>
            <h1>{currentInvoice?.title ?? 'Nessuna fattura selezionata'}</h1>
            <p>
              File origine:{' '}
              {fileNameFromPath(
                currentInvoice?.sourceFile ?? manifestSourceFile,
              ) ?? 'non disponibile'}
            </p>
            <p>
              OCR: {manifestError ?? formatOcrDisplay(currentInvoice)}
              {currentInvoice?.fallbackOcrEventCount
                ? ` / fallback ${currentInvoice.fallbackOcrEventCount}`
                : ''}
            </p>
            {currentInvoice ? (() => {
              const firstToPreviousState =
                currentInvoiceBoundaryPageMoveState('firstToPrevious')
              const lastToNextState = currentInvoiceBoundaryPageMoveState('lastToNext')
              const disabledReason =
                !firstToPreviousState.enabled && !lastToNextState.enabled
                  ? firstToPreviousState.reason
                  : ''

              return (
                <details className="invoice-page-reorder-menu">
                  <summary>Riordina pagine</summary>
                  <div className="invoice-page-boundary-actions">
                    <button
                      type="button"
                      onClick={() => moveCurrentInvoiceBoundaryPage('firstToPrevious')}
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
                      onClick={() => moveCurrentInvoiceBoundaryPage('lastToNext')}
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
                  </div>
                </details>
              )
            })() : null}

          </div>

          <div className="zoom-controls">
            <button className="registry-import-button" onClick={openRegistryPreview}>
              Anagrafica clienti
            </button>
            <button onClick={zoomOut}>-</button>
            <label>
              <input
                type="number"
                min="35"
                max="150"
                step="5"
                value={zoomInput}
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
            <button onClick={zoomIn}>+</button>
            <button onClick={resetDocumentView}>Ripristina</button>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showTokenBoxes}
                onChange={(event) => setShowTokenBoxes(event.target.checked)}
              />
              Parole
            </label>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showCropBoxes}
                onChange={(event) => setShowCropBoxes(event.target.checked)}
              />
              Ritagli
            </label>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showTableBoxes}
                onChange={(event) => setShowTableBoxes(event.target.checked)}
              />
              Tabelle
            </label>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showBoxLabels}
                onChange={(event) => setShowBoxLabels(event.target.checked)}
              />
              Etichette
            </label>
          </div>
        </header>

        <div className="document-viewport" ref={documentViewportRef}>
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

      <aside className="invoice-fields-panel">
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
                Boolean(field.registry_replaced_value) &&
                Boolean(originalScanReviewKey) &&
                !reviewedOriginalScanFields[originalScanReviewKey as string]
              const fieldSuggestions = getFieldSuggestions(
                suggestionIndex,
                field.kind,
                field.value,
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
                        title={`Valore letto dalla scan: ${field.registry_replaced_value}`}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          showOriginalScanField(field)
                        }}
                      >
                        <EyeIcon />
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
                            const normalizedValue = normalizeDateFieldValue(
                              field.kind,
                              event.currentTarget.value,
                            )
                            applyExactRegistryFieldMatch(field.kind, normalizedValue)
                            setActiveSuggestionFieldKind(null)
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
                              const normalizedValue = normalizeDateFieldValue(
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
                              key={`${field.kind}-${suggestion.value}`}
                              type="button"
                              className={
                                suggestionIndex === safeHighlightedSuggestionIndex
                                  ? 'active'
                                  : ''
                              }
                              onMouseDown={(event) => {
                                event.preventDefault()
                                acceptFieldSuggestion(field.kind, suggestion)
                              }}
                            >
                              <span>{suggestion.value}</span>
                              <small>{suggestion.count}x</small>
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
                              <span>{option.nominativo}</span>
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
      {batchToDelete ? (
        <ImportedBatchDeleteConfirmDialog
          batch={batchToDelete}
          onCancel={cancelDeleteImportedBatch}
          onConfirm={confirmDeleteImportedBatch}
        />
      ) : null}
      {isInvoiceDiscardConfirmOpen ? (
        <DestructiveActionConfirmDialog
          title="Elimina fattura"
          message={`Sei sicuro di voler eliminare ${currentInvoice?.title ?? 'questa fattura'} dalla revisione?`}
          confirmLabel="Elimina"
          onCancel={() => setIsInvoiceDiscardConfirmOpen(false)}
          onConfirm={confirmDiscardCurrentInvoice}
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
          sources={pendingImportSources}
          isImporting={isDocumentImportRunning}
          onAddFiles={addPdfImportSource}
          onAddFolder={addFolderImportSource}
          onRemoveSource={requestRemovePendingImportSource}
          onCancel={closeDocumentImportDialog}
          onConfirm={confirmImportSources}
        />
      ) : null}
      {pendingImportSourceToRemove ? (
        <DestructiveActionConfirmDialog
          title="Rimuovi sorgente"
          message={`Sei sicuro di voler rimuovere ${pendingImportSourceToRemove.label} dai documenti da importare?`}
          confirmLabel="Rimuovi"
          onCancel={() => setPendingImportSourceIdToRemove(null)}
          onConfirm={confirmRemovePendingImportSource}
        />
      ) : null}
    </main>
  )
}

export default App

