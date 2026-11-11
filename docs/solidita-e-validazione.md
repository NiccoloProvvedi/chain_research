# Solidità e validazione dei risultati

Revisione dell'11/11/2026 (ramo `revisione-correzioni-e-test`). La revisione precedente ha sistemato ciclo di vita,
sicurezza, impostazioni e test. Questo documento riguarda lo strato successivo: che cosa lo strumento *dichiara* e che
cosa ha davvero *controllato*. Il punto centrale è la **validazione dei risultati**: una volta trovato un dato, essere
sicuri che sia valido e giusto.

**Prove usate:** il codice (`web/catena.py`, `web/raccolta.py`, `banco/giudice.py`, `banco/banco.py`,
`banco/fedelta.py`, `prove/triple.py`, le istruzioni in `modello/prompt/`) e i dati delle corse: domande di controllo
corsa 1 (36 domande, completa) e corsa 2 (14 domande, interrotta; la corsa 3 non è mai partita), le 40 domande
(`dopo-revisione-2`).

Sigle: **D** = visto nei dati delle corse, **R** = visto leggendo il codice, **I** = ipotesi da controllare.

---

## 1. In breve: la validazione è più debole di quanto dicevano i numeri

| Misura | 40 domande regolate | Domande di controllo (mai viste) | Obiettivo |
|---|---|---|---|
| Risposte giuste | 34/40 = 85% | 25/36 = 69% (95%: 53–82%) | limite inferiore ≥ 70% |
| Risposte «confermate» giuste | 13/13 | corsa 1: 10/12 = 83%; corsa 2: 4/7 | ≥ 95% |
| Stesso valore ripetendo | — | 11/14 = 79% | ≥ 85% |
| Stesso «confermata» ripetendo | — | **8/14 = 57%** | ≥ 85% |

Con il metro corretto (fase A, fatta): corsa 1 di controllo 25/35 = 71% (95%: 55–84%), una da giudicare; corsa 2
10/12. Sulle due corse insieme le «confermate» sono giuste 13/17 = 76%, le non confermate 22/30 = 73%: **il sigillo
oggi non dice niente.**

1. **«Confermata» non è un sigillo affidabile.** Perfetta sulle domande regolate, 83% e 57% su quelle nuove; ripetendo
   la corsa cambia quasi una volta su due. Oggi vuol dire solo «nessun anello della strada vincente è segnato debole»
   (`raccolta.py:159`).
2. **Lo strumento di misura sbaglia in tutte e due le direzioni** (§3). La percentuale vera sulle domande di controllo
   non si conosce, a meno di circa ±4 domande, finché giudice e risposte attese non sono sistemati.
3. Quindi due lavori, in quest'ordine: raddrizzare il metro, poi costruire i controlli veri.

---

## 2. Che cosa è controllato oggi e che cosa no

**Controllato dal programma (da tenere):**
- Controlli di ogni anello: due letture indipendenti, «sì» solo se concordano (`_esiti`, `_unisci_esiti`).
- Le parole citate stanno davvero nella riga; la riga è del candidato; l'anno preciso del controllo è nella citazione
  (`_applica_esiti`, `triple.citazione_presente`).
- Tipo e intervallo del valore (`valore_ammesso`); la riga del valore parla dell'entità finale (`_valore_entita`).
- Più strade allo stesso valore («confermato da N strade»).

**Non controllato:**

