"""Il documento finale di un'indagine: completo, con le fonti, in HTML e in PDF.

- Fonti: tutte quelle delle risposte, numerate una volta sola per tutto il documento (stesso indirizzo = stessa
  fonte); le citazioni [n] di ogni risposta si rinumerano di conseguenza.
- Sezioni: la regia scrive ogni sezione dalle risposte complete delle sue domande (e dai fatti brevi delle altre),
  alcune in parallelo.
- Cornice: sintesi, punti chiave, conclusioni e limiti, dalle sezioni scritte.
- Appendici: tutte le domande con la risposta e la fiducia, come è stata fatta l'indagine; elenco delle fonti.
- PDF: la pagina HTML stampata dal Chromium del progetto.
"""
import hashlib
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from markdown_it import MarkdownIt

from . import configuration
from .model import claude_json, cleaning
from .web.sources.wikipedia_graph import split_sentences

if TYPE_CHECKING:
    from .investigation import Investigation

FRAME_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["synthesis", "key_points", "conclusions", "limits"],
                  "properties": {"synthesis": {"type": "string", "minLength": 50},
                                 "key_points": {"type": "array", "minItems": 1, "maxItems": 12,
                                                  "items": {"type": "string"}},
                                 "conclusions": {"type": "string"}, "limits": {"type": "string"}}}
CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sentences"], "properties": {
    "sentences": {"type": "array", "maxItems": 60, "items": {
        "type": "object", "additionalProperties": False, "required": ["number", "outcome", "reason"],
        "properties": {"number": {"type": "integer", "minimum": 1},
                       "outcome": {"type": "string", "enum": ["partly", "unsupported"]},
                       "reason": {"type": "string", "maxLength": 300}}}}}}
QUOTE = re.compile(r"\[(\s*\d+\s*(?:[,;]\s*\d+\s*)*)\]")
OUTCOMES = {"answer": "found", "not_found": "not found", "error": "error", "waiting": "not done",
         "in_progress": "interrupted"}
_md = MarkdownIt("commonmark", {"html": False, "typographer": False}).enable("table")


STATUS_NAMES = {"verified": ("verified", "verificate"), "supported": ("supported", "sostenute"),
              "contested": ("contested", "contestate"), "uncertain": ("uncertain", "incerte")}


def status_of(d: dict) -> str:
    """Lo stato di una risposta trovata (web/validazione.py): quello deciso dai controlli; per le indagini di prima
    (11/11/2026) «incerta» se la catena non era tutta confermata, altrimenti «sostenuta». Vuoto senza risposta."""
    if d.get("status") != "answer":
        return ""
    if d.get("answer_status") in STATUS_NAMES:
        return d["answer_status"]
    return "uncertain" if d.get("confirmed") is False else "supported"


def count_statuses(questions: list[dict]) -> str:
    """«1 verificata, 2 incerte»: solo gli stati presenti."""
    how_many = {s: sum(status_of(d) == s for d in questions) for s in STATUS_NAMES}
    return ", ".join(f"{n} {STATUS_NAMES[s][n != 1]}" for s, n in how_many.items() if n)


def _key_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), parts.path.rstrip("/"),
                       parts.query, ""))


def global_sources(questions: list[dict]) -> tuple[list[dict], dict[str, dict[int, int]]]:
    """Fonti di tutto il documento (numerate da 1) e, per ogni domanda, numero locale → numero globale."""
    sources: list[dict] = []
    per_url: dict[str, int] = {}
    mappings: dict[str, dict[int, int]] = {}
    for d in questions:
        mapping = {}
        for f in d.get("sources") or []:
            key = _key_url(str(f.get("url") or ""))
            if not key:
                continue
            if key not in per_url:
                sources.append({"number": len(sources) + 1, "url": f["url"],
                              "title": str(f.get("title") or f["url"])[:200], "questions": []})
                per_url[key] = len(sources)
            number = per_url[key]
            mapping[int(f["number"])] = number
            sources[number - 1]["questions"].append(d["id"])
        mappings[d["id"]] = mapping
    return sources, mappings


def renumber(text: str, mapping: dict[int, int]) -> str:
    """[1, 3] della risposta di una domanda → i numeri delle fonti del documento; i numeri sconosciuti spariscono."""
    def replace(match: re.Match) -> str:
        numbers = [mapping.get(int(n)) for n in re.findall(r"\d+", match.group(1))]
        numbers = list(dict.fromkeys(n for n in numbers if n))
        return f"[{', '.join(map(str, numbers))}]" if numbers else ""
    return QUOTE.sub(replace, text or "")


