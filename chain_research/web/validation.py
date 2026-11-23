"""Controlli del programma sui risultati della catena: decide il programma, il modello propone.

Vedi docs/validation.md §4. Qui stanno i controlli senza modello: il valore scritto nella riga citata (G1),
la strada senza entità ripetute (G3), il livello della fonte di una prova (G4), il registro dei disaccordi (G7).
Le funzioni ricevono gli oggetti della catena (`Anello`, `Prova`) senza importarli.
"""
import re
import unicodedata
from urllib.parse import urlsplit

# I mesi per numero, nelle lingue delle voci lette (it, en, de, fr, es, pt) e nelle abbreviazioni inglesi.
_MONTHS = [
    "gennaio january jan januar janvier enero janeiro",
    "febbraio february feb februar fevrier febrero fevereiro",
    "marzo march mar marz mars marco",
    "aprile april apr avril abril",
    "maggio may mai mayo maio",
    "giugno june jun juni juin junio junho",
    "luglio july jul juli juillet julio julho",
    "agosto august aug aout",
    "settembre september sept sep septembre septiembre setembro",
    "ottobre october oct oktober octobre octubre outubro",
    "novembre november nov noviembre novembro",
    "dicembre december dec dezember decembre diciembre dezembro",
]
MONTH = {name: number for number, names in enumerate(_MONTHS, start=1) for name in names.split()}
_NUMBER = re.compile(r"\d(?:[\d.,   ]*\d)?")
_STOP_WORDS = {"the", "del", "della", "dei", "di", "von", "van", "de", "la", "le", "il", "and"}
SNIPPET = "[risultato di ricerca:"  # come catena.py segna gli estratti dei risultati (pagine non lette)


