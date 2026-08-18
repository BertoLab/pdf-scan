# Rules Workflow per Codex

Questo documento contiene le regole operative stabili del progetto.

Deve essere letto prima di lavori non banali. Non e' un diario storico: il diario e' `docs/notebook.md`.

## Ruolo

Codex deve lavorare come head engineer pragmatico:

- leggere il codice prima di decidere;
- spiegare le scelte tecniche importanti;
- preferire soluzioni pulite, aggiornabili e coerenti con il prodotto finale;
- evitare scorciatoie presentate come architettura;
- insegnare il ragionamento professionale senza appesantire inutilmente la chat.

## Separazione tra regole e notebook

Struttura documentale:

```text
AGENTS.md
  -> indice breve e autoritativo

.agents/rules_workflow.md
  -> regole operative stabili

docs/notebook.md
  -> taccuino progettuale, storico decisioni, punti, dubbi, verifiche
```

Regola:

- se una cosa e' un principio stabile, va qui;
- se una cosa e' una decisione di progetto, un tentativo, un dubbio o una chiusura di punto, va nel notebook.

## Flusso per punti progettuali

Per lavori strutturali, packaging, runtime, input/output, salvataggi, workflow utente, code OCR, archivi o refactor importanti, seguire il flusso completo.

### 1. Definizione teorica del problema

Formulare il problema nel modo piu' generale possibile prima di scegliere una soluzione.

Evitare formulazioni troppo locali tipo:

```text
spostare questa tabella
```

Preferire:

```text
separare dati operativi, dati archiviati e viste di supervisione in una app gestionale desktop
```

Obiettivo: far emergere problemi fratelli nascosti.

### 2. Teoria risolutiva e best practice

Descrivere:

- come il problema viene risolto normalmente in app moderne;
- quali workflow sono robusti;
- quali errori evitare;
- quali tradeoff contano;
- cosa e' standard industriale e cosa e' scelta nostra.

Se una proposta e' quasi novelty o non standard:

- segnalarlo chiaramente in chat;
- scrivere nel notebook critiche, difese e criteri per abbandonarla.

### 3. Approccio tecnico scelto

Classificare la scelta:

- **scelta finale**: adatta anche al prodotto distribuito;
- **scelta incrementale sana**: incompleta ma cresce nella direzione finale;
- **scelta provvisoria rischiosa**: sblocca una prova ma va rimossa.

Default:

- preferire scelta finale o incrementale sana;
- non usare "beta" come scusa per debito architetturale;
- fermarsi e avvisare se l'ambiente attuale forza una soluzione peggiore.

### 4. Micro-step

Prima di scrivere codice, dividere il punto in micro-step:

- cosa cambia;
- perche' cambia;
- quali file/aree tocca;
- come verra' verificato.

Aggiornare il notebook se il piano cambia durante il lavoro.

### 5. Implementazione

Il codice deve essere:

- pulito;
- testabile;
- poco dipendente dall'ambiente;
- compatibile con Windows 10 e 11;
- coerente con una app desktop seria;
- semplice da aggiornare;
- senza path hardcoded del PC;
- senza duplicazioni inutili.

Preferire astrazioni piccole ma reali per:

- path runtime;
- accesso file utente;
- log;
- launcher;
- import/export;
- tabelle riusabili;
- workflow di salvataggio.

### 6. Verifica e occhi sul risultato

"Test" non significa solo pytest.

Usare gli occhi adatti al rischio:

- build frontend;
- typecheck;
- test Python;
- script diagnostici;
- screenshot;
- controllo file creati;
- dry run;
- verifica manuale mirata;
- confronto prima/dopo.

### 7. Revisione critica

Prima di chiudere un punto chiedersi:

- il codice e' piu' semplice o piu' fragile?
- abbiamo introdotto accoppiamenti inutili?
- l'utente vede meno complessita'?
- l'aggiornamento futuro e' piu' facile?
- i dati utente sono protetti?
- i log/errori aiutano davvero?
- c'e' qualcosa da rifare ora invece di accumulare debito?

### 8. Chiusura

A fine punto scrivere nel notebook:

- soluzione adottata;
- perche' e' soddisfacente;
- verifiche eseguite;
- limiti residui;
- prossimi punti consigliati.

Nel messaggio finale linkare il punto del notebook da cui iniziare a leggere.

## Regole desktop e dati utente

Principio architetturale principale:

```text
codice applicazione != dati utente
```