def _clean_answer(d: dict, mapping: dict[int, int]) -> str:
    return cleaning.text(renumber(d.get("answer") or "", mapping), origin="answer")


def markdown_sentences(text: str) -> list[str]:
    """Le frasi di una sezione, come stanno scritte (per ritrovarle e toglierle): niente titoli né righe di tabella."""
    sentences: list[str] = []
    for row in (text or "").splitlines():
        row = row.strip()
        if not row or row.startswith(("#", "|")):
            continue
        sentences += split_sentences(re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", row))
    return [f for f in sentences if len(f) >= 8]


def remove_sentences(text: str, sentences: list[str]) -> str:
    """Il testo senza quelle frasi; le voci d'elenco e i paragrafi rimasti vuoti spariscono."""
    for sentence in sentences:
        text = text.replace(sentence, "")
    rows = []
    for row in text.splitlines():
        clean = re.sub(r"[ \t]{2,}", " ", row).rstrip()
        if row.strip() and re.fullmatch(r"\s*(?:[-*+]|\d+[.)])?\s*", clean):
            continue  # una voce d'elenco o un paragrafo che conteneva solo la frase tolta
        rows.append(clean)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(rows)).strip()


_NUMBER_IN_TEXT = re.compile(r"(?<![\w\[])\d[\d.,\u202f\u00a0 ]*\d")


def numbers_without_support(sentence: str, support: str) -> list[str]:
    """I numeri di una frase (date, quantità: almeno due cifre; non le citazioni [n]) che non stanno nei testi da cui la
    sezione è stata scritta. Un numero che le risposte non danno, chi scrive lo ha aggiunto (14/11: «fino al 1964»)."""
    from .web.validation import _numbers
    without_quotes = QUOTE.sub(" ", sentence)
    known = _numbers(QUOTE.sub(" ", support))
    missing = []
    for piece in _NUMBER_IN_TEXT.findall(without_quotes):
        whole = re.sub(r"\D", "", piece).lstrip("0") or "0"
        groups = [g.lstrip("0") or "0" for g in re.findall(r"\d+", piece)]
        if whole not in known and not all(g in known or len(g) == 1 for g in groups):
            missing.append(piece.strip(" .,"))
    return missing


