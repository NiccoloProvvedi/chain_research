# Ricerca a catena

Indagini sul web guidate da Claude: vedi `README.md` per il funzionamento e la struttura.

## Comandi

```bash
venv/bin/python -m pytest -q -m "not rete and not claude"   # test senza Internet e senza Claude
venv/bin/python -m pytest -q -m browser                     # solo quelli con Chromium
venv/bin/python -m pytest -q -m rigioco                     # le catene registrate (se ci sono), senza rete né Claude
venv/bin/python banco/sabotaggio.py                         # ogni controllo spento deve far fallire un test
venv/bin/python banco/rigiudica.py <corsa>                  # nuovo giudizio di una corsa salvata, senza Claude
```

Il banco (`banco/banco.py`) e ogni corsa con il Claude vero consumano l'abbonamento: chiedi prima di lanciarli.
Le corse lunghe partono con `banco/corsa.sh` da una copia ferma del codice (`git worktree add --detach`, con `venv`,
`browser`, `python`, `risultati`, `log`, `indagini` collegati): staccate dal terminale, riprendono da dove erano.
Le domande di controllo, di conferma e senza risposta non si usano mai per regolare la catena: le regole dello stato
si tarano sulle 40 di `domande.json` e si provano sulle corse salvate con `banco/rigiudica.py` e `banco/rapporto.py`.

## Regole

- Codice, commenti e messaggi in italiano, come il resto del progetto.
- Il traffico del browser passa da `web/proxy.py`, le richieste HTTP da `web/fonti/http.py`: niente altre strade verso la
  rete, altrimenti il blocco di computer e rete di casa non vale.
- `catena.py` è regolato sulle 40 domande di `banco/domande.json`: i risultati su quelle domande non misurano domande
  nuove.
- Ogni difetto corretto ha il suo test, scritto prima della correzione.
- I controlli sui risultati stanno in `web/validazione.py` (piano e stato in `docs/solidita-e-validazione.md`): un controllo
  nuovo entra in `banco/sabotaggio.py` e, se cambia una catena registrata, il motivo va in `da_registrare.txt`.
