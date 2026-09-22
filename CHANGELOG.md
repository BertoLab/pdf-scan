# Changelog

Traccia delle modifiche principali al progetto. Le note sono scritte per
ricordare sia cosa e cambiato sia perche e stato fatto.

## 2026-09-21 - Withholding field uses the monetary amount

- The reviewed `Ritenuta` field now stores the monetary amount rather than the
  printed percentage.
- Frontend and backend validate the value as money and enforce `gross total -
  withholding = net total` when all three fields are present.
- Printed percentages remain internal OCR evidence and are no longer exposed as
  the accounting value.
- Registry schema version 3 migrates legacy percentage fields from the known
  difference between gross and net totals.

## 2026-06-11 - Fallback OCR crop-only

### Cambiato

- Il fallback OCR operativo usa ora `crop-only`: prima lettura `ita/fast` su
  pagina intera, poi crop mirati ancora con `ita/fast` solo per fatture sotto
  soglia.
- Il profilo `best-crop` resta disponibile come modalita di debug/confronto,
  ma non e il default perche nei test iniziali ha rallentato molto senza
  recuperare abbastanza informazioni rispetto a `crop-only`.
- La suite comparativa del viewer genera `__no_fallback` per tutti i documenti e
  crea `__fallback_best_crop` / `__fallback_crop_only` solo per i documenti che
  risultano davvero sotto soglia secondo il gate OCR.

### Nota

- Se `best-crop` non mostrera benefici chiari in test futuri, il supporto al
  modello `tessdata_best` potra essere rimosso dalla versione finale.

## 2026-06-08 - Refactor segmentazione fatture e pulizia debug

### Aggiunto

- Nuovo modulo `src/estrattore/viewer_invoice_segmentation.py`.
- Funzione pubblica `build_viewer_invoices(page_manifests)` per costruire le
  fatture del viewer partendo dai manifest pagina.
- I campi in `invoice.fields` includono `page`, `bbox` e `role` del target
  selezionato, cosi il viewer puo evidenziare l'evidenza del campo senza
  ricalcolare il matching.
- Overlay frontend della bbox selezionata: cliccando/focalizzando un campo a
  destra viene disegnata solo la bbox collegata su documento centrale.
- Selezionando un campo, il viewer porta lo zoom al 140% e scorre il documento
  verso la bbox collegata, centrando l'evidenza quando lo spazio disponibile lo
  permette.
- I target e i campi fattura espongono `ocr_confidence` nel range `0..1`, cioe
  l'affidabilita OCR della lettura scan separata dallo score euristico.
- Nuovo campo fattura `pagata_il`, mostrato come `Pagata il`, per estrarre la
  data pagamento/esecuzione dai riepiloghi bancari stampati.
- Nuovo modulo `src/estrattore/ocr_normalization.py` per centralizzare le
  normalizzazioni OCR-aware usate nel matching delle anchor.
- Toggle frontend `All` per mostrare tutte le bbox target e toggle `Tokens` per
  mostrare le bbox OCR dei token.
- Toggle frontend `Labels` per mostrare/nascondere il testo estratto sopra le
  bbox.

### Cambiato

- `scripts/debug_words.py` non contiene piu la logica di segmentazione fatture:
  ora genera i manifest, prepara i dati debug e delega la costruzione delle
  fatture al nuovo modulo.
- I test sulla segmentazione importano direttamente
  `build_viewer_invoices`, rendendo chiara la ownership della logica.
- La costruzione del viewer manifest indicizza i candidati per pagina una sola
  volta, invece di rifiltrare ogni lista per ogni pagina.
- La generazione dei target e delle highlight debug e stata accorpata con helper
  comuni per ridurre duplicazione.
- Le bbox interattive sono disegnate dal frontend in percentuale sul contenitore
  pagina, quindi restano allineate con zoom e scroll.
- Le bbox target sono colorate in base a `ocr_confidence` per distinguere
  rapidamente letture OCR forti, medie e deboli.
- Il viewer mostra a destra `ocr_confidence` invece dello score euristico, cosi
  il numero visibile indica il rischio di errore OCR sul testo letto.
- `pagata_il` viene scelto tra i candidati data con contesto bancario, ad
  esempio `Data esecuzione`, `Data/Ora`, `bonifico`, `Credit Transfer`,
  `disposizione`, stato pagamento, e viene scartato se coincide con
  `data_fattura`.
- Le anchor fiscali usano una vista normalizzata temporanea: varianti OCR come
  `P.AVA`, `P.1VA` e `P.lVA` vengono matchate come `P.IVA` senza modificare
  testo originale, bbox o confidence OCR.
- Le immagini generate dal backend non disegnano piu target e token di default:
  le bbox restano dati del manifest e vengono visualizzate dal frontend.

### Verificato

- Suite Python: `80 passed`.
- TypeScript viewer: `npm exec -- tsc -b` passato.

## 2026-06-05 / 2026-06-08 - Segmentazione multi-fattura

### Aggiunto

