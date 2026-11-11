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
