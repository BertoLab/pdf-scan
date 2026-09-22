# Estrattore Scan Fatture

Pipeline conservativa per processare fatture scannerizzate, estrarre i dati affidabili, isolare le informazioni incerte e generare un Excel pensato per una revisione umana rapida.

Questo README e il documento di contesto principale del progetto. Quando si apre una nuova chat o si riprende il lavoro, va letto prima del codice per mantenere chiari obiettivo, architettura e criteri di qualita.

## Obiettivo

Il progetto lavora su fatture che erano originariamente digitali, poi sono state stampate, annotate manualmente e scannerizzate di nuovo. I PDF finali sono quindi immagini, non documenti digitali strutturati.

L'obiettivo non e costruire una "AI perfetta" che capisce sempre ogni fattura. L'obiettivo professionale e:

- massimizzare l'estrazione automatica affidabile;
- minimizzare i falsi positivi;
- rifiutare esplicitamente i casi incerti;
- rendere veloce la verifica umana;
- mantenere il sistema semplice da debuggare e migliorare.

La regola piu importante e: meglio un campo `uncertain` o `missing` che un valore sbagliato scritto con sicurezza.

## Strategia Architetturale

La pipeline deve essere ibrida e a layer. Non vogliamo basarci solo su OCR grezzo e regex, ma non vogliamo nemmeno partire subito con un modello ML custom.

Direzione corretta:

```text
PDF scannerizzato
  -> immagini pagina
  -> rappresentazione documentale strutturata
  -> regole di estrazione conservative
  -> validazione
  -> InvoiceResult
  -> Excel + crop immagini + log rifiuti
```

Il principio e separare percezione, giudizio e revisione:

- la percezione legge il documento e produce evidenze;
- il codice applicativo decide cosa e abbastanza affidabile;
- la persona verifica rapidamente solo cio che resta incerto.

## Layer Della Pipeline

### 1. Input e normalizzazione

Responsabilita:

- caricare PDF scannerizzati o immagini;
- convertire ogni pagina in immagine;
- applicare preprocessing solo quando utile;
- mantenere tracciabilita tra file sorgente, pagina e output.

Il preprocessing non deve distruggere informazioni. Ogni trasformazione deve avere uno scopo chiaro: migliorare OCR, layout detection o crop.

### 2. Perception layer

Questo layer deve trasformare le immagini in una rappresentazione strutturata del documento.

Deve usare un backend OCR leggero e controllabile, piu un layer geometrico proprietario, per ottenere:

- testo OCR;
- token e coordinate;
- blocchi di testo;
- regioni di layout;
- tabelle;
- possibili relazioni chiave-valore;
- confidence per testo e regioni.

Questo layer non deve decidere il significato finale dei dati fiscali. Deve produrre osservazioni leggibili e verificabili.

### 3. Extraction layer

Questo layer usa la rappresentazione strutturata per trovare candidati ai campi richiesti.

Esempi:

- partita IVA tramite anchor testuali, coordinate e validazione formale;
- totale fattura tramite label vicine come `Totale`, `Totale fattura`, `Importo documento`;
- date tramite pattern, posizione e contesto;
- righe tabellari usando la struttura tabella quando disponibile.

Le regole devono produrre candidati con evidenze, non solo valori finali.

### 4. Validation layer

Questo layer decide lo stato finale di ogni campo.

Stati consigliati:

- `accepted`: valore affidabile e validato;
- `uncertain`: candidato presente ma non abbastanza sicuro;
- `missing`: campo non trovato;
- `conflict`: piu candidati plausibili in contraddizione;
- `rejected`: documento non processabile o sotto soglia minima.

Validazioni tipiche:

- formato data valido;
- partita IVA formalmente valida;
- importi normalizzati correttamente;
- confidence sopra soglia;
- coerenza tra label, posizione e valore;
- assenza di candidati concorrenti troppo simili.

### 5. Handwriting layer

La data scritta a mano e un problema separato.

Caratteristiche:

- e irregolare;
- puo essere inclinata o poco leggibile;
- puo stare in una zona simile ma non perfettamente fissa;
- non deve essere accettata automaticamente se la confidence e bassa.

Comportamento corretto:

- individuare una o piu regioni candidate;
- salvare sempre il crop immagine;
- tentare OCR manoscritto solo come supporto opzionale;
- scrivere il testo riconosciuto come `uncertain` se non e molto affidabile;
- permettere alla persona di verificare il crop direttamente dall'Excel.

Per la V1, il crop corretto della data manoscritta vale piu del riconoscimento automatico.

### 6. Export e review

L'Excel non e una verita assoluta. E uno strumento operativo per ridurre lavoro manuale.

Ogni riga dovrebbe contenere:

- nome file sorgente;
- campi stampati estratti;
- stato finale del documento;
- stato dei campi importanti;
- motivo di incertezza o rifiuto;
- eventuale testo riconosciuto per la data manoscritta;
- immagine crop della data manoscritta o riferimento al file crop.

## Modello Dati

Il sistema deve rappresentare i risultati con abbastanza contesto da poterli debuggare.

Esempio concettuale:

```python
InvoiceField(
    name="total_amount",
    value="1234.56",
    status="accepted",
    confidence=0.94,
    page=1,
    bbox=(120, 710, 260, 735),
    evidence=[
        "label vicino: Totale fattura",
        "formato importo valido",
        "nessun candidato concorrente forte",
    ],
)
```

Il repository contiene i modelli principali in `src/estrattore/model.py`. `InvoiceResult` rappresenta il risultato finale della fattura, mentre `DocumentRepresentation` rappresenta l'output intermedio prodotto dal perception layer.

## Stato Attuale Del Repository

Stato corrente:

- progetto Python installabile da `pyproject.toml`;
- codice principale in `src/estrattore`;
- test automatici in `tests`;
- caricamento PDF tramite `pdf2image`;
- caricamento immagini singole per prove OCR mirate;
- modelli dominio/documento in `model.py`;
- `DocumentRepresentation` con pagine, token OCR, blocchi layout e tabelle;
- backend perception vuoto per test e struttura;
- adapter Tesseract word-level in `tesseract_adapter.py`;
- grouping geometrico token -> righe in `text_layout.py`;
- debug output generico in `debug_output.py`;
- pipeline iniziale che carica una scansione, produce la rappresentazione documentale e rifiuta i casi non caricabili;
- moduli `handwriting_roi.py` ed `excel_export.py` ancora da implementare.

Questo significa che il progetto e ancora nella fase di fondazione. Prima si consolidano struttura, modelli e contratti; poi si aggiungono OCR, layout analysis ed export.

## Struttura Del Progetto

```text
estrattore-scan-fatture/
|-- src/estrattore/
|   |-- pipeline.py          # Orchestrazione del processamento
|   |-- io.py                # Caricamento input e preprocessing immagini
|   |-- model.py             # Modelli dominio, documento e stati
|   |-- perception.py        # Contratto pubblico del perception layer
|   |-- tesseract_adapter.py # OCR leggero parola-per-parola
|   |-- text_layout.py       # Ricostruzione righe da token OCR
|   |-- debug_output.py      # JSON e immagini annotate per debug perception
|   |-- handwriting_roi.py   # Futuro rilevamento crop manoscritto
|   |-- excel_export.py      # Futuro export Excel
|   `-- config.py            # Configurazione
|-- data/
|   |-- input/               # Fatture da processare
|   |-- processed/           # File gia processati
|   |-- rejected/            # File scartati o non gestibili
|   `-- crops/               # Crop delle date manoscritte
|-- outputs/
|   |-- batches/             # File Excel generati
|   |-- debug/               # Output di ispezione OCR/layout
|   `-- logs/                # Log e report di processo
|-- scripts/
|   |-- dev_run.py                # Script di sviluppo
|   |-- debug_words.py            # Salva token OCR e bounding box
|   |-- debug_words_batch.py      # Genera manifest viewer per una cartella batch
|   |-- process_year_batches.py   # Wrapper per processare batch per anno/range
|   `-- batch_rename_pdfs.py      # Rinomina PDF e li divide in cartelle batch
|-- tests/                   # Test automatici
|-- pyproject.toml
`-- README.md
```

## Comandi Operativi Per Batch

La pipeline di lavoro e separata in due fasi:

1. generazione dei manifest OCR/debug;
2. visualizzazione/revisione dei manifest gia generati.

Il viewer non processa PDF direttamente. Legge solo i manifest presenti in
`apps/viewer/public/sample`.

### Preparare PDF in batch da 10

Dato un folder con PDF sparsi, questo script chiede l'anno, rinomina i PDF in
modo progressivo e crea cartelle batch da 10 file:

```powershell
.\.venv\Scripts\python.exe scripts\batch_rename_pdfs.py "C:\percorso\cartella_pdf"
```

Esempio con anno passato da comando:

```powershell
.\.venv\Scripts\python.exe scripts\batch_rename_pdfs.py "C:\percorso\cartella_pdf" --year 2025
```

Per vedere cosa farebbe senza modificare i file:

```powershell
.\.venv\Scripts\python.exe scripts\batch_rename_pdfs.py "C:\percorso\cartella_pdf" --year 2025 --dry-run
```

Output atteso:

```text
pdf_2025_1.pdf ... pdf_2025_10.pdf  -> batch_2025_1/
pdf_2025_11.pdf ... pdf_2025_20.pdf -> batch_2025_2/
```

### Generare manifest per un singolo batch

Per processare una cartella batch gia pronta:

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input\2025\batch_2025_1
```

