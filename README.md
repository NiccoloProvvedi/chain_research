# Ricerca a catena

Indagini sul web guidate da Claude. Scrivi un argomento: Claude lo divide in sezioni e in domande precise, ogni
domanda viene risolta dalla **ricerca a catena** con prove controllate, dopo ogni giro Claude valuta cosa manca e
chiede ancora, e alla fine ottieni un **documento PDF completo** con tutte le fonti.

## Come funziona

1. **Piano.** Claude (il modello che scegli: Haiku, Sonnet o Opus) legge l'argomento e prepara il titolo, le
   sezioni del documento e le prime domande. Ogni domanda chiede un solo dato verificabile: una data, un numero,
   un nome, un luogo, un evento.
2. **Risposte.** Ogni domanda va alla catena: un programma che parte da cose certe, segue i collegamenti tra le voci
   di Wikipedia e le pagine del web, e usa Claude Haiku solo per leggere poche righe numerate alla volta. Ogni prova
   citata viene controllata dal programma nella pagina da cui viene. Tre domande alla volta, ognuna con il suo
   browser.
3. **Valutazione.** Dopo ogni giro Claude guarda tutte le risposte, dà un voto di copertura a ogni sezione e aggiunge
   domande sui buchi, sui nomi emersi e sui fatti da verificare. Il ciclo si ripete finché Claude non ritiene la
   conoscenza solida, oppure finiscono budget o tempo (al massimo 12 giri).
4. **Documento.** Claude scrive ogni sezione dalle risposte trovate, con le fonti numerate una sola volta per tutto
   il documento; ogni frase è poi riletta contro le risposte e quelle senza appoggio si riscrivono o si tolgono. Poi
   sintesi, punti chiave, conclusioni e limiti. In appendice tutte le domande con la risposta e il suo stato, il metodo
   e l'elenco delle fonti. Il PDF è stampato dal browser del progetto.

### Quanto ci si può fidare delle risposte

Prima di diventare un fatto del documento ogni risposta passa da controlli del programma e da una revisione fatta da
un secondo modello (dettagli in [`docs/solidita-e-validazione.md`](docs/solidita-e-validazione.md)): il valore deve
essere scritto nella riga citata, la catena non deve tornare su sé stessa, un estratto di ricerca non conferma niente,
il valore si rilegge e si cerca in un'altra fonte, e la revisione cerca l'indizio della domanda che non torna. Da qui
lo **stato** di ogni risposta:

| Stato | Che cosa vuol dire | Nel documento |
|---|---|---|
| verificata | riletta, ritrovata in un'altra fonte, catena rivista senza obiezioni | come fatto |
| sostenuta | nessuna obiezione, ma una sola fonte o un controllo non fatto | come fatto, con il limite |
| contestata | le fonti o due ricerche danno valori diversi | con tutte e due le versioni |
| incerta | un passaggio non confermato o un'obiezione della revisione (cercata due volte) | solo nei riquadri «Punti aperti» |
| non trovata | nessun valore, oppure la premessa della domanda è falsa | tra i limiti |

Misure (novembre 2026, su domande mai usate per regolare il programma, con risposta controllata su Wikipedia):

- risposte giuste: dal 70% al 96% secondo la corsa (la ricerca non dà sempre lo stesso risultato; in media circa 85%);
- **risposte date come fatto (verificata o sostenuta): 54 giuste su 55** nelle ultime tre corse;
- domande che non hanno risposta (persone, opere, relazioni inventate): «non trovata» in 19 casi su 20 nell'ultima
  corsa; quando un valore c'è, è segnato incerto, mai dato come fatto;
- frasi del documento sostenute dalle risposte: 89% (il resto aggiunge sfumature).

Le domande delle prove stanno in `banco/`; ogni corsa ha una riga in `banco/REGISTRO.md`. Il paper in
[`docs/paper/paper.pdf`](docs/paper/paper.pdf) descrive il metodo di ricerca com'era il 1° novembre 2026: i suoi
numeri sono sulle domande usate per regolarlo, quindi più alti di quelli su domande nuove.

## Avvio

```bash
./avvia.sh
```

