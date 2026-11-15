"""Controlli del programma sui risultati (docs/solidita-e-validazione.md §4): senza rete né Claude."""
import pytest

from ricerca_catena.web import validazione as v
from ricerca_catena.web.catena import Anello, Catena, Prova


@pytest.mark.parametrize("valore,riga", [
    ("21 giugno 1886", "Hoshiyar died at her palace on June 21, 1886 and was buried in Cairo"),
    ("11 ottobre 1943", "[Michael Stonebraker (en)] Born: (1943-10-11) October 11, 1943"),
    ("11 ottobre 1943", "Born: 1943-10-11 (age 82)"),
    ("1º gennaio 2015", "Istituzione: 1º gennaio 2015"),
    ("1 gennaio 2015", "istituita il 1º gennaio 2015 dalla legge n. 56"),
    ("15. Dezember 1941", "geboren am 15. Dezember 1941 in Wien"),
    ("56 m s.l.m.", "Altitudine: 56 m s.l.m."),
    ("−2 m s.l.m.", "Altitudine: −2 m s.l.m."),
    ("20,65 km²", "Superficie: 20.65 km²"),
    ("1234 abitanti", "Abitanti: 1 234 (2019)"),
    ("36 metri", "Elevation: 118 ft (36 m)"),
    ("00118–00199", "CAP: 00118–00199"),
    ("43010", "Codice postale 43010"),
    ("Novara di Sicilia", "Il simulacro si trova a Novara di Sicilia, in provincia di Messina"),
    ("44 a.C.", "morì nel 44 a.C. a Roma"),
    ("26 luglio 1985", "[Υπουργείο Πολιτισμού (el)] συγκροτήθηκε στις 26 Ιουλίου 1985 από την κυβέρνηση"),
])
def test_valore_scritto_nella_riga(valore: str, riga: str) -> None:
    assert v.valore_nella_riga(valore, riga)


@pytest.mark.parametrize("valore,riga", [
    ("21 giugno 1885", "Hoshiyar died at her palace on June 21, 1886"),
    ("21 luglio 1886", "Hoshiyar died at her palace on June 21, 1886"),
    ("21 luglio 1886", "Morta il 21 giugno 1886; ebbe 7 figli"),
    ("56 m s.l.m.", "Altitudine: 58 m s.l.m."),
    ("5 m", "Altitudine: 56 m, superficie 15 km²"),
    # 14/11: un numero con la virgola non si ritrova a pezzi («9,7» non è «9 … 7»)
    ("9,7 miliardi di dollari", "Nel 1997 il costo previsto era di 9 miliardi per 7 strumenti"),
    ("20,65 km²", "Superficie: 20 km² (65 abitanti)"),
    ("13 dicembre 2004", "David Wheeler was a computer scientist at Cambridge"),
    ("Capri Leone", "Il simulacro si trova a Novara di Sicilia"),
    ("", "qualunque riga"),
])
def test_valore_che_non_e_nella_riga(valore: str, riga: str) -> None:
    assert not v.valore_nella_riga(valore, riga)


@pytest.mark.parametrize("testo,url,atteso", [
    ("[Amsterdam (it)] Altitudine: −2 m", "https://it.wikipedia.org/wiki/Amsterdam", "A"),
    ("Michael Stonebraker was born in Milton", "https://example.org/bio", "B"),
    ("[risultato di ricerca: Michael Stonebraker | Aubrey Research] born in Milton", "https://example.org/bio", "C"),
    ("[risultato di ricerca: Wikipedia] nato a Milton", "https://en.wikipedia.org/wiki/X", "C"),
])
def test_livello_della_fonte(testo: str, url: str, atteso: str) -> None:
    assert v.livello(Prova(testo, url)) == atteso


