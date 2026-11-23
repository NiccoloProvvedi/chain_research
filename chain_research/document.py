"""The final document of an investigation: complete, with the sources, in HTML and PDF.

- Sources: all those of the answers, numbered once for the whole document (same address = same source); the [n]
  citations of each answer are renumbered accordingly.
- Sections: the director writes each section from the full answers to its questions (and from the short facts of the
  others), some in parallel.
- Frame: summary, key points, conclusions and limitations, from the written sections.
- Appendices: all the questions with answer and confidence, how the investigation was done; list of sources.
- PDF: the HTML page printed by the project's Chromium.
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


STATUS_NAMES = {"verified": ("verified", "verified"), "supported": ("supported", "supported"),
              "contested": ("contested", "contested"), "uncertain": ("uncertain", "uncertain")}


def status_of(d: dict) -> str:
    """The status of a found answer (web/validation.py): the one decided by the checks; for older investigations
    (before 2026-11-11) "uncertain" if the chain was not fully confirmed, otherwise "supported". Empty without answer."""
    if d.get("status") != "answer":
        return ""
    if d.get("answer_status") in STATUS_NAMES:
        return d["answer_status"]
    return "uncertain" if d.get("confirmed") is False else "supported"


def count_statuses(questions: list[dict]) -> str:
    """"1 verified, 2 uncertain": only the statuses present."""
    how_many = {s: sum(status_of(d) == s for d in questions) for s in STATUS_NAMES}
    return ", ".join(f"{n} {STATUS_NAMES[s][n != 1]}" for s, n in how_many.items() if n)


def _key_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), parts.path.rstrip("/"),
                       parts.query, ""))


def global_sources(questions: list[dict]) -> tuple[list[dict], dict[str, dict[int, int]]]:
    """Sources of the whole document (numbered from 1) and, for each question, local number → global number."""
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
    """[1, 3] in the answer to a question → the document's source numbers; unknown numbers disappear."""
    def replace(match: re.Match) -> str:
        numbers = [mapping.get(int(n)) for n in re.findall(r"\d+", match.group(1))]
        numbers = list(dict.fromkeys(n for n in numbers if n))
        return f"[{', '.join(map(str, numbers))}]" if numbers else ""
    return QUOTE.sub(replace, text or "")


def _clean_answer(d: dict, mapping: dict[int, int]) -> str:
    return cleaning.text(renumber(d.get("answer") or "", mapping), origin="answer")


def markdown_sentences(text: str) -> list[str]:
    """The sentences of a section, as written (to find and remove them): no headings or table rows."""
    sentences: list[str] = []
    for row in (text or "").splitlines():
        row = row.strip()
        if not row or row.startswith(("#", "|")):
            continue
        sentences += split_sentences(re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", row))
    return [f for f in sentences if len(f) >= 8]


def remove_sentences(text: str, sentences: list[str]) -> str:
    """The text without those sentences; list items and paragraphs left empty disappear."""
    for sentence in sentences:
        text = text.replace(sentence, "")
    rows = []
    for row in text.splitlines():
        clean = re.sub(r"[ \t]{2,}", " ", row).rstrip()
        if row.strip() and re.fullmatch(r"\s*(?:[-*+]|\d+[.)])?\s*", clean):
            continue  # a list item or paragraph that held only the removed sentence
        rows.append(clean)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(rows)).strip()


_NUMBER_IN_TEXT = re.compile(r"(?<![\w\[])\d[\d.,\u202f\u00a0 ]*\d")