Lo script genera:

```text
apps/viewer/public/sample/batch_2025_1/
|-- viewer_index.json
|-- pdf_2025_1/
|   |-- viewer_manifest.json
|   `-- page-001-boxes.png
`-- pdf_2025_2/
    `-- viewer_manifest.json
```

Aggiorna anche:

```text
apps/viewer/public/sample/viewer_batches.json
```

Il viewer usa questo file per mostrare il menu dei batch disponibili.

### Generare manifest per anno e numero batch

Per non scrivere ogni volta il path completo si puo usare il wrapper per anno:

```powershell
.\.venv\Scripts\python.exe scripts\process_year_batches.py --year 2025 --batch 1
```

Questo processa:

```text
data/input/2025/batch_2025_1
```

### Generare manifest per un range di batch

Per processare piu batch consecutivi:

```powershell
.\.venv\Scripts\python.exe scripts\process_year_batches.py --year 2025 --batch-range 1-10
```

Lo script processa in ordine:

```text
batch_2025_1
batch_2025_2
...
batch_2025_10
```

### Rigenerare un solo file dentro un batch

Durante il debug conviene spesso rigenerare un solo PDF invece dell'intero batch.
`--file-index` indica la posizione del file dentro la cartella batch, usando
l'ordine alfabetico dei PDF. E un indice 1-based: `1` significa il primo file
del batch, non la prima fattura progressiva globale.

```powershell
.\.venv\Scripts\python.exe scripts\process_year_batches.py --year 2025 --batch 1 --file-index 1
```

Esempio: in `data/input/2025/batch_2025_1`, `--file-index 1` rigenera il primo
PDF della cartella batch e aggiorna solo il suo `viewer_manifest.json`.

Uso guidato, senza parametri:

```powershell
.\.venv\Scripts\python.exe scripts\process_year_batches.py
```

### Visualizzare i batch generati

Il viewer si avvia separatamente:

```powershell
cd apps\viewer
npm run dev
```

Poi aprire l'URL stampato da Vite.

Nel viewer:

- il menu `Batch` mostra i batch gia elaborati;
- selezionando un batch vengono caricati solo i manifest di quel batch;
- il viewer non rielabora i PDF.

Per questo motivo, se un batch non appare nel menu, significa che non e ancora
stato processato oppure che `viewer_batches.json` non e stato aggiornato.

### Avviare l'app locale

La forma locale dell'app non richiede due terminali: un unico launcher Python
controlla la build del frontend, avvia il backend locale e serve il viewer.

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py
```

L'app apre:

```text
http://127.0.0.1:8765
```

Il backend anagrafica e incluso nello stesso processo locale.

Per forzare la rigenerazione della build React:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --rebuild
```

Per provare l'app con una cartella dati isolata:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --data-dir .\runtime-beta-test --rebuild
```

Per sviluppo frontend puro si puo ancora usare Vite, ma in quel caso il backend
locale va avviato a parte:

```powershell
.\.venv\Scripts\python.exe scripts\registry_api.py
cd apps\viewer
npm run dev
```

### Raccolta dati per il miglioramento dell'estrazione