def test_un_antenato_non_torna_nella_strada() -> None:
    stroustrup = Anello("Bjarne Stroustrup")
    wheeler = Anello("David Wheeler", da=stroustrup)
    burrows = Anello("Michael Burrows (computer scientist)", da=wheeler)
    assert v.ripete_un_antenato(Anello("David Wheeler"), burrows)
    assert v.ripete_un_antenato(Anello("David  wheeler"), burrows)
    assert not v.ripete_un_antenato(Anello("Maurice Wilkes"), burrows)
    assert v.ripete_un_antenato(Anello("Bjarne Stroustrup"), burrows)
    assert v.ripete_un_antenato(Anello("Burrows", titolo=("en", "Michael Burrows (computer scientist)")), burrows)
    romanzo = Anello("I promessi sposi")
    assert not v.ripete_un_antenato(Anello("I promessi sposi (Petrella)"), romanzo)
    assert not v.ripete_un_antenato(Anello("Solférino (Landes)"), Anello("Solferino"))


def _si(controllo: str, prova: Prova) -> dict:
    return {"controllo": controllo, "esito": "sì", "prova": prova, "peso": 1}


def test_legame_provato_solo_da_un_estratto() -> None:
    estratto = Prova("[risultato di ricerca: Aubrey Research] Stonebraker was born in Milton", "https://x.org/a")
    letta = Prova("Stonebraker was born in Milton, New Hampshire", "https://x.org/a")
    assert v.legame_solo_da_estratti(Anello("Milton", da=Anello("S"), esiti=[_si("è in relazione «nato a»", estratto)]))
    assert not v.legame_solo_da_estratti(Anello("Milton", da=Anello("S"), esiti=[_si("è in relazione «nato a»", letta)]))
    assert not v.legame_solo_da_estratti(Anello("Milton", esiti=[]))  # partenza: nessun legame da provare


def test_registro_dei_disaccordi() -> None:
    stonebraker = Anello("Michael Stonebraker")
    riquadro = Prova("[Michael Stonebraker (en)] Born: Newburyport, Massachusetts", "https://en.wikipedia.org/wiki/MS")
    scartato = Anello("Newburyport, Massachusetts", da=stonebraker, prova_legame=riquadro, esiti=[
        {"controllo": "comune del New Hampshire", "esito": "no", "prova": riquadro, "peso": 2}])
    senza_fonte = Anello("Derry", da=stonebraker, esiti=[{"controllo": "c", "esito": "no", "prova": riquadro}])
    no = [{"controllo": "c", "esito": "no", "prova": riquadro}]
    sua_voce = Anello("Hassan", da=stonebraker, esiti=no, prova_legame=Prova(
        "[Hassan (nome) (it)] Hassan è un nome proprio di persona", "https://it.wikipedia.org/wiki/Hassan"))
    frase = Anello("Freeduino", da=stonebraker, esiti=no, prova_legame=Prova(
        "[Michael Stonebraker (en) § Derivati] Quale conseguenza, un gruppo ha fatto Freeduino", "https://en.wikipedia.org/wiki/MS"))
    voci = v.disaccordi([scartato, senza_fonte, sua_voce, frase], "luogo di nascita di")
    assert len(voci) == 1 and voci[0]["nome"] == "Newburyport, Massachusetts" and voci[0]["da"] == "Michael Stonebraker"
    assert voci[0]["url"].endswith("/MS") and voci[0]["contro"] == ["comune del New Hampshire"]
    milton = Anello("Milton", da=stonebraker)
    dover = Anello("Dover", da=Anello("Strafford County", da=milton))
    assert v.disaccordi_sulla_strada(voci, dover) == voci
    assert v.disaccordi_sulla_strada(voci, Anello("Roma", da=Anello("Italia"))) == []
    assert v.disaccordi_sulla_strada(voci, scartato) == []  # la strada passa dal candidato della fonte forte
    fratello = Anello("Milton", da=stonebraker, prova_legame=Prova("[MS (en)] Born: Milton", "https://en.wikipedia.org/wiki/MS"))
    assert v.disaccordi_sulla_strada(voci, fratello) == []  # anche lo scelto ha una fonte forte: sono fratelli


