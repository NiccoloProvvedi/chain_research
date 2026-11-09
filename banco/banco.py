"""Banco di prova: le 40 domande a catena (banco/domande.json) risolte con lo stesso esecutore dell'app.

Uso:  venv/bin/python banco/banco.py --nome prova-1 [--semi "informatica/Linux kernel,..."] [--parallelo 3]
      [--nota "cosa si prova"] [--budget 12]
Le domande vanno in parallelo, ognuna nel suo processo (`python -m ricerca_catena.domanda`) con il suo profilo del
browser; Wikidata è vietata (le domande vengono da lì). Esiti in risultati/banco/<nome>/esiti.json (si riprende
dalle domande non fatte) e una riga in banco/REGISTRO.md.
"""
import argparse
import json
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
from ricerca_catena.web.fonti.wikipedia_grafo import intervallo_per  # noqa: E402


def una_domanda(d: dict, profilo: str, parallelo: int) -> dict:
    richiesta = {"domanda": d["domanda"], "profilo": str(configurazione.cartella("cartella_profili") / profilo),
                 "cartella_pezzi": str(RADICE / "risultati" / "banco" / ".pezzi"),
                 "opzioni": {"senza_wikidata": True, "wikipedia_intervallo": intervallo_per(parallelo)}}
    uscita = subprocess.run([str(RADICE / "venv" / "bin" / "python"), "-m", "ricerca_catena.domanda"], cwd=RADICE,
                            input=json.dumps(richiesta, ensure_ascii=False) + "\n", capture_output=True, text=True,
                            timeout=3 * 3600)
    for riga in reversed(uscita.stdout.splitlines()):
        messaggio = json.loads(riga)
        if "esito" in messaggio:
            return messaggio["esito"]
        if "errore" in messaggio:
            raise RuntimeError(messaggio["errore"])
    raise RuntimeError(f"nessun esito (codice {uscita.returncode})")


def main() -> None:
    a = argparse.ArgumentParser(description="Banco di prova delle domande a catena")
    a.add_argument("--nome", required=True)
    a.add_argument("--semi", default="", help="solo queste domande: tema/seme separati da virgola")
    a.add_argument("--parallelo", type=int, default=3)
    a.add_argument("--budget", type=float, default=12.0, help="dollari massimi di Claude per tutta la corsa")
    a.add_argument("--nota", default="")
    arg = a.parse_args()
    cartella = RADICE / "risultati" / "banco" / arg.nome
    cartella.mkdir(parents=True, exist_ok=True)
    file_esiti = cartella / "esiti.json"
    esiti = json.loads(file_esiti.read_text(encoding="utf-8")) if file_esiti.is_file() else {}
    domande = json.loads((RADICE / "banco" / "domande.json").read_text(encoding="utf-8"))["domande"][:40]
    if arg.semi:
        volute = {s.strip() for s in arg.semi.split(",") if s.strip()}
        domande = [d for d in domande if f"{d['tema']}/{d['seme']}" in volute]
    da_fare = [d for d in domande if "corretta" not in esiti.get(f"{d['tema']}/{d['seme']}", {})]
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
            esito = una_domanda(d, profilo, arg.parallelo)
            finale = giudice.estrai(d["domanda"], giudice.risposta_breve(esito["risposta"]))
            corretta, deciso = giudice.giudica(d["domanda"], d["risposta"], finale)
            voce = {"tema": d["tema"], "seme": d["seme"], "domanda": d["domanda"], "attesa": d["risposta"],
                    "finale": finale, "corretta": corretta, "deciso_da": deciso, **esito}
            print(f"{'✓' if corretta else '✗'} {chiave}: attesa «{d['risposta']}», finale «{finale[:80]}» "
                  f"({esito['secondi']} s, {esito['costo_usd']} $)", flush=True)
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
    fatte = [esiti[f"{d['tema']}/{d['seme']}"] for d in domande if "corretta" in esiti.get(f"{d['tema']}/{d['seme']}", {})]
    giuste = sum(e["corretta"] for e in fatte)
    minuti = round(sum(e["secondi"] for e in fatte) / max(1, len(fatte)) / 60, 1)
    costo = round(sum(e["costo_usd"] for e in fatte), 3)
    errori = sum(1 for d in domande if "errore" in esiti.get(f"{d['tema']}/{d['seme']}", {}))
    print(f"RIEPILOGO {giuste}/{len(fatte)} giuste, {minuti} minuti medi, {costo} $, {errori} errori", flush=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=RADICE).stdout.strip() or "?"
    with (RADICE / "banco" / "REGISTRO.md").open("a", encoding="utf-8") as f:
        f.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {arg.nota or arg.nome} | {commit} | {len(domande)} domande | "
                f"{giuste}/{len(fatte)} | {minuti} | {costo} | {errori} |\n")


if __name__ == "__main__":
    main()