L'app raccoglie automaticamente, nella propria cartella dati, le evidenze OCR,
le proposte iniziali, i valori mostrati dopo l'anagrafica e le revisioni salvate:

```text
%LOCALAPPDATA%\EstrattoreScanFatture\feedback\
```

La raccolta non rilancia l'OCR e non duplica stabilmente i PDF. Per creare un
pacchetto consistente da trasferire nell'ambiente di analisi:

Nell'app aprire `Area sviluppatore` e scegliere
`Esporta dati per miglioramento`. Dopo la conferma, il pacchetto completo viene
salvato sul Desktop in una nuova cartella con data e ora. Sono inclusi anche i PDF
ancora disponibili negli archivi dell'app.

Lo stesso export puo essere eseguito dagli strumenti di sviluppo con:

```powershell
.\.venv\Scripts\python.exe scripts\export_feedback_collection.py .\feedback-export
```

Il pacchetto contiene una copia consistente del database, le evidenze OCR
compresse, i PDF ancora disponibili nell'archivio e un manifest con checksum.
Per omettere i PDF usare `--without-pdfs`.

Per controllare run completi o temporanei, revisioni, operazioni pendenti,
anomalie e sorgenti immagine non piu disponibili senza creare un export:

```powershell
.\.venv\Scripts\python.exe scripts\export_feedback_collection.py --status-only
```

### Importare l'anagrafica clienti

Nel viewer, il pulsante `Anagrafica clienti` apre la finestra di gestione
anagrafica. Il tasto `Aggiungi anagrafica` accetta un file `.xlsx` con le
colonne dell'export anagrafica atteso. Se il file e valido, il backend
sostituisce l'unica anagrafica attiva:

```text
data/registry/input/elenco_anagrafiche.xlsx
```

e rigenera:

```text
data/registry/cache/anagrafica_registry.json
```

## Milestone Consigliate

### Milestone 1: base solida

- caricare PDF e immagini in modo affidabile;
- normalizzare il risultato in un modello chiaro;
- gestire errori tecnici con `rejected` e motivazione;
- coprire il comportamento con test.

### Milestone 2: rappresentazione documentale

- integrare un OCR/document understanding layer;
- salvare token, blocchi, coordinate e confidence;
- rendere ispezionabile l'output intermedio.

### Milestone 3: primi campi stampati

- estrarre pochi campi ad alto valore;
- validare formalmente ogni campo;
- distinguere `accepted`, `missing`, `uncertain` e `conflict`.

### Milestone 4: crop data manoscritta

- individuare regioni candidate;
- salvare crop per ogni fattura;
- collegare crop e stato nel risultato finale.

### Milestone 5: Excel operativo

- generare Excel con una riga per fattura;
- includere dati, stati, motivi di rifiuto e crop;
- usare l'Excel come interfaccia di revisione rapida.

## Avvio Rapido

Installazione in sviluppo:

```bash
pip install -e .[dev]
```

Esecuzione test:

```bash
pytest
```

La CLI definitiva non e ancora stabilizzata. Se una chat futura deve lavorare sul progetto, deve prima verificare gli script disponibili e lo stato dei test.

## Debug Token OCR

Il primo obiettivo operativo e valutare se l'OCR produce token puliti, coordinate utili e gruppi di token leggibili. Il backend leggero corrente usa Tesseract tramite `pytesseract`.

Installazione dipendenza Python:

```bash
pip install -e .[ocr]
```

Serve anche l'eseguibile Tesseract installato nel sistema e disponibile su `PATH`.
Per il packaging finale, l'app non deve richiedere installazioni manuali: Tesseract va distribuito dentro `vendor/tesseract/`.

Ordine di ricerca Tesseract:

1. `vendor/tesseract/tesseract.exe`;
2. `C:\Program Files\Tesseract-OCR\tesseract.exe`;
3. `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`;
4. `tesseract.exe` disponibile su `PATH`.

Layout previsto per distribuzione:

```text
vendor/tesseract/
|-- tesseract.exe
`-- tessdata/
    |-- ita.traineddata
    `-- eng.traineddata
```

Esecuzione:

```bash
python scripts/debug_words.py data/input/fattura.pdf
```

Esecuzione batch su tutti i PDF/immagini in `data/input`:

