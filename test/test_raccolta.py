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
