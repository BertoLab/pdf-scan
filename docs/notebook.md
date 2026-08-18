# Notebook progetto - memoria operativa Codex

## Scopo di questo documento

Questo documento e' una memoria di lavoro per Codex, non una guida utente.

Deve servire per mantenere continuita' tecnica sul progetto mentre costruiamo l'app e prendiamo decisioni concrete. La beta e' solo una milestone temporale di consegna, non il criterio architetturale con cui giustificare soluzioni fragili. Va aggiornato nel tempo con:

- decisioni prese;
- tentativi fatti;
- problemi incontrati;
- soluzioni scartate;
- motivazioni tecniche;
- prossimi passi;
- rischi ancora aperti.

Quando torno su questo tema devo leggere prima `.agents/rules_workflow.md`, poi questo notebook per evitare di ripartire da zero o contraddire decisioni gia' prese.

## Uso del notebook

Le regole operative stabili non vivono qui: sono in `.agents/rules_workflow.md` e sono richiamate da `AGENTS.md`.

Questo notebook serve per scrivere:

- punti progettuali;
- decisioni prese;
- dubbi e rischi;
- tentativi e soluzioni scartate;
- verifiche eseguite;
- chiusure e prossimi passi.

Quando chiudo un punto devo linkare in chat la sezione rilevante di questo file.

## Documenti collegati

- Le regole operative stabili sono in `.agents/rules_workflow.md`.
- La roadmap futura del packaging desktop e' in `docs/packaging_plan.md`.
- Questo notebook contiene solo memoria progettuale, punti passati e punti presenti.

## Principio architetturale principale

Separare sempre:

```text
codice applicazione
dati utente
```

Il codice puo' essere sostituito a ogni aggiornamento.
I dati utente non devono essere cancellati o sovrascritti durante aggiornamenti o reinstallazioni.

Path indicativi:

```text
Programma:
C:\Program Files\Estrattore Scan Fatture\

Dati utente:
C:\Users\<utente>\AppData\Local\EstrattoreScanFatture\
```

Nei dati utente devono vivere:

- anagrafica clienti;
- JSON/cache anagrafica;
- PDF importati o batch selezionati;
- manifest elaborati;
- modifiche manuali;
- configurazione locale;
- log.

## Regola di sicurezza primaria

L'app non deve mai modificare o cancellare file fuori dal proprio storage interno.

Questa e' una regola assoluta del progetto.

Quando l'utente seleziona PDF, cartelle, anagrafiche o altri file dal disco:

- l'app puo' leggerli;
- l'app puo' copiarli nel proprio storage interno;
- l'app puo' lavorare sulle copie interne;
- l'app puo' cancellare solo copie interne create dall'app;
- l'app non deve cancellare, rinominare, spostare o modificare i file originali dell'utente.

Motivo:

- una app desktop beta non deve avere effetti distruttivi sul filesystem esterno;
- lo studio deve poter provare l'app senza rischio sui documenti originali;
- ogni operazione distruttiva deve essere confinata in `%LOCALAPPDATA%\EstrattoreScanFatture` o nella cartella runtime configurata;
- anche in caso di bug, il danno massimo deve essere limitato allo storage interno dell'app.

Implicazione pratica:

- "cancellare PDF originali dopo processamento" significa cancellare solo la copia interna in `intake` o `files_to_process`;
- non significa mai cancellare i PDF dal percorso da cui l'utente li ha selezionati.

## Regola UX desktop per accesso ai file

Per una app desktop seria, l'accesso a PDF, cartelle, anagrafiche e output non deve dipendere dai limiti casuali del browser quando abbiamo gia' un backend/launcher locale.

Principio:

- il frontend React mostra l'interfaccia;
- il layer locale/native apre picker file/cartelle coerenti con Windows;
- il backend copia i file selezionati nello storage interno dell'app;
- la UI riceve solo il risultato strutturato dell'import.

Questo evita una UX ibrida dove:

- `Carica PDF` usa picker browser;
- `Carica cartella` usa picker Windows;
- alcune azioni permettono multi-selezione e altre no per limiti del browser.

Direzione corretta:

- uniformare `Carica PDF` e `Carica cartella` sullo stesso layer Windows/native;
- supportare progressivamente selezione multipla file e workflow per piu' cartelle;
- non vincolare decisioni strutturali a cio' che un input HTML consente oggi.

## Vincoli da rispettare

- Non dipendere da `npm run dev` nella beta desktop.
- Non dipendere da path assoluti del PC di sviluppo.
- Non salvare dati definitivi dentro la repo.
- Non richiedere all'utente finale di lanciare comandi Python.
- Non cancellare dati utente durante rebuild o aggiornamento.
- Non modificare o cancellare file originali fuori dallo storage interno dell'app.
- Non introdurre scorciatoie che poi rendono difficile arrivare all'app desktop reale.
- Ogni scelta deve essere compatibile con una consegna beta entro fine agosto.

## Stato attuale

L'app oggi e' una app locale da sviluppo.

Componenti principali:

- frontend: `apps/viewer`;
- backend locale: `scripts/local_app.py`;
- launcher attuale: `scripts/run_app.py`;
- dati progetto: `data`;
- sample/viewer output: `apps/viewer/public/sample`;
- logica estrazione: `src/estrattore`.

Comando attuale:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --rebuild
```

Questo comando e' utile in sviluppo, ma non e' ancora un'esperienza desktop per utente finale.

## Piano di azione

### 0. Definire modello dati runtime e output finale

Obiettivo:

- definire come l'utente carica PDF o cartelle di PDF da elaborare;
- decidere se i PDF originali vengono copiati nello storage dell'app o solo referenziati;
- definire dove vengono salvati PDF originali, batch, manifest, immagini viewer e correzioni manuali;
- separare dati reali dell'app da `apps/viewer/public/sample`;
- definire cosa significa output finale dell'app dopo revisione manuale;
- decidere formato e posizione del salvataggio finale.

Perche' viene prima del packaging:

- oggi `apps/viewer/public/sample` e' sia output debug sia sorgente dati della UI;
- questa struttura e' accettabile in sviluppo ma non come base desktop finale;
- impacchettare prima di chiarire input/output produrrebbe una beta fragile;
- il launcher deve sapere quali dati sono runtime e quali sono risorse applicative.

Definizione teorica del problema:

- non stiamo solo scegliendo cartelle;
- stiamo definendo il **modello di persistenza documentale** dell'app;
- il problema generale e' governare il ciclo di vita dei documenti: import, elaborazione, revisione, salvataggio, export, aggiornamento e recupero.

Domande da risolvere dentro questo punto:

- l'utente carica singoli PDF, cartelle, batch gia' divisi o tutti e tre?
- quando un PDF viene caricato, lo copiamo dentro AppData o manteniamo solo il path originale?
- se il file originale viene spostato/cancellato dall'utente, cosa deve succedere?
- come identifichiamo in modo stabile un batch elaborato?
- dove salviamo immagini viewer e manifest generati?
- le modifiche manuali sovrascrivono il manifest o stanno in un file separato?
- qual e' il vero output finale: JSON, Excel, PDF rinominati, cartella di esportazione, o piu' formati?
- quando una fattura e' considerata completata?
- serve uno stato tipo bozza/completata/esportata?
- come si riapre un lavoro gia' iniziato?
- cosa resta solo debug e cosa diventa dato utente reale?

Prima ipotesi da valutare:

```text
AppData\EstrattoreScanFatture\
  batches\
    <batch_id>\
      input\
        originali copiati o riferimenti
      work\
        manifest, immagini viewer, debug essenziale
      corrections\
        modifiche manuali utente
      export\
        output finale
```

Decisione da non prendere in automatico:

- non spostare `public/sample` finche' non e' definito il modello runtime;
- non decidere output finale senza discutere con l'utente;
- non mescolare dati debug e dati finali.

#### Punto 0 parte I - Input PDF

Stato: completato nella prima forma.

Decisioni direzionali gia' prese dall'utente:

- i PDF originali devono essere mantenuti dall'app in una cartella locale;
- il caricamento deve essere simile a quello dell'anagrafica;
- per ora si affronta solo la parte input, non elaborazione, correzioni o output finale.

##### 1. Definizione teorica del problema

Il problema generale e': **gestire l'acquisizione e la persistenza locale dei documenti sorgente**.

Non e' solo upload file. E' il primo pezzo del ciclo documentale:

- selezione file da parte dell'utente;
- validazione formato;
- copia nello storage dell'app;
- assegnazione identita' stabile ai documenti;
- tracciamento del batch;
- possibilita' di riaprire in futuro cio' che e' stato caricato;
- base per elaborazione successiva senza dipendere dal path originale.

In una app desktop locale per uno studio e' corretto copiare i PDF dentro lo storage dell'app: aumenta uso disco, ma riduce fragilita' se l'utente sposta o cancella i file originali.

##### 2. Teoria risolutiva e best practice

Best practice:

- non fidarsi del path originale come unica fonte;
- copiare i file sorgente in una cartella controllata dall'app;
- salvare un indice leggero del batch;
- non caricare o rileggere tutti i PDF a ogni refresh;
- validare estensione e contenuto minimo;
- rinominare internamente in modo stabile/procedurale, mantenendo il nome originale nell'indice;
- usare scrittura atomica per l'indice;
- non mescolare input originali con manifest, immagini viewer o output finale.

Giudizio Codex:

- per la beta conviene creare batch importati dentro `AppData\batches`;
- ogni import crea un batch nuovo;
- i PDF vengono copiati in `input`;
- l'indice del batch contiene metadata leggeri;
- il frontend deve vedere la lista dei batch tramite API, non leggendo direttamente cartelle statiche.

##### 3. Approccio tecnico scelto

Struttura proposta per la sola parte input:

```text
AppData\EstrattoreScanFatture\
  batches\
    batch_YYYYMMDD_HHMMSS\
      batch_index.json
      input\
        doc_000001.pdf
        doc_000002.pdf
```

`batch_index.json` deve contenere almeno:

- `batch_id`;
- `created_at`;
- `document_count`;
- lista documenti con `document_id`, `stored_file_name`, `original_file_name`, `relative_path`, `size_bytes`;
- stato iniziale `imported`.

Decisione:

- il nome file interno e' stabile e procedurale;
- il nome originale resta metadata;
- niente elaborazione OCR in questo punto;
- niente output finale in questo punto.

##### 4. Micro-step

1. Leggere pattern attuale di import anagrafica.
2. Creare modulo Python per import batch PDF.
3. Aggiungere endpoint API per import/list/status batch.
4. Aggiungere UI minima vicino alla selezione batch o toolbar.
5. Verificare copia file, indice e lista batch senza OCR.
6. Aggiornare questo documento con esito e limiti.

##### 5. Implementazione eseguita

File introdotto:

- `src/estrattore/batch_storage.py`;
- `tests/test_batch_storage.py`.

File collegati:

- `scripts/registry_api.py`;
- `scripts/local_app.py`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/types.ts`;
- `apps/viewer/src/App.css`;
- `src/estrattore/runtime_paths.py`.

Funzionalita' implementata:

- import multiplo PDF via UI;
- endpoint `POST /batches/import`;
- endpoint `GET /batches`;
- copia dei PDF dentro `app_data_dir\batches\<batch_id>\input`;
- generazione `batch_index.json`;
- lista dei batch PDF importati nella sidebar;
- validazione minima: solo `.pdf`;
- staging temporaneo upload in `app_data_dir\uploads\pdf`;
- nessuna elaborazione OCR in questo punto.

Struttura creata:

```text
AppData\EstrattoreScanFatture\
  uploads\
    pdf\
  batches\
    batch_YYYYMMDD_HHMMSS\
      batch_index.json
      input\
        doc_000001.pdf
        doc_000002.pdf
```

Decisione durante implementazione:

- i file vengono rinominati internamente `doc_000001.pdf`, `doc_000002.pdf`, ecc.;
- il nome originale viene conservato in `batch_index.json`;
- questo evita problemi con nomi duplicati, caratteri strani o path lunghi;
- l'utente non perde informazione sul nome iniziale.

Bug evitato:

- nell'upload multipart il backend salva prima file temporanei;
- se avessi passato solo il path temporaneo allo storage, avremmo perso `original_file_name`;
- ho introdotto `PdfBatchSource` per passare insieme path temporaneo e nome originale.

##### 6. Verifiche eseguite

Test automatici:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 8 passed.

Controllo sintassi Python:

```powershell
.\.venv\Scripts\python.exe -m py_compile src\estrattore\batch_storage.py src\estrattore\runtime_paths.py scripts\registry_api.py scripts\local_app.py
```

Esito:

- ok.

Typecheck frontend:

```powershell
npm exec -- tsc -b
```

Esito:

- ok.

Build frontend:

```powershell
npm run build
```

Esito:

- ok dopo rilancio fuori sandbox per `spawn EPERM`, problema gia' osservato con Vite in questo ambiente.

Regression mirata:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_estrattore.py -k "registry or viewer_manifest"
```

Esito:

- 20 passed.

##### 7. Revisione critica

Valutazione:

- l'input PDF ora e' un dato runtime reale, non un sample dentro il frontend;
- il caricamento e' simile all'anagrafica: file picker browser + upload al backend locale;
- il backend copia i PDF nello storage app, quindi non dipende piu' dal path originale;
- l'indice batch e' leggero e permette futura UI senza leggere tutti i PDF;
- la UI distingue ancora correttamente batch importati da batch viewer elaborati.

Limiti residui:

- non c'e' ancora elaborazione OCR dei batch importati;
- i batch importati non sono ancora selezionabili per visualizzazione fatture;
- non c'e' ancora cancellazione batch;
- non c'e' ancora import cartella via directory picker;
- non c'e' ancora stato `processed/completed/exported`;
- non c'e' ancora gestione contenuto PDF corrotto oltre alla validazione estensione.

Domande risolte:

- i PDF originali vengono copiati dentro AppData;
- ogni import crea un batch nuovo;
- il nome originale resta metadata;
- il frontend non legge direttamente la cartella, ma usa API.

Domande rimaste aperte:

- caricamento cartella intera vs selezione multipla file;
- quando e come partire con elaborazione OCR;
- come cancellare/archiviare batch importati;
- come collegare `batch_index.json` a manifest e correzioni;
- quale sara' l'output finale dopo revisione manuale.

##### 8. Chiusura Punto 0 parte I

La parte input PDF e' completata come scheletro beta: l'app puo' prendere in carico PDF reali e conservarli nello storage runtime.

Non considero completato il modello dati finale: ora esiste solo l'acquisizione sorgente. Il prossimo sotto-punto dovra' decidere come un batch importato passa da `imported` a `processed`, dove vengono salvati manifest/immagini viewer, e come la UI sceglie tra batch importati e batch elaborati.

#### Punto 0 parte II - Processamento, batch di revisione e salvataggio progressivo

Stato: definizione teorica.

Nota: questa sezione descrive il flusso immaginato dall'utente e lo rielabora in forma progettuale. Non e' ancora implementazione.

##### 1. Definizione teorica del problema

Il problema generale e': **trasformare documenti sorgente caricati dall'utente in un flusso di lavoro revisionabile, salvabile progressivamente e archiviabile in modo stabile**.

Non stiamo solo "processando PDF". Stiamo definendo una pipeline documentale con stati:

- input grezzo;
- elaborazione OCR;
- split in fatture;
- revisione manuale;
- salvataggio nel registro;
- archiviazione PDF finale;
- eliminazione dei materiali temporanei.

Questa distinzione e' importante perche' il sistema deve poter gestire:

- un singolo PDF con molte pagine;
- molti PDF singoli;
- cartelle caricate dall'utente;
- fatture multiple dentro lo stesso PDF;
- salvataggio di una fattura alla volta;
- salvataggio di un intero batch;
- eliminazione di una fattura o di un batch;
- tracciabilita' verso il documento sorgente.

##### 2. Flusso utente rielaborato

Flusso proposto:

```text
1. L'utente carica PDF o cartelle.
2. L'app controlla che i file validi siano solo PDF.
3. Se vengono caricate cartelle, l'app estrae tutti i PDF contenuti e appiattisce l'input.
4. Tutti i PDF vengono copiati in una cartella unica "file da processare".
5. L'utente puo' vedere questi PDF e aprirli in sola lettura.
6. L'utente avvia il processamento.
7. L'app processa tutti i PDF caricati.
8. Ogni PDF viene diviso in fatture.
9. Le fatture vengono raggruppate in gruppi di revisione, ad esempio 15 fatture per gruppo.
10. L'utente apre un gruppo e revisiona le 15 fatture.
11. Ogni fattura puo' essere salvata singolarmente o eliminata.
12. Salvando una fattura, questa sparisce dal batch di revisione.
13. La fattura salvata entra nel registro applicativo.
14. Il PDF singolo della fattura viene spostato da archivio temporaneo ad archivio finale.
15. Quando tutte le fatture di un batch sono salvate/eliminate, il batch si chiude.
```

##### 3. Struttura dati concettuale

Prima fase, file da processare:

```text
AppData\EstrattoreScanFatture\
  intake\
    files_to_process\
      source_000001.pdf
      source_000002.pdf
      intake_index.json
```

Seconda fase, area di lavoro persistente:

```text
AppData\EstrattoreScanFatture\
  processing\
    process_YYYYMMDD_HHMMSS\
      source_index.json
      invoices\
        invoice_tmp_000001\
          viewer_manifest.json
          invoice.pdf
          pages...
        invoice_tmp_000002\
          viewer_manifest.json
          invoice.pdf
      review_batches\
        review_batch_0001.json
        review_batch_0002.json
```

Terza fase, registro e archivio finale:

```text
AppData\EstrattoreScanFatture\
  registry\
    invoices_registry.json
  archive\
    f<codice_identificativo>_<ente>_<condominio>_<data>.pdf
