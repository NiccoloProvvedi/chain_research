"""Raccolta senza browser né rete: siti esclusi, archivio delle wiki, messaggi dei limiti."""
import pytest

from ricerca_catena.web.raccolta import Raccolta


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=x", "https://youtu.be/x", "https://x.com/a", "https://twitter.com/a",
    "https://www.amazon.it/dp/1", "https://www.ebay.de/i/1", "https://www.google.com/search?q=a",
    "https://it.linkedin.com/in/a", "https://www.bing.com/a", "https://html.duckduckgo.com/a",
    "https://old.reddit.com/r/a", "https://www.facebook.com/a"])
def test_siti_esclusi(url: str) -> None:
    assert Raccolta("domanda")._escluso(url)


@pytest.mark.parametrize("url", [
    "https://www.linux.com/a", "https://www.unix.com/a", "https://www.netflix.com/a", "https://www.dropbox.com/a",
    "https://www.fedex.com/a", "https://arduino.cc/a", "https://www.notgoogle.org/a", "https://binging.com/a"])
def test_siti_non_esclusi_per_una_parte_del_nome(url: str) -> None:
    assert not Raccolta("domanda")._escluso(url)


class PaginaFinta:
    def __init__(self) -> None:
        self.contenuti: list[str] = []

    @property
    def request(self):
        raise AssertionError("l'archivio non deve passare da pagina.request: sfugge al filtro delle reti")

    def goto(self, *_a, **_k) -> None:
        pass

    def set_content(self, html: str, **_k) -> None:
        self.contenuti.append(html)

    def evaluate(self, _script: str) -> dict:
        return {"titolo": "t", "testo": "x" * 700}


class ClientFinto:
    def __init__(self) -> None:
        self.chiamate: list[tuple[str, dict]] = []

    def get_json(self, url: str, params: dict | None = None) -> dict:
        self.chiamate.append((url, params or {}))
        return {"parse": {"title": "A</title><script>x</script>", "text": "<p>" + "t" * 600 + "</p>"}}


def test_archivio_wiki_con_il_client_sicuro_e_titolo_codificato() -> None:
    r = Raccolta("domanda")
    client, pagina = ClientFinto(), PaginaFinta()
    r._client_archivio = lambda _host: client
    r.browser = type("B", (), {"pagina": pagina})()
    assert r._da_archivio_wiki('https://wiki.esempio.org/wiki/AT&T_"x"')["testo"]
    url, params = client.chiamate[0]
    assert url == "https://wiki.esempio.org/w/api.php" and params["page"] == 'AT&T_"x"' and params["action"] == "parse"
    assert "<title>A</title><script>" not in pagina.contenuti[0] and '"x""' not in pagina.contenuti[0]


def test_limite_di_minuti_arrotondato() -> None:
    r = Raccolta("domanda", opzioni={"minuti_catena": 5.003691091149818})
    r._attesa_iniziale, r._inizio = 0.0, -1e9
    assert r._limite_raggiunto() == "raggiunto il limite di sicurezza di 5 minuti"


# --- revisione dell'11/11/2026: l'esito della domanda porta lo stato deciso dai controlli --------------------------

@pytest.mark.parametrize("opzioni,attesi", [({}, 1), ({"controlli_modello": False}, 0)])
def test_esegui_fa_gli_esami_e_riporta_lo_stato(tmp_path, monkeypatch, opzioni: dict, attesi: int) -> None:
    from ricerca_catena.web import catena as modulo_catena
    from ricerca_catena.web import raccolta

    class CatenaFinta:
        esami_fatti = 0

        def __init__(self, r) -> None:
            self.valore, self.motivo = {"valore": "56 m"}, "catena completa"

        def percorri(self) -> None:
            pass

        def revisione_finale(self) -> None:
            CatenaFinta.esami_fatti += 1

        def controlli(self) -> dict:
            return {"confermata": False, "stato": "contestata"}

        def scrivi(self, _compito: str) -> str:
            return "**Risposta:** 56 m."

        def riepilogo(self) -> dict:
            return {}

    class BrowserFinto:
        def __init__(self, *_a, **_k) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(modulo_catena, "Catena", CatenaFinta)
    monkeypatch.setattr(raccolta, "Browser", BrowserFinto)
    r = raccolta.Raccolta("domanda", opzioni=opzioni, cartella_pezzi=tmp_path / "pezzi", profilo=tmp_path / "profilo")
    esito = r.esegui()
    assert CatenaFinta.esami_fatti == attesi
    assert (esito["stato"], esito["confermata"], esito["valore"], esito["trovata"]) == ("contestata", False, "56 m", True)