def numbers_without_support(sentence: str, support: str) -> list[str]:
    """The numbers in a sentence (dates, quantities: at least two digits; not the [n] citations) that are not in the
    texts the section was written from. A number the answers do not give was added by the writer (14 Nov: "until
    1964")."""
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
    """The section text, sentence by sentence, against the answers it was written from. Two checks: the program (every
    number in the sentence is in the answers) and a reread by the director (sentences that add or change a fact).
    Flagged sentences go back to the writer once; those still unsupported are removed by the program ("partly"
    stays: the main fact is there). Better one sentence less than one fact too many."""
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
            outcome = {}  # without the reread the program's check remains: the section already paid for is not lost
        for article in (outcome.get("sentences") if isinstance(outcome, dict) else None) or []:
            number = article.get("number") if isinstance(article, dict) else None
            if isinstance(number, int) and 1 <= number <= len(sentences):
                found_items[sentences[number - 1]] = (str(article.get("outcome")), str(article.get("reason", ""))[:300])
        for sentence in sentences:
            missing = numbers_without_support(sentence, support)
            if missing:
                found_items[sentence] = ("unsupported", "numbers the answers do not give: " + ", ".join(missing))
        return sentences, {f: found_items[f] for f in sentences if f in found_items}

    sentences, flagged = flag(text)
    outcome = {"sentences": len(sentences), "flagged": len(flagged), "rewritten": False, "removed": 0}
    if flagged:
        investigation.event({"kind": "document", "text": f"Section \"{s['title']}\": {len(flagged)} sentences not supported "
                                                       "by the answers, rewriting it…"})
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
    """The result already written for these same data, or it is written and saved: if a section or the PDF fails, the
    next attempt does not redo those already paid for."""
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
    # "Uncertain" answers never reach the writer (neither the sections nor, through them, the summary): the program
    # puts them in an "Open points" box of their section, with the reason.
    resolved = [d for d in with_text if status_of(d) != "uncertain"]
    uncertain = [d for d in with_text if status_of(d) == "uncertain"]
    short = [{"section": d["section"], "question": d["question"], "outcome": OUTCOMES.get(d["status"]),
              "answer": cleaning.text(renumber(_row(d, "Answer"), mappings[d["id"]]), origin="answer")[:300]}
             for d in resolved]

    def section(s: dict) -> dict:
        owned = [{"question": d["question"], "outcome": OUTCOMES.get(d["status"]),
                    "status": status_of(d) or OUTCOMES.get(d["status"]),
                    "checks_limits": [cleaning.text(str(m), origin="answer") for m in d.get("status_reasons") or []],
                    "answer": _clean_answer(d, mappings[d["id"]])} for d in resolved if d["section"] == s["id"]]
        s = {**s, "open_points": [d["id"] for d in uncertain if d["section"] == s["id"]]}
        if not owned:
            return {**s, "text": "*No confirmed answer for this section: see the open points.*" if s["open_points"]
                    else "*No question of this section found an answer.*"}
        input_data = {"title": plan["title"], "goal": plan["goal"], "section": s, "answers": owned,
                    "short_facts_other_sections": [b for b in short if b["section"] != s["id"]],
                    "sentences_check": 1}  # version of the check: changing it rewrites the sections already done

        def produce() -> str:
            investigation.event({"kind": "document", "text": f"Writing the section \"{s['title']}\"…"})
            written = _remove_title(str(investigation.director.ask("document_section", input_data, None)), s["title"])
            return check_section(investigation, s, input_data, written)
        return {**s, "text": _remove_title(_with_draft(investigation, f"section:{s['id']}", input_data, produce), s["title"])}

    with ThreadPoolExecutor(max_workers=3) as executor:
        sections = list(executor.map(section, plan["sections"]))
    investigation.event({"kind": "document", "text": "Writing summary, conclusions and limitations…"})
    not_resolved = [{"question": d["question"], "outcome": OUTCOMES.get(d["status"]),
                    "reason": d.get("chain_reason") or d.get("error") or ""}
                   for d in questions if d["status"] != "answer"]
    frame_input = {"title": plan["title"], "goal": plan["goal"],
                        "sections": [{"title": s["title"], "text": s["text"]} for s in sections],
                        "unresolved_questions": not_resolved}
    frame = _with_draft(investigation, "frame", frame_input,
                         lambda: investigation.director.ask("document_frame", frame_input, FRAME_SCHEMA))
    valid_numbers = set(range(1, len(sources) + 1))
    page = compose(status, sections, frame, sources, mappings, valid_numbers)
    file_html = investigation.folder / "document.html"
    file_html.write_text(page, encoding="utf-8")
    file_pdf = investigation.folder / "document.pdf"
    investigation.event({"kind": "document", "text": "Preparing the PDF…"})
    print_pdf(file_html, file_pdf)
    return {"html": str(file_html), "pdf": str(file_pdf), "sources": len(sources), "sections": len(sections)}


def _row(d: dict, label: str) -> str:
    found = re.search(rf"\*\*{label}:?\*\*:?\s*(.+)", d.get("answer") or "")
    return found.group(1).strip() if found else ""


def _level(confidence: str) -> str:
    """Only the confidence level (high, medium, low): the reason is in the answer."""
    match = re.match(r"\W*(high|medium|low)", confidence, re.IGNORECASE)
    return match.group(1).lower() if match else confidence[:40]


def _remove_title(text: str, title: str) -> str:
    """If the director repeated the section title at the top, it is removed (the program adds it)."""
    rows = text.strip().splitlines()
    if rows and rows[0].lstrip("#").strip().lower() == title.strip().lower():
        rows = rows[1:]
    return "\n".join(rows).strip()


def _html(markdown: str, valid_numbers: set[int]) -> str:
    """Markdown → HTML (no raw HTML), with the [n] citations as links to the sources."""
    page = _md.render(markdown or "")

    def connect(match: re.Match) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        if not numbers or not all(n in valid_numbers for n in numbers):
            return match.group(0)
        return "[" + ", ".join(f'<a href="#source-{n}">{n}</a>' for n in numbers) + "]"
    return QUOTE.sub(connect, page)


