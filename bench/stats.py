"""Statistiche delle corse del banco: intervalli di Wilson, per tema, costi; confronto A/B con il test di McNemar.

Uso: venv/bin/python bench/stats.py <corsa> [<corsa> ...]          (cartelle di risultati/banco o loro nomi)
     venv/bin/python bench/stats.py --confronta <corsa A> <corsa B>
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Z = 1.959964


def wilson(right: int, total: int) -> tuple[float, float]:
    """Intervallo di confidenza al 95% di una proporzione."""
    if not total:
        return 0.0, 0.0
    p = right / total
    d = 1 + Z * Z / total
    centre = (p + Z * Z / (2 * total)) / d
    margin = Z * math.sqrt(p * (1 - p) / total + Z * Z / (4 * total * total)) / d
    return max(0.0, centre - margin), min(1.0, centre + margin)


def mcnemar(only_to: int, only_b: int) -> float:
    """p bilaterale esatto del test di McNemar sulle domande in cui le due corse differiscono."""
    n = only_to + only_b
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(only_to, only_b) + 1)) / 2 ** n)


def load(run: str) -> dict[str, dict]:
    """Gli esiti della corsa; se c'è, il nuovo giudizio di `rigiudica.py` (stesso metro per tutte le corse)."""
    folder = Path(run) if Path(run).is_dir() else ROOT / "results" / "bench" / run
    new = folder / "rejudged_outcomes.json"
    return json.loads((new if new.is_file() else folder / "outcomes.json").read_text(encoding="utf-8"))


def counted(outcomes: list[dict]) -> list[dict]:
    """Le domande giudicate e con una risposta attesa buona."""
    return [e for e in outcomes if "correct" in e and e.get("reference_status", "ok") == "ok"]


def _equal(a: object, b: object) -> bool:
    """Lo stesso valore anche se scritto in un altro modo («−2 m s.l.m.», «−2 metri»); due «non trovata» sono uguali."""
    import sys
    sys.path.insert(0, str(ROOT))
    from chain_research.web.validation import same_value
    return (not a and not b) or same_value(str(a or ""), str(b or ""))


def repetitions(runs: list[dict[str, dict]]) -> str:
    """Quante domande danno lo stesso valore e lo stesso «confermata» in tutte le corse che le hanno fatte."""
    common = [k for k in runs[0] if all("value" in c.get(k, {}) for c in runs)]
    value = sum(all(_equal(c[k]["value"], runs[0][k]["value"]) for c in runs) for k in common)
    seal = sum(len({bool(c[k].get("confirmed")) for c in runs}) == 1 for k in common)
    return f"ripetendo ({len(common)} domande comuni): stesso valore {value}, stesso «confermata» {seal}"


def summarize(outcomes: list[dict]) -> str:
    done = counted(outcomes)
    right = sum(e["correct"] for e in done)
    low, high = wilson(right, len(done))
    return f"{right}/{len(done)} = {right / max(1, len(done)):.1%} (95%: {low:.1%}-{high:.1%})"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("runs", nargs="+")
    a.add_argument("--compare", action="store_true")
    arg = a.parse_args()
    if arg.confronta:
        first, second = load(arg.runs[0]), load(arg.runs[1])
        common = [k for k in first if "correct" in first[k] and "correct" in second.get(k, {})]
        only_to = [k for k in common if first[k]["correct"] and not second[k]["correct"]]
        only_b = [k for k in common if second[k]["correct"] and not first[k]["correct"]]
        print(f"{len(common)} domande comuni. Solo A giuste: {len(only_to)}, solo B: {len(only_b)}. "
              f"McNemar p = {mcnemar(len(only_to), len(only_b)):.3f}")
        print("solo A:", ", ".join(only_to) or "-")
        print("solo B:", ", ".join(only_b) or "-")
        return
    all_entries: list[dict] = []
    for run in arg.runs:
        outcomes = list(load(run).values())
        all_entries += outcomes
        done = counted(outcomes)
        errors = sum("error" in e for e in outcomes)
        outside = sum("correct" in e for e in outcomes) - len(done)
        pending = sum(1 for e in outcomes if "correct" not in e and "error" not in e)
        print(f"{run}: {summarize(outcomes)}, {sum(e['cost_usd'] for e in done):.2f} $, "
              f"{sum(e['seconds'] for e in done) / max(1, len(done)) / 60:.1f} min medi, {errors} errori, "
              f"{sum(bool(e.get('confirmed')) for e in done)} confermate, "
              f"{sum(e.get('decided_by') == 'judge' for e in done)} decise dal giudice, {pending} da giudicare, "
              f"{outside} fuori dal conto (risposta attesa non buona)")
    if len(arg.runs) > 1:
        print(repetitions([load(c) for c in arg.runs]))
        print(f"TOTALE (le ripetizioni non sono indipendenti): {summarize(all_entries)}")
    themes: dict[str, list[dict]] = defaultdict(list)
    for e in all_entries:
        themes[e.get("theme", "?")].append(e)
    for theme, outcomes in sorted(themes.items()):
        print(f"  {theme}: {summarize(outcomes)}")
    confirmed = [e for e in counted(all_entries) if e.get("confirmed")]
    other_ones = [e for e in counted(all_entries) if not e.get("confirmed")]
    print(f"  catena tutta confermata: {summarize(confirmed)}")
    print(f"  catena non confermata: {summarize(other_ones)}")
    for status in ("verified", "supported", "contested", "uncertain"):
        with_status = [e for e in counted(all_entries) if e.get("status") == status]
        if with_status:
            print(f"  stato {status}: {summarize(with_status)}")


if __name__ == "__main__":
    main()
