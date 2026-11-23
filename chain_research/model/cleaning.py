"""Pulizia dei campi che arrivano dal web prima di entrare in un prompt per Claude (Parte 3.3 del piano).

Ogni campo: niente caratteri di controllo né a capo, lunghezza massima, e se contiene frasi rivolte a un modello
("ignora le istruzioni", "you are", "system prompt"...) diventa [TESTO SOSPETTO RIMOSSO] (stessa idea del caso F8
della guida delle fonti). I campi ripuliti vanno poi tra i delimitatori <<<DATI ... DATI>>>.
"""
import json
import re
import unicodedata
from typing import Any

from . import event_log

SUSPICIOUS = "[TESTO SOSPETTO RIMOSSO]"
_CHECK = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u2064\\ufeff]")
_IMPERATIVE = re.compile(
    r"\b(?:ignor(?:a|ate|e|ing)|dimentica|forget|disregard|override)\b.{0,40}\b(?:istruzion\w*|instruction\w*|"
    r"regol\w*|rules?|prompt|previous|precedent\w*|above|sopra)\b"
    r"|\b(?:you are|you're now|sei un|sei una|ora sei|act as|agisci come|pretend|fingi)\b"
    r"|\b(?:system prompt|prompt di sistema|developer message|jailbreak|assistant:|system:)|<\|im_(?:start|end)\|>"
    r"|\b(?:new|nuove?) (?:instructions?|istruzioni)\b"
    r"|\b(?:rispondi|answer|reply|respond)\b.{0,20}\b(?:only|soltanto|solo|instead|invece)\b"
    r"|\b(?:call|chiama|usa|use|invoke|esegui|execute)\b.{0,20}\b(?:tools?|strument\w|functions?|funzion\w)\b"
    r"|<<<|>>>|```", re.IGNORECASE)


def field(value: Any, maximum: int = 120, origin: str = "") -> str:
    """Un campo testuale dal web, pronto per il prompt."""
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    text = re.sub(r"\s+", " ", _CHECK.sub(" ", text)).strip()
    if _IMPERATIVE.search(text):
        event_log.write("sospetti", {"origin": origin, "field": text[:300]})
        return SUSPICIOUS
    return text[:maximum]


def data(obj: Any) -> str:
    """JSON compatto tra delimitatori: tutto ciò che c'è dentro è dato, mai istruzione."""
    return "<<<DATI\n" + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\nDATI>>>"


_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
# Per il testo delle pagine solo le iniezioni esplicite: "you are", "act as" o "rispondi solo" sono frasi comuni in
# una pagina e toglierle farebbe perdere informazioni (il testo arriva comunque tra delimitatori a un Claude senza
# strumenti).
_INJECTION = re.compile(
    r"\b(?:ignor(?:a|ate|e|ing)|dimentica|forget|disregard|override)\b.{0,40}\b(?:istruzion\w*|instruction\w*|"
    r"regol\w*|rules?|prompt)\b"
    r"|\b(?:system prompt|prompt di sistema|developer message|jailbreak)\b|<\|im_(?:start|end)\|>"
    r"|\b(?:new|nuove?) (?:instructions?|istruzioni)\b|^\s*(?:assistant|system|user)\s*:", re.IGNORECASE)


def text(value: Any, maximum: int = 20000, origin: str = "") -> str:
    """Un pezzo di pagina web per Claude: si tolgono solo le FRASI sospette (sostituite da [TESTO SOSPETTO
    RIMOSSO]), non tutto il pezzo come per un campo breve ("you are" è comunissimo nelle pagine inglesi: con la
    pulizia per campo si perderebbero blocchi interi). Caratteri di controllo tolti, a capo tenuti."""
    raw = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    raw = _CHECK.sub(lambda m: "\n" if m.group(0) == "\n" else " ", raw)
    sentences = []
    for sentence in _SENTENCES.split(raw):
        sentence = re.sub(r"[ \t]+", " ", sentence).strip()
        if not sentence:
            continue
        if _INJECTION.search(sentence):
            event_log.write("sospetti", {"origin": origin, "field": sentence[:300]})
            sentence = SUSPICIOUS
        sentences.append(sentence.replace("<<<", "‹‹‹").replace(">>>", "›››"))
    return "\n".join(sentences)[:maximum]