def check_section(investigation: "Investigation", s: dict, input_data: dict, text: str) -> str:
    """Il testo della sezione, frase per frase, contro le risposte da cui è stato scritto. Due controlli: il programma
    (ogni numero della frase sta nelle risposte) e una rilettura della regia (frasi che aggiungono o cambiano un fatto).
    Le frasi segnalate tornano una volta a chi scrive; quelle ancora senza appoggio le toglie il programma («in parte»
    resta: il fatto principale c'è). Meglio una frase in meno che un fatto in più."""
    support = json.dumps(input_data, ensure_ascii=False)

    def flag(current: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
        sentences = markdown_sentences(current)
        found_items: dict[str, tuple[str, str]] = {}
        try:
            outcome = investigation.director.ask("document_check", {
                "answers": input_data["answers"], "short_facts_other_sections": input_data["short_facts_other_sections"],
                "sentences": [{"number": i, "sentence": f} for i, f in enumerate(sentences, start=1)]}, CHECK_SCHEMA)
        except (claude_json.SessionLimit, claude_json.Interrupted):
            raise
        except claude_json.ClaudeError:
            outcome = {}  # senza la rilettura resta il controllo del programma: la sezione già pagata non si perde
        for article in (outcome.get("sentences") if isinstance(outcome, dict) else None) or []:
            number = article.get("number") if isinstance(article, dict) else None
            if isinstance(number, int) and 1 <= number <= len(sentences):
                found_items[sentences[number - 1]] = (str(article.get("outcome")), str(article.get("reason", ""))[:300])
        for sentence in sentences:
            missing = numbers_without_support(sentence, support)
            if missing:
                found_items[sentence] = ("unsupported", "numeri che le risposte non danno: " + ", ".join(missing))
        return sentences, {f: found_items[f] for f in sentences if f in found_items}

    sentences, flagged = flag(text)
    outcome = {"sentences": len(sentences), "flagged": len(flagged), "rewritten": False, "removed": 0}
    if flagged:
        investigation.event({"kind": "document", "text": f"Sezione «{s['title']}»: {len(flagged)} frasi senza appoggio "
                                                       "nelle risposte, la riscrivo…"})
        new = str(investigation.director.ask("document_section", {
            **input_data, "previous_text": text,
            "sentences_to_fix": [{"sentence": f, "reason": reason} for f, (_and, reason) in flagged.items()]}, None))
        text, outcome["rewritten"] = _remove_title(new, s["title"]), True
        _, anchor = flag(text)
        from_remove = [f for f, (degree, _m) in anchor.items() if degree == "unsupported"]
        text, outcome["removed"] = remove_sentences(text, from_remove), len(from_remove)
    with investigation._mutex:
        investigation.status.setdefault("sections_check", {})[s["id"]] = outcome
    return text


def _with_draft(investigation: "Investigation", key: str, input_data: Any, produce: Any) -> Any:
    """Il risultato già scritto per questi stessi dati, oppure lo si scrive e si salva: se una sezione o il PDF falliscono,
    alla prossima prova quelle già pagate non si rifanno."""
    fingerprint = hashlib.sha256(json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    with investigation._mutex:
        draft = (investigation.status.get("drafts") or {}).get(key)
    if draft and draft["fingerprint"] == fingerprint:
        return draft["text"]
    text = produce()
    with investigation._mutex:
        investigation.status.setdefault("drafts", {})[key] = {"fingerprint": fingerprint, "text": text}
    investigation.save()
    return text


def write(investigation: "Investigation") -> dict:
    status = investigation.status
    plan = status["plan"]
    questions = status["questions"]
    sources, mappings = global_sources(questions)
    with_text = [d for d in questions if d["status"] in ("answer", "not_found") and d.get("answer")]
    # Le risposte «incerte» non arrivano a chi scrive (né alle sezioni né, attraverso di loro, alla sintesi): le mette
    # il programma in un riquadro «Punti aperti» della loro sezione, con il motivo.
    resolved = [d for d in with_text if status_of(d) != "uncertain"]
    uncertain = [d for d in with_text if status_of(d) == "uncertain"]
    short = [{"section": d["section"], "question": d["question"], "outcome": OUTCOMES.get(d["status"]),
              "answer": cleaning.text(renumber(_row(d, "Risposta"), mappings[d["id"]]), origin="answer")[:300]}
             for d in resolved]

    def section(s: dict) -> dict:
        owned = [{"question": d["question"], "outcome": OUTCOMES.get(d["status"]),
                    "status": status_of(d) or OUTCOMES.get(d["status"]),
                    "checks_limits": [cleaning.text(str(m), origin="answer") for m in d.get("status_reasons") or []],
                    "answer": _clean_answer(d, mappings[d["id"]])} for d in resolved if d["section"] == s["id"]]
        s = {**s, "open_points": [d["id"] for d in uncertain if d["section"] == s["id"]]}
        if not owned:
            return {**s, "text": "*Nessuna risposta confermata per questa sezione: vedi i punti aperti.*" if s["open_points"]
                    else "*Nessuna domanda di questa sezione ha trovato una risposta.*"}
        input_data = {"title": plan["title"], "goal": plan["goal"], "section": s, "answers": owned,
                    "short_facts_other_sections": [b for b in short if b["section"] != s["id"]],
                    "sentences_check": 1}  # versione del controllo: cambiarla fa riscrivere le sezioni già fatte

        def produce() -> str:
            investigation.event({"kind": "document", "text": f"Scrivo la sezione «{s['title']}»…"})
            written = _remove_title(str(investigation.director.ask("document_section", input_data, None)), s["title"])
            return check_section(investigation, s, input_data, written)
        return {**s, "text": _remove_title(_with_draft(investigation, f"sezione:{s['id']}", input_data, produce), s["title"])}

    with ThreadPoolExecutor(max_workers=3) as executor:
        sections = list(executor.map(section, plan["sections"]))
    investigation.event({"kind": "document", "text": "Scrivo sintesi, conclusioni e limiti…"})
    not_resolved = [{"question": d["question"], "outcome": OUTCOMES.get(d["status"]),
                    "reason": d.get("chain_reason") or d.get("error") or ""}
                   for d in questions if d["status"] != "answer"]
    frame_input = {"title": plan["title"], "goal": plan["goal"],
                        "sections": [{"title": s["title"], "text": s["text"]} for s in sections],
                        "unresolved_questions": not_resolved}
    frame = _with_draft(investigation, "cornice", frame_input,
                         lambda: investigation.director.ask("document_frame", frame_input, FRAME_SCHEMA))
    valid_numbers = set(range(1, len(sources) + 1))
    page = compose(status, sections, frame, sources, mappings, valid_numbers)
    file_html = investigation.folder / "document.html"
    file_html.write_text(page, encoding="utf-8")
    file_pdf = investigation.folder / "document.pdf"
    investigation.event({"kind": "document", "text": "Preparo il PDF…"})
    print_pdf(file_html, file_pdf)
    return {"html": str(file_html), "pdf": str(file_pdf), "sources": len(sources), "sections": len(sections)}


def _row(d: dict, label: str) -> str:
    found = re.search(rf"\*\*{label}:?\*\*:?\s*(.+)", d.get("answer") or "")
    return found.group(1).strip() if found else ""


def _level(confidence: str) -> str:
    """Solo il livello della fiducia (alta, media, bassa): il motivo sta nella risposta."""
    match = re.match(r"\W*(alta|media|bassa)", confidence, re.IGNORECASE)
    return match.group(1).lower() if match else confidence[:40]


def _remove_title(text: str, title: str) -> str:
    """Se la regia ha ripetuto il titolo della sezione in testa, si toglie (lo mette il programma)."""
    rows = text.strip().splitlines()
    if rows and rows[0].lstrip("#").strip().lower() == title.strip().lower():
        rows = rows[1:]
    return "\n".join(rows).strip()


def _html(markdown: str, valid_numbers: set[int]) -> str:
    """Markdown → HTML (niente HTML grezzo), con le citazioni [n] come collegamenti alle fonti."""
    page = _md.render(markdown or "")

    def connect(match: re.Match) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        if not numbers or not all(n in valid_numbers for n in numbers):
            return match.group(0)
        return "[" + ", ".join(f'<a href="#fonte-{n}">{n}</a>' for n in numbers) + "]"
    return QUOTE.sub(connect, page)


def _in_line(markdown: str, valid_numbers: set[int]) -> str:
    """Come `_html`, senza il paragrafo intorno (per le voci di un elenco)."""
    return _html(markdown, valid_numbers).strip().removeprefix("<p>").removesuffix("</p>").strip()


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def compose(status: dict, sections: list[dict], frame: dict, sources: list[dict], mappings: dict[str, dict[int, int]],
            valid_numbers: set[int]) -> str:
    plan = status["plan"]
    questions = status["questions"]
    found_items = sum(d["status"] == "answer" for d in questions)
    e = html.escape
    index = "".join(f'<li><a href="#{e(s["id"])}">{e(s["title"])}</a></li>' for s in sections)
    per_id = {d["id"]: d for d in questions}

    def open_points(s: dict) -> str:
        """Le risposte incerte della sezione: domanda, valore con la fonte e il motivo, scritti dal programma."""
        articles = "".join(
            f"<li><strong>{e(d['question'])}</strong> "
            f"{_in_line(cleaning.text(renumber(_row(d, 'Risposta'), mappings.get(d['id'], {})), origin='answer'), valid_numbers)}"
            f" <span class='motivo'>Non confermata: "
            f"{e('; '.join(str(m) for m in d.get('status_reasons') or []) or 'la catena non è tutta confermata')}.</span></li>"
            for d in (per_id[i] for i in s.get("open_points") or [] if i in per_id))
        return (f'<aside class="aperti"><h3>Punti aperti</h3><p>Risposte trovate ma non confermate dai controlli: '
                f"non vanno prese come fatti.</p><ul>{articles}</ul></aside>") if articles else ""
    sections_body = "".join(f'<section id="{e(s["id"])}"><h2>{e(s["title"])}</h2>{_html(s["text"], valid_numbers)}'
                            f"{open_points(s)}</section>" for s in sections)
    points = "".join(f"<li>{_in_line(p, valid_numbers)}</li>" for p in frame["key_points"])
    sections_titles = {s["id"]: s["title"] for s in sections}
    questions_rows = "".join(
        f"<tr><td>{e(d['id'])}</td><td>{d['round']}</td><td>{e(sections_titles.get(d['section'], d['section']))}</td>"
        f"<td>{e(d['question'])}</td><td class='esito-{e(d['status'])}'>{e(OUTCOMES.get(d['status'], d['status']))}</td>"
        f"<td>{_html(renumber(_row(d, 'Risposta'), mappings.get(d['id'], {})), valid_numbers)}</td>"
        f"<td>{e(status_of(d))}</td></tr>" for d in questions)
    synthesis, conclusions, limits = (_html(frame[k], valid_numbers) for k in ("synthesis", "conclusions", "limits"))
    cited = {int(n) for n in re.findall(r'href="#fonte-(\d+)"',
                                         sections_body + points + synthesis + conclusions + limits + questions_rows)}
    statuses = count_statuses(questions)
    checks = list((status.get("sections_check") or {}).values())
    sentences_check = (f"{sum(c['sentences'] for c in checks)} frasi, {sum(c['flagged'] for c in checks)} segnalate "
                       f"e fatte riscrivere, {sum(c['removed'] for c in checks)} tolte dal programma perché ancora "
                       "senza appoggio") if checks else "controllo non fatto"
    with_answer = f"{found_items} con risposta" + (f" ({statuses})" if statuses else "")

    def sources_articles(choices: list[dict]) -> str:
        return "".join(f'<li id="fonte-{f["number"]}" value="{f["number"]}"><span class="titolo-fonte">{e(f["title"])}'
                       f'</span><br><a href="{e(f["url"])}">{e(f["url"])}</a></li>' for f in choices)
    cited_sources = sources_articles([f for f in sources if f["number"] in cited])
    sources_other = sources_articles([f for f in sources if f["number"] not in cited])
    sources_list = f'<h3>Fonti citate</h3><ol class="fonti">{cited_sources}</ol>' + (
        f'<h3>Altre fonti consultate</h3><ol class="fonti">{sources_other}</ol>' if sources_other else "")
    evaluations = status.get("evaluations") or []
    last = evaluations[-1] if evaluations else None
    method = (
        f"<p>L'indagine è partita dalla richiesta: «{e(status['topic'])}». Claude ({e(status['director_model'])}) "
        f"ha diviso l'argomento in {len(sections)} sezioni e ha formulato domande precise, ognuna su un solo dato. "
        f"Ogni domanda è stata risolta dalla ricerca a catena: un programma che parte da cose certe, segue i "
        f"collegamenti tra le pagine di Wikipedia e del web, e usa Claude ({e(status['chain_model'])}) solo per "
        f"leggere poche righe alla volta; ogni prova citata è stata controllata dal programma nella pagina da cui "
        f"viene; alla fine il valore è riletto, cercato in un'altra fonte e la catena è rivista da un secondo modello: da "
        f"questi controlli viene lo stato di ogni risposta (verificata, sostenuta, contestata, incerta). Dopo ogni giro Claude ha valutato quanto ogni sezione fosse coperta e ha aggiunto domande sui buchi "
        f"e sui nomi emersi. Il testo delle sezioni è scritto da Claude a partire dalle risposte: le sue citazioni non "
        f"sono ricontrollate dal programma sulle pagine; ogni frase delle sezioni è però riletta contro le risposte "
        f"({sentences_check}); le risposte incerte non entrano nel testo, stanno nei riquadri «Punti aperti».</p><p>Giri di domande: {status['round']} · domande: {len(questions)}, {with_answer} · fonti citate: {len(cited)}, consultate: {len(sources)} · durata: {_duration(status.get('seconds', 0))} · costo di Claude: "
        f"{sum(d.get('cost_usd') or 0 for d in questions) + status.get('director_spent_usd', 0):.2f} $ · conclusione "
        f"dell'indagine: {e(status.get('end_reason') or '')}.</p>"
        + (f"<p>Ultima valutazione di Claude: {e(last['reason'])}</p>" if last else ""))
    date = datetime.now().strftime("%d/%m/%Y")
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(plan['title'])}</title>
<style>{STYLE}</style></head>
<body><main>
<header class="copertina">
<div class="sopra">Documento di ricerca · {date}</div>
<h1>{e(plan['title'])}</h1>
<p class="obiettivo">{e(plan['goal'])}</p>
<p class="dati">{len(questions)} domande · {with_answer} ·
{len(cited)} fonti citate su {len(sources)} consultate · {status['round']} giri di indagine</p>
</header>
<nav class="indice"><h2>Indice</h2><ol><li><a href="#sintesi">Sintesi</a></li>{index}
<li><a href="#conclusioni">Conclusioni</a></li><li><a href="#limiti">Limiti</a></li>
<li><a href="#domande">Appendice A · Domande e risposte</a></li><li><a href="#metodo">Appendice B · Metodo</a></li>
<li><a href="#fonti">Fonti</a></li></ol></nav>
<section id="sintesi" class="sintesi"><h2>Sintesi</h2>{synthesis}
<h3>Punti chiave</h3><ul>{points}</ul></section>
{sections_body}
<section id="conclusioni"><h2>Conclusioni</h2>{conclusions}</section>
<section id="limiti"><h2>Limiti</h2>{limits}</section>
<section id="domande" class="appendice"><h2>Appendice A · Domande e risposte</h2>
<div class="tabella"><table><thead><tr><th>#</th><th>Giro</th><th>Sezione</th><th>Domanda</th><th>Esito</th>
<th>Risposta</th><th>Stato</th></tr></thead><tbody>{questions_rows}</tbody></table></div></section>
<section id="metodo" class="appendice"><h2>Appendice B · Metodo</h2>{method}</section>
<section id="fonti" class="appendice"><h2>Fonti</h2>{sources_list}</section>
</main></body></html>
"""


STYLE = """
:root { --testo: #1c2128; --tenue: #5a6472; --riga: #d7dce2; --fondo: #ffffff; --pannello: #f1f4f7;
  --accento: #1f5f7a; --ok: #2f7a4b; --ko: #a34040; }
html { background: var(--fondo); }
body { margin: 0; color: var(--testo); background: var(--fondo);
  font: 11pt/1.55 "Noto Serif", "DejaVu Serif", Georgia, "Times New Roman", serif; }
main { max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.2rem 4rem; }
h1, h2, h3 { font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif; line-height: 1.25; }
h1 { font-size: 1.9rem; margin: .4rem 0 1rem; }
h2 { font-size: 1.35rem; margin: 2.2rem 0 .6rem; padding-top: .6rem; border-top: 1px solid var(--riga); }
h3 { font-size: 1.05rem; margin: 1.4rem 0 .4rem; }
a { color: var(--accento); text-decoration: none; overflow-wrap: anywhere; }
p { margin: .6rem 0; }
.sopra { font: 600 .75rem/1.4 "Noto Sans", Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase;
  color: var(--tenue); }
.copertina { padding-bottom: 1rem; }
.obiettivo { font-size: 1.05rem; }
.dati { color: var(--tenue); font-size: .92rem; }
.indice ol { columns: 1; }
.sintesi { background: var(--pannello); padding: .2rem 1.2rem 1rem; border-radius: 4px; }
.sintesi h2 { border-top: 0; }
table { border-collapse: collapse; width: 100%; font: .82rem/1.4 "Noto Sans", Arial, sans-serif; }
th { text-align: left; border-bottom: 1.5px solid var(--testo); padding: .3rem .4rem; vertical-align: bottom; }
td { border-bottom: 1px solid var(--riga); padding: .3rem .4rem; vertical-align: top; }
td p { margin: 0; }
.tabella { overflow-x: auto; }
.esito-risposta { color: var(--ok); font-weight: 600; }
.esito-non_trovata, .esito-errore { color: var(--ko); }
.aperti { border-left: 3px solid var(--ko); background: var(--pannello); padding: .1rem 1rem .6rem; margin: 1.2rem 0;
  font-size: .92rem; break-inside: avoid; }
.aperti h3 { margin-top: .8rem; }
.aperti .motivo { color: var(--ko); }
ol.fonti { font-size: .85rem; padding-left: 1.8rem; }
ol.fonti li { margin: .35rem 0; }
.titolo-fonte { font-weight: 600; }
@media print {
  main { max-width: none; padding: 0; }
  h2 { break-after: avoid; }
  .copertina { min-height: 40vh; }
  .indice { break-after: page; }
  table, tr { break-inside: avoid; }
  .appendice { break-before: page; }
}
"""


def print_pdf(file_html: Path, file_pdf: Path) -> None:
    """Il documento HTML stampato in PDF (A4, numeri di pagina) dal Chromium del progetto, senza rete."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configuration.folder("engines_folder"))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file:", "data:"))
                         else r.abort())
            page.goto(file_html.resolve().as_uri(), wait_until="load")
            page.emulate_media(media="print")
            page.pdf(path=str(file_pdf), format="A4", print_background=True,
                       margin={"top": "18mm", "bottom": "18mm", "left": "17mm", "right": "17mm"},
                       display_header_footer=True, header_template="<span></span>",
                       footer_template='<div style="font:8px sans-serif;width:100%;text-align:center;color:#777">'
                                       '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        finally:
            browser.close()
