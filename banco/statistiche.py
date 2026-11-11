"""Statistiche delle corse del banco: intervalli di Wilson, per tema, costi; confronto A/B con il test di McNemar.

Uso: venv/bin/python banco/statistiche.py <corsa> [<corsa> ...]          (cartelle di risultati/banco o loro nomi)
     venv/bin/python banco/statistiche.py --confronta <corsa A> <corsa B>
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
Z = 1.959964


def wilson(giuste: int, totale: int) -> tuple[float, float]:
    """Intervallo di confidenza al 95% di una proporzione."""
    if not totale:
        return 0.0, 0.0
    p = giuste / totale
    d = 1 + Z * Z / totale
    centro = (p + Z * Z / (2 * totale)) / d
    margine = Z * math.sqrt(p * (1 - p) / totale + Z * Z / (4 * totale * totale)) / d
    return max(0.0, centro - margine), min(1.0, centro + margine)


def mcnemar(solo_a: int, solo_b: int) -> float:
    """p bilaterale esatto del test di McNemar sulle domande in cui le due corse differiscono."""
    n = solo_a + solo_b
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(solo_a, solo_b) + 1)) / 2 ** n)


def carica(corsa: str) -> dict[str, dict]:
    """Gli esiti della corsa; se c'è, il nuovo giudizio di `rigiudica.py` (stesso metro per tutte le corse)."""
    cartella = Path(corsa) if Path(corsa).is_dir() else RADICE / "risultati" / "banco" / corsa
    nuovo = cartella / "esiti_rigiudicati.json"
    return json.loads((nuovo if nuovo.is_file() else cartella / "esiti.json").read_text(encoding="utf-8"))


def contano(esiti: list[dict]) -> list[dict]:
    """Le domande giudicate e con una risposta attesa buona."""
    return [e for e in esiti if "corretta" in e and e.get("stato_riferimento", "ok") == "ok"]


def _uguale(a: object, b: object) -> bool:
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def ripetizioni(corse: list[dict[str, dict]]) -> str:
    """Quante domande danno lo stesso valore e lo stesso «confermata» in tutte le corse che le hanno fatte."""
    comuni = [k for k in corse[0] if all("valore" in c.get(k, {}) for c in corse)]
    valore = sum(all(_uguale(c[k]["valore"], corse[0][k]["valore"]) for c in corse) for k in comuni)
    sigillo = sum(len({bool(c[k].get("confermata")) for c in corse}) == 1 for k in comuni)
    return f"ripetendo ({len(comuni)} domande comuni): stesso valore {valore}, stesso «confermata» {sigillo}"


def riassunto(esiti: list[dict]) -> str:
    fatte = contano(esiti)
    giuste = sum(e["corretta"] for e in fatte)
    basso, alto = wilson(giuste, len(fatte))
    return f"{giuste}/{len(fatte)} = {giuste / max(1, len(fatte)):.1%} (95%: {basso:.1%}-{alto:.1%})"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("corse", nargs="+")
    a.add_argument("--confronta", action="store_true")
    arg = a.parse_args()
    if arg.confronta:
        prima, seconda = carica(arg.corse[0]), carica(arg.corse[1])
        comuni = [k for k in prima if "corretta" in prima[k] and "corretta" in seconda.get(k, {})]
        solo_a = [k for k in comuni if prima[k]["corretta"] and not seconda[k]["corretta"]]
        solo_b = [k for k in comuni if seconda[k]["corretta"] and not prima[k]["corretta"]]
        print(f"{len(comuni)} domande comuni. Solo A giuste: {len(solo_a)}, solo B: {len(solo_b)}. "
              f"McNemar p = {mcnemar(len(solo_a), len(solo_b)):.3f}")
        print("solo A:", ", ".join(solo_a) or "-")
        print("solo B:", ", ".join(solo_b) or "-")
        return
    tutte: list[dict] = []
    for corsa in arg.corse:
        esiti = list(carica(corsa).values())
        tutte += esiti
        fatte = contano(esiti)
        errori = sum("errore" in e for e in esiti)
        fuori = sum("corretta" in e for e in esiti) - len(fatte)
        sospese = sum(1 for e in esiti if "corretta" not in e and "errore" not in e)
        print(f"{corsa}: {riassunto(esiti)}, {sum(e['costo_usd'] for e in fatte):.2f} $, "
              f"{sum(e['secondi'] for e in fatte) / max(1, len(fatte)) / 60:.1f} min medi, {errori} errori, "
              f"{sum(bool(e.get('confermata')) for e in fatte)} confermate, "
              f"{sum(e.get('deciso_da') == 'giudice' for e in fatte)} decise dal giudice, {sospese} da giudicare, "
              f"{fuori} fuori dal conto (risposta attesa non buona)")
    if len(arg.corse) > 1:
        print(ripetizioni([carica(c) for c in arg.corse]))
        print(f"TOTALE (le ripetizioni non sono indipendenti): {riassunto(tutte)}")
    temi: dict[str, list[dict]] = defaultdict(list)
    for e in tutte:
        temi[e.get("tema", "?")].append(e)
    for tema, esiti in sorted(temi.items()):
        print(f"  {tema}: {riassunto(esiti)}")
    confermate = [e for e in contano(tutte) if e.get("confermata")]
    altre = [e for e in contano(tutte) if not e.get("confermata")]
    print(f"  catena tutta confermata: {riassunto(confermate)}")
    print(f"  catena non confermata: {riassunto(altre)}")


if __name__ == "__main__":
    main()