def test_confermata_vuole_tutti_i_controlli() -> None:
    wiki = Prova("[Dover (en)] Elevation: 36 m", "https://en.wikipedia.org/wiki/Dover")
    estratto = Prova("[risultato di ricerca: x] Elevation 36 m", "https://x.org")
    a = Anello("Dover", da=Anello("Contea"), esiti=[_si("è in relazione «capoluogo»", wiki)])
    c = Catena.__new__(Catena)
    c.conflitti = []
    c.valore = {"valore": "36 m", "prova": wiki, "anello": a}
    assert c.controlli()["confermata"]
    c.valore["prova"] = estratto
    assert not c.controlli()["confermata"] and c.controlli()["fonte_valore"] == "C"
    c.valore["prova"] = wiki
    a.debole = True
    assert not c.controlli()["confermata"]
    a.debole = False
    a.esiti = [_si("è in relazione «capoluogo»", Prova("Dover is the county seat", "https://x.org/contea"))]
    c.conflitti = [{"da": "Contea", "nome": "Altro", "url": "u", "riga": "r", "contro": ["c"], "relazione": "capoluogo"}]
    assert not c.controlli()["confermata"] and len(c.controlli()["disaccordi"]) == 1
    c.valore = None
    assert not c.controlli()["confermata"]


# --- prove a tappeto: ogni data in ogni lingua si ritrova, ogni data cambiata di poco no --------------------------

_LINGUE = {
    "it": ("{g} {m} {a}", "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre"),
    "en": ("{m} {g}, {a}", "January February March April May June July August September October November December"),
    "de": ("{g}. {m} {a}", "Januar Februar März April Mai Juni Juli August September Oktober November Dezember"),
    "fr": ("{g} {m} {a}", "janvier février mars avril mai juin juillet août septembre octobre novembre décembre"),
    "es": ("{g} de {m} de {a}", "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"),
    "pt": ("{g} de {m} de {a}", "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro"),
}
_ITALIANI = _LINGUE["it"][1].split()
_DATE = [(g, m, a) for g in (1, 9, 15, 28) for m in range(1, 13) for a in (476, 1703, 1886, 2015)]


@pytest.mark.parametrize("lingua", sorted(_LINGUE))
def test_ogni_data_tradotta_si_ritrova(lingua: str) -> None:
    forma, mesi = _LINGUE[lingua][0], _LINGUE[lingua][1].split()
    for g, m, a in _DATE:
        valore = f"{g} {_ITALIANI[m - 1]} {a}"
        riga = "[Voce (xx)] Evento avvenuto: " + forma.format(g=g, m=mesi[m - 1], a=a) + ", secondo le cronache."
        assert v.valore_nella_riga(valore, riga), (valore, riga)
        assert v.valore_nella_riga(valore, f"Born: {a:04d}-{m:02d}-{g:02d} (aged 70)"), valore


@pytest.mark.parametrize("lingua", sorted(_LINGUE))
def test_ogni_data_cambiata_di_poco_e_rifiutata(lingua: str) -> None:
    forma, mesi = _LINGUE[lingua][0], _LINGUE[lingua][1].split()
    for g, m, a in _DATE:
        riga = "[Voce (xx)] Evento avvenuto: " + forma.format(g=g, m=mesi[m - 1], a=a) + ", secondo le cronache."
        for g2, m2, a2 in ((g + 1, m, a), (g, m % 12 + 1, a), (g, m, a + 1), (g, m, a - 1)):
            valore = f"{g2} {_ITALIANI[m2 - 1]} {a2}"
            assert not v.valore_nella_riga(valore, riga), (valore, riga)


def test_ogni_numero_cambiato_e_rifiutato() -> None:
    for n in (2, 15, 56, 68, 138, 312, 4810, 43010):
        riga = f"[Comune (it)] Altitudine: {n} m s.l.m.; abitanti: 7 (2019)"
        assert v.valore_nella_riga(f"{n} m s.l.m.", riga)
        for altro in (n + 1, n - 1, n * 10, int(str(n)[::-1]) if str(n)[::-1] != str(n) else n + 2):
            if altro != n:
                assert not v.valore_nella_riga(f"{altro} m s.l.m.", riga), (altro, riga)


def test_mesi_senza_doppioni_tra_i_numeri() -> None:
    """Ogni nome di mese sta in un solo numero, tranne quelli uguali in più lingue con lo stesso numero."""
    assert set(v.MESE.values()) == set(range(1, 13))
    for numero, nomi in enumerate(v._MESI, start=1):
        assert all(v.MESE[n] == numero for n in nomi.split())


