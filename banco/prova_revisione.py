"""Prova delle istruzioni della revisione sulle corse salvate: si rifà solo la revisione, non la domanda.

Per ogni risposta con un valore di una corsa salvata (dal 12/11: l'esito ha le righe della soluzione) si chiede di
nuovo la revisione, con le istruzioni di adesso, e si guarda che cosa direbbe della premessa. Costa circa 2 centesimi
a risposta; non misura le righe delle altre voci (non sono salvate): prima di fidarsi serve una corsa vera.

Uso: venv/bin/python banco/prova_revisione.py <corsa> [<corsa> ...] [--parallelo 3]
Le revisioni restano in risultati/banco/<corsa>/prova_revisione.json.
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "banco"))

from statistiche import carica  # noqa: E402

from ricerca_catena import configurazione  # noqa: E402
from ricerca_catena.modello import claude_json, pulizia  # noqa: E402
from ricerca_catena.modello.pensatore import SCHEMI, errori_schema, istruzioni  # noqa: E402
from ricerca_catena.web import validazione  # noqa: E402


def richiesta(voce: dict) -> tuple[dict, list]:
    """I dati per la revisione ricostruiti dall'esito salvato: la strada è la riga «Spiegazione» della risposta."""
    righe = [SimpleNamespace(testo=p["riga"], url=p["url"]) for p in voce["catena"]["prove"]]
    spiegazione = re.search(r"\*\*Spiegazione:?\*\*:?\s*(.+)", voce.get("risposta") or "")
    valore = voce.get("valore") or voce["catena"].get("valore_scartato")
    return {"domanda": voce["domanda"][:1500], "strada": (spiegazione.group(1) if spiegazione else "")[:800],
            "risposta": str(valore), "righe": "\n".join(f"[{i}] {r.testo[:700]}" for i, r in enumerate(righe, start=1))}, righe


def _una(dati: dict, righe: list) -> dict:
    try:
        esito, _ = claude_json.chiedi(istruzioni("revisione"), pulizia.dati(dati), SCHEMI["revisione"], "revisione",
                                      modello=configurazione.carica()["modello_revisione"], max_budget_usd=0.2)
    except claude_json.LimiteSessione:
        raise
    except claude_json.ErroreClaude:
        return {"esito": "non fatta"}
    return {"esito": "non fatta"} if errori_schema(esito, SCHEMI["revisione"]) else validazione.esamina_revisione(esito, righe)


def rivedi(voce: dict) -> dict | None:
    """Due revisioni, come nella catena: quella unita (la più severa) e, in `singole`, il giudizio di ognuna."""
    dati, righe = richiesta(voce)
    singole = [_una(dati, righe) for _ in range(2)]
    unita = validazione.unisci_revisioni(singole)
    return {**unita, "singole": [r.get("regge") for r in singole]} if unita.get("esito") == "fatta" else None


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("corse", nargs="+")
    a.add_argument("--parallelo", type=int, default=3)
    arg = a.parse_args()
    for corsa in arg.corse:
        voci = {k: v for k, v in carica(corsa).items()
                if "corretta" in v and (v.get("catena") or {}).get("prove")
                and (v.get("valore") or v["catena"].get("valore_scartato"))}
        with ThreadPoolExecutor(max_workers=arg.parallelo) as esecutore:
            esiti = dict(zip(voci, esecutore.map(rivedi, voci.values()), strict=True))
        cartella = Path(corsa) if Path(corsa).is_dir() else RADICE / "risultati" / "banco" / corsa
        (cartella / "prova_revisione.json").write_text(json.dumps(
            {k: {"corretta": bool(voci[k]["corretta"]) and bool(voci[k].get("valore")), "revisione": e,
                 "anelli_deboli": voci[k]["catena"]["controlli"].get("anelli_deboli")} for k, e in esiti.items()},
            ensure_ascii=False, indent=1), encoding="utf-8")
        conti: dict[tuple, int] = {}
        for chiave, esame in esiti.items():
            premessa = (esame or {}).get("premessa", {}).get("esito", "revisione fallita")
            giusta = bool(voci[chiave]["corretta"]) and bool(voci[chiave].get("valore"))
            conti[(premessa, giusta)] = conti.get((premessa, giusta), 0) + 1
            if premessa == "falsa":
                print(f"  {'GIUSTA PERSA' if giusta else 'sbagliata fermata'} {chiave}: «{esame['premessa']['cosa']}» "
                      f"(«{esame['premessa']['citazione'][:90]}»)", flush=True)
        print(f"{corsa}: " + ", ".join(f"premessa {p}, risposta {'giusta' if g else 'sbagliata'}: {n}"
                                        for (p, g), n in sorted(conti.items(), key=str)), flush=True)


if __name__ == "__main__":
    main()
