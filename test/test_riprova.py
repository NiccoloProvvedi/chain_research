"""Seconda ricerca indipendente per le risposte incerte (docs/solidita-e-validazione.md, 16/11): senza rete né Claude."""
import pytest

from ricerca_catena.web import riprova


def _esito(valore, stato, costo=0.10, secondi=300, motivi=None) -> dict:
    fiducia = {"verificata": "alta", "sostenuta": "media"}.get(stato, "bassa")
    return {"domanda": "d", "valore": valore, "trovata": bool(valore), "stato": stato, "confermata": stato in ("verificata", "sostenuta"),
            "risposta": f"**Risposta:** {valore} [1].\n\n**Fiducia:** {fiducia} — risposta {stato}: x." if valore else "**Risposta:** Non trovata.",
            "costo_usd": costo, "secondi": secondi, "fonti": [{"numero": 1, "url": f"https://e.org/{valore}"}],
            "pagine_lette": 4, "ricerche": ["q"],
            "catena": {"controlli": {"stato": stato, "motivi": list(motivi or ["1 anelli della catena non confermati"]), "confermata": False}}}


def test_stesso_valore_due_volte_la_risposta_e_sostenuta() -> None:
    unito = riprova.unisci(_esito("56 m s.l.m.", "incerta"), _esito("56 metri", "incerta", costo=0.2, secondi=100))
    assert (unito["stato"], unito["confermata"], unito["valore"]) == ("sostenuta", True, "56 m s.l.m.")
    assert unito["riprova"] == {"esito": "stesso valore", "valore": "56 metri", "stato": "incerta", "costo_usd": 0.2, "secondi": 100}
    assert unito["costo_usd"] == pytest.approx(0.3) and unito["secondi"] == 400
    motivi = unito["catena"]["controlli"]["motivi"]
    assert motivi[0].startswith("due ricerche indipendenti danno lo stesso valore") and "1 anelli" in motivi[1]
    assert unito["catena"]["controlli"]["stato"] == "sostenuta" and unito["catena"]["controlli"]["confermata"]
    assert "**Fiducia:** media — risposta sostenuta: due ricerche indipendenti" in unito["risposta"]
    assert "risposta incerta" not in unito["risposta"]


def test_la_seconda_ricerca_trova_di_meglio() -> None:
    """Stesso valore e la seconda catena è tutta confermata: vale la sua risposta, con le sue fonti e il suo stato."""
    unito = riprova.unisci(_esito("56 m", "incerta"), _esito("56 m s.l.m.", "verificata", motivi=[]))
    assert (unito["stato"], unito["valore"], unito["fonti"][0]["url"]) == ("verificata", "56 m s.l.m.", "https://e.org/56 m s.l.m.")
    assert unito["riprova"]["esito"] == "stesso valore" and unito["riprova"]["stato"] == "incerta"   # l'altra è la prima


@pytest.mark.parametrize("stato_seconda,principale", [("incerta", "120 m"), ("sostenuta", "138 m")])
def test_altro_valore_la_risposta_e_contestata(stato_seconda: str, principale: str) -> None:
    unito = riprova.unisci(_esito("120 m", "incerta"), _esito("138 m", stato_seconda))
    assert (unito["stato"], unito["confermata"], unito["valore"]) == ("contestata", False, principale)
    altro = "138 m" if principale == "120 m" else "120 m"
    assert unito["riprova"]["esito"] == "altro valore" and unito["riprova"]["valore"] == altro
    assert f"un'altra ricerca indipendente dà {altro}" in unito["catena"]["controlli"]["motivi"][0]
    assert "**Fiducia:** bassa — risposta contestata" in unito["risposta"]


def test_la_seconda_ricerca_non_trova_niente() -> None:
    unito = riprova.unisci(_esito("56 m", "incerta"), _esito(None, "non trovata"))
    assert (unito["stato"], unito["valore"], unito["riprova"]["esito"]) == ("incerta", "56 m", "nessun valore")
    assert "una seconda ricerca non ha trovato un valore" in unito["catena"]["controlli"]["motivi"][-1]