```

Nota: i nomi sono provvisori. Il concetto importante e' separare input, area di lavoro persistente, registro finale e archivio finale.

Regola di persistenza:

- "temporaneo" non significa volatile;
- ogni dato prodotto dall'app deve sopravvivere alla chiusura;
- manifest, immagini viewer, review batch e stato processamento sono dati di lavoro persistenti;
- solo una azione esplicita dell'utente puo' cancellare dati interni dell'app;
- il sistema deve poter distinguere dati `in lavorazione` da dati `finali`, ma entrambi restano su disco.

##### 4. Stati proposti

Per i file sorgente:

```text
queued
processing
processed
archived
deleted
error
```

Per le fatture:

```text
ready_for_review
saved
deleted
exported
error
```

Per i batch di revisione:

```text
open
partially_completed
completed
deleted
```

##### 5. Commento tecnico Codex

La direzione e' buona: separa chiaramente caricamento, processamento, revisione e archivio finale.

La parte piu' forte dell'idea e':

- non far dipendere il lavoro dai PDF originali scelti dall'utente;
- creare PDF intermedi gia' splittati per fattura;
- salvare una fattura alla volta;
- far sparire dal batch cio' che e' stato completato;
- costruire progressivamente un registro consultabile.

Questa e' una logica molto adatta a uno studio, perche' riduce il rischio di dover completare tutto in una singola sessione.

##### 6. Criticita' da correggere o precisare

Prima criticita': "file da processare" come cartella unica.

Va bene come concetto utente, ma tecnicamente serve anche un indice. Una cartella con tanti PDF e basta non e' sufficiente: dobbiamo sapere nome originale, provenienza, data caricamento, stato, eventuale cartella sorgente, errori.

Seconda criticita': cancellare i PDF originali dopo processamento.

La direzione e' sensata, ma va formulata meglio. Non cancelliamo i file originali dell'utente sul disco. Cancelliamo solo la copia interna in `files_to_process` dopo aver prodotto:

- PDF splittati per fattura;
- indice sorgente;
- collegamento tra fatture e file origine.

Terza criticita': Excel come registro principale.

Meglio non usare Excel come storage primario dell'app. Excel e' ottimo come export, ma fragile come database applicativo. Il registro principale dovrebbe essere JSON strutturato o SQLite. Per la beta, probabilmente JSON puo' bastare; per crescita futura SQLite diventera' piu' adatto.

Scelta preferita Codex:

- registro interno applicativo strutturato;
- UI tabellare stile anagrafica con ricerca e filtri;
- export Excel solo su richiesta.

Quarta criticita': gruppi da 15 fatture.

Ottima come unita' di revisione UI, ma non deve essere il modo in cui salviamo fisicamente tutto. Il gruppo da 15 e' una vista operativa. Le fatture devono avere identita' stabile indipendente dal gruppo in cui vengono mostrate.

##### 7. Strategia proposta

Separare quattro concetti:

```text
Intake
= PDF caricati e copiati nell'app, non ancora processati.

Processing run
= sessione di elaborazione OCR/split su uno o piu' PDF.

Review batch
= gruppo tecnico di fatture da revisionare, mostrato all'utente come gruppo, ad esempio 15 per volta.

Invoice registry
= archivio logico finale delle fatture salvate.
```

Questa separazione evita di confondere:

- file sorgenti;
- tentativi OCR;
- batch di lavoro;
- risultato finale.

##### 8. Identificativo e tracciabilita'

Ogni PDF sorgente deve avere un identificativo stabile:

```text
source_id
```

Ogni fattura splittata deve avere un identificativo stabile:

```text
invoice_id
```

Ogni fattura deve conservare il collegamento:

```text
invoice_id -> source_id -> original_file_name
```

Il nome finale PDF puo' includere un codice identificativo:

```text
f<invoice_id>_<nome_ente>_<nome_condominio>_<data>.pdf
```

Pero' il collegamento affidabile non deve dipendere solo dal nome file. Deve stare anche nei metadati del registro.

##### 9. Decisioni consigliate

Decisioni che Codex propone:

- usare una cartella `intake` o `files_to_process` per i PDF caricati;
- appiattire le cartelle caricate mantenendo nel metadata il path relativo originale;
- non usare Excel come storage principale;
- usare registro interno consultabile dalla UI;
- esportare Excel solo come output derivato;
- creare PDF splittati per fattura prima di cancellare la copia intake;
- salvare ogni fattura singolarmente nel registro;
- rimuovere dal batch di revisione le fatture salvate o eliminate;
- gruppi di revisione da 15 fatture come default configurabile.

Decisioni ancora da discutere:

- JSON o SQLite per il registro finale;
- se nella beta basta selezione multipla PDF o serve subito caricamento cartelle;
- come gestire errori su singolo PDF dentro un gruppo grande;
- se eliminare una fattura significa cancellare definitivamente il PDF intermedio o spostarlo in cestino interno;
- formato esatto del registro finale;
- quali campi minimi rendono una fattura "salvabile".

##### 10. Scope implementativo corrente

Decisione per la prima implementazione del Punto 0 parte II:

- implementare il passaggio `imported -> processed`;
- processare i PDF gia' caricati nello storage runtime;
- generare manifest viewer nello storage runtime, non in `public/sample`;
- creare gruppi di revisione da 15 fatture;
- permettere alla UI di caricare i review batch prodotti;
- non implementare ancora salvataggio fattura nel registro finale;
- non implementare ancora export Excel;
- non eliminare ancora copie interne `input` dopo processamento;
- non creare ancora PDF splittati fisici per fattura se richiede introdurre una dipendenza PDF dedicata.

Motivo:

- la pipeline OCR/split esiste gia' e va riusata;
- il primo valore beta e' poter passare da PDF caricati a fatture revisionabili;
- registro finale e archivio PDF sono un punto successivo separato, perche' definiscono dati permanenti.

Decisione tecnica:

- i gruppi di revisione da 15 sono una vista operativa;
- se un singolo PDF contiene molte fatture, piu' review batch possono referenziare lo stesso manifest documento filtrando invoice id diversi;
- questo evita duplicazione pesante di manifest/immagini e risolve il caso del PDF unico da molte pagine.

Decisione durante esecuzione:

- il processamento non deve essere una richiesta HTTP lunga sincrona;
- anche nella beta va trattato come job locale;
- `POST /batches/process` avvia il lavoro;
- la UI interroga lo stato del job;
- quando il job termina, la UI ricarica i review batch disponibili.

Motivo:

- OCR su molti PDF puo' durare minuti;
- una richiesta HTTP bloccante renderebbe l'app fragile e opaca;
- un job locale e' piu' vicino alla forma finale e permette progresso/log/errori.

##### 11. Implementazione eseguita

File introdotti:

- `src/estrattore/processing_storage.py`;
- `tests/test_processing_storage.py`.

File collegati:

- `scripts/registry_api.py`;
- `scripts/local_app.py`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/types.ts`;
- `apps/viewer/src/App.css`.

Funzionalita' implementata:

- endpoint `POST /batches/process` per avviare processamento batch importato;
- processamento eseguito come job locale in background;
- endpoint `GET /processing/jobs/<job_id>` per polling stato;
- endpoint `GET /viewer/review-batches` per lista batch revisionabili;
- file server sicuro per `/runtime/manifests/...`;
- generazione manifest viewer in `app_data_dir\manifests`;
- creazione gruppi di revisione da 15 fatture;
- supporto al caso PDF singolo con molte fatture tramite `review_invoice_ids`;
- UI: bottone `Processa` sui batch PDF importati;
- UI: stato `Processamento OCR...`;
- UI: caricamento automatico dei review batch processati prima del fallback `sample`.

Struttura runtime prodotta:

```text
AppData\EstrattoreScanFatture\
  manifests\
    process_YYYYMMDD_HHMMSS\
      processing_run_index.json
      documents\
        doc_000001\
          viewer_manifest.json
          page-001-boxes.png
      review_batches\
        review_batch_0001\
          viewer_index.json
          review_batch_index.json
```

Decisione tecnica importante:

- non duplicare manifest e immagini per ogni review batch;
- un review batch contiene riferimenti a documenti piu' un filtro `review_invoice_ids`;
- il frontend carica il manifest documento e mostra solo le fatture richieste dal review batch.

Motivo:

- risolve il caso PDF unico da molte fatture;
- evita duplicazione pesante;
- mantiene identita' stabile del documento sorgente;
- lascia i gruppi da 15 come vista operativa, non come storage fisico primario.

##### 12. Verifiche eseguite

Test automatici:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 10 passed.

Regression mirata:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_estrattore.py -k "registry or viewer_manifest"
```

Esito:

- 20 passed.

Controllo sintassi Python:

```powershell
.\.venv\Scripts\python.exe -m py_compile src\estrattore\processing_storage.py src\estrattore\batch_storage.py scripts\registry_api.py scripts\local_app.py
```

Esito:

- ok.

Typecheck frontend:

```powershell
npm exec -- tsc -b
```

Esito:

- ok.

Build frontend:

```powershell
npm run build
```

Esito:

- ok con esecuzione fuori sandbox per il problema noto `spawn EPERM`.

##### 13. Revisione critica

Valutazione:

- il flusso ora e' realmente diviso: upload PDF, processamento, review batch;
- il processamento non scrive piu' in `public/sample`;
- la UI puo' aprire review batch runtime;
- il caso "un PDF enorme con molte fatture" e' gestito a livello di invoice id;
- la soluzione non cancella file esterni e non tocca sorgenti originali dell'utente.

Limiti residui:

- job progress e' qualitativo, non percentuale;
- lo stato job e' in memoria: se l'app si chiude durante OCR, il job non riprende automaticamente;
- non vengono ancora creati PDF splittati fisici per fattura;
- non viene ancora eliminata la copia interna `input` dopo processamento;
- non esiste ancora registro finale fatture;
- non esiste ancora salvataggio/eliminazione singola fattura dal review batch;
- non esiste ancora cancellazione batch.

Decisione consapevole:

- non introdurre ora una dipendenza PDF dedicata solo per splittare fisicamente i PDF;
- prima rendere stabile il flusso `imported -> processed -> review`;
- affrontare split PDF fisico insieme ad archivio finale e salvataggio fattura.

Correzione concettuale successiva:

- lo stato `processing` deve essere scritto su disco a inizio job;
- se l'app si chiude durante OCR, alla riapertura deve restare una traccia persistente del processo incompleto;
- il job in memoria serve solo per polling live della UI, non come unica fonte dello stato di lavorazione.

##### 14. Chiusura Punto 0 parte II

Punto 0 parte II completato per la parte intermedia: i PDF caricati possono essere processati in job locale e trasformati in gruppi di revisione da 15 fatture.

Non e' ancora completato il flusso finale di studio: mancano salvataggio fattura, registro finale, archiviazione PDF finale e cancellazione/chiusura batch.

Prossimo punto consigliato:

- Punto 0 parte III: definire e implementare registro fatture revisionate, salvataggio/eliminazione singola fattura e comportamento della batch quando una fattura viene completata.

#### Punto 0 parte III - Registro fatture revisionate e completamento review batch

Stato: in corso.

##### 1. Definizione teorica del problema

Il problema generale e': **trasformare una fattura estratta e revisionata in un record applicativo persistente, ricercabile e archiviabile, mantenendo tracciabilita' verso i documenti di lavorazione**.

Non e' solo un pulsante "salva". Il salvataggio di una fattura definisce:

- il passaggio da dato di lavoro a dato ufficiale dell'app;
- quali campi vengono considerati validati dall'utente;
- come resta tracciata la provenienza;
- cosa succede al review batch;
- dove vive il registro consultabile;
- come preparare export Excel futuro;
- come gestire eliminazione/scarto.

##### 2. Teoria risolutiva e best practice

Best practice:

- non usare il manifest OCR come registro finale;
- mantenere un registro applicativo separato;
- salvare snapshot dei campi al momento della conferma utente;
- mantenere collegamenti a `process_id`, `review_batch_id`, `document_id`, `invoice_id`, `source_batch_id`;
- non cancellare immediatamente dati di lavoro senza una regola esplicita;
- rendere ogni operazione idempotente: salvare due volte la stessa fattura non deve duplicarla;
- registrare lo stato della fattura nel review batch;
- export Excel derivato dal registro, non storage primario.

Giudizio Codex:

- per la beta iniziale scelgo JSON strutturato come registro interno;
- SQLite e' piu' adatto nel medio termine, ma introdurlo ora aumenta scope e migrazione;
- il JSON deve avere schema esplicito e scrittura atomica;
- UI tabellare completa arrivera' dopo, ma lo storage deve essere pronto.

##### 3. Scope implementativo corrente

Implementare ora:

- registro interno `invoices_registry.json`;
- endpoint per salvare una fattura revisionata;
- endpoint per eliminare/scartare una fattura dal review batch;
- aggiornamento persistente dello stato nel `review_batch_index.json`;
- rimozione visiva della fattura dal batch caricato dopo salvataggio/eliminazione;
- comportamento idempotente sul salvataggio.

Non implementare ora:

- export Excel del registro;
- archivio PDF finale rinominato;
- split fisico PDF per fattura;
- filtri avanzati registro;
- UI tabellare registro stile anagrafica.

##### 4. Micro-step

1. Mappare quali dati della fattura sono disponibili nel frontend al momento del salvataggio.
2. Definire struttura JSON del registro fatture.
3. Implementare storage Python del registro con scrittura atomica.
4. Implementare endpoint save/delete invoice.
5. Aggiornare review batch index persistente.
6. Collegare UI minima: pulsanti Salva fattura / Elimina fattura.
7. Testare idempotenza, stato review batch e persistenza.

Domande da risolvere dentro questo punto:

- quali campi minimi servono per permettere il salvataggio?
- eliminare significa scartare in modo persistente o spostare in cestino interno?
- la fattura salvata sparisce subito dalla UI o resta visibile come completata?
- cosa succede se salvo una fattura con campi vuoti?
- come gestire modifiche manuali non ancora persistite nel frontend?

##### 5. Decisioni prese durante implementazione

Chiarimento importante dell'utente:

- tutti i dati prodotti dall'app devono essere persistenti anche se sono dati di lavoro;
- "temporaneo" significa non finale, non volatile;
- chiudere e riaprire l'app non deve far perdere batch processati, manifest, review batch o stato di avanzamento;
- l'app non deve mai cancellare o modificare file fuori dal proprio storage.

Decisione:

- il registro fatture revisionate vive nello storage runtime dell'app;
- il manifest OCR resta dato di lavorazione;
- il registro interno e' un JSON applicativo separato;
- `Elimina` nella review significa `discarded` persistente, non cancellazione fisica aggressiva;
- la fattura salvata o scartata viene rimossa dal review batch persistente, cosi' non ricompare al riavvio;
- per ora il salvataggio non blocca campi vuoti, perche' non abbiamo ancora definito una regola di validazione finale e l'utente deve poter completare gradualmente la beta.

##### 6. Implementazione eseguita

File introdotto:

- `src/estrattore/invoice_registry_storage.py`;
- `tests/test_invoice_registry_storage.py`.

File modificati:

- `src/estrattore/runtime_paths.py`;
- `src/estrattore/processing_storage.py`;
- `scripts/registry_api.py`;
- `scripts/local_app.py`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/App.css`;
- `apps/viewer/src/types.ts`;
- `tests/test_runtime_paths.py`.

Struttura runtime aggiunta:

```text
AppData\EstrattoreScanFatture\
  invoice_registry\
    invoices_registry.json
```

Registro interno:

- schema esplicito `schema_version`;
- lista `records`;
- record idempotente basato su batch runtime + invoice id;
- snapshot della fattura vista/modificata dal frontend;
- stato `saved` o `discarded`;
- timestamp applicativo.

Review batch:

- `ProcessedInvoiceRef` ha ora stato `pending/saved/discarded`;
- `mark_review_invoice(...)` aggiorna `review_batch_index.json`;
- `viewer_index.json` del review batch viene aggiornato rimuovendo l'invoice id completato;
- `list_review_batches(...)` legge il conteggio corrente e non restituisce batch senza fatture pending.

API:

- `GET /invoices/registry`;
- `POST /invoices/save`;
- `POST /invoices/discard`.

Frontend:

- nel pannello campi compaiono `Salva fattura` ed `Elimina` solo per batch runtime;
- dopo salvataggio/scarto la fattura sparisce dalla review aperta;
- lo stato operativo viene mostrato nel pannello;
- se l'ultima fattura viene completata, la lista batch viene svuotata correttamente.

##### 7. Verifiche eseguite

Test backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_invoice_registry_storage.py tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 13 passed;
- warning noto: pytest non riesce a scrivere `.pytest_cache` per permessi locali, non incide sul risultato.

Build frontend:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- ok;
- necessario rilancio fuori sandbox per il noto `spawn EPERM` di Vite su Windows in questo ambiente.

##### 8. Revisione critica e chiusura Punto 0 parte III

Punto 0 parte III completato per la prima forma beta:

- esiste un registro persistente separato dai manifest OCR;
- il salvataggio usa i dati effettivamente presenti nel frontend, quindi include modifiche manuali e arricchimenti anagrafica;
- lo stato del review batch sopravvive al riavvio;
- lo storage resta dentro la cartella dati dell'app;
- non sono state introdotte cancellazioni distruttive fuori dall'app.

Limiti residui consapevoli:

- manca export Excel del registro fatture;
- manca tabella consultabile del registro fatture;
- manca split fisico dei PDF per fattura e archivio finale rinominato;
- manca validazione formale dei campi obbligatori prima del salvataggio;
- manca annullamento dello scarto/salvataggio, che in futuro puo' diventare un cestino interno o uno stato ripristinabile.

Prossimo punto consigliato:

- Punto 0 parte IV: definire output finale, split fisico dei PDF salvati, archivio consultabile e strumenti minimi di correzione batch.

#### Punto 0 parte IV - Output finale, split PDF e archivio fatture

Stato: in corso.

##### 1. Workflow utente atteso

Workflow principale ricostruito dall'utente:

```text
caricamento PDF/cartelle
  -> copia interna dei PDF dentro storage app
  -> estrazione OCR e split logico fatture
  -> batch di revisione da consultare
  -> modifica manuale dei campi
  -> Salva
  -> split fisico della fattura dal PDF sorgente copiato
  -> PDF finale archiviato
  -> record nel registro/archivio fatture
```

Punto chiave:

- lo split fisico definitivo non avviene subito dopo l'estrazione;
- avviene quando l'utente salva una fattura revisionata;
- prima del salvataggio la fattura e' una entita' logica dentro il batch di review;
- dopo il salvataggio diventa una fattura archiviata con PDF fisico proprio.

##### 2. Cosa e' gia' implementato

Fase 1, caricamento file:

- implementato import multiplo di singoli PDF;
- i PDF caricati vengono copiati nello storage dell'app;
- il PDF originale esterno dell'utente non viene modificato;
- esiste `batch_index.json`;
- il nome originale del file viene mantenuto come metadata.

Non ancora implementato nella fase 1:

- caricamento cartelle dal browser/frontend;
- appiattimento automatico di piu' cartelle in singoli PDF;
- politica di retention della copia interna caricata.

Fase 2, estrazione:

- implementato processamento del batch importato;
- lo split logico delle fatture usa l'algoritmo esistente;
- ogni fattura ha `invoice_id`, `page_numbers`, campi estratti e riferimento al documento;
- la numerazione progressiva globale e' gestita nel processamento batch.

Fase 3, batch dopo estrazione:

- implementati gruppi di revisione da 15 fatture;
- il viewer carica i batch processati da runtime storage;
- le fatture sono consultabili e modificabili prima del salvataggio;
- non c'e' ancora split fisico definitivo in questa fase, ed e' corretto.

Fase 4, modifica campi:

- implementata modifica campi nel frontend;
- implementato arricchimento anagrafica;
- implementato salvataggio dello snapshot della fattura come vista/modificata dall'utente.

Fase 5, salvataggio:

- implementato primo salvataggio persistente nel registro interno JSON;
- implementato stato `saved`/`discarded`;
- implementata rimozione della fattura dalla review dopo save/discard;
- implementata persistenza del review batch aggiornato.

Non ancora implementato nella fase 5:

- split fisico del PDF al momento del salvataggio;
- nome finale del PDF;
- spostamento del PDF finale in archivio;
- collegamento del record salvato al PDF fisico finale;
- rinomina/spostamento del file sorgente copiato dopo completamento lavorazione.

Fase 6, archivio fatture:

- esiste solo il registro JSON interno;
- non esiste ancora una pagina archivio consultabile;
- non esiste ancora export Excel del registro fatture;
- non esiste ancora link apribile al PDF finale.

##### 3. Definizione teorica del problema

Il problema generale e': **trasformare una review OCR in un archivio documentale applicativo**.

Non e' solo esportare file. Dobbiamo governare il ciclo finale:

- validazione dati;
- creazione PDF fisico della singola fattura;
- naming stabile e leggibile;
- tracciabilita' verso sorgente, batch e processo;
- consultazione futura;
- export;
- correzione errori di raggruppamento;
- recupero da errori o scarti.

Il registro interno deve diventare la fonte consultabile dell'app. Il PDF finale deve essere un allegato archiviato e linkato dal record.

##### 4. Teoria risolutiva e best practice

Best practice:

- non modificare mai i PDF originali esterni dell'utente;
- lavorare solo sulle copie interne dell'app;
- mantenere i file sorgente interni finche' possono servire a generare PDF finali;
- non fare split fisico prematuro prima della review;
- salvare il PDF finale solo dopo conferma utente;
- mantenere id stabile tra record fattura e PDF archiviato;
- usare nomi file leggibili ma non affidarsi solo al nome come identificativo;
- il registro applicativo deve contenere percorso/URI interno al PDF finale;
- l'export Excel deve derivare dal registro, non diventare storage primario.

Giudizio Codex:

- il flusso proposto dall'utente e' corretto;
- lo split logico in estrazione e lo split fisico in salvataggio devono restare separati;
- questo riduce spreco di file e permette all'utente di correggere campi prima di produrre archivio finale;
- la copia interna del PDF sorgente non va eliminata appena finisce l'OCR, perche' serve allo split fisico successivo;
- la cancellazione o retention dei sorgenti interni va decisa dopo, con una regola esplicita e visibile.

##### 5. Struttura dati proposta per il punto IV

Prima ipotesi runtime:

```text
AppData\EstrattoreScanFatture\
  batches\
    batch_...\input\doc_000001.pdf
  manifests\
    process_...\documents\doc_000001\viewer_manifest.json
    process_...\review_batches\review_batch_0001\...
  invoice_registry\
    invoices_registry.json
  archive\
    invoices\
      f_<invoice_uid>_<emittente>_<condominio>_<data>.pdf
    sources\
      processed_<source_batch_id>_<document_id>.pdf
```

Nota:

- `archive/invoices` contiene i PDF finali consultabili;
- `archive/sources` e' una possibile destinazione per sorgenti interni gia' completamente lavorati;
- la scelta se archiviare o eliminare i sorgenti interni resta aperta.

Record registro futuro:

- `record_id`;
- `invoice_id`;
- `source_batch_id`;
- `process_id`;
- `document_id`;
- `source_file_name`;
- `page_numbers`;
- `fields`;
- `status`;
- `archived_pdf_path`;
- `created_at`;
- `saved_at`;
- eventuale `discarded_at` o `restored_at`.

##### 6. Casi da affrontare dopo il flusso principale

Pagine non pertinenti:

- se una pagina inutile viene accorpata alla fattura precedente, l'estrazione dati puo' anche non peggiorare;
- ma il PDF finale risulterebbe sporco;
- serve almeno una funzione "scarta pagina dalla fattura".

Errori di raggruppamento:

- pagina assegnata alla fattura sbagliata;
- due fatture unite erroneamente;
- una fattura divisa in due;
- pagina extra non appartenente a nessuna fattura.

Questi strumenti non vanno progettati come OCR, ma come editor del raggruppamento:

- sposta pagina;
- separa fattura;
- unisci fatture;
- scarta pagina;
- ripristina pagina scartata.

Non implementarli prima dello split fisico base, ma tenere il modello dati pronto.

##### 7. Micro-step consigliati per implementare il punto IV

1. Estendere runtime paths con cartella `archive`.
2. Salvare nel registro abbastanza metadata per risalire al PDF sorgente interno e alle pagine.
3. Implementare utility Python di split fisico da PDF sorgente copiato + page numbers.
4. Agganciare lo split a `POST /invoices/save`.
5. Salvare `archived_pdf_path` nel record fattura.
6. Aggiungere endpoint sicuro per aprire/scaricare PDF archiviato.
7. Creare pagina archivio fatture consultabile stile anagrafica.
8. Aggiungere filtri e ordinamento base.
9. Aggiungere export Excel derivato dal registro.
10. Solo dopo: strumenti di correzione raggruppamento pagine.

##### 8. Domande aperte del punto IV

- Quali campi minimi rendono una fattura salvabile?
- Il PDF finale va generato per ogni singolo `Salva` o solo con `Salva batch`?
- Il nome PDF finale deve essere modificabile dall'utente?
- Se due PDF finali avrebbero lo stesso nome leggibile, che suffisso usiamo?
- Il sorgente interno va archiviato, mantenuto in lavoro o eliminato dopo completamento batch?
- Serve una schermata "batch completato"?
- Lo scarto deve essere ripristinabile da cestino interno?
- L'archivio deve mostrare anche fatture scartate o solo salvate?
- L'export Excel deve esportare tutto o solo il filtro corrente?

##### 9. Prima implementazione eseguita: split fisico al salvataggio

Obiettivo affrontato:

- quando l'utente salva una fattura revisionata, generare anche il PDF fisico finale della singola fattura;
- usare il PDF sorgente interno copiato dall'app, non il file originale esterno dell'utente;
- usare i numeri pagina gia' determinati dallo split logico;
- salvare nel registro il riferimento al PDF archiviato.

Decisione tecnica:

- introdotta dipendenza `pypdf`;
- lo split PDF sta in un modulo separato dal registro;
- il registro chiama lo split durante `save_reviewed_invoice`;
- se lo split fallisce, la fattura non viene marcata come salvata;
- `discard` non genera PDF fisico.

File introdotti:

- `src/estrattore/invoice_pdf_archive.py`;
- `tests/test_invoice_pdf_archive.py`.

File modificati:

- `pyproject.toml`;
- `src/estrattore/runtime_paths.py`;
- `src/estrattore/processing_storage.py`;
- `src/estrattore/invoice_registry_storage.py`;
- `scripts/local_app.py`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/types.ts`;
- `tests/test_invoice_registry_storage.py`;
- `tests/test_runtime_paths.py`.

Runtime paths aggiunti:

```text
AppData\EstrattoreScanFatture\
  archive\
    invoices\
```

Contratto implementato:

- il frontend passa anche `manifestInvoiceId` e `pageNumbers`;
- il backend usa `manifestInvoiceId` per agganciare il record giusto nel review batch;
- il backend legge `source_batch_id` e `document_id` dal `review_batch_index.json`;
- il PDF sorgente interno atteso e':

```text
batches\<source_batch_id>\input\<document_id>.pdf
```

- il PDF finale viene scritto in:

```text
archive\invoices\f_<uid>_<emittente>_<condominio>_<data>.pdf
```

- il record registro salvato contiene:
  - `archived_pdf_path`;
  - `archived_pdf_url`;
  - `archived_pdf_page_numbers`;
  - `source_batch_id`;
  - `document_id`;
  - `source_file_name`.

Endpoint statico aggiunto:

- `GET /runtime/archive/...`;
- serve solo file dentro `archive`;
- include controllo anti path traversal.

Correzione importante:

- prima il frontend usava un `invoiceId` composito come `doc_000001:invoice-001`;
- il backend invece conosce nel review batch l'id grezzo `invoice-001`;
- ora il frontend invia entrambi:
  - `invoiceId` per unicita' UI;
  - `manifestInvoiceId` per aggancio backend.

##### 10. Verifiche eseguite per la prima implementazione

Installata dipendenza:

```powershell
.\.venv\Scripts\python.exe -m pip install pypdf>=4.0.0
```

Test:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_invoice_pdf_archive.py tests\test_invoice_registry_storage.py tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 15 passed;
- warning noto su `.pytest_cache` non scrivibile.

Build frontend:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- ok.

##### 11. Limiti residui del Punto 0 parte IV

Ancora da fare:

- pagina archivio fatture consultabile;
- link visibile in UI al PDF archiviato;
- export Excel del registro fatture;
- validazione campi prima del salvataggio;
- `Salva batch`;
- gestione sorgente interno dopo completamento batch;
- strumenti di correzione raggruppamento pagine;
- caricamento cartelle e appiattimento PDF.

Prossimo micro-step consigliato:

- creare una prima pagina archivio fatture che legga `invoices_registry.json`, mostri i record salvati e permetta di aprire il PDF finale.

#### Punto 0 parte V - Archivio fatture consultabile e backlog output finale

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **rendere consultabile il risultato finale del lavoro utente**.

Dopo il Punto 0 parte IV una fattura salvata puo' generare un PDF fisico finale e un record persistente. Pero' senza una schermata archivio l'utente non ha ancora un modo naturale per:

- vedere cosa e' gia' stato salvato;
- cercare una fattura;
- aprire il PDF finale;
- verificare che il salvataggio abbia prodotto un documento consultabile;
- usare il registro come base gestionale.

Questo punto non deve risolvere tutti gli output finali. Deve creare il primo archivio leggibile e lasciare il backlog ordinato.

##### 2. Teoria risolutiva e best practice

Best practice:

- distinguere review da archivio;
- mostrare solo fatture `saved` nell'archivio principale;
- non mostrare record `discarded` come se fossero fatture archiviate;
- leggere l'archivio dal registro applicativo interno;
- aprire i PDF finali tramite endpoint runtime sicuro;
- non duplicare lo stato archivio nel frontend;
- fornire ricerca testuale prima di filtri complessi;
- rimandare export Excel e correzione raggruppamenti a punti successivi.

Giudizio Codex:

- una prima pagina archivio e' il prossimo passo corretto dopo lo split fisico;
- la beta deve far vedere all'utente che `Salva` produce qualcosa di recuperabile;
- filtri avanzati e ordinamento custom possono arrivare dopo, perche' la struttura dati ora e' pronta;
- la vista deve essere coerente con la pagina anagrafica, per ridurre complessita' visiva.

##### 3. Resto da fare riorganizzato

Backlog funzionale rimasto dopo riallineamento con l'utente:

1. Validazione campi prima del salvataggio.
   - deve restare semplice e pragmatica;
   - controllare che P.IVA e codici fiscali siano formalmente corretti quando presenti;
   - controllare se ci sono campi vuoti;
   - se ci sono campi vuoti, avvertire e chiedere se continuare comunque;
   - se si salva una fattura con un nuovo dato anagrafico, mostrarlo e chiedere se inserirlo in anagrafica;
   - se il nuovo dato anagrafico ha lo stesso codice fiscale di un record gia' presente ma dati diversi, chiedere conferma esplicita prima di continuare;
   - non costruire subito un sistema rigido di blocchi complessi.

2. Salvataggio o eliminazione intera batch.
   - non e' un processo concettualmente diverso da salva/elimina singola fattura;
   - cambia solo il numero di target;
   - `Salva batch` salva tutte le fatture pending;
   - `Elimina batch` scarta tutte le fatture pending;
   - serve report finale con successi, errori e fatture rimaste pending.

3. Archivio dei file sorgente interni.
   - i PDF splittati e rinominati dopo il salvataggio vanno salvati come ora;
   - anche i file sorgente interni copiati dall'app vanno salvati, non eliminati;
   - se lo spazio disco diventera' un problema si introdurra' retention/cancellazione;
   - la pagina archivio fatture deve permettere accesso anche al file sorgente;
   - ogni fattura deve avere link al PDF finale e riferimento/link al PDF sorgente;
   - se due PDF finali hanno lo stesso nome leggibile, usare stile Windows `nome.pdf`, `nome(1).pdf`, `nome(2).pdf`;
   - anche a parita' di nome leggibile i file vanno comunque salvati: l'archivio e' storico di estrazione, non deduplica.

4. Strumenti di correzione raggruppamento pagine.
   - resta intenzionalmente alla fine perche' e' il blocco piu' complesso;
   - scarta pagina;
   - sposta pagina;
   - unisci fatture;
   - separa fattura;
   - ripristina pagina scartata.

Fuori da questo Punto 0:

- packaging desktop, launcher `.exe`, gestione Tesseract nel pacchetto e installer non sono sotto-punti interni del workflow documentale;
- restano nel piano generale di desktop packaging, ma non vanno mischiati con il completamento del ciclo input/review/output fatture.

##### 4. Scope implementativo del Punto 0 parte V

Implementare ora:

- nuova vista `Archivio fatture`;
- pulsante in sidebar;
- lettura `GET /invoices/registry`;
- mostrare solo record `saved`;
- tabella con campi principali;
- ricerca testuale base;
- apertura PDF finale tramite `archived_pdf_url`;
- stato vuoto chiaro.

Non implementare ora:

- export Excel;
- modifica record archivio;
- filtri avanzati;
- ordinamento interattivo;
- riapertura fattura salvata;
- visualizzazione scartate.

##### 5. Implementazione eseguita

File modificati:

- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/App.css`.

Cambiamenti:

- `AppView` ora include `archive`;
- aggiunta funzione `openInvoiceArchive`;
- aggiunta funzione `loadInvoiceArchive`;
- aggiunto stato `invoiceArchiveRecords`;
- aggiunto stato `invoiceArchiveSearch`;
- aggiunto componente `InvoiceArchiveTable`;
- aggiunto pulsante `Archivio fatture` nella sidebar review;
- aggiunta schermata archivio con:
  - titolo;
  - contatore fatture salvate;
  - ricerca;
  - refresh;
  - tabella;
  - link `Apri PDF`;
- i link PDF usano `runtimeApiUrl(...)`, quindi funzionano sia in app servita da `run_app.py` sia in sviluppo Vite.

Colonne iniziali archivio:

- salvata il;
- emittente;
- condominio;
- numero;
- data;
- totale;
- origine;
- PDF.

##### 6. Verifiche eseguite

Build frontend:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- ok.

Test backend/regressione mirata:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_invoice_pdf_archive.py tests\test_invoice_registry_storage.py tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 15 passed;
- warning noto su `.pytest_cache` non scrivibile.

##### 7. Revisione critica e chiusura Punto 0 parte V

Punto 0 parte V soddisfacente per beta iniziale:

- l'utente ha un primo archivio consultabile;
- il salvataggio non e' piu' invisibile;
- il PDF finale e' apribile dalla UI;
- il registro resta la fonte dati;
- il frontend non ricalcola informazioni archivio, ma legge il registro;
- i record scartati non vengono confusi con fatture archiviate.

Limiti residui consapevoli:

- ricerca solo testuale, non filtri strutturati;
- nessun export Excel ancora;
- nessuna modifica da archivio;
- nessuna gestione avanzata di stati `discarded/restored`;
- nessuna correzione raggruppamento pagine.

Prossimo punto consigliato:

- Punto 0 parte VI: export Excel del registro fatture, perche' ora esiste una fonte dati finale e consultabile.

#### Punto 0 parte VI - Export Excel del registro fatture

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **produrre un output gestionale esterno dal registro applicativo interno senza trasformare l'Excel nel database dell'app**.

Ora l'app ha:

- fatture salvate;
- PDF finali archiviati;
- registro JSON persistente;
- pagina archivio consultabile.

Lo studio pero' avra' bisogno di lavorare anche fuori dall'app, soprattutto in Excel. L'export deve quindi essere una proiezione controllata del registro, non una copia grezza dei dati interni.

##### 2. Teoria risolutiva e best practice

Best practice:

- fonte dati unica: `invoices_registry.json`;
- esportare solo record `saved`;
- non esportare record `discarded` nell'archivio ordinario;
- colonne esplicite e stabili;
- generare il file al momento del download;
- non mantenere Excel come storage primario;
- non includere metadata tecnici inutili all'utente finale;
- mantenere riferimenti utili come file origine e PDF archiviato.

Giudizio Codex:

- per la beta l'export deve essere semplice e leggibile;
- meglio evitare filtri export in questa fase: esportiamo tutto l'archivio salvato;
- i filtri/export del filtro corrente saranno naturali dopo filtri strutturati in archivio;
- il backend deve generare Excel, non il frontend, per tenere coerente formato e colonne.

##### 3. Scope implementativo del Punto 0 parte VI

Implementare ora:

- funzione backend di export Excel;
- endpoint `GET /invoices/export.xlsx`;
- pulsante download nella pagina Archivio fatture;
- test che verifica colonne e record esportati;
- esclusione record `discarded`.

Non implementare ora:

- export del filtro corrente;
- formattazione avanzata Excel;
- formule;
- riimport del registro fatture da Excel;
- gestione colonne configurabili dall'utente.

##### 4. Implementazione eseguita

File modificati:

- `src/estrattore/runtime_paths.py`;
- `src/estrattore/invoice_registry_storage.py`;
- `scripts/registry_api.py`;
- `scripts/local_app.py`;
- `apps/viewer/src/App.tsx`;
- `tests/test_invoice_registry_storage.py`.

Runtime path aggiunto:

```text
invoice_registry\registro_fatture.xlsx
```

Funzione aggiunta:

- `save_invoice_registry_excel(...)`.

Endpoint aggiunto:

- `GET /invoices/export.xlsx`.

Frontend:

- pulsante download nella pagina `Archivio fatture`;
- file scaricato come `registro_fatture.xlsx`;
- bottone disabilitato se non ci sono fatture salvate.

Colonne esportate:

- Salvata il;
- Nome emittente;
- C.F. emittente;
- P.IVA emittente;
- Condominio;
- C.F. condominio;
- Numero fattura;
- Data fattura;
- Totale fattura;
- Ritenuta;
- Totale netto fattura;
- Data pagamento;
- File origine;
- PDF archiviato.

##### 5. Verifiche eseguite

Test:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_invoice_registry_storage.py tests\test_invoice_pdf_archive.py tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 16 passed;
- warning noto su `.pytest_cache` non scrivibile.

Compile Python:

```powershell
.\.venv\Scripts\python.exe -m py_compile src\estrattore\invoice_registry_storage.py scripts\registry_api.py scripts\local_app.py src\estrattore\runtime_paths.py
```

Esito:

- ok.

Build frontend:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- ok.

##### 6. Revisione critica e chiusura Punto 0 parte VI

Punto 0 parte VI soddisfacente per beta:

- l'Excel e' derivato dal registro interno;
- non duplica logica nel frontend;
- non esporta scarti come fatture valide;
- le colonne sono comprensibili per uso gestionale;
- il file e' generato su richiesta, quindi riflette l'ultimo stato salvato.

Limiti residui:

- export sempre completo, non filtrato;
- nessun ordinamento Excel dedicato oltre all'ordine record;
- nessuna formattazione economica/date avanzata;
- nessun riimport del registro fatture da Excel.

Prossimo punto consigliato:

- Punto 0 parte VII: validazione campi prima del salvataggio, per evitare che l'archivio finale contenga fatture salvate con informazioni critiche mancanti senza avviso.

#### Punto 0 parte VII - Caricamento cartelle e archivio avanzato

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

L'utente ha scelto di anticipare due blocchi del backlog:

- caricamento cartelle;
- archivio avanzato.

Il problema generale e': **rendere piu' fluido l'ingresso massivo dei documenti e piu' utile la consultazione dell'archivio gia' prodotto**.

Sono due aspetti collegati per la beta:

- se lo studio lavora con molti PDF, selezionare file uno per uno e' troppo lento;
- se l'archivio cresce, la sola ricerca testuale non basta per controllare velocemente le fatture salvate.

##### 2. Teoria risolutiva e best practice

Caricamento cartelle:

- in una web app pura il browser non permette accesso arbitrario al filesystem;
- poiche' questa app e' locale e il backend Python gira sullo stesso PC, il modo piu' coerente con la beta desktop e' aprire il file picker Windows dal backend;
- React deve solo chiedere al backend di aprire la finestra, non dipendere da API directory del browser;
- il backend seleziona la cartella, legge ricorsivamente i PDF e li appiattisce nello storage interno;
- i PDF originali esterni non devono essere modificati.

Archivio avanzato:

- la fonte resta `invoices_registry.json`;
- il frontend puo' filtrare localmente nella prima beta;
- i filtri strutturati devono affiancare, non sostituire, la ricerca generale;
- l'ordinamento deve essere esplicito, non implicito;
- export filtrato resta un punto separato.

Giudizio Codex:

- non usiamo API directory del browser: sono state scartate per evitare dipendenze comportamentali dal browser specifico;
- il caricamento cartella passa da endpoint locale Python + dialog Windows;
- questa scelta e' piu' vicina al packaging desktop finale;
- per archivio avanzato conviene fare filtri lato client finche' il registro resta piccolo/medio;
- se l'archivio cresce molto, si potra' spostare query/filtri lato backend o SQLite.

##### 3. Scope implementativo

Implementare ora:

- pulsante `Carica cartella`;
- endpoint backend `POST /batches/pick-folder`;
- dialog Windows tramite backend Python;
- import ricorsivo dei PDF presenti nella cartella selezionata;
- appiattimento in batch interno;
- reset input file dopo import browser;
- filtri archivio per emittente, condominio e intervallo data fattura;
- ordinamento archivio per salvataggio, data fattura, emittente, condominio e totale;
- reset filtri.

Non implementare ora:

- export Excel del filtro corrente;
- filtri server-side;
- salvataggio preset filtri;
- visualizzazione scartate.

##### 4. Implementazione eseguita

File modificati:

- `src/estrattore/batch_storage.py`;
- `scripts/registry_api.py`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/App.css`.
- `tests/test_batch_storage.py`.

Caricamento cartelle:

- aggiunto `import_pdf_folder(...)`;
- il backend cerca ricorsivamente solo file `.pdf`;
- i file non PDF vengono ignorati;
- l'ordine e' stabile sul path relativo;
- il nome originale mantenuto nel batch e' il path relativo dentro la cartella scelta;
- aggiunto endpoint `POST /batches/pick-folder`;
- `openPdfFolderPicker` chiama il backend locale;
- il frontend non contiene piu' una seconda input file per le cartelle.

Archivio avanzato:

- aggiunto tipo `InvoiceArchiveFilters`;
- aggiunto tipo `InvoiceArchiveSortKey`;
- aggiunto stato `invoiceArchiveFilters`;
- aggiunto stato `invoiceArchiveSortKey`;
- `filterInvoiceArchiveRecords(...)` ora applica:
  - ricerca generale;
  - filtro emittente;
  - filtro condominio;
  - data fattura da/a;
  - ordinamento selezionato;
- aggiunta barra filtri nella pagina `Archivio fatture`;
- aggiunto pulsante `Pulisci filtri`.

Ordinamenti disponibili:

- salvataggio recente;
- salvataggio meno recente;
- data fattura decrescente;
- data fattura crescente;
- emittente A-Z;
- condominio A-Z;
- totale decrescente;
- totale crescente.

##### 5. Verifiche eseguite

Build frontend:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- ok.

Test backend/regressione storage:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_invoice_registry_storage.py tests\test_invoice_pdf_archive.py tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 17 passed;
- warning noto su `.pytest_cache` non scrivibile.

##### 6. Revisione critica e chiusura Punto 0 parte VII

Punto soddisfacente per beta:

- l'utente puo' selezionare una cartella tramite finestra Windows;
- i file continuano a entrare nel batch come singoli PDF appiattiti;
- il modello di sicurezza resta invariato: si copiano file nello storage app, non si lavora sugli originali;
- l'archivio diventa consultabile anche quando cresce;
- l'ordinamento e' esplicito e prevedibile.

Limiti residui:

- il dialog Windows usa `tkinter`, quindi va verificato bene nel pacchetto `.exe`;
- se il backend gira senza sessione grafica, il picker non e' disponibile;
- i filtri sono lato client;
- l'export Excel continua a esportare tutto, non il filtro corrente;
- non ci sono ancora filtri per stato/scartate.

Sequenza interna aggiornata del Punto 0:

1. Punto 0 parte VIII: validazione semplice prima del salvataggio.
2. Punto 0 parte IX: salvataggio/eliminazione intera batch.
3. Punto 0 parte X: archivio dei file sorgente interni e link sorgente in archivio fatture.
4. Punto 0 parte XI: strumenti di correzione raggruppamento pagine, da lasciare alla fine per complessita'.

Non aprire un nuovo punto su archivio avanzato generico: per ora e' sufficiente.

Prossimo punto consigliato:

- Punto 0 parte VIII: validazione campi prima del salvataggio, perche' ora input, archivio e output sono piu' fluidi e il rischio principale diventa salvare dati incompleti.

#### Punto 0 parte VIII - Validazione semplice prima del salvataggio

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **impedire salvataggi inconsapevoli di dati incompleti o incoerenti senza bloccare il lavoro operativo dell'utente**.

In questa fase l'app non deve comportarsi come un sistema contabile definitivo che rifiuta rigidamente ogni fattura imperfetta. Il valore beta e' permettere all'utente di completare manualmente le fatture estratte, archiviarle e poi migliorare progressivamente automazioni e anagrafica.

La validazione deve quindi essere un controllo di qualita' prima della persistenza finale:

- segnala campi vuoti;
- segnala C.F. e P.IVA formalmente sospetti;
- segnala quando una fattura contiene un codice fiscale valido che non e' ancora in anagrafica;
- segnala quando un codice fiscale corrisponde a piu' righe anagrafiche con riempimenti diversi;
- lascia comunque all'utente la possibilita' di continuare.

Questo e' diverso da una validazione bloccante. La fattura puo' essere corretta anche se manca un campo, per esempio quando il dato non e' presente nel documento e verra' gestito manualmente dopo.

##### 2. Teoria risolutiva

Best practice scelta:

- validazione vicino all'azione critica, quindi sul tasto `Salva fattura`;
- dialog dell'app, non `confirm()` del browser;
- messaggi brevi e leggibili dall'utente;
- nessuna duplicazione del salvataggio backend;
- riuso del flusso anagrafica gia' esistente per creare nuove righe.

Decisione importante:

- se non ci sono anomalie, il salvataggio resta diretto;
- se ci sono anomalie, l'app apre un dialog di controllo;
- `Continua e salva` archivia comunque la fattura;
- `Aggiungi in anagrafica` apre la pagina anagrafica con la nuova riga in alto, usando lo stesso meccanismo del pulsante `+` sui campi C.F.

Questa scelta e' volutamente prudente: non scrive automaticamente in anagrafica durante il salvataggio fattura, perche' l'anagrafica e' un dato persistente ad alto impatto e deve essere confermata nel suo contesto naturale.

##### 3. Microflusso implementato

1. L'utente clicca `Salva fattura`.
2. La UI controlla la fattura corrente.
3. Se tutto e' pulito, chiama `/invoices/save`.
4. Se trova problemi, apre `Controllo prima del salvataggio`.
5. Il dialog mostra:
   - campi mancanti;
   - valori fiscali da controllare;
   - candidato anagrafico non ancora presente;
   - eventuali righe anagrafiche multiple compatibili.
6. L'utente puo':
   - annullare;
   - continuare e salvare;
   - aprire la nuova riga in anagrafica quando esiste un candidato.

##### 4. Implementazione

File modificati:

- `apps/viewer/src/App.tsx`
- `apps/viewer/src/App.css`

Elementi introdotti:

- `InvoiceSaveValidationIssue`;
- `PendingInvoiceSaveValidation`;
- `buildInvoiceSaveValidation`;
- validazione UI per P.IVA con checksum;
- validazione UI per C.F. come 11 cifre fiscali oppure formato alfanumerico a 16 caratteri;
- `InvoiceSaveValidationDialog`;
- collegamento di `saveCurrentInvoice()` alla validazione prima di chiamare il backend.

Nota critica:

- il controllo C.F. alfanumerico lato UI e' volutamente leggero. Il validatore Python ha logica piu' ricca e correzioni OCR, ma qui serve un avviso operativo rapido. Se in futuro vogliamo una validazione fiscale canonica unica, va esposta dal backend o duplicata in una libreria condivisa.

##### 5. Occhi costruiti

Verifica eseguita:

- `npm --prefix apps/viewer run build`

Risultato:

- build completata correttamente;
- primo tentativo bloccato da `spawn EPERM` dentro sandbox Windows;
- secondo tentativo eseguito fuori sandbox e completato.

##### 6. Revisione critica e chiusura Punto 0 parte VIII

Perche' la soluzione e' soddisfacente per la beta:

- non cambia il formato dei dati salvati;
- non richiede migrazioni;
- non introduce nuove dipendenze backend;
- usa lo stile dialog gia' presente;
- mantiene l'utente al centro della decisione nei casi dubbi;
- prepara il terreno per validazioni piu' forti senza rendere rigido il flusso attuale.

Limiti residui da non dimenticare:

- il candidato anagrafico non viene salvato automaticamente durante `Salva fattura`;
- la validazione C.F. alfanumerica lato UI e' formale, non completa di checksum;
- non esiste ancora una validazione backend obbligatoria sul registro finale;
- il salvataggio batch intera dovra' riusare la stessa validazione per ogni fattura.

Prossimo punto consigliato:

- Punto 0 parte IX: salvataggio/eliminazione intera batch, riusando la validazione appena introdotta come controllo per ogni fattura coinvolta.

#### Punto 0 parte IX - Salvataggio ed eliminazione intera batch

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **permettere operazioni massive senza introdurre una seconda semantica di persistenza**.

Una batch non deve diventare un oggetto contabile diverso dalla fattura singola. E' solo una raccolta di fatture attualmente in review. Quindi `Salva batch` ed `Elimina batch` devono comportarsi come una ripetizione controllata di:

- `Salva fattura`;
- `Elimina fattura`.

Questo evita una classe di bug tipica: due percorsi di salvataggio che col tempo divergono, per esempio uno aggiorna il registro, l'altro no; uno archivia il PDF, l'altro no; uno marca la review, l'altro lascia residui.

##### 2. Teoria risolutiva

Best practice scelta:

- riusare gli endpoint esistenti `/invoices/save` e `/invoices/discard`;
- eseguire le operazioni in sequenza, non in parallelo;
- mostrare una conferma esplicita prima dell'operazione batch;
- per `Salva batch`, riusare la validazione UI del Punto VIII su tutte le fatture;
- per `Elimina batch`, mostrare solo conferma, perche' non serve validare dati che vengono scartati.

Decisione importante:

- niente endpoint batch backend per ora.

Motivo:

- gli endpoint singoli sono gia' la fonte di verita';
- ogni salvataggio aggiorna lo stesso registro JSON;
- richieste parallele potrebbero sovrascriversi;
- la sequenza frontend e' sufficiente per una beta locale e mantiene il comportamento leggibile.

Possibile evoluzione futura:

- introdurre un endpoint backend transazionale `/invoices/batch-save` solo se emergono problemi reali di performance o atomicita'.

##### 3. Microflusso implementato

Salvataggio batch:

1. L'utente clicca `Salva batch`.
2. La UI valida tutte le fatture presenti nella batch corrente.
3. Il dialog mostra quante anomalie sono state trovate:
   - campi mancanti;
   - C.F. o P.IVA sospetti;
   - nuovi valori non presenti in anagrafica;
   - conflitti anagrafica.
4. L'utente puo' annullare o continuare.
5. Se continua, la UI salva le fatture una per volta usando `/invoices/save`.
6. Le fatture salvate spariscono dalla review.
7. L'elenco batch viene ricaricato.

Eliminazione batch:

1. L'utente clicca `Elimina batch`.
2. La UI chiede conferma.
3. Se confermato, elimina le fatture una per volta usando `/invoices/discard`.
4. Le fatture eliminate spariscono dalla review.
5. L'elenco batch viene ricaricato.

##### 4. Implementazione

File modificati:

- `apps/viewer/src/App.tsx`
- `apps/viewer/src/App.css`

Elementi introdotti:

- `PendingBatchReviewAction`;
- `BatchReviewActionConfirmDialog`;
- `canPersistCurrentBatch`;
- `requestBatchReviewAction`;
- `confirmBatchReviewAction`;
- `persistInvoiceReviewAction`, funzione comune per salvare/eliminare una singola fattura;
- pulsanti `Salva batch` ed `Elimina batch` nella sidebar della review.

Nota critica:

- `persistInvoiceReviewAction` e' ora il nucleo comune. Il salvataggio singolo e quello batch passano dallo stesso codice di richiesta HTTP.

##### 5. Occhi costruiti

Verifica eseguita:

- `npm --prefix apps/viewer run build`

Risultato:

- build completata correttamente.

##### 6. Revisione critica e chiusura Punto 0 parte IX

Perche' la soluzione e' soddisfacente per la beta:

- non cambia il backend;
- non duplica logica di persistenza;
- evita race condition sul registro JSON usando operazioni sequenziali;
- espone un comando utile per l'utente reale;
- integra la validazione precedente nel flusso massivo.

Limiti residui:

- l'operazione batch non e' atomica: se una fattura fallisce a meta', quelle precedenti restano salvate o eliminate;
- non esiste ancora un report dettagliato fattura-per-fattura in caso di errore parziale;
- il salvataggio batch non apre una riga anagrafica nuova: segnala il conteggio, ma l'inserimento anagrafica resta nel flusso singolo;
- in futuro un endpoint backend transazionale potrebbe diventare preferibile.

Prossimo punto consigliato:

- Punto 0 parte X: archivio dei file sorgente interni e link sorgente in archivio fatture, includendo la regola di collisione `nome.pdf`, `nome(1).pdf` per mantenere uno storico completo.

#### Punto 0 parte X - Archivio sorgenti e link sorgente nel registro fatture

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **separare memoria di lavorazione e memoria storica senza perdere tracciabilita'**.

La cartella `batches` contiene i PDF importati e serve al processamento. Pero' non deve essere l'unico riferimento storico, perche' in futuro potremmo voler pulire batch temporanee, riprocessare, spostare dati o cambiare struttura interna.

Quando una fattura viene salvata nel registro finale, l'utente deve poter risalire a:

- PDF fattura splittato, cioe' il documento operativo della singola fattura;
- PDF sorgente, cioe' il file originale importato da cui quella fattura e' stata estratta;
- nome file origine mostrato in modo leggibile.

La regola di sicurezza resta invariata: l'app non cancella mai file fuori dalla propria area dati. Inoltre, per ora, non cancella nemmeno i PDF sorgente interni in `batches`; mantiene anche una copia in archivio.

##### 2. Teoria risolutiva

Best practice scelta:

- creare un archivio sorgenti esplicito sotto `archive/sources`;
- copiare il PDF sorgente al momento del salvataggio della fattura;
- salvare nel registro sia path sia URL runtime del sorgente;
- mostrare in archivio fatture un link separato al sorgente;
- gestire collisioni file con suffisso progressivo `nome(1).pdf`, `nome(2).pdf`.

Decisione importante:

- il sorgente viene copiato per ogni salvataggio che ne ha bisogno, anche se il file originale e' uguale.

Motivo:

- per la beta conta piu' lo storico completo della deduplicazione;
- se due estrazioni producono nomi uguali, devono comunque restare entrambe;
- la deduplicazione puo' essere introdotta dopo con hash e reference count, ma sarebbe complessita' prematura.

##### 3. Microflusso implementato

1. L'utente salva una fattura.
2. Il backend carica il riferimento review della fattura.
3. Trova il PDF sorgente interno in `batches/<batch>/input/<document_id>.pdf`.
4. Splitta le pagine della fattura in `archive/invoices`.
5. Copia il PDF sorgente in `archive/sources`.
6. Se un nome e' gia' presente, usa `nome(1).pdf`, poi `nome(2).pdf`, ecc.
7. Il registro fatture salva:
   - `archived_pdf_path`;
   - `archived_pdf_url`;
   - `archived_source_pdf_path`;
   - `archived_source_pdf_url`;
   - `source_file_name`.
8. La pagina archivio mostra due link:
   - fattura;
   - sorgente.

##### 4. Implementazione

File modificati:

- `src/estrattore/runtime_paths.py`
- `src/estrattore/invoice_pdf_archive.py`
- `src/estrattore/invoice_registry_storage.py`
- `apps/viewer/src/App.tsx`
- `apps/viewer/src/App.css`
- `tests/test_runtime_paths.py`
- `tests/test_invoice_pdf_archive.py`
- `tests/test_invoice_registry_storage.py`

Elementi introdotti:

- `archive_sources_dir` nei runtime paths;
- campi `archived_source_path` e `archived_source_relative_path` nel risultato di archiviazione PDF;
- helper `_archive_source_pdf`;
- helper `_unique_path`;
- colonne registro/export per `PDF sorgente`;
- link `Apri origine` nella tabella archivio fatture.

##### 5. Occhi costruiti

Verifiche eseguite:

- `.\.venv\Scripts\python.exe -m pytest tests/test_runtime_paths.py tests/test_invoice_pdf_archive.py tests/test_invoice_registry_storage.py`
- `.\.venv\Scripts\python.exe -m pytest tests/test_batch_storage.py tests/test_processing_storage.py tests/test_runtime_paths.py tests/test_invoice_pdf_archive.py tests/test_invoice_registry_storage.py`
- `npm --prefix apps/viewer run build`

Risultato:

- 18 test Python passati;
- build frontend completata correttamente.

Nota:

- pytest mostra un warning sulla cache `.pytest_cache` non scrivibile in sandbox. Non riguarda il comportamento dell'app.

##### 6. Revisione critica e chiusura Punto 0 parte X

Perche' la soluzione e' soddisfacente per la beta:

- rende l'archivio fatture realmente tracciabile;
- non dipende dalla permanenza futura delle batch di lavorazione;
- non cancella dati utente;
- espone il sorgente nella UI;
- mantiene un comportamento prevedibile su collisioni di nomi.

Limiti residui:

- i file sorgente in `batches/input` e i file pesanti in `manifests/documents` non restano piu' come retention di lavorazione quando tutte le fatture del documento sono chiuse;
- non c'e' deduplicazione dei sorgenti identici in archivio finale;
- il nome del PDF fattura contiene ancora l'hash del record, quindi la collisione e' rara ma comunque gestita;
- se in futuro vogliamo nomi piu' leggibili, va deciso uno schema stabile che non rompa i link gia' salvati;
- non esiste ancora una pagina separata "Archivio file sorgente", ma il link sorgente e' presente per ogni fattura salvata.

Prossimo punto consigliato:

- Punto 0 parte XI: strumenti di correzione raggruppamento pagine, da lasciare alla fine perche' e' il punto piu' delicato e puo' cambiare i manifest/processi di review.

##### 7. Correzione ciclo vita working directory

Decisione aggiornata dopo confronto:

- non serve retention automatica dei duplicati di lavorazione;
- dopo che tutte le fatture derivate da un documento sono `saved` o `discarded`, i dati intermedi di quel documento devono sparire dalla working directory;
- l'archivio finale resta permanente;
- i file esterni all'app non vengono mai toccati.

Regola implementata:

```text
Documento con almeno una fattura pending
  -> conserva batches/<batch>/input/<document_id>.pdf
  -> conserva manifests/<process>/documents/<document_id>