Si apre la finestra. A sinistra scrivi l'argomento, scegli il modello e i limiti (o «Nessun limite»), poi «Avvia
l'indagine». A destra segui il lavoro in diretta: sezioni con la copertura, domande con la risposta, attività. Alla
fine «Apri il PDF». Un'indagine fermata o interrotta si può riprendere; con «Scrivi il documento con quello che c'è»
ottieni il PDF anche prima della fine.

Costi indicativi (due indagini reali, novembre 2026): una domanda costa in media 0,07-0,10 $ e dura circa 7 minuti (tre
alla volta); un'indagine con i limiti predefiniti (5 $, 2 ore) risponde a 35-45 domande. La spesa passa dall'abbonamento Claude dell'account collegato a
`claude` (Claude Code).

## Installazione

Serve `claude` (Claude Code) collegato al tuo account e `uv`.

```bash
uv python install 3.12            # oppure un Python 3.12 già presente
uv venv venv --python 3.12
VIRTUAL_ENV=venv uv pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=browser/motori venv/bin/playwright install chromium
cd app && npm install
```

## Impostazioni

Dal pulsante «Impostazioni» (salvate in `config/impostazioni.json`, fuori da git):

- **modello** predefinito per piano, valutazione e documento;
- **budget e tempo** predefiniti, oppure nessun limite;
- **domande in parallelo** (1-4);
- **contatto per Wikipedia**: Wikipedia chiede un contatto a chi usa i suoi servizi (con il contatto 200 richieste
  al minuto, senza 10). Se il campo è vuoto si usa l'email del tuo account Claude. Il contatto va solo nelle
  richieste a `wikipedia.org`, mai ad altri siti; sta in `config/segreti.json` con permessi 600.

## Sicurezza

- Claude è sempre chiamato senza strumenti (`claude -p --tools ""`, nessun server MCP, nessuna memoria): non può
  navigare, leggere file o eseguire comandi. Riceve solo testo e risponde in un formato fisso.
- Il testo delle pagine è un dato, mai un'istruzione: le frasi rivolte a un modello vengono tolte e il testo viaggia
  tra delimitatori.
- Il browser della ricerca è separato, senza i tuoi account, riparte vuoto a ogni domanda e tutto il suo traffico passa
  da un proxy locale che rifiuta il computer e la rete di casa (anche dopo un redirect o con un WebSocket).
- Il server dell'app ascolta solo su 127.0.0.1, con una chiave segreta che conosce solo la finestra.

## Struttura

| Percorso | Cosa contiene |
|---|---|
| `ricerca_catena/indagine.py` | Il ciclo: piano, risposte in parallelo, valutazione, limiti |
| `ricerca_catena/documento.py` | Documento: sezioni, cornice, fonti, HTML e PDF |
| `ricerca_catena/domanda.py` | Una domanda risolta con la catena, in un processo a sé |
| `ricerca_catena/web/catena.py` | La catena: piano, frontiera, candidati, verifica, valore |
| `ricerca_catena/web/raccolta.py` | Ricerche sul web, lettura delle pagine, limiti di una domanda |
| `ricerca_catena/web/fonti/wikipedia_grafo.py` | Wikipedia come grafo di righe con i loro collegamenti |
| `ricerca_catena/modello/` | Chiamate a Claude, budget, pulizia del testo, prompt |
| `ricerca_catena/server.py`, `app/` | Server locale e finestra Electron |
| `ricerca_catena/web/validazione.py`, `web/riprova.py` | I controlli sui risultati e lo stato delle risposte |
| `banco/` | Banco di prova (domande regolate, di controllo, di conferma, senza risposta), giudice, rapporti, registro |
| `docs/solidita-e-validazione.md` | Come si controllano i risultati, il piano dei test, tutte le misure |
| `docs/paper/` | Il paper sul metodo |

Le indagini stanno in `indagini/<data>-<argomento>/`: `stato.json` (tutto lo stato, si riprende da lì),
`eventi.jsonl`, `documento.html`, `documento.pdf` e i pezzi di pagina letti.

## Test

```bash
VIRTUAL_ENV=venv uv pip install -r requirements-dev.txt     # strumenti di sviluppo, una volta
venv/bin/python -m pytest -q -m "not rete and not claude"   # senza rete e senza Claude (Chromium locale sì)
venv/bin/python banco/sabotaggio.py                         # ogni controllo spento deve far fallire un test
venv/bin/python banco/banco.py --nome prova --semi "informatica/Linux kernel"   # una domanda del banco, dal vivo
```
