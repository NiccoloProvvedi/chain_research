"""Il documento finale di un'indagine: completo, con le fonti, in HTML e in PDF.

- Fonti: tutte quelle delle risposte, numerate una volta sola per tutto il documento (stesso indirizzo = stessa
  fonte); le citazioni [n] di ogni risposta si rinumerano di conseguenza.
- Sezioni: la regia scrive ogni sezione dalle risposte complete delle sue domande (e dai fatti brevi delle altre),
  alcune in parallelo.
- Cornice: sintesi, punti chiave, conclusioni e limiti, dalle sezioni scritte.
- Appendici: tutte le domande con la risposta e la fiducia, come è stata fatta l'indagine; elenco delle fonti.
- PDF: la pagina HTML stampata dal Chromium del progetto.
"""
import html
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from markdown_it import MarkdownIt

from . import configurazione
from .modello import pulizia

if TYPE_CHECKING:
    from .indagine import Indagine

SCHEMA_CORNICE = {"type": "object", "additionalProperties": False,
                  "required": ["sintesi", "punti_chiave", "conclusioni", "limiti"],
                  "properties": {"sintesi": {"type": "string", "minLength": 50},
                                 "punti_chiave": {"type": "array", "minItems": 1, "maxItems": 12,
                                                  "items": {"type": "string"}},
                                 "conclusioni": {"type": "string"}, "limiti": {"type": "string"}}}
CITAZIONE = re.compile(r"\[(\s*\d+\s*(?:[,;]\s*\d+\s*)*)\]")
ESITI = {"risposta": "trovata", "non_trovata": "non trovata", "errore": "errore", "in_attesa": "non fatta",
         "in_corso": "interrotta"}
_md = MarkdownIt("commonmark", {"html": False, "typographer": False}).enable("table")


def _chiave_url(url: str) -> str:
    parti = urlsplit(url)
    return urlunsplit((parti.scheme.lower(), parti.netloc.lower().removeprefix("www."), parti.path.rstrip("/"),
                       parti.query, ""))


def fonti_globali(domande: list[dict]) -> tuple[list[dict], dict[str, dict[int, int]]]:
    """Fonti di tutto il documento (numerate da 1) e, per ogni domanda, numero locale → numero globale."""
    fonti: list[dict] = []
    per_url: dict[str, int] = {}
    mappe: dict[str, dict[int, int]] = {}
    for d in domande:
        mappa = {}
        for f in d.get("fonti") or []:
            chiave = _chiave_url(str(f.get("url") or ""))
            if not chiave:
                continue
            if chiave not in per_url:
                fonti.append({"numero": len(fonti) + 1, "url": f["url"],
                              "titolo": str(f.get("titolo") or f["url"])[:200], "domande": []})
                per_url[chiave] = len(fonti)
            numero = per_url[chiave]
            mappa[int(f["numero"])] = numero
            fonti[numero - 1]["domande"].append(d["id"])
        mappe[d["id"]] = mappa
    return fonti, mappe


def rinumera(testo: str, mappa: dict[int, int]) -> str:
    """[1, 3] della risposta di una domanda → i numeri delle fonti del documento; i numeri sconosciuti spariscono."""
    def sostituisci(trovato: re.Match) -> str:
        numeri = [mappa.get(int(n)) for n in re.findall(r"\d+", trovato.group(1))]
        numeri = list(dict.fromkeys(n for n in numeri if n))
        return f"[{', '.join(map(str, numeri))}]" if numeri else ""
    return CITAZIONE.sub(sostituisci, testo or "")


def _risposta_pulita(d: dict, mappa: dict[int, int]) -> str:
    return pulizia.testo(rinumera(d.get("risposta") or "", mappa), origine="risposta")


