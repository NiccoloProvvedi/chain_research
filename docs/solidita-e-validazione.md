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

## 10. Stato dei lavori

Aggiornato al 12/11/2026. I risultati delle corse a pagamento si aggiungono qui sotto quando arrivano.

**Fatto e protetto dai test** (455 test senza rete, 35 sabotaggi su 35 scoperti):
- Fase A, il metro: giudice con il segno, valore del programma, errori del giudice fuori dal conto, durate pulite,
  `rigiudica.py`, `rapporto.py` (giuste per stato, che cosa segnala ogni controllo, regola di decisione).
- Fase B, controlli del programma: G1 valore nella riga, G3 strada senza ritorni, G4 un estratto non conferma,
  G7 registro dei disaccordi (solo riquadro della voce di prima contro un candidato con fonte più debole).
- Fase C, controlli con il modello (`Catena.revisione_finale`): G2 seconda lettura, G5 riscontro in altre voci e
  pagine, G6 revisione contraria con Sonnet, che riceve anche le righe delle altre voci di ogni anello. Non cambiano
  la risposta: ne decidono lo stato (`validazione.stato`). La riga «Fiducia» la scrive il programma.
- Fase D: stato e motivi salvati con ogni domanda; la regia riceve lo stato; nel documento le `incerta` stanno solo
  nei riquadri «Punti aperti» scritti dal programma; interfaccia con lo stato accanto a ogni domanda; `fedelta.py
  --su fonti` sulle righe delle pagine citate.
- Difetti B5 e B6 corretti (esiti al candidato e al controllo giusti), con i test che li riproducevano.
- T1, T3, T4 (domande scritte e punteggio), T5, T7, T10 del piano dei test.

**Scelte fatte strada facendo:**
- Niente Wikidata: il riscontro usa le altre voci di Wikipedia; gli insiemi nuovi di domande si scrivono a mano.
- Il `verso` della relazione non entra nel piano della catena: il verso lo controlla la revisione (G6), senza
  cambiare le istruzioni che guidano la ricerca.
- I controlli con il modello non fermano né deviano la catena (prima versione): decidono solo lo stato. Farle
  cercare un altro candidato dopo un'obiezione è il passo successivo, da misurare a parte.
- Le corse del banco partono con `banco/corsa.sh`, staccate dalla sessione, da una copia ferma del codice.

**Non fatto:**
- T2 (pagina avvelenata con il browser vero): servirebbe un motore di ricerca finto e un lettore finto per tutta la
  raccolta; i controlli sono provati sugli oggetti della catena e dal vivo.
- T6, T8, T9, T11, T12 e i giri 4, 8 e 9 della campagna: dipendono dalle corse a pagamento o dal tempo di una persona.
- B7 («confermato da N strade» con le fonti diverse): cambia il motivo finale scritto nelle catene registrate, si fa
  insieme alla nuova registrazione.
- Codice morto ancora da togliere; `test_contratti.py` e la CI mai eseguiti.

### Risultati delle corse (12/11/2026)