- Supporto a PDF con piu fatture nello stesso file.
- Ogni `viewer_manifest.json` puo contenere una lista `invoices`, dove ogni
  fattura ha:
  - `invoice_id`;
  - `title`;
  - `page_numbers`;
  - lista fissa di `fields`.
- Le pagine hanno diagnostica di segmentazione:
  - `invoice_signature`;
  - `invoice_start_decision`;
  - `invoice_header_regions`.

### Regole attuali

- La prima pagina apre sempre la prima fattura.
- Una pagina successiva apre una nuova fattura solo se ha almeno 5 segnali forti.
- Deve avere almeno 3 segnali diversi dalla firma della fattura corrente.
- Una regione `invoice_header` puo contribuire come segnale forte quando trova
  almeno 4 label allineate e almeno 2 valori collegati.

### Corretto

- Caso OTIS: rilevata una fattura aggiuntiva quando nello stesso PDF sono
  presenti piu blocchi fattura.
- Se una riga contiene due coppie `numero fattura + data`, la data finale della
  fattura preferisce quella geometricamente a destra del numero fattura corrente.

## 2026-06-05 - Manifest viewer e caricamento frontend

### Aggiunto

- `apps/viewer/public/sample/viewer_index.json` come indice statico dei documenti
  processati.
- Il frontend carica piu manifest da `/sample/...` invece di dipendere da un
  solo file hardcoded.
- Il viewer lavora per fatture, non solo per pagine.
- I campi a destra restano presenti anche quando il valore non e stato trovato:
  il campo resta vuoto ed editabile.

### Cambiato

- Output del backend debug orientato direttamente a
  `apps/viewer/public/sample/<documento>`.
- I bottoni laterali mostrano solo `Fattura 001`, `Fattura 002`, ecc.
- Il pannello destro e stato allargato e reso scrollabile.

## 2026-06-05 - Viewer React

### Aggiunto

- Viewer locale in `apps/viewer` basato su Vite + React + TypeScript.
- Layout a tre colonne:
  - lista fatture a sinistra;
  - documento/pagine al centro;
  - campi estratti a destra.
- Zoom sul documento centrale.
- Zoom editabile tramite percentuale.
- Editing locale dei campi estratti.
- Reset dei campi al valore iniziale.
- Supporto a scroll/zoom piu naturale nel pannello documento.

### Cambiato

- La visualizzazione centrale usa pagine con dimensioni proporzionali al formato
  reale del documento, invece di box mock compressi.

## 2026-06-03 / 2026-06-05 - Campi estratti e debug visuale

### Aggiunto

- Estrazione `nome_emittente` da span contenenti `SRL` / `S.R.L.`.
- Estrazione `codice_fiscale_emittente` oltre al codice fiscale del condominio.
- Distinzione tra:
  - `codice_fiscale`;
  - `codice_fiscale_emittente`;
  - `p_iva`.
- Bbox debug per target riconosciuti.
- Evidenza visuale arancione quando una bbox contiene almeno un token con
  confidence sotto `0.9`.
- File centralizzato `src/estrattore/debug_flags.py` per abilitare/disabilitare
  box debug.

### Cambiato

- I target finali del viewer hanno nomi italiani, per esempio:
  - `codice_fiscale`;
  - `p_iva`;
  - `numero_fattura`;
  - `data_fattura`.
- I flag debug sono centralizzati, invece di essere sparsi nel codice.

## 2026-05 / 2026-06 - Economic summary

### Aggiunto

- Riconoscimento della regione riepilogo economico.
- Estrazione e debug di:
  - totale fattura;
  - ritenuta;
  - totale netto fattura.
- Righe risolte in `economic_summary_resolved_rows`.
- Supporto a varianti tipo:
  - `Tot. documento`;
  - `Rit. acconto`;
  - `Tot. dovuto`.

### Cambiato

- Il campo finale `Ritenuta` nel viewer ora rappresenta la percentuale
  selezionata (`4%` o `20%`), non l'importo monetario.
- L'importo della ritenuta resta nei dati diagnostici e nelle overlay come
  evidenza per validare i calcoli.

## 2026-05 / 2026-06 - Numero fattura e data fattura

### Aggiunto

- Estrazione `numero_fattura` con fuzzy matching su anchor come:
  - `FATTURA`;
  - `FATTURA NUMERO`;
  - `N.`;
  - `Numero fattura`.
- Estrazione `data_fattura` con contesti positivi e negativi.

### Cambiato

- Il numero fattura normalizza prefissi nello stesso token, per esempio
  `N.227` diventa `227`.
- `entro` / `entro il` penalizzano la data, perche indicano scadenza pagamento.
- `del` e `periodo di fatturazione` sono contesti positivi, ma la scelta finale
  della data tiene conto anche del collegamento con il numero fattura corrente.

## Note aperte

- Rigenerare i sample quando si vuole vedere nel frontend l'effetto delle ultime
  regole backend.
- Valutare un secondo refactor per separare meglio:
  - creazione del viewer manifest;
  - serializzazione dei candidati;
  - disegno delle highlight debug.
- Valutare un modulo condiviso per normalizzazioni OCR comuni, per evitare che
  ogni estrattore cresca con helper simili ma leggermente diversi.