class _Finta:
    create: list[dict] = []
    esiti: list[dict] = []

    def __init__(self, opzioni: dict) -> None:
        _Finta.create.append(opzioni)

    def esegui(self) -> dict:
        return _Finta.esiti.pop(0)


@pytest.fixture
def finte():
    _Finta.create, _Finta.esiti = [], []
    return _Finta


def test_solo_le_incerte_si_cercano_due_volte(finte) -> None:
    for stato in ("verificata", "sostenuta", "contestata", "non trovata"):
        finte.esiti = [_esito("56 m" if stato != "non trovata" else None, stato)]
        esito = riprova.con_riprova(finte, {"budget_claude": 0.5, "minuti_catena": 35}, lambda _e: None)
        assert esito["stato"] == stato and "riprova" not in esito
    assert len(finte.create) == 4


def test_la_seconda_ricerca_ha_il_budget_e_il_tempo_rimasti(finte) -> None:
    finte.esiti = [_esito("56 m", "incerta", costo=0.12, secondi=600), _esito("56 m", "incerta", costo=0.2)]
    eventi: list[dict] = []
    esito = riprova.con_riprova(finte, {"budget_claude": 0.5, "minuti_catena": 35, "x": 1}, eventi.append)
    assert esito["stato"] == "sostenuta" and len(finte.create) == 2
    seconda = finte.create[1]
    assert seconda["budget_claude"] == pytest.approx(0.38) and seconda["minuti_catena"] == pytest.approx(25) and seconda["x"] == 1
    assert any("seconda" in e.get("testo", "") for e in eventi)


@pytest.mark.parametrize("opzioni,costo,secondi", [
    ({"budget_claude": 0.5, "minuti_catena": 35}, 0.40, 300),                        # resta troppo poco budget
    ({"budget_claude": 0.5, "minuti_catena": 35}, 0.10, 31 * 60),                    # resta troppo poco tempo
    ({"budget_claude": 0.5, "minuti_catena": 35, "seconda_ricerca": False}, 0.10, 300),
])
def test_senza_budget_tempo_o_permesso_una_ricerca_sola(finte, opzioni: dict, costo: float, secondi: int) -> None:
    finte.esiti = [_esito("56 m", "incerta", costo=costo, secondi=secondi)]
    esito = riprova.con_riprova(finte, opzioni, lambda _e: None)
    assert esito["stato"] == "incerta" and len(finte.create) == 1 and "riprova" not in esito


def test_il_processo_della_domanda_cerca_due_volte_e_somma_i_costi(monkeypatch, capsys) -> None:
    import io
    import json
    from types import SimpleNamespace

    from ricerca_catena import domanda
    esiti = [_esito("120 m", "incerta", costo=0.1), _esito("138 m", "sostenuta", costo=0.2)]
    create: list[dict] = []

    class RaccoltaFinta:
        def __init__(self, _domanda: str, *, opzioni: dict, **_altro) -> None:
            create.append(opzioni)
            self.pensatore = SimpleNamespace(bilancio=SimpleNamespace(speso=0.1 * len(create)))

        def esegui(self) -> dict:
            return esiti.pop(0)

    monkeypatch.setattr(domanda, "Raccolta", RaccoltaFinta)
    monkeypatch.setattr(domanda.signal, "signal", lambda *_a: None)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"domanda": "d", "opzioni": {"budget_claude": 0.5}}) + "\n"))
    assert domanda.main() == 0
    righe = [json.loads(r) for r in capsys.readouterr().out.splitlines()]
    esito = righe[-1]["esito"]
    assert len(create) == 2 and create[1]["budget_claude"] == pytest.approx(0.4)
    assert (esito["stato"], esito["valore"], esito["riprova"]["valore"]) == ("contestata", "138 m", "120 m")
    assert esito["costo_usd"] == pytest.approx(0.3) and any("seconda volta" in r.get("evento", {}).get("testo", "") for r in righe)