**Risposte attese** (`banco/verifica_riferimenti.py`, poi a mano): su 36 domande di controllo 27 buone, 6 contestate
(Wikipedia dà un altro valore o un altro passaggio: per il luogo di nascita di Guido van Rossum, Haarlem per Wikidata,
Amsterdam per it.wiki, L'Aia per en.wiki), 3 ambigue (calendario giuliano o gregoriano; istituito o operativo). Le
note stanno in `banco/domande_holdout.json`. I numeri si danno sempre nei due modi: togliere domande dopo aver visto
gli errori può far sembrare lo strumento migliore.

| Corsa | Codice | Giuste, tutte | Giuste, risposte attese buone |
|---|---|---|---|
| `holdout-1` | prima dei controlli | 25/35 = 71% | 24/27 = 89% (95%: 72–96%) |
| `controllo-c1` | tutti i controlli | 24/33 | 22/26 = 85% (95%: 67–94%) |
| `controllo-d1` | la revisione vede le altre voci | 24/36 | 22/27 = 81% (95%: 63–92%) |

Le risposte giuste non cambiano oltre il rumore (McNemar p = 1,0 nei due confronti): i controlli non guidano la
ricerca, danno lo stato. Il calo sulle domande nuove visto all'inizio (69%) veniva per la maggior parte dalle risposte
attese sbagliate, non dallo strumento.

**Stato della risposta, c1 e d1 insieme, risposte attese buone:**

| Stato | Giuste | Sbagliate |
|---|---|---|
| verificata | 9 | 0 |
| sostenuta | 11 | 0 |
| contestata | 2 | 0 |
| incerta | 22 | 9 |

- **Nessuna risposta sbagliata è stata data come fatto**: 20 su 20 tra verificata e sostenuta. Con il vecchio
  «confermata» erano 13 su 17.
- Regola di decisione: passa «verificata e sostenuta più giuste di contestata e incerta» (20/20 contro 24/33); non
  passa ancora «verificata ≥ 95% con limite inferiore ≥ 85%»: 9 su 9 sono troppo poche (limite inferiore 70%).
- **`incerta` è troppo larga**: 22 risposte giuste. La causa è l'«anello debole», che da solo porta a `incerta` ed è un
  segnale povero (21 giuste, 7 sbagliate). Il segnale forte è la revisione: «regge: sì» 29 giuste e 0 sbagliate;
  obiezione con riga 4 e 4.
- Ripetendo (c1 e d1, 33 domande): stesso valore 25, stesso stato 19. Delle 8 domande che cambiano valore, 7 sono
  `incerta` in tutte e due le corse: l'instabilità sta dove lo stato già avverte.
- Il caso che ha portato a d1 (luogo di nascita conteso, risposta `verificata` in c1) in d1 è `incerta`, con la riga
  dell'altra voce come motivo.
- Costo: 4,4 e 5,4 $ per corsa, circa 8 minuti a domanda (prima 16).

**Difetti trovati dalle corse e corretti:** tre domande perse dal banco a 3 ore mentre aspettavano il rinnovo del
limite di sessione; due valori uguali segnati «discordi» perché uno aveva una nota dietro.

**Prossimo passo:** restringere `incerta` (anello debole ma revisione «regge: sì» e riscontro concorde) usando solo
la corsa di taratura sulle 40 domande, poi una corsa di conferma sulle domande di controllo.

### Taratura, regola ristretta e domande senza risposta (13/11/2026)

**Taratura sulle 40 domande regolate** (`taratura-1`, 33/40 giuste, 6,6 $): l'anello debole da solo era un segnale
povero anche qui (21 giuste, 6 sbagliate). Con la revisione che regge senza obiezioni: 6 giuste e 0 sbagliate.
Regola nuova, decisa solo su questa corsa: **un anello debole porta a `incerta` solo se la revisione non regge**;
se regge, la risposta è al massimo `sostenuta`, con il limite scritto.

**La regola nuova sulle corse già fatte** (`rigiudica.py` ricalcola lo stato dagli esiti salvati, senza rifare niente):

| Stato | Domande di controllo, c1 e d1 (risposte attese buone) | Taratura, 40 domande |
|---|---|---|
| verificata | 9 giuste, 0 sbagliate | 1, 0 |
| sostenuta | 23 giuste, 0 sbagliate | 15, 0 |
| contestata | 3 giuste, 0 sbagliate | 1, 0 |
| incerta | 10 giuste, 9 sbagliate | 16, 6 |

- Sulle domande di controllo **32 risposte su 32 date come fatto sono giuste** (limite inferiore al 95%: 89%), e
  `incerta` ora separa davvero: 10 giuste su 19. Tutte le risposte sbagliate stanno in `incerta`.
- Il criterio «`verificata` ≥ 95% con limite inferiore ≥ 85%» resta non dimostrato per lo stato `verificata` da
  solo (9 su 9, limite inferiore 70%); per `verificata` e `sostenuta` insieme è raggiunto. Le due corse sono
  ripetizioni delle stesse domande: la conferma indipendente è il terzo insieme di domande.
- **Lo stato non è stabile quanto il valore**: ripetendo, stesso valore in 28 domande su 36, stesso stato in 19. Il
  verdetto della revisione cambia tra «regge» e «non provato» da una corsa all'altra. Cambia tra `sostenuta` e
  `incerta`, non tra giusto dato per fatto e sbagliato dato per fatto; da migliorare (due revisioni, vale la più severa).

**Venti domande senza risposta** (`senza-risposta-1`, 3,1 $; entità inventate, relazioni che non esistono, dati mai
esistiti):

| Che cosa ha fatto la catena | Domande |
|---|---|
| «Non trovata» (giusto) | 5 |
| Un valore, segnato `incerta` | 13 |
| Un valore dato come fatto (`sostenuta`) | 2 |

- **La catena da sola inventa**: in 15 casi su 20 trova una strada «abbastanza simile» e dà un valore (obiettivo: al
  massimo 2). È il difetto più serio rimasto, e non è dei controlli: è della ricerca, che accetta la premessa della
  domanda.
- I controlli ne fermano 13 su 15 (`incerta`, in 10 con un'obiezione della revisione che dice proprio che la premessa
  è falsa: «Newton non ha una moglie», «non si aggiudicarono mai il premio»). Nel documento finiscono nei «Punti
  aperti», non nel testo.
- 2 su 20 passano come fatto: il relatore del «dottorato» di Leonardo da Vinci e il codice postale del villaggio natale
  di Spartaco. La revisione non ha obiettato.
- Tutte le entità inventate (8 su 8) hanno avuto un valore: la catena sostituisce l'entità che non esiste con una
  vicina. Rimedio da provare: un controllo sulla partenza (l'entità nominata dalla domanda esiste, con quel nome e
  quei tratti?) prima di cercare, e «non trovata» quando la revisione smentisce la premessa.

**Rigioco**: le 40 catene sono state registrate di nuovo con i controlli; 39 si ripetono uguali senza rete né Claude,
una (finita per budget) non è ripetibile. Le vecchie registrazioni stanno in `risultati/rigioco-vecchio` (70 MB, si
possono cancellare).

**Spesa delle corse** dall'11/11: circa 20 $ (c1 5,1; d1 5,4; taratura 6,6; senza risposta 3,1).

**B7** («confermato da N strade») resta com'è: cambierebbe il motivo finale scritto in tutte le registrazioni per una
frase che non entra nello stato.

### Risposte inventate: premessa smentita e catena respinta (13/11/2026)

Due regole nuove in `Catena.revisione_finale`, tutte e due con le parole di una riga controllate dal programma:
- **premessa smentita**: la revisione dice che cosa la domanda dà per vero e, se una riga lo nega (o mostra che la
  strada è partita da un'altra cosa), la risposta è «non trovata» con il motivo; il valore resta in `valore_scartato`;
- **catena respinta**: obiezione con riga, premessa non provata e anello debole insieme.

Prima della corsa vera, `banco/prova_revisione.py` (rifà solo la revisione sulle corse salvate, circa 1,5 $): sulle 40
domande di taratura nessuna risposta giusta persa su 33.

| | Prima (`senza-risposta-1`) | Dopo (`senza-risposta-2`) |
|---|---|---|
| «Non trovata» (giusto) | 5 su 20 | **14 su 20** |
| Valore dato come fatto | 2 | **0** |
| Valore segnato `incerta` o `contestata` | 13 | 6 |

Sulle domande di controllo (`controllo-f1`, risposte attese buone): 25 giuste su 27 = 93% (95%: 77–98%), contro 24 su
27 di prima dei controlli (McNemar p = 0,69: nessuna differenza dimostrata, e nessun calo); **20 risposte su 20 date
come fatto sono giuste**; una sola risposta rifiutata, ed era sbagliata. Nessuna risposta giusta persa.

Restano 6 domande senza risposta su 20 con un valore, tutte segnate: due entità inventate (la catena prende un'entità
vicina e la revisione non ha una riga per dirlo) e quattro in cui la strada trovata «somiglia». L'obiettivo del 10% non
è raggiunto (30%); nessuna però arriva nel documento come fatto. Il passo che manca è nella ricerca, non nei
controlli: controllare che l'entità nominata dalla domanda esista con quel nome prima di cercare (cambia le istruzioni
del piano della catena: vuole una nuova registrazione).

**Insieme di conferma** (`banco/domande_conferma.json`, scritto a mano il 13/11): 13 domande con la risposta
controllata sulle voci italiane e inglesi (tenute solo quelle in cui le due lingue coincidono; 10 scartate per
altitudini diverse) e 10 domande senza risposta nuove. Si usa una volta sola, sul codice finale.

### Conferma su domande mai viste (13/11/2026, `conferma-1`, una volta sola, 1,9 $)

| | Risultato |
|---|---|
| Domande con risposta (13) | 12 giuste = 92% (95%: 67–99%) |
| Risposte date come fatto (`verificata` 8, `sostenuta` 4) | **12 giuste su 12** |
| La risposta sbagliata | `incerta`: la revisione ha obiettato citando la riga con il nome giusto («ebbe Oswald Veblen come relatore») |
| Domande senza risposta (10) | 6 «non trovata»; 4 con un valore, tutte `incerta`; **nessuna data come fatto** |

È il numero indipendente: queste domande non hanno mai deciso niente. Sono poche e, quelle con risposta, più facili
delle domande di controllo (2 o 3 passi, partenze molto note): conferma la direzione, non stringe gli intervalli.
Insieme alle domande di controllo sul codice finale (`controllo-f1`): 32 risposte su 32 date come fatto sono giuste
(limite inferiore al 95%: 89%); le domande senza risposta danno un valore in 10 casi su 30, mai come fatto.

### Tre indagini complete sul codice finale (13–14/11/2026)

| Indagine | Domande | verificata | sostenuta | contestata | incerta | Costo |
|---|---|---|---|---|---|---|
| Canale di Suez | 28 | 8 | 16 | 0 | 4 | 3,0 $ |
| Olivetti | 40 | 5 | 24 | 4 | 7 | 3,7 $ |
| Telescopio James Webb | 36 (30 con risposta) | 2 | 16 | 4 | 8 | 3,7 $ |

- **La regola del documento tiene dal vivo**: 19 risposte incerte nei tre documenti, tutte nei riquadri «Punti
  aperti»; nessuna entra nel testo da sola (8 valori ci sono anche nel testo perché un'altra risposta, non incerta,
  porta lo stesso valore con la sua fonte).
- L'indagine Olivetti si è fermata con la regola nuova («la copertura non cresce più: molti fatti chiave sono incerti
  o contestati»): la regia reagisce allo stato.
