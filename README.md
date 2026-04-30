# Estrattore Scan Fatture

Pipeline per processare fatture scannerizzate, estrarre campi stampati affidabili, individuare la data scritta a mano e generare un file Excel verificabile rapidamente da una persona.

## Contesto del problema

Il sistema lavora su fatture che nascono come documenti digitali, vengono stampate e poi scannerizzate. Ogni fattura contiene due tipi di informazione molto diversi:

1. testo stampato, regolare e relativamente stabile;
2. una data scritta a mano, irregolare e molto piu difficile da riconoscere automaticamente.

Questa distinzione e centrale per tutto il progetto: non trattiamo il documento come un unico problema OCR generico, ma separiamo cio che puo essere estratto in modo affidabile da cio che richiede verifica umana.

## Contenuto stampato

Il testo stampato e considerato la parte a bassa difficolta.

Caratteristiche attese:

- testo regolare, allineato e leggibile;
- documento originariamente digitale;
- struttura abbastanza stabile;
- presenza di circa 4-5 template principali;
- pochi campi chiave da estrarre.

Per questa parte l'obiettivo e usare OCR standard, regole deterministiche e validazioni forti. Se un campo stampato non viene estratto con affidabilita sufficiente, il documento puo essere scartato o marcato come non valido.

## Data scritta a mano

La data manoscritta e la parte ad alta difficolta.

Caratteristiche attese:

- e l'unico elemento manoscritto presente nella fattura;
- si trova in una zona approssimativamente simile, ma non fissa;
- puo essere inclinata, irregolare o poco leggibile;
- non e affidabile presumere un riconoscimento automatico corretto in V1.

Per questo motivo, in V1 il requisito principale non e leggere sempre la data, ma individuare correttamente la regione dell'immagine che la contiene e salvarla come crop.

## Obiettivo V1

Per ogni fattura il sistema deve:

1. estrarre in modo affidabile le informazioni stampate principali;
2. individuare la regione dell'immagine contenente la data scritta a mano;
3. salvare un crop dell'area della data;
4. tentare il riconoscimento automatico della data, solo quando possibile;
5. scrivere una riga in Excel con dati strutturati, eventuale data riconosciuta e immagine del crop.

L'Excel finale non e pensato come verita automatica assoluta, ma come strumento operativo per velocizzare la revisione umana.

## Decisioni progettuali

### Separazione dei problemi

Il sistema distingue esplicitamente:

- estrazione dei dati stampati: problema strutturato, validabile e ad alta affidabilita;
- data manoscritta: problema incerto, da supportare con crop e verifica umana.

Questa separazione evita di abbassare la qualita complessiva della pipeline a causa della componente piu incerta.

### Riduzione dei falsi positivi

La priorita e non produrre dati sbagliati con apparente sicurezza.

Regole guida:

- se un campo stampato non supera le validazioni, il documento va scartato o marcato;
- se la data manoscritta non e riconosciuta con confidenza sufficiente, il valore testuale resta vuoto o non affidabile;
- il crop della data resta comunque l'artefatto principale per la verifica.

### Centralita del crop

Per la data manoscritta, il risultato fondamentale e:

> individuare correttamente la regione contenente la data e salvarla come immagine.

Il riconoscimento automatico e utile, ma secondario. Un crop corretto permette all'utente di controllare la data direttamente nell'Excel senza riaprire il file originale.

### Revisione umana

Ogni riga dell'Excel deve contenere:

- campi stampati estratti automaticamente;
- eventuale data manoscritta riconosciuta;
- immagine del crop della data;
- stato di validazione o motivo di eventuale incertezza.

Il sistema serve ad automatizzare la parte affidabile e rendere rapida la revisione della parte incerta.

### Fallimenti espliciti

I documenti che non soddisfano i criteri minimi devono essere separati e tracciati.

Esempi di motivi di fallimento:

- template non riconosciuto;
- campi stampati obbligatori mancanti;
- OCR stampato sotto soglia;
- area data non individuata;
- qualita immagine insufficiente;
- errore tecnico durante il processamento.

Questi casi sono importanti: costituiscono la base per migliorare la pipeline nelle versioni successive.

## Sintesi operativa

Per ogni fattura:

1. caricare immagine o PDF scannerizzato;
2. normalizzare l'immagine se necessario;
3. riconoscere il template;
4. estrarre e validare i campi stampati;
5. individuare l'unica area manoscritta;
6. generare il crop della data;
7. tentare il riconoscimento automatico della data;
8. scrivere i risultati nell'Excel;
9. spostare o registrare il documento come processato o rifiutato.

## Output attesi

Output principali:

- file Excel con una riga per fattura;
- immagini crop delle date manoscritte;
- log di processo;
- elenco dei documenti rifiutati con motivo.

Campi tipici dell'Excel:

- identificativo documento;
- nome file originale;
- campi stampati estratti;
- data manoscritta riconosciuta, se disponibile;
- confidenza o stato della data;
- immagine crop della data;
- stato finale;
- motivo di fallimento, se presente.

## Filosofia del progetto

Il progetto non punta alla completezza immediata. La direzione corretta e:

- automatizzare in modo sicuro una parte significativa del lavoro;
- non inventare confidenza dove non esiste;
- rendere verificabile rapidamente la parte incerta;
- migliorare iterativamente partendo dai casi rifiutati e dagli errori osservati.

Una V1 solida deve essere conservativa, tracciabile e facile da correggere.

## Struttura del progetto

```text
estrattore-scan-fatture/
|-- src/estrattore/      # Codice principale della pipeline
|-- data/
|   |-- input/           # Fatture da processare
|   |-- processed/       # File gia processati
|   |-- rejected/        # File scartati o non gestibili
|   `-- crops/           # Crop delle date manoscritte
|-- outputs/
|   |-- batches/         # File Excel generati
|   `-- logs/            # Log e report di processo
|-- tests/               # Test automatici
|-- pyproject.toml       # Configurazione Python
`-- README.md            # Contesto e specifiche operative
```

## Avvio rapido

```bash
pip install -e .
python -m estrattore run data/input
```

## Principio di sviluppo

Ogni nuova funzione deve avere input e output chiari, fallimenti espliciti e test proporzionati al rischio.

Prima di aggiungere automazione complessa, preferire:

- validazioni semplici;
- soglie conservative;
- log leggibili;
- casi rifiutati ben spiegati;
- output facilmente verificabile da una persona.
