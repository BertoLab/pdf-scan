
import { useEffect, useRef, useState } from 'react'
import './App.css'
import type {
  BoundingBox,
  InvoiceField,
  ViewerDocumentIndex,
  ViewerDocumentIndexEntry,
  ViewerInvoice,
  ViewerManifest,
} from './types'

const sampleIndexPath = '/sample/viewer_index.json'
const BASE_PAGE_SCALE = 0.45
const FIELD_FOCUS_SCALE = 1.4
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

function getBboxStyle(bbox: BoundingBox, pageWidth: number, pageHeight: number) {
  return {
    left: `${(bbox.x1 / pageWidth) * 100}%`,
    top: `${(bbox.y1 / pageHeight) * 100}%`,
    width: `${((bbox.x2 - bbox.x1) / pageWidth) * 100}%`,
    height: `${((bbox.y2 - bbox.y1) / pageHeight) * 100}%`,
  }
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
      ? ` · ${invoice.variantLabel}`
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
  const [showBoxLabels, setShowBoxLabels] = useState(false)
  const documentViewportRef = useRef<HTMLDivElement | null>(null)
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

  useEffect(() => {
    fetch(sampleIndexPath)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        return response.json()
      })
      .then((index: ViewerDocumentIndex) => loadDocumentEntries(index.documents))
      .catch((error) => {
        setManifestError(`Impossibile caricare viewer_index.json: ${String(error)}`)
      })
  }, [])

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
        fetch(`${documentEntry.base_path}/viewer_manifest.json`)
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
      return
    }

    setInvoices([])
    setSelectedInvoiceId(null)
    setSelectedFieldKind(null)
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
    <main>
      <aside>
        <h2>Fatture</h2>
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
                ? ` · fallback ${currentInvoice.fallbackOcrEventCount}`
                : ''}
            </p>

          </div>

          <div className="zoom-controls">
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
                checked={showBoxLabels}
                onChange={(event) => setShowBoxLabels(event.target.checked)}
              />
              Labels
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
                          {region.reason} · ink {region.ink_density.toFixed(3)} ·
                          tokens {region.token_count}
                        </span>
                      ) : null}
                    </div>
                  ))
                : null}
            </article>
          ))}
        </div>
      </section>

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
            {displayedFields.map((field, fieldIndex) => (
              <li
                key={field.kind}
                className={field.kind === selectedFieldKind ? 'selected-field' : ''}
              >
                <strong>
                  <span>{field.label}</span>
                </strong>
                <span>
                  <input
                    data-field-index={fieldIndex}
                    value={field.value}
                    onFocus={() => selectField(field)}
                    onClick={() => selectField(field)}
                    onChange={(event) =>
                      updateFieldValue(field.kind, event.target.value)
                    }
                    onKeyDown={(event) => {
                      if (
                        event.ctrlKey &&
                        (event.key === 'ArrowDown' || event.key === 'ArrowUp')
                      ) {
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
                  {field.ocr_confidence !== null &&
                  field.ocr_confidence !== undefined ? (
                    <em>({formatOcrConfidence(field.ocr_confidence)})</em>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </main>
  )
}

export default App