- Nota sulla prova, non sul prodotto: le indagini sono partite con l'attesa del limite di sessione accesa; il tempo
  dell'indagine e quello della domanda contano anche quell'attesa (Suez ferma a 2 giri, 5 domande del James Webb
  scadute). Nell'applicazione il limite ferma l'indagine, che si riprende.

**Fedeltà delle frasi** (`banco/fedelta.py`, 40 frasi citate a caso per documento, giudice Sonnet):

| Indagine | Contro le risposte: sostenute / in parte / no | Contro le righe delle fonti: sostenute / in parte / no |
|---|---|---|
| Canale di Suez | 38 / 2 / 0 | 28 / 7 / 5 |
| Olivetti | 31 / 9 / 0 | 27 / 8 / 5 |
| Telescopio James Webb | 36 / 3 / 1 | 23 / 10 / 7 |

- Contro le risposte: 105 frasi su 120 sostenute (88%), 1 non sostenuta. **L'obiettivo del 95% è raggiunto in un
  documento su tre.** Le «in parte» sono frasi che aggiungono o cambiano un dettaglio rispetto alla risposta.
- Contro le righe delle fonti il numero è più basso (65%) ma misura meno di quel che sembra: si salvano solo le righe
  della strada della catena, non tutto quello che chi ha scritto la risposta aveva davanti; e molte frasi «non
  sostenute» parlano delle fonti stesse («viene da una sola fonte», «la data non è indicata»), non di fatti.
