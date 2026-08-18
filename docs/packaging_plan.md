# Packaging plan - app desktop Windows

Questo documento contiene il piano futuro di packaging e distribuzione.

Non e' il notebook operativo quotidiano. Il notebook vive in `docs/notebook.md` e deve contenere passato e presente: punti eseguiti, decisioni prese, dubbi emersi durante il lavoro e stato corrente.

## Nome del lavoro

Il lavoro si chiama **desktop packaging** o **desktop application packaging**.

Nel nostro caso significa prendere un'app locale da sviluppo composta da:

- frontend React/Vite;
- backend Python locale;
- OCR e logica di estrazione;
- file locali come input, manifest, anagrafiche e output;
- avvio attuale tramite script.

E trasformarla progressivamente in una normale app desktop Windows:

- avviabile da icona o launcher;
- installabile;
- usabile senza terminale;
- aggiornata senza perdere dati;
- stabile su PC non di sviluppo.

## Approccio scelto

Non dobbiamo riscrivere l'app come desktop app nativa.

L'approccio scelto e':

```text
launcher Windows
  -> avvia backend Python locale
  -> serve frontend React buildato
  -> apre finestra app controllata dal launcher
  -> salva dati in cartelle utente persistenti
```

Questo approccio mantiene il valore del codice gia' scritto:

- React resta il frontend;
- Python resta il backend;
- la logica OCR resta nel package esistente;
- il packaging aggiunge il modo corretto di distribuire e avviare l'app.

## Decisioni gia' prese

- Il browser esterno resta solo modalita' sviluppo/debug.
- L'app finale non deve dipendere da una tab di Chrome/Edge aperta dall'utente.
- Il launcher deve possedere la finestra applicativa o comunque controllarne il ciclo di vita.
- Chiudere la finestra dell'app deve permettere di governare anche backend Python e lavorazioni OCR.
- I dati utente devono vivere fuori dal codice applicativo, in una cartella runtime persistente.
- L'app non deve modificare o cancellare file fuori dal proprio storage interno.

## Problema launcher/finestra controllata

Scenario attuale sviluppo:

```text
python scripts/run_app.py
  -> avvia backend Python
  -> apre browser esterno

Se l'utente chiude la pagina browser:
  -> il backend Python continua a girare
  -> eventuale OCR continua
  -> l'utente puo' non capire che l'app e' ancora attiva
```

Questo non va trattato come semplice bug UI. E' un problema di ownership del processo applicativo.

Decisione architetturale da affrontare nel packaging:

- il processo launcher deve essere il proprietario del backend;
- la finestra finale deve avere un evento di chiusura controllabile;
- in chiusura, se non ci sono lavorazioni attive, il launcher puo' terminare backend e app;
- in chiusura, se ci sono lavorazioni attive, l'app deve chiedere all'utente cosa fare:
  - tornare all'app;
  - annullare la lavorazione e chiudere;
  - continuare in background, se questa modalita' verra' approvata;
- non usare `beforeunload`/chiusura tab browser come soluzione finale, perche' refresh, crash, doppie tab e browser esterno rendono fragile il ciclo di vita.

## Roadmap futura

### 3. Preparare frontend buildato

Obiettivo:

- usare sempre `apps/viewer/dist` nel pacchetto desktop;
- mantenere `npm run dev` solo per sviluppo frontend;
- evitare che la beta dipenda da sorgenti React.

Nota:

- `--rebuild` resta utile in sviluppo;
- nel pacchetto desktop il frontend deve essere gia' buildato.

Domande da risolvere dentro questo punto:

- come garantire che il pacchetto desktop includa sempre una build frontend aggiornata?
- la beta deve poter partire se la build manca oppure deve fallire con errore chiaro?
- quali asset statici devono stare nel pacchetto e quali nei dati utente?
- come evitare cache/browser stale durante test e aggiornamenti?

### 4. Creare launcher eseguibile

Obiettivo:

- generare un `.exe` che avvia l'app;
- non chiedere all'utente di conoscere Python;
- gestire errori di avvio con log comprensibili.

Prima ipotesi:

- PyInstaller per impacchettare launcher/backend Python.

Da verificare:

- dimensione pacchetto;
- gestione dipendenze OCR;
- compatibilita' Windows;
- tempi di build.

Domande da risolvere dentro questo punto:

- quale entrypoint Python diventa il target dell'eseguibile?
- quali file e cartelle vanno inclusi nel bundle?
- come riconoscere se l'app gira da sorgente o da eseguibile impacchettato?
- come rendere i log raggiungibili se l'eseguibile non si apre correttamente?
- come testare il launcher su path con spazi e su Windows 10/11?

### 5. Gestire Tesseract/OCR

Obiettivo:

- decidere se includere Tesseract nel pacchetto o richiederne installazione separata;
- controllare lingua italiana;
- fallire con messaggio chiaro se OCR non disponibile.

Rischio:

- e' una delle parti piu' fragili su PC esterni.

Domande da risolvere dentro questo punto:

- Tesseract incluso o prerequisito?
- se incluso, dove vive nel pacchetto?
- se prerequisito, come rilevarlo e come spiegare il problema all'utente?
- quali lingue OCR devono essere garantite nella beta?
- come verificare che `ita` e configurazioni OCR siano disponibili?
- come evitare path hardcoded verso Tesseract del PC di sviluppo?

### 6. Installer Windows

Obiettivo:

- creare installer o pacchetto consegnabile;
- installare launcher;
- creare icona;
- preservare dati utente.

Prima ipotesi:

- Inno Setup dopo aver stabilizzato launcher e cartelle.

Domande da risolvere dentro questo punto:

- installer classico o pacchetto zip consegnabile per la prima beta?
- come creare icona e collegamento desktop/start menu?
- come preservare AppData durante disinstallazione o aggiornamento?
- serve una procedura di backup prima di update?
- come versionare il pacchetto consegnato?
- come distinguere installazione nuova, reinstallazione e aggiornamento?