# --- i casi veri delle domande di controllo (11/11), come li ha visti la catena ------------------------------------

def test_caso_postgresql_estratto_contro_riquadro() -> None:
    """Il riquadro di Wikipedia dice Newburyport; la catena teneva Milton da un estratto, «forte» e «confermata»."""
    stonebraker = Anello("Michael Stonebraker")
    riquadro = Prova("[Michael Stonebraker (en)] Born: (1943-10-11) October 11, 1943 Newburyport, Massachusetts, U.S.",
                     "https://en.wikipedia.org/wiki/Michael_Stonebraker")
    estratto = Prova("[risultato di ricerca: Michael Stonebraker | Computer scientist - Aubrey Research] Michael "
                     "Stonebraker was born in Milton, New Hampshire in 1943.", "https://aubrey.example/stonebraker")
    newburyport = Anello("Newburyport, Massachusetts", da=stonebraker, prova_legame=riquadro, esiti=[
        {"controllo": "comune del New Hampshire", "esito": "no", "prova": riquadro, "peso": 2}])
    milton = Anello("Milton", da=stonebraker, prova_legame=estratto, qualita=0.9,
                    esiti=[_si("è in relazione «luogo di nascita di» con Michael Stonebraker", estratto)])
    assert v.legame_solo_da_estratti(milton)
    dover = Anello("Dover, New Hampshire", da=Anello("Strafford County", da=milton))
    c = Catena.__new__(Catena)
    c.conflitti = v.disaccordi([newburyport], "è in relazione «luogo di nascita di» con Michael Stonebraker")
    c.valore = {"valore": "36 metri", "anello": dover,
                "prova": Prova("[Dover, New Hampshire (en)] Elevation: 118 ft (36 m)", "https://en.wikipedia.org/wiki/Dover")}
    esito = c.controlli()
    assert not esito["confermata"] and [x["nome"] for x in esito["disaccordi"]] == ["Newburyport, Massachusetts"]


def test_caso_aida_il_valore_e_quello_della_riga() -> None:
    riga = "[Hoshiyar Qadin (en)] Hoshiyar died at her palace on 21 June 1886 and was buried in the Khedival Mausoleum"
    assert v.valore_nella_riga("21 giugno 1886", riga) and not v.valore_nella_riga("21 giugno 1885", riga)


def test_caso_cpp_la_catena_non_torna_su_wheeler() -> None:
    stroustrup = Anello("Bjarne Stroustrup")
    wheeler = Anello("David Wheeler (computer scientist)", da=stroustrup, titolo=("en", "David Wheeler (computer scientist)"))
    burrows = Anello("Michael Burrows (computer scientist)", da=Anello("Roger Needham", da=wheeler))
    assert v.ripete_un_antenato(Anello("David Wheeler", titolo=("en", "David Wheeler (computer scientist)")), burrows)
    assert not v.ripete_un_antenato(Anello("David Wheeler"), burrows)  # senza la voce, un omonimo resta possibile


# --- controlli con il modello: decide il programma ------------------------------------------------------------------

@pytest.mark.parametrize("a,b,uguali", [
    ("56 m s.l.m.", "56 metri", True), ("21 giugno 1886", "June 21, 1886", True), ("1 gennaio 2015", "1º gennaio 2015", True),
    ("20,65 km²", "20.65 km2", True), ("Novara di Sicilia", "novara di sicilia", True), ("1886", "1886", True),
    ("−2 m", "2 m", False), ("21 giugno 1886", "21 giugno 1885", False), ("21 giugno 1886", "21 luglio 1886", False),
    ("56 m", "58 m", False), ("Novara di Sicilia", "Capri Leone", False), ("", "", False),
    # 12/11, domande di controllo (Fortran): il valore scritto con una nota dietro è lo stesso valore
    ("10 marzo 1682 (data di istituzione); alcune fonti indicano novembre 1682 per la divisione in tre contee", "10 marzo 1682", True),
    ("138 m s.l.m. (453 ft)", "138 m", True), ("120 m s.l.m.", "138 m s.l.m.", False), ("320 m", "274 m s.l.m.", False),
    ("10 marzo 1682 (alcune fonti: novembre 1682)", "10 novembre 1683", False), ("−2 m (2 metri sotto)", "2 m", False),
])
def test_stesso_valore(a: str, b: str, uguali: bool) -> None:
    assert v.stesso_valore(a, b) is uguali and v.stesso_valore(b, a) is uguali


