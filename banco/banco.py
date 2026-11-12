"""Banco di prova: le 40 domande a catena (banco/domande.json) risolte con lo stesso esecutore dell'app.

Uso:  venv/bin/python banco/banco.py --nome prova-1 [--semi "informatica/Linux kernel,..."] [--parallelo 3]
      [--nota "cosa si prova"] [--budget 12]
Le domande vanno in parallelo, ognuna nel suo processo (`python -m ricerca_catena.domanda`) con il suo profilo del
browser; Wikidata è vietata (le domande vengono da lì). Esiti in risultati/banco/<nome>/esiti.json (si riprende
dalle domande non fatte) e una riga in banco/REGISTRO.md.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "banco"))

import giudice  # noqa: E402

from ricerca_catena import configurazione  # noqa: E402
from ricerca_catena.modello import claude_json  # noqa: E402
from ricerca_catena.web.fonti.wikipedia_grafo import intervallo_per  # noqa: E402


ATTESA_MASSIMA_S = 14 * 3600


def una_domanda(d: dict, profilo: str, parallelo: int, registra: Path | None = None) -> dict:
    opzioni = {"senza_wikidata": True, "wikipedia_intervallo": intervallo_per(parallelo), "controlli_modello": True}
    richiesta = {"domanda": d["domanda"], "profilo": str(configurazione.cartella("cartella_profili") / profilo),
                 "cartella_pezzi": str(RADICE / "risultati" / "banco" / ".pezzi"), "opzioni": opzioni}
    ambiente = dict(os.environ)
    cartella, oggi = None, f"{datetime.now():%Y-%m-%d}"
    if registra is not None:  # ogni domanda nella sua cartella; PYTHONHASHSEED e data uguali anche nel rigioco
        cartella = registra / re.sub(r"\W+", "-", f"{d['tema']}-{d['seme']}")
        ambiente.update(RICERCA_REGISTRA=str(cartella), RICERCA_OGGI=oggi, PYTHONHASHSEED="0")
    uscita = subprocess.run([str(RADICE / "venv" / "bin" / "python"), "-m", "ricerca_catena.domanda"], cwd=RADICE,
                            input=json.dumps(richiesta, ensure_ascii=False) + "\n", capture_output=True, text=True,
                            # l'attesa del limite di sessione (fino a 6 ore) sta dentro questo tempo: 12/11, tre
                            # domande di controllo perse a 3 ore mentre aspettavano il rinnovo
                            timeout=ATTESA_MASSIMA_S, env=ambiente)
    for riga in reversed(uscita.stdout.splitlines()):
        messaggio = json.loads(riga)
        if "esito" in messaggio:
            esito = messaggio["esito"]
            if cartella is not None:
                (cartella / "atteso.json").write_text(json.dumps({
                    "domanda": d["domanda"], "opzioni": opzioni, "oggi": oggi, "valore": esito.get("valore"),
                    "trovata": esito.get("trovata"), "motivo_fine": esito.get("motivo_fine"),
                    "azioni": esito.get("catena", {}).get("azioni"), "risposta": esito.get("risposta")},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            return esito
        if "errore" in messaggio:
            raise RuntimeError(messaggio["errore"])
    raise RuntimeError(f"nessun esito (codice {uscita.returncode})")


def giudica_voce(voce: dict) -> None:
    """Aggiunge `finale`, `corretta` e `deciso_da`. Se il giudice non risponde la risposta trovata resta, segnata
    `da_giudicare`: alla ripresa si rifà solo il giudizio, non la domanda."""
    voce.pop("da_giudicare", None)
    if voce.get("senza_risposta"):  # la domanda non ha risposta: giusto è non darne una (nessun modello a giudicare)
        voce["finale"] = " ".join(str(voce.get("valore") or "").split())
        voce["corretta"], voce["deciso_da"] = not voce.get("trovata"), "senza risposta"
        return
    try:
        voce["finale"] = giudice.finale_di(voce, voce["domanda"])
        corretta, voce["deciso_da"] = giudice.giudica(voce["domanda"], voce["attesa"], voce["finale"])
    except claude_json.ErroreClaude as errore:
        voce.setdefault("finale", "")
        corretta, voce["deciso_da"] = None, f"giudice in errore: {errore}"[:200]
    if corretta is None:
        voce["da_giudicare"] = True
    else:
        voce["corretta"] = corretta


def contano(esiti: list[dict]) -> list[dict]:
    """Le domande giudicate con una risposta attesa buona (`stato_riferimento`: docs/solidita-e-validazione.md §3)."""
    return [e for e in esiti if "corretta" in e and e.get("stato_riferimento", "ok") == "ok"]


def main() -> None:
    a = argparse.ArgumentParser(description="Banco di prova delle domande a catena")
    a.add_argument("--nome", required=True)
    a.add_argument("--semi", default="", help="solo queste domande: tema/seme separati da virgola")
    a.add_argument("--parallelo", type=int, default=3)
    a.add_argument("--budget", type=float, default=12.0, help="dollari massimi di Claude per tutta la corsa")
    a.add_argument("--nota", default="")
    a.add_argument("--domande", default="", help="file di domande diverso da banco/domande.json")
    a.add_argument("--registra", default="", help="cartella in cui registrare tutto per il rigioco (test_rigioco.py)")
    arg = a.parse_args()
    cartella = RADICE / "risultati" / "banco" / arg.nome
    cartella.mkdir(parents=True, exist_ok=True)
    file_esiti = cartella / "esiti.json"
    esiti = json.loads(file_esiti.read_text(encoding="utf-8")) if file_esiti.is_file() else {}
    file_domande = Path(arg.domande) if arg.domande else RADICE / "banco" / "domande.json"
    domande = json.loads(file_domande.read_text(encoding="utf-8"))["domande"][:None if arg.domande else 40]
    registra = Path(arg.registra).resolve() if arg.registra else None
    if arg.semi:
        volute = {s.strip() for s in arg.semi.split(",") if s.strip()}
        domande = [d for d in domande if f"{d['tema']}/{d['seme']}" in volute]
    for voce in esiti.values():  # giudizi rimasti in sospeso (limite di sessione, errore del giudice)
        if voce.get("da_giudicare"):
            giudica_voce(voce)
    da_fare = [d for d in domande if not {"corretta", "da_giudicare"} & set(esiti.get(f"{d['tema']}/{d['seme']}", {}))]
    print(f"{len(da_fare)} domande da fare su {len(domande)}; risultati in {cartella}", flush=True)
    serratura = threading.Lock()
    profili = [f"banco_{i + 1}" for i in range(arg.parallelo)]

    def lavora(d: dict) -> None:
        chiave = f"{d['tema']}/{d['seme']}"
        with serratura:
            if sum(e.get("costo_usd", 0) for e in esiti.values()) >= arg.budget:
                return
            profilo = profili.pop(0)
        try:
            esito = una_domanda(d, profilo, arg.parallelo, registra)
            voce = {"tema": d["tema"], "seme": d["seme"], "domanda": d["domanda"], "attesa": d["risposta"],
                    "stato_riferimento": d.get("stato_riferimento", "ok"),
                    "senza_risposta": bool(d.get("senza_risposta")), **esito}
            giudica_voce(voce)
            print(f"{'?' if 'corretta' not in voce else '✓' if voce['corretta'] else '✗'} {chiave}: attesa "
                  f"«{d['risposta']}», finale «{voce['finale'][:80]}» ({esito['secondi']} s, {esito['costo_usd']} $)",
                  flush=True)
        except Exception as errore:  # noqa: BLE001 - una domanda fallita non ferma la corsa
            voce = {"tema": d["tema"], "seme": d["seme"], "errore": f"{type(errore).__name__}: {errore}"[:500]}
            print(f"ERRORE {chiave}: {errore}", flush=True)
        finally:
            with serratura:
                profili.append(profilo)
        with serratura:
            esiti[chiave] = voce
            file_esiti.write_text(json.dumps(esiti, ensure_ascii=False, indent=1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=arg.parallelo) as esecutore:
        list(esecutore.map(lavora, da_fare))
    fatte = contano([esiti.get(f"{d['tema']}/{d['seme']}", {}) for d in domande])
    giuste = sum(e["corretta"] for e in fatte)
    confermate = [e for e in fatte if e.get("confermata")]
    dal_giudice = sum(e.get("deciso_da") == "giudice" for e in fatte)
    sospese = sum(1 for e in esiti.values() if e.get("da_giudicare"))
    minuti = round(sum(e["secondi"] for e in fatte) / max(1, len(fatte)) / 60, 1)
    costo = round(sum(e["costo_usd"] for e in fatte), 3)
    errori = sum(1 for d in domande if "errore" in esiti.get(f"{d['tema']}/{d['seme']}", {}))
    print(f"RIEPILOGO {giuste}/{len(fatte)} giuste, {minuti} minuti medi, {costo} $, {errori} errori, confermate giuste "
          f"{sum(e['corretta'] for e in confermate)}/{len(confermate)}, decise dal giudice {dal_giudice}, da giudicare "
          f"{sospese}", flush=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=RADICE).stdout.strip() or "?"
    with (RADICE / "banco" / "REGISTRO.md").open("a", encoding="utf-8") as f:
        f.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {arg.nota or arg.nome} | {commit} | {len(domande)} domande | "
                f"{giuste}/{len(fatte)} | {minuti} | {costo} | {errori} | "
                f"{sum(e['corretta'] for e in confermate)}/{len(confermate)} | {dal_giudice} |\n")


if __name__ == "__main__":
    main()
