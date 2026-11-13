"""Controlli del programma sui risultati della catena: decide il programma, il modello propone.

Vedi docs/solidita-e-validazione.md §4. Qui stanno i controlli senza modello: il valore scritto nella riga citata (G1),
la strada senza entità ripetute (G3), il livello della fonte di una prova (G4), il registro dei disaccordi (G7).
Le funzioni ricevono gli oggetti della catena (`Anello`, `Prova`) senza importarli.
"""
import re
import unicodedata
from urllib.parse import urlsplit

# I mesi per numero, nelle lingue delle voci lette (it, en, de, fr, es, pt) e nelle abbreviazioni inglesi.
_MESI = [
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
MESE = {nome: numero for numero, nomi in enumerate(_MESI, start=1) for nome in nomi.split()}
_NUMERO = re.compile(r"\d(?:[\d.,   ]*\d)?")
_PAROLE_VUOTE = {"the", "del", "della", "dei", "di", "von", "van", "de", "la", "le", "il", "and"}
ESTRATTO = "[risultato di ricerca:"  # come catena.py segna gli estratti dei risultati (pagine non lette)


def _piatto(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", str(testo or "").lower())
    return "".join(c for c in testo if not unicodedata.combining(c))


def _numeri(testo: str) -> set[str]:
    """I numeri di un testo, come sole cifre: sia interi con i separatori («1 234» → 1234, «20,65» → 2065) sia i loro
    gruppi («1», «234»), senza zeri davanti."""
    numeri = set()
    for pezzo in _NUMERO.findall(testo):
        numeri.add(re.sub(r"\D", "", pezzo).lstrip("0") or "0")
        numeri.update(g.lstrip("0") or "0" for g in re.findall(r"\d+", pezzo))
    return numeri


def _mesi(testo: str) -> set[int]:
    return {MESE[p] for p in re.findall(r"[a-z]+", _piatto(testo)) if p in MESE}


def valore_nella_riga(valore: str, riga: str) -> bool:
    """G1. Il valore dato dal modello è scritto nella riga che cita: ogni suo numero (anche con altri separatori), il
    suo mese (in qualunque lingua o come numero: «1943-10-11») e, se non ha numeri, le sue parole. Il modello può
    tradurre una data, non cambiarla (11/11, domande di controllo: il valore non era mai confrontato con la riga)."""
    valore, riga = str(valore or "").strip(), str(riga or "")
    if not valore:
        return False
    pezzi = _NUMERO.findall(valore)
    if pezzi:
        suoi = _numeri(riga)
        for pezzo in pezzi:  # intero («1234» per «1 234») oppure gruppo per gruppo («21», «1886» per «21, 1886»)
            intero = re.sub(r"\D", "", pezzo).lstrip("0") or "0"
            if intero not in suoi and not all((g.lstrip("0") or "0") in suoi for g in re.findall(r"\d+", pezzo)):
                return False
        # Il mese: lo stesso se la riga ne nomina uno; una riga senza mesi riconosciuti («1943-10-11», o una lingua
        # che qui non c'è: «26 Ιουλίου 1985») non si può smentire, bastano giorno e anno.
        mesi, della_riga = _mesi(valore), _mesi(riga)
        return not mesi or not della_riga or bool(mesi & della_riga)
    parole = [p for p in re.findall(r"\w+", _piatto(valore)) if len(p) >= 3 and p not in _PAROLE_VUOTE]
    nella_riga = set(re.findall(r"\w+", _piatto(riga)))
    return bool(parole) and all(p in nella_riga for p in parole)


def livello(prova) -> str:
    """G4. A = voce di Wikipedia letta; B = un'altra pagina letta; C = estratto dei risultati di una ricerca (pagina
    mai letta): può proporre un candidato, non confermare."""
    if prova is None:
        return ""
    if str(prova.testo).startswith(ESTRATTO):
        return "C"
    host = (urlsplit(str(prova.url)).hostname or "").lower()
    return "A" if host == "wikipedia.org" or host.endswith(".wikipedia.org") else "B"


def _chiave(nome: str) -> str:
    return re.sub(r"[\W_]+", "", _piatto(str(nome)))


def _identita(a) -> set:
    """Come riconoscere la stessa entità: il nome intero (con le parentesi: «I promessi sposi (Petrella)» non è «I
    promessi sposi», «Solférino (Landes)» non è «Solferino»), il titolo e l'indirizzo delle sue voci di Wikipedia."""
    segni = {_chiave(a.nome)}
    if getattr(a, "titolo", None):
        segni.add(_chiave(a.titolo[1]))
    segni.update(_chiave(voce.titolo) for voce in (getattr(a, "voci", None) or {}).values())
    return segni - {""}


def ripete_un_antenato(a, nodo) -> bool:
    """G3. Il candidato è già nella strada che porta a `nodo` (lui compreso): una catena non torna su un'entità già
    attraversata (11/11, C++: Stroustrup → Wheeler → … → Burrows → Wheeler, «relatore di» letto al contrario)."""
    mia = _identita(a)
    while nodo is not None:
        if mia & _identita(nodo):
            return True
        nodo = nodo.da
    return False


def _legami(a) -> list[dict]:
    return [e for e in a.esiti if e.get("esito") == "sì" and e.get("prova") is not None
            and str(e.get("controllo", "")).startswith("è in relazione")]


def legame_solo_da_estratti(a) -> bool:
    """G4. Il legame con l'anello di prima è provato solo da estratti di ricerca (11/11, PostgreSQL: «born in Milton,
    New Hampshire» nell'estratto di una pagina mai letta, anello «forte» a 0,9 contro il riquadro di Wikipedia)."""
    legami = _legami(a)
    return a.da is not None and bool(legami) and all(livello(e["prova"]) == "C" for e in legami)


_RIGA_DEL_RIQUADRO = re.compile(r"\[(?P<voce>[^\]]+)\]\s*[^.:\[\]]{1,40}:\s")


def _dal_riquadro(a) -> bool:
    """La riga del legame è una riga «Etichetta: valore» della voce dell'anello di prima (il suo riquadro): la fonte
    dice proprio «il luogo di nascita è questo». Una frase che nomina più candidati (derivati, allievi) o la voce del
    candidato stesso non lo dice."""
    trovata = _RIGA_DEL_RIQUADRO.match(str(a.prova_legame.testo))
    return trovata is not None and _chiave(re.sub(r"\([^)]*\)", " ", a.da.nome)) in _chiave(trovata.group("voce"))


def disaccordi(scartati: list, relazione: str) -> list[dict]:
    """G7. I candidati scartati dai controlli che il riquadro della voce di prima (livello A) indicava: la fonte dice
    una cosa, gli indizi della domanda un'altra. Si registrano invece di perderli."""
    voci = []
    for a in scartati:
        if a.da is None or a.prova_legame is None or livello(a.prova_legame) != "A" or not _dal_riquadro(a):
            continue
        contro = [str(e.get("controllo", "")) for e in a.esiti if e.get("esito") == "no" and e.get("prova") is not None]
        if contro:
            voci.append({"relazione": relazione, "da": a.da.nome, "nome": a.nome, "url": a.prova_legame.url,
                         "riga": " ".join(str(a.prova_legame.testo).split())[:300], "contro": contro[:4]})
    return voci


def _legame_forte(a) -> bool:
    return livello(a.prova_legame) == "A" or any(livello(e["prova"]) == "A" for e in _legami(a))


def disaccordi_sulla_strada(voci: list[dict], anello) -> list[dict]:
    """I disaccordi che toccano la strada della soluzione: dallo stesso anello di partenza una fonte di livello A
    indicava un candidato, e la catena ne ha scelto un altro con una fonte più debole. Se anche lo scelto ha il legame
    in una fonte di livello A sono solo fratelli (gli allievi di Galileo), non un disaccordo: sul rigioco delle 40
    domande, senza questa condizione, 33 catene su 39 avevano «disaccordi»."""
    strada, a = [], anello
    while a is not None:
        strada.append(a)
        a = a.da
    deboli = {_chiave(a.da.nome): _chiave(a.nome) for a in strada if a.da is not None and not _legame_forte(a)}
    return [x for x in voci if _chiave(x["da"]) in deboli and deboli[_chiave(x["da"])] != _chiave(x["nome"])]


# --- controlli con il modello: il modello propone, queste funzioni decidono ---------------------------------------

_MENO = re.compile(r"(?<![\w.,])(?<!\d\s)[−–-]\s?\d|sotto il livello del mare|below sea level", re.I)


def _interi(testo: str) -> set[str]:
    """I numeri di un valore, senza quelli attaccati a una lettera («km2») e senza unire «21, 1886»."""
    return {re.sub(r"\D", "", p).lstrip("0") or "0"
            for p in re.findall(r"(?<![^\W\d_])\d+(?:[.,]\d+)*", str(testo or ""))}


def stesso_valore(a: str, b: str) -> bool:
    """Due valori dicono la stessa cosa: stessi numeri, stesso segno e stesso mese (in qualunque lingua); senza numeri,
    stesso testo. «56 m s.l.m.» = «56 metri»; «21 giugno 1886» = «June 21, 1886»; «−2 m» ≠ «2 m»."""
    # Il valore è quello scritto prima di una nota («10 marzo 1682 (data di istituzione); alcune fonti…»).
    breve_a, breve_b = (re.split(r"\s*[(;]|\s+[—–-]\s+", str(x or "").strip(), maxsplit=1)[0] for x in (a, b))
    numeri_a, numeri_b = _interi(breve_a), _interi(breve_b)
    if numeri_a or numeri_b:
        mesi_a, mesi_b = _mesi(breve_a), _mesi(breve_b)
        return (numeri_a == numeri_b and bool(_MENO.search(str(a))) == bool(_MENO.search(str(b)))
                and (not mesi_a or not mesi_b or mesi_a == mesi_b))
    return bool(_chiave(breve_a)) and _chiave(breve_a) == _chiave(breve_b)


def esamina_revisione(risposta: dict, righe: list) -> dict:
    """G6. Che cosa resta della revisione dopo il controllo del programma: un «no» o un «sì» valgono solo con il numero
    di una riga data e parole che in quella riga ci sono davvero; altrimenti l'indizio è «non provato»."""
    from ..prove.triple import citazione_presente
    obiezioni, provati, non_provati = [], 0, 0
    for indizio in risposta.get("indizi") or []:
        numero, esito = indizio.get("riga"), indizio.get("esito")
        riga = righe[numero - 1] if isinstance(numero, int) and 1 <= numero <= len(righe) else None
        valida = riga is not None and citazione_presente(str(indizio.get("citazione") or ""), riga.testo)
        if esito == "no" and valida:
            obiezioni.append({"indizio": str(indizio.get("indizio", ""))[:200], "url": riga.url,
                              "citazione": str(indizio.get("citazione", ""))[:200]})
        elif esito == "sì" and valida:
            provati += 1
        else:
            non_provati += 1
    # La premessa della domanda: «falsa» solo con le parole di una riga data, come gli indizi.
    detta = risposta.get("premessa") or {}
    numero = detta.get("riga")
    riga = righe[numero - 1] if isinstance(numero, int) and 1 <= numero <= len(righe) else None
    # «assente»: la revisione non ha parlato della premessa (revisioni registrate prima del 13/11): non si sa.
    premessa = {"esito": detta.get("esito") if detta.get("esito") in ("regge", "falsa", "non provata") else "assente"}
    if premessa["esito"] == "falsa":
        if riga is not None and citazione_presente(str(detta.get("citazione") or ""), riga.testo):
            premessa.update(cosa=str(detta.get("cosa", ""))[:200], citazione=str(detta.get("citazione", ""))[:200],
                            url=riga.url)
        else:
            premessa["esito"] = "non provata"
    return {"esito": "fatta", "regge": risposta.get("regge", "non provato"), "obiezioni": obiezioni,
            "provati": provati, "non_provati": non_provati, "premessa": premessa,
            "motivo": str(risposta.get("motivo", ""))[:300]}


def premessa_smentita(esami: dict | None) -> dict | None:
    """La premessa della domanda smentita dalla revisione con una riga (13/11, domande senza risposta: la catena dava
    un valore in 15 casi su 20, seguendo una strada «abbastanza simile»): allora il valore non si dà."""
    premessa = ((esami or {}).get("revisione") or {}).get("premessa") or {}
    return premessa if premessa.get("esito") == "falsa" else None


STATI = ("verificata", "sostenuta", "contestata", "incerta", "non trovata")
FIDUCIA = {"verificata": "alta", "sostenuta": "media", "contestata": "bassa", "incerta": "bassa"}


def stato(c: dict) -> tuple[str, list[str]]:
    """Lo stato della risposta dagli esiti dei controlli (docs/solidita-e-validazione.md §4) e i motivi, in parole."""
    if not c.get("valore"):
        return "non trovata", []
    esami = c.get("esami") or {}
    lettura, riscontro, revisione = (esami.get(k) or {} for k in ("seconda_lettura", "riscontro", "revisione"))
    incerta, contestata, limiti = [], [], []
    # Un anello debole (controlli non tutti provati) porta a «incerta», a meno che la revisione della catena intera
    # regga senza obiezioni: allora resta un limite e la risposta è al massimo «sostenuta» (taratura del 13/11 sulle
    # 40 domande: debole con revisione che regge 6 giuste e 0 sbagliate; debole senza, 16 e 6).
    regge = revisione.get("esito") == "fatta" and revisione.get("regge") == "sì" and not revisione.get("obiezioni")
    if c.get("anelli_deboli") and regge:
        limiti.append(f"{c['anelli_deboli']} anelli con controlli non tutti provati, ma la catena intera regge alla revisione")
    elif c.get("anelli_deboli"):
        incerta.append(f"{c['anelli_deboli']} anelli della catena non confermati")
    if c.get("fonte_valore") == "C":
        incerta.append("il valore viene dall'estratto di una ricerca, da una pagina non letta")
    for o in revisione.get("obiezioni") or []:
        incerta.append(f"la revisione smentisce «{o['indizio']}» («{o['citazione']}»)")
    for d in c.get("disaccordi") or []:
        contestata.append(f"per «{d['relazione']}» la voce di {d['da']} indica {d['nome']}")
    if lettura.get("esito") == "discordi":
        contestata.append(f"una seconda lettura delle stesse righe dà {lettura.get('valore')}")
    if riscontro.get("esito") == "discordi":
        contestata.append(f"un'altra fonte dà {riscontro.get('valore')}")
    if incerta:
        return "incerta", incerta + contestata
    if contestata:
        return "contestata", contestata
    if not esami:
        return "sostenuta", limiti + ["controlli con il modello non fatti"]
    if lettura.get("esito") not in ("concordi", "non serve"):
        limiti.append("la seconda lettura non ha ritrovato il valore")
    if riscontro.get("esito") != "concordi":
        limiti.append("il valore viene da una sola fonte")
    if revisione.get("esito") != "fatta":
        limiti.append("revisione non fatta")
    elif revisione.get("regge") != "sì":
        limiti.append(f"la revisione non trova provati {revisione.get('non_provati', 0)} indizi della domanda")
    return ("sostenuta", limiti) if limiti else ("verificata", [])


def strada_respinta(esami: dict | None, anelli_deboli: int) -> dict | None:
    """Tre cose che non tornano insieme: la revisione ha un'obiezione con riga, non trova provata la premessa della
    domanda, e la strada ha un anello non confermato. Allora il valore non si dà (restituisce l'obiezione). Con la
    premessa che regge la domanda ha senso e la risposta resta «incerta» (taratura del 13/11: 0 giuste perse)."""
    revisione = (esami or {}).get("revisione") or {}
    obiezioni = revisione.get("obiezioni") or []
    if not anelli_deboli or not obiezioni or (revisione.get("premessa") or {}).get("esito") != "non provata":
        return None
    return obiezioni[0]