def scrivi(indagine: "Indagine") -> dict:
    stato = indagine.stato
    piano = stato["piano"]
    domande = stato["domande"]
    fonti, mappe = fonti_globali(domande)
    risolte = [d for d in domande if d["stato"] in ("risposta", "non_trovata") and d.get("risposta")]
    brevi = [{"sezione": d["sezione"], "domanda": d["domanda"], "esito": ESITI.get(d["stato"]),
              "risposta": pulizia.testo(rinumera(_riga(d, "Risposta"), mappe[d["id"]]), origine="risposta")[:300]}
             for d in risolte]

    def sezione(s: dict) -> dict:
        proprie = [{"domanda": d["domanda"], "esito": ESITI.get(d["stato"]),
                    "risposta": _risposta_pulita(d, mappe[d["id"]])} for d in risolte if d["sezione"] == s["id"]]
        if not proprie:
            return {**s, "testo": "*Nessuna domanda di questa sezione ha trovato una risposta.*"}
        indagine.evento({"tipo": "documento", "testo": f"Scrivo la sezione «{s['titolo']}»…"})
        testo = indagine.regia.chiedi("documento_sezione", {
            "titolo": piano["titolo"], "obiettivo": piano["obiettivo"], "sezione": s, "risposte": proprie,
            "fatti_brevi_altre_sezioni": [b for b in brevi if b["sezione"] != s["id"]]}, None)
        return {**s, "testo": _togli_titolo(str(testo), s["titolo"])}

    with ThreadPoolExecutor(max_workers=3) as esecutore:
        sezioni = list(esecutore.map(sezione, piano["sezioni"]))
    indagine.evento({"tipo": "documento", "testo": "Scrivo sintesi, conclusioni e limiti…"})
    non_risolte = [{"domanda": d["domanda"], "esito": ESITI.get(d["stato"]),
                    "motivo": d.get("motivo_catena") or d.get("errore") or ""}
                   for d in domande if d["stato"] != "risposta"]
    cornice = indagine.regia.chiedi("documento_cornice", {
        "titolo": piano["titolo"], "obiettivo": piano["obiettivo"],
        "sezioni": [{"titolo": s["titolo"], "testo": s["testo"]} for s in sezioni],
        "domande_non_risolte": non_risolte}, SCHEMA_CORNICE)
    numeri_validi = set(range(1, len(fonti) + 1))
    pagina = componi(stato, sezioni, cornice, fonti, mappe, numeri_validi)
    file_html = indagine.cartella / "documento.html"
    file_html.write_text(pagina, encoding="utf-8")
    file_pdf = indagine.cartella / "documento.pdf"
    indagine.evento({"tipo": "documento", "testo": "Preparo il PDF…"})
    stampa_pdf(file_html, file_pdf)
    return {"html": str(file_html), "pdf": str(file_pdf), "fonti": len(fonti), "sezioni": len(sezioni)}


def _riga(d: dict, etichetta: str) -> str:
    trovata = re.search(rf"\*\*{etichetta}:?\*\*:?\s*(.+)", d.get("risposta") or "")
    return trovata.group(1).strip() if trovata else ""


def _togli_titolo(testo: str, titolo: str) -> str:
    """Se la regia ha ripetuto il titolo della sezione in testa, si toglie (lo mette il programma)."""
    righe = testo.strip().splitlines()
    if righe and righe[0].lstrip("#").strip().lower() == titolo.strip().lower():
        righe = righe[1:]
    return "\n".join(righe).strip()


def _html(markdown: str, numeri_validi: set[int]) -> str:
    """Markdown → HTML (niente HTML grezzo), con le citazioni [n] come collegamenti alle fonti."""
    pagina = _md.render(markdown or "")

    def collega(trovato: re.Match) -> str:
        numeri = [int(n) for n in re.findall(r"\d+", trovato.group(1))]
        if not numeri or not all(n in numeri_validi for n in numeri):
            return trovato.group(0)
        return "[" + ", ".join(f'<a href="#fonte-{n}">{n}</a>' for n in numeri) + "]"
    return CITAZIONE.sub(collega, pagina)


def _durata(secondi: float) -> str:
    minuti = round(secondi / 60)
    return f"{minuti // 60} h {minuti % 60} min" if minuti >= 60 else f"{minuti} min"


