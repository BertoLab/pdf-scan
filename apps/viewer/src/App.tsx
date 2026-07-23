
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import './App.css'
import type {
  BoundingBox,
  InvoiceField,
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

const expectedInvoiceFields: Pick<InvoiceField, 'kind' | 'label'>[] = [
  { kind: 'nome_emittente', label: 'Nome emittente' },
  { kind: 'condominio', label: 'Condominio' },
  { kind: 'codice_fiscale', label: 'C.F. Condominio' },
  { kind: 'codice_fiscale_emittente', label: 'Codice fiscale emittente' },
  { kind: 'p_iva', label: 'P.IVA' },
  { kind: 'numero_fattura', label: 'Numero fattura' },
  { kind: 'data_fattura', label: 'Data fattura' },
  { kind: 'pagata_il', label: 'Pagata il' },
  { kind: 'economic_summary.totale_fattura.value', label: 'Totale fattura' },
  { kind: 'economic_summary.ritenuta_percentuale', label: 'Ritenuta' },
  {
    kind: 'economic_summary.totale_netto_fattura.value',
    label: 'Totale netto fattura',
  },
]

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
}

type AppView = 'review' | 'registry'

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

function formatFieldValueForDisplay(field: Pick<InvoiceField, 'kind' | 'value'>) {
  if (!dateFieldKinds.has(field.kind)) {
    return field.value
  }

  return formatItalianDateValue(field.value)
}

