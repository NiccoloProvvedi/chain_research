"""Expected-answer sheet: each expected answer (from Wikidata) next to what the Wikipedia articles of the final entity
say, in Italian and in English. Questions where Wikipedia gives another value do not measure the chain, which reads
Wikipedia: a person must look at them (docs/validation.md, T9).

The right article is recognized by the entity's code (the same as in the question's path), not by its name. Reads only
Wikipedia, with the application's client and timings; does not call Claude.

Usage: venv/bin/python bench/check_references.py [bench/questions_holdout.json] [--write]
       Prints the sheet; with --write adds the field `wikipedia_corroboration` to the questions (not
       `reference_status`: that is decided by a person).
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

# The infobox labels for each requested datum (Italian and English); for birth and death also the start of the article.
LABELS = {
    "elevation": r"altitudine|elevation|altitude",
    "postal code": r"cod\.? postale|codice postale|cap\b|postal code|post ?code|zip",
    "founding date": r"istituzione|istituit|fondazione|fondat|nascita|creazione|established|founded|formation|"
                          r"formed|incorporated|settled|created|inizio",
    "date of birth": r"nascita|nato|born",
    "date of death": r"morte|morto|died",
}
FROM_START = ("date of birth", "date of death", "founding date")


def entity_article(wiki: WikipediaGraph, name: str, qid: str) -> Article | None:
    """The Italian or English article with this code: first the name, then the search results."""
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
    """"equal" if a row contains the expected answer; "different" if there are rows for the datum but none contains it;
    "no rows" if the articles lack that datum; "no article" if the article is not found."""
    if not rows:
        return "no article"
    all_entries = [r for group in rows.values() for r in group]
    if any(validation.value_in_row(wait, r) for r in all_entries):
        return "equal"
    return "different" if all_entries else "no rows"


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
        print(f"\n[{outcome}] {d['theme']}/{d['seed']} — {relation} of {last['name']}: expected \"{d['answer']}\"", flush=True)
        for article, group in rows.items():
            for r in group:
                print(f"    {article}: {r[:230]}")
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if arg.write:
        Path(arg.file).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