def test_la_revisione_vale_solo_con_la_riga() -> None:
    righe = [Prova("David Wheeler was a British computer scientist", "u1"), Prova("Maurice Wilkes supervised Wheeler", "u2")]
    risposta = {"regge": "no", "motivo": "non è un fisico", "indizi": [
        {"indizio": "fisico britannico", "esito": "no", "riga": 1, "citazione": "British computer scientist"},
        {"indizio": "relatore di", "esito": "no", "riga": 2, "citazione": "parole che nella riga non ci sono"},
        {"indizio": "inventato", "esito": "no", "riga": 9, "citazione": "British"},
        {"indizio": "relatore", "esito": "sì", "riga": 2, "citazione": "supervised Wheeler"},
        {"indizio": "nato nel 1927", "esito": "non provato", "riga": 0, "citazione": ""}]}
    esito = v.esamina_revisione(risposta, righe)
    assert [o["indizio"] for o in esito["obiezioni"]] == ["fisico britannico"] and esito["obiezioni"][0]["url"] == "u1"
    assert (esito["provati"], esito["non_provati"], esito["regge"]) == (1, 3, "no")
    assert v.esamina_revisione({}, righe)["obiezioni"] == []


_BUONI = {"seconda_lettura": {"esito": "concordi"}, "riscontro": {"esito": "concordi"},
          "revisione": {"esito": "fatta", "regge": "sì", "obiezioni": [], "non_provati": 0}}
_OBIEZIONE = {"esito": "fatta", "regge": "no", "obiezioni": [{"indizio": "fisico", "citazione": "computer scientist"}]}


@pytest.mark.parametrize("cambi,atteso", [
    ({}, "verificata"),
    ({"valore": ""}, "non trovata"),
    # 13/11, taratura sulle 40 domande: anello debole ma revisione «regge: sì» 6 giuste e 0 sbagliate (sulle domande di
    # controllo 13 e 0); con «non provato» 11 giuste e 2 sbagliate. L'anello debole porta a «incerta» solo senza una
    # revisione che regge; con la revisione che regge la risposta è al massimo «sostenuta».
    ({"anelli_deboli": 1}, "sostenuta"),
    ({"anelli_deboli": 2, "esami": {**_BUONI, "revisione": {"esito": "fatta", "regge": "non provato", "obiezioni": [], "non_provati": 1}}}, "incerta"),
    ({"anelli_deboli": 1, "esami": {**_BUONI, "revisione": {"esito": "non fatta"}}}, "incerta"),
    ({"anelli_deboli": 1, "esami": {**_BUONI, "revisione": _OBIEZIONE}}, "incerta"),
    ({"anelli_deboli": 1, "esami": None}, "incerta"),
    ({"anelli_deboli": 1, "esami": {**_BUONI, "riscontro": {"esito": "discordi", "valore": "58 m"}}}, "contestata"),
    ({"anelli_deboli": 1, "fonte_valore": "C"}, "incerta"),
    ({"fonte_valore": "C"}, "incerta"),
    ({"esami": {**_BUONI, "revisione": _OBIEZIONE}}, "incerta"),
    ({"esami": {**_BUONI, "riscontro": {"esito": "discordi", "valore": "21 giugno 1885"}}}, "contestata"),
    ({"esami": {**_BUONI, "seconda_lettura": {"esito": "discordi", "valore": "58 m"}}}, "contestata"),
    ({"disaccordi": [{"relazione": "nato a", "da": "S", "nome": "Newburyport"}]}, "contestata"),
    ({"anelli_deboli": 1, "disaccordi": [{"relazione": "nato a", "da": "S", "nome": "N"}]}, "contestata"),
    ({"anelli_deboli": 1, "esami": None, "disaccordi": [{"relazione": "nato a", "da": "S", "nome": "N"}]}, "incerta"),
    ({"esami": {**_BUONI, "riscontro": {"esito": "sola fonte"}}}, "sostenuta"),
    ({"esami": {**_BUONI, "seconda_lettura": {"esito": "non ritrovato"}}}, "sostenuta"),
    ({"esami": {**_BUONI, "seconda_lettura": {"esito": "non serve"}}}, "verificata"),
    ({"esami": {**_BUONI, "revisione": {"esito": "fatta", "regge": "non provato", "obiezioni": [], "non_provati": 2}}}, "sostenuta"),
    ({"esami": {**_BUONI, "revisione": {"esito": "non fatta"}}}, "sostenuta"),
    ({"esami": None}, "sostenuta"),
])
def test_stato_della_risposta(cambi: dict, atteso: str) -> None:
    base = {"valore": "56 m", "anelli_deboli": 0, "fonte_valore": "A", "disaccordi": [], "esami": _BUONI}
    stato, motivi = v.stato({**base, **cambi})
    assert stato == atteso and stato in v.STATI
    assert bool(motivi) == (stato not in ("verificata", "non trovata"))