Documento con tutte le fatture saved/discarded
  -> elimina batches/<batch>/input/<document_id>.pdf
  -> elimina manifests/<process>/documents/<document_id>
  -> conserva archive/invoices
  -> conserva archive/sources
  -> conserva invoice_registry
```

Dettaglio importante:

- la cancellazione e' interna all'app e avviene solo su path verificati dentro `batches_dir` e `manifests_dir`;
- se lo stesso documento risulta ancora pending in un altro indice di review, il PDF sorgente interno non viene cancellato;
- la fattura salvata archivia prima PDF finale e sorgente, poi marca la review e solo dopo pulisce la working directory.

File modificati in questa correzione:

- `src/estrattore/processing_storage.py`
- `tests/test_processing_storage.py`
- `tests/test_invoice_registry_storage.py`

Occhi costruiti:

- test sul documento diviso in due fatture: dopo la prima chiusura i file restano, dopo la seconda spariscono;
- test sul salvataggio dell'unica fattura di un documento: manifest documentale e PDF input interno spariscono.

Verifica eseguita:

- `.\.venv\Scripts\python.exe -m pytest tests/test_processing_storage.py tests/test_invoice_registry_storage.py tests/test_invoice_pdf_archive.py tests/test_runtime_paths.py`
- `npm --prefix apps/viewer run build`

Risultato:

- 14 test Python passati;
- build frontend completata correttamente.

##### 8. Separazione UI tra archivio PDF e registro dati

Decisione aggiornata:

- `Archivio fatture` e `Registro fatture` sono due pagine diverse;
- usano la stessa sorgente persistente `invoice_registry/invoices_registry.json`;
- non devono diventare due JSON separati, per evitare desincronizzazione;
- cambiano invece finalita' e rappresentazione.

Modello:

```text
Archivio fatture
  -> pagina documentale
  -> mostra PDF fattura splittato
  -> mostra PDF sorgente
  -> serve a recuperare/aprire documenti

Registro fatture
  -> pagina dati post-compilazione
  -> mostra i valori estratti e corretti
  -> e' la base dell'export Excel
  -> deve assomigliare ad Anagrafica clienti come esperienza tabellare
```

Implementazione:

- aggiunta view `invoiceRegistry`;
- `Archivio fatture` ora mostra una tabella documentale centrata su `Apri PDF` e `Apri origine`;
- `Registro fatture` mostra una tabella dati con colonne contabili/anagrafiche;
- il download Excel e' stato lasciato nel `Registro fatture`, non nell'archivio PDF;
- la sidebar review espone due ingressi separati:
  - `Archivio fatture`;
  - `Registro fatture`.

File modificati:

- `apps/viewer/src/App.tsx`
- `apps/viewer/src/App.css`

Occhi costruiti:

- `npm --prefix apps/viewer run build`

Risultato:

- build frontend completata correttamente.

##### 9. Rifinitura UI e nomenclatura italiana

Problema osservato:

- nella sidebar `Registro fatture` si sovrapponeva leggermente ad `Archivio fatture`;
- alcune etichette visibili erano ancora in inglese o troppo tecniche per l'utente finale;
- i controlli compatti rischiavano di collidere quando il testo cresceva o quando la finestra era piu' stretta.

Principio usato:

- la UI beta deve usare parole italiane orientate al lavoro dell'utente, non al modello interno del codice;
- la sidebar deve avere spazio sufficiente per nomi funzionali lunghi;
- i pulsanti laterali devono potersi disporre in verticale e andare a capo senza sovrapporsi;
- i nomi tecnici interni possono restare in inglese se non sono visibili, per evitare refactor cosmetici rischiosi.

Implementazione:

- allargata la colonna laterale principale;
- trasformate le azioni di gruppo in colonna singola;
- permesso il wrapping dei testi nei pulsanti laterali e nei controlli;
- aggiunto un breakpoint intermedio per ridurre la larghezza del pannello campi prima che crei compressioni;
- nascosto dalla sidebar l'identificativo tecnico del gruppo caricato, sostituito con il numero di PDF caricati;
- tradotte le etichette visibili principali:
  - `batch` -> `gruppo`;
  - `review` -> `revisione`;
  - `Reset` -> `Ripristina`;
  - `Tokens` -> `Parole`;
  - `Crops` -> `Ritagli`;
  - `Tables` -> `Tabelle`;
  - `Labels` -> `Etichette`;
  - `All Targets` -> `Tutti i campi`;
  - `Saving` -> `Salvataggio`;
  - `Cleaning` -> `Pulizia`.

Occhi costruiti:

- ricerca mirata nel frontend per etichette inglesi residue visibili;
- `npm --prefix apps/viewer run build`.

Risultato:

- build frontend completata correttamente;
- nessuna occorrenza residua trovata per le etichette inglesi controllate nella superficie principale.

##### 10. Rifinitura revisione fatture

Problemi osservati:

- `Prima pagina alla precedente` e `Ultima pagina alla successiva` erano esposti nel punto piu' visibile dell'header;
- sono azioni potenti, quindi non devono essere facili da cliccare per errore durante la normale compilazione;
- il pannello `Campi fattura` occupava troppo spazio rispetto al documento;
- gruppi da 20 fatture risultavano meno equilibrati nella schermata laterale.

Decisioni:

- raggruppare le correzioni pagine sotto `Riordina pagine`;
- mantenere le due azioni minime gia' scelte, senza introdurre drag&drop;
- ridurre il pannello `Campi fattura` del 10% circa, includendo contenitore, font principali, padding e larghezze minime;
- spostare lo spazio recuperato sulla visualizzazione della fattura;
- passare da 20 a 15 fatture per gruppo di revisione.
- rendere la sidebar scorrevole, con la lista fatture come area interna scrollabile, per evitare che i comandi di gruppo vengano tagliati.
- mostrare comunque i comandi `Salva gruppo` ed `Elimina gruppo` quando esiste un gruppo selezionato; se il gruppo non e' operativo per salvataggio/eliminazione, i pulsanti restano disabilitati e la UI spiega il motivo.
- posizionare `Salva gruppo` ed `Elimina gruppo` in fondo alla sidebar, perche' sono azioni di massa e non devono interrompere la selezione delle singole fatture.
- posizionare le azioni della singola fattura dentro `Campi fattura`, nella stessa riga di `Ripristina`, con ordine `Salva`, `Ripristina`, `Elimina`.
- rendere esplicito il dialog di eliminazione gruppo con domanda `Sei sicuro...`, oltre ai pulsanti `Annulla` e `Elimina gruppo`.
- durante il processamento PDF mostrare avanzamento reale `x/y file elaborati`, derivato dal job backend.
- aggiungere annullamento del processamento con dialog dell'app; l'annullamento e' cooperativo e avviene al primo punto sicuro tra un PDF e il successivo, senza uccidere brutalmente l'OCR in corso.
- se il processamento viene annullato, l'intera cartella `process_...` prodotta da quel tentativo viene rimossa: non restano manifest parziali, gruppi incompleti o fatture gia' visibili da quel job.
- mostrare tempo stimato residuo calcolato dal backend sul tempo medio per PDF gia' elaborato; sotto 3 file elaborati la UI mostra `tempo stimato in calcolo`, per evitare stime iniziali troppo instabili.
- rendere validi i processi completati solo se esiste il marker finale `processing_complete.ok`; all'avvio/API i processi senza marker o con stato non terminale vengono rimossi automaticamente come residui da crash/chiusura app.

Motivo:

- il riordino pagine e' una funzione correttiva, non un comando quotidiano;
- 15 fatture entrano meglio nella schermata e riducono compressioni visive;
- ridurre il pannello campi con misure reali e non con scala globale mantiene input e menu piu' prevedibili.

Occhi costruiti:

- `npm --prefix apps/viewer run build`;
- `.\.venv\Scripts\python.exe -m pytest tests\test_processing_storage.py`.

Risultato:

- build frontend completata correttamente;
- 8 test storage processamento passati;
- resta un warning locale sulla cache pytest non scrivibile, non collegato alla modifica.

Nota sui vecchi elaborati:

- i manifest/PDF generati prima del flusso runtime possono trovarsi ancora in `apps/viewer/public/sample`;
- il nuovo flusso beta legge invece i gruppi operativi da `AppData\Local\EstrattoreScanFatture\manifests`;
- non conviene spostare manualmente cartelle `sample` dentro `manifests`, perche' il runtime non usa solo i file PDF/manifest ma anche indici di processamento e riferimenti di revisione;
- per trasformare vecchi test in dati operativi beta, la strada pulita e' ricaricare i PDF dall'app e riprocessarli;
- se serve davvero recuperare un'elaborazione storica, va costruita una migrazione esplicita, non uno spostamento manuale di cartelle.

#### Punto 0 parte XI - Correzione minima raggruppamento pagine

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **permettere all'utente di correggere gli errori di split senza trasformare la review in un editor complesso di PDF**.

Nella pratica, gli errori piu' frequenti non richiedono riordinamento libero delle pagine. Richiedono quasi sempre di correggere un confine:

```text
una pagina e' rimasta nella fattura precedente
oppure
una pagina e' scivolata nella fattura successiva
```

Quindi la beta non deve partire da drag&drop completo. Il drag&drop risolve piu' casi teorici, ma introduce complessita' alta:

- pagine duplicate;
- fatture vuote;
- ordine interno;
- scroll durante trascinamento;
- undo;
- persistenza piu' rischiosa;
- UX fragile su molte pagine.

La riduzione corretta del problema e':

- spostare la prima pagina della fattura corrente alla fattura precedente;
- spostare l'ultima pagina della fattura corrente alla fattura successiva.

##### 2. Teoria risolutiva

Best practice scelta:

- comandi locali vicino alla fattura corrente;
- niente ricalcolo OCR;
- niente modifica del PDF sorgente;
- persistenza sul `viewer_manifest.json`;
- validazioni strette lato frontend e backend;
- salvataggio finale che usa naturalmente i nuovi `page_numbers`.

Decisioni operative:

- si spostano pagine solo tra fatture adiacenti;
- si spostano pagine solo nello stesso documento sorgente;
- non si puo' lasciare una fattura senza pagine;
- dopo lo spostamento si resetta zoom/scroll e si deseleziona il campo;
- i campi estratti non vengono ricalcolati. Se dopo uno spostamento un valore non torna, l'utente lo corregge manualmente.

Questa e' una scelta beta molto pragmatica: corregge il punto di split senza aprire un editor visuale completo.

##### 3. Microflusso implementato

1. L'utente apre una fattura in review.
2. Se la fattura ha piu' di una pagina e ha una fattura precedente dello stesso documento, puo' cliccare:
   - `Prima pagina alla precedente`.
3. Se la fattura ha piu' di una pagina e ha una fattura successiva dello stesso documento, puo' cliccare:
   - `Ultima pagina alla successiva`.
4. La UI chiama `/viewer/move-page`.
5. Il backend valida:
   - batch runtime valido;
   - fatture pending;
   - stesso documento;
   - pagina presente nella fattura origine;
   - fattura origine non lasciata vuota.
6. Il backend aggiorna `viewer_manifest.json`.
7. La UI aggiorna la fattura corrente e quella adiacente in memoria.
8. Il PDF finale verra' splittato al salvataggio usando le pagine corrette.

##### 4. Implementazione

File modificati:

- `src/estrattore/processing_storage.py`
- `scripts/registry_api.py`
- `apps/viewer/src/App.tsx`
- `apps/viewer/src/App.css`
- `tests/test_processing_storage.py`

Elementi introdotti:

- `move_review_invoice_page`;
- endpoint `POST /viewer/move-page`;
- helper backend di validazione e aggiornamento manifest;
- pulsanti UI:
  - `Prima pagina alla precedente`;
  - `Ultima pagina alla successiva`;
- aggiornamento locale di `pageNumbers`, `pages` e `loadedManifests`.

##### 5. Occhi costruiti

Verifiche eseguite:

- `.\.venv\Scripts\python.exe -m pytest tests/test_processing_storage.py tests/test_invoice_registry_storage.py tests/test_invoice_pdf_archive.py`
- `.\.venv\Scripts\python.exe -m pytest tests/test_batch_storage.py tests/test_processing_storage.py tests/test_runtime_paths.py tests/test_invoice_pdf_archive.py tests/test_invoice_registry_storage.py`
- `npm --prefix apps/viewer run build`

Risultato:

- 21 test Python passati nel set runtime;
- build frontend completata correttamente.

Nota:

- pytest continua a mostrare un warning sulla cache `.pytest_cache` non scrivibile in sandbox. Non riguarda la feature.

##### 6. Revisione critica e chiusura Punto 0 parte XI

Perche' la soluzione e' soddisfacente per la beta:

- risolve il caso reale piu' frequente con una UI piccola;
- non richiede drag&drop;
- non introduce ricalcolo OCR rischioso;
- persiste la correzione nel manifest;
- il salvataggio finale eredita automaticamente lo split corretto;
- mantiene invarianti semplici: niente fatture vuote, niente pagine tra PDF diversi.

Limiti residui:

- non permette riordinamento libero;
- non permette duplicazione volontaria di una pagina tra due fatture;
- non corregge errori distribuiti su molte pagine non adiacenti;
- non visualizza ancora una timeline completa delle pagine.

Con questo punto, il Punto 0 e' chiuso abbastanza per tornare ai punti tecnici successivi.

La roadmap futura del packaging non vive piu' qui: sta in `docs/packaging_plan.md`.

#### Punto 2 - Consolidare entrypoint locale

Stato: completato nella prima forma beta.

##### 1. Definizione teorica del problema

Il problema generale e': **ridurre l'app a un solo punto di avvio stabile, diagnostico e portabile**.

Per una beta desktop non basta che il frontend e il backend funzionino separatamente. Serve un entrypoint ufficiale che:

- prepari l'ambiente;
- scelga o riceva la cartella dati;
- controlli la build frontend;
- avvii il server locale;
- apra il browser;
- lasci tracce diagnostiche leggibili.

##### 2. Teoria risolutiva e best practice

Best practice:

- un solo comando ufficiale per l'utente/beta tester;
- nessun path runtime fissato prima del parsing configurazione;
- override esplicito della cartella dati per test e debug;
- porta preferita con fallback se occupata;
- log persistente nella cartella dati app;
- errore chiaro se manca `npm` o la build fallisce.

Giudizio Codex:

- `scripts/run_app.py` resta l'entrypoint ufficiale in sviluppo/beta sorgente;
- non serve ancora una finestra embedded: browser locale va bene per beta e riduce rischio;
- il refactor importante e' importare backend/frontend server solo dopo aver applicato opzioni come `--data-dir`;
- il launcher `.exe` futuro dovra' chiamare lo stesso entrypoint o una funzione equivalente, non duplicare il flusso.

##### 3. Implementazione eseguita

File modificati:

- `scripts/run_app.py`;
- `scripts/local_app.py`;
- `README.md`.

Cambiamenti:

- rimosso import eager di `scripts.local_app` e `scripts.registry_api` da `run_app.py`;
- aggiunto `--data-dir` per scegliere una cartella dati persistente prima che i runtime paths vengano calcolati;
- aggiunto `--port` per scegliere la porta preferita;
- mantenuto fallback automatico su porte successive se la porta preferita e' occupata;
- aggiunto log di avvio in `logs/app.log` dentro la cartella dati runtime;
- mantenuto `--rebuild`;
- mantenuto `--no-browser`.

Comando ufficiale:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py
```

Comando utile per test beta isolati:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --data-dir .\runtime-beta-test --rebuild
```

##### 4. Verifiche eseguite

Compile Python:

```powershell
.\.venv\Scripts\python.exe -m py_compile scripts\run_app.py scripts\local_app.py scripts\registry_api.py src\estrattore\runtime_paths.py src\estrattore\processing_storage.py src\estrattore\invoice_registry_storage.py
```

Test backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_invoice_registry_storage.py tests\test_processing_storage.py tests\test_batch_storage.py tests\test_runtime_paths.py
```

Esito:

- 13 passed;
- warning noto su `.pytest_cache` non scrivibile.

Build frontend:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- ok;
- rilanciato fuori sandbox per `spawn EPERM` Vite.

