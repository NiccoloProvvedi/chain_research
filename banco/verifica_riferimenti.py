"""Scheda delle risposte attese: ogni risposta attesa (da Wikidata) accanto a quello che dicono le voci di Wikipedia
dell'entità finale, in italiano e in inglese. Le domande in cui Wikipedia dà un altro valore non misurano la catena,
che legge Wikipedia: vanno guardate da una persona (docs/solidita-e-validazione.md, T9).

La voce giusta si riconosce dal codice dell'entità (lo stesso del percorso della domanda), non dal nome. Legge solo
Wikipedia, con il client e i tempi dell'applicazione; non chiama Claude.

Uso: venv/bin/python banco/verifica_riferimenti.py [banco/domande_holdout.json] [--scrivi]
     Stampa la scheda; con --scrivi aggiunge alle domande il campo `riscontro_wikipedia` (non `stato_riferimento`:
     quello lo decide una persona).
"""
import argparse
import json
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))

from ricerca_catena.web import validazione  # noqa: E402
from ricerca_catena.web.fonti.wikipedia_grafo import Voce, WikipediaGrafo, intervallo_per  # noqa: E402

# Le etichette del riquadro per ogni dato chiesto (italiano e inglese); per nascita e morte anche l'inizio della voce.
ETICHETTE = {
    "altitudine": r"altitudine|elevation|altitude",
    "codice postale": r"cod\.? postale|codice postale|cap\b|postal code|post ?code|zip",
    "data di fondazione": r"istituzione|istituit|fondazione|fondat|nascita|creazione|established|founded|formation|"
                          r"formed|incorporated|settled|created|inizio",
    "data di nascita": r"nascita|nato|born",
    "data di morte": r"morte|morto|died",
}
DALL_INIZIO = ("data di nascita", "data di morte", "data di fondazione")


def voce_dell_entita(wiki: WikipediaGrafo, nome: str, qid: str) -> Voce | None:
    """La voce in italiano o in inglese con questo codice: prima il nome, poi i risultati della ricerca."""
    for lingua in ("it", "en"):
        prove = [nome] + [x["titolo"] for x in wiki.cerca(nome, lingua, n=4)]
        for titolo in dict.fromkeys(prove):
            voce = wiki.voce(titolo, lingua)
            if voce is not None and voce.qid == qid:
                return voce
    return None


def righe_del_dato(voce: Voce, relazione: str) -> list[str]:
    etichette = re.compile(ETICHETTE[relazione], re.I)
    righe = [r.testo for r in voce.riquadro() if etichette.search(r.testo.split(":")[0])]
    return righe + ([voce.inizio[:500]] if relazione in DALL_INIZIO else [])


def riscontro(attesa: str, righe: dict[str, list[str]]) -> str:
    """«uguale» se una riga contiene la risposta attesa; «diversa» se le righe del dato ci sono ma nessuna la contiene;
    «senza righe» se le voci non hanno quel dato; «senza voce» se la voce non si trova."""
    if not righe:
        return "senza voce"
    tutte = [r for gruppo in righe.values() for r in gruppo]
    if any(validazione.valore_nella_riga(attesa, r) for r in tutte):
        return "uguale"
    return "diversa" if tutte else "senza righe"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("file", nargs="?", default=str(RADICE / "banco" / "domande_holdout.json"))
    a.add_argument("--scrivi", action="store_true")
    arg = a.parse_args()
    dati = json.loads(Path(arg.file).read_text(encoding="utf-8"))
    wiki = WikipediaGrafo(intervallo_s=intervallo_per(1))
    conti: dict[str, int] = {}
    for d in dati["domande"]:
        ultima, relazione = d["percorso"][-1], d["attributo"]["relazione"]
        prima = voce_dell_entita(wiki, ultima["nome"], ultima["qid"])
        righe: dict[str, list[str]] = {}
        if prima is not None:
            voci = [prima]
            altra = "en" if prima.lingua == "it" else "it"
            if altra in prima.lingue:
                seconda = wiki.voce(prima.lingue[altra], altra)
                voci += [seconda] if seconda is not None else []
            righe = {f"{v.titolo} ({v.lingua})": righe_del_dato(v, relazione) for v in voci}
        esito = riscontro(d["risposta"], righe)
        conti[esito] = conti.get(esito, 0) + 1
        d["riscontro_wikipedia"] = esito
        print(f"\n[{esito}] {d['tema']}/{d['seme']} — {relazione} di {ultima['nome']}: attesa «{d['risposta']}»", flush=True)
        for voce, gruppo in righe.items():
            for r in gruppo:
                print(f"    {voce}: {r[:230]}")
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(conti.items())))
    if arg.scrivi:
        Path(arg.file).write_text(json.dumps(dati, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
