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
    cartella = Path(corsa) if Path(corsa).is_dir() else RADICE / "risultati" / "banco" / corsa
    return json.loads((cartella / "esiti.json").read_text(encoding="utf-8"))


def riassunto(esiti: list[dict]) -> str:
    fatte = [e for e in esiti if "corretta" in e]
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
        fatte = [e for e in esiti if "corretta" in e]
        errori = len(esiti) - len(fatte)
        print(f"{corsa}: {riassunto(esiti)}, {sum(e['costo_usd'] for e in fatte):.2f} $, "
              f"{sum(e['secondi'] for e in fatte) / max(1, len(fatte)) / 60:.1f} min medi, {errori} errori, "
              f"{sum(bool(e.get('confermata')) for e in fatte)} confermate")
    if len(arg.corse) > 1:
        print(f"TOTALE (le ripetizioni non sono indipendenti): {riassunto(tutte)}")
    temi: dict[str, list[dict]] = defaultdict(list)
    for e in tutte:
        temi[e.get("tema", "?")].append(e)
    for tema, esiti in sorted(temi.items()):
        print(f"  {tema}: {riassunto(esiti)}")
    confermate = [e for e in tutte if e.get("confermata") and "corretta" in e]
    altre = [e for e in tutte if not e.get("confermata") and "corretta" in e]
    print(f"  catena tutta confermata: {riassunto(confermate)}")
    print(f"  catena non confermata: {riassunto(altre)}")


if __name__ == "__main__":
    main()
