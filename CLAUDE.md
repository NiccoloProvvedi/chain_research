# Ricerca a catena

Indagini sul web guidate da Claude: vedi `README.md` per il funzionamento e la struttura.

## Comandi

```bash
venv/bin/python -m pytest -q -m "not rete and not claude"   # test senza Internet e senza Claude
venv/bin/python -m pytest -q -m browser                     # solo quelli con Chromium
```

Il banco (`banco/banco.py`) e ogni corsa con il Claude vero consumano l'abbonamento: chiedi prima di lanciarli.

## Regole

- Codice, commenti e messaggi in italiano, come il resto del progetto.
- Il traffico del browser passa da `web/proxy.py`, le richieste HTTP da `web/fonti/http.py`: niente altre strade verso la
  rete, altrimenti il blocco di computer e rete di casa non vale.
- `catena.py` è regolato sulle 40 domande di `banco/domande.json`: i risultati su quelle domande non misurano domande
  nuove.
- Ogni difetto corretto ha il suo test, scritto prima della correzione.
