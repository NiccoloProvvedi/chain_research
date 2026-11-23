"""Rapporto sui controlli dei risultati: quanto vale lo stato di una risposta e che cosa segnala ogni controllo.

Legge le corse salvate (il nuovo giudizio di `rigiudica.py`, se c'è) e non chiama né la rete né Claude.

Uso: venv/bin/python bench/report.py <corsa di base> <corsa> [<corsa> ...]
     La prima corsa è il confronto (codice di prima); le altre sono ripetizioni della stessa prova con i controlli.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stats import _equal, load, counted, mcnemar, summarize, wilson  # noqa: E402

STATUSES = ("verified", "supported", "contested", "uncertain")


def signals(e: dict) -> list[str]:
    """I controlli che hanno segnalato qualcosa su questa risposta (catena.controlli dell'esito)."""
    c = (e.get("chain") or {}).get("checks") or {}
    exams = c.get("exams") or {}
    reading, corroboration, review = (exams.get(k) or {} for k in ("second_reading", "corroboration", "review"))
    hits = []
    if c.get("weak_links"):
        hits.append("anello debole")
    if c.get("value_source") == "C":
        hits.append("valore da un estratto")
    if c.get("disagreements"):
        hits.append("riquadro che indica un altro candidato")
    if reading.get("outcome") in ("disagree", "not found again"):
        hits.append(f"seconda lettura: {reading['outcome']}")
    if corroboration.get("outcome") in ("disagree", "single source", "agree"):
        hits.append(f"altre fonti: {corroboration['outcome']}")
    if review.get("objections"):
        hits.append("revisione: obiezione con riga")
    elif review.get("outcome") == "done":
        hits.append(f"revisione: regge {review.get('holds')}")
    if not exams or all(x.get("outcome") == "not done" for x in (reading, corroboration, review)):
        hits.append("esami non fatti")
    return hits


def signals_table(outcomes: list[dict]) -> dict[str, tuple[int, int]]:
    """Segnale → (risposte giuste, risposte sbagliate) tra quelle con un valore."""
    counts: dict[str, list[int]] = {}
    for e in counted(outcomes):
        if not e.get("value"):
            continue
        for s in signals(e):
            counts.setdefault(s, [0, 0])[0 if e["correct"] else 1] += 1
    return {s: (g, b) for s, (g, b) in sorted(counts.items())}


def per_status(outcomes: list[dict]) -> dict[str, tuple[int, int]]:
    """Stato → (giuste, totale)."""
    done = counted(outcomes)
    return {s: (sum(e["correct"] for e in done if e.get("status") == s), sum(e.get("status") == s for e in done))
            for s in STATUSES}


def rule(outcomes: list[dict]) -> list[tuple[str, bool, str]]:
    """La regola di decisione fissata prima delle corse (docs/validation.md): (criterio, passato, numeri)."""
    statuses = per_status(outcomes)
    right, total = statuses["verified"]
    low = wilson(right, total)[0]
    good = tuple(map(sum, zip(statuses["verified"], statuses["supported"], strict=True)))
    doubtful = tuple(map(sum, zip(statuses["contested"], statuses["uncertain"], strict=True)))
    share = lambda x: x[0] / x[1] if x[1] else 0.0  # noqa: E731
    return [
        ("verificata: giuste almeno il 95% (limite inferiore almeno 85%)",
         total > 0 and right / total >= 0.95 and low >= 0.85, f"{right}/{total}, limite inferiore {low:.0%}"),
        ("verificata e sostenuta più giuste di contestata e incerta",
         good[1] > 0 and doubtful[1] > 0 and share(good) - share(doubtful) >= 0.15,
         f"{good[0]}/{good[1]} contro {doubtful[0]}/{doubtful[1]}"),
    ]


def repetitions(runs: list[dict[str, dict]]) -> str:
    common = [k for k in runs[0] if all("value" in c.get(k, {}) for c in runs)]
    value = sum(all(_equal(c[k]["value"], runs[0][k]["value"]) for c in runs) for k in common)
    status = sum(len({c[k].get("status") for c in runs}) == 1 for k in common)
    return f"{len(common)} domande in tutte le corse: stesso valore {value}, stesso stato {status}"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("base")
    a.add_argument("runs", nargs="+")
    arg = a.parse_args()
    base = load(arg.base)
    print(f"BASE {arg.base}: {summarize(list(base.values()))}")
    all_entries: list[dict] = []
    loaded = []
    for name in arg.runs:
        run = load(name)
        loaded.append(run)
        outcomes = list(run.values())
        all_entries += outcomes
        done = counted(outcomes)
        common = [k for k in base if "correct" in base[k] and "correct" in run.get(k, {})]
        base_only = sum(base[k]["correct"] and not run[k]["correct"] for k in common)
        run_only = sum(run[k]["correct"] and not base[k]["correct"] for k in common)
        print(f"\n{name}: {summarize(outcomes)}; {sum(e['cost_usd'] for e in done):.2f} $, "
              f"{sum(e['seconds'] for e in done) / max(1, len(done)) / 60:.1f} min medi; da giudicare "
              f"{sum(1 for e in outcomes if 'correct' not in e and 'error' not in e)}, errori {sum('error' in e for e in outcomes)}")
        print(f"  contro la base ({len(common)} comuni): solo la base giusta {base_only}, solo questa {run_only}, "
              f"McNemar p = {mcnemar(base_only, run_only):.3f}")
        for status, (right, total) in per_status(outcomes).items():
            if total:
                print(f"  {status}: {right}/{total}")
    print(f"\nTUTTE LE CORSE CON I CONTROLLI ({len(arg.runs)}; le ripetizioni non sono indipendenti)")
    for status, (right, total) in per_status(all_entries).items():
        low, high = wilson(right, total)
        print(f"  {status}: {right}/{total}" + (f" = {right / total:.0%} (95%: {low:.0%}-{high:.0%})" if total else ""))
    print("  segnale: giuste / sbagliate")
    for signal, (right, wrong) in signals_table(all_entries).items():
        print(f"    {signal}: {right} / {wrong}")
    if len(loaded) > 1:
        print("  " + repetitions(loaded))
    print("\nREGOLA DI DECISIONE")
    for criterion, passed, numbers in rule(all_entries):
        print(f"  {'PASSA' if passed else 'NON PASSA'}: {criterion} — {numbers}")


if __name__ == "__main__":
    main()