- Tra le non sostenute c'è almeno un fatto che sembra inventato da chi scrive la sezione («fino al 1964 la presidenza
  fu di Elio Schiavo»): l'obiettivo «nessun fatto chiave inventato» non è dimostrato.
- **Lo strato più debole adesso è la scrittura delle sezioni**: i controlli proteggono le risposte, non le frasi che
  Claude scrive a partire da esse. Passo successivo: controllare ogni frase della sezione contro la risposta che cita
  mentre il documento si scrive (stessa logica della revisione: una frase senza appoggio si toglie o si riscrive), e
  salvare tutte le righe viste da chi scrive, non solo quelle della strada.

### Bilancio (14/11/2026)

| Obiettivo fissato all'inizio | Risultato |
|---|---|
| Risposte giuste su domande nuove, limite inferiore ≥ 70% | 25/27 = 93% (limite inferiore 77%) sulle domande di controllo; 12/13 sull'insieme di conferma: **raggiunto** |
| Risposte date come fatto giuste ≥ 95%, limite inferiore ≥ 85% | 32 su 32 (limite inferiore 89%) tra controllo e conferma: **raggiunto** per `verificata` e `sostenuta` insieme; non dimostrabile per `verificata` da sola con questi numeri |
| Stesso valore ripetendo ≥ 85% | 28 su 36 = 78%: **non raggiunto**; le domande instabili sono quasi tutte `incerta` |
| Stesso stato ripetendo ≥ 85% | 19 su 36: **non raggiunto** (il verdetto della revisione oscilla tra «regge» e «non provato») |
| Risposte inventate ≤ 10% | valore dato in 10 casi su 30 domande senza risposta: **non raggiunto**; dato come fatto: 0 su 30 |
| Frasi del documento sostenute ≥ 95% | 88% contro le risposte: **non raggiunto** |
| Limiti di budget e di tempo rispettati | sì nelle tre indagini |