def _in_line(markdown: str, valid_numbers: set[int]) -> str:
    """Like `_html`, without the surrounding paragraph (for list items)."""
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
        """The uncertain answers of the section: question, value with the source and the reason, written by the program."""
        articles = "".join(
            f"<li><strong>{e(d['question'])}</strong> "
            f"{_in_line(cleaning.text(renumber(_row(d, 'Answer'), mappings.get(d['id'], {})), origin='answer'), valid_numbers)}"
            f" <span class='reason'>Not confirmed: "
            f"{e('; '.join(str(m) for m in d.get('status_reasons') or []) or 'the chain is not fully confirmed')}.</span></li>"
            for d in (per_id[i] for i in s.get("open_points") or [] if i in per_id))
        return (f'<aside class="open-points"><h3>Open points</h3><p>Answers found but not confirmed by the checks: '
                f"do not take them as facts.</p><ul>{articles}</ul></aside>") if articles else ""
    sections_body = "".join(f'<section id="{e(s["id"])}"><h2>{e(s["title"])}</h2>{_html(s["text"], valid_numbers)}'
                            f"{open_points(s)}</section>" for s in sections)
    points = "".join(f"<li>{_in_line(p, valid_numbers)}</li>" for p in frame["key_points"])
    sections_titles = {s["id"]: s["title"] for s in sections}
    questions_rows = "".join(
        f"<tr><td>{e(d['id'])}</td><td>{d['round']}</td><td>{e(sections_titles.get(d['section'], d['section']))}</td>"
        f"<td>{e(d['question'])}</td><td class='outcome-{e(d['status'])}'>{e(OUTCOMES.get(d['status'], d['status']))}</td>"
        f"<td>{_html(renumber(_row(d, 'Answer'), mappings.get(d['id'], {})), valid_numbers)}</td>"
        f"<td>{e(status_of(d))}</td></tr>" for d in questions)
    synthesis, conclusions, limits = (_html(frame[k], valid_numbers) for k in ("synthesis", "conclusions", "limits"))
    cited = {int(n) for n in re.findall(r'href="#source-(\d+)"',
                                         sections_body + points + synthesis + conclusions + limits + questions_rows)}
    statuses = count_statuses(questions)
    checks = list((status.get("sections_check") or {}).values())
    sentences_check = (f"{sum(c['sentences'] for c in checks)} sentences, {sum(c['flagged'] for c in checks)} flagged "
                       f"and rewritten, {sum(c['removed'] for c in checks)} removed by the program because still "
                       "unsupported") if checks else "check not done"
    with_answer = f"{found_items} answered" + (f" ({statuses})" if statuses else "")

    def sources_articles(choices: list[dict]) -> str:
        return "".join(f'<li id="source-{f["number"]}" value="{f["number"]}"><span class="source-title">{e(f["title"])}'
                       f'</span><br><a href="{e(f["url"])}">{e(f["url"])}</a></li>' for f in choices)
    cited_sources = sources_articles([f for f in sources if f["number"] in cited])
    sources_other = sources_articles([f for f in sources if f["number"] not in cited])
    sources_list = f'<h3>Cited sources</h3><ol class="sources">{cited_sources}</ol>' + (
        f'<h3>Other sources consulted</h3><ol class="sources">{sources_other}</ol>' if sources_other else "")
    evaluations = status.get("evaluations") or []
    last = evaluations[-1] if evaluations else None
    method = (
        f"<p>The investigation started from the request: “{e(status['topic'])}”. Claude "
        f"({e(status['director_model'])}) divided the topic into {len(sections)} sections and wrote precise questions, "
        f"each about a single fact. Each question was solved by the chain research: a program that starts from certain "
        f"things, follows the links between the pages of Wikipedia and of the web, and uses Claude "
        f"({e(status['chain_model'])}) only to read a few lines at a time; every cited proof was checked by the program "
        f"in the page it comes from; finally the value is reread, looked up in another source and the chain is reviewed "
        f"by a second model: these checks give the status of each answer (verified, supported, contested, uncertain). "
        f"After each round Claude assessed how well each section was covered and added questions on the gaps and on the "
        f"names that emerged. The text of the sections is written by Claude from the answers: its citations are not "
        f"rechecked by the program on the pages; every sentence of the sections is however reread against the answers "
        f"({sentences_check}); uncertain answers do not enter the text, they are in the “Open points” boxes."
        f"</p><p>Rounds of questions: {status['round']} · questions: {len(questions)}, {with_answer} · cited "
        f"sources: {len(cited)}, consulted: {len(sources)} · duration: {_duration(status.get('seconds', 0))} "
        f"· Claude cost: "
        f"${sum(d.get('cost_usd') or 0 for d in questions) + status.get('director_spent_usd', 0):.2f} · end of "
        f"the investigation: {e(status.get('end_reason') or '')}.</p>"
        + (f"<p>Last evaluation by Claude: {e(last['reason'])}</p>" if last else ""))
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(plan['title'])}</title>
<style>{STYLE}</style></head>
<body><main>
<header class="cover">
<div class="overline">Research document · {date}</div>
<h1>{e(plan['title'])}</h1>
<p class="goal">{e(plan['goal'])}</p>
<p class="facts">{len(questions)} questions · {with_answer} ·
{len(cited)} sources cited out of {len(sources)} consulted · {status['round']} investigation rounds</p>
</header>
<nav class="contents"><h2>Contents</h2><ol><li><a href="#summary">Summary</a></li>{index}
<li><a href="#conclusions">Conclusions</a></li><li><a href="#limitations">Limitations</a></li>
<li><a href="#questions">Appendix A · Questions and answers</a></li><li><a href="#method">Appendix B · Method</a></li>
<li><a href="#sources">Sources</a></li></ol></nav>
<section id="summary" class="summary"><h2>Summary</h2>{synthesis}
<h3>Key points</h3><ul>{points}</ul></section>
{sections_body}
<section id="conclusions"><h2>Conclusions</h2>{conclusions}</section>
<section id="limitations"><h2>Limitations</h2>{limits}</section>
<section id="questions" class="appendix"><h2>Appendix A · Questions and answers</h2>
<div class="table"><table><thead><tr><th>#</th><th>Round</th><th>Section</th><th>Question</th><th>Outcome</th>
<th>Answer</th><th>Status</th></tr></thead><tbody>{questions_rows}</tbody></table></div></section>
<section id="method" class="appendix"><h2>Appendix B · Method</h2>{method}</section>
<section id="sources" class="appendix"><h2>Sources</h2>{sources_list}</section>
</main></body></html>
"""


STYLE = """
:root { --text: #1c2128; --muted: #5a6472; --rule: #d7dce2; --background: #ffffff; --panel: #f1f4f7;
  --accent: #1f5f7a; --ok: #2f7a4b; --ko: #a34040; }
html { background: var(--background); }
body { margin: 0; color: var(--text); background: var(--background);
  font: 11pt/1.55 "Noto Serif", "DejaVu Serif", Georgia, "Times New Roman", serif; }
main { max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.2rem 4rem; }
h1, h2, h3 { font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif; line-height: 1.25; }
h1 { font-size: 1.9rem; margin: .4rem 0 1rem; }
h2 { font-size: 1.35rem; margin: 2.2rem 0 .6rem; padding-top: .6rem; border-top: 1px solid var(--rule); }
h3 { font-size: 1.05rem; margin: 1.4rem 0 .4rem; }
a { color: var(--accent); text-decoration: none; overflow-wrap: anywhere; }
p { margin: .6rem 0; }
.overline { font: 600 .75rem/1.4 "Noto Sans", Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase;
  color: var(--muted); }
.cover { padding-bottom: 1rem; }
.goal { font-size: 1.05rem; }
.facts { color: var(--muted); font-size: .92rem; }
.contents ol { columns: 1; }
.summary { background: var(--panel); padding: .2rem 1.2rem 1rem; border-radius: 4px; }
.summary h2 { border-top: 0; }
table { border-collapse: collapse; width: 100%; font: .82rem/1.4 "Noto Sans", Arial, sans-serif; }
th { text-align: left; border-bottom: 1.5px solid var(--text); padding: .3rem .4rem; vertical-align: bottom; }
td { border-bottom: 1px solid var(--rule); padding: .3rem .4rem; vertical-align: top; }
td p { margin: 0; }
.table { overflow-x: auto; }
.outcome-answer { color: var(--ok); font-weight: 600; }
.outcome-not_found, .outcome-error { color: var(--ko); }
.open-points { border-left: 3px solid var(--ko); background: var(--panel); padding: .1rem 1rem .6rem; margin: 1.2rem 0;
  font-size: .92rem; break-inside: avoid; }
.open-points h3 { margin-top: .8rem; }
.open-points .reason { color: var(--ko); }
ol.sources { font-size: .85rem; padding-left: 1.8rem; }
ol.sources li { margin: .35rem 0; }
.source-title { font-weight: 600; }
@media print {
  main { max-width: none; padding: 0; }
  h2 { break-after: avoid; }
  .cover { min-height: 40vh; }
  .contents { break-after: page; }
  table, tr { break-inside: avoid; }
  .appendix { break-before: page; }
}
"""


def print_pdf(file_html: Path, file_pdf: Path) -> None:
    """The HTML document printed to PDF (A4, page numbers) by the project's Chromium, without network."""
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
