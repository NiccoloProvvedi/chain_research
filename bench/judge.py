"""Bench judge: extracts the value given as the answer and compares it with the expected one.

First the normalized comparison (the expected answer appears in the answer), then Claude Haiku as a judge with two
examples (as in DeepDive). Claude is called without tools, as in the rest of the project."""
import re
import unicodedata

from chain_research.model import claude_json

MODEL = "haiku"
EXTRACT = ("Extract from the TEXT the value it gives as the answer to the QUESTION: a single line with a single value "
           "(name, date or number), copied from the text. Do not check whether it is right, nor whether the person or "
           "place is the one in the question: just extract. Write only NOT FOUND if the text says it does not have the "
           "answer, or if it gives two or more different values as the answer without choosing one.")
JUDGE_SYSTEM_PROMPT = (
    "You are a judge. I give you a question, the expected answer and a model's answer. Tell me whether the model's "
    "answer indicates the same thing as the expected answer (same person, same place, same date to the day, same "
    "number; language, format and unit of measurement may differ). An answer vaguer than the expected one (only the "
    "year instead of the day, a range) is NOT correct.\n\n"
    "Example 1 — expected: \"24 July 1973\"; model: \"July 24, 1973\" → {\"correct\": true}\n"
    "Example 2 — expected: \"Mitchell Baker\"; model: \"Brendan Eich\" → {\"correct\": false}\n\n"
    'Reply only with JSON: {"correct": true} or {"correct": false}.')


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", " ", "".join(c for c in text if not unicodedata.combining(c))))


# A minus sign before a number: not the dash of a range ("00118–00199") nor of a date ("1973-07-24").
_NEGATIVE = re.compile(r"(?<![\w.,])(?<!\d\s)[−–-]\s?(\d[\d.,]*)")
_BELOW_SEA_LEVEL = re.compile(r"sotto il livello del mare|below sea level", re.I)


def _digits(number: str) -> str:
    return number.rstrip(".,")


def _negatives(text: str) -> set[str]:
    return {_digits(n) for n in _NEGATIVE.findall(str(text))}


def normalized_equal(wait: str, date: str) -> bool:
    """The expected answer appears in the given answer, with the same sign on the numbers (11 Nov, control questions:
    "2 m" expected, "−2 m a.s.l." given, counted as right)."""
    wait_n, date_n = normalize(wait).strip(), normalize(date)
    if not wait_n or re.search(rf"\b{re.escape(wait_n)}\b", date_n) is None:
        return False
    under = _negatives(date) | ({_digits(n) for n in re.findall(r"\d[\d.,]*", str(date))}
                               if _BELOW_SEA_LEVEL.search(str(date)) else set())
    return all((n in _negatives(wait)) == (n in under) for n in map(_digits, re.findall(r"\d[\d.,]*", str(wait))))


def _ask(instructions: str, text: str) -> str:
    """Claude errors propagate: a failed call is not a wrong answer (11 Nov)."""
    return str(claude_json.ask(instructions, text, None, "judge", model=MODEL)[0])


def short_answer(text: str) -> str:
    """The "Answer" line (or the first non-empty line); "Risposta" in runs saved before the translation."""
    found = re.search(r"\*\*(?:Answer|Risposta):?\*\*:?\s*(.+)", text)
    if found:
        return found.group(1).strip()[:500]
    return next((r.strip() for r in text.splitlines() if r.strip()), "")[:500]


def extract(question: str, text: str) -> str:
    outcome = _ask(EXTRACT, f"QUESTION: {question}\n\nDOCUMENT:\n{text[:40000]}")
    # The first line with a value: no preambles such as "From the text provided, the value … is:".
    rows = [r.strip().strip("*").strip() for r in outcome.splitlines() if r.strip()]
    row = next((r for r in rows if not r.endswith(":")), rows[0] if rows else "")
    return "" if re.match(r"(NON|NOT)\s+(TROVATA|FOUND)", row.upper()) else row[:300]


def final_of(outcome: dict, question: str) -> str:
    """The value to judge: the one found by the program; only if missing, the one Haiku extracts from the "Answer"
    line (11 Nov: "00118–00199" and "15 October 1870", right, counted wrong because the extraction was empty)."""
    value = " ".join(str(outcome.get("value") or "").split())
    return value or extract(question, short_answer(outcome.get("answer") or ""))


def assess(question: str, wait: str, date: str) -> tuple[bool | None, str]:
    """(correct, who decided): "comparison", "judge", "empty", "judge gave no valid answer"; (None, "judge error")
    if Claude did not answer: the question stays pending judgement. The session limit propagates to the caller."""
    if not date:
        return False, "empty"
    if normalized_equal(wait, date):
        return True, "comparison"
    text = f"QUESTION: {question}\nEXPECTED ANSWER: {wait}\nMODEL ANSWER: {date}"
    errors = 0
    for _ in range(2):
        try:
            answer = _ask(JUDGE_SYSTEM_PROMPT, text)
        except claude_json.SessionLimit:
            raise
        except claude_json.ClaudeError:
            errors += 1
            continue
        match = re.search(r'"correct"\s*:\s*(true|false)', answer)
        if match:
            return match.group(1) == "true", "judge"
    return (None, "judge error") if errors == 2 else (False, "judge gave no valid answer")