function getDisplayInvoiceField(field: InvoiceField): InvoiceField {
  return {
    ...field,
    value: formatFieldValueForDisplay(field),
  }
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

function buildSuggestionIndex(invoices: ViewerInvoice[]): SuggestionIndex {
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

  for (const invoice of invoices) {
    for (const record of suggestionRecordsFromInvoice(invoice)) {
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

function suggestionRecordsFromInvoice(invoice: ViewerInvoice): SuggestionRecord[] {
  const fieldsByKind = Object.fromEntries(
    invoice.fields
      .map((field) => [field.kind, field.value.trim()] as const)
      .filter(([, value]) => value),
  )

  return relatedFieldGroups
    .map((group) => ({
      fields: Object.fromEntries(
        group
          .map((kind) => [kind, fieldsByKind[kind] ?? ''] as const)
          .filter(([, value]) => value),
      ),
    }))
    .filter((record) => Object.keys(record.fields).length > 0)
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
  onClearHighlight: () => void
}

const RegistryTable = memo(function RegistryTable({
  records,
  isEditMode,
  highlightedRecordKey,
  tableViewportRef,
  onUpdateRecord,
  onClearHighlight,
}: RegistryTableProps) {
  const [scrollState, setScrollState] = useState({ scrollTop: 0, viewportHeight: 620 })
  const firstVisibleRow = Math.max(
    0,
    Math.floor(scrollState.scrollTop / REGISTRY_TABLE_ROW_HEIGHT) -
      REGISTRY_TABLE_OVERSCAN_ROWS,
  )
  const visibleRowCount =
    Math.ceil(scrollState.viewportHeight / REGISTRY_TABLE_ROW_HEIGHT) +
    REGISTRY_TABLE_OVERSCAN_ROWS * 2
  const lastVisibleRow = Math.min(records.length, firstVisibleRow + visibleRowCount)
  const visibleRecords = records.slice(firstVisibleRow, lastVisibleRow)

  return (
    <div
      className="registry-preview-table-wrap registry-preview-table-wrap-virtual"
      ref={tableViewportRef}
      onMouseDown={onClearHighlight}
      onScroll={(event) => {
        setScrollState({
          scrollTop: event.currentTarget.scrollTop,
          viewportHeight: event.currentTarget.clientHeight,
        })
      }}
    >
      <div
        className="registry-preview-table registry-preview-table-virtual"
        style={{
          height:
            REGISTRY_TABLE_HEADER_HEIGHT +
            records.length * REGISTRY_TABLE_ROW_HEIGHT,
        }}
      >
        <div className="registry-preview-table-head">
          <div>Codice anagrafica</div>
          <div>Nominativo</div>
          <div>Codice fiscale</div>
          <div>Partita IVA</div>
          <div>Tipologia</div>
          <div>Avvisi</div>
        </div>
        {visibleRecords.map((record, visibleRecordIndex) => {
          const recordIndex = firstVisibleRow + visibleRecordIndex

          return (
            <div
              key={`${record.registry_id}-${record.source_row}-${recordIndex}`}
              className={[
                'registry-preview-table-row',
                highlightedRecordKey === registryRecordKey(record)
                  ? 'registry-search-highlight'
                  : '',
              ]
                .filter(Boolean)
                .join(' ')}
              data-registry-record-key={registryRecordKey(record)}
              style={{
                transform: `translateY(${
                  REGISTRY_TABLE_HEADER_HEIGHT +
                  recordIndex * REGISTRY_TABLE_ROW_HEIGHT
                }px)`,
              }}
            >
              <div>
                {isEditMode ? (
                  <input
                    value={record.codice_anagrafica ?? ''}
                    onChange={(event) =>
                      onUpdateRecord(recordIndex, 'codice_anagrafica', event.target.value)
                    }
                  />
                ) : (
                  record.codice_anagrafica ?? ''
                )}
              </div>
              <div>
                {isEditMode ? (
                  <input
                    value={record.nominativo}
                    onChange={(event) =>
                      onUpdateRecord(recordIndex, 'nominativo', event.target.value)
                    }
                  />
                ) : (
                  record.nominativo
                )}
              </div>
              <div>
                {isEditMode ? (
                  <input
                    value={record.codice_fiscale ?? ''}
                    onChange={(event) =>
                      onUpdateRecord(recordIndex, 'codice_fiscale', event.target.value)
                    }
                  />
                ) : (
                  record.codice_fiscale ?? ''
                )}
              </div>
              <div>
                {isEditMode ? (
                  <input
                    value={record.partita_iva ?? ''}
                    onChange={(event) =>
                      onUpdateRecord(recordIndex, 'partita_iva', event.target.value)
                    }
                  />
                ) : (
                  record.partita_iva ?? ''
                )}
              </div>
              <div>
                {isEditMode ? (
                  <input
                    value={record.tipologia}
                    onChange={(event) =>
                      onUpdateRecord(recordIndex, 'tipologia', event.target.value)
                    }
                  />
                ) : (
                  record.tipologia
                )}
              </div>
              <div>{record.warnings.join(', ')}</div>
            </div>
          )
        })}
      </div>
    </div>
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

type RegistryCleaningConfirmDialogProps = {
  onConfirm: () => void
  onCancel: () => void
  isRunning: boolean
}

function RegistryCleaningConfirmDialog({
  onConfirm,
  onCancel,
  isRunning,
}: RegistryCleaningConfirmDialogProps) {
  return (
    <div className="registry-dialog-backdrop" role="presentation">
      <section className="registry-dialog registry-cleaning-dialog" role="dialog" aria-modal="true">
        <header>
          <h2>Pulizia anagrafica</h2>
          <button onClick={onCancel} aria-label="Chiudi" disabled={isRunning}>
            x
          </button>
        </header>
        <div className="registry-cleaning-body">
          <p>
            Verranno cercati duplicati con stesso codice fiscale o stessa partita IVA.
          </p>
          <p>
            I record chiaramente uguali verranno uniti tenendo la versione piu completa.
            I casi con dati diversi resteranno separati e saranno marcati come da controllare.
          </p>
          <p>Il JSON anagrafica verra aggiornato solo dopo questa conferma.</p>
        </div>
        <div className="registry-cleaning-actions">
          <button onClick={onCancel} disabled={isRunning}>Annulla</button>
          <button className="registry-save-button" onClick={onConfirm} disabled={isRunning}>
            {isRunning ? 'Pulizia in corso...' : 'Conferma'}
          </button>
        </div>
      </section>
    </div>
  )
}

type RegistryCleaningReportDialogProps = {
  report: RegistryCleaningReport
  onClose: () => void
}

function RegistryCleaningReportDialog({
  report,
  onClose,
}: RegistryCleaningReportDialogProps) {
  const mergedGroups = report.groups.filter((group) => group.status === 'merged')
  const conflictGroups = report.groups.filter((group) => group.status === 'conflict')

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
          {mergedGroups.length > 0 ? (
            <RegistryCleaningReportGroupList title="Righe unite" groups={mergedGroups} />
          ) : (
            <p>Nessuna riga eliminata automaticamente.</p>
          )}
          {conflictGroups.length > 0 ? (
            <RegistryCleaningReportGroupList
              title="Duplicati non uniti per dati diversi"
              groups={conflictGroups}
            />
          ) : null}
        </div>
        <div className="registry-cleaning-actions">
          <button className="registry-save-button" onClick={onClose}>OK</button>
        </div>
      </section>
    </div>
  )
}

function RegistryCleaningReportGroupList({
  title,
  groups,
}: {
  title: string
  groups: RegistryCleaningReportGroup[]
}) {
  return (
    <section className="registry-cleaning-report-section">
      <h3>{title}</h3>
      {groups.map((group, groupIndex) => (
        <article
          className="registry-cleaning-report-group"
          key={`${group.status}-${group.source_rows.join('-')}-${groupIndex}`}
        >
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
    .filter((candidate) => candidate.normalized !== normalizedInput)
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
): FieldEvidence | null {
  if (!field || !currentInvoice) {
    return null
  }

  if (field.page && field.bbox) {
    return {
      page: field.page,
      bbox: field.bbox,
      value: formatFieldValueForDisplay(field),
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
  if (!manifest.invoices?.length) {
    return [
      {
        invoiceId: `${documentEntry.id}:invoice-001`,
        title: 'Fattura 001',
        documentId: documentEntry.id,
        variantLabel: getVariantLabel(documentEntry.id),
        sourceFile: manifest.source_file,
        ocrLang: manifest.ocr_lang,
        ocrProfile: manifest.ocr_profile,
        fallbackOcrEventCount: manifest.fallback_ocr_events?.length ?? 0,
        basePath: documentEntry.base_path,
        pages: manifest.pages,
        fields: getFieldsFromManifest(manifest),
      },
    ]
  }

  return manifest.invoices.map((invoice) => ({
    invoiceId: `${documentEntry.id}:${invoice.invoice_id}`,
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
    pages: manifest.pages.filter((page) =>
      invoice.page_numbers.includes(page.page),
    ),
    fields: invoice.fields.map(getDisplayInvoiceField),
  }))
}

function App() {
  const [invoices, setInvoices] = useState<ViewerInvoice[]>([])
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<string | null>(null)
  const [pageScale, setPageScale] = useState(BASE_PAGE_SCALE)
  const [zoomInput, setZoomInput] = useState('45')
  const [selectedFieldKind, setSelectedFieldKind] = useState<string | null>(null)
  const [showAllTargetBoxes, setShowAllTargetBoxes] = useState(false)
  const [showTokenBoxes, setShowTokenBoxes] = useState(false)
  const [showCropBoxes, setShowCropBoxes] = useState(false)
  const [showTableBoxes, setShowTableBoxes] = useState(false)
  const [showBoxLabels, setShowBoxLabels] = useState(false)
  const documentViewportRef = useRef<HTMLDivElement | null>(null)
  const registryTableViewportRef = useRef<HTMLDivElement | null>(null)
  const registryFileInputRef = useRef<HTMLInputElement | null>(null)
  const [registryImportStatus, setRegistryImportStatus] = useState<string | null>(null)
  const [isRegistryDialogOpen, setIsRegistryDialogOpen] = useState(false)
  const [registryStatus, setRegistryStatus] = useState<RegistryStatus | null>(null)
  const [registryStatusError, setRegistryStatusError] = useState<string | null>(null)
  const [registryPreviewRecords, setRegistryPreviewRecords] = useState<
    RegistryPreviewRecord[]
  >([])
  const [registryDraftRecords, setRegistryDraftRecords] = useState<
    RegistryPreviewRecord[]
  >([])
  const [isRegistryEditMode, setIsRegistryEditMode] = useState(false)
  const [registrySaveStatus, setRegistrySaveStatus] = useState<string | null>(null)
  const [registryPreviewError, setRegistryPreviewError] = useState<string | null>(null)
  const [isRegistryCleaningConfirmOpen, setIsRegistryCleaningConfirmOpen] = useState(false)
  const [isRegistryCleaningRunning, setIsRegistryCleaningRunning] = useState(false)
  const [registryCleaningReport, setRegistryCleaningReport] =
    useState<RegistryCleaningReport | null>(null)
  const [highlightedRegistryRecordKey, setHighlightedRegistryRecordKey] = useState<string | null>(null)
  const currentInvoice = invoices.find(
    (invoice) => invoice.invoiceId === selectedInvoiceId,
  )
  const displayedFields = currentInvoice?.fields ?? []
  const selectedField = displayedFields.find(
    (field) => field.kind === selectedFieldKind,
  )
  const selectedFieldEvidence = getFieldEvidence(selectedField, currentInvoice)
  const zoomPercent = Math.round(pageScale * 100)
  const [manifestSourceFile, setManifestSourceFile] = useState<string | null>(null)
  const [loadedManifests, setLoadedManifests] = useState<
    { manifest: ViewerManifest; documentEntry: ViewerDocumentIndexEntry }[]
  >([])
  const [manifestError, setManifestError] = useState<string | null>(null)
  const [batches, setBatches] = useState<ViewerBatchIndexEntry[]>([])
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)
  const [appView, setAppView] = useState<AppView>('review')
  const [activeSuggestionFieldKind, setActiveSuggestionFieldKind] = useState<string | null>(null)
  const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(0)
  const suggestionIndex = useMemo(() => buildSuggestionIndex(invoices), [invoices])
  const registryTableRecords = isRegistryEditMode ? registryDraftRecords : registryPreviewRecords
  const registrySearchIndex = useMemo(
    () => buildRegistrySearchIndex(registryPreviewRecords),
    [registryPreviewRecords],
  )

  useEffect(() => {
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
        setManifestError('Nessun batch nel viewer_batches.json')
      })
      .catch(() => {
        loadLegacyDocumentIndex()
      })
  }, [])

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
          `Impossibile caricare viewer_batches.json o viewer_index.json: ${String(
            error,
          )}`,
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
        setManifestError(String(error))
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

  function openRegistryFilePicker() {
    registryFileInputRef.current?.click()
  }

  function openRegistryDialog() {
    setIsRegistryDialogOpen(true)
    loadRegistryStatus()
  }

  function closeRegistryDialog() {
    setIsRegistryDialogOpen(false)
  }

  function openRegistryPreview() {
    setAppView('registry')
    setIsRegistryDialogOpen(false)
    setRegistryPreviewError(null)
    setHighlightedRegistryRecordKey(null)
    fetch(`${registryApiBaseUrl}/registry/records`, { cache: 'no-store' })
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
        setRegistryPreviewError(`Anteprima non disponibile: ${String(error)}`)
      })
  }

  function closeRegistryPreview() {
    setAppView('review')
    setIsRegistryEditMode(false)
  }

  function enterRegistryEditMode() {
    setRegistryDraftRecords(registryPreviewRecords.map((record) => ({ ...record })))
    setRegistrySaveStatus(null)
    setIsRegistryEditMode(true)
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

  const clearRegistrySearchHighlight = useCallback(() => {
    setHighlightedRegistryRecordKey(null)
  }, [])

  function saveRegistryEdits() {
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
        applyRegistryRecordsToInvoiceFields(payload.records)
        setIsRegistryEditMode(false)
        setRegistrySaveStatus('Anagrafica salvata')
      })
      .catch((error) => {
        setRegistrySaveStatus(`Anagrafica non salvata: ${String(error)}`)
      })
  }

  function runRegistryCleaning() {
    setIsRegistryCleaningRunning(true)
    setRegistrySaveStatus('Pulizia anagrafica in corso...')
    fetch(`${registryApiBaseUrl}/registry/clean-duplicates`, {
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
        applyRegistryRecordsToInvoiceFields(payload.records)
        setIsRegistryEditMode(false)
        setIsRegistryCleaningConfirmOpen(false)
        setRegistryCleaningReport(payload.cleaning_report ?? null)
        setRegistrySaveStatus('Pulizia anagrafica completata')
      })
      .catch((error) => {
        setRegistrySaveStatus(`Pulizia anagrafica non completata: ${String(error)}`)
      })
      .finally(() => {
        setIsRegistryCleaningRunning(false)
      })
  }

  function applyRegistryRecordsToInvoiceFields(records: RegistryPreviewRecord[]) {
    const recordsByKey = new Map(
      records.map((record) => [
        `${record.registry_id}:${record.source_row}`,
        record,
      ]),
    )

    setInvoices((currentInvoices) =>
      currentInvoices.map((invoice) => ({
        ...invoice,
        fields: invoice.fields.map((field) => {
          if (field.role !== 'registry_enriched' || !field.registry_id) {
            return field
          }

          const record = recordsByKey.get(
            `${field.registry_id}:${field.registry_source_row ?? ''}`,
          )
          if (!record) {
            return field
          }

          const nextValue = registryFieldValueForInvoiceKind(record, field.kind)
          if (!nextValue) {
            return field
          }

          return {
            ...field,
            value: nextValue,
          }
        }),
      })),
    )
  }

  function downloadRegistryFile() {
    if (!registryStatus?.file_url) {
      return
    }

    const link = document.createElement('a')
    link.href = `${registryApiBaseUrl}${registryStatus.file_url}`
    link.download = registryStatus.file_name ?? 'elenco_anagrafiche.xlsx'
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
        setRegistryStatusError(`Stato anagrafica non disponibile: ${String(error)}`)
      })
  }

  function importRegistryFile(file: File | undefined) {
    if (!file) {
      return
    }

    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setRegistryImportStatus('Seleziona un file Excel .xlsx')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    setRegistryImportStatus('Caricamento anagrafica...')

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
        setRegistryImportStatus(
          registryStatus?.has_file
            ? `Anagrafica sostituita: ${payload.record_count} record`
            : `Anagrafica caricata: ${payload.record_count} record`,
        )
        loadRegistryStatus()
        if (appView === 'registry') {
          openRegistryPreview()
        }
      })
      .catch((error) => {
        setRegistryImportStatus(`Anagrafica non caricata: ${String(error)}`)
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
        setLoadedManifests(loadedDocuments)
        setManifestSourceFile(
          loadedDocuments.length === 1
            ? loadedDocuments[0].manifest.source_file
            : `${loadedDocuments.length} documenti`,
        )
        setInvoices(manifestInvoices)
        setSelectedInvoiceId(manifestInvoices[0]?.invoiceId ?? null)
        setSelectedFieldKind(null)
        setActiveSuggestionFieldKind(null)
        setManifestError(null)
      })
      .catch((error) => {
        setInvoices([])
        setSelectedInvoiceId(null)
        setManifestSourceFile(null)
        setLoadedManifests([])
        setManifestError(String(error))
      })
  }

  function updateFieldValue(fieldKind: string, nextValue: string) {
    setActiveSuggestionFieldKind(fieldKind)
    setHighlightedSuggestionIndex(0)
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

  function acceptFieldSuggestion(fieldKind: string, suggestion: FieldSuggestion) {
    if (relatedFieldGroups.some((group) => group.includes(fieldKind))) {
      completeRelatedFieldValues(fieldKind, suggestion)
    } else {
      updateFieldValue(fieldKind, suggestion.value)
    }
    setActiveSuggestionFieldKind(null)
    setHighlightedSuggestionIndex(0)
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
      setInvoices(manifestInvoices)
      setSelectedInvoiceId(manifestInvoices[0]?.invoiceId ?? null)
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

    const evidence = getFieldEvidence(field, currentInvoice)

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
  }, [invoices, selectedFieldKind, selectedInvoiceId])

  return (
    <main className={appView === 'registry' ? 'registry-page-mode' : ''}>
      <aside>
        <h2>Fatture</h2>
        {batches.length > 0 ? (
          <label className="batch-select">
            <span>Batch</span>
            <select
              value={selectedBatchId ?? ''}
              onChange={(event) => selectBatch(event.target.value)}
            >
              {batches.map((batch) => (
                <option key={batch.id} value={batch.id}>
                  {batch.label}
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
      </aside>

      {appView === 'registry' ? (
        <section className="registry-page">
          <header>
            <div>
              <h1>Anagrafica clienti</h1>
              <p>
                File caricato: {registryStatus?.file_name ?? 'non disponibile'}
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
              <button onClick={closeRegistryPreview}>Torna alle fatture</button>
              {isRegistryEditMode ? (
                <>
                  <button className="registry-save-button" onClick={saveRegistryEdits}>
                    Salva
                  </button>
                  <button onClick={cancelRegistryEditMode}>Annulla</button>
                </>
              ) : (
                <button onClick={enterRegistryEditMode}>Modifica</button>
              )}
              <button
                onClick={() => setIsRegistryCleaningConfirmOpen(true)}
                disabled={isRegistryEditMode || registryPreviewRecords.length === 0}
              >
                Cleaning
              </button>
              <button className="registry-add-button" onClick={openRegistryFilePicker}>
                {registryStatus?.has_file ? 'Sostituisci anagrafica' : 'Aggiungi anagrafica'}
              </button>
              <button
                className="registry-download-button"
                onClick={downloadRegistryFile}
                aria-label="Scarica anagrafica"
                title="Scarica anagrafica"
                disabled={!registryStatus?.file_url}
              >
                {'\u2193'}
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
          {registryPreviewError ? (
            <p className="registry-page-message">{registryPreviewError}</p>
          ) : (
            <RegistryTable
              records={registryTableRecords}
              isEditMode={isRegistryEditMode}
              highlightedRecordKey={highlightedRegistryRecordKey}
              tableViewportRef={registryTableViewportRef}
              onUpdateRecord={updateRegistryDraftRecord}
              onClearHighlight={clearRegistrySearchHighlight}
            />
          )}
          {registryImportStatus ? (
            <p className="registry-import-status">{registryImportStatus}</p>
          ) : null}
          {registrySaveStatus ? (
            <p className="registry-import-status">{registrySaveStatus}</p>
          ) : null}
          {isRegistryCleaningConfirmOpen ? (
            <RegistryCleaningConfirmDialog
              isRunning={isRegistryCleaningRunning}
              onCancel={() => setIsRegistryCleaningConfirmOpen(false)}
              onConfirm={runRegistryCleaning}
            />
          ) : null}
          {registryCleaningReport ? (
            <RegistryCleaningReportDialog
              report={registryCleaningReport}
              onClose={() => setRegistryCleaningReport(null)}
            />
          ) : null}
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

          </div>

          <div className="zoom-controls">
            <button className="registry-import-button" onClick={openRegistryDialog}>
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
            <button onClick={resetDocumentView}>Reset</button>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showTokenBoxes}
                onChange={(event) => setShowTokenBoxes(event.target.checked)}
              />
              Tokens
            </label>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showCropBoxes}
                onChange={(event) => setShowCropBoxes(event.target.checked)}
              />
              Crops
            </label>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showTableBoxes}
                onChange={(event) => setShowTableBoxes(event.target.checked)}
              />
              Tables
            </label>
            <label className="checkbox-control">
              <input
                type="checkbox"
                checked={showBoxLabels}
                onChange={(event) => setShowBoxLabels(event.target.checked)}
              />
              Labels
            </label>
          </div>
        </header>

        {isRegistryDialogOpen ? (
          <div className="registry-dialog-backdrop" role="presentation">
            <section className="registry-dialog" role="dialog" aria-modal="true">
              <header>
                <h2>Anagrafica cliente:</h2>
                <button onClick={closeRegistryDialog} aria-label="Chiudi">
                  x
                </button>
              </header>
              <div className="registry-file-slot">
                {registryStatusError ? (
                  <p>{registryStatusError}</p>
                ) : registryStatus?.has_file && registryStatus.file_url ? (
                  <div className="registry-file-actions">
                    <button
                      className="registry-preview-button"
                      onClick={openRegistryPreview}
                    >
                      {registryStatus.file_name ?? 'elenco_anagrafiche.xlsx'}
                      {registryStatus.record_count !== undefined
                        ? ` / ${registryStatus.record_count} record`
                        : ''}
                    </button>
                    <button
                      className="registry-download-button"
                      onClick={downloadRegistryFile}
                      aria-label="Scarica anagrafica"
                      title="Scarica anagrafica"
                    >
                      ?
                    </button>
                  </div>
                ) : (
                  <p>Nessun file in memoria</p>
                )}
              </div>
              <p className="registry-dialog-note">
                Un solo elenco anagrafica puo essere attivo: un nuovo file
                sostituisce quello presente.
              </p>
              {registryImportStatus ? (
                <p className="registry-import-status">{registryImportStatus}</p>
              ) : null}
              <button className="registry-add-button" onClick={openRegistryFilePicker}>
                {registryStatus?.has_file ? 'Sostituisci anagrafica' : 'Aggiungi anagrafica'}
              </button>
              <input
                ref={registryFileInputRef}
                className="hidden-file-input"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(event) => importRegistryFile(event.target.files?.[0])}
              />
            </section>
          </div>
        ) : null}

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

      <aside>
        <div className="fields-header">
          <h2>Campi fattura</h2>
          <label className="checkbox-control">
            <input
              type="checkbox"
              checked={showAllTargetBoxes}
              onChange={(event) => setShowAllTargetBoxes(event.target.checked)}
            />
            All Targets
          </label>
          <button onClick={resetFieldValues}>Reset</button>
        </div>
        <div>
          <p>Campi fattura: {displayedFields.length}</p>
          <ul>
            {displayedFields.map((field, fieldIndex) => {
              const fieldSuggestions = getFieldSuggestions(
                suggestionIndex,
                field.kind,
                field.value,
              )
              const isSuggestionOpen =
                activeSuggestionFieldKind === field.kind &&
                fieldSuggestions.length > 0
              const safeHighlightedSuggestionIndex = Math.min(
                highlightedSuggestionIndex,
                Math.max(0, fieldSuggestions.length - 1),
              )

              return (
                <li
                  key={field.kind}
                  className={[
                    field.kind === selectedFieldKind ? 'selected-field' : '',
                    field.role === 'registry_enriched' ? 'registry-enriched-field' : '',
                    field.role === 'suggestion_enriched' ? 'suggestion-enriched-field' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <strong>
                    <span>{field.label}</span>
                  </strong>
                  <span className="field-input-row">
                    <span className="field-input-shell">
                      <input
                        data-field-index={fieldIndex}
                        value={field.value}
                        aria-autocomplete="list"
                        aria-expanded={isSuggestionOpen}
                        onFocus={() => {
                          selectField(field)
                          setActiveSuggestionFieldKind(field.kind)
                          setHighlightedSuggestionIndex(0)
                        }}
                        onClick={() => {
                          selectField(field)
                          setActiveSuggestionFieldKind(field.kind)
                        }}
                        onChange={(event) =>
                          updateFieldValue(field.kind, event.target.value)
                        }
                        onBlur={() => setActiveSuggestionFieldKind(null)}
                        onKeyDown={(event) => {
                          if (
                            event.ctrlKey &&
                            (event.key === 'ArrowDown' || event.key === 'ArrowUp')
                          ) {
                            return
                          }
                          if (isSuggestionOpen && event.key === 'ArrowDown') {
                            event.preventDefault()
                            setHighlightedSuggestionIndex((currentIndex) =>
                              Math.min(currentIndex + 1, fieldSuggestions.length - 1),
                            )
                            return
                          }
                          if (isSuggestionOpen && event.key === 'ArrowUp') {
                            event.preventDefault()
                            setHighlightedSuggestionIndex((currentIndex) =>
                              Math.max(currentIndex - 1, 0),
                            )
                            return
                          }
                          if (isSuggestionOpen && event.key === 'Enter') {
                            event.preventDefault()
                            acceptFieldSuggestion(
                              field.kind,
                              fieldSuggestions[safeHighlightedSuggestionIndex],
                            )
                            return
                          }
                          if (isSuggestionOpen && event.key === 'Escape') {
                            event.preventDefault()
                            setActiveSuggestionFieldKind(null)
                            return
                          }
                          if (event.key === 'Enter') {
                            event.currentTarget.blur()
                          }
                          if (event.key === 'ArrowDown') {
                            event.preventDefault()
                            focusFieldByIndex(fieldIndex + 1)
                          }
                          if (event.key === 'ArrowUp') {
                            event.preventDefault()
                            focusFieldByIndex(fieldIndex - 1)
                          }
                        }}
                      />
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
    </main>
  )
}

export default App

