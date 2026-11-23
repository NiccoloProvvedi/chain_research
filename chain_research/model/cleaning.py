"""Cleaning of the fields coming from the web before they enter a prompt for Claude (Part 3.3 of the plan).

Each field: no control characters or newlines, maximum length, and if it contains sentences addressed to a model
("ignora le istruzioni", "you are", "system prompt"...) it becomes [SUSPICIOUS TEXT REMOVED] (same idea as case F8
of the sources guide). The cleaned fields then go between the delimiters <<<DATA ... DATA>>>.
"""
import json
import re
import unicodedata
from typing import Any

from . import event_log

SUSPICIOUS = "[SUSPICIOUS TEXT REMOVED]"
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
    """A text field from the web, ready for the prompt."""
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    text = re.sub(r"\s+", " ", _CHECK.sub(" ", text)).strip()
    if _IMPERATIVE.search(text):
        event_log.write("suspicious", {"origin": origin, "field": text[:300]})
        return SUSPICIOUS
    return text[:maximum]


def data(obj: Any) -> str:
    """Compact JSON between delimiters: everything inside is data, never an instruction."""
    return "<<<DATA\n" + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\nDATA>>>"


_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
# For page text only explicit injections: "you are", "act as" or "rispondi solo" are common phrases in a page and
# removing them would lose information (the text reaches a Claude without tools, between delimiters, anyway).
_INJECTION = re.compile(
    r"\b(?:ignor(?:a|ate|e|ing)|dimentica|forget|disregard|override)\b.{0,40}\b(?:istruzion\w*|instruction\w*|"
    r"regol\w*|rules?|prompt)\b"
    r"|\b(?:system prompt|prompt di sistema|developer message|jailbreak)\b|<\|im_(?:start|end)\|>"
    r"|\b(?:new|nuove?) (?:instructions?|istruzioni)\b|^\s*(?:assistant|system|user)\s*:", re.IGNORECASE)


def text(value: Any, maximum: int = 20000, origin: str = "") -> str:
    """A piece of web page for Claude: only the suspicious SENTENCES are removed (replaced by [SUSPICIOUS TEXT
    REMOVED]), not the whole piece as for a short field ("you are" is very common in English pages: field cleaning
    would lose whole blocks). Control characters removed, newlines kept."""
    raw = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    raw = _CHECK.sub(lambda m: "\n" if m.group(0) == "\n" else " ", raw)
    sentences = []
    for sentence in _SENTENCES.split(raw):
        sentence = re.sub(r"[ \t]+", " ", sentence).strip()
        if not sentence:
            continue
        if _INJECTION.search(sentence):
            event_log.write("suspicious", {"origin": origin, "field": sentence[:300]})
            sentence = SUSPICIOUS
        sentences.append(sentence.replace("<<<", "‹‹‹").replace(">>>", "›››"))
    return "\n".join(sentences)[:maximum]