Controllo help:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --help
```

Esito:

- mostra `--rebuild`, `--no-browser`, `--data-dir`, `--port`.

##### 5. Revisione critica e chiusura

Punto 2 soddisfacente per beta sorgente:

- l'avvio e' centralizzato;
- la cartella dati puo' essere scelta senza side effect anticipati;
- la porta e' controllabile;
- viene scritto un log persistente di avvio;
- il flusso resta compatibile con packaging futuro.

Limiti residui:

- non c'e' ancora gestione strutturata degli errori runtime su file oltre al log di avvio;
- non c'e' ancora finestra desktop embedded;
- non c'e' ancora shutdown coordinato per servizi multipli, perche' oggi il server e' unico;
- il pacchetto `.exe` non e' ancora creato.

Prossimo punto consigliato:

- consultare `docs/packaging_plan.md` quando si riprenderanno i punti futuri di packaging.

## Registro tentativi

### Tentativo 001 - Documento iniziale

Data: 2026-08-10

Fatto:

- creato documento di memoria sul desktop packaging;
- chiarito che il documento e' per Codex;
- definito approccio generale: backend Python locale + frontend React buildato + launcher Windows.

Esito:

- nessuna implementazione ancora fatta;
- serve prossimo passo sui path runtime.

### Punto 1 - Stabilizzare i path runtime

Stato: in corso.

#### 1. Definizione teorica del problema

Il problema generale non e' solo "scegliere una cartella".

Il problema e': **gestire lo stato persistente e le risorse mutabili di una app desktop aggiornabile**, separando cio' che appartiene al programma da cio' che appartiene all'utente.

Sotto-problemi inclusi:

- dati utente persistenti;
- input caricati dall'utente;
- output generati;
- manifest e cache;
- anagrafica e modifiche manuali;
- log;
- configurazione;
- migrazioni future;
- modalita' sviluppo vs modalita' installata;
- permessi Windows;
- aggiornamenti senza perdita dati.

#### 2. Teoria risolutiva e best practice

Best practice desktop moderna:

- il codice installato e' read-only o trattato come sostituibile;
- i dati scritti dall'app vivono nella cartella profilo utente;
- i path runtime sono calcolati da una funzione centrale, non sparsi nel codice;
- esiste un override esplicito per test/sviluppo;
- i log devono vivere in una posizione stabile e diagnosticabile;
- il codice non deve dipendere dal current working directory;
- eventuali migrazioni devono essere versionate e idempotenti.

Per Windows 10/11 la cartella utente corretta per dati applicativi locali e':

```text
%LOCALAPPDATA%\EstrattoreScanFatture
```

Giudizio tecnico Codex:

- preferisco introdurre subito una piccola utility Python centrale per i path runtime;
- non voglio spostare immediatamente tutti i dati reali, per evitare una migrazione grande e rischiosa;
- il primo step deve creare la fonte di verita' dei path e una diagnostica visibile;
- poi i componenti verranno collegati progressivamente.

#### 3. Approccio tecnico scelto

Creare un modulo piccolo nel package Python, indicativamente:

```text
src/estrattore/runtime_paths.py
```

Responsabilita':

- calcolare cartella dati app;
- distinguere sviluppo e pacchetto;
- supportare override via variabile ambiente;
- creare le sottocartelle standard;
- esporre una diagnostica stampabile/testabile.

Variabile ambiente proposta:

```text
ESTRATTORE_SCAN_DATA_DIR
```

Serve per test, sviluppo e beta controllate. Se non presente, usare `%LOCALAPPDATA%\EstrattoreScanFatture`.

#### 4. Micro-step

1. Mappare i path attuali nel codice.
2. Creare utility centrale per runtime paths.
3. Aggiungere test unitari sui path senza toccare dati reali.
4. Collegare almeno backend/anagrafica o local app alla nuova utility se il cambio e' contenuto.
5. Aggiungere diagnostica per vedere i path risolti.
6. Riesaminare se conviene migrare subito i dati o rimandare al punto successivo.

#### Nota durante mappatura

Path mutabili trovati:

- `data/registry/input/elenco_anagrafiche.xlsx`;
- `data/registry/cache/anagrafica_registry.json`;
- `data/registry/cache/anagrafica_registry_duplicates.*`;
- `data/registry/cache/anagrafica_estrattore_scan.xlsx`;
- `outputs/registry_uploads`;
- `apps/viewer/public/sample` per manifest viewer/debug.

Decisione provvisoria:

- collegare subito l'anagrafica ai runtime paths, perche' e' dato utente persistente;
- lasciare temporaneamente i sample viewer nella posizione attuale, perche' oggi sono ancora sia output debug sia sorgente visiva del frontend;
- introdurre comunque nel modello runtime una destinazione futura per batch/input/output/manifest, cosi' la struttura resta pronta.

Rischio:

- spostare subito `apps/viewer/public/sample` cambierebbe il contratto frontend `/sample/...`; meglio affrontarlo dopo aver definito come la beta seleziona batch e manifest elaborati.

#### 5. Implementazione eseguita

File introdotto:

- `src/estrattore/runtime_paths.py`.

Responsabilita' implementate:

- risoluzione cartella dati app;
- override tramite `ESTRATTORE_SCAN_DATA_DIR`;
- fallback Windows su `%LOCALAPPDATA%\EstrattoreScanFatture`;
- sottocartelle standard per registry, upload, batch, manifest, log, config;
- diagnostica serializzabile con `as_dict()`;
- migrazione conservativa dei file anagrafica legacy da `data/registry` verso runtime path solo se il file runtime manca.

File collegati:

- `src/estrattore/registry.py` usa i runtime paths per i default anagrafica;
- `scripts/registry_api.py` usa runtime paths per input/cache/export/report/upload anagrafica;
- `scripts/local_app.py` prepara i runtime paths all'avvio e stampa dati/log app;
- `scripts/run_app.py` usa path frontend assoluti rispetto alla root progetto.

Decisione correttiva durante implementazione:

- non creare directory runtime all'import di `registry_api.py`;
- prepararle solo quando il servizio parte o quando arriva una richiesta registry.

Motivo:

- creare cartelle all'import e' un side effect fragile;
- i test hanno evidenziato subito un caso di permessi negati su `AppData`;
- il modulo deve poter essere importato senza scrivere su disco.

#### 6. Verifiche eseguite

Test automatici:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_paths.py
```

Esito:

- 4 passed.

Regression mirata registry/viewer:

```powershell
$env:ESTRATTORE_SCAN_DATA_DIR=(Resolve-Path runtime-paths-test-manual).Path + '\app-data-regression'
.\.venv\Scripts\python.exe -m pytest tests\test_estrattore.py -k "registry or viewer_manifest"
```

Esito:

- 20 passed.

Controllo sintassi:

```powershell
.\.venv\Scripts\python.exe -m py_compile src\estrattore\runtime_paths.py src\estrattore\registry.py scripts\registry_api.py scripts\local_app.py scripts\run_app.py
```

Esito:

- ok.

Occhi diagnostici creati:

- endpoint locale `GET /runtime/paths`;
- stampa all'avvio di `Dati app` e `Log app`;
- `RuntimePaths.as_dict()` per dry run e debugging.

Nota sui test:

- alcune cartelle temporanee preesistenti nel repository/profilo hanno ACL non leggibili;
- questo non e' stato trattato come errore del codice runtime;
- per i test mirati e' stato usato un workspace controllato.

#### 7. Revisione critica

Valutazione:

- il codice ora ha una fonte centrale per i path runtime;
- l'anagrafica non dipende piu' strutturalmente da `data/registry`;
- la migrazione e' conservativa: copia solo se il target manca;
- non e' stato spostato il contratto `/sample`, evitando una modifica troppo larga nel punto 1;
- `registry_api.py` non scrive piu' su disco durante import.

Limiti residui:

- i manifest viewer e i batch elaborati sono ancora legati a `apps/viewer/public/sample`;
- non esiste ancora una migrazione versionata per manifest/anagrafica;
- non esiste ancora logging su file, solo stampa console;
- il comportamento in caso di `%LOCALAPPDATA%` non scrivibile andra' gestito meglio nel punto 2/launcher.

Domande del punto:

- dove salvare batch originali caricati dall'utente? Risposta provvisoria: `app_data_dir\batches`, da collegare quando implementiamo selezione/import batch reale.
- dove salvare manifest elaborati? Risposta provvisoria: `app_data_dir\manifests`, da collegare dopo aver separato generazione e visualizzazione.
- dove salvare anagrafica, cache anagrafica e modifiche manuali? Risolto: `app_data_dir\registry\input` e `app_data_dir\registry\cache`.
- dove salvare log e configurazione locale? Definito strutturalmente: `app_data_dir\logs` e `app_data_dir\config`; implementazione logging rimandata.
- serve un sistema di versioning/migrazione per anagrafica e manifest? Si', ma non nel punto 1; per ora c'e' solo migrazione legacy conservativa dei file anagrafica.
- come fare update senza perdere dati? Prima risposta architetturale: dati fuori dal programma in `AppData`; procedura completa da definire in launcher/installer.

#### 8. Chiusura punto 1

Il punto 1 e' considerato completato per lo scheletro beta: abbiamo una utility runtime centrale, anagrafica spostata sui path utente, diagnostica minima e test mirati.

Non considero completata la migrazione di tutto lo storage dell'app: sample viewer, batch e manifest restano un tema del punto successivo o di un sotto-punto dedicato quando si definisce il flusso utente di selezione batch.

Prossimo punto consigliato:

- punto 2, consolidare entrypoint locale, usando i runtime paths appena introdotti per log di avvio, errori critici e gestione porta.

Domande del punto da risolvere:

- dove salvare batch originali caricati dall'utente?
- dove salvare manifest elaborati?
- dove salvare anagrafica, cache anagrafica e modifiche manuali?
- dove salvare log e configurazione locale?
- serve un sistema di versioning/migrazione per anagrafica e manifest?
- come fare update senza perdere dati?

## Riflessioni tecniche

- Il packaging va iniziato ora, non alla fine, per scoprire presto problemi di path, permessi, OCR, porte e dati utente.
- Non bisogna confondere beta desktop con installer finale. Pero' la beta deve gia' usare la struttura reale.
- La separazione codice/dati e' la decisione piu' importante per rendere aggiornabile l'app.
- Ogni nuova feature da ora in avanti dovrebbe essere pensata chiedendosi: dove vive questo dato quando l'app e' installata?

## Prossimo passo consigliato

Implementare o consolidare una piccola utility Python centrale per i path runtime dell'app.

Questa utility dovrebbe rispondere in modo unico a domande come:

- qual e' la cartella dati dell'app?
- dove salvo anagrafica?
- dove salvo manifest elaborati?
- dove salvo log?
- sto girando in sviluppo o in app impacchettata?

#### Punto 0 parte XII - Coda lavorazioni e revisione indipendente

Stato: completato per i primi due livelli; resume automatico rinviato a sessione dedicata.

##### 1. Definizione teorica del problema

Il problema generale e': **separare il lavoro lungo dell'OCR dal lavoro interattivo dell'utente**.

In una situazione reale da studio, l'utente non lavora in modo lineare:

```text
carica 100 PDF
avvia OCR
carica altri 400 PDF
revisiona le fatture gia' pronte
salva alcune fatture
continua a far processare altri gruppi
```

Quindi il processamento non deve bloccare:

- caricamento di nuovi PDF;
- revisione di gruppi gia' pronti;
- salvataggio/eliminazione fatture;
- consultazione archivio e registro.

##### 2. Teoria risolutiva

La best practice e' distinguere tre concetti:

```text
Batch importati
  -> PDF gia' copiati dentro l'app.

Lavorazioni
  -> job OCR con stato in coda / in corso / completato / fallito / annullato.

Gruppi di revisione
  -> output stabile dei job completati, revisionabile indipendentemente da altri job.
```

Per la beta la scelta prudente e':

- una sola OCR attiva alla volta;
- piu' lavorazioni accodabili;
- caricamento PDF sempre consentito;
- revisione dei gruppi completati sempre consentita;
- annullamento possibile per ogni job non terminale;
- nessun OCR parallelo, per non saturare CPU/RAM e non rendere l'app inutilizzabile.

##### 3. Decisione implementativa

Implementare una coda seriale in memoria del backend locale:

- `POST /batches/process` crea sempre un job;
- se non c'e' OCR attiva, il job parte subito;
- se c'e' gia' OCR attiva, il job resta `queued`;
- quando il job attivo termina, fallisce o viene annullato, parte il prossimo in coda;
- il frontend puo' continuare a caricare PDF e revisionare fatture gia' presenti;
- la UI mostra stato per ogni batch importato, non un singolo stato globale.

Limite accettato per beta:

- se l'app viene chiusa, il job che era in corso viene marcato interrotto;
- i job che erano solo in coda vengono recuperati dal file persistente;
- eventuali processi incompleti su disco vengono puliti all'avvio tramite marker `processing_complete.ok`;
- non implementiamo ancora resume automatico dei job dopo riapertura.

##### 4. Occhi da costruire

- test backend su coda seriale;
- build frontend;
- controllo manuale UI: un batch in corso, un secondo in coda, revisione ancora utilizzabile.

##### 5. Implementazione eseguita

Backend:

- aggiunta coda seriale in memoria;
- aggiunta persistenza della coda in `config/processing_jobs.json`;
- aggiunto `PROCESSING_ACTIVE_JOB_ID`;
- aggiunto endpoint `GET /processing/jobs`;
- `POST /batches/process` ora crea un job e lo accoda;
- se non c'e' OCR attiva, il job parte subito;
- se c'e' OCR attiva, il job resta `queued`;
- a fine job viene avviato automaticamente il prossimo;
- annullare un job `queued` lo porta subito a `cancelled`;
- annullare un job `processing` mantiene annullamento cooperativo e rollback totale.
- alla riapertura, un job rimasto `processing` viene marcato come interrotto dalla chiusura app;
- alla riapertura, i job rimasti `queued` vengono recuperati.

Frontend:

- sostituito lo stato globale singolo con stato per batch;
- ogni batch importato mostra il proprio stato;
- un batch in coda/in corso ha pulsante `Annulla`;
- altri batch restano processabili mentre una OCR e' attiva;
- il polling legge la lista lavori, non un singolo job.
- aggiunta pagina `Lavorazioni` con elenco job, stato, avanzamento e annullamento.

##### 6. Verifiche eseguite

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_registry_api_processing_queue.py tests\test_processing_storage.py
npm --prefix apps/viewer run build
.\.venv\Scripts\python.exe -m py_compile scripts\registry_api.py scripts\local_app.py
```

Esito:

- 11 test passati;
- build frontend completata;
- controllo sintassi backend completato;
- resta warning locale pytest cache non scrivibile, non collegato alla modifica.

##### 7. Revisione critica

La soluzione e' adeguata per beta perche':

- evita OCR paralleli;
- consente caricamento/revisione durante lavorazioni lunghe;
- rende visibile la differenza tra `in coda` e `in corso`;
- mantiene rollback e cleanup gia' introdotti.

Limite consapevole:

- la coda e' persistente per i job non ancora iniziati;
- il job attivo al momento della chiusura non viene ripreso dal punto esatto;
- il resume automatico del job attivo e' una feature successiva, piu' complessa.

#### Punto 0 parte XIII - Resume automatico del processamento interrotto

Stato: analisi teorica e implementazione.

##### 1. Definizione teorica del problema

Il problema generale e': **rendere riprendibile un lavoro lungo senza corrompere l'output parziale gia' prodotto**.

Non basta sapere che il job era arrivato a un certo contatore. Serve sapere quali unita' di lavoro sono concluse in modo affidabile. Nel nostro caso l'unita' naturale non e' la pagina e non e' la fattura, ma il PDF sorgente importato:

```text
doc_000001.pdf -> completato
doc_000002.pdf -> completato
doc_000003.pdf -> interrotto durante OCR
doc_000004.pdf -> non iniziato
```

Il resume deve garantire questi invarianti:

- un PDF gia' completato non viene rielaborato;
- un PDF interrotto a meta' viene scartato e rielaborato da zero;
- la numerazione progressiva globale delle fatture resta stabile;
- i gruppi di revisione vengono creati una sola volta, alla fine del job;
- un processo ripreso non deve duplicare manifest, fatture o review batch;
- un processo annullato dall'utente continua invece a fare rollback totale.

##### 2. Best practice applicabili

Il pattern corretto e' una combinazione di:

```text
checkpoint per unita' di lavoro
scrittura atomica
marker di completamento
idempotenza del resume
promozione finale solo a job concluso
```

In pratica:

- ogni documento viene elaborato in una cartella temporanea;
- solo quando il documento e' completo viene spostato nella cartella definitiva;
- nella cartella definitiva viene scritto un marker `document_complete.ok`;
- alla ripartenza si leggono solo i documenti con marker;
- eventuali cartelle temporanee o documenti senza marker vengono eliminati;
- il job ricostruisce lo stato dai documenti completati;
- i review batch vengono rigenerati solo quando tutti i documenti sono stati attraversati.

##### 3. Strategia risolutiva scelta

Scelta per beta:

- riprendere automaticamente il job che era `processing` al momento della chiusura app;
- non riprendere a meta' OCR del singolo PDF;
- riprendere dal PDF successivo all'ultimo documento completato;
- se il documento in corso aveva lasciato file parziali, cancellarli e rifare quel documento;
- mantenere una sola OCR attiva;
- mantenere i job in coda come gia' implementato.

Decisione importante:

- l'annullamento manuale resta diverso dalla chiusura/crash;
- se l'utente preme `Annulla`, il job viene rimosso interamente;
- se l'app si chiude/crasha, il job viene ripreso automaticamente.

##### 4. Prima revisione critica della strategia

Rischio: numerazione progressiva globale.

Soluzione:

- per i documenti gia' completati, rileggo i manifest in ordine di batch;
- ricostruisco gli `invoice_refs`;
- il prossimo numero fattura e' `1 + numero fatture gia' completate`;
- il documento rielaborato dopo resume riceve quindi gli stessi numeri che avrebbe ricevuto nel run originale.

Rischio: gruppi di revisione duplicati.

Soluzione:

- durante il processamento non considero finali i `review_batches`;
- prima di rigenerarli a fine job pulisco la cartella `review_batches`;
- il marker finale `processing_complete.ok` resta l'unica prova che il processo e' pubblicabile.

Rischio: output parziale di un documento.

Soluzione:

- usare `doc_000123.working` come cartella temporanea;
- rimuoverla sempre all'inizio del documento;
- promuoverla a `doc_000123` solo dopo manifest valido;
- scrivere `document_complete.ok` solo dopo la promozione.

##### 5. Seconda revisione critica dopo confronto col codice attuale

Il codice attuale scrive direttamente in:

```text
manifests\process_...\documents\doc_000123
```

Questo non basta per resume robusto, perche' una chiusura durante OCR puo' lasciare una cartella apparentemente valida ma incompleta.

Correzione necessaria:

- introdurre una cartella working per documento;
- considerare completato solo il documento con marker;
- modificare il cleanup all'avvio per non cancellare processi associati a job riprendibili.

##### 6. Terza revisione critica prima del codice

La modifica non deve trasformare il job in un database complesso.

Quindi evito:

- resume a livello pagina;
- merge di review batch parziali;
- ripresa di Tesseract a meta' subprocess;
- scrittura continua di grandi indici.

Mantengo invece:

- checkpoint leggero per PDF;
- JSON job persistente gia' esistente;
- marker finale di processo;
- marker per documento.

Questa scelta e' abbastanza robusta per beta e non dovrebbe rallentare sensibilmente: il costo aggiunto e' scrivere piccoli marker JSON e fare rename di directory, trascurabile rispetto all'OCR.

##### 7. Piano implementativo

Micro-punti:

1. Aggiungere `document_complete.ok` per ogni documento completato.
2. Elaborare ogni documento in `doc_XXXXXX.working`.
3. Promuovere atomicamente la cartella working a cartella definitiva.
4. In resume, saltare i documenti con marker valido e ricostruire `invoice_refs`.
5. Cancellare working/incompleti prima di rielaborare un documento.
6. Dare al job persistente un `process_id` stabile.
7. Alla riapertura, trasformare il job `processing` in `queued` riprendibile, non in `cancelled`.
8. Pulire processi incompleti solo se non collegati a job riprendibili.
9. Aggiungere test su resume dopo crash.
10. Verificare build frontend e test backend.

##### 8. Implementazione eseguita

Implementazione completata per la prima versione beta del resume automatico.

Scelte realizzate nel codice:

- ogni job di processamento riceve un `process_id` stabile quando entra in coda;
- il `process_id` viene persistito in `processing_jobs.json`;
- se l'app si chiude mentre un job e' `processing`, alla riapertura quel job torna `queued`;
- il job ripreso usa lo stesso `process_id`, quindi rientra nella stessa cartella di output;
- ogni PDF viene elaborato in una cartella `doc_XXXXXX.working`;
- la cartella working viene promossa a `doc_XXXXXX` solo dopo aver trovato un `viewer_manifest.json` valido;
- dopo la promozione viene scritto `document_complete.ok`;
- in resume, i documenti con `document_complete.ok` vengono saltati e usati per ricostruire il contatore globale;
- i documenti senza marker o le cartelle `.working` vengono scartati e rifatti;
- i `review_batches` vengono rigenerati da zero solo alla fine del job;
- l'annullamento manuale continua a fare rollback totale del processo.

Moduli toccati:

- `src/estrattore/processing_storage.py`;
- `scripts/registry_api.py`;
- `tests/test_processing_storage.py`;
- `tests/test_registry_api_processing_queue.py`.

##### 9. Revisione critica finale

Controllo 1: coerenza dati.

Il resume non si fida della sola presenza di una cartella documento. Serve sempre:

```text
documents/doc_XXXXXX/viewer_manifest.json
documents/doc_XXXXXX/document_complete.ok
```

Questo evita di pubblicare output lasciati a meta' da OCR, crash o chiusura dell'app.

Controllo 2: numerazione fatture.

La numerazione resta progressiva perche' alla ripresa i manifest gia' completati vengono riletti in ordine di documento. Il prossimo `first_invoice_number` nasce dal numero di fatture gia' confermate, non da un contatore volatile in memoria.

Controllo 3: gestione cartelle incomplete.

All'avvio il sistema carica prima i job persistenti, poi pulisce le cartelle incomplete. Questo ordine e' essenziale: un processo incompleto ma collegato a un job riprendibile non deve essere cancellato.

Limiti intenzionali:

- non riprendiamo OCR a meta' PDF;
- il PDF in corso al momento del crash viene rifatto;
- i job molto vecchi senza `process_id` non sono riprendibili e restano trattati come interrotti;
- non esiste ancora una pagina dedicata allo storico completo delle lavorazioni.

Questi limiti sono accettabili per la beta perche' tengono la semantica semplice: o un documento e' completo, oppure viene rifatto.

##### 10. Verifiche

Eseguite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_registry_api_processing_queue.py tests\test_processing_storage.py
```

Risultato:

```text
13 passed
```

Eseguito:

```powershell
.\.venv\Scripts\python.exe -m py_compile src\estrattore\processing_storage.py scripts\registry_api.py scripts\local_app.py
```

Risultato: nessun errore.

Eseguito:

```powershell
npm --prefix apps/viewer run build
```

Risultato: build completata.

##### 11. Correzioni QA dopo checklist Punto 0

Problemi osservati durante la preparazione dei test manuali:

- i PDF caricati ma non ancora processati non avevano un comando di eliminazione;
- il dialog di annullamento processamento non spiegava abbastanza bene che l'annullamento e' cooperativo e puo' richiedere qualche secondo.

Decisione:

- aggiungere una `x` sui gruppi PDF importati;
- usare un dialog dell'app con conferma prima di eliminare;
- cancellare solo la copia interna sotto `batches`, mai il file originale sul disco;
- impedire la cancellazione se il gruppo ha una lavorazione `queued` o `processing`;
- in quel caso l'utente deve prima annullare la lavorazione;
- chiarire nel dialog `Annulla processamento` che l'app completa il PDF in corso e poi chiude il processo in modo sicuro.

File modificati:

- `src/estrattore/batch_storage.py`;
- `scripts/registry_api.py`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/App.css`;
- `tests/test_batch_storage.py`;
- `tests/test_registry_api_processing_queue.py`.

Verifiche:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_batch_storage.py tests\test_registry_api_processing_queue.py
.\.venv\Scripts\python.exe -m py_compile src\estrattore\batch_storage.py scripts\registry_api.py
npm --prefix apps/viewer run build
```

Esito:

- 11 test passati;
- controllo sintassi backend completato;
- build frontend completata.

##### 12. Pulizia messaggi errore visibili

Problema osservato:

- nella sezione OCR poteva comparire un errore tecnico del browser:

```text
SyntaxError: Unexpected token '<', "<!doctype "... is not valid JSON
```

Cause:

- alcune richieste frontend aspettano JSON;
- in sviluppo, se il file runtime/sample non esiste, il server puo' restituire HTML dell'app invece del JSON richiesto;
- il parsing JSON fallisce e il messaggio tecnico veniva mostrato direttamente all'utente.

Decisione:

- introdurre un helper frontend unico `userErrorMessage(...)`;
- intercettare errori tecnici noti:
  - JSON non valido;
  - risposta HTML al posto del JSON;
  - backend locale non raggiungibile;
  - `HTTP 404/500` generici;
- mostrare testi operativi puliti e contestuali;
- non esporre `String(error)`, `SyntaxError`, HTML o dettagli JavaScript nella UI.

File modificato:

- `apps/viewer/src/App.tsx`.

Verifica:

```powershell
npm --prefix apps/viewer run build
```

Esito:

- build frontend completata;
- ricerca mirata: nessun `String(error)` resta esposto direttamente nei messaggi UI.

##### 13. Origine leggibile dei PDF caricati

Problema osservato:

- quando si carica una cartella, nella sidebar compariva solo una label generica con data e numero PDF;
- l'utente non poteva controllare facilmente di aver scelto la cartella giusta;
- non era immediato capire se la stessa cartella fosse stata caricata due volte.

Decisione:

- aggiungere `source_label` al modello `ImportedBatchIndex`;
- per caricamento cartella, usare il nome della cartella selezionata;
- per caricamento multiplo file, usare una label sintetica tipo `N PDF selezionati`;
- inviare `source_label` dall'API `/batches`;
- mostrarlo nella sidebar come `Origine: ...`;
- mostrarlo anche nel dialog di eliminazione PDF caricati;
- mantenere fallback per batch vecchi senza `source_label`.

File modificati:

- `src/estrattore/batch_storage.py`;
- `scripts/registry_api.py`;
- `apps/viewer/src/types.ts`;
- `apps/viewer/src/App.tsx`;
- `apps/viewer/src/App.css`;
- `tests/test_batch_storage.py`.

Verifiche:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_batch_storage.py tests\test_registry_api_processing_queue.py
.\.venv\Scripts\python.exe -m py_compile src\estrattore\batch_storage.py scripts\registry_api.py
npm --prefix apps/viewer run build
```

Esito:

- 11 test passati;
- controllo sintassi backend completato;
- build frontend completata.

#### Punto 0 parte XIV - Layer unico di import documenti

Stato: completato con implementazione incrementale.

##### 1. Definizione teorica del problema

Il problema generale e': **governare l'acquisizione di documenti utente in una app desktop senza dipendere dai limiti del browser e senza perdere controllo sul ciclo di vita dei file**.

Non stiamo solo scegliendo un file picker. Stiamo definendo il confine tra:

- UI React;
- layer locale/native Windows;
- backend Python;
- storage interno dell'app;
- file originali dell'utente.

Oggi il flusso e' ibrido:

```text
Carica PDF
  -> input HTML/browser
  -> selezione multipla file

Carica cartella
  -> backend Python
  -> tkinter askdirectory
  -> una cartella alla volta
```

Questo crea una UX incoerente e una architettura ambigua:

- due finestre diverse;
- due set di limiti diversi;
- due modi diversi di gestire annulla/errore;
- difficolta' a supportare piu' cartelle;
- difficolta' a mostrare una lista preliminare di sorgenti prima dell'import;
- dipendenza dal comportamento del browser/WebView per un'azione che in una app desktop dovrebbe essere controllata dal layer locale.

La formulazione piu' generale e':

```text
L'app deve avere un unico processo di acquisizione documenti:
  selezione sorgenti
  -> anteprima/controllo sorgenti
  -> validazione
  -> copia nello storage interno
  -> creazione batch importato
```

##### 2. Teoria risolutiva e best practice

In una app desktop professionale, il file picker non deve essere la logica di import. Il picker e' solo un modo per aggiungere sorgenti a una lista.

Best practice rilevanti:

- separare **selezione** da **import effettivo**;
- non copiare file appena l'utente apre un picker se prima serve conferma;
- mostrare all'utente cosa sta per importare;
- permettere rimozione di sorgenti selezionate prima dell'import;
- validare duplicati, cartelle vuote e file non PDF prima di creare batch;
- copiare sempre nello storage interno dell'app;
- non modificare mai i file originali;
- restituire alla UI un risultato strutturato, non path grezzi da manipolare nel browser;
- mantenere un solo backend path per creare `ImportedBatchIndex`.

Pattern consigliato:

```text
React
  -> apre pannello "Carica documenti"
  -> chiede al backend di scegliere PDF o cartella
  -> riceve una sorgente normalizzata
  -> mostra la lista sorgenti
  -> conferma import

Backend/local native
  -> apre picker Windows
  -> valida path base
  -> conta PDF
  -> restituisce metadati sorgente
  -> su conferma copia nello storage interno
```

Nota importante:

- non serve imitare Explorer al 100%;
- serve costruire un workflow migliore per il nostro dominio;
- la selezione multipla cartelle puo' essere ottenuta con una lista di sorgenti incrementale, anche se il picker nativo base sceglie una cartella alla volta.

##### 3. Approccio tecnico scelto

Classificazione: **scelta incrementale sana**.

Non e' ancora il sistema finale di packaging native completo, ma va nella direzione finale e non va buttato dopo.

Approccio proposto:

```text
Nuovo pannello UI: Carica documenti
  [Aggiungi PDF]
  [Aggiungi cartella]

  Sorgenti selezionate:
    Cartella: Giugno 2025       48 PDF
    Cartella: Luglio 2025       51 PDF
    PDF: fattura singola.pdf     1 PDF

  [Rimuovi selezionati]
  [Importa 100 PDF]
```

Regole:

- `Carica PDF` e `Carica cartella` non devono piu' importare direttamente;
- devono aggiungere sorgenti alla lista temporanea;
- l'import avviene solo quando l'utente conferma;
- la lista mostra nome sorgente, tipo sorgente e numero PDF trovati;
- se la stessa cartella viene aggiunta due volte, la UI lo segnala prima dell'import;
- se la stessa sorgente file viene aggiunta due volte, la UI lo segnala;
- l'utente puo' rimuovere sorgenti prima di importare;
- dopo conferma, il backend crea uno o piu' batch.

Decisione proposta su batch:

- per ora creare **un batch unico** da tutte le sorgenti selezionate.

Motivo:

- l'utente sta costruendo intenzionalmente un'unica lavorazione;
- la coda OCR lavora gia' per batch;
- un batch unico evita di generare decine di righe se l'utente seleziona molte cartelle piccole.

Controindicazione:

- se lo studio vuole mantenere separazione forte per cartella, potremmo aggiungere piu' avanti una scelta:

```text
Importa come:
  ( ) gruppo unico
  ( ) un gruppo per cartella
```

Per ora non la introdurrei: rischia di complicare la UI prima di aver validato il workflow reale.

##### 4. Micro-step implementativi proposti

1. Modellare `PendingImportSource`.

   Area:

   - frontend types/local state;
   - backend payload.

   Campi minimi:

   - `source_id`;
   - `kind`: `files` oppure `folder`;
   - `label`;
   - `path_count` o `document_count`;
   - `duplicate_key`;
   - `warning`.

2. Aggiungere endpoint backend per scegliere PDF via Windows/native.

   Stato attuale:

   - PDF usa input browser.

   Nuovo:

   - `POST /batches/pick-files`;
   - apre picker Windows per file PDF multipli;
   - restituisce lista sorgenti/metadati, non importa subito.

   Nota:

   - `tkinter.filedialog.askopenfilenames` puo' essere primo layer Windows coerente con `askdirectory`;
   - non e' la soluzione native perfetta, ma e' incrementale sana perche' sposta il controllo fuori dal browser.

3. Modificare endpoint cartella.

   Stato attuale:

   - `POST /batches/pick-folder` importa subito.

   Nuovo:

   - dovrebbe restituire una sorgente cartella selezionata;
   - l'import vero avviene dopo conferma.

   Compatibilita':

   - durante transizione possiamo introdurre nuovi endpoint e poi rimuovere il vecchio comportamento.

4. Aggiungere endpoint di import da sorgenti confermate.

   Possibile endpoint:

   - `POST /batches/import-sources`.

   Responsabilita':

   - riceve sorgenti gia' selezionate;
   - ricava PDF;
   - appiattisce;
   - copia nello storage interno;
   - crea `ImportedBatchIndex`;
   - salva `source_label` utile in sidebar.

5. Costruire pannello UI `Carica documenti`.

   Requisiti:

   - sostituire i due bottoni diretti nella sidebar;
   - mostrare una finestra/pannello coerente con lo stile app;
   - permettere aggiunta PDF;
   - permettere aggiunta cartella;
   - mostrare lista sorgenti;
   - mostrare duplicati e cartelle vuote;
   - permettere rimozione;
   - confermare import.

6. Aggiornare comportamento sidebar.

   Dopo import:

   - mostrare `Origine` come ora;
   - per batch da piu' sorgenti, usare label tipo `3 sorgenti - 100 PDF`;
   - mantenere lista scorrevole;
   - mantenere cancellazione dei PDF importati con conferma.

7. Test e occhi.

   Test automatici:

   - import da sorgenti multiple;
   - duplicato cartella rilevato;
   - duplicato file rilevato;
   - cartella senza PDF;
   - batch unico con source label corretta;
   - path con spazi.

   Occhi manuali:

   - aprire app;
   - aggiungere due cartelle;
   - aggiungere la stessa cartella due volte;
   - rimuovere una sorgente;
   - importare;
   - verificare sidebar;
   - verificare AppData;
   - processare batch creato.

##### 5. Revisione critica prima dell'implementazione

Perche' questa proposta e' coerente con app desktop seria:

- elimina la UX ibrida browser/native;
- crea un unico concetto utente: `sorgenti da importare`;
- riduce rischio di caricamenti doppi non visti;
- prepara selezione multi-cartella senza dipendere da feature browser;
- mantiene sicurezza sui file originali;
- migliora il controllo prima di creare batch pesanti;
- resta compatibile con futuro WebView/Electron/launcher.

Rischi:

- richiede piu' codice frontend rispetto ai due bottoni attuali;
- bisogna decidere come serializzare temporaneamente i path scelti;
- i path originali non devono diventare stato permanente sensibile oltre il tempo necessario all'import;
- `tkinter` non e' necessariamente il layer finale migliore per packaging, ma usarlo dietro un endpoint locale e' sostituibile piu' avanti.

Mitigazione:

- isolare la scelta file/cartelle in funzioni backend piccole;
- non far conoscere al frontend logiche filesystem complesse;
- mantenere l'import effettivo in `batch_storage`;
- documentare chiaramente che `tkinter` e' implementazione del layer native, non architettura del prodotto.

##### 6. Decisioni applicate

Decisioni applicate nella prima implementazione:

1. Il primo import multi-sorgente deve creare sempre un batch unico?

   Proposta: si.

2. Vogliamo mantenere temporaneamente i vecchi bottoni o sostituirli subito con `Carica documenti`?

   Proposta: sostituirli, per evitare doppio workflow.

3. I duplicati devono bloccare l'import o solo avvisare?

   Proposta: bloccare duplicati esatti di sorgente, avvisare eventuali nomi simili.

4. Il pannello deve stare nella sidebar o aprirsi come pagina/modal grande?

   Proposta: modal/pagina dedicata piccola-media, non dentro la sidebar, per dare spazio alla lista sorgenti.

##### 7. Implementazione eseguita

Decisioni confermate durante l'implementazione:

- il workflow visibile e' stato unificato in `Carica documenti`;
- i vecchi pulsanti separati `Carica PDF` / `Carica cartella` non sono piu' esposti nella sidebar;
- la selezione e l'import sono separati;
- le sorgenti selezionate restano temporanee nel backend e vengono importate solo alla conferma;
- il primo comportamento crea un batch unico da tutte le sorgenti confermate;
- i duplicati esatti di sorgente vengono bloccati prima dell'import;
- quando l'utente annulla, rimuove una sorgente o aggiunge un duplicato, il frontend chiede al backend di scartare la sorgente temporanea.

Backend:

- aggiunto `POST /batches/pick-files`;
- modificato `POST /batches/pick-folder` per restituire una sorgente pendente invece di importare subito;
- aggiunto `POST /batches/import-sources`;
- aggiunto `POST /batches/import-sources/discard`;
- introdotto `PENDING_IMPORT_SOURCES` come memoria temporanea runtime;
- centralizzato l'import confermato in `_import_pending_sources`;
- mantenuto l'import effettivo tramite `import_pdf_batch`, quindi la copia nello storage interno resta nel layer gia' corretto;
- mantenuto temporaneamente `POST /batches/import` come compatibilita' backend, anche se la UI non lo usa piu'.

Frontend:

- aggiunto tipo `PendingImportSource`;
- aggiunta finestra `DocumentImportDialog`;
- aggiunti comandi `Aggiungi PDF`, `Aggiungi cartella`, `Rimuovi sorgente`, `Importa N PDF`;
- rimossa la dipendenza visibile dall'input file browser;
- la sidebar ora espone un solo comando: `Carica documenti`.

Test:

- aggiunto test di import multi-sorgente in batch unico;
- aggiunto test di rifiuto duplicati;
- rafforzata l'asserzione sul messaggio duplicato.

##### 8. Verifiche

Comandi eseguiti:

```text
.\.venv\Scripts\python.exe -m py_compile scripts\registry_api.py src\estrattore\batch_storage.py
.\.venv\Scripts\python.exe -m pytest tests\test_batch_storage.py tests\test_registry_api_processing_queue.py
npm --prefix apps/viewer run build
```

Esito:

- sintassi Python valida;
- 13 test passati;
- build frontend completata;
- unico warning: pytest non riesce a scrivere cache in `.pytest_cache`, non riguarda il comportamento applicativo.

##### 9. Revisione critica

Il punto e' soddisfacente perche':

- elimina il doppio comportamento incoerente tra PDF e cartella;
- rende il caricamento piu' simile a una app desktop seria: prima raccolta sorgenti, poi conferma;
- non lavora sui file originali dell'utente;
- prepara bene il caso reale di piu' cartelle caricate in sequenza;
- mantiene il frontend lontano dai path filesystem;
- mantiene sostituibile il layer `tkinter` quando arriveremo al packaging definitivo.

Limiti consapevoli:

- `tkinter` resta un'implementazione tecnica transitoria del native picker, non una scelta definitiva di prodotto;
- la selezione multipla cartelle non e' ancora una finestra Windows unica: si ottiene aggiungendo piu' cartelle una alla volta;
- i duplicati sono bloccati per sorgente esatta, non ancora per sovrapposizione parziale tra una selezione file e una cartella che contiene gli stessi file;
- le sorgenti pendenti sono memoria runtime: se l'app viene chiusa prima della conferma, vengono perse, che e' corretto perche' non sono ancora un import confermato.