Spesa totale delle corse dall'11 al 14/11: circa 47 $ di abbonamento.

### Controllo delle frasi delle sezioni (15/11/2026)

`documento.controlla_sezione`: il programma segnala le frasi con un numero che le risposte non danno, la regia rilegge
le frasi contro le risposte, le frasi segnalate tornano una volta a chi scrive, quelle ancora senza appoggio le toglie
il programma. I tre documenti sono stati riscritti (i vecchi restano accanto come `documento_prima_del_controllo.*`):

| Indagine | Frasi segnalate e fatte riscrivere | Tolte dal programma | Sostenute prima | Sostenute dopo | Regia, prima e dopo |
|---|---|---|---|---|---|
| Canale di Suez | 3 | 0 | 38 / 40 | 39 / 40 | 0,42 → 1,18 $ |
| Olivetti | 8 | 0 | 31 / 40 | 35 / 40 | 0,61 → 1,75 $ |
| Telescopio James Webb | 3 | 0 | 36 / 40 | 33 / 40 | 0,59 → 1,33 $ |

- Frasi «non sostenute» nel campione: da 1 a **0**. La frase inventata sulla presidenza Olivetti «fino al 1964» non
  c'è più (ora dice quello che le fonti dicono, senza la data).
- Frasi del tutto sostenute: 105 su 120 prima, 107 su 120 dopo (88% e 89%): **nessun miglioramento misurabile**, e
  l'obiettivo del 95% resta non raggiunto. Le «in parte» rimaste sono quasi tutte sfumature («una seconda fonte»,
  «nel mondo», «massima») o frasi che parlano delle fonti; il giudice e chi rilegge sono lo stesso modello e non
  concordano frase per frase.
