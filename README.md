# Ricerca a catena

[![test](https://github.com/NiccoloProvvedi/chain_research/actions/workflows/test.yml/badge.svg)](https://github.com/NiccoloProvvedi/chain_research/actions/workflows/test.yml)

**Indagini sul web guidate da Claude, con risposte verificate e un documento PDF con tutte le fonti.**

Si scrive un argomento. Claude lo divide in sezioni e in domande precise; ogni domanda viene risolta da un programma
che segue le prove da una pagina all'altra (la *ricerca a catena*) e controlla ogni citazione; ogni risposta riceve uno
**stato** che dice quanto è affidabile; alla fine si ottiene un documento completo in cui solo le risposte confermate
compaiono come fatti.

- **Prove controllate dal programma**: ogni valore deve essere scritto nella riga della pagina che lo cita.
- **Stato di ogni risposta**: verificata, sostenuta, contestata, incerta o non trovata, deciso dal programma e non
  dal modello che scrive.
- **Documento prudente**: le risposte incerte restano fuori dal testo, in riquadri «Punti aperti»; ogni frase delle
  sezioni è riletta contro le risposte.
- **Applicazione locale**: finestra desktop, indagini che si fermano e si riprendono, limiti di spesa e di tempo.

## Indice

- [Come funziona](#come-funziona)
- [Affidabilità delle risposte](#affidabilità-delle-risposte)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Utilizzo](#utilizzo)
- [Configurazione](#configurazione)
- [Sicurezza e privacy](#sicurezza-e-privacy)
- [Struttura del progetto](#struttura-del-progetto)
- [Sviluppo e test](#sviluppo-e-test)
- [Limiti noti](#limiti-noti)
- [Documentazione](#documentazione)

## Come funziona

```
argomento ─► piano ─► domande ─► ricerca a catena ─► controlli ─► stato ─┐
                ▲                                                         │
                └──────────── valutazione della copertura ◄───────────────┘
                                         │
                                         ▼
                        documento (frasi riverificate) ─► PDF
```

1. **Piano.** Claude (Haiku, Sonnet od Opus, a scelta) prepara titolo, sezioni e prime domande. Ogni domanda chiede un
   solo dato verificabile: una data, un numero, un nome, un luogo, un evento.
2. **Ricerca a catena.** Per ogni domanda un programma parte da ciò che è certo, segue i collegamenti tra le voci di
   Wikipedia e le pagine del web e usa Claude Haiku solo per leggere poche righe numerate alla volta. Le domande vanno
   in parallelo (tre per impostazione predefinita), ognuna con il proprio browser.
3. **Controlli e stato.** Il programma verifica la catena trovata e un secondo modello la rilegge cercando l'errore
   (vedi [Affidabilità delle risposte](#affidabilità-delle-risposte)). Una risposta incerta viene cercata una seconda
   volta, da capo.
4. **Valutazione.** Dopo ogni giro Claude valuta la copertura di ogni sezione, tenendo conto dello stato delle
   risposte, e aggiunge domande sui buchi e sui fatti da confermare. Il ciclo si ferma quando la conoscenza è solida,
   quando la copertura non cresce più, al termine del budget o del tempo, o dopo 12 giri.
5. **Documento.** Claude scrive ogni sezione dalle risposte; ogni frase viene riletta contro le risposte e quelle senza
   appoggio si riscrivono o si tolgono. Seguono sintesi, punti chiave, conclusioni, limiti, l'appendice con tutte le
   domande e il loro stato, il metodo e l'elenco delle fonti. Il PDF è generato dal browser del progetto.

## Affidabilità delle risposte

Prima di diventare un fatto del documento, ogni risposta passa da controlli del programma e da una revisione
indipendente:

- il valore deve essere scritto nella riga citata, e la riga deve parlare dell'entità giusta;
- la catena non può tornare su un'entità già attraversata;
- un estratto dei risultati di ricerca (pagina non letta) non conferma niente;
- il valore viene riletto e cercato in un'altra fonte;
- un secondo modello rilegge, due volte, domanda, catena e righe cercando l'indizio che non torna, e controlla la
  premessa della domanda: se la premessa è falsa (una persona o un'opera che non esiste) la risposta è «non trovata».

| Stato | Significato | Nel documento |
|---|---|---|
| **verificata** | riletta, ritrovata in un'altra fonte, catena senza obiezioni | come fatto |
| **sostenuta** | nessuna obiezione, ma una sola fonte o un controllo non eseguito | come fatto, con il limite indicato |
| **contestata** | le fonti, o due ricerche indipendenti, danno valori diversi | con entrambe le versioni |
| **incerta** | un passaggio non confermato o un'obiezione della revisione | solo nei riquadri «Punti aperti» |
| **non trovata** | nessun valore, oppure premessa della domanda falsa | tra i limiti |

### Misure

Novembre 2026, su domande mai usate per regolare il programma, con risposta attesa controllata sulle voci di
Wikipedia:

| Misura | Risultato |
|---|---|
| Risposte date come fatto (verificata o sostenuta) che sono giuste | **54 su 55** nelle ultime tre corse |
| Risposte giuste sul totale | dal 70% al 96% secondo la corsa, in media circa 85% |
| Domande senza risposta (entità o relazioni inventate) concluse con «non trovata» | 19 su 20 nell'ultima corsa; nessun valore inventato dato come fatto |
| Frasi del documento interamente sostenute dalle risposte | 89% |

Metodo, piano dei test e risultati di ogni corsa sono in
[`docs/validation.md`](docs/validation.md); ogni corsa ha una riga in
[`bench/LOG.md`](bench/LOG.md).

## Requisiti

- Linux (l'unica piattaforma provata);
- [Claude Code](https://claude.com/claude-code) (`claude`) collegato al proprio account: le chiamate a Claude usano
  l'abbonamento di quell'account;
- [uv](https://docs.astral.sh/uv/) e Python 3.12;
- Node.js 22 o successivo, con npm.

## Installazione

```bash
git clone https://github.com/NiccoloProvvedi/chain_research.git
cd chain_research

uv python install 3.12
uv venv venv --python 3.12
VIRTUAL_ENV=venv uv pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=browser/motori venv/bin/playwright install chromium

cd app && npm install
```

## Utilizzo

```bash
./start.sh
```

1. Nella colonna di sinistra si scrive l'argomento, si sceglie il modello e si fissano budget e tempo (oppure
   «Nessun limite»), poi **Avvia l'indagine**.
2. A destra si segue il lavoro in diretta: sezioni con la copertura, domande con risposta e stato, attività.
3. Al termine, **Apri il PDF**.

Un'indagine fermata o interrotta si riprende con **Riprendi**, anche con limiti nuovi; **Scrivi il documento con
quello che c'è** produce il PDF prima della fine. Ogni indagine sta in `indagini/<data>-<argomento>/` (`stato.json`,
`eventi.jsonl`, `documento.html`, `documento.pdf`).

**Costi e tempi indicativi** (indagini reali, novembre 2026): 0,08–0,20 $ e 6–9 minuti per domanda; un'indagine
completa di 30–40 domande costa circa 4–5 $ di abbonamento e richiede da una a quattro ore.

## Configurazione

Dal pulsante **Impostazioni** (salvate in `config/impostazioni.json`, escluso da git):

| Impostazione | Predefinito | Descrizione |
|---|---|---|
| Modello della regia | Sonnet | piano, valutazione e documento |
| Budget e tempo | 5 $, 2 ore | limiti predefiniti di un'indagine; in alternativa nessun limite |
| Domande in parallelo | 3 | da 1 a 4 |
| Modello della catena | Haiku | lettura delle pagine nella ricerca a catena |
| Modello della revisione | Sonnet | rilettura della catena alla ricerca di errori |
| Budget, pagine e minuti per domanda | 0,50 $, 30, 35 | limiti di una singola domanda |
| Contatto per Wikipedia | email dell'account Claude | vedi [Sicurezza e privacy](#sicurezza-e-privacy) |

## Sicurezza e privacy

- **Claude senza strumenti.** Ogni chiamata usa `claude -p --tools ""`, senza server MCP né memoria: il modello non
  può navigare, leggere file o eseguire comandi. Riceve testo e risponde in un formato fisso.
- **Il testo delle pagine è un dato.** Le frasi rivolte a un modello vengono rimosse e il testo viaggia tra
  delimitatori.
- **Browser isolato.** Il browser della ricerca non usa i tuoi account, riparte vuoto a ogni domanda e tutto il suo
  traffico passa da un proxy locale che rifiuta il computer e la rete di casa, anche dopo un redirect o tramite
  WebSocket.
- **Server locale.** Il server dell'applicazione ascolta solo su `127.0.0.1`, con una chiave nota solo alla finestra.
- **Contatto per Wikipedia.** Wikipedia chiede un contatto a chi usa le sue API (con il contatto 200 richieste al
  minuto, senza 10). Se il campo è vuoto si usa l'email dell'account Claude; nelle impostazioni si può indicare un
  altro indirizzo o disattivarlo. Il contatto viene inviato solo nelle richieste a `wikipedia.org` ed è salvato in
  `config/segreti.json` con permessi 600.

## Struttura del progetto

| Percorso | Contenuto |
|---|---|
| `chain_research/investigation.py` | ciclo dell'indagine: piano, domande in parallelo, valutazione, limiti |
| `chain_research/question.py` | una domanda in un processo separato, con la seconda ricerca per le risposte incerte |
| `chain_research/document.py` | documento: sezioni con controllo delle frasi, cornice, fonti, HTML e PDF |
| `chain_research/web/chain.py` | ricerca a catena: piano, frontiera, candidati, verifica, valore, revisione |
| `chain_research/web/validation.py` | controlli sui risultati e stato delle risposte |
| `chain_research/web/retry.py` | seconda ricerca indipendente per le risposte incerte |
| `chain_research/web/collector.py` | ricerche sul web, lettura delle pagine, limiti di una domanda |
| `chain_research/web/fonti/` | Wikipedia come grafo di righe collegate; client HTTP sicuro |
| `chain_research/web/proxy.py`, `browser.py` | proxy di rete e browser isolato |
| `chain_research/modello/` | chiamate a Claude, budget, pulizia del testo, prompt, registrazione e rigioco |
| `chain_research/server.py`, `app/` | server locale e finestra Electron |
| `bench/` | banco di prova: insiemi di domande, giudice, statistiche, rapporti, registro delle corse |
| `test/` | test automatici |
| `docs/` | validazione dei risultati e paper sul metodo |

## Sviluppo e test

```bash
VIRTUAL_ENV=venv uv pip install -r requirements-dev.txt

venv/bin/python -m pytest -q -m "not network and not claude"   # suite completa, senza Internet e senza Claude
venv/bin/python -m pytest -q -m browser                     # solo i test con il browser locale
venv/bin/ruff check ricerca_catena banco test               # analisi statica
venv/bin/python bench/sabotage.py                         # ogni controllo disattivato deve far fallire un test
```

La suite (oltre 500 test) gira a ogni push su GitHub Actions. Le catene registrate in `risultati/rigioco/` si
ripercorrono senza rete né Claude con `pytest -m replay`.

**Banco di prova.** Quattro insiemi di domande con ruoli separati: `domande.json` (40, usate per regolare la catena),
`domande_holdout.json` (36, di controllo), `domande_conferma.json` (23, conferma finale) e
`domande_senza_risposta.json` (20, domande senza risposta). Le corse usano Claude e consumano l'abbonamento:

```bash
venv/bin/python bench/bench.py --nome prova --domande bench/questions_holdout.json --parallelo 1
venv/bin/python bench/report.py <corsa di base> prova    # confronto, precisione per stato, regola di decisione
```

## Limiti noti

- **Variabilità della ricerca.** La stessa domanda può seguire una strada diversa e dare un altro valore da una corsa
  all'altra; lo stato della risposta ne tiene conto, ma la percentuale di risposte giuste varia tra le corse.
- **Tipo di domande.** Le misure riguardano catene di fatti (date, luoghi, numeri) ricostruibili soprattutto da
  Wikipedia, su campioni di qualche decina di domande.
- **Scrittura del documento.** Circa una frase su dieci aggiunge sfumature che le risposte non contengono.
- **Dipendenze esterne.** La ricerca sul web legge le pagine dei risultati di DuckDuckGo e Bing, che possono cambiare.
- **Costo e durata.** Un'indagine completa richiede ore e qualche dollaro di abbonamento.

## Documentazione

- [`docs/validation.md`](docs/validation.md): come si controllano i risultati, piano dei test,
  risultati di tutte le corse.
- [`docs/paper/paper.pdf`](docs/paper/paper.pdf): il metodo di ricerca a catena com'era il 1° novembre 2026. I suoi
  numeri sono misurati sulle domande usate per regolarlo e sono quindi più alti di quelli su domande nuove.
- [`bench/LOG.md`](bench/LOG.md): registro di tutte le corse del banco di prova.
