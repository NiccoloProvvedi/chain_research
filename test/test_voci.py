"""Dal nome alla voce di Wikipedia (fasi 2-3 del piano entità e righe): `corrisponde`, `non_entita`, `scegli_voce`."""
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RADICE), str(RADICE / "ponte")]

from ricerca_catena.web.fonti.wikipedia_grafo import Riga, Voce, WikipediaGrafo, corrisponde, non_entita  # noqa: E402


@pytest.mark.parametrize("nome, titolo, contesto, atteso", [
    ("Mario Bettinus", "Mario Bettini", "", "variante"),
    ("Mario Bettinus", "Mario (nome)", "Giuseppe Biancani", ""),  # 23/10: il vecchio «simile» li univa
    ("Silvio Piccolomini", "Papa Pio II", "", ""),
    ("Pio II", "Pio III", "", ""),  # numeri diversi: mai la stessa entità
    ("Democratic Federal Yugoslavia", "Socialist Federal Republic of Yugoslavia", "", ""),
    ("Jugoslavia Democratica Federale", "Jugoslavia Federale Democratica", "", "variante"),
    ("Tux", "Tux (mascot)", "mascotte di Linux", "parentesi"),
    ("Tux", "Tux (mascot)", "Linux", "base"),  # parentesi non giustificata: serve un collegamento
    ("Fermi", "Enrico Fermi", "", "base"),
    ("Giuseppe Biancani", "Biancani", "", ""),  # titolo più corto del nome: un'altra voce
    ("Freedo", "Freedom", "", ""),  # nomi corti: solo un refuso di una lettera
    ("Niccolò Zucchi", "Niccolo Zucchi", "", "esatto"),
    ("il Regno di Francia", "Regno di Francia", "", "esatto"),
    ("La Thuile", "La Thuile (Italia)", "Courmayeur, Valle d'Aosta, Italia", "parentesi"),
    ("Tex", "TeX", "", "variante"),  # maiuscole diverse oltre la prima: non è lo stesso titolo
    ("Plutone", "Plutone (astrologia)", "l'astronomo che scoprì la luna", "base"),  # astronomo ≠ astrologia
    ("Plutone", "Plutone (astronomia)", "l'astronomo che scoprì la luna", "parentesi"),
    ("Monte Bianco", "Traforo del Monte Bianco", "", ""),  # il cognome vale solo per nomi di una parola
])
def test_corrisponde(nome, titolo, contesto, atteso) -> None:
    assert corrisponde(nome, titolo, contesto) == atteso


def test_non_entita() -> None:
    assert non_entita(Voce("Mario (nome)", "it", "u"))
    assert non_entita(Voce("Bettini", "it", "u", categorie=["Cognomi italiani"]))
    assert non_entita(Voce("Lista di gesuiti", "it", "u"))
    assert non_entita(Voce("Mario", "it", "u", disambigua=True))
    assert not non_entita(Voce("Mario Bettini", "it", "u", categorie=["Gesuiti italiani"]))


class Finta:
    """Wikipedia finta per `scegli_voce`: voci intere (parse), ricerca, proprietà/categorie/collegamenti, inizi."""

    def __init__(self, voci: dict, ricerca: dict, info: dict) -> None:
        self.voci, self.ricerca, self.info = voci, ricerca, info

    def __call__(self, url, params):
        if params.get("action") == "parse":
            v = self.voci.get(params["page"])
            if v is None:
                return {"error": {"code": "missingtitle"}}, None
            return {"parse": v}, None
        if params.get("list") == "search":
            return {"query": {"search": [{"title": t, "snippet": ""} for t in self.ricerca.get(params["srsearch"], [])]}}, None
        if params.get("prop") == "extracts":
            return {"query": {"pages": [{"title": t, "extract": f"{t} è una voce."} for t in params["titles"].split("|")]}}, None
        pagine = []
        vicini = set((params.get("pltitles") or "").split("|"))
        for t in params["titles"].split("|"):
            x = self.info.get(t, {})
            pagine.append({"title": t, "pageprops": {"disambiguation": ""} if x.get("disambigua") else {},
                           "categories": [{"title": f"Categoria:{c}"} for c in x.get("categorie", [])],
                           "links": [{"title": v} for v in x.get("verso", []) if v in vicini]})
        return {"query": {"pages": pagine}}, None


def _voce(titolo, testo, collegamenti=()):
    html = f"<p>{testo}</p>" + "".join(f'<p><a href="/wiki/{c.replace(" ", "_")}">{c}</a>.</p>' for c in collegamenti)
    return {"title": titolo, "text": html}