def _flat(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _numbers(text: str) -> set[str]:
    """I numeri di un testo, come sole cifre: sia interi con i separatori («1 234» → 1234, «20,65» → 2065) sia i loro
    gruppi («1», «234»), senza zeri davanti."""
    numbers = set()
    for piece in _NUMBER.findall(text):
        numbers.add(re.sub(r"\D", "", piece).lstrip("0") or "0")
        numbers.update(g.lstrip("0") or "0" for g in re.findall(r"\d+", piece))
    return numbers


def _months(text: str) -> set[int]:
    return {MONTH[p] for p in re.findall(r"[a-z]+", _flat(text)) if p in MONTH}


def value_in_row(value: str, row: str) -> bool:
    """G1. Il valore dato dal modello è scritto nella riga che cita: ogni suo numero (anche con altri separatori), il
    suo mese (in qualunque lingua o come numero: «1943-10-11») e, se non ha numeri, le sue parole. Il modello può
    tradurre una data, non cambiarla (11/11, domande di controllo: il valore non era mai confrontato con la riga)."""
    value, row = str(value or "").strip(), str(row or "")
    if not value:
        return False
    pieces = _NUMBER.findall(value)
    if pieces:
        its = _numbers(row)
        for piece in pieces:  # intero («1234» per «1 234») oppure gruppo per gruppo («21», «1886» per «21, 1886»)
            whole = re.sub(r"\D", "", piece).lstrip("0") or "0"
            # a gruppi solo se nel valore sono staccati da uno spazio («21, 1886», «1 234»): «9,7» non è «9 … 7»
            to_groups = bool(re.search(r"\s", piece)) and all(
                (g.lstrip("0") or "0") in its for g in re.findall(r"\d+", piece))
            if whole not in its and not to_groups:
                return False
        # Il mese: lo stesso se la riga ne nomina uno; una riga senza mesi riconosciuti («1943-10-11», o una lingua
        # che qui non c'è: «26 Ιουλίου 1985») non si può smentire, bastano giorno e anno.
        months, of_row = _months(value), _months(row)
        return not months or not of_row or bool(months & of_row)
    words = [p for p in re.findall(r"\w+", _flat(value)) if len(p) >= 3 and p not in _STOP_WORDS]
    row_in = set(re.findall(r"\w+", _flat(row)))
    return bool(words) and all(p in row_in for p in words)


def level(proof) -> str:
    """G4. A = voce di Wikipedia letta; B = un'altra pagina letta; C = estratto dei risultati di una ricerca (pagina
    mai letta): può proporre un candidato, non confermare."""
    if proof is None:
        return ""
    if str(proof.text).startswith(SNIPPET):
        return "C"
    host = (urlsplit(str(proof.url)).hostname or "").lower()
    return "A" if host == "wikipedia.org" or host.endswith(".wikipedia.org") else "B"


def _key(name: str) -> str:
    return re.sub(r"[\W_]+", "", _flat(str(name)))


def _identity(a) -> set:
    """Come riconoscere la stessa entità: il nome intero (con le parentesi: «I promessi sposi (Petrella)» non è «I
    promessi sposi», «Solférino (Landes)» non è «Solferino»), il titolo e l'indirizzo delle sue voci di Wikipedia."""
    marks = {_key(a.name)}
    if getattr(a, "title", None):
        marks.add(_key(a.title[1]))
    marks.update(_key(article.title) for article in (getattr(a, "articles", None) or {}).values())
    return marks - {""}


def repeats_ancestor(a, node) -> bool:
    """G3. Il candidato è già nella strada che porta a `nodo` (lui compreso): una catena non torna su un'entità già
    attraversata (11/11, C++: Stroustrup → Wheeler → … → Burrows → Wheeler, «relatore di» letto al contrario)."""
    my = _identity(a)
    while node is not None:
        if my & _identity(node):
            return True
        node = node.parent
    return False


def _ties(a) -> list[dict]:
    return [e for e in a.outcomes if e.get("outcome") == "yes" and e.get("proof") is not None
            and str(e.get("check", "")).startswith("è in relazione")]


def tie_only_from_snippets(a) -> bool:
    """G4. Il legame con l'anello di prima è provato solo da estratti di ricerca (11/11, PostgreSQL: «born in Milton,
    New Hampshire» nell'estratto di una pagina mai letta, anello «forte» a 0,9 contro il riquadro di Wikipedia)."""
    ties = _ties(a)
    return a.parent is not None and bool(ties) and all(level(e["proof"]) == "C" for e in ties)


_INFOBOX_ROW = re.compile(r"\[(?P<voce>[^\]]+)\]\s*[^.:\[\]]{1,40}:\s")


def _from_infobox(a) -> bool:
    """La riga del legame è una riga «Etichetta: valore» della voce dell'anello di prima (il suo riquadro): la fonte
    dice proprio «il luogo di nascita è questo». Una frase che nomina più candidati (derivati, allievi) o la voce del
    candidato stesso non lo dice."""
    found = _INFOBOX_ROW.match(str(a.tie_proof.text))
    return found is not None and _key(re.sub(r"\([^)]*\)", " ", a.parent.name)) in _key(found.group("article"))


def disagreements(discarded: list, relation: str) -> list[dict]:
    """G7. I candidati scartati dai controlli che il riquadro della voce di prima (livello A) indicava: la fonte dice
    una cosa, gli indizi della domanda un'altra. Si registrano invece di perderli."""
    articles = []
    for a in discarded:
        if a.parent is None or a.tie_proof is None or level(a.tie_proof) != "A" or not _from_infobox(a):
            continue
        against = [str(e.get("check", "")) for e in a.outcomes if e.get("outcome") == "no" and e.get("proof") is not None]
        if against:
            articles.append({"relation": relation, "parent": a.parent.name, "name": a.name, "url": a.tie_proof.url,
                         "row": " ".join(str(a.tie_proof.text).split())[:300], "against": against[:4]})
    return articles


def _strong_tie(a) -> bool:
    return level(a.tie_proof) == "A" or any(level(e["proof"]) == "A" for e in _ties(a))


def disagreements_on_route(articles: list[dict], link) -> list[dict]:
    """I disaccordi che toccano la strada della soluzione: dallo stesso anello di partenza una fonte di livello A
    indicava un candidato, e la catena ne ha scelto un altro con una fonte più debole. Se anche lo scelto ha il legame
    in una fonte di livello A sono solo fratelli (gli allievi di Galileo), non un disaccordo: sul rigioco delle 40
    domande, senza questa condizione, 33 catene su 39 avevano «disaccordi»."""
    route, a = [], link
    while a is not None:
        route.append(a)
        a = a.parent
    weak_ones = {_key(a.parent.name): _key(a.name) for a in route if a.parent is not None and not _strong_tie(a)}
    return [x for x in articles if _key(x["parent"]) in weak_ones and weak_ones[_key(x["parent"])] != _key(x["name"])]


# --- controlli con il modello: il modello propone, queste funzioni decidono ---------------------------------------

_LESS = re.compile(r"(?<![\w.,])(?<!\d\s)[−–-]\s?\d|sotto il livello del mare|below sea level", re.I)


def _whole(text: str) -> set[str]:
    """I numeri di un valore, senza quelli attaccati a una lettera («km2») e senza unire «21, 1886»."""
    return {re.sub(r"\D", "", p).lstrip("0") or "0"
            for p in re.findall(r"(?<![^\W\d_])\d+(?:[.,]\d+)*", str(text or ""))}


def same_value(a: str, b: str) -> bool:
    """Due valori dicono la stessa cosa: stessi numeri, stesso segno e stesso mese (in qualunque lingua); senza numeri,
    stesso testo. «56 m s.l.m.» = «56 metri»; «21 giugno 1886» = «June 21, 1886»; «−2 m» ≠ «2 m»."""
    # Il valore è quello scritto prima di una nota («10 marzo 1682 (data di istituzione); alcune fonti…»).
    short_to, short_b = (re.split(r"\s*[(;]|\s+[—–-]\s+", str(x or "").strip(), maxsplit=1)[0] for x in (a, b))
    numbers_to, numbers_b = _whole(short_to), _whole(short_b)
    if numbers_to or numbers_b:
        months_to, months_b = _months(short_to), _months(short_b)
        return (numbers_to == numbers_b and bool(_LESS.search(str(a))) == bool(_LESS.search(str(b)))
                and (not months_to or not months_b or months_to == months_b))
    return bool(_key(short_to)) and _key(short_to) == _key(short_b)


def examine_review(answer: dict, rows: list) -> dict:
    """G6. Che cosa resta della revisione dopo il controllo del programma: un «no» o un «sì» valgono solo con il numero
    di una riga data e parole che in quella riga ci sono davvero; altrimenti l'indizio è «non provato»."""
    from ..proofs.triples import present_quote
    objections, proven, unproven = [], 0, 0
    for clue in answer.get("clues") or []:
        number, outcome = clue.get("row"), clue.get("outcome")
        row = rows[number - 1] if isinstance(number, int) and 1 <= number <= len(rows) else None
        is_valid = row is not None and present_quote(str(clue.get("quote") or ""), row.text)
        if outcome == "no" and is_valid:
            objections.append({"clue": str(clue.get("clue", ""))[:200], "url": row.url,
                              "quote": str(clue.get("quote", ""))[:200]})
        elif outcome == "yes" and is_valid:
            proven += 1
        else:
            unproven += 1
    # La premessa della domanda: «falsa» solo con le parole di una riga data, come gli indizi.
    said = answer.get("premise") or {}
    number = said.get("row")
    row = rows[number - 1] if isinstance(number, int) and 1 <= number <= len(rows) else None
    # «assente»: la revisione non ha parlato della premessa (revisioni registrate prima del 13/11): non si sa.
    premise = {"outcome": said.get("outcome") if said.get("outcome") in ("holds", "refuted", "unproven") else "absent"}
    if premise["outcome"] == "refuted":
        if row is not None and present_quote(str(said.get("quote") or ""), row.text):
            premise.update(what=str(said.get("what", ""))[:200], quote=str(said.get("quote", ""))[:200],
                            url=row.url)
        else:
            premise["outcome"] = "unproven"
    return {"outcome": "done", "holds": answer.get("holds", "unproven"), "objections": objections,
            "proven": proven, "unproven": unproven, "premise": premise,
            "starting_point_name": " ".join(str(answer.get("starting_point_name") or "").split())[:120],
            "reason": str(answer.get("reason", ""))[:300]}


def refuted_premise(exams: dict | None) -> dict | None:
    """La premessa della domanda smentita dalla revisione con una riga (13/11, domande senza risposta: la catena dava
    un valore in 15 casi su 20, seguendo una strada «abbastanza simile»): allora il valore non si dà."""
    premise = ((exams or {}).get("review") or {}).get("premise") or {}
    return premise if premise.get("outcome") == "refuted" else None


STATUSES = ("verified", "supported", "contested", "uncertain", "not found")
CONFIDENCE = {"verified": "alta", "supported": "media", "contested": "bassa", "uncertain": "bassa"}


def status(c: dict) -> tuple[str, list[str]]:
    """Lo stato della risposta dagli esiti dei controlli (docs/validation.md §4) e i motivi, in parole."""
    if not c.get("value"):
        return "not found", []
    exams = c.get("exams") or {}
    reading, corroboration, review = (exams.get(k) or {} for k in ("second_reading", "corroboration", "review"))
    uncertain, contested, limits = [], [], []
    # Un anello debole (controlli non tutti provati) porta a «incerta», a meno che la revisione della catena intera
    # regga senza obiezioni: allora resta un limite e la risposta è al massimo «sostenuta» (taratura del 13/11 sulle
    # 40 domande: debole con revisione che regge 6 giuste e 0 sbagliate; debole senza, 16 e 6).
    holds = review.get("outcome") == "done" and review.get("holds") == "yes" and not review.get("objections")
    if c.get("weak_links") and holds:
        limits.append(f"{c['weak_links']} anelli con controlli non tutti provati, ma la catena intera regge alla revisione")
    elif c.get("weak_links"):
        uncertain.append(f"{c['weak_links']} anelli della catena non confermati")
    if c.get("value_source") == "C":
        uncertain.append("il valore viene dall'estratto di una ricerca, da una pagina non letta")
    for o in review.get("objections") or []:
        uncertain.append(f"la revisione smentisce «{o['clue']}» («{o['quote']}»)")
    for d in c.get("disagreements") or []:
        contested.append(f"per «{d['relation']}» la voce di {d['parent']} indica {d['name']}")
    if reading.get("outcome") == "disagree":
        contested.append(f"una seconda lettura delle stesse righe dà {reading.get('value')}")
    if corroboration.get("outcome") == "disagree":
        contested.append(f"un'altra fonte dà {corroboration.get('value')}")
    if uncertain:
        return "uncertain", uncertain + contested
    if contested:
        return "contested", contested
    if not exams:
        return "supported", limits + ["controlli con il modello non fatti"]
    if reading.get("outcome") not in ("agree", "not needed"):
        limits.append("la seconda lettura non ha ritrovato il valore")
    if corroboration.get("outcome") != "agree":
        limits.append("il valore viene da una sola fonte")
    if review.get("outcome") != "done":
        limits.append("revisione non fatta")
    elif review.get("holds") != "yes":
        limits.append(f"la revisione non trova provati {review.get('unproven', 0)} indizi della domanda")
    return ("supported", limits) if limits else ("verified", [])


def rejected_route(exams: dict | None, weak_links: int) -> dict | None:
    """Tre cose che non tornano insieme: la revisione ha un'obiezione con riga, non trova provata la premessa della
    domanda, e la strada ha un anello non confermato. Allora il valore non si dà (restituisce l'obiezione). Con la
    premessa che regge la domanda ha senso e la risposta resta «incerta» (taratura del 13/11: 0 giuste perse)."""
    review = (exams or {}).get("review") or {}
    objections = review.get("objections") or []
    if not weak_links or not objections or (review.get("premise") or {}).get("outcome") != "unproven":
        return None
    return objections[0]


_SEVERITY = {"yes": 0, "unproven": 1, "no": 2}


def merge_reviews(reviews: list[dict]) -> dict:
    """Di più revisioni della stessa catena vale la più severa (15/11: ripetendo le domande lo stato cambiava in 17
    casi su 36, perché una revisione sola oscilla tra «regge» e «non provato»): il giudizio peggiore, tutte le
    obiezioni con riga, la premessa «regge» solo se regge per tutte."""
    done = [r for r in reviews if r.get("outcome") == "done"]
    if not done:
        return {"outcome": "not done"}
    worst = max(done, key=lambda r: _SEVERITY.get(r.get("holds"), 1))
    objections: list[dict] = []
    for r in done:
        objections += [o for o in r.get("objections") or [] if o not in objections]
    premises = [r.get("premise") or {"outcome": "absent"} for r in done]
    premise = next((p for p in premises if p.get("outcome") == "refuted"), None) \
        or next((p for p in premises if p.get("outcome") == "absent"), None) \
        or ({"outcome": "holds"} if all(p.get("outcome") == "holds" for p in premises) else {"outcome": "unproven"})
    return {**worst, "objections": objections, "premise": premise, "readings": len(done),
            "proven": min(r.get("proven", 0) for r in done), "unproven": max(r.get("unproven", 0) for r in done),
            "starting_point_name": next((r["starting_point_name"] for r in done if r.get("starting_point_name")), "")}


def missing_name(name: str, question: str, texts: list[str]) -> list[str]:
    """Le parole con la maiuscola del nome con cui la domanda chiama la sua partenza che non stanno in nessuna pagina
    letta. Solo se il nome è copiato dalla domanda. Con più parole la prima non conta («Battaglia di Hastings» letta
    in inglese: «Battle of Hastings»). 13/11, domande senza risposta: per 8 entità inventate su 8 la catena aveva dato
    un valore, partendo da un'entità vicina; «Zorvex» o «Aurelia» non stanno in nessuna pagina."""
    name = " ".join(str(name or "").split())
    if not name or name not in " ".join(str(question or "").split()):
        return []
    owned = [p for p in re.findall(r"[^\W\d_]{4,}", name) if p[0].isupper()]
    if len(owned) > 1:
        owned = owned[1:] if name.startswith(owned[0]) else owned
    read = set(re.findall(r"\w+", _flat(" ".join(texts))))
    return [p for p in owned if _flat(p) not in read]


def with_confidence(text: str, status: str, reasons: list[str]) -> str:
    """Il testo della risposta con la riga **Fiducia:** scritta dal programma dallo stato dei controlli."""
    if status not in CONFIDENCE:
        return text
    row = (f"**Fiducia:** {CONFIDENCE[status]} — risposta {status}"
            + (": " + "; ".join(reasons[:4]) if reasons else
               ": valore letto due volte, ritrovato in un'altra fonte, catena riletta senza obiezioni") + ".")
    found = re.search(r"\*\*Fiducia:?\*\*:?[^\n]*", text)
    return text[:found.start()] + row + text[found.end():] if found else f"{text}\n\n{row}"