# --- 13/11: la premessa della domanda (domande senza risposta: 15 valori inventati su 20) ---------------------------

def _premessa(esito: str, riga: int, citazione: str) -> dict:
    return {"regge": "no", "motivo": "", "indizi": [],
            "premessa": {"esito": esito, "cosa": "Newton aveva una moglie", "riga": riga, "citazione": citazione}}


def test_la_premessa_falsa_vale_solo_con_la_riga() -> None:
    righe = [Prova("Newton never married and had no children", "u1"), Prova("His mother remarried in 1646", "u2")]
    falsa = v.esamina_revisione(_premessa("falsa", 1, "never married"), righe)["premessa"]
    assert falsa == {"esito": "falsa", "cosa": "Newton aveva una moglie", "citazione": "never married", "url": "u1"}
    assert v.esamina_revisione(_premessa("falsa", 2, "never married"), righe)["premessa"]["esito"] == "non provata"
    assert v.esamina_revisione(_premessa("falsa", 0, ""), righe)["premessa"]["esito"] == "non provata"
    assert v.esamina_revisione(_premessa("regge", 1, "never married"), righe)["premessa"]["esito"] == "regge"
    assert v.esamina_revisione({"regge": "sì", "indizi": []}, righe)["premessa"]["esito"] == "assente"  # risposte di prima
    assert v.premessa_smentita({"revisione": v.esamina_revisione(_premessa("falsa", 1, "never married"), righe)})
    assert not v.premessa_smentita({"revisione": v.esamina_revisione(_premessa("falsa", 2, "never married"), righe)})
    assert not v.premessa_smentita({}) and not v.premessa_smentita(None)


_OB = [{"indizio": "fisico britannico", "citazione": "computer scientist", "url": "u"}]


@pytest.mark.parametrize("revisione,deboli,respinta", [
    ({"esito": "fatta", "obiezioni": _OB, "premessa": {"esito": "non provata"}}, 1, True),
    ({"esito": "fatta", "obiezioni": _OB}, 2, False),                                          # revisioni di prima: non si sa
    ({"esito": "fatta", "obiezioni": _OB, "premessa": {"esito": "assente"}}, 2, False),
    ({"esito": "fatta", "obiezioni": _OB, "premessa": {"esito": "regge"}}, 1, False),          # la domanda ha senso: resta incerta
    ({"esito": "fatta", "obiezioni": _OB, "premessa": {"esito": "non provata"}}, 0, False),    # strada tutta confermata
    ({"esito": "fatta", "obiezioni": [], "regge": "no", "premessa": {"esito": "non provata"}}, 1, False),  # «no» senza riga
    ({"esito": "non fatta"}, 3, False),
])
def test_strada_respinta(revisione: dict, deboli: int, respinta: bool) -> None:
    """13/11, taratura (prova_revisione.py): premessa non provata, obiezione con riga e anello debole insieme: 0 risposte
    giuste e 2 sbagliate sulle 40 domande; sulle domande senza risposta 3 valori inventati. Tre cose che non tornano:
    il valore non si dà."""
    esito = v.strada_respinta({"revisione": revisione}, deboli)
    assert (esito == _OB[0]) if respinta else esito is None
    assert v.strada_respinta(None, 2) is None