- Il costo della regia cresce di circa 0,75 $ a documento (la riscrittura delle sezioni è compresa in questa cifra;
  la sola rilettura costa meno).
- Che cosa dà di sicuro: nessun numero o data nel testo che non stia nelle risposte (controllo del programma, senza
  costo) e nessuna frase che chi rilegge giudica senza appoggio. Che cosa non dà: frasi senza sfumature aggiunte.
  Per quello servirebbe scrivere le sezioni per composizione di frasi già controllate, non per riscrittura libera.

### Due revisioni e nome della partenza (15–16/11/2026)

Due modifiche, tutte e due dopo la catena (non cambiano la ricerca):
- **due revisioni indipendenti, vale la più severa** (`validazione.unisci_revisioni`);
- **il nome con cui la domanda chiama la sua partenza** (la revisione lo copia dalla domanda, il programma controlla
  che sia davvero nella domanda) **deve stare in almeno una pagina letta** (`validazione.nome_assente`): altrimenti
  «non trovata».

Corse a bassa priorità, una domanda alla volta (`BASSA_PRIORITA=1 banco/corsa.sh … --parallelo 1`):

| | Prima (codice del 13/11) | Dopo (codice del 15/11) |
|---|---|---|
| 20 domande senza risposta: «non trovata» | 14 | **17** (3 con un valore, tutte `incerta`) |
| 10 domande senza risposta dell'insieme di conferma | 6 | **8** |
| Valori inventati dati come fatto | 0 | 0 |
| Domande di controllo, risposte attese buone | 25 su 27 | **19 su 27** |
| Insieme di conferma, 13 domande con risposta | 12 giuste | 11 giuste |

- Sulle domande senza risposta il valore inventato resta in 5 casi su 30 (17%; l'obiettivo era 10%), mai come fatto.
- **Il calo sulle domande di controllo non viene dai controlli ma dalla catena, che cambia da una corsa all'altra**: in
  5 delle 8 risposte sbagliate la ricerca ha trovato un altro valore prima di ogni revisione (C++, ENIAC, Museo del
  Louvre, Madama Butterfly, Isaac Newton: le stesse domande instabili delle altre corse). Nelle quattro corse fatte
  con i controlli le giuste sono state 23, 22, 25 e 19 su 27: in media l'82%, con tre domande in più o in meno da
  una corsa all'altra. Il 93% del 13/11 era una corsa fortunata; il numero onesto è «tra il 70 e il 93%, in media
  82%».
- I rifiuti sulle domande di controllo sono stati 2: uno di una risposta sbagliata, **uno di una risposta giusta**
  (Decameron: 50 m, catena respinta). È il prezzo misurato delle regole che fermano le risposte inventate.
- Risposte date come fatto, corse del 13/11 e del 15/11 insieme: `verificata` 15 giuste su 15, `sostenuta` 22 su 23.
  L'unica sbagliata è un passaggio su cui le fonti non concordano (dove morì la madre di Newton: Stamford per
  Wikidata, Colsterworth per la pagina letta), che la revisione non ha visto.
- La stabilità dello stato non è migliorata in modo misurabile: tra le due corse stesso valore in 26 domande su 36,
  stesso stato in 21 (prima 28 e 19). Con una catena che cambia valore una volta su quattro, lo stato non può essere
  più stabile del valore.

**Che cosa resta, in ordine:** la catena non è ripetibile sulle domande difficili (stessa domanda, altra strada e
altro valore): è il limite più grande rimasto, e sta nella ricerca, non nella validazione. Poi: i passaggi su cui le
fonti non concordano e che la revisione non vede; le frasi delle sezioni con sfumature aggiunte.
