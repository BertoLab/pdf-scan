# Domande per Giorgio

## 1. Pagamento unico per piu fatture

Abbiamo un caso in cui due fatture restano correttamente separate per numero e data, ma sembrano pagate con un solo bonifico.

Esempio osservato:

- fattura `3FC24014301`: non ha una data pagamento diretta nella sua sezione;
- fattura `3FO24061716`: ha una pagina di pagamento;
- nella causale/testo del pagamento compaiono entrambi i numeri fattura;
- il pagamento sembra riferirsi a entrambe.

Domanda:

Quando un bonifico cita piu numeri fattura, dobbiamo assegnare la stessa data pagamento a tutte le fatture citate, mantenendo comunque le fatture separate?

Ipotesi tecnica proposta:

- mantenere le fatture separate;
- creare un evento pagamento a livello documento;
- collegare l'evento pagamento a una o piu fatture tramite i numeri fattura trovati nella causale;
- usare l'importo del bonifico e la somma degli importi delle fatture solo come conferma aggiuntiva, non come unico criterio.

Da confermare:

- se questa interpretazione e corretta dal punto di vista operativo;
- se nel file finale ogni fattura deve mostrare la stessa `pagata_il`;
- se serve indicare che il pagamento e condiviso con altre fatture;
- se l'importo del pagamento va riportato come importo unico del bonifico o duplicato sulle singole fatture.