# --- 15/11: due revisioni (vale la più severa) e il nome con cui la domanda chiama la partenza ----------------------

def _rev(regge: str, obiezioni=(), premessa="regge", nome="", provati=3, non_provati=0) -> dict:
    return {"esito": "fatta", "regge": regge, "obiezioni": list(obiezioni), "provati": provati, "non_provati": non_provati,
            "premessa": {"esito": premessa}, "nome_di_partenza": nome, "motivo": regge}


def test_di_due_revisioni_vale_la_piu_severa() -> None:
    a, b = {"indizio": "a", "citazione": "x", "url": "u"}, {"indizio": "b", "citazione": "y", "url": "u"}
    assert v.unisci_revisioni([_rev("sì"), _rev("sì")])["regge"] == "sì"
    assert v.unisci_revisioni([_rev("sì"), _rev("non provato", non_provati=2)])["regge"] == "non provato"
    unita = v.unisci_revisioni([_rev("sì", provati=4), _rev("no", [a], premessa="non provata", provati=2, non_provati=1)])
    assert (unita["regge"], unita["obiezioni"], unita["provati"], unita["non_provati"]) == ("no", [a], 2, 1)
    assert unita["premessa"]["esito"] == "non provata" and unita["letture"] == 2
    assert v.unisci_revisioni([_rev("no", [a]), _rev("no", [a, b])])["obiezioni"] == [a, b]       # senza doppioni
    falsa = {"esito": "falsa", "cosa": "c", "citazione": "q", "url": "u"}
    assert v.unisci_revisioni([_rev("sì"), {**_rev("no"), "premessa": falsa}])["premessa"] == falsa
    assert v.unisci_revisioni([_rev("sì", premessa="assente"), _rev("sì")])["premessa"]["esito"] == "assente"
    assert v.unisci_revisioni([_rev("sì", nome=""), _rev("sì", nome="Zorvex")])["nome_di_partenza"] == "Zorvex"
    # una sola revisione riuscita: vale quella; nessuna: non fatta
    assert v.unisci_revisioni([{"esito": "non fatta"}, _rev("sì")])["regge"] == "sì"
    assert v.unisci_revisioni([{"esito": "non fatta"}, {"esito": "non fatta"}]) == {"esito": "non fatta"}


@pytest.mark.parametrize("nome,domanda,testi,mancanti", [
    ("Zorvex", "creatore nel 1987 del linguaggio Zorvex?", ["Lua è un linguaggio creato nel 1993"], ["Zorvex"]),
    ("sonda Aurelia 3", "della sonda Aurelia 3, lanciata dall'ESA?", ["Deep Impact fu una sonda della NASA"], ["Aurelia"]),
    ("Museo Castrovillari", "fondò a Oslo il Museo Castrovillari di arte etrusca?", ["Museo di Oslo", "arte etrusca"], ["Castrovillari"]),
    ("Battaglia di Hastings", "nella Battaglia di Hastings del 1066?", ["The Battle of Hastings was fought on 14 October 1066"], []),
    ("linguaggio Python", "il linguaggio Python?", ["[Python (it)] Python è un linguaggio"], []),
    ("Ducato di Valsenia", "capitale del Ducato di Valsenia?", ["Casola Valsenio è un comune", "Il ducato di Urbino"], ["Valsenia"]),
    ("Alan Turing", "il matematico alan turing?", ["nessuna riga utile"], []),          # non è scritto così nella domanda
    ("", "domanda senza nome?", ["x"], []),
    ("il celebre linguaggio dal nome di un serpente", "il celebre linguaggio dal nome di un serpente?", ["Python"], []),
])
def test_nome_di_partenza_assente_dalle_pagine(nome: str, domanda: str, testi: list[str], mancanti: list[str]) -> None:
    """Le parole con la maiuscola del nome con cui la domanda chiama la partenza devono stare in almeno una pagina
    letta: «Zorvex», «Aurelia», «Castrovillari» non ci sono, perché la cosa non esiste. Vale solo per un nome copiato
    dalla domanda; una descrizione senza nomi propri non si può controllare così."""
    assert v.nome_assente(nome, domanda, testi) == mancanti
