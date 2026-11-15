"""Catena guidata (navigatore/catena.py) con un modello finto e pagine finte: percorso, scarti, schede, valore."""
import re

import pytest

from ricerca_catena.web import raccolta as ricerca
from ricerca_catena.web.catena import Catena
from ricerca_catena.prove.concetti import Concetti

PAGINE = {
    "Galileo Galilei allievi": [
        ("https://it.wikipedia.org/wiki/Galileo_Galilei", "Tra gli allievi di Galileo figurano Benedetto Castelli, "
         "Evangelista Torricelli e il gesuita Giuseppe Biancani.")],
    "Giuseppe Biancani allievi": [
        ("https://it.wikipedia.org/wiki/Giuseppe_Biancani", "Giuseppe Biancani (Bologna, 8 marzo 1566) è stato un "
         "gesuita. Tra suoi discepoli figurano Giovanni Riccioli, Mario Bettini, Niccolò Zucchi e Niccolò Cabeo.")],
    "Niccolò Zucchi": [
        ("https://it.wikipedia.org/wiki/Niccol%C3%B2_Zucchi", "Niccolò Zucchi (Parma, 6 dicembre 1586 – Roma, 1670) "
         "è stato un gesuita, rettore del Collegio Romano.")],
    "Giovanni Riccioli": [
        ("https://it.wikipedia.org/wiki/Giovanni_Riccioli", "Giovanni Riccioli (Ferrara, 17 aprile 1598) fu "
         "gesuita e astronomo a Bologna; non fu mai rettore del Collegio Romano.")],
}
FATTI = {  # controllo -> candidati che lo rispettano / lo negano (secondo le pagine finte)
    "gesuita": ({"Giuseppe Biancani", "Niccolò Zucchi", "Giovanni Riccioli"}, {"Benedetto Castelli"}),
    "bolognese": ({"Giuseppe Biancani"}, {"Evangelista Torricelli"}),
    "emiliano": ({"Niccolò Zucchi", "Giovanni Riccioli"}, set()),
    "rettore": ({"Niccolò Zucchi"}, {"Giovanni Riccioli"}),
}


def _riga(prove: str, testo: str) -> int:
    for riga in prove.splitlines():
        m = re.match(r"\[(\d+)\] (.*)", riga)
        if m and testo.lower() in m.group(2).lower():
            return int(m.group(1))
    return 0


def _decidi(punto: str, dati: dict, predefinito):
    if punto == "interpreta":
        return {"riformulazioni": ["x"], "indizi": [], "sotto_domande": ["chi?"]}, "claude"
    if punto == "catena":
        return {"partenza": {"indizio": "scienziato pisano", "entita": "Galileo Galilei"},
                "passi": [{"relazione": "allievi", "tipo": "persona",
                           "controlli": [{"descrizione": "gesuita", "letture": []},
                                         {"descrizione": "bolognese", "letture": []}]},
                          {"relazione": "allievi", "tipo": "persona",
                           "controlli": [{"descrizione": "emiliano", "letture": []},
                                         {"descrizione": "rettore", "letture": ["Collegio Romano"]}]}],
                "risposta": {"cosa": "data di nascita", "controlli": []}, "lingua_ricerca": "it",
                "motivo": "m"}, "claude"
    if punto == "passo":
        return {"ricerche": [f"{dati.get('entita', dati.get('descrizione'))} allievi"]}, "claude"
    if punto == "candidati" and "descrizione" in dati:
        return {"candidati": [{"nome": "Giuseppe Biancani", "prova": _riga(dati["prove"], "Biancani")}]
                if "Biancani" in dati["prove"] else []}, "claude"
    if punto == "candidati":
        nomi = ["Benedetto Castelli", "Evangelista Torricelli", "Giuseppe Biancani", "Giovanni Riccioli",
                "Mario Bettini", "Niccolò Zucchi", "Niccolò Cabeo"]
        segno = {"Galileo Galilei": "allievi di Galileo", "Giuseppe Biancani": "suoi discepoli"}[dati["entita"]]
        righe = [r for r in dati["prove"].splitlines() if segno in r]
        return {"candidati": [{"nome": n, "prova": _riga("\n".join(righe), n.split()[-1])}
                              for n in nomi if any(n.split()[-1] in r for r in righe)]}, "claude"
    if punto == "verifica":
        risposta = []
        for blocco in dati["candidati"].split("\n\n"):
            nome = blocco.splitlines()[0].removeprefix("CANDIDATO: ")
            controlli = []
            for c in dati["controlli"]:
                chiave = re.sub(r"^C\d+: ", "", c.split(" (")[0])
                if chiave.startswith("è in relazione"):  # legame con l'anello di prima
                    origini = [n.split()[-1] for n in c.split("» con ", 1)[1].split(" (")[0].split(" o ")]
                    trovata = next((o for o in origini if o in blocco.split("\n", 1)[-1]), None)
                    controlli.append({"controllo": chiave, "esito": "sì" if trovata else "non so",
                                      "prova": _riga(blocco, trovata) if trovata else 0,
                                      "citazione": trovata or ""})
                    continue
                if chiave.startswith("è di questo tipo"):  # tutti i candidati finti sono persone
                    controlli.append({"controllo": chiave, "esito": "sì", "prova": _riga(blocco, nome.split()[-1]),
                                      "citazione": nome.split()[-1]})
                    continue
                si, no = FATTI[chiave]
                # Zucchi: emiliano e rettore si vedono solo dopo aver letto la sua scheda
                serve = {"emiliano": "Parma", "rettore": "Collegio Romano"}.get(chiave) if "Zucchi" in nome else None
                esito = "sì" if nome in si and (serve is None or serve in blocco) else \
                    ("no" if nome in no else "non so")
                controlli.append({"controllo": chiave, "esito": esito,
                                  "prova": _riga(blocco, nome.split()[-1]) if esito != "non so" else 0,
                                  "citazione": nome.split()[-1]})
            risposta.append({"nome": nome, "controlli": controlli})
        return {"candidati": risposta}, "claude"
    if punto == "valore":
        return ({"valore": "6 dicembre 1586", "prova": _riga(dati["prove"], "6 dicembre 1586"), "motivo": ""}
                if "6 dicembre 1586" in dati["prove"] else {"valore": "", "prova": 0, "motivo": ""}), "claude"
    return predefinito(), "locale"


@pytest.fixture
def catena(monkeypatch: pytest.MonkeyPatch):
    r = ricerca.Raccolta("domanda su Galileo", opzioni={"wikipedia_grafo": False})
    r.concetti = Concetti()
    monkeypatch.setattr(r.pensatore, "decidi", _decidi)
    monkeypatch.setattr(r, "_limite_raggiunto", lambda: "")
    c = Catena(r)
    r.catena = c
    cercate = []

    def cerca_e_leggi(ricerche, pagine, scopo=""):
        cercate.extend(ricerche)
        ultimo = c._ultimo_segmento()
        for q in ricerche:
            chiave = next((k for k in PAGINE if q == k or q.startswith(k + " ")), None)
            for url, testo in PAGINE.get(chiave, []):
                r.concetti.registra(url, url, 1, testo, [], [], [])
        return c._segmenti_dopo(ultimo)

    monkeypatch.setattr(c, "_cerca_e_leggi", cerca_e_leggi)
    c.cercate = cercate
    return c


def test_catena_di_galileo_arriva_a_zucchi(catena) -> None:
    catena.percorri()
    assert [a.nome for a in catena.anelli[0]] == ["Giuseppe Biancani"]  # Castelli e Torricelli scartati
    assert catena.anelli[1][0].nome == "Niccolò Zucchi"
    assert "Giovanni Riccioli" not in [a.nome for a in catena.anelli[1]]  # "no" con prova: non fu rettore
    assert catena.valore["valore"] == "6 dicembre 1586" and "Zucchi" in catena.valore["prova"].url
    assert "Niccolò Zucchi giuseppe biancani" in catena.cercate  # scheda cercata, con il contesto
    testo = catena.materiale()
    assert "Galileo Galilei → Giuseppe Biancani" in testo and "Giuseppe Biancani → Niccolò Zucchi" in testo
    assert "VALORE CERCATO (data di nascita): 6 dicembre 1586" in testo
    assert catena.riepilogo()["valore"]["di"] == "Niccolò Zucchi"


