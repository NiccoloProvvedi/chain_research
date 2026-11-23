"""Scheda delle risposte attese: ogni risposta attesa (da Wikidata) accanto a quello che dicono le voci di Wikipedia
dell'entità finale, in italiano e in inglese. Le domande in cui Wikipedia dà un altro valore non misurano la catena,
che legge Wikipedia: vanno guardate da una persona (docs/validation.md, T9).

La voce giusta si riconosce dal codice dell'entità (lo stesso del percorso della domanda), non dal nome. Legge solo
Wikipedia, con il client e i tempi dell'applicazione; non chiama Claude.

Uso: venv/bin/python bench/check_references.py [bench/questions_holdout.json] [--scrivi]
     Stampa la scheda; con --scrivi aggiunge alle domande il campo `riscontro_wikipedia` (non `stato_riferimento`:
     quello lo decide una persona).
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chain_research.web import validation  # noqa: E402
from chain_research.web.sources.wikipedia_graph import Article, WikipediaGraph, interval_per  # noqa: E402

# Le etichette del riquadro per ogni dato chiesto (italiano e inglese); per nascita e morte anche l'inizio della voce.
LABELS = {
    "altitudine": r"altitudine|elevation|altitude",
    "codice postale": r"cod\.? postale|codice postale|cap\b|postal code|post ?code|zip",
    "data di fondazione": r"istituzione|istituit|fondazione|fondat|nascita|creazione|established|founded|formation|"
                          r"formed|incorporated|settled|created|inizio",
    "data di nascita": r"nascita|nato|born",
    "data di morte": r"morte|morto|died",
}
FROM_START = ("data di nascita", "data di morte", "data di fondazione")


def entity_article(wiki: WikipediaGraph, name: str, qid: str) -> Article | None:
    """La voce in italiano o in inglese con questo codice: prima il nome, poi i risultati della ricerca."""
    for language in ("it", "en"):
        proofs = [name] + [x["title"] for x in wiki.search(name, language, n=4)]
        for title in dict.fromkeys(proofs):
            article = wiki.article(title, language)
            if article is not None and article.qid == qid:
                return article
    return None


def datum_rows(article: Article, relation: str) -> list[str]:
    labels = re.compile(LABELS[relation], re.I)
    rows = [r.text for r in article.infobox() if labels.search(r.text.split(":")[0])]
    return rows + ([article.start[:500]] if relation in FROM_START else [])


def corroboration(wait: str, rows: dict[str, list[str]]) -> str:
    """«uguale» se una riga contiene la risposta attesa; «diversa» se le righe del dato ci sono ma nessuna la contiene;
    «senza righe» se le voci non hanno quel dato; «senza voce» se la voce non si trova."""
    if not rows:
        return "senza voce"
    all_entries = [r for group in rows.values() for r in group]
    if any(validation.value_in_row(wait, r) for r in all_entries):
        return "uguale"
    return "diversa" if all_entries else "senza righe"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("file", nargs="?", default=str(ROOT / "bench" / "questions_holdout.json"))
    a.add_argument("--write", action="store_true")
    arg = a.parse_args()
    data = json.loads(Path(arg.file).read_text(encoding="utf-8"))
    wiki = WikipediaGraph(interval_s=interval_per(1))
    counts: dict[str, int] = {}
    for d in data["questions"]:
        last, relation = d["path"][-1], d["attribute"]["relation"]
        first = entity_article(wiki, last["name"], last["qid"])
        rows: dict[str, list[str]] = {}
        if first is not None:
            articles = [first]
            another = "en" if first.language == "it" else "it"
            if another in first.languages:
                second = wiki.article(first.languages[another], another)
                articles += [second] if second is not None else []
            rows = {f"{v.title} ({v.language})": datum_rows(v, relation) for v in articles}
        outcome = corroboration(d["answer"], rows)
        counts[outcome] = counts.get(outcome, 0) + 1
        d["wikipedia_corroboration"] = outcome
        print(f"\n[{outcome}] {d['theme']}/{d['seed']} — {relation} di {last['name']}: attesa «{d['answer']}»", flush=True)
        for article, group in rows.items():
            for r in group:
                print(f"    {article}: {r[:230]}")
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if arg.write:
        Path(arg.file).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