| # | Lacuna | Prova | |
|---|---|---|---|
| V1 | **Il valore non è mai confrontato con la sua riga.** Il modello dà valore e numero di riga; il programma controlla che la riga parli dell'entità, non che contenga il valore. | `catena.py`, `_valore_entita` | R |
| V2 | **Il valore si legge una volta sola.** Gli anelli hanno due letture, il valore finale (la risposta) una. | `_valore_entita` | R |
| V3 | **Nessun confronto del valore tra fonti.** Basta una riga di una pagina. Aida: en.wiki 21 giugno 1886, attesa 1885. 30 valori su 35 vengono da una sola voce di Wikipedia. | controllo 1 | D |
| V4 | **Un estratto dei risultati di ricerca vale come prova forte di un anello.** PostgreSQL: il riquadro di Wikipedia («Newburyport, Massachusetts») scartato, l'estratto di una pagina mai letta («born in Milton, New Hampshire») a 0,9 «forte», catena «confermata», risposta sbagliata. Le due corse hanno dato due città sbagliate diverse. | controllo 1 e 2 | D |
| V5 | **Il disaccordo tra fonti si perde.** Un candidato scartato che aveva una fonte forte non lascia traccia: «Fonti in disaccordo: nessuno». | stesso caso | D |
| V6 | **Nessun controllo di cicli e di verso sulla strada.** C++ corsa 2: Stroustrup → Wheeler → … → Burrows → **di nuovo Wheeler** (relazione percorsa al contrario: allievo invece di relatore), «forte» 0,88, «confermata», sbagliata. | controllo 2 | D |
| V7 | **Nessun controllo della risposta contro tutta la domanda.** C++ corsa 1: un passo in meno («fisico britannico» mai controllato sull'entità finale). Ducato di Savoia: strada «Ducato di Savoia → Prima Repubblica francese → Primo Impero → Regno d'Olanda → Roma» detta «catena completa»; valore giusto per caso. | controllo 1 | D |
| V8 | **Anelli deboli dentro una catena «completa», e chi scrive deve rispondere comunque.** 14 risposte non confermate giuste su 24, 9 sbagliate: una moneta 60/40 mostrata come risposta. | `scrivi`, dati | D |
| V9 | **La «Fiducia» è testo libero del modello che scrive.** Non dipende dai controlli. Risposte sbagliate con «alta» (Reggio Emilia) e «media» (4). Maiuscole diverse, a volte assente. La regia e le sezioni la usano come una misura. | dati, `indagine.in_breve` | D |
| V10 | **Segno, unità e significato del valore non normalizzati.** −2 m e 2 m; data di istituzione o di entrata in vigore; calendario giuliano o gregoriano (16 e 27 maggio 1703). | dati | D |
| V11 | **Documento:** `fedelta.py` confronta la frase con le *risposte*, mai con la *pagina*. Le risposte non confermate entrano nelle sezioni con un solo avviso. Nessun controllo che i fatti della sintesi siano confermati. | `fedelta.py`, `documento_sezione.md` | R |
| V12 | **Dati vecchi:** il valore non ha data («i passaggi non dicono di quando è») e non si rilegge. | righe «Limiti» | D |
| V13 | **«Non trovata» non è verificata:** può essere il limite di sessione, una pagina bloccata o una vera assenza; oggi si distingue solo il limite. | `raccolta.py` | R |

---

## 3. Il metro è storto (banco e giudice)

| # | Problema | Prova | |
|---|---|---|---|
| M1 | **Il confronto normalizzato ignora il segno.** «2 m» è dentro «−2 m s.l.m.» → contata giusta. | `giudice.uguale_normalizzata`, domanda Python nelle due corse | D |
| M2 | **Il banco fa estrarre la risposta a un modello invece di usare il valore che il programma ha già.** `esito["valore"]` esiste; `banco.py` chiede a Haiku di ricavarlo dalla riga «Risposta». Estrazione vuota = «vuota» = sbagliata. Due domande con il valore atteso esatto contate sbagliate (Ducato di Savoia corsa 1, Presa di Roma corsa 2). | dati | D |
| M3 | **Gli errori del giudice spariscono.** `giudice._chiedi` trasforma ogni `ErroreClaude` (anche il limite di sessione) in "", cioè risposta sbagliata. | `giudice.py` | R |
| M4 | **Le risposte attese di Wikidata non sono la verità.** Almeno 5 su 36 non coincidono con Wikipedia e vanno guardate da una persona: Reggio Emilia 68 e 56 m, Aida 1885 e 1886, Regno Unito 1921 e 1801, Gorizia 2020 e 2019, Amsterdam 2 e −2 m; in più la premessa della domanda PostgreSQL (stato di nascita). | dati | D/I |
| M5 | **Le durate contano le attese del limite di sessione** (145, 152, 159, 160 minuti): «19,9 minuti medi» è falso. | `raccolta.py`: `secondi` usa l'orologio grezzo | D |
| M6 | **Il registro non ha colonne di qualità:** niente precisione delle confermate, niente quota decisa dal giudice. | `banco.py` | R |
| M7 | Domande generate con due letture difendibili («stato insulare… fondazione»); nessun campo le segna. | dati | D |

Finché M1–M4 non sono corretti, nessuna percentuale va citata.

---

## 4. Il disegno: sette controlli tra «trovato» e «mostrato come fatto»

Principio: **il programma decide, il modello propone.** Ogni controllo è del programma dove si può; dove serve un
modello, è una seconda chiamata indipendente che prova a smentire, non quella che ha proposto.

```
valore proposto
  G1 valore nella riga      numeri, data o nome del valore scritti nella riga citata          (programma)
  G2 seconda lettura        una seconda chiamata deve dare lo stesso valore normalizzato      (modello ×2)
  G3 strada integra         nessuna entità due volte, passi = piano, verso della relazione    (programma)
  G4 qualità della fonte    ogni anello e il valore provati da una pagina letta, livello ≥ B  (programma)
  G5 riscontro              valore in ≥ 2 fonti indipendenti, o livello A e nessuna contraria (programma + rete)
  G6 revisione contraria    un altro modello vede domanda, strada e righe: «quale indizio non torna?»
  G7 registro dei disaccordi ogni smentita da fonte forte registrata e mostrata               (programma)
→ stato e punteggio calcolati dal programma
```

- **G1 Valore nella riga** (`_valore_entita`). Valore e riga normalizzati (cifre, mesi con `MESI`, segno, virgola
  decimale). Date: giorno, mese e anno nella riga. Numeri: stesse cifre con il segno. Nomi: `citazione_presente`.
  Se manca: valore rifiutato, in `storia` «valore non nella riga». Il controllo meno caro e più utile.
- **G2 Seconda lettura del valore.** Come `_esiti`: due letture, unione in ordine fisso. Se discordano si passa al
  tentativo successivo; se restano divise, tutte e due nel registro dei disaccordi e stato «contestata».
- **G3 Strada integra.** In `_percorri_frontiera`: un figlio con la stessa chiave (`_chiave_canonica`,
  `_chiave_entita`) di un antenato è scartato. Alla soluzione: lunghezza della strada = passi del piano + 1. Il piano
  dice il `verso` di ogni passo e la riga del legame deve rispettarlo (relatore *di*, non allievo *di*).
- **G4 Qualità della fonte.** Nuovo modulo `prove/fonte.py`: A = voce o riquadro di Wikipedia, sito ufficiale o
  istituzionale, sito dell'entità; B = pagina letta di un altro sito; C = estratto dei risultati, pagina non letta,
  aggregatore. Una prova C può proporre un candidato, non può rendere «forte» un anello né portare un valore.
- **G5 Riscontro.** Dopo G1–G2 lo stesso dato si cerca (a) nella voce di Wikipedia in un'altra lingua (collegamenti
  tra lingue, già in `WikipediaGrafo`), (b) nelle pagine già lette, (c) in al massimo 2 pagine nuove. Esito:
  `concordi`, `sola fonte`, `discordi`. A scelta (impostazione, spenta): l'affermazione di Wikidata sull'entità finale.