```bash
python scripts/debug_words_batch.py
```

Output:

```text
outputs/debug/words/fattura/
|-- document.json
|-- lines.csv
|-- spans.csv
|-- vat_candidates.csv
|-- fiscal_identifier_candidates.csv
|-- invoice_number_candidates.csv
|-- invoice_date_candidates.csv
|-- condominium_candidates.csv
|-- economic_summary_regions.csv
|-- economic_summary_field_candidates.csv
|-- economic_summary_resolved_rows.csv
`-- page-001-boxes.png
```

`document.json` contiene token OCR, righe ricostruite e span di token con testo, bounding box e confidence. `lines.csv` contiene una riga per ogni riga visuale ricostruita. `spans.csv` contiene gruppi di token separati dai gap orizzontali ed e il modo piu comodo per controllare rapidamente le unita candidate per il parsing. `vat_candidates.csv` contiene i candidati P.IVA validati formalmente e segnati con uno score contestuale. `fiscal_identifier_candidates.csv` valuta gli stessi identificativi fiscali a coppia, con score separati per `p_iva` e `codice_fiscale`, cosi l'anchor mista `COD. FISCALE / P.IVA` puo rimanere ambigua finche non ci sono altri indizi. `invoice_number_candidates.csv` contiene i candidati `numero_fattura`, con formato riconosciuto, anchor vicina, data vicina, esclusioni di contesto e score. `invoice_date_candidates.csv` contiene i candidati `data_fattura`. `condominium_candidates.csv` contiene gli span riconosciuti come `condominio`. `economic_summary_regions.csv` contiene i quadranti riepilogo economico riconosciuti tramite anchor e importi vicini. `economic_summary_field_candidates.csv` collega ogni descrizione economica al valore numerico piu plausibile nel quadrante. `economic_summary_resolved_rows.csv` contiene una riga per quadrante economico risolto con `totale_fattura`, `ritenuta`, percentuale, `totale_netto_fattura`, sorgenti e controllo matematico. Le immagini annotate mostrano visivamente le bounding box degli span senza etichetta, i token con etichetta e i target riconosciuti con bbox gialla.

Nel progetto chiamiamo **span** un gruppo contiguo di token vicini tra loro, separato dagli altri gruppi da un gap orizzontale significativo. Lo span e l'unita candidata principale per il parsing.

## Decisione OCR/Layout

PaddleOCR con PP-StructureV3 e stato valutato e scartato come backend della pipeline.

Motivi:

- installazione e setup troppo pesanti per un'app destinata a una persona non tecnica;
- download automatici di modelli molto grandi;
- problemi iniziali di compatibilita runtime su Windows CPU;
- tempi di inferenza non accettabili su documenti multipagina anche dopo cache calda;
- rischio alto per packaging e distribuzione futura.

La direzione corrente e:

```text
OCR leggero parola-per-parola
  -> token con bounding box
  -> grouping geometrico di righe e colonne
  -> ricostruzione tabelle controllata dal progetto
  -> labeling assistito quando le regole non bastano
```

Il progetto deve scegliere il backend piu leggero che produce parole e coordinate affidabili. La struttura tabellare non deve dipendere da un modello pesante se puo essere ricostruita con geometria, validazioni e labeling incrementale.

## Regole Di Sviluppo

Ogni nuova funzione deve avere:

- input e output chiari;
- fallimenti espliciti;
- test proporzionati al rischio;
- log o motivazioni utili alla revisione;
- nessuna accettazione automatica senza validazione.

Preferenze tecniche:

- usare strutture dati esplicite invece di dizionari opachi quando il dominio cresce;
- mantenere separati OCR, estrazione, validazione ed export;
- non mischiare codice di business con dettagli Excel;
- non allenare modelli custom finche non ci sono dati, metriche e fallimenti chiari;
- aggiungere automazione solo quando e misurabile e verificabile.

## Criterio Di Qualita

Una modifica e buona se rende il sistema piu:

- conservativo sui valori incerti;
- facile da testare;
- facile da debuggare;
- utile per ridurre lavoro manuale reale;
- chiaro per chi riprende il progetto in una chat futura.

Il progetto deve trattare l'incertezza come un risultato normale, non come un errore imprevisto.
