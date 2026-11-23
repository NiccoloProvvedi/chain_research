"""Banco di prova: le 40 domande a catena (bench/questions.json) risolte con lo stesso esecutore dell'app.

Uso:  venv/bin/python bench/bench.py --nome prova-1 [--semi "informatica/Linux kernel,..."] [--parallelo 3]
      [--nota "cosa si prova"] [--budget 12]
Le domande vanno in parallelo, ognuna nel suo processo (`python -m chain_research.question`) con il suo profilo del
browser; Wikidata è vietata (le domande vengono da lì). Esiti in risultati/bench/<nome>/esiti.json (si riprende
dalle domande non fatte) e una riga in bench/LOG.md.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import judge  # noqa: E402

from chain_research import configuration  # noqa: E402
from chain_research.model import claude_json  # noqa: E402
from chain_research.web.sources.wikipedia_graph import interval_per  # noqa: E402


MAX_WAIT_S = 14 * 3600


def one_question(d: dict, profile: str, parallel: int, record: Path | None = None) -> dict:
    # Una registrazione per il rigioco è di una ricerca sola: la seconda ricerca delle risposte incerte resta spenta.
    options = {"without_wikidata": True, "wikipedia_interval": interval_per(parallel), "model_checks": True,
               "second_research": record is None}
    request = {"question": d["question"], "profile": str(configuration.folder("profiles_folder") / profile),
                 "pieces_folder": str(ROOT / "results" / "bench" / ".pieces"), "options": options}
    environment = dict(os.environ)
    folder, today = None, f"{datetime.now():%Y-%m-%d}"
    if record is not None:  # ogni domanda nella sua cartella; PYTHONHASHSEED e data uguali anche nel rigioco
        folder = record / re.sub(r"\W+", "-", f"{d['theme']}-{d['seed']}")
        environment.update(CHAIN_RECORD=str(folder), CHAIN_TODAY=today, PYTHONHASHSEED="0")
    output = subprocess.run([str(ROOT / "venv" / "bin" / "python"), "-m", "chain_research.question"], cwd=ROOT,
                            input=json.dumps(request, ensure_ascii=False) + "\n", capture_output=True, text=True,
                            # l'attesa del limite di sessione (fino a 6 ore) sta dentro questo tempo: 12/11, tre
                            # domande di controllo perse a 3 ore mentre aspettavano il rinnovo
                            timeout=MAX_WAIT_S, env=environment)
    for row in reversed(output.stdout.splitlines()):
        message = json.loads(row)
        if "outcome" in message:
            outcome = message["outcome"]
            if folder is not None:
                (folder / "expected.json").write_text(json.dumps({
                    "question": d["question"], "options": options, "today": today, "value": outcome.get("value"),
                    "found": outcome.get("found"), "end_reason": outcome.get("end_reason"),
                    "actions": outcome.get("chain", {}).get("actions"), "answer": outcome.get("answer")},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            return outcome
        if "error" in message:
            raise RuntimeError(message["error"])
    raise RuntimeError(f"nessun esito (codice {output.returncode})")


def assess_article(article: dict) -> None:
    """Aggiunge `finale`, `corretta` e `deciso_da`. Se il giudice non risponde la risposta trovata resta, segnata
    `da_giudicare`: alla ripresa si rifà solo il giudizio, non la domanda."""
    article.pop("pending_judgement", None)
    if article.get("without_answer"):  # la domanda non ha risposta: giusto è non darne una (nessun modello a giudicare)
        article["final"] = " ".join(str(article.get("value") or "").split())
        article["correct"], article["decided_by"] = not article.get("found"), "unanswerable"
        return
    try:
        article["final"] = judge.final_of(article, article["question"])
        correct, article["decided_by"] = judge.assess(article["question"], article["wait"], article["final"])
    except claude_json.ClaudeError as error:
        article.setdefault("final", "")
        correct, article["decided_by"] = None, f"giudice in errore: {error}"[:200]
    if correct is None:
        article["pending_judgement"] = True
    else:
        article["correct"] = correct


def counted(outcomes: list[dict]) -> list[dict]:
    """Le domande giudicate con una risposta attesa buona (`stato_riferimento`: docs/validation.md §3)."""
    return [e for e in outcomes if "correct" in e and e.get("reference_status", "ok") == "ok"]


def main() -> None:
    a = argparse.ArgumentParser(description="Banco di prova delle domande a catena")
    a.add_argument("--name", required=True)
    a.add_argument("--seeds", default="", help="solo queste domande: tema/seme separati da virgola")
    a.add_argument("--parallel", type=int, default=3)
    a.add_argument("--budget", type=float, default=12.0, help="dollari massimi di Claude per tutta la corsa")
    a.add_argument("--note", default="")
    a.add_argument("--questions", default="", help="file di domande diverso da bench/questions.json")
    a.add_argument("--record", default="", help="cartella in cui registrare tutto per il rigioco (test_rigioco.py)")
    arg = a.parse_args()
    folder = ROOT / "results" / "bench" / arg.name
    folder.mkdir(parents=True, exist_ok=True)
    outcomes_file = folder / "outcomes.json"
    outcomes = json.loads(outcomes_file.read_text(encoding="utf-8")) if outcomes_file.is_file() else {}
    questions_file = Path(arg.questions) if arg.questions else ROOT / "bench" / "questions.json"
    questions = json.loads(questions_file.read_text(encoding="utf-8"))["questions"][:None if arg.questions else 40]
    record = Path(arg.record).resolve() if arg.record else None
    if arg.seeds:
        wanted = {s.strip() for s in arg.seeds.split(",") if s.strip()}
        questions = [d for d in questions if f"{d['theme']}/{d['seed']}" in wanted]
    for article in outcomes.values():  # giudizi rimasti in sospeso (limite di sessione, errore del giudice)
        if article.get("pending_judgement"):
            assess_article(article)
    from_do = [d for d in questions if not {"correct", "pending_judgement"} & set(outcomes.get(f"{d['theme']}/{d['seed']}", {}))]
    print(f"{len(from_do)} domande da fare su {len(questions)}; risultati in {folder}", flush=True)
    mutex = threading.Lock()
    profiles = [f"bench_{i + 1}" for i in range(arg.parallel)]

    def work(d: dict) -> None:
        key = f"{d['theme']}/{d['seed']}"
        with mutex:
            if sum(e.get("cost_usd", 0) for e in outcomes.values()) >= arg.budget:
                return
            profile = profiles.pop(0)
        try:
            outcome = one_question(d, profile, arg.parallel, record)
            article = {"theme": d["theme"], "seed": d["seed"], "question": d["question"], "wait": d["answer"],
                    "reference_status": d.get("reference_status", "ok"),
                    "without_answer": bool(d.get("without_answer")), **outcome}
            assess_article(article)
            print(f"{'?' if 'correct' not in article else '✓' if article['correct'] else '✗'} {key}: attesa "
                  f"«{d['answer']}», finale «{article['final'][:80]}» ({outcome['seconds']} s, {outcome['cost_usd']} $)",
                  flush=True)
        except Exception as error:  # noqa: BLE001 - una domanda fallita non ferma la corsa
            article = {"theme": d["theme"], "seed": d["seed"], "error": f"{type(error).__name__}: {error}"[:500]}
            print(f"ERRORE {key}: {error}", flush=True)
        finally:
            with mutex:
                profiles.append(profile)
        with mutex:
            outcomes[key] = article
            outcomes_file.write_text(json.dumps(outcomes, ensure_ascii=False, indent=1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=arg.parallel) as executor:
        list(executor.map(work, from_do))
    done = counted([outcomes.get(f"{d['theme']}/{d['seed']}", {}) for d in questions])
    right = sum(e["correct"] for e in done)
    confirmed = [e for e in done if e.get("confirmed")]
    from_judge = sum(e.get("decided_by") == "judge" for e in done)
    pending = sum(1 for e in outcomes.values() if e.get("pending_judgement"))
    minutes = round(sum(e["seconds"] for e in done) / max(1, len(done)) / 60, 1)
    cost = round(sum(e["cost_usd"] for e in done), 3)
    errors = sum(1 for d in questions if "error" in outcomes.get(f"{d['theme']}/{d['seed']}", {}))
    print(f"RIEPILOGO {right}/{len(done)} giuste, {minutes} minuti medi, {cost} $, {errors} errori, confermate giuste "
          f"{sum(e['correct'] for e in confirmed)}/{len(confirmed)}, decise dal giudice {from_judge}, da giudicare "
          f"{pending}", flush=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT).stdout.strip() or "?"
    with (ROOT / "bench" / "LOG.md").open("a", encoding="utf-8") as f:
        f.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {arg.nota or arg.name} | {commit} | {len(questions)} domande | "
                f"{right}/{len(done)} | {minutes} | {cost} | {errors} | "
                f"{sum(e['correct'] for e in confirmed)}/{len(confirmed)} | {from_judge} |\n")


if __name__ == "__main__":
    main()