def test_omonimo_scartato_e_voce_collegata_scelta() -> None:
    """«Mario Bettinus»: la ricerca dà «Mario (nome)» e «Mario Bettini»; vince la voce collegata a Biancani."""
    finta = Finta(
        voci={"Giuseppe Biancani": _voce("Giuseppe Biancani", "Gesuita bolognese.", ["Mario Bettini"]),
              "Mario Bettini": _voce("Mario Bettini", "Mario Bettini è stato un gesuita.")},
        ricerca={"Mario Bettinus": ["Mario (nome)", "Mario Bettini"]},
        info={"Mario Bettini": {"verso": ["Giuseppe Biancani"], "categorie": ["Gesuiti italiani"]}})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    v = wiki.scegli_voce("Mario Bettinus", ["it"], contesto="gesuita", vicini=[("it", "Giuseppe Biancani")])
    assert v is not None and v.titolo == "Mario Bettini"


def test_nessuna_voce_meglio_di_un_omonimo() -> None:
    finta = Finta(voci={}, ricerca={"Silvio Piccolomini": ["Papa Pio II", "Piccolomini"]}, info={})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    assert wiki.scegli_voce("Silvio Piccolomini", ["it", "en"], contesto="gesuita") is None


def test_disambiguazione_diventa_candidati_e_vince_il_legame() -> None:
    """«Tux»: la pagina è una disambiguazione; tra le sue voci vince quella che collega Linux (vicino)."""
    finta = Finta(
        voci={"Tux": {"title": "Tux", "text": '<ul><li><a href="/wiki/Tux_(mascotte)">Tux (mascotte)</a></li>'
                                              '<li><a href="/wiki/Tux_(abito)">Tux (abito)</a></li></ul>',
                      "properties": {"disambiguation": ""}},
              "Linux": _voce("Linux", "Linux è un kernel."),
              "Tux (mascotte)": _voce("Tux (mascotte)", "Tux è il pinguino mascotte di Linux.")},
        ricerca={}, info={"Tux (mascotte)": {"verso": ["Linux"]}})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    v = wiki.scegli_voce("Tux", ["it"], contesto="Linux", vicini=[("it", "Linux")])
    assert v is not None and v.titolo == "Tux (mascotte)"


def test_pareggio_chiede_lo_spareggio_e_rispetta_nessuna() -> None:
    finta = Finta(
        voci={"Linux": _voce("Linux", "Linux.", ["Tux (mascotte)", "Tux (gioco)"])},
        ricerca={"Tux": ["Tux (mascotte)", "Tux (gioco)"]},
        info={"Tux (mascotte)": {"verso": ["Linux"]}, "Tux (gioco)": {"verso": ["Linux"]}})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    chieste = []

    def spareggio(nome, opzioni):
        chieste.append([t for t, _ in opzioni])
        return None

    assert wiki.scegli_voce("Tux", ["it"], vicini=[("it", "Linux")], spareggio=spareggio) is None
    assert chieste == [["Tux (mascotte)", "Tux (gioco)"]]


def test_titolo_esatto_senza_legame_passa_da_accetta() -> None:
    finta = Finta(voci={"Niccolò Zucchi": _voce("Niccolò Zucchi", "Niccolò Zucchi è stato un gesuita.")},
                  ricerca={}, info={})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    assert wiki.scegli_voce("Niccolò Zucchi", ["it"]).titolo == "Niccolò Zucchi"
    assert wiki.scegli_voce("Niccolò Zucchi", ["it"], accetta=lambda v: False) is None


def test_parentesi_giustificata_batte_il_titolo_esatto() -> None:
    """«Haiku» in una domanda su un sistema operativo: «Haiku (sistema operativo)», non la poesia."""
    finta = Finta(voci={"Haiku": _voce("Haiku", "Lo haiku è un componimento poetico."),
                        "Haiku (sistema operativo)": _voce("Haiku (sistema operativo)", "Haiku è un sistema operativo.")},
                  ricerca={"Haiku": ["Haiku", "Haiku (sistema operativo)"]}, info={})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    v = wiki.scegli_voce("Haiku", ["it"], contesto="un sistema operativo open source erede di BeOS")
    assert v is not None and v.titolo == "Haiku (sistema operativo)"


