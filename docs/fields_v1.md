# Campi Da Estrarre - V1

Questo documento definisce i campi che la prima versione deve provare a estrarre dalle fatture scannerizzate.

La regola generale resta conservativa: se un campo non e supportato da evidenze chiare, deve essere `missing`, `uncertain` o `conflict`, non inventato.

## Campi Prioritari

| Campo interno | Descrizione | Esempi anchor | Formato atteso | Note |
| --- | --- | --- | --- | --- |
| `invoice_number` | Numero fattura | `FATTURA`, `N.`, `Numero` | testo/numero, es. `1166/2024` | Di solito in testata. |
| `p_iva` | Partita IVA | `P.I.`, `P.IVA`, `PIVA`, `PVA`, `COD. FISCALE / P.IVA`, `Partita IVA` | 11 cifre, opzionale prefisso `IT` | `PVA` e una variante OCR osservata di `P.IVA`; attenzione a non confonderla con codice fiscale. |
| `codice_fiscale` | Codice fiscale | `CODICE FISCALE`, `C.F.`, `COD. FISCALE / P.IVA` | 11 cifre o codice fiscale alfanumerico | Per condomini spesso numerico. L'anchor mista puo indicare uno dei due campi. |
| `customer_name` | Nome condominio/intestatario | `INTESTATARIO FATTURA`, `CONDOMINIO` | testo | Campo descrittivo, piu soggetto a varianti OCR. |
| `taxable_amount` | Imponibile | `IMPONIBILE`, `TOTALE IMPONIBILE` | importo italiano, es. `105,00` | Da distinguere da totale fattura e netto. |
| `withholding_amount` | Ritenuta | `RITENUTA` | importo italiano, spesso negativo, es. `-4,20` | La percentuale puo essere letta male; il valore importo e prioritario. |
| `net_total_amount` | Totale da pagare dopo ritenuta | `TOTALE NETTO FATTURA`, `SCADENZARIO` | importo italiano, es. `123,90` | Corrisponde a `totale imponibile/totale fattura - ritenuta` secondo il layout. |

## Relazioni Da Verificare

Quando disponibili, i valori monetari devono essere controllati tra loro.

Esempio osservato:

```text
TOTALE FATTURA EURO.          128,10
RITENUTA 4%                    -4,20
TOTALE NETTO FATTURA EURO      123,90
```

Relazione attesa:

```text
net_total_amount = gross_total_amount + withholding_amount
```

Nota: se la ritenuta e scritta come valore negativo, si somma il valore firmato. Se viene normalizzata come valore assoluto, la relazione diventa sottrazione. La V1 deve scegliere una convenzione e mantenerla esplicita.

## Campi Da Chiarire

Questi campi sono emersi ma richiedono una definizione piu precisa prima dell'implementazione:

| Campo possibile | Dubbio |
| --- | --- |
| `gross_total_amount` | Il totale fattura prima della ritenuta va estratto separatamente? |
| `total_taxable_amount` | Differenza esatta rispetto a `taxable_amount`: imponibile singolo o totale imponibile riepilogativo? |
| `withholding_rate` | Serve estrarre anche la percentuale della ritenuta, es. `4%`, o basta l'importo? |
| `supplier_name` | Serve il nome fornitore o basta P.IVA/identificativo? |
| `invoice_date` | Non ancora richiesto esplicitamente, ma probabilmente utile. |

## Strategia Di Estrazione

La V1 deve lavorare sugli span, non su coordinate assolute.

Approccio:

1. cercare anchor testuali negli span;
2. cercare candidati vicini nello stesso span, nella stessa riga o nelle righe immediatamente sotto;
3. validare il formato del candidato;
4. assegnare uno score semplice basato su anchor, distanza, formato e unicita;
5. produrre `InvoiceField` con evidenze testuali e bounding box;
6. marcare `conflict` se ci sono candidati simili e non risolvibili.

## Stati Attesi

Ogni campo deve terminare in uno stato esplicito:

- `accepted`: candidato unico, formato valido, evidenza forte;
- `missing`: nessun candidato plausibile;
- `uncertain`: candidato presente ma evidenza debole o OCR sospetto;
- `conflict`: piu candidati plausibili in competizione.

## Note OCR

Gli errori OCR non devono essere corretti globalmente senza contesto.

Esempio:

```text
RITENUTA 4%
```

puo diventare:

```text
RITENUTA 454
```

Questa correzione va gestita solo dentro regole contestuali per `withholding_rate` o `withholding_amount`, conservando sempre l'evidenza raw.