def test_catena_interrotta_senza_candidati(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    PAGINE_VUOTE: dict = {}
    monkeypatch.setattr(catena, "_cerca_e_leggi", lambda ricerche, pagine, scopo="": [])
    assert PAGINE_VUOTE == {}
    catena.percorri()
    assert catena.anelli == [] and catena.motivo.startswith("catena interrotta al passo 1")
    assert catena.r.scrivi().startswith("**Risposta:** Non trovata")


def test_passo_trovato_per_descrizione(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    pagine = dict(PAGINE)
    pagine["Galileo Galilei allievi"] = [("https://it.wikipedia.org/wiki/Galileo_Galilei",
                                          "Tra gli allievi di Galileo figurano Benedetto Castelli ed Evangelista "
                                          "Torricelli.")]
    pagine["gesuita; bolognese allievi"] = [("https://it.wikipedia.org/wiki/Gesuiti_bolognesi",
                                             "Giuseppe Biancani, gesuita nato a Bologna, astronomo e matematico, "
                                             "indicato come allievo di Galileo.")]
    c = catena
    r = c.r

    def cerca_e_leggi(ricerche, pagine_massime, scopo=""):
        c.cercate.extend(ricerche)
        ultimo = c._ultimo_segmento()
        for q in ricerche:
            chiave = next((k for k in pagine if q == k or q.startswith(k + " ")), None)
            for url, testo in pagine.get(chiave, []):
                r.concetti.registra(url, url, 1, testo, [], [], [])
        return c._segmenti_dopo(ultimo)

    monkeypatch.setattr(c, "_cerca_e_leggi", cerca_e_leggi)
    c.percorri()
    assert c.anelli[0][0].nome == "Giuseppe Biancani"
    assert c.storia[0]["tenuti"] == [] and c.storia[1]["tentativo"] == 4
    assert c.valore and c.valore["valore"] == "6 dicembre 1586"


def test_candidati_dal_grafo_quando_la_relazione_non_ne_da(catena) -> None:
    c = catena
    c.r.concetti.registra("https://keyboardio.com/about", "Keyboardio", 1,
                          "Keyboardio was founded by Kaia Dekker and Jesse Vincent in 2014.",
                          [{"nome": "Kaia Dekker", "frase": "Kaia Dekker", "traduzione": ""},
                           {"nome": "Jesse Vincent", "frase": "Jesse Vincent", "traduzione": ""},
                           {"nome": "Keyboardio", "frase": "Keyboardio was founded", "traduzione": ""}],
                          [{"da": "Kaia Dekker", "relazione": "co-fondatrice di", "a": "Keyboardio",
                            "frase": "Keyboardio was founded by Kaia Dekker", "traduzione": "", "perche": "fondatrice"},
                           {"da": "Jesse Vincent", "relazione": "co-fondatore con", "a": "Kaia Dekker",
                            "frase": "founded by Kaia Dekker and Jesse Vincent", "traduzione": "", "perche": "soci"}])
    from ricerca_catena.web.catena import Anello
    trovati: dict = {}
    c._candidati_dal_grafo([Anello("Kaia Dekker")], trovati)
    assert sorted(a.nome for a in trovati.values()) == ["Jesse Vincent", "Keyboardio"]
    assert "founded by Kaia Dekker and Jesse Vincent" in trovati["jessevincent"].prova_legame.testo


# --- Wikipedia come grafo (16/10) ---------------------------------------------------------------------------------

from ricerca_catena.web.fonti.wikipedia_grafo import Riga, Voce, analizza  # noqa: E402


class WikiFinta:
    """Due voci di Wikipedia finte: Galileo (allievi con collegamenti) e Biancani (discepoli), Zucchi (nascita)."""

    def __init__(self) -> None:
        self.voci = {
            "Galileo Galilei": Voce("Galileo Galilei", "it", "https://it.wikipedia.org/wiki/Galileo_Galilei", [
                Riga("Galileo Galilei è stato un fisico pisano.", []),
                Riga("Tra gli allievi di Galileo figurano Benedetto Castelli e il gesuita Giuseppe Biancani.",
                     ["Benedetto Castelli", "Giuseppe Biancani"], sezione="Allievi")]),
            "Giuseppe Biancani": Voce("Giuseppe Biancani", "it", "https://it.wikipedia.org/wiki/Giuseppe_Biancani", [
                Riga("Giuseppe Biancani (Bologna, 8 marzo 1566) è stato un gesuita, matematico e astronomo.", []),
                Riga("Tra suoi discepoli figurano Giovanni Riccioli e Niccolò Zucchi.",
                     ["Giovanni Riccioli", "Niccolò Zucchi"], sezione="Biografia")]),
            "Niccolò Zucchi": Voce("Niccolò Zucchi", "it", "https://it.wikipedia.org/wiki/Niccol%C3%B2_Zucchi", [
                Riga("Niccolò Zucchi (Parma, 6 dicembre 1586 – Roma, 1670) è stato un gesuita, rettore del "
                     "Collegio Romano.", [])]),
            "Giovanni Riccioli": Voce("Giovanni Riccioli", "it", "https://it.wikipedia.org/wiki/Giovanni_Riccioli", [
                Riga("Giovanni Riccioli (Ferrara, 17 aprile 1598) fu gesuita; non fu mai rettore del Collegio "
                     "Romano.", [])]),
            "Benedetto Castelli": Voce("Benedetto Castelli", "it", "https://it.wikipedia.org/wiki/B_Castelli", [
                Riga("Benedetto Castelli (Brescia, 1578) è stato un monaco benedettino.", [])]),
        }
        self.lette: list[str] = []

    def voce(self, titolo, lingua):
        self.lette.append(titolo)
        return self.voci.get(titolo) if lingua == "it" else None

    def trova(self, nome, lingua, contesto="", accetta=None):
        voce = self.voce(nome, lingua)
        return voce if voce is not None and (accetta is None or accetta(voce)) else None

    def scegli_voce(self, nome, lingue, contesto="", vicini=(), parole_ricerca="", accetta=None, spareggio=None):
        for lingua in lingue:
            voce = self.trova(nome, lingua, contesto, accetta)
            if voce is not None:
                return voce
        return None

    def cerca(self, testo, lingua, n=5):
        return []

    def entranti(self, titolo, lingua, n=60):
        return []

    def inizi(self, titoli, lingua):
        return {}

    def url(self, titolo, lingua):
        return f"https://{lingua}.wikipedia.org/wiki/{titolo}"


def test_catena_su_wikipedia_senza_web(catena) -> None:
    """Con le voci di Wikipedia il passo non cerca sul web: candidati dalle righe con i collegamenti, voce del
    candidato raggiunta dal collegamento (niente omonimi), valore dall'inizio della voce."""
    c = catena
    c.wiki = WikiFinta()
    c.percorri()
    assert c.anelli[0][0].nome == "Giuseppe Biancani" and c.anelli[0][0].titolo == ("it", "Giuseppe Biancani")
    assert c.anelli[1][0].nome == "Niccolò Zucchi"
    assert c.valore["valore"] == "6 dicembre 1586" and "wikipedia.org" in c.valore["prova"].url
    assert c.cercate == []  # nessuna ricerca sul web
    assert c.storia[0]["tentativo"] == 1


def test_analizza_riquadro_frasi_e_collegamenti() -> None:
    html = ('<table class="sinottico"><tr><th>Nascita</th><td><a href="/wiki/Parma">Parma</a>, '
            '<a href="/wiki/6_dicembre">6 dicembre</a> 1586</td></tr></table>'
            '<p>Niccolò Zucchi è stato un gesuita. Fu allievo di <a href="/wiki/Giuseppe_Biancani">Giuseppe '
            'Biancani</a><sup class="reference">[1]</sup> a Parma.</p>'
            '<h2>Note</h2><ol><li>Una nota da saltare</li></ol>')
    righe = analizza(html, "Niccolò Zucchi", "it")
    assert righe[0].tipo == "riquadro" and righe[0].testo == "Nascita: Parma, 6 dicembre 1586"
    assert righe[0].collegamenti == ["Parma"]  # il giorno non è un'entità
    frasi = [r for r in righe if r.tipo == "frase"]
    assert [f.testo for f in frasi] == ["Niccolò Zucchi è stato un gesuita.",
                                        "Fu allievo di Giuseppe Biancani a Parma."]
    assert frasi[1].collegamenti == ["Giuseppe Biancani"]
    assert not any("nota" in r.testo for r in righe)


def test_analizza_elenco_nel_riquadro_non_incolla_i_nomi() -> None:
    """23/10, Galileo: «Notable students» con un elenco nel riquadro diventava «Benedetto CastelliMario Guiducci»."""
    html = ('<table class="infobox"><tr><th>Notable students</th><td><div class="plainlist"><ul>'
            '<li><a href="/wiki/Benedetto_Castelli">Benedetto Castelli</a></li>'
            '<li><a href="/wiki/Mario_Guiducci">Mario Guiducci</a><br/>(dal 1620)</li></ul></div></td></tr></table>'
            '<p>Uno <!-- commento --> due <b>tre</b>.</p>')
    righe = analizza(html, "Galileo Galilei", "en")
    assert righe[0].testo == "Notable students: Benedetto Castelli · Mario Guiducci · (dal 1620)"
    assert righe[0].collegamenti == ["Benedetto Castelli", "Mario Guiducci"]
    assert righe[1].testo == "Uno due tre."


def test_voce_con_categorie_disambigua_e_rinvio() -> None:
    """Categorie visibili come riga citabile, disambiguazione dalla proprietà della pagina, rinvio ricordato."""
    from ricerca_catena.web.fonti.wikipedia_grafo import WikipediaGrafo, _disambigua

    def ottieni(url, params):
        if params.get("page") == "Mario":
            return {"parse": {"title": "Mario", "text": "<p>Mario è un nome proprio.</p>",
                              "categories": [{"category": "Pagine_di_disambiguazione"}],
                              "properties": {"disambiguation": "", "wikibase_item": "Q15888821"}}}, None
        return {"parse": {"title": "Mario Bettini", "text": "<p>Mario Bettini è stato un gesuita.</p>",
                          "redirects": [{"from": "Mario Bettinus", "to": "Mario Bettini"}],
                          "categories": [{"category": "Gesuiti_italiani"}, {"category": "Nati_a_Bologna"},
                                         {"category": "BioBot", "hidden": True}],
                          "properties": {"wikibase_item": "Q1898676"}}}, None

    wiki = WikipediaGrafo(ottieni=ottieni, intervallo_s=0.0)
    v = wiki.voce("Mario Bettinus", "it")
    assert v.titolo == "Mario Bettini" and v.rinvio_da == "Mario Bettinus" and v.qid == "Q1898676"
    assert v.categorie == ["Gesuiti italiani", "Nati a Bologna"] and not v.disambigua
    assert v.righe[-1].tipo == "categorie" and v.righe[-1].testo == "Categorie: Gesuiti italiani; Nati a Bologna"
    m = wiki.voce("Mario", "it")
    assert m.disambigua and _disambigua(m)


def test_prova_di_un_altro_candidato_non_vale(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un "sì" con il numero di una riga di un altro candidato diventa "non so" (controllo del programma)."""
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    a, b = Anello("Mario Rossi"), Anello("Luigi Bianchi")
    a.prova_legame = Prova("Mario Rossi è un gesuita.", "u1")
    b.prova_legame = Prova("Luigi Bianchi è un medico.", "u2")

    def decidi(punto, dati, predefinito):
        return {"candidati": [{"nome": "Luigi Bianchi", "controlli": [
            {"controllo": "gesuita", "esito": "sì", "prova": 1, "citazione": "gesuita"}]}]}, "claude"  # di Rossi
    monkeypatch.setattr(c.r.pensatore, "decidi", decidi)
    c._esiti([a, b], [{"descrizione": "gesuita", "letture": []}])
    assert b.esiti[0]["esito"] == "non so" and b.esiti[0]["prova"] is None and b.punti == 0


def test_secondo_piano_se_il_primo_non_arriva(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    piani = []

    def decidi(punto, dati, predefinito):
        if punto == "catena":
            piani.append(dati.get("catena_precedente"))
            if len(piani) == 1:  # primo piano: partenza sbagliata, nessun candidato
                return {"partenza": {"indizio": "x", "entita": "Nessuno"},
                        "passi": [{"relazione": "allievi", "tipo": "persona", "controlli": []}],
                        "risposta": {"cosa": "data di nascita", "controlli": []}, "motivo": "m"}, "claude"
        if punto == "candidati" and dati.get("entita") == "Nessuno":
            return {"candidati": []}, "claude"
        return _decidi(punto, dati, predefinito)
    monkeypatch.setattr(catena.r.pensatore, "decidi", decidi)
    catena.o["piani_paralleli"] = 1
    catena.percorri()
    assert len(piani) == 2 and piani[1]["partenza"] == "Nessuno"
    assert piani[1]["dove_si_e_fermata"].startswith("catena interrotta al passo 1")
    assert catena.valore and catena.valore["valore"] == "6 dicembre 1586"


def test_valore_in_testa_alla_risposta(catena) -> None:
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    c.valore = {"valore": "Mitchell Baker", "prova": Prova("x", "u"), "anello": Anello("MPL"), "motivo": ""}
    testo = "**Risposta:** La persona indicata, con dubbi sulla versione 1.1, è forse lei [3].\n\n**Limiti:** x"
    assert c._valore_in_testa(testo).startswith("**Risposta:** Mitchell Baker.\n\n**Spiegazione:** La persona")
    gia = "**Risposta:** **Mitchell Baker** [3].\n"
    assert c._valore_in_testa(gia) == gia


def test_citazione_inventata_non_vale(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    a = Anello("Daniel Murphy")
    a.prova_legame = Prova("Daniel Murphy worked on TENEX at BBN.", "u1")

    def decidi(punto, dati, predefinito):
        return {"candidati": [{"nome": "Daniel Murphy", "controlli": [
            {"controllo": "premio nel 2009", "esito": "sì", "prova": 1, "citazione": "received the award in 2009"},
            {"controllo": "lavorò a TENEX", "esito": "sì", "prova": 1, "citazione": "worked on TENEX"}]}]}, "claude"
    monkeypatch.setattr(c.r.pensatore, "decidi", decidi)
    c._esiti([a], [{"descrizione": "premio nel 2009", "letture": []}, {"descrizione": "TENEX", "letture": []}])
    assert [e["esito"] for e in a.esiti] == ["non so", "sì"] and a.punti == 1


def test_due_letture_si_solo_se_concordi() -> None:
    from ricerca_catena.web.catena import Catena, Prova
    p = Prova("riga", "u")
    primi = [{"controllo": "a", "esito": "sì", "prova": p}, {"controllo": "b", "esito": "sì", "prova": p},
             {"controllo": "c", "esito": "non so", "prova": None}]
    secondi = [{"controllo": "a", "esito": "sì", "prova": p}, {"controllo": "b", "esito": "non so", "prova": None},
               {"controllo": "c", "esito": "no", "prova": p}]
    assert [e["esito"] for e in Catena._unisci_esiti(primi, secondi)] == ["sì", "non so", "no"]


def test_valore_ammesso_tipo_e_intervallo():
    from ricerca_catena.web.catena import valore_ammesso
    data = {"tipo_valore": "data completa", "minimo": 1945, "massimo": 1945}
    assert valore_ammesso("29 novembre 1943", data) and not valore_ammesso("7 marzo 1945", data)
    assert valore_ammesso("1945", data) == "non è una data completa"
    abitanti = {"tipo_valore": "numero", "minimo": 300, "massimo": 350}
    assert valore_ammesso("3.274 abitanti", abitanti) and not valore_ammesso("324", abitanti)
    assert not valore_ammesso("20,65 km²", {"tipo_valore": "numero"})
    assert not valore_ammesso("118 m s.l.m.", {"tipo_valore": "numero", "minimo": 100, "massimo": 130})
    assert valore_ammesso("98 m s.l.m.", {"tipo_valore": "numero", "minimo": 100, "massimo": 130})
    assert not valore_ammesso("28 ottobre", {"tipo_valore": "giorno e mese"})
    assert valore_ammesso("Simone", {"tipo_valore": "giorno e mese"})


def test_qualita_pesata_e_in_parte(catena):
    from ricerca_catena.web.catena import Anello, qualita
    esiti = [{"controllo": "insegnante di Mascagni", "esito": "sì", "peso": 3},
             {"controllo": "librettista", "esito": "in parte", "peso": 2},
             {"controllo": "è di questo tipo: persona", "esito": "non so", "peso": 1}]
    assert qualita(esiti) == (3 + 1) / 6
    a = Anello("Praga", esiti=[{"controllo": "insegnante di Mascagni", "esito": "no", "peso": 3, "prova": object()},
                               {"controllo": "librettista", "esito": "sì", "peso": 2, "prova": object()}])
    assert catena._smentito(a)  # un "no" pesante non si compensa con un "sì" leggero
    uniti = catena._unisci_esiti([{"controllo": "x", "esito": "sì", "prova": 1}],
                                 [{"controllo": "x", "esito": "in parte", "prova": 2}])
    assert uniti[0]["esito"] == "in parte"


def test_controllo_dal_codice(catena):
    controlli = [{"descrizione": "gesuita", "peso": 3}, {"descrizione": "bolognese", "peso": 1}]
    assert catena._risolvi_controllo("C2", controlli)["descrizione"] == "bolognese"
    assert catena._risolvi_controllo("gesuita (letture: )", controlli)["peso"] == 3
    assert catena._risolvi_controllo("C9", controlli) is None


def test_frontiera_torna_al_candidato_di_riserva(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il candidato migliore del passo 1 non porta a niente: la frontiera prova da sola quello di riserva, senza un
    nuovo piano (DeepSearch)."""
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    c.piano = {"partenza": {"entita": "P", "indizio": ""}, "risposta": {"cosa": "x"},
               "passi": [{"relazione": "r1", "controlli": []}, {"relazione": "r2", "controlli": []}]}
    monkeypatch.setattr(c, "prepara", lambda: True)
    figli = {"P": [("A", 0.9), ("B", 0.6)], "A": [], "B": [("C", 0.8)]}

    def tentativo(t, numero, passo, fascio, visti, tutti=False):
        origine = fascio[0]
        if t not in (1, 3):
            return []
        nuovi = []
        for nome, q in figli[origine.nome]:
            if nome not in visti:
                visti.add(nome)
                nuovi.append(Anello(nome, qualita=q, da=origine, prova_legame=Prova("riga", "u")))
        return nuovi

    def valore(a, tentativi):
        if a.nome == "C":
            c.valore = {"valore": "42", "prova": Prova("42", "u"), "anello": a, "motivo": ""}
            return True
        return False
    monkeypatch.setattr(c, "_tentativo", tentativo)
    monkeypatch.setattr(c, "_valore_entita", valore)
    c.o["piani_catena"] = 1
    c.o["piani_paralleli"] = 1
    c.percorri()
    assert c.valore["valore"] == "42" and [x.nome for x in c._percorso(c.valore["anello"])] == ["B", "C"]
    assert c.anelli[0][0].nome == "A"  # A resta il migliore del passo 1, ma senza figli


def test_ancora_porta_direttamente_al_passo(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un'entità nominata dalla domanda (ancora) porta al risultato del passo 2 con una sua relazione: la frontiera la
    esplora prima della partenza lontana."""
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    c.piano = {"partenza": {"entita": "P", "indizio": ""}, "risposta": {"cosa": "x"},
               "ancore": [{"entita": "Mascagni", "relazione": "insegnanti di", "passo": 2}],
               "passi": [{"relazione": "r1", "controlli": []}, {"relazione": "librettisti di", "controlli": []}]}
    monkeypatch.setattr(c, "prepara", lambda: True)
    espansi = []

    def tentativo(t, numero, passo, fascio, visti, tutti=False):
        espansi.append((fascio[0].nome, numero, passo["relazione"]))
        if fascio[0].nome == "Mascagni" and t == 3:
            return [Anello("Ponchielli", qualita=0.8, da=fascio[0], prova_legame=Prova("Ponchielli insegnò", "u"))]
        return []

    def valore(a, tentativi):
        c.valore = {"valore": "31 agosto 1834", "prova": Prova("31 agosto 1834", "u"), "anello": a, "motivo": ""}
        return a.nome == "Ponchielli"
    monkeypatch.setattr(c, "_tentativo", tentativo)
    monkeypatch.setattr(c, "_valore_entita", valore)
    c.o["piani_catena"] = 1
    c.o["piani_paralleli"] = 1
    c.percorri()
    assert espansi[0] == ("Mascagni", 2, "insegnanti di")  # l'ancora vicina alla risposta va per prima
    assert c.valore["anello"].nome == "Ponchielli"
    assert "PARTENZA: Mascagni" in c.materiale() and "«insegnanti di» Mascagni → Ponchielli" in c.materiale()


def test_piano_alternativo_nella_stessa_frontiera(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il primo piano non porta a niente; quello alternativo, chiesto subito, arriva al valore nella stessa frontiera,
    senza un secondo giro."""
    chiesti = []

    def decidi(punto, dati, predefinito):
        if punto == "catena":
            chiesti.append(dati)
            if len(chiesti) == 1:
                return {"partenza": {"indizio": "x", "entita": "Nessuno"},
                        "passi": [{"relazione": "allievi", "tipo": "persona", "controlli": []}],
                        "risposta": {"cosa": "data di nascita", "controlli": []}, "motivo": "m"}, "claude"
        if punto == "candidati" and dati.get("entita") == "Nessuno":
            return {"candidati": []}, "claude"
        return _decidi(punto, dati, predefinito)
    monkeypatch.setattr(catena.r.pensatore, "decidi", decidi)
    catena.percorri()
    assert len(chiesti) == 2 and chiesti[1]["alternativa_a"]["partenza"] == "Nessuno"
    assert catena.valore["valore"] == "6 dicembre 1586" and catena.piano["partenza"]["entita"] == "Galileo Galilei"
    assert any("[piano 2]" in x for x in catena.azioni)


def test_verifica_vede_la_riga_decisiva_oltre_il_riquadro(catena) -> None:
    from ricerca_catena.web.catena import Anello
    from ricerca_catena.web.fonti.wikipedia_grafo import Riga, Voce
    righe = [Riga(f"Campo {i}: valore {i}", [], "riquadro", "") for i in range(25)]
    righe.append(Riga("Linards (Francia)", ["Linards"], "elenco", "Gemellaggi"))
    a = Anello("Gravellona Lomellina")
    a.voci = {"it": Voce("Gravellona Lomellina", "it", "https://it.wikipedia.org/wiki/G", righe, {})}
    a.voci_cercate = True
    catena.wiki = object()  # con Wikipedia attiva (le voci sono già lette)
    prove = catena._prove_wiki_candidato(a, [{"descrizione": "gemellato con un paese dell'Alta Vienna",
                                              "letture": []}])
    assert any("Linards" in p.testo for p in prove)
    a.esiti = [{"controllo": "gemellato", "esito": "non so", "peso": 3, "prova": None},
               {"controllo": "provincia di Pavia", "esito": "sì", "peso": 2, "prova": object()}]
    assert catena._decisivo_ignoto(a)


def test_voci_altre_lingue_aggiunte_dopo_la_verifica(catena) -> None:
    from ricerca_catena.web.catena import Anello
    from ricerca_catena.web.fonti.wikipedia_grafo import Riga, Voce

    class Wiki:
        def voce(self, titolo, lingua):
            return Voce(titolo, lingua, f"https://{lingua}.wikipedia.org/wiki/W3C",
                        [Riga("Founded 1 October 1994" if lingua == "en" else "Fondazione ottobre 1994")],
                        {"en": "World Wide Web Consortium", "it": "World Wide Web Consortium"})
    catena.wiki = Wiki()
    a = Anello("World Wide Web Consortium", titolo=("it", "World Wide Web Consortium"))
    assert [v.lingua for v in catena._voci(a, altre=0)] == ["it"]  # verifica: una lingua
    assert sorted(v.lingua for v in catena._voci(a)) == ["en", "it"]  # valore: si aggiungono le altre


def test_voce_adiacente_per_il_controllo_decisivo(catena) -> None:
    from ricerca_catena.web.catena import Anello
    from ricerca_catena.web.fonti.wikipedia_grafo import Riga, Voce

    class Wiki:
        def inizi(self, titoli, lingua):
            return {"Linards": "Linards è un comune francese situato nel dipartimento dell'Alta Vienna."}
    catena.wiki = Wiki()
    a = Anello("Gravellona Lomellina")
    a.voci = {"it": Voce("Gravellona Lomellina", "it", "https://it.wikipedia.org/wiki/G",
                         [Riga("Linards (Francia)", ["Linards"], "elenco", "Gemellaggi")], {})}
    a.voci_cercate, a.voci_altre = True, 5
    prove = catena._prove_wiki_candidato(a, [{"descrizione": "gemellato con un paese dell'Alta Vienna",
                                              "letture": [], "peso": 3}])
    assert "dipartimento dell'Alta Vienna" in prove[0].testo


def test_lingua_della_ricerca(catena) -> None:
    """Inglese di base; la lingua del paese quando il piano la sceglie (22/10, Niccolò)."""
    catena.piano = {"lingue": ["el"]}
    assert catena._lingue() == ["en", "el", "it"]
    catena.piano = {"lingua_ricerca": "it", "lingue": ["fr"]}
    assert catena._lingue() == ["it", "en", "fr"]


def test_lettura_spenta_salva_il_pezzo_senza_modello(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Con lettura_catena spenta il blocco letto si salva senza chiamare il modello (22/10)."""
    r = catena.r
    r.o["lettura_catena"] = False
    monkeypatch.setattr(r.pensatore, "chiedi", lambda *a, **k: pytest.fail("il modello non va chiamato"))
    ultimo = catena._ultimo_segmento()
    assert r._leggi_blocco("Freedo", "https://example.org/freedo", 1, 1, "Freedo was drawn by Rubén.") == (2, "")
    assert any("Freedo was drawn" in s["testo"] for s in catena._segmenti_dopo(ultimo))


def test_partenza_omonima_non_passa_per_il_nome(catena) -> None:
    from ricerca_catena.web.catena import Anello
    from ricerca_catena.web.fonti.wikipedia_grafo import Riga, Voce
    a = Anello("Freedo", contesto="Freedo, la mascotte derivata dal pinguino Tux")
    produttore = Voce("Freedo", "en", "u", [Riga("Freedo is a Swedish record producer and songwriter.")], {})
    mascotte = Voce("Freedo", "it", "u", [Riga("Freedo è la mascotte di Linux-libre, un pinguino derivato da Tux.")], {})
    assert not catena._pertinente(produttore, a) and catena._pertinente(mascotte, a)


def test_a_parita_vince_la_co_occorrenza(catena) -> None:
    """Quattro città a pari merito senza legame provato: passa davanti quella che compare nei pezzi salvati
    sull'entità di prima (logica del grafo, 22/10)."""
    from ricerca_catena.web.catena import Anello
    c = catena
    c.r.concetti.registra("https://example.org/skyos", "SkyOS", 1,
                          "Robert Szeleney, born in Wels, started SkyOS in 1996.", [], [], [])
    szeleney = Anello("Robert Szeleney")
    citta = [Anello(n, qualita=0.6, da=szeleney, debole=True) for n in ("Bad Ischl", "Gmunden", "Traun", "Wels")]
    assert sorted(citta, key=c._ordine)[0].nome == "Wels"
    c.piano = {"passi": [{}, {}]}
    assert c._priorita(citta[3], 2, "valore") > c._priorita(citta[0], 2, "valore")


def test_righe_wiki_elenco_unito_riquadro_pulito_e_poche_righe() -> None:
    """Fase 4 (23/10): elenco della stessa sezione in una riga, righe inutili del riquadro fuori, al massimo 20."""
    from ricerca_catena.web.catena import Catena
    righe = [Riga("Immagine: Debian-logo.svg", [], "riquadro"), Riga("Sviluppatore: Progetto Debian",
             ["Progetto Debian"], "riquadro"), Riga("Debian è un sistema operativo libero composto da software "
             "libero, sviluppato dalla comunità del Progetto Debian.", [], "frase")]
    righe += [Riga(f"Distro{i}", [f"Distro{i}"], "elenco", "Distribuzioni derivate") for i in range(40)]
    righe += [Riga(f"Una frase qualunque numero {i} sulla storia.", [], "frase", "Storia") for i in range(60)]
    voce = Voce("Debian", "it", "u", righe)
    c = Catena.__new__(Catena)
    prove = c._righe_wiki([voce], ["opera derivata", "derivate fork"])
    testi = [p.testo for p in prove]
    assert len(prove) <= 20
    assert not any("Immagine" in t for t in testi)
    assert any("Distro0 · Distro1" in t and "Distro39" in " ".join(testi) for t in testi)
    assert any("sistema operativo libero" in t for t in testi)  # la definizione entra sempre


def test_candidato_che_non_sta_nella_riga_scartato(catena) -> None:
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    prove = [Prova("Tra suoi discepoli figurano Giovanni Riccioli e Niccolò Zucchi.", "u",
                   ["Giovanni Battista Riccioli", "Niccolò Zucchi"], "it")]
    candidati: dict = {}
    c._aggiungi([{"nome": "Niccolò Zucchi", "prova": 1}, {"nome": "Mario Bettini", "prova": 1},
                 {"nome": "Giovanni Battista Riccioli", "prova": 1}, {"nome": "Niccolò Cabeo", "prova": 7}],
                prove, Anello("Giuseppe Biancani"), candidati)
    assert sorted(a.nome for a in candidati.values()) == ["Giovanni Battista Riccioli", "Niccolò Zucchi"]


def test_controllo_che_ripete_il_legame_non_conta_due_volte(catena) -> None:
    """Fase 5 (23/10, paper §9.1): «indicato come allievo di Galileo» è il legame: diventa una sua lettura."""
    from ricerca_catena.web.catena import Anello
    passo = {"relazione": "allievi di", "tipo": "persona",
             "controlli": [{"descrizione": "indicato come allievo di Galileo", "peso": 2},
                           {"descrizione": "gesuita nato a Bologna", "peso": 3}]}
    controlli = catena._controlli_passo(passo, [Anello("Galileo Galilei")])
    descrizioni = [c["descrizione"] for c in controlli]
    assert "indicato come allievo di Galileo" not in descrizioni and "gesuita nato a Bologna" in descrizioni
    assert "indicato come allievo di Galileo" in controlli[-1]["letture"]


def test_forte_solo_con_un_tratto_identificante_provato() -> None:
    from ricerca_catena.web.catena import Anello, Catena
    a = Anello("Mario Guiducci")
    a.esiti = [{"controllo": "è in relazione «allievi di» con Galileo Galilei", "esito": "sì", "prova": 3, "peso": 2},
               {"controllo": "gesuita nato a Bologna", "esito": "non so", "prova": 0, "peso": 2},
               {"controllo": "è di questo tipo: persona", "esito": "sì", "prova": 3, "peso": 1}]
    assert not Catena._identificato(a)
    a.esiti[1] = {**a.esiti[1], "esito": "sì", "prova": 5}
    assert Catena._identificato(a)


def test_varianti_dello_stesso_nome_un_solo_candidato(catena) -> None:
    """23/10, Arduino: «Keyboard.io» e «Keyboardio» erano due candidati (e due espansioni con pagine web)."""
    from ricerca_catena.web.catena import Anello, Prova
    prove = [Prova("Keyboardio (Keyboard.io) makes the Keyboard.io Model01.", "u", [], "en")]
    candidati: dict = {}
    catena._aggiungi([{"nome": "Keyboard.io", "prova": 1}, {"nome": "Keyboardio", "prova": 1},
                      {"nome": "Keyboard.io Model01", "prova": 1}], prove, Anello("Arduino"), candidati)
    assert [a.nome for a in candidati.values()] == ["Keyboard.io", "Keyboard.io Model01"]


def test_stessa_voce_stessa_entita_e_verifica_in_breve() -> None:
    """23/10, Arduino: «Adafruit» e «Adafruit Industries» portano alla stessa voce: una chiave sola. La storia tiene
    per ogni controllo l'esito detto dal modello, quello tenuto e la riga citata."""
    from ricerca_catena.web.catena import Anello, Catena, Prova
    a, b = Anello("Adafruit"), Anello("Adafruit Industries")
    voce = Voce("Adafruit Industries", "en", "u")
    a.voci, b.voci = {"en": voce}, {"en": voce}
    assert Catena._chiave_canonica(a) == Catena._chiave_canonica(b) == "voce:adafruitindustries"
    assert Catena._chiave_canonica(Anello("Keyboard.io")) == Catena._chiave_canonica(Anello("Keyboardio"))
    riga = Prova("Adafruit sells programmable keyboard kits.", "u")
    a.esiti = [{"controllo": "produce tastiere programmabili", "peso": 2, "esito": "non so", "prova": None,
                "detto": "sì", "citata": riga}]
    breve = Catena._verifica_in_breve(a)
    assert breve["voce"] == "Adafruit Industries (en)"
    assert breve["esiti"][0] == {"controllo": "produce tastiere programmabili", "peso": 2, "detto": "sì",
                                 "tenuto": "non so", "riga": "Adafruit sells programmable keyboard kits."}


def test_web_meno_caro_per_chi_non_ha_candidati_su_wikipedia(catena) -> None:
    """24/10: un nodo che su Wikipedia non ha dato candidati, con qualità ≥ 0,5, paga 0,1 per il web invece di 0,35
    (Keyboardio, Simone da Orsenigo); sotto 0,5 resta tutto come prima."""
    from ricerca_catena.web.catena import Anello, Prova
    c = catena
    radice = Anello("Arduino")
    nodo = Anello("Keyboardio", da=radice, qualita=0.6)
    nodo.esiti = [{"controllo": "è in relazione «ispirata da» con Arduino", "esito": "sì", "prova": Prova("r", "u"),
                   "peso": 2}]
    wiki = c._priorita(nodo, 3, "wiki")
    assert round(wiki - c._priorita(nodo, 3, "web"), 2) == 0.35
    nodo.vuoto_wiki = True
    assert round(wiki - c._priorita(nodo, 3, "web"), 2) == 0.10
    nodo.qualita = 0.4
    assert round(c._priorita(nodo, 3, "wiki") - c._priorita(nodo, 3, "web"), 2) == 0.35


def test_allargamento_web_uguale_anche_con_relazione_scritta_diversa(catena) -> None:
    """24/10: due piani allargano Arduino sul web con «aziende ispirate da» e «progetti ispirati ad»: è lo stesso
    allargamento (stesse pagine). Su Wikipedia la relazione conta ancora."""
    from ricerca_catena.web.catena import Anello
    a = Anello("Arduino")
    c = catena
    assert c._firma_azione("web", a, 0, "aziende ispirate da") == c._firma_azione("web", a, 0, "progetti ispirati ad")
    assert c._firma_azione("wiki", a, 0, "aziende ispirate da") != c._firma_azione("wiki", a, 0, "fondatori di")
    assert c._firma_azione("web", a, 0, "x") != c._firma_azione("web", a, 1, "x")


def test_risposta_e_entita_dell_ultimo_passo(catena) -> None:
    """24/10, Cappella Sistina: «in quale comune si trova il simulacro» → il valore è il comune trovato dall'ultimo
    passo, con la riga che lo lega, senza chiedere a Haiku un «valore» nelle righe del comune."""
    from ricerca_catena.web.catena import Anello, Prova
    catena.piano = {"passi": [{"relazione": "comune in cui si trova"}],
                    "risposta": {"cosa": "nome del comune", "tipo_valore": "nome", "e_entita": True}}
    legame = Prova("Il simulacro dell'Assunta si trova a Novara di Sicilia.", "https://x.it/a")
    a = Anello("Novara di Sicilia", prova_legame=legame, da=Anello("Madonna Assunta"),
               esiti=[{"controllo": "è in relazione «comune in cui si trova» con Madonna Assunta", "esito": "in parte",
                       "prova": legame, "peso": 2}], qualita=0.6)
    senza = Anello("Capri Leone", prova_legame=legame, da=Anello("Assunzione di Maria"),
                   esiti=[{"controllo": "è in relazione «comune in cui si trova» con X", "esito": "non so",
                           "prova": None, "peso": 2}])
    assert not catena._valore_entita(senza, (1,)) and not catena._valore_entita(senza, (2, 3))
    assert catena._valore_entita(a, (1,))
    assert catena.valore["valore"] == "Novara di Sicilia" and catena.valore["prova"] is legame
    catena.valore = None
    catena.piano["risposta"]["e_entita"] = False
    assert not catena._valore_e_entita(Anello("Senza prove"))


def test_spareggio_finale_premia_lo_scelto(catena, monkeypatch) -> None:
    """24/10, Monte Bianco: due comuni pari all'ultimo passo; Haiku li confronta con la domanda («borgo») e lo
    scelto passa davanti. Chi è lontano dalla migliore non entra; «0» lascia tutto com'era."""
    from ricerca_catena.web.catena import Anello, Prova
    viste = []

    def decidi(punto, dati, predefinito):
        viste.append(dati)
        return ({"scelta": 2, "motivo": "borgo"} if "Laigueglia" in dati["candidati"] else {"scelta": 0}), "claude"

    monkeypatch.setattr(catena.r.pensatore, "decidi", decidi)
    alassio = Anello("Alassio", inizio=Prova("Alassio è un comune di 9 874 abitanti.", "u1"))
    laigueglia = Anello("Laigueglia", inizio=Prova("Laigueglia è un piccolo borgo di mare.", "u2"))
    lontano = Anello("Andora")
    punti = {"Alassio": 0.71, "Laigueglia": 0.70, "Andora": 0.4}
    assert catena._spareggio_finale([alassio, laigueglia, lontano], lambda a: punti[a.nome]) == 1
    assert laigueglia.preferito > 0 and alassio.preferito == 0
    assert "Andora" not in viste[0]["candidati"] and "piccolo borgo di mare" in viste[0]["candidati"]
    assert catena._spareggio_finale([Anello("A"), Anello("B")], lambda a: 0.5) == -1
    assert catena._spareggio_finale([Anello("Solo")], lambda a: 0.5) == -1 and len(viste) == 2


def test_ricerca_della_scheda_dal_controllo_aperto() -> None:
    """24/10, Arduino: la seconda ricerca della scheda web prova il controllo più pesante ancora aperto."""
    from ricerca_catena.web.catena import Anello
    a = Anello("Seeed Studio", esiti=[
        {"controllo": "azienda americana", "peso": 1, "esito": "non so", "prova": None},
        {"controllo": "produce tastiere programmabili", "peso": 3, "esito": "non so", "prova": None},
        {"controllo": "è in relazione «ispirata da» con Arduino", "peso": 3, "esito": "non so", "prova": None}])
    assert Catena._ricerca_controllo(a) == "Seeed Studio produce tastiere programmabili"
    assert Catena._ricerca_controllo(Anello("Mario Rossi")) == "Mario Rossi biografia"


def test_titolo_dalla_riga_senza_confondere_le_parentesi() -> None:
    """24/10, Monte Bianco: «La Thuile (Italia)» prendeva «La Salle (Italia)» (chiave «italia»)."""
    from ricerca_catena.web.catena import Prova, _chiave_nome
    riga = Prova("Comuni confinanti: La Salle, La Thuile", "u", ["La Salle (Italia)", "La Thuile (Italia)"], "it")
    assert Catena._titolo_da_prova("La Thuile (Italia)", riga) == ("it", "La Thuile (Italia)")
    assert Catena._titolo_da_prova("Fermi", Prova("x", "u", ["Enrico Fermi"], "it")) == ("it", "Enrico Fermi")
    assert _chiave_nome("La Thuile (Italia)") == "thuile" and _chiave_nome("Tux (mascot)") == "tux"
    riga = Prova("Paul McCartney and his wife Linda", "u", ["Linda McCartney", "Wings"], "en")
    assert Catena._titolo_da_prova("Paul McCartney", riga) is None
    assert Catena._titolo_da_prova("Microsoft", Prova("x", "u", ["Microsoft ergonomic keyboards"], "en")) is None
    assert Catena._titolo_da_prova("Adafruit", Prova("x", "u", ["Adafruit Industries"], "en")) == ("en", "Adafruit Industries")


def test_legami_scritti_dalla_catena_non_sono_prove(catena) -> None:
    """24/10, Monte Bianco: un anello debole registrato dal primo piano non deve diventare prova per il secondo."""
    from ricerca_catena.web.catena import Anello, Prova
    a = Anello("Vado Ligure", da=Anello("Saint-Gervais-les-Bains"),
               prova_legame=Prova("Vado Ligure è un comune della provincia di Savona.", "https://it.wikipedia.org/wiki/Vado"))
    catena._registra(a, "gemellata con")
    assert catena.r.concetti.legami()  # il legame è nel grafo (per la vista e per chi scrive)
    assert not any("→ gemellata con →" in p.testo for p in catena._prove_entita("Vado Ligure"))
    assert Catena._titolo_da_prova("Napoli", Prova("x", "u", ["Regno di Napoli"], "it")) is None


def test_indizi_del_passo_per_le_ricerche() -> None:
    """25/10, Arduino: le ricerche web ricevono i controlli del passo (prima i più pesanti)."""
    passo = {"controlli": [{"descrizione": "ispirata da Arduino", "letture": [], "peso": 1},
                           {"descrizione": "produce tastiere programmabili", "letture": ["tastiere"], "peso": 3}]}
    assert Catena._indizi(passo) == ["produce tastiere programmabili / tastiere", "ispirata da Arduino"]


def test_nome_intero_solo_per_le_persone() -> None:
    """25/10, Arduino: «Kaia» diventa «Kaia Dekker» (passo di persone); un'azienda di una parola resta com'è."""
    from ricerca_catena.web.catena import Prova
    prove = [Prova("Keyboardio was founded in 2012 by Jesse Vincent and Kaia Dekker.", "u")]
    assert Catena._nome_intero("Kaia", prove) == "Kaia Dekker" and Catena._nome_intero("jesse", prove) == "Jesse Vincent"
    assert Catena._nome_intero("Mario Rossi", prove) == "Mario Rossi"
    assert Catena._di_persone({"tipo": "persona", "relazione": "fondatori di"})
    assert not Catena._di_persone({"tipo": "organizzazione", "relazione": "aziende ispirate da"})


def test_piano_con_salto_di_una_cosa_senza_nome() -> None:
    """25/10, Cappella Sistina: «festa → simulacro → comune» diventa «festa → comune (attraverso il simulacro)»."""
    piano = {"partenza": {"entita": "Cappella Sistina"}, "passi": [
        {"relazione": "festa a cui è dedicata", "tipo": "festa", "controlli": []},
        {"relazione": "simulacro che raffigura", "tipo": "simulacro", "parole": ["simulacro"],
         "controlli": [{"descrizione": "raffigura l'Assunta", "letture": [], "peso": 2}]},
        {"relazione": "comune in cui si trova", "tipo": "comune", "parole": ["comune"],
         "controlli": [{"descrizione": "provincia di Messina", "letture": [], "peso": 2}]}]}
    salto = Catena._piano_salto(piano)
    assert [p["relazione"] for p in salto["passi"]] == ["festa a cui è dedicata",
                                                        "comune in cui si trova (attraverso: simulacro che raffigura)"]
    assert len(salto["passi"][1]["controlli"]) == 2 and "simulacro" in salto["passi"][1]["parole"]
    assert Catena._piano_salto({"passi": [{"relazione": "allievi di", "tipo": "persona"},
                                          {"relazione": "luogo di nascita", "tipo": "città"}]}) is None


def test_spareggio_largo_tra_legati_senza_no(catena, monkeypatch) -> None:
    """25/10, Monte Bianco: Alassio 0,9 e Laigueglia 0,7, tutte e due legate a La Thuile e senza «no»: spareggio."""
    from ricerca_catena.web.catena import Anello, Prova
    viste = []
    monkeypatch.setattr(catena.r.pensatore, "decidi", lambda p, d, f: (viste.append(d) or {"scelta": 2}, "claude"))
    riga = Prova("La Thuile è gemellata con Alassio e Laigueglia.", "u")
    legato = lambda nome, q: Anello(nome, qualita=q, esiti=[  # noqa: E731
        {"controllo": "è in relazione «gemellato con» con La Thuile", "esito": "sì", "prova": riga, "peso": 2}])
    alassio, laigueglia = legato("Alassio", 0.9), legato("Laigueglia", 0.7)
    lontano = legato("Andora", 0.4)
    punti = {"Alassio": 1.0, "Laigueglia": 0.8, "Andora": 0.5}
    assert catena._spareggio_finale([alassio, laigueglia, lontano], lambda a: punti[a.nome]) == 1
    assert "Andora" not in viste[0]["candidati"] and laigueglia.preferito > 0


def test_valore_dall_estratto_di_un_risultato(catena, monkeypatch) -> None:
    """29/10, Congresso di Vienna: la data stava solo nell'estratto di una pagina mai letta (Geni)."""
    from ricerca_catena.web.catena import Anello, Prova
    catena.piano = {"passi": [{"relazione": "allievi"}],
                    "risposta": {"cosa": "data di nascita", "controlli": [], "tipo_valore": "data completa"}}

    def cerca_e_leggi(ricerche, pagine, scopo=""):
        catena._estratti = [Prova("[risultato di ricerca: Niccolò Zucchi] Niccolò Zucchi, gesuita, nato il "
                                  "6 dicembre 1586 a Parma.", "https://esempio.org/zucchi")]
        return []

    monkeypatch.setattr(catena, "_cerca_e_leggi", cerca_e_leggi)
    assert catena._valore_entita(Anello("Niccolò Zucchi", da=Anello("Giuseppe Biancani")), (2, 3))
    assert catena.valore["valore"] == "6 dicembre 1586" and "esempio.org" in catena.valore["prova"].url



def test_stesso_passo_con_controlli_diversi_si_rifa_su_wikipedia() -> None:
    """29/10, Galileo: il piano 2 ha la stessa relazione del piano 1 ma controlli diversi: su Wikipedia si rifà
    (Biancani scartato con i controlli del piano 1, tenuto con quelli del piano 2); sul web no (costa pagine)."""
    gia = {("wiki", "galileo", 0, "allievi"): ("A", []), ("web", "galileo", 0): ("A", [])}
    segni = {("wiki", "galileo", 0, "allievi"): {"A"}}
    assert Catena._gia_fatta(("wiki", "galileo", 0, "allievi"), "wiki", "B", gia, segni) == "rifai"
    assert Catena._gia_fatta(("wiki", "galileo", 0, "allievi"), "wiki", "A", gia, segni) == "riusa"
    assert Catena._gia_fatta(("web", "galileo", 0), "web", "B", gia, segni) == "salta"
    assert Catena._gia_fatta(("wiki", "altro", 0, "allievi"), "wiki", "A", gia, segni) == "nuova"


# --- revisione dell'11/11/2026: i controlli sono collegati alla risposta (docs/solidita-e-validazione.md, T3) ------

def test_valore_cambiato_dal_modello_non_passa(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il modello cita la riga giusta ma dà un altro anno: il programma rifiuta il valore, la risposta non c'è."""
    def bugiardo(punto: str, dati: dict, predefinito):
        if punto == "valore" and "6 dicembre 1586" in dati["prove"]:
            return {"valore": "6 dicembre 1587", "prova": _riga(dati["prove"], "6 dicembre 1586"), "motivo": ""}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", bugiardo)
    catena.percorri()
    assert catena.valore is None and not catena.controlli()["confermata"]
    rifiuti = [s for s in catena.storia if s["relazione"] == "valore non nella riga"]
    assert rifiuti and rifiuti[0]["scartati"][0][0] == "6 dicembre 1587"
    assert catena.r.scrivi().startswith("**Risposta:** Non trovata")


def test_valore_giusto_porta_l_esito_dei_controlli(catena) -> None:
    catena.percorri()
    esito = catena.riepilogo()
    assert esito["controlli"]["fonte_valore"] == "A" and esito["controlli"]["disaccordi"] == []
    assert "6 dicembre 1586" in esito["valore"]["riga"]
    assert any("6 dicembre 1586" in p["riga"] and "Zucchi" in p["url"] for p in esito["prove"]) and len(esito["prove"]) >= 3
    assert esito["controlli"]["confermata"] == (esito["controlli"]["anelli_deboli"] == 0)


def test_la_catena_non_torna_sulla_partenza(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tra i «discepoli» di Biancani il modello propone Galileo, da cui la catena è partita: scartato, con traccia."""
    def circolare(punto: str, dati: dict, predefinito):
        esito, chi = _decidi(punto, dati, predefinito)
        if punto == "candidati" and dati.get("entita") == "Giuseppe Biancani":
            esito["candidati"].append({"nome": "Galileo Galilei", "prova": _riga(dati["prove"], "lettori di Galileo")})
        return esito, chi

    monkeypatch.setitem(PAGINE, "Giuseppe Biancani allievi", [(
        "https://it.wikipedia.org/wiki/Giuseppe_Biancani", "Giuseppe Biancani (Bologna, 8 marzo 1566) è stato un "
        "gesuita. Tra suoi discepoli figurano Giovanni Riccioli, Mario Bettini, Niccolò Zucchi e Niccolò Cabeo, tutti "
        "lettori di Galileo Galilei.")])
    monkeypatch.setattr(catena.r.pensatore, "decidi", circolare)
    catena.percorri()
    tornati = [s for s in catena.storia if s["relazione"] == "già nella strada"]
    assert tornati and tornati[0]["candidati"] == ["Galileo Galilei"]
    assert all(a.nome != "Galileo Galilei" for fascio in catena.anelli for a in fascio)
    assert catena.valore["valore"] == "6 dicembre 1586"


@pytest.mark.parametrize("testo,forte", [
    ("[risultato di ricerca: Aubrey Research] Stonebraker was born in Milton, New Hampshire", False),
    ("Michael Stonebraker was born in Milton, New Hampshire in 1943", True)])
def test_un_estratto_non_fa_un_anello_forte(catena, monkeypatch: pytest.MonkeyPatch, testo: str, forte: bool) -> None:
    from ricerca_catena.web.catena import Anello, Prova, qualita
    prova = Prova(testo, "https://esempio.org/stonebraker")
    controlli = [{"descrizione": "è in relazione «luogo di nascita di» con Michael Stonebraker", "letture": [], "peso": 2}]
    milton = Anello("Milton", da=Anello("Michael Stonebraker"))

    def esiti(candidati, _controlli) -> None:
        for a in candidati:
            a.esiti = [{"controllo": controlli[0]["descrizione"], "esito": "sì", "prova": prova, "peso": 2}]
            a.qualita, a.punti = qualita(a.esiti), 1

    monkeypatch.setattr(catena, "_esiti", esiti)
    tenuti = catena._verifica([milton], controlli)
    assert tenuti == [milton] and milton.debole is (not forte)


def test_il_candidato_del_riquadro_scartato_resta_nel_registro(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    from ricerca_catena.web.catena import Anello, Prova, qualita
    stonebraker = Anello("Michael Stonebraker")
    riquadro = Prova("[Michael Stonebraker (en)] Born: Newburyport, Massachusetts, U.S.",
                     "https://en.wikipedia.org/wiki/Michael_Stonebraker")
    legame = "è in relazione «luogo di nascita di» con Michael Stonebraker"
    controlli = [{"descrizione": legame, "letture": [], "peso": 1},
                 {"descrizione": "comune del New Hampshire", "letture": [], "peso": 2}]
    newburyport = Anello("Newburyport, Massachusetts", da=stonebraker, prova_legame=riquadro)

    def esiti(candidati, _controlli) -> None:
        for a in candidati:
            a.esiti = [{"controllo": legame, "esito": "sì", "prova": riquadro, "peso": 1},
                       {"controllo": "comune del New Hampshire", "esito": "no", "prova": riquadro, "peso": 2}]
            a.qualita, a.punti = qualita(a.esiti), 0

    monkeypatch.setattr(catena, "_esiti", esiti)
    assert newburyport not in [a for a in catena._verifica([newburyport], controlli) if not a.debole]
    assert [(x["da"], x["nome"], x["contro"]) for x in catena.conflitti] == [
        ("Michael Stonebraker", "Newburyport, Massachusetts", ["comune del New Hampshire"])]
    catena._verifica([newburyport], controlli)
    assert len(catena.conflitti) == 1  # una volta sola


# --- controlli con il modello sulla soluzione (seconda lettura, altre fonti, revisione) ----------------------------

def _strada_forte(catena) -> None:
    """Nella catena finta di Galileo un anello resta debole (le pagine finte non provano tutti i controlli): qui si
    provano gli esami, quindi la strada si dà per confermata."""
    a = (catena.valore or {}).get("anello")
    while a is not None:
        a.debole = False
        a = a.da


def _con_revisione(catena, monkeypatch, revisione: dict, seconda: str = "6 dicembre 1586", budget: float = 1.0):
    """La catena di Galileo con un modello finto che fa anche la revisione; `seconda` è il valore che il modello dà
    quando rilegge (dalla seconda chiamata «valore» in poi)."""
    chiamate = {"valore": 0, "revisione": []}

    def modello(punto: str, dati: dict, predefinito, **altri):
        if punto == "revisione":
            chiamate["revisione"].append((dati, altri.get("modello")))
            return revisione, "claude"
        if punto == "valore" and "6 dicembre 1586" in dati["prove"]:
            chiamate["valore"] += 1
            if chiamate["valore"] > 1:
                return {"valore": seconda, "prova": _riga(dati["prove"], seconda) if seconda else 0, "motivo": ""}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = budget
    catena.percorri()
    _strada_forte(catena)
    catena.revisione_finale()
    return chiamate


_REGGE = {"regge": "sì", "motivo": "", "indizi": []}


def test_esami_senza_obiezioni_una_sola_fonte(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    chiamate = _con_revisione(catena, monkeypatch, _REGGE)
    esito = catena.controlli()
    assert esito["esami"]["seconda_lettura"] == {"esito": "concordi"}
    assert esito["esami"]["riscontro"]["esito"] == "sola fonte" and esito["esami"]["revisione"]["esito"] == "fatta"
    assert esito["stato"] == "sostenuta" and esito["confermata"] and "una sola fonte" in " ".join(esito["motivi"])
    dati, modello = chiamate["revisione"][0]
    assert modello == "sonnet" and "Galileo Galilei → [allievi] Giuseppe Biancani → [allievi] Niccolò Zucchi" in dati["strada"]
    assert "6 dicembre 1586" in dati["risposta"] and "[1] " in dati["righe"]
    testo = catena._fiducia_dal_programma("**Risposta:** 6 dicembre 1586.\n\n**Fiducia:** alta, tutto torna.")
    assert "**Fiducia:** media — risposta sostenuta: " in testo and "tutto torna" not in testo
    assert "CONTROLLI DEL PROGRAMMA SULLA RISPOSTA" in catena.materiale()
    catena.revisione_finale()
    assert len(chiamate["revisione"]) == 2  # due revisioni, una volta sola


def test_seconda_lettura_diversa_risposta_contestata(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PAGINE, "Niccolò Zucchi", [(
        "https://it.wikipedia.org/wiki/Niccol%C3%B2_Zucchi", "Niccolò Zucchi (Parma, 6 dicembre 1586 – Roma, 1670) "
        "è stato un gesuita, rettore del Collegio Romano. Altre fonti danno per Zucchi la nascita al 7 dicembre 1586.")])
    _con_revisione(catena, monkeypatch, _REGGE, seconda="7 dicembre 1586")
    esito = catena.controlli()
    assert esito["esami"]["seconda_lettura"]["esito"] == "discordi" and esito["esami"]["seconda_lettura"]["valore"] == "7 dicembre 1586"
    assert esito["stato"] == "contestata" and not esito["confermata"]
    assert "altro valore: 7 dicembre 1586" in catena.materiale()
    assert "**Fiducia:** bassa — risposta contestata" in catena._fiducia_dal_programma("**Fiducia:** alta")


def test_seconda_lettura_con_un_valore_che_non_e_nella_riga_non_conta(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    def bugiardo(revisione):
        return revisione
    _con_revisione(catena, monkeypatch, bugiardo(_REGGE), seconda="")
    assert catena.controlli()["esami"]["seconda_lettura"] == {"esito": "non ritrovato"}
    assert catena.controlli()["stato"] == "sostenuta"


@pytest.mark.parametrize("citazione,stato", [("rettore del Collegio Romano", "incerta"), ("parole inventate", "sostenuta")])
def test_obiezione_della_revisione(catena, monkeypatch: pytest.MonkeyPatch, citazione: str, stato: str) -> None:
    """Un «no» della revisione conta solo se cita parole che stanno davvero in una delle righe date."""
    def revisione(dati: dict) -> dict:
        return {"regge": "no", "motivo": "m", "indizi": [{"indizio": "laico", "esito": "no", "citazione": citazione,
                                                          "riga": _riga(dati["righe"], "Collegio Romano")}]}

    chiamate = {"n": 0}

    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            chiamate["n"] += 1
            return revisione(dati), "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    _strada_forte(catena)
    catena.revisione_finale()
    esito = catena.controlli()
    assert chiamate["n"] == 2 and esito["stato"] == stato and esito["confermata"] is (stato == "sostenuta")
    assert len(esito["esami"]["revisione"]["obiezioni"]) == (1 if stato == "incerta" else 0)


def test_senza_budget_gli_esami_non_partono(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    chiamate = _con_revisione(catena, monkeypatch, _REGGE, budget=0.01)
    assert chiamate["revisione"] == [] and catena.esami["motivo"] == "budget di Claude quasi finito"
    assert catena.controlli()["stato"] == "sostenuta" and catena._fiducia_dal_programma("**Fiducia:** alta").startswith("**Fiducia:** media")


def test_senza_valore_niente_esami(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catena, "_cerca_e_leggi", lambda ricerche, pagine, scopo="": [])
    catena.percorri()
    catena.revisione_finale()
    assert catena.esami is None and catena.controlli()["stato"] == "non trovata"


def test_seconda_lettura_che_cambia_il_valore_della_riga_non_e_un_disaccordo(catena, monkeypatch) -> None:
    """Rileggendo, il modello cita la riga giusta ma scrive un altro giorno: non è un'altra fonte, è un errore di
    lettura. Il programma lo scarta (valore non nella riga): «non ritrovato», non «discordi»."""
    chiamate = {"valore": 0}

    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            return _REGGE, "claude"
        if punto == "valore" and "6 dicembre 1586" in dati["prove"]:
            chiamate["valore"] += 1
            if chiamate["valore"] > 1:
                return {"valore": "9 dicembre 1586", "prova": _riga(dati["prove"], "6 dicembre 1586"), "motivo": ""}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    _strada_forte(catena)
    catena.revisione_finale()
    assert catena.esami["seconda_lettura"] == {"esito": "non ritrovato"} and catena.controlli()["stato"] == "sostenuta"


def test_la_revisione_vede_le_altre_voci_di_ogni_anello(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    """12/11, domande di controllo (Python): il luogo di nascita preso da una voce, un'altra lingua ne dava un altro e
    la risposta era «verificata». La revisione riceve anche le righe delle altre voci dell'anello di prima: un «no» con
    quelle parole rende la risposta incerta."""
    from types import SimpleNamespace

    from ricerca_catena.web.catena import Prova
    altra = Prova("[Giuseppe Biancani (en)] Notable students: Giovanni Battista Riccioli", "https://en.wikipedia.org/wiki/Biancani")
    viste: list[dict] = []

    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            viste.append(dati)
            return {"regge": "no", "motivo": "m", "indizi": [{
                "indizio": "allievo di Biancani", "esito": "no", "riga": _riga(dati["righe"], "Notable students"),
                "citazione": "Notable students: Giovanni Battista Riccioli"}]}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    _strada_forte(catena)
    chieste: list[tuple[str, list[str]]] = []

    def voci(a, altre=2):
        return [SimpleNamespace(url="https://en.wikipedia.org/wiki/Biancani", titolo="Giuseppe Biancani", lingua="en",
                                lingue={})] if a.nome == "Giuseppe Biancani" else []

    def righe_wiki(voci, parole, **_k):
        chieste.append((voci[0].titolo, parole))
        return [altra]

    monkeypatch.setattr(catena, "_voci", voci)
    monkeypatch.setattr(catena, "_righe_wiki", righe_wiki)
    catena.revisione_finale()
    assert "Notable students: Giovanni Battista Riccioli" in viste[0]["righe"]
    assert ("Giuseppe Biancani", ["allievi", "Niccolò Zucchi"]) in chieste  # la relazione e il nome dell'anello scelto
    esito = catena.controlli()
    assert esito["stato"] == "incerta" and esito["esami"]["revisione"]["obiezioni"][0]["url"].endswith("/Biancani")


# --- revisione dell'11/11/2026, difetti B5 e B6: gli esiti vanno al candidato e al controllo giusti ----------------

def _catena_nuda():
    c = Catena.__new__(Catena)
    c.o = {}
    return c


def test_due_candidati_con_lo_stesso_cognome_non_si_scambiano_gli_esiti() -> None:
    """Il modello riscrive i nomi («G. Verdi», «Verdi Carlo»): con due candidati con lo stesso cognome l'esito di uno
    non deve finire all'altro. Senza un nome uguale e con il cognome ambiguo, nessun esito («non so»)."""
    from ricerca_catena.web.catena import Anello, Prova
    c = _catena_nuda()
    giuseppe, carlo = Anello("Giuseppe Verdi"), Anello("Carlo Verdi")
    prove = [Prova("Giuseppe Verdi fu un compositore", "u1"), Prova("Carlo Verdi fu un oste", "u2")]
    controlli = [{"descrizione": "compositore", "letture": [], "peso": 2}]
    esito = {"candidati": [
        {"nome": "Verdi Carlo", "controlli": [{"controllo": "C1", "esito": "no", "prova": 2, "citazione": "fu un oste"}]},
        {"nome": "G. Verdi", "controlli": [{"controllo": "C1", "esito": "sì", "prova": 1, "citazione": "fu un compositore"}]}]}
    c._applica_esiti([giuseppe, carlo], prove, {id(giuseppe): range(1, 2), id(carlo): range(2, 3)}, esito, controlli)
    assert [e["esito"] for e in giuseppe.esiti] == ["non so"] and [e["esito"] for e in carlo.esiti] == ["non so"]
    # con un solo candidato con quel cognome l'abbinamento per cognome resta (16/10: nomi riscritti dal modello)
    solo = Anello("Giuseppe Verdi")
    c._applica_esiti([solo], prove, {id(solo): range(1, 2)}, {"candidati": [esito["candidati"][1]]}, controlli)
    assert [e["esito"] for e in solo.esiti] == ["sì"]


def test_due_controlli_che_cominciano_uguale_restano_due() -> None:
    from ricerca_catena.web.catena import Anello, Prova
    c = _catena_nuda()
    inizio = "è stato rettore di un collegio della Compagnia di Gesù nella città di "
    controlli = [{"descrizione": inizio + "Roma", "letture": [], "peso": 1},
                 {"descrizione": inizio + "Parma", "letture": [], "peso": 1}]
    a = Anello("Niccolò Zucchi")
    prove = [Prova("Niccolò Zucchi fu rettore del Collegio Romano a Roma", "u1")]
    esito = {"candidati": [{"nome": "Niccolò Zucchi", "controlli": [
        {"controllo": "C1", "esito": "sì", "prova": 1, "citazione": "rettore del Collegio Romano"},
        {"controllo": "C2", "esito": "no", "prova": 1, "citazione": "a Roma"}]}]}
    c._applica_esiti([a], prove, {id(a): range(1, 2)}, esito, controlli)
    assert [(e["controllo"][-5:].strip(), e["esito"]) for e in a.esiti] == [("Roma", "sì"), ("Parma", "no")]
    uniti = Catena._unisci_esiti(a.esiti, a.esiti)
    assert [e["esito"] for e in uniti] == ["sì", "no"]


@pytest.mark.parametrize("riga,valore,esito,stato", [
    ("[Niccolò Zucchi (en)] Niccolò Zucchi (December 6, 1586 – May 21, 1670) was an Italian Jesuit", "6 dicembre 1586", "concordi", "verificata"),
    ("[Niccolò Zucchi (en)] Niccolò Zucchi (December 16, 1586 – May 21, 1670) was an Italian Jesuit", "16 dicembre 1586", "discordi", "contestata"),
    ("[Niccolò Zucchi (en)] Niccolò Zucchi was an Italian Jesuit astronomer", "", "sola fonte", "sostenuta"),
    # il modello dà una data che nella riga non c'è: per il programma non è un'altra fonte
    ("[Niccolò Zucchi (en)] Niccolò Zucchi was an Italian Jesuit astronomer", "16 dicembre 1586", "sola fonte", "sostenuta"),
    # una riga che parla di un altro (né la sua voce né il suo nome) non conta
    ("[Giovanni Riccioli (en)] born December 16, 1586", "16 dicembre 1586", "sola fonte", "sostenuta"),
])
def test_riscontro_in_un_altra_voce(catena, monkeypatch: pytest.MonkeyPatch, riga: str, valore: str, esito: str, stato: str) -> None:
    """T7: lo stesso dato cercato in un'altra voce dell'entità finale: uguale, diverso o assente."""
    from types import SimpleNamespace

    from ricerca_catena.web.catena import Prova
    altra = Prova(riga, "https://en.wikipedia.org/wiki/Altra_voce")

    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            return {"regge": "sì", "motivo": "", "indizi": []}, "claude"
        if punto == "valore" and "[Voce (it)]" not in dati["prove"] and "Altra" not in dati["prove"] and riga in dati["prove"]:
            return {"valore": valore, "prova": _riga(dati["prove"], riga[riga.index("]") + 2:][:30]) if valore else 0, "motivo": ""}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    _strada_forte(catena)
    finale = catena.valore["anello"]
    monkeypatch.setattr(catena, "_voci", lambda a, altre=2: [SimpleNamespace(
        url=altra.url, titolo="Niccolò Zucchi", lingua="en", lingue={})] if a is finale else [])
    monkeypatch.setattr(catena, "_righe_wiki", lambda voci, parole, **_k: [altra])
    monkeypatch.setattr(catena, "_prove_entita", lambda *_a, **_k: [])
    catena.revisione_finale()
    riscontro = catena.esami["riscontro"]
    assert riscontro["esito"] == esito and catena.controlli()["stato"] == stato
    if esito == "discordi":
        assert riscontro["valore"] == "16 dicembre 1586" and "altro valore: 16 dicembre 1586" in catena.materiale()


@pytest.mark.parametrize("citazione,smentita", [("rettore del Collegio Romano", True), ("parole inventate", False)])
def test_premessa_smentita_niente_valore(catena, monkeypatch: pytest.MonkeyPatch, citazione: str, smentita: bool) -> None:
    """13/11: se la revisione mostra, con le parole di una riga, che la domanda dà per vero qualcosa che le fonti negano,
    la catena non dà il valore: «non trovata», con il motivo. Senza la riga la premessa resta in piedi."""
    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            return {"regge": "no", "motivo": "m", "indizi": [], "premessa": {
                "esito": "falsa", "cosa": "Zucchi era un laico", "citazione": citazione,
                "riga": _riga(dati["righe"], "Collegio Romano")}}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    _strada_forte(catena)
    catena.revisione_finale()
    esito = catena.controlli()
    if not smentita:
        assert catena.valore["valore"] == "6 dicembre 1586" and esito["stato"] == "sostenuta" and catena.scartato is None
        return
    assert catena.valore is None and esito["stato"] == "non trovata" and not esito["confermata"]
    assert catena.scartato["valore"] == "6 dicembre 1586" and "premessa smentita" in catena.motivo
    assert "Zucchi era un laico" in catena.motivo and "rettore del Collegio Romano" in catena.motivo
    assert esito["premessa"]["url"].endswith("Zucchi") and catena.riepilogo()["valore"] is None
    assert catena.riepilogo()["valore_scartato"] == "6 dicembre 1586"
    assert catena.r.scrivi().startswith("**Risposta:** Non trovata: premessa smentita")


@pytest.mark.parametrize("premessa,deboli,dato", [("non provata", True, False), ("regge", True, True), ("non provata", False, True)])
def test_strada_respinta_niente_valore(catena, monkeypatch: pytest.MonkeyPatch, premessa: str, deboli: bool, dato: bool) -> None:
    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            return {"regge": "no", "motivo": "m", "premessa": {"esito": premessa, "cosa": "", "riga": 0, "citazione": ""},
                    "indizi": [{"indizio": "laico", "esito": "no", "citazione": "rettore del Collegio Romano",
                                "riga": _riga(dati["righe"], "Collegio Romano")}]}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    if deboli:
        assert catena._anelli_deboli() >= 1   # nella catena finta di Galileo un anello resta debole
    else:
        _strada_forte(catena)
    catena.revisione_finale()
    if dato:
        assert catena.valore is not None and catena.controlli()["stato"] == "incerta" and catena.scartato is None
    else:
        assert catena.valore is None and catena.controlli()["stato"] == "non trovata"
        assert catena.scartato["valore"] == "6 dicembre 1586" and catena.motivo.startswith("catena respinta dalla revisione")
        assert "laico" in catena.motivo and "laico" in catena.controlli()["motivi"][0]


# --- 15/11: due revisioni, vale la più severa; il nome con cui la domanda chiama la partenza ---------------------

def test_di_due_revisioni_discordi_vale_la_piu_severa(catena, monkeypatch: pytest.MonkeyPatch) -> None:
    risposte = iter([{"regge": "sì", "motivo": "", "indizi": []}, {"regge": "non provato", "motivo": "dubbio", "indizi": []}])
    serratura = __import__("threading").Lock()

    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            with serratura:
                return next(risposte), "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.percorri()
    assert catena._anelli_deboli() >= 1
    catena.revisione_finale()
    revisione = catena.esami["revisione"]
    assert (revisione["regge"], revisione["letture"]) == ("non provato", 2)
    assert catena.controlli()["stato"] == "incerta"       # anello debole: serve che reggano tutte e due


@pytest.mark.parametrize("nome,trovata", [("Zorvex", False), ("Galileo", True), ("", True), ("Zucchi", True)])
def test_la_partenza_nominata_dalla_domanda_deve_stare_nelle_pagine(catena, monkeypatch, nome: str, trovata: bool) -> None:
    def modello(punto: str, dati: dict, predefinito, **_altri):
        if punto == "revisione":
            return {"regge": "sì", "motivo": "", "indizi": [], "nome_di_partenza": nome}, "claude"
        return _decidi(punto, dati, predefinito)

    monkeypatch.setattr(catena.r.pensatore, "decidi", modello)
    catena.r.pensatore.bilancio.budget_usd = 1.0
    catena.r.argomento = "Chi fu allievo dell'allievo di Galileo secondo il trattato Zorvex?"   # «Zucchi» non è nella domanda
    catena.percorri()
    _strada_forte(catena)
    catena.revisione_finale()
    if trovata:
        assert catena.valore is not None and catena.scartato is None
    else:
        assert catena.valore is None and catena.scartato["valore"] == "6 dicembre 1586"
        assert "«Zorvex», nominato dalla domanda, non compare" in catena.motivo
        assert catena.controlli()["stato"] == "non trovata" and "Zorvex" in catena.controlli()["motivi"][0]
