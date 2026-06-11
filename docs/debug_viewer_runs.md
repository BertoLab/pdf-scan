# Debug Viewer Runs

## Run Operativa Pulita

Rigenera i sample standard usati dal viewer. Prima cancella le vecchie cartelle
in `apps/viewer/public/sample`, poi crea solo le versioni operative.

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input
```

Comportamento OCR:

- prima lettura: `ita` + `fast`, pagina intera, senza crop;
- fallback qualita: `crop-only`;
- il modello `best` resta disponibile solo per confronti tecnici.

## Suite Comparativa Legata Al Fallback

Rigenera prima la versione senza fallback per ogni PDF. Solo se quella versione
risulta sotto soglia secondo il gate OCR, crea anche le varianti tecniche di
confronto.

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input --comparison-suite
```

Versioni sempre generate:

- `__no_fallback`: solo OCR iniziale `ita/fast`, senza fallback.

Versioni generate solo per i documenti sottosoglia:

- `__fallback_best_crop`: fallback `ita/best`, poi crop se serve;
- `__fallback_crop_only`: fallback con crop `ita/fast`, senza `best`.

Questa modalita serve solo per confronto tecnico nel viewer, ma resta legata al
gate reale: niente copie per fatture che non entrano in fallback.

## Opzioni Utili

Non cancellare i sample esistenti:

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input --no-clean-sample
```

Forzare una modalita fallback specifica:

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input --quality-fallback-mode crop-only
```

Testare `best` per confronto futuro:

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input --quality-fallback-mode best-crop
```

Disabilitare il fallback:

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input --disable-quality-fallback
```

Confrontare un'altra lingua OCR:

```powershell
.\.venv\Scripts\python.exe scripts\debug_words_batch.py data\input --ocr-lang ita+eng --output-suffix __ita_eng
```
