# Gestione possibilita anagrafica

## Stati dei campi

- Campo normale: valore estratto o inserito manualmente, senza evidenza speciale.
- Campo celeste: valore compilato usando una riga dell'anagrafica clienti.
- Campo grigio/chiaro: valore compilato da suggerimento selezionato dall'utente.
- Campo giallo: l'anagrafica contiene piu righe compatibili con lo stesso codice fiscale e il sistema richiede una scelta.

## Suggerimenti

- I suggerimenti dei campi anagrafici devono nascere solo dall'anagrafica clienti caricata.
- I valori gia presenti nelle fatture non alimentano i suggerimenti.
- Per i condomini si usano le righe anagrafica con tipologia condominio.
- Per gli emittenti si usano le righe anagrafica non condominio.

## Match diretto

- Se viene selezionato un codice fiscale condominio con una sola riga anagrafica compatibile, il sistema compila anche il campo condominio.
- Se viene selezionato un codice fiscale emittente con una sola riga anagrafica compatibile, il sistema compila anche nome emittente e partita IVA.
- I campi compilati da anagrafica diventano celesti.

## Piu possibilita

- Se lo stesso codice fiscale e collegato a piu righe anagrafica con riempimenti diversi, il sistema non sceglie automaticamente.
- Il campo anagrafico collegato viene svuotato.
- Il campo diventa giallo e mostra un piccolo punto esclamativo.
- Cliccando il campo o il punto esclamativo si vedono le righe anagrafica possibili.
- La scelta di una possibilita compila i campi collegati e li porta allo stato celeste.

## Regola prudenziale

- Quando piu righe anagrafica sono compatibili, il sistema deve preferire la scelta manuale rispetto alla deduzione automatica.
- L'automatismo e ammesso solo nei casi in cui esiste una singola riga compatibile oppure una regola di cleaning gia approvata.