- **G6 Revisione contraria.** Una chiamata con un modello diverso da quello della catena (catena Haiku → revisione
  Sonnet). Riceve la domanda, la strada con una riga per anello e la riga del valore. Per ogni indizio della domanda:
  `{indizio, soddisfatto: sì / no / non provato, riga}`. Decide il programma: un «no» con riga e citazione valide →
  strada «smentita», la catena prova il candidato successivo. Avrebbe fermato C++ (l'entità finale non è un fisico) e
  Ducato di Savoia.
- **G7 Registro dei disaccordi.** `Catena.conflitti`: ogni candidato o valore scartato che aveva una prova di livello
  A o B, con la fonte. La riga «Fonti in disaccordo» la scrive il programma.

### Stato e punteggio

Al posto di `confermata` (sì/no) e della «Fiducia» libera:

| Stato | Regola | Mostrato come |
|---|---|---|
| `verificata` | G1–G4 passati, G5 concordi, G6 senza obiezioni, nessun anello debole | fatto |
| `sostenuta` | G1–G4 passati, G5 sola fonte (livello A), G6 senza obiezioni | fatto, «una sola fonte» |
| `contestata` | G5 discordi o registro non vuoto | le due versioni |
| `incerta` | un anello debole, o G6 «non provato» su un indizio | «non confermata», mai nella sintesi |
| `non trovata` | nessun valore, con la causa: assente, limite, pagina bloccata | lacuna |

Il `punteggio` (0–1) viene dagli esiti dei controlli; i pesi si regolano **solo** su un insieme di taratura, mai sulle
domande di controllo. La riga «Fiducia» la scrive il programma dallo stato. Regia e documento ricevono lo stato.

### Documento

- Le sezioni danno come fatto solo le risposte `verificata` e `sostenuta`; `contestata` con le due versioni; `incerta`
  in un riquadro «punti aperti». Lo impone `documento.py` passando le risposte divise per stato.
- Copertina: quante risposte per stato.
- `fedelta.py`, secondo modo: frase → **riga della fonte** (testo della prova salvato), non solo → risposta.
- Ogni valore porta «letto il <data>» e, se la riga la dà, la data del dato.

---

## 5. Altri difetti e miglioramenti

| # | Dove | Problema | |
|---|---|---|---|
| B1 | `banco/giudice.py` | M1: il segno si perde in `normalizza`. | D |
| B2 | `banco/banco.py` | M2: usare `esito["valore"]`; l'estrazione solo se manca. | D |
| B3 | `banco/giudice.py` | M3: il limite di sessione si propaga; gli altri errori sono «giudice in errore», fuori dal conto. | R |
| B4 | `web/raccolta.py` | `secondi` senza le attese del limite (`_trascorsi`). | D |
| B5 | `catena._applica_esiti` | Il candidato è abbinato alla risposta del modello per chiave del nome contenuta: due candidati con lo stesso cognome possono scambiarsi gli esiti. | R/I |
| B6 | `catena._unisci_esiti` | Controlli riconosciuti dai primi 50 caratteri: due controlli che cominciano uguale si fondono. | R/I |
| B7 | `catena._percorri_frontiera` | «Confermato da N strade» conta strade con la stessa entità finale e la stessa pagina. Vanno contate le fonti diverse. | R |
| B8 | `catena.percorri` | La catena migliore si sceglie per anelli deboli e qualità; il riscontro non conta. | R |
| B9 | istruzioni di scrittura | «Fiducia» con maiuscole diverse o assente. Sparisce quando la scrive il programma. | D |
| B10 | banco | La corsa 3 di controllo non è partita: il ciclo è morto con la sessione. | D |
| O1 | costo e tempo | Domande di controllo con più di 20 pagine lette: 9 su 36, 4 sbagliate. Una ricerca lunga è un segnale: entra nel punteggio. | D |
| O2 | `catena.py` | 2.015 righe. I controlli vanno in `web/validazione.py`: primo passo della divisione. | R |
| O3 | rigioco | G2, G5 e G6 aggiungono chiamate: le 39 catene vanno registrate di nuovo una volta (circa 6 $). | R |

Ancora aperti da prima: la causa del rigioco di Blender; `test_contratti.py` e la CI mai eseguiti; codice morto (`Bm25`,
`_percorri_piano`, metodi di `Concetti`); l'e-mail dell'account Claude a Wikipedia attiva in partenza.

---

## 6. Ordine dei lavori

**Fase A — raddrizzare il metro (senza spesa):** B1–B4 e M6 con i test prima; nuovo giudizio delle corse salvate
(`banco/rigiudica.py`); scheda delle risposte attese (`banco/verifica_riferimenti.py`) con il campo
`stato_riferimento` nelle domande di controllo (`ok`, `contestata`, `sbagliata`, `ambigua`): si conta solo su `ok`.

**Fase B — controlli del programma (gratis, protetti dal rigioco):** G1, G3, G4, G7, poi B5–B7. Ogni controllo ha il
suo test, scritto prima, costruito sui casi veri delle domande di controllo.

**Fase C — controlli con il modello (il costo sale, misurato):** G2, G5, G6; stato e punteggio al posto di
`confermata` e «Fiducia» in `raccolta.py`, `indagine.py`, `documento.py` e nell'interfaccia.

**Fase D — documento e misura:** regole del documento per stato, `fedelta.py` sulla riga della fonte, nuova
registrazione del rigioco, campagna di prova (§7).

Regola: un controllo resta solo se sulle domande di controllo la precisione delle `verificata` sale e le risposte
giuste non scendono oltre il rumore (McNemar, `banco/statistiche.py`).

---

## 7. Campagna di prova

| Giro | Che cosa | Criterio | Costo |
|---|---|---|---|
| 0 Fermo immagine | commit, modelli esatti, impostazioni di ogni corsa | — | 0 |
| 1 Integrità | test senza rete, rigioco, ruff, copertura ≥ 80% | tutto verde | 0 |
| 2 Metro | test del giudice; nuovo giudizio delle corse; scheda delle risposte attese; 50 verdetti del giudice riletti a mano | errori del giudice ≤ 2%; ogni risposta attesa `ok` o esclusa | centesimi e tempo di una persona |
| 3 Base | domande di controllo ×3 con il metro corretto; risposte a memoria (haiku, sonnet, opus) | solo numeri | circa 14 $ |
| 4 Taratura | insieme B (36 domande, altri semi) per i pesi del punteggio; 20 domande senza risposta | risposte inventate ≤ 10% | circa 5 $ |
| 5 Controlli A/B | ogni controllo acceso e spento sulle domande di controllo, a coppie | `verificata` giuste ≥ 95% (limite inferiore ≥ 85%); giuste non in calo | circa 5 $ a controllo |
| 6 Stabilità | stessa domanda ×3 | stesso valore ≥ 85%; stesso stato ≥ 85% | nei dati dei giri 3 e 5 |
| 7 Fonti | 150 prove di anelli e valori rilette dal vivo | ≥ 90% sostenute | centesimi |
| 8 Documenti | 6 indagini complete; `fedelta.py` nei due modi; fatti chiave ritrovati, a mano | frasi ≥ 95%, nessun fatto chiave inventato, ritrovati ≥ 80% | circa 30 $ |
| 9 Conferma | codice finale su un terzo insieme mai visto, una volta | limite inferiore ≥ 70%; `verificata` ≥ 95% | circa 5 $ |

Le domande di controllo servono nei giri 3 e 5 per *accettare o rifiutare* un controllo, mai per scrivere regole su
una domanda. Dopo il giro 5 sono consumate: il giro 9 vuole un insieme nuovo.

## 8. Piano dei test dei controlli

Ogni controllo passa da sei livelli, dal meno caro al più caro. Un controllo entra nel codice dopo i livelli 1–4
(gratis); resta nel codice solo se passa il 5 e il 6.

| Livello | Che cosa prova | Come | Criterio | Stato all'11/11 |
|---|---|---|---|---|
| 1 Tabelle | la regola, caso per caso | `test/test_validazione.py`: valori e righe scritti a mano, giusti e sbagliati | tutti | fatto per G1, G3, G4, G7 |
| 2 A tappeto | la regola su centinaia di casi generati | ogni data (192) in 6 lingue e in forma numerica si ritrova; la stessa data con giorno, mese o anno cambiato di uno è rifiutata; ogni numero cambiato è rifiutato | 0 errori | fatto per G1 (2.300 date giuste, 4.600 cambiate) |
| 3 Casi veri | i difetti visti nelle corse | un test per caso, con le righe vere: PostgreSQL (estratto contro riquadro), Aida (1885 e 1886), C++ (ritorno su Wheeler), Python (−2 e 2 m), Ducato di Savoia e Presa di Roma (valore giusto contato sbagliato) | tutti | fatto |
| 4 Rigioco | il controllo non cambia le catene che non deve cambiare | le 39 catene registrate, senza rete né Claude: ogni catena che cambia va guardata e spiegata | ogni differenza spiegata e scritta in `da_registrare.txt` | 37 uguali, 2 cambiate apposta (sotto) |
| 5 Corse salvate | quante risposte tocca, giuste e sbagliate | il controllo ricalcolato sugli esiti salvati (`esiti_rigiudicati.json`) | toglie il sigillo a più sbagliate che giuste | G4: 1 sbagliata e 1 giusta su 87 risposte |
| 6 Dal vivo, a coppie | l'effetto vero | stesse domande di controllo con il controllo acceso e spento; McNemar | `confermata` giuste in salita, giuste totali non in calo | da fare (a pagamento) |

**Che cosa ha già trovato il livello 4** (tre regole sbagliate, corrette prima di qualunque corsa a pagamento):
- G7 nella prima forma segnava «disaccordo» in 33 catene su 39: ogni candidato scartato con una riga di Wikipedia
  (i fratelli di una relazione uno-a-molti: gli allievi, i derivati). Ora vale solo il riquadro della voce di prima
  contro un candidato scelto con una fonte più debole.
- G3 senza le parentesi prendeva «I promessi sposi (Petrella)» per il romanzo e «Solférino (Landes)» per Solferino.
  Ora conta il nome intero o la stessa voce di Wikipedia.
- G1 rifiutava «26 Ιουλίου 1985» (mese in greco). Ora una riga senza mesi riconosciuti non si può smentire: bastano
  giorno e anno.

Le due catene cambiate apposta: Divina Commedia (la registrazione finiva il budget girando tra Kyparissia e Tripyla:
G3 ferma il giro) e Penicillina (un anello forte solo per un estratto: G4 lo rende debole e la catena cerca ancora).

**Test ancora da scrivere, in ordine:**

| # | Test | Che cosa dimostra | Costo |
|---|---|---|---|
| T1 | **Sabotaggio (fatto):** spento un controllo alla volta, almeno un test deve fallire (`banco/sabotaggio.py`, su una copia del codice). 17 sabotaggi, 17 scoperti; alla prima corsa ne mancava uno (il registro dei disaccordi non collegato): test aggiunto | nessun controllo è senza test | 0 |
| T2 | **Pagina avvelenata** (`test_browser.py`, server locale): una pagina che dà un valore diverso dal riquadro; un estratto che contraddice la pagina letta; una riga che nomina il valore di un'altra entità | G1, G4 e G7 tengono con pagine vere, non solo con oggetti finti | 0 |
| T3 | **Catena finta intera (fatto)** (`test_catena.py`, modello finto): il modello dà un valore che non è nella riga; torna sulla partenza; un legame da un estratto; il candidato del riquadro scartato; in tutti i casi la risposta finale non è «confermata» o il candidato è scartato con traccia | i controlli sono collegati alla risposta, non solo scritti | 0 |
| T4 | **Domande senza risposta** (20, scritte a mano: entità inventate, relazioni che non esistono, valore mai pubblicato) | lo strumento dice «non trovata» invece di inventare | circa 2 $ |
| T5 | **Seconda lettura (G2):** modello finto che dà due valori diversi nelle due letture → `contestata`; uguali → passa | G2 | 0 |
| T6 | **Revisione contraria (G6):** strade vere sbagliate delle corse (C++, Ducato di Savoia, ENIAC, Marignano) e 10 giuste, date al revisore | quante sbagliate ferma e quante giuste lascia passare, prima di collegarlo | centesimi |
| T7 | **Riscontro (G5):** voci registrate in due lingue che concordano, discordano (Aida) o mancano | G5 | 0 con il rigioco |
| T8 | **Giudice:** 50 verdetti del giudice riletti a mano; le coppie attesa/risposta diventano test del confronto | errori del giudice ≤ 2% | tempo di una persona |
| T9 | **Risposte attese:** ogni risposta attesa confrontata con il riquadro di it.wiki ed en.wiki; quelle che non coincidono segnate | il metro misura lo strumento, non Wikidata | 0 e tempo di una persona |
| T10 | **Stato e documento:** una risposta `incerta` non compare mai come fatto nella sintesi; `contestata` ha le due versioni; la copertina conta gli stati | lo strato del documento | 0 |
| T11 | **Stabilità:** stessa domanda tre volte: stesso valore e stesso stato | il sigillo non dipende dal caso | nei dati del giro 3 |
| T12 | **Due persone** rileggono 40 risposte `verificata` con le fonti aperte; accordo tra loro (kappa) | la misura finale non dipende da un solo lettore né dal giudice | tempo di due persone |

Regole dei test:
- Il test si scrive prima del controllo e deve fallire sul codice di prima.
- Ogni rifiuto di un controllo lascia una riga in `storia` (`valore non nella riga`, `già nella strada`) e l'esito in
  `catena.controlli`: senza traccia un controllo non si può misurare.
- I casi veri restano nei test anche quando il difetto è corretto.
- Un controllo che sul rigioco cambia una catena giusta senza spiegazione non entra.

## 9. Decisioni aperte

1. **Wikidata** come riscontro facoltativo (G5) e per i nuovi insiemi di domande (giri 4 e 9): vuole un indirizzo di
   contatto. Scelte: l'e-mail dell'account Claude, un altro indirizzo, oppure niente Wikidata (G5 solo con Wikipedia in
   altre lingue, insiemi scritti a mano).
2. **Risposte con un anello debole:** mostrate come `incerta` con il valore (consigliato) o nascoste come «non trovata».
3. **Modello della revisione (G6):** Sonnet (consigliato, circa +0,02 $ a domanda) o lo stesso della catena.
4. **Spesa:** fasi A e B centesimi; C, D e campagna circa 60–70 $ di abbonamento in più finestre. Ogni giro a pagamento
   parte solo dopo un sì.
