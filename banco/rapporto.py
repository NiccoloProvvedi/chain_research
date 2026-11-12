"""Rapporto sui controlli dei risultati: quanto vale lo stato di una risposta e che cosa segnala ogni controllo.

Legge le corse salvate (il nuovo giudizio di `rigiudica.py`, se c'è) e non chiama né la rete né Claude.

Uso: venv/bin/python banco/rapporto.py <corsa di base> <corsa> [<corsa> ...]
     La prima corsa è il confronto (codice di prima); le altre sono ripetizioni della stessa prova con i controlli.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from statistiche import _uguale, carica, contano, mcnemar, riassunto, wilson  # noqa: E402

STATI = ("verificata", "sostenuta", "contestata", "incerta")


def segnali(e: dict) -> list[str]:
    """I controlli che hanno segnalato qualcosa su questa risposta (catena.controlli dell'esito)."""
    c = (e.get("catena") or {}).get("controlli") or {}
    esami = c.get("esami") or {}
    lettura, riscontro, revisione = (esami.get(k) or {} for k in ("seconda_lettura", "riscontro", "revisione"))
    trovati = []
    if c.get("anelli_deboli"):
        trovati.append("anello debole")
    if c.get("fonte_valore") == "C":
        trovati.append("valore da un estratto")
    if c.get("disaccordi"):
        trovati.append("riquadro che indica un altro candidato")
    if lettura.get("esito") in ("discordi", "non ritrovato"):
        trovati.append(f"seconda lettura: {lettura['esito']}")
    if riscontro.get("esito") in ("discordi", "sola fonte", "concordi"):
        trovati.append(f"altre fonti: {riscontro['esito']}")
    if revisione.get("obiezioni"):
        trovati.append("revisione: obiezione con riga")
    elif revisione.get("esito") == "fatta":
        trovati.append(f"revisione: regge {revisione.get('regge')}")
    if not esami or all(x.get("esito") == "non fatta" for x in (lettura, riscontro, revisione)):
        trovati.append("esami non fatti")
    return trovati


def tabella_segnali(esiti: list[dict]) -> dict[str, tuple[int, int]]:
    """Segnale → (risposte giuste, risposte sbagliate) tra quelle con un valore."""
    conti: dict[str, list[int]] = {}
    for e in contano(esiti):
        if not e.get("valore"):
            continue
        for s in segnali(e):
            conti.setdefault(s, [0, 0])[0 if e["corretta"] else 1] += 1
    return {s: (g, b) for s, (g, b) in sorted(conti.items())}


def per_stato(esiti: list[dict]) -> dict[str, tuple[int, int]]:
    """Stato → (giuste, totale)."""
    fatte = contano(esiti)
    return {s: (sum(e["corretta"] for e in fatte if e.get("stato") == s), sum(e.get("stato") == s for e in fatte))
            for s in STATI}


def regola(esiti: list[dict]) -> list[tuple[str, bool, str]]:
    """La regola di decisione fissata prima delle corse (docs/solidita-e-validazione.md): (criterio, passato, numeri)."""
    stati = per_stato(esiti)
    giuste, totale = stati["verificata"]
    basso = wilson(giuste, totale)[0]
    buone = tuple(map(sum, zip(stati["verificata"], stati["sostenuta"], strict=True)))
    dubbie = tuple(map(sum, zip(stati["contestata"], stati["incerta"], strict=True)))
    quota = lambda x: x[0] / x[1] if x[1] else 0.0  # noqa: E731
    return [
        ("verificata: giuste almeno il 95% (limite inferiore almeno 85%)",
         totale > 0 and giuste / totale >= 0.95 and basso >= 0.85, f"{giuste}/{totale}, limite inferiore {basso:.0%}"),
        ("verificata e sostenuta più giuste di contestata e incerta",
         buone[1] > 0 and dubbie[1] > 0 and quota(buone) - quota(dubbie) >= 0.15,
         f"{buone[0]}/{buone[1]} contro {dubbie[0]}/{dubbie[1]}"),
    ]


def ripetizioni(corse: list[dict[str, dict]]) -> str:
    comuni = [k for k in corse[0] if all("valore" in c.get(k, {}) for c in corse)]
    valore = sum(all(_uguale(c[k]["valore"], corse[0][k]["valore"]) for c in corse) for k in comuni)
    stato = sum(len({c[k].get("stato") for c in corse}) == 1 for k in comuni)
    return f"{len(comuni)} domande in tutte le corse: stesso valore {valore}, stesso stato {stato}"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("base")
    a.add_argument("corse", nargs="+")
    arg = a.parse_args()
    base = carica(arg.base)
    print(f"BASE {arg.base}: {riassunto(list(base.values()))}")
    tutte: list[dict] = []
    caricate = []
    for nome in arg.corse:
        corsa = carica(nome)
        caricate.append(corsa)
        esiti = list(corsa.values())
        tutte += esiti
        fatte = contano(esiti)
        comuni = [k for k in base if "corretta" in base[k] and "corretta" in corsa.get(k, {})]
        solo_base = sum(base[k]["corretta"] and not corsa[k]["corretta"] for k in comuni)
        solo_corsa = sum(corsa[k]["corretta"] and not base[k]["corretta"] for k in comuni)
        print(f"\n{nome}: {riassunto(esiti)}; {sum(e['costo_usd'] for e in fatte):.2f} $, "
              f"{sum(e['secondi'] for e in fatte) / max(1, len(fatte)) / 60:.1f} min medi; da giudicare "
              f"{sum(1 for e in esiti if 'corretta' not in e and 'errore' not in e)}, errori {sum('errore' in e for e in esiti)}")
        print(f"  contro la base ({len(comuni)} comuni): solo la base giusta {solo_base}, solo questa {solo_corsa}, "
              f"McNemar p = {mcnemar(solo_base, solo_corsa):.3f}")
        for stato, (giuste, totale) in per_stato(esiti).items():
            if totale:
                print(f"  {stato}: {giuste}/{totale}")
    print(f"\nTUTTE LE CORSE CON I CONTROLLI ({len(arg.corse)}; le ripetizioni non sono indipendenti)")
    for stato, (giuste, totale) in per_stato(tutte).items():
        basso, alto = wilson(giuste, totale)
        print(f"  {stato}: {giuste}/{totale}" + (f" = {giuste / totale:.0%} (95%: {basso:.0%}-{alto:.0%})" if totale else ""))
    print("  segnale: giuste / sbagliate")
    for segnale, (giuste, sbagliate) in tabella_segnali(tutte).items():
        print(f"    {segnale}: {giuste} / {sbagliate}")
    if len(caricate) > 1:
        print("  " + ripetizioni(caricate))
    print("\nREGOLA DI DECISIONE")
    for criterio, passato, numeri in regola(tutte):
        print(f"  {'PASSA' if passato else 'NON PASSA'}: {criterio} — {numeri}")


if __name__ == "__main__":
    main()
