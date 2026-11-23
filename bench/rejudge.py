"""Nuovo giudizio delle corse salvate, con il metro corretto e senza rifare le domande.

Il valore giudicato è quello trovato dal programma (`valore`), non quello estratto da un modello; il confronto tiene
conto del segno. Un vecchio verdetto del giudice resta se riguardava lo stesso valore. Il resto è «da giudicare»:
con `--giudice` lo decide Claude Haiku (centesimi), senza resta fuori dal conto.

Uso: venv/bin/python bench/rejudge.py <corsa> [<corsa> ...] [--giudice] [--domande bench/questions_holdout.json]
Scrive risultati/bench/<corsa>/esiti_rigiudicati.json (letto da statistiche.py al posto di esiti.json).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import judge  # noqa: E402

from chain_research.web import validation  # noqa: E402


def rejudge(outcomes: dict[str, dict], with_judge: bool = False, statuses: dict[str, str] | None = None) -> dict[str, dict]:
    new_items = {}
    for key, old in outcomes.items():
        article = dict(old)
        new_items[key] = article
        if "error" in article:
            continue
        if statuses and key in statuses:
            article["reference_status"] = statuses[key]
        # Lo stato si ricalcola dagli esiti dei controlli salvati, con la regola di adesso: una regola cambiata si può
        # provare sulle corse già fatte senza rifarle. Lo stato scritto dalla corsa resta in `stato_della_corsa`.
        checks = (article.get("chain") or {}).get("checks")
        # con una seconda ricerca lo stato viene dal confronto delle due (web/riprova.py): non si ricalcola da una sola
        if checks and "exams" in checks and not article.get("retry"):
            article.setdefault("run_status", article.get("status"))
            article["status"], reasons = validation.status(checks)
            article["confirmed"] = article["status"] in ("verified", "supported")
        if article.get("without_answer"):  # giusta solo se la catena non ha dato un valore
            article["correct"], article["decided_by"] = not article.get("found"), "unanswerable"
            continue
        value = " ".join(str(article.get("value") or "").split())
        if not value:  # nessun valore dal programma: vale il vecchio giudizio sulla risposta scritta
            continue
        final_old = judge.normalize(article.get("final") or "").strip()
        value_n = judge.normalize(value).strip()
        article["final"] = value
        if judge.normalized_equal(article["wait"], value):
            article["correct"], article["decided_by"] = True, "comparison"
        elif article.get("decided_by") == "judge" and final_old and (
                value_n in final_old or final_old in value_n):
            pass  # il giudice aveva già visto questo valore
        elif with_judge:
            correct, article["decided_by"] = judge.assess(article.get("question", ""), article["wait"], value)
            if correct is None:
                article.pop("correct", None)
            else:
                article["correct"] = correct
        else:
            article.pop("correct", None)
            article["decided_by"] = "pending judgement"
    return new_items


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("runs", nargs="+")
    a.add_argument("--judge", action="store_true", help="i casi dubbi li decide Claude Haiku")
    a.add_argument("--questions", default="", help="file delle domande, per il campo stato_riferimento")
    arg = a.parse_args()
    statuses = {}
    if arg.questions:
        for d in json.loads(Path(arg.questions).read_text(encoding="utf-8"))["questions"]:
            statuses[f"{d['theme']}/{d['seed']}"] = d.get("reference_status", "ok")
    for run in arg.runs:
        folder = Path(run) if Path(run).is_dir() else ROOT / "results" / "bench" / run
        outcomes = json.loads((folder / "outcomes.json").read_text(encoding="utf-8"))
        new_items = rejudge(outcomes, arg.judge, statuses)
        (folder / "rejudged_outcomes.json").write_text(json.dumps(new_items, ensure_ascii=False, indent=1), encoding="utf-8")
        for key, article in new_items.items():
            first, after = outcomes[key].get("correct"), article.get("correct", "pending judgement")
            if first != after and "error" not in article:
                print(f"{run} {key}: {first} → {after} (attesa «{article.get('wait')}», valore «{article.get('value')}»)")
        judged = [v for v in new_items.values() if "correct" in v]
        print(f"{run}: {sum(v['correct'] for v in judged)}/{len(judged)} giuste, "
              f"{sum(1 for v in new_items.values() if v.get('decided_by') == 'pending judgement')} da giudicare")


if __name__ == "__main__":
    main()