def test_categorie_generiche_non_contano() -> None:
    """«Arduino»: «Pittori italiani» non dice niente su una domanda che nomina l'Italia."""
    finta = Finta(voci={"Anna Maria Arduino": _voce("Anna Maria Arduino", "Pittrice.")},
                  ricerca={"Arduino": ["Anna Maria Arduino"]},
                  info={"Anna Maria Arduino": {"categorie": ["Pittori italiani del XVII secolo"]}})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    assert wiki.scegli_voce("Arduino", ["it"], contesto="una piattaforma hardware nata in Italia") is None


def test_rinvio_verso_un_altra_entita_non_vale() -> None:
    """23/10, Arduino: su en.wikipedia «Phillip Torrone» rinvia a «Limor Fried» (la moglie): non è la sua voce."""
    finta = Finta(voci={"Phillip Torrone": {"title": "Limor Fried", "text": "<p>Limor Fried is an engineer.</p>",
                                            "redirects": [{"from": "Phillip Torrone", "to": "Limor Fried"}]},
                        "Adafruit Industries": _voce("Adafruit Industries", "Adafruit.", ["Limor Fried"]),
                        "Mozilla Firefox": {"title": "Firefox", "text": "<p>Firefox is a web browser.</p>",
                                            "redirects": [{"from": "Mozilla Firefox", "to": "Firefox"}]}},
                  ricerca={}, info={"Limor Fried": {"verso": ["Adafruit Industries"]}})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    assert wiki.scegli_voce("Phillip Torrone", ["en"], vicini=[("en", "Adafruit Industries")]) is None
    assert wiki.scegli_voce("Mozilla Firefox", ["en"]).titolo == "Firefox"


def test_dividi_frasi_senza_tagliare_abbreviazioni() -> None:
    """24/10, Wikivoyage: «Wikivoyage e. V., Roland Unger» e le date tedesche «(* 15. Dezember 1941» restano intere."""
    from ricerca_catena.web.fonti.wikipedia_grafo import dividi_frasi
    assert dividi_frasi("Der Vorsitzende des Wikivoyage e. V., Roland Unger, wurde angefragt. Danach kam X.") == [
        "Der Vorsitzende des Wikivoyage e. V., Roland Unger, wurde angefragt.", "Danach kam X."]
    assert len(dividi_frasi("Roland Unger (* 15. Dezember 1941 in Thalheim) war ein Maler. Er lebte.")) == 2
    assert dividi_frasi("J. R. R. Tolkien was born in 1892. He died in 1973.") == [
        "J. R. R. Tolkien was born in 1892.", "He died in 1973."]
    assert dividi_frasi("Nacque nel 1564. Morì nel 1642.") == ["Nacque nel 1564.", "Morì nel 1642."]


def test_nominato_senza_collegamento_e_un_omonimo() -> None:
    """24/10, Wikipedia: la voce tedesca di Wikivoyage nomina «Roland Unger» (presidente dell'associazione) senza
    collegarlo; la voce «Roland Unger» è di un pittore del 1941. Con menzione = "contro" non passa."""
    finta = Finta(voci={"Wikivoyage": {"title": "Wikivoyage", "text": '<p>Der Vorsitzende des Wikivoyage e. V., Roland '
                                       'Unger, wurde von der <a href="/wiki/Wikimedia">Wikimedia</a> angefragt.</p>'},
                        "Roland Unger": _voce("Roland Unger", "Roland Unger (* 15. Dezember 1941) war ein Maler.")},
                  ricerca={}, info={})
    wiki = WikipediaGrafo(ottieni=finta, intervallo_s=0.0)
    wiki.menzione = "contro"
    assert wiki.scegli_voce("Roland Unger", ["de"], vicini=[("de", "Wikivoyage")]) is None
    wiki.menzione = "favore"
    assert wiki.scegli_voce("Roland Unger", ["de"], vicini=[("de", "Wikivoyage")]).titolo == "Roland Unger"


def test_suffissi_societari_non_contano() -> None:
    """29/10, Raspberry Pi: «Premier Farnell Ltd.» è «Premier Farnell»."""
    from ricerca_catena.web.fonti.wikipedia_grafo import senza_suffisso_societario
    from ricerca_catena.web.catena import _chiave_entita
    assert senza_suffisso_societario("Premier Farnell Ltd.") == "Premier Farnell"
    assert senza_suffisso_societario("Ferrari S.p.A.") == "Ferrari"
    assert senza_suffisso_societario("Russell Investments") == "Russell Investments"
    assert corrisponde("Premier Farnell Ltd.", "Premier Farnell") == "esatto"
    assert _chiave_entita("Premier Farnell Ltd.") == _chiave_entita("Premier Farnell")