def componi(stato: dict, sezioni: list[dict], cornice: dict, fonti: list[dict], mappe: dict[str, dict[int, int]],
            numeri_validi: set[int]) -> str:
    piano = stato["piano"]
    domande = stato["domande"]
    trovate = sum(d["stato"] == "risposta" for d in domande)
    e = html.escape
    indice = "".join(f'<li><a href="#{e(s["id"])}">{e(s["titolo"])}</a></li>' for s in sezioni)
    corpo_sezioni = "".join(f'<section id="{e(s["id"])}"><h2>{e(s["titolo"])}</h2>{_html(s["testo"], numeri_validi)}'
                            f"</section>" for s in sezioni)
    punti = "".join(f"<li>{_html(p, numeri_validi).removeprefix('<p>').removesuffix('</p>').strip()}</li>"
                    for p in cornice["punti_chiave"])
    titoli_sezioni = {s["id"]: s["titolo"] for s in sezioni}
    righe_domande = "".join(
        f"<tr><td>{e(d['id'])}</td><td>{d['giro']}</td><td>{e(titoli_sezioni.get(d['sezione'], d['sezione']))}</td>"
        f"<td>{e(d['domanda'])}</td><td class='esito-{e(d['stato'])}'>{e(ESITI.get(d['stato'], d['stato']))}</td>"
        f"<td>{_html(rinumera(_riga(d, 'Risposta'), mappe.get(d['id'], {})), numeri_validi)}</td>"
        f"<td>{e(_riga(d, 'Fiducia')[:80])}</td></tr>" for d in domande)
    elenco_fonti = "".join(
        f'<li id="fonte-{f["numero"]}"><span class="titolo-fonte">{e(f["titolo"])}</span><br>'
        f'<a href="{e(f["url"])}">{e(f["url"])}</a></li>' for f in fonti)
    valutazioni = stato.get("valutazioni") or []
    ultima = valutazioni[-1] if valutazioni else None
    metodo = (
        f"<p>L'indagine è partita dalla richiesta: «{e(stato['argomento'])}». Claude ({e(stato['modello_regia'])}) "
        f"ha diviso l'argomento in {len(sezioni)} sezioni e ha formulato domande precise, ognuna su un solo dato. "
        f"Ogni domanda è stata risolta dalla ricerca a catena: un programma che parte da cose certe, segue i "
        f"collegamenti tra le pagine di Wikipedia e del web, e usa Claude ({e(stato['modello_catena'])}) solo per "
        f"leggere poche righe alla volta; ogni prova citata è stata controllata dal programma nella pagina da cui "
        f"viene. Dopo ogni giro Claude ha valutato quanto ogni sezione fosse coperta e ha aggiunto domande sui buchi "
        f"e sui nomi emersi.</p><p>Giri di domande: {stato['giro']} · domande: {len(domande)}, con risposta "
        f"{trovate} · fonti: {len(fonti)} · durata: {_durata(stato.get('secondi', 0))} · costo di Claude: "
        f"{sum(d.get('costo_usd') or 0 for d in domande) + stato.get('speso_regia_usd', 0):.2f} $ · conclusione "
        f"dell'indagine: {e(stato.get('motivo_fine') or '')}.</p>"
        + (f"<p>Ultima valutazione di Claude: {e(ultima['motivo'])}</p>" if ultima else ""))
    data = datetime.now().strftime("%d/%m/%Y")
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(piano['titolo'])}</title>
<style>{STILE}</style></head>
<body><main>
<header class="copertina">
<div class="sopra">Documento di ricerca · {data}</div>
<h1>{e(piano['titolo'])}</h1>
<p class="obiettivo">{e(piano['obiettivo'])}</p>
<p class="dati">{len(domande)} domande · {trovate} con risposta verificata · {len(fonti)} fonti ·
{stato['giro']} giri di indagine</p>
</header>
<nav class="indice"><h2>Indice</h2><ol><li><a href="#sintesi">Sintesi</a></li>{indice}
<li><a href="#conclusioni">Conclusioni</a></li><li><a href="#limiti">Limiti</a></li>
<li><a href="#domande">Appendice A · Domande e risposte</a></li><li><a href="#metodo">Appendice B · Metodo</a></li>
<li><a href="#fonti">Fonti</a></li></ol></nav>
<section id="sintesi" class="sintesi"><h2>Sintesi</h2>{_html(cornice['sintesi'], numeri_validi)}
<h3>Punti chiave</h3><ul>{punti}</ul></section>
{corpo_sezioni}
<section id="conclusioni"><h2>Conclusioni</h2>{_html(cornice['conclusioni'], numeri_validi)}</section>
<section id="limiti"><h2>Limiti</h2>{_html(cornice['limiti'], numeri_validi)}</section>
<section id="domande" class="appendice"><h2>Appendice A · Domande e risposte</h2>
<div class="tabella"><table><thead><tr><th>#</th><th>Giro</th><th>Sezione</th><th>Domanda</th><th>Esito</th>
<th>Risposta</th><th>Fiducia</th></tr></thead><tbody>{righe_domande}</tbody></table></div></section>
<section id="metodo" class="appendice"><h2>Appendice B · Metodo</h2>{metodo}</section>
<section id="fonti" class="appendice"><h2>Fonti</h2><ol class="fonti">{elenco_fonti}</ol></section>
</main></body></html>
"""


STILE = """
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


def stampa_pdf(file_html: Path, file_pdf: Path) -> None:
    """Il documento HTML stampato in PDF (A4, numeri di pagina) dal Chromium del progetto, senza rete."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configurazione.cartella("cartella_motori"))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium")
        try:
            pagina = browser.new_page()
            pagina.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file:", "data:"))
                         else r.abort())
            pagina.goto(file_html.resolve().as_uri(), wait_until="load")
            pagina.emulate_media(media="print")
            pagina.pdf(path=str(file_pdf), format="A4", print_background=True,
                       margin={"top": "18mm", "bottom": "18mm", "left": "17mm", "right": "17mm"},
                       display_header_footer=True, header_template="<span></span>",
                       footer_template='<div style="font:8px sans-serif;width:100%;text-align:center;color:#777">'
                                       '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        finally:
            browser.close()