Il codice puo' essere sostituito a ogni aggiornamento.
I dati utente devono vivere fuori dalla repo e non devono essere persi durante rebuild, aggiornamenti o reinstallazioni.

Runtime atteso:

```text
%LOCALAPPDATA%\EstrattoreScanFatture\
```

Regola di sicurezza primaria:

- l'app non deve mai modificare o cancellare file fuori dal proprio storage interno;
- quando l'utente seleziona PDF, cartelle o Excel, l'app puo' leggerli e copiarli;
- ogni cancellazione deve riguardare solo copie interne create dall'app.

## Packaging direction

Non riscrivere l'app come desktop app nativa.

Approccio scelto:

```text
launcher Windows
  -> avvia backend Python locale
  -> serve frontend React buildato
  -> apre finestra app controllata dal launcher
  -> salva dati in cartelle utente persistenti
```

Il browser esterno resta modalita' sviluppo/debug.
La versione finale deve governare ciclo di vita di finestra, backend e lavorazioni OCR.

## Regole UI e design

Per UI nuove o rifatte:

- seguire lo stile di `mystudio-platform` quando pertinente;
- evitare duplicazione di informazioni nella stessa vista;
- separare navigazione, supervisione processi, archivio documentale e registro dati;
- non mettere funzioni operative in pagine storiche se appartengono a workflow attivi;
- tabelle gestionali grandi devono usare infrastruttura coerente e virtualizzata;
- label e testi devono essere italiani e orientati all'utente, non tecnici.

## Protocollo controlli UI

Dopo ogni modifica UI non banale, classificare il rischio:

- **basso**: testi, label, piccole classi CSS isolate;
- **medio**: layout di pagina, sidebar, modali, tabelle, bottoni con stato;
- **alto**: workflow interattivi, salvataggi, cancellazioni, import, OCR, navigazione tra pagine.

Verifiche minime:

- rischio basso: `npm --prefix apps/viewer run build`;
- rischio medio: build + controllo visuale/Playwright delle superfici UI toccate dalla modifica, quando disponibile;
- rischio alto: build + click-path Playwright sulle superfici UI e sui flussi toccati dalla modifica, quando disponibile + test backend/frontend pertinenti.

## Uso di Playwright

Playwright e' tooling di sviluppo, non dipendenza runtime.

Quando verra' installato, deve vivere fuori dall'app finale, preferibilmente in:

```text
tools/ui-checks/
```

Comando standard previsto:

```powershell
npm --prefix tools/ui-checks run check -- --url http://127.0.0.1:8765 --scope "<descrizione-modifica>"
```

Gli screenshot generati devono essere temporanei:

```text
tmp/ui-checks/
```

Non devono entrare nel packaging finale, non devono restare come artefatti permanenti e vanno cancellati dopo la valutazione, salvo richiesta esplicita dell'utente di conservarli.

Regola fondamentale:

- gli screenshot non devono seguire una lista fissa di "pagine importanti";
- devono coprire tutte e solo le pagine, modali, menu, stati e flussi potenzialmente toccati dalla modifica appena fatta;
- se una modifica cambia un componente condiviso, bisogna fotografare almeno due contesti reali in cui quel componente appare;
- se una modifica cambia uno stato interattivo, bisogna fotografare anche lo stato aperto/attivo, non solo la pagina ferma;
- dopo averli usati per valutare layout, sovrapposizioni, leggibilita', scroll e coerenza visiva, Codex deve cancellare gli screenshot temporanei.

Ordine preferito dei dati per test UI:

1. runtime di test o sample controllati;
2. app reale solo per navigazione e screenshot;
3. app reale con azioni di scrittura solo se richiesto esplicitamente.

Codex non deve usare Playwright per confermare azioni distruttive su dati reali senza autorizzazione esplicita.

Quando usa Playwright deve riportare:

- comando eseguito;
- viste controllate;
- screenshot temporanei prodotti e poi cancellati;
- problemi trovati;
- limiti del controllo.

Se Playwright non e' disponibile, dirlo esplicitamente e non fingere un controllo visuale reale.

## Regole di modifica codice

- Usare `rg` per cercare.
- Usare `apply_patch` per modifiche manuali ai file.
- Non cancellare o revertire modifiche utente non richieste.
- Non fare refactor laterali se non servono al punto.
- Prima di editare file importanti, spiegare brevemente cosa si sta per cambiare.
- Verificare con comandi proporzionati al rischio.
