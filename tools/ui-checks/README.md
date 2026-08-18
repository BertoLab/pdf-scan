# UI checks temporanei

Tooling di sviluppo per controllare visivamente modifiche UI.

Questi script non fanno parte dell'app finale e non devono entrare nel packaging.

## Setup

Da eseguire una volta:

```powershell
npm --prefix tools/ui-checks install
npm --prefix tools/ui-checks exec playwright install chromium
```

## Uso

Avviare prima l'app:

```powershell
.\.venv\Scripts\python.exe scripts\run_app.py --rebuild
```

Poi, in un altro terminale:

```powershell
npm --prefix tools/ui-checks run check -- --url http://127.0.0.1:8765 --scope "modifica descritta"
```

Per una modifica che tocca piu' superfici UI, passare la sequenza reale da aprire:

```powershell
npm --prefix tools/ui-checks run check -- --url http://127.0.0.1:8765 --scope "archivi-cronologia" --click "Archivi" --click "Registro fatture"
```

La sequenza va scelta ogni volta in base alla modifica fatta, non da una lista fissa.

Gli screenshot vengono creati in:

```text
tmp/ui-checks/
```

Per default vengono cancellati a fine controllo. Per tenerli temporaneamente mentre si analizza un problema:

```powershell
npm --prefix tools/ui-checks run check -- --url http://127.0.0.1:8765 --scope "modifica descritta" --keep
```

Pulizia manuale:

```powershell
npm --prefix tools/ui-checks run clean
```

## Regola di metodo

Non esiste una lista fissa di pagine importanti.

Ogni controllo deve coprire le superfici UI realmente toccate dalla modifica:

- pagina o pannello modificato;
- modale/menu aperto se la modifica riguarda stati interattivi;
- almeno due contesti reali se e' stato modificato un componente condiviso;
- viewport desktop e, quando pertinente, viewport stretto.
