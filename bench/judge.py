"""Giudice del banco di prova: estrae il valore dato come risposta e lo confronta con quello atteso.

Prima il confronto normalizzato (la risposta attesa compare nella risposta), poi Claude Haiku come giudice con due
esempi (come DeepDive). Claude è chiamato senza strumenti, come nel resto del progetto."""
import re
import unicodedata

from chain_research.model import claude_json

MODEL = "haiku"
EXTRACT = ("Estrai dal TESTO il valore che dà come risposta alla DOMANDA: una sola riga con un solo valore (nome, data o "
          "numero), copiato dal testo. Non controllare se è giusto né se la persona o il luogo sono quelli della domanda: "
          "estrai e basta. Scrivi solo NON TROVATA se il testo dice di non avere la risposta, oppure se dà come risposta "
          "due o più valori diversi senza sceglierne uno.")
JUDGE_SYSTEM_PROMPT = (
    "Sei un giudice. Ti do una domanda, la risposta attesa e la risposta di un modello. Dimmi se la risposta del "
    "modello indica la stessa cosa della risposta attesa (stessa persona, stesso luogo, stessa data al giorno, stesso "
    "numero; lingua, formato e unità di misura possono cambiare). Una risposta più vaga di quella attesa (solo l'anno "
    "invece del giorno, un intervallo) NON è corretta.\n\n"
    "Esempio 1 — attesa: \"24 luglio 1973\"; modello: \"July 24, 1973\" → {\"corretta\": true}\n"
    "Esempio 2 — attesa: \"Mitchell Baker\"; modello: \"Brendan Eich\" → {\"corretta\": false}\n\n"
    'Rispondi solo con JSON: {"corretta": true} oppure {"corretta": false}.')


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", " ", "".join(c for c in text if not unicodedata.combining(c))))


# Un meno davanti a un numero: non il trattino di un intervallo («00118–00199») né di una data («1973-07-24»).
_NEGATIVE = re.compile(r"(?<![\w.,])(?<!\d\s)[−–-]\s?(\d[\d.,]*)")
_BELOW_SEA_LEVEL = re.compile(r"sotto il livello del mare|below sea level", re.I)


def _digits(number: str) -> str:
    return number.rstrip(".,")


def _negatives(text: str) -> set[str]:
    return {_digits(n) for n in _NEGATIVE.findall(str(text))}


def normalized_equal(wait: str, date: str) -> bool:
    """La risposta attesa compare nella risposta data, con lo stesso segno dei numeri (11/11, domande di controllo:
    «2 m» attesa, «−2 m s.l.m.» data, contata giusta)."""
    wait_n, date_n = normalize(wait).strip(), normalize(date)
    if not wait_n or re.search(rf"\b{re.escape(wait_n)}\b", date_n) is None:
        return False
    under = _negatives(date) | ({_digits(n) for n in re.findall(r"\d[\d.,]*", str(date))}
                               if _BELOW_SEA_LEVEL.search(str(date)) else set())
    return all((n in _negatives(wait)) == (n in under) for n in map(_digits, re.findall(r"\d[\d.,]*", str(wait))))


def _ask(instructions: str, text: str) -> str:
    """Gli errori di Claude salgono: una chiamata fallita non è una risposta sbagliata (11/11)."""
    return str(claude_json.ask(instructions, text, None, "judge", model=MODEL)[0])


def short_answer(text: str) -> str:
    """La riga «Risposta» (o la prima riga non vuota)."""
    found = re.search(r"\*\*Risposta:?\*\*:?\s*(.+)", text)
    if found:
        return found.group(1).strip()[:500]
    return next((r.strip() for r in text.splitlines() if r.strip()), "")[:500]


def extract(question: str, text: str) -> str:
    outcome = _ask(EXTRACT, f"DOMANDA: {question}\n\nDOCUMENTO:\n{text[:40000]}")
    # La prima riga con un valore: niente premesse come «Dal testo fornito, il valore … è:».
    rows = [r.strip().strip("*").strip() for r in outcome.splitlines() if r.strip()]
    row = next((r for r in rows if not r.endswith(":")), rows[0] if rows else "")
    return "" if re.match(r"(NON|NOT)\s+(TROVATA|FOUND)", row.upper()) else row[:300]


def final_of(outcome: dict, question: str) -> str:
    """Il valore da giudicare: quello trovato dal programma; solo se manca, quello che Haiku estrae dalla riga
    «Risposta» (11/11: «00118–00199» e «15 ottobre 1870», giusti, contati sbagliati perché l'estrazione era vuota)."""
    value = " ".join(str(outcome.get("value") or "").split())
    return value or extract(question, short_answer(outcome.get("answer") or ""))


def assess(question: str, wait: str, date: str) -> tuple[bool | None, str]:
    """(corretta, chi ha deciso): "confronto", "giudice", "vuota", "giudice senza risposta valida"; (None, "giudice
    in errore") se Claude non ha risposto: la domanda resta da giudicare. Il limite di sessione sale a chi chiama."""
    if not date:
        return False, "empty"
    if normalized_equal(wait, date):
        return True, "comparison"
    text = f"DOMANDA: {question}\nRISPOSTA ATTESA: {wait}\nRISPOSTA DEL MODELLO: {date}"
    errors = 0
    for _ in range(2):
        try:
            answer = _ask(JUDGE_SYSTEM_PROMPT, text)
        except claude_json.SessionLimit:
            raise
        except claude_json.ClaudeError:
            errors += 1
            continue
        match = re.search(r'"corretta"\s*:\s*(true|false)', answer)
        if match:
            return match.group(1) == "true", "judge"
    return (None, "judge error") if errors == 2 else (False, "judge gave no valid answer")