Decisione finale del punto:

- punto completato;
- prossimo raffinamento possibile: controllo duplicati per singolo file fisico dentro sorgenti diverse, se nei test utente emerge che caricare cartella + file contenuto nella cartella e' un errore frequente.

#### Punto 0 parte XV - Governo delle lavorazioni fuori dalla sidebar

Stato: in corso.

##### 1. Definizione teorica del problema

Il problema generale e': **separare navigazione, revisione documentale e controllo dei processi lunghi**.

La sidebar deve aiutare l'utente a:

- caricare documenti;
- entrare negli strumenti principali;
- selezionare gruppo/fattura;
- compilare senza perdita di spazio.

Non deve diventare il pannello operativo completo dei job OCR. Quando i box delle lavorazioni stanno nella sidebar:

- comprimono la lista fatture;
- duplicano informazioni gia' presenti nella pagina dedicata;
- rendono piu' difficile compilare mentre l'OCR lavora;
- mischiano due livelli concettuali diversi: "dove sono le fatture" e "cosa sta facendo il motore OCR".

Il problema fratello e' il controllo dei job lunghi:

- processo attivo;
- processi in coda;
- pausa del singolo job;
- ripresa del singolo job;
- rimozione dalla coda;
- annullamento distruttivo;
- avanzamento verso il job successivo.

##### 2. Teoria risolutiva e best practice

In una app desktop seria, i processi lunghi devono avere una pagina o pannello dedicato di gestione operativa.

Best practice:

- sidebar leggera: navigazione e stato minimo;
- pagina dedicata: stato dettagliato, progressi e azioni;
- azioni su job esplicite e reversibili dove possibile;
- differenza chiara tra `Pausa` e `Annulla`;
- pausa cooperativa tra unita' di lavoro sicure, non kill brutale del processo;
- rimozione dalla coda possibile solo se il job non e' gia' in esecuzione;
- ripresa di un job in pausa tramite la stessa infrastruttura di resume;
- persistenza dello stato dei job, cosi' chiusura app e riapertura non creano ambiguita'.

Definizioni operative:

- `Annulla`: interrompe la lavorazione e scarta il run parziale;
- `Pausa`: completa il PDF corrente, conserva il run parziale e libera il worker per il prossimo job;
- `Riprendi`: rimette il job in pausa in coda, usando lo stesso `process_id`;
- `Rimuovi dalla coda`: toglie un job non attivo dalla coda senza cancellare i PDF importati.

Questa e' una scelta incrementale sana: non serve un orchestratore esterno, ma il modello si avvicina a quello finale del launcher desktop.

##### 3. Approccio tecnico scelto

Backend:

- introdurre stato job `paused`;
- aggiungere flag `pause_requested`;
- aggiungere endpoint:
  - `POST /processing/jobs/<id>/pause`;
  - `POST /processing/jobs/<id>/resume`;
  - `POST /processing/jobs/<id>/remove`;
- implementare pausa cooperativa in `process_imported_batch`;
- non cancellare la directory di processo in caso di pausa;
- dopo pausa del job attivo, liberare il worker e far partire il job successivo.

Frontend:

- rinominare `Lavorazioni` in un nome piu' chiaro e operativo;
- rimuovere i box dettagliati `PDF caricati` dalla sidebar;
- mantenere in sidebar solo un accesso leggero alla gestione processi;
- spostare nella pagina dedicata azioni e dettagli:
  - processa;
  - pausa;
  - riprendi;
  - annulla;
  - rimuovi dalla coda;
  - progresso;
  - tempo stimato;
  - file corrente.

##### 4. Micro-step implementativi

1. Aggiornare modello backend dei job.

   File:

   - `src/estrattore/processing_storage.py`;
   - `scripts/registry_api.py`.

   Verifica:

   - test pausa tra documenti;
   - test resume da pausa;
   - test rimozione job in coda.

2. Aggiornare API locali.

   File:

   - `scripts/registry_api.py`.

   Verifica:

   - test helper backend;
   - controllo sintassi.

3. Pulire sidebar.

   File:

   - `apps/viewer/src/App.tsx`;
   - `apps/viewer/src/App.css`.

   Verifica:

   - build React;
   - controllo visuale: lista fatture non compressa dai box OCR.

4. Potenziare pagina gestione processi.

   File:

   - `apps/viewer/src/App.tsx`;
   - `apps/viewer/src/App.css`.

   Verifica:

   - build React;
   - flusso manuale: processa, pausa, riprendi, annulla, rimuovi dalla coda.

##### 5. Decisioni durante implementazione

- `Pausa` non e' una pausa globale del motore OCR: e' una sospensione del singolo job.
- Se il job e' attivo, la pausa e' cooperativa: termina il PDF corrente, salva i marker gia' completati, passa a `paused` e libera il worker per il job successivo.
- Se il job e' in coda, la pausa e' immediata: viene rimosso dalla coda e passa a `paused`.
- `Riprendi` rimette un job `paused` in coda con lo stesso `process_id`; il resume esistente salta i PDF gia' completati.
- `Rimuovi dalla coda` vale per job `queued` o `paused`; non cancella i PDF importati, ma elimina eventuale run parziale interno per evitare manifest orfani.
- `Annulla` resta distinto da `Pausa`: scarta il run parziale.
- La sidebar non mostra piu' i box `PDF caricati`; mostra solo un riepilogo leggero e il pulsante verso `Processi OCR`.
- La pagina `Processi OCR` diventa la superficie operativa per processare, pausare, riprendere, annullare, rimuovere dalla coda ed eliminare gruppi PDF.

##### 6. Implementazione eseguita

Backend:

- aggiunta eccezione `ProcessingPaused`;
- aggiunto callback `should_pause` in `process_imported_batch`;
- pausa gestita tra documenti, non durante OCR del PDF corrente;
- stato job esteso con `paused`, `removed`, `pause_requested`;
- aggiunti endpoint:
  - `POST /processing/jobs/<id>/pause`;
  - `POST /processing/jobs/<id>/resume`;
  - `POST /processing/jobs/<id>/remove`;
- aggiornato caricamento job persistenti:
  - `processing` dopo riavvio torna in coda;
  - `paused` resta fermo;
  - `paused` preserva il process dir dalla pulizia degli incompleti;
- eliminazione gruppo PDF bloccata anche se esiste un job in pausa collegato.

Frontend:

- rinominata pagina/pulsante da `Lavorazioni` a `Processi OCR`;
- sidebar alleggerita: rimossi i box dettagliati dei gruppi caricati;
- aggiunto riepilogo semplice dei gruppi PDF caricati;
- pagina `Processi OCR` basata sui gruppi PDF importati, quindi mostra anche gruppi pronti ma non ancora processati;
- azioni disponibili nella pagina:
  - `Processa`;
  - `Elimina PDF`;
  - `Pausa`;
  - `Riprendi`;
  - `Rimuovi dalla coda`;
  - `Annulla`;
- aggiunta visualizzazione distinta di `Pausa richiesta` e `Annullamento in corso`.

Test:

- test pausa tra documenti senza cancellare il run parziale;
- test pausa immediata di job in coda;
- test resume di job in pausa;
- test rimozione job dalla coda senza cancellare il batch PDF.

##### 7. Verifiche

Comandi eseguiti:

```text
.\.venv\Scripts\python.exe -m py_compile scripts\registry_api.py src\estrattore\processing_storage.py
.\.venv\Scripts\python.exe -m pytest tests\test_processing_storage.py tests\test_registry_api_processing_queue.py
npm --prefix apps/viewer run build
```

Esito:

- sintassi Python valida;
- 22 test passati;
- build frontend completata;
- unico warning: pytest non riesce a scrivere cache in `.pytest_cache`, non riguarda il comportamento applicativo.

##### 8. Revisione critica e chiusura

Il punto e' soddisfacente perche':

- riduce il carico visivo della sidebar e protegge lo spazio per la lista fatture;
- sposta il controllo dei job nella pagina corretta;
- separa semanticamente pausa, annullamento e rimozione dalla coda;
- usa il resume gia' esistente invece di duplicare logica OCR;
- evita output parziali visibili come batch revisionabili;
- mantiene i PDF importati separati dai run OCR, quindi `Rimuovi dalla coda` non distrugge documenti caricati.

Limiti residui:

- la pausa e' cooperativa tra PDF, non interrompe Tesseract durante il PDF corrente;
- manca ancora una conferma dedicata per `Rimuovi dalla coda`, se nei test utente verra' percepita come azione rischiosa;
- la pagina `Processi OCR` e' ora funzionale, ma potra' essere rifinita graficamente dopo prova reale con molte lavorazioni.

Decisione finale:

- punto completato;
- prossimo controllo manuale consigliato: caricare piu' gruppi, avviare due processi, mettere in pausa il primo e verificare che parta il secondo.

#### Punto 0 parte XVI - Infrastruttura tabellare comune per anagrafica e registro fatture

Stato: in corso.

##### 1. Definizione teorica del problema

Il problema generale e': **mantenere coerenti viste dati grandi che hanno lo stesso comportamento applicativo ma contenuti diversi**.

Non e' solo "rendere editabile il registro". Anagrafica e registro fatture sono entrambe tabelle operative persistenti:

- molte righe nel tempo;
- ricerca e filtri;
- modifica inline;
- salvataggio esplicito;
- righe evidenziate per stato o qualita';
- azioni per riga;
- bisogno di prestazioni stabili anche con dataset grandi.

Se ogni tabella implementa da sola scroll, rendering righe, edit mode, stile celle e azioni, il codice diverge velocemente. Il rischio e' avere bug risolti nell'anagrafica ma non nel registro, o viceversa.

##### 2. Teoria risolutiva e best practice

Best practice:

- separare infrastruttura tabellare e contenuto delle celle;
- virtualizzare tutte le tabelle che possono crescere molto;
- mantenere row height stabile per evitare calcoli fragili;
- configurare colonne e renderer per dominio;
- non creare una mega astrazione generica se bastano pochi parametri chiari;
- mantenere salvataggio e validazione fuori dal componente tabella.

Giudizio tecnico:

- conviene estrarre un componente `VirtualDataTable` piccolo;
- deve restare nel frontend per ora, perche' il bisogno e' solo UI;
- deve accettare righe, header, className per riga, renderer riga e ref viewport;
- anagrafica e registro devono usarlo entrambi;
- le differenze di dominio restano nei rispettivi renderer.

Questa non e' novelty: e' un pattern standard per app gestionali.

##### 3. Approccio tecnico scelto

Classificazione: **scelta incrementale sana**.

Approccio:

```text
VirtualDataTable
  -> gestisce scroll virtualizzato
  -> calcola righe visibili
  -> renderizza header sticky
  -> espone recordIndex reale

RegistryTable
  -> decide colonne anagrafica
  -> decide input/celle/azioni anagrafica

InvoiceRegistryTable
  -> decide colonne registro fatture
  -> decide input/celle/link PDF/righe incomplete
```

Non creo ancora un package UI separato. Lo faro' solo se emergono altre tabelle con lo stesso bisogno.

##### 4. Micro-step implementativi

1. Estrarre componente `VirtualDataTable`.

   Area:

   - `apps/viewer/src/App.tsx`;
   - `apps/viewer/src/App.css`.

   Verifica:

   - build TypeScript.

2. Migrare `RegistryTable` su `VirtualDataTable`.

   Motivo:

   - conservare il comportamento gia' buono dell'anagrafica usando la nuova base.

   Verifica:

   - ricerca anagrafica e highlight restano possibili;
   - edit/delete righe restano collegati all'indice reale.

3. Migrare `InvoiceRegistryTable` su `VirtualDataTable`.

   Motivo:

   - registro fatture deve scalare come anagrafica.

   Verifica:

   - righe incomplete restano gialle;
   - edit inline resta funzionante;
   - link PDF origine resta visibile;
   - salvataggio continua a scrivere il JSON.

4. Revisione CSS.

   Motivo:

   - stessa infrastruttura, identita' visiva coerente, colonne diverse.

   Verifica:

   - nessuna sovrapposizione evidente;
   - tabella scorrevole sia verticale sia orizzontale.

##### 5. Dubbi durante implementazione

- Il registro usa molte piu' colonne dell'anagrafica: serve permettere `min-width` e `grid-template-columns` specifici.
- La virtualizzazione richiede altezza riga stabile: gli input non devono aumentare altezza.
- I link PDF devono restare azioni di riga, non partecipare alla logica di salvataggio.

##### 6. Implementazione eseguita

Frontend:

- introdotto `VirtualDataTable` in `apps/viewer/src/App.tsx`;
- `VirtualDataTable` gestisce:
  - stato scroll;
  - calcolo righe visibili;
  - overscan;
  - altezza virtuale totale;
  - header sticky;
  - indice reale della riga;
  - attributi per riga, utili per highlight/search;
- `RegistryTable` ora usa `VirtualDataTable`;
- `InvoiceRegistryTable` ora usa `VirtualDataTable`;
- il registro fatture mantiene:
  - edit inline;
  - righe incomplete gialle;
  - link `Apri origine`;
  - salvataggio su `invoice_registry.json`;
- il CSS usa classi comuni `virtual-data-table-head` e `virtual-data-table-row`, con colonne specifiche per anagrafica e registro.

Decisione durante implementazione:

- non ho memoizzato direttamente `VirtualDataTable` perche' `React.memo` faceva perdere inferenza al tipo generico in TypeScript;
- la virtualizzazione stessa e' l'ottimizzazione importante;
- i componenti specifici restano `memo`, quindi il rendering di dominio non viene peggiorato.

##### 7. Verifiche

Comando eseguito:

```text
npm --prefix apps/viewer run build
```

Esito:

- build React/Vite completata;
- TypeScript valido;
- nessun test Python necessario, perche' il cambiamento e' confinato al rendering frontend.

##### 8. Revisione critica e chiusura

Il punto e' soddisfacente perche':

- anagrafica e registro fatture ora condividono la stessa infrastruttura di tabella virtualizzata;
- il registro non renderizza piu' tutte le righe insieme;
- lo scroll/edit/highlight restano separati dal contenuto specifico delle celle;
- il codice e' piu' coerente con una app gestionale che puo' crescere a migliaia di righe;
- il refactor non cambia il modello dati ne' il formato del registro;
- eventuali prossime tabelle potranno riusare lo stesso componente invece di copiare logica.

Limiti residui:

- `VirtualDataTable` resta dentro `App.tsx`; se cresce ancora andra' spostato in un file dedicato tipo `components/VirtualDataTable.tsx`;
- l'altezza riga e' ancora globale e fissa a 64px, scelta corretta ora ma da rendere parametrica se avremo tabelle con densita' diverse;
- non e' stato fatto test visuale automatico con screenshot, quindi il controllo estetico finale resta da fare in app.

Decisione finale:

- punto completato;
- prossimo miglioramento consigliato: aprire app e confrontare visivamente anagrafica e registro con molte righe, verificando scroll verticale, scroll orizzontale, edit e righe gialle.

##### 9. Controllo successivo del risultato

Controlli eseguiti dopo richiesta utente:

- `npm --prefix apps/viewer run build`;
- controllo dei selettori CSS collegati alla nuova infrastruttura virtualizzata;
- controllo che non restassero riferimenti attivi alle vecchie classi `registry-preview-table-row` / `invoice-registry-table-row`;
- controllo presenza Playwright: non installato nel viewer, quindi niente screenshot automatico senza introdurre una nuova dipendenza.

Esito:

- build completata;
- la struttura comune `VirtualDataTable` e' usata da anagrafica e registro;
- registro e anagrafica hanno griglie colonne separate ma stessa logica di scroll virtualizzato;
- eliminate regole CSS morte per le vecchie tabelle HTML `th/td/tr`, per evitare confusione futura;
- nessuna modifica backend necessaria.

Nota estetica:

- da codice la resa e' coerente: header sticky, righe a quota fissa, overflow orizzontale per il registro, colori di stato conservati;
- resta opportuno un controllo manuale nell'app per verificare densita' visiva e comodita' reale con dati lunghi.

## Riorganizzazione documentale - regole e notebook

Decisione:

- `AGENTS.md` diventa l'indice breve e autoritativo delle istruzioni progetto;
- `.agents/rules_workflow.md` contiene le regole operative stabili;
- `docs/notebook.md` contiene solo memoria progettuale, decisioni, tentativi, dubbi, verifiche e chiusure;
- `docs/packaging_plan.md` contiene la roadmap futura del desktop packaging;
- il vecchio `docs/desktop_packaging.md` e' stato rinominato in `docs/notebook.md`;
- il vecchio `.agents/ui_check_protocol.md` e' stato assorbito in `.agents/rules_workflow.md` ed eliminato.

Motivo:

- separare regole stabili da storia del progetto;
- evitare che il notebook diventi un manuale operativo duplicato;
- evitare che il notebook diventi anche un backlog di packaging futuro;
- rendere piu' probabile che Codex segua sempre lo stesso workflow;
- mantenere Playwright/UI QA come protocollo stabile, ma fuori dal packaging finale.

Verifica:

- non restano riferimenti ufficiali a `docs/desktop_packaging.md`;
- non restano riferimenti ufficiali a `.agents/ui_check_protocol.md`;
- la roadmap futura 3-6 del packaging e' stata spostata in `docs/packaging_plan.md`;
- `AGENTS.md` punta a `.agents/rules_workflow.md` e `docs/notebook.md`.

Aggiornamento successivo:

- il protocollo screenshot e' stato precisato in `.agents/rules_workflow.md`;
- gli screenshot Playwright devono essere temporanei;
- devono coprire il perimetro reale della modifica fatta, non una lista fissa di pagine importanti;
- dopo la valutazione devono essere cancellati, salvo richiesta esplicita di conservarli.

Applicazione pratica:

- creato scheletro tooling in `tools/ui-checks`;
- il tooling e' separato dal viewer e quindi non entra nel packaging finale;
- gli screenshot vengono prodotti in `tmp/ui-checks`;
- il comportamento default dello script e' cancellare gli screenshot a fine controllo;
- `--keep` esiste solo per analisi esplicita e temporanea;
- Playwright non e' stato installato in questa modifica: manca ancora `npm --prefix tools/ui-checks install` e installazione browser Playwright.

Verifica ambiente completata:

- Playwright installato dall'utente dentro `tools/ui-checks`;
- Chromium Playwright installato nel percorso standard utente `%LOCALAPPDATA%\ms-playwright`;
- `node --check` sugli script `capture-change-screenshots.mjs` e `clean-screenshots.mjs` completato senza errori;
- test diretto `chromium.launch()` completato correttamente fuori sandbox;
- eseguito controllo reale sull'app locale `http://127.0.0.1:8765`;
- screenshot temporanei prodotti con `--keep`, ispezionati e poi cancellati con `npm --prefix tools/ui-checks run clean`;
- `tmp/ui-checks` resta la sola cartella destinata agli artefatti temporanei.





