"""Nuovo giudizio delle corse salvate, con il metro corretto e senza rifare le domande.

Il valore giudicato è quello trovato dal programma (`valore`), non quello estratto da un modello; il confronto tiene
conto del segno. Un vecchio verdetto del giudice resta se riguardava lo stesso valore. Il resto è «da giudicare»:
con `--giudice` lo decide Claude Haiku (centesimi), senza resta fuori dal conto.

Uso: venv/bin/python banco/rigiudica.py <corsa> [<corsa> ...] [--giudice] [--domande banco/domande_holdout.json]
Scrive risultati/banco/<corsa>/esiti_rigiudicati.json (letto da statistiche.py al posto di esiti.json).
"""
import argparse
import json
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "banco"))

import giudice  # noqa: E402


def rigiudica(esiti: dict[str, dict], con_giudice: bool = False, stati: dict[str, str] | None = None) -> dict[str, dict]:
    nuovi = {}
    for chiave, vecchia in esiti.items():
        voce = dict(vecchia)
        nuovi[chiave] = voce
        if "errore" in voce:
            continue
        if stati and chiave in stati:
            voce["stato_riferimento"] = stati[chiave]
        valore = " ".join(str(voce.get("valore") or "").split())
        if not valore:  # nessun valore dal programma: vale il vecchio giudizio sulla risposta scritta
            continue
        vecchio_finale = giudice.normalizza(voce.get("finale") or "").strip()
        valore_n = giudice.normalizza(valore).strip()
        voce["finale"] = valore
        if giudice.uguale_normalizzata(voce["attesa"], valore):
            voce["corretta"], voce["deciso_da"] = True, "confronto"
        elif voce.get("deciso_da") == "giudice" and vecchio_finale and (
                valore_n in vecchio_finale or vecchio_finale in valore_n):
            pass  # il giudice aveva già visto questo valore
        elif con_giudice:
            corretta, voce["deciso_da"] = giudice.giudica(voce.get("domanda", ""), voce["attesa"], valore)
            if corretta is None:
                voce.pop("corretta", None)
            else:
                voce["corretta"] = corretta
        else:
            voce.pop("corretta", None)
            voce["deciso_da"] = "da giudicare"
    return nuovi


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("corse", nargs="+")
    a.add_argument("--giudice", action="store_true", help="i casi dubbi li decide Claude Haiku")
    a.add_argument("--domande", default="", help="file delle domande, per il campo stato_riferimento")
    arg = a.parse_args()
    stati = {}
    if arg.domande:
        for d in json.loads(Path(arg.domande).read_text(encoding="utf-8"))["domande"]:
            stati[f"{d['tema']}/{d['seme']}"] = d.get("stato_riferimento", "ok")
    for corsa in arg.corse:
        cartella = Path(corsa) if Path(corsa).is_dir() else RADICE / "risultati" / "banco" / corsa
        esiti = json.loads((cartella / "esiti.json").read_text(encoding="utf-8"))
        nuovi = rigiudica(esiti, arg.giudice, stati)
        (cartella / "esiti_rigiudicati.json").write_text(json.dumps(nuovi, ensure_ascii=False, indent=1), encoding="utf-8")
        for chiave, voce in nuovi.items():
            prima, dopo = esiti[chiave].get("corretta"), voce.get("corretta", "da giudicare")
            if prima != dopo:
                print(f"{corsa} {chiave}: {prima} → {dopo} (attesa «{voce.get('attesa')}», valore «{voce.get('valore')}»)")
        giudicate = [v for v in nuovi.values() if "corretta" in v]
        print(f"{corsa}: {sum(v['corretta'] for v in giudicate)}/{len(giudicate)} giuste, "
              f"{sum(1 for v in nuovi.values() if v.get('deciso_da') == 'da giudicare')} da giudicare")


if __name__ == "__main__":
    main()
