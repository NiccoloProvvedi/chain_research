"""Test dell'infrastruttura comune delle fonti (senza rete: trasporti finti, orologio finto, processi brevi)."""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from ricerca_catena.web import rete
from ricerca_catena.web.fonti import base, config, http
from ricerca_catena.web.fonti.limiti import Limitatore

PROGETTO = Path(__file__).resolve().parents[1]


# --- rete -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fe80::1",
                                "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_ip_vietati(ip: str) -> None:
    assert rete.ip_vietato(ip)


def test_ip_pubblici_e_nomi_locali() -> None:
    assert not rete.ip_vietato("93.184.216.34") and not rete.ip_vietato("2606:4700::1111")
    assert rete.nome_locale("stampante.local") and rete.nome_locale("localhost") and not rete.nome_locale("esempio.org")


# --- client HTTP ----------------------------------------------------------------------------------------------------

def _client(gestore, domini=("api.esempio.org",), **opzioni) -> http.ClientFonte:
    attese: list[float] = []
    client = http.ClientFonte("prova", user_agent="test", domini=domini, transport=httpx.MockTransport(gestore),
                              dormi=attese.append, **opzioni)
    client.attese = attese  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _nomi_pubblici(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nei test i nomi di esempio risultano pubblici senza interrogare il DNS."""
    vero = rete.host_vietato.__wrapped__

    def finto(host: str) -> bool:
        if host.endswith(("esempio.org", "altro.org")):
            return False
        return vero(host)
    monkeypatch.setattr(rete, "host_vietato", finto)


def test_json_e_user_agent() -> None:
    visti = []

    def gestore(r: httpx.Request) -> httpx.Response:
        visti.append(r)
        return httpx.Response(200, json={"ok": True})
    assert _client(gestore).get_json("https://api.esempio.org/x", params={"q": "a b"}) == {"ok": True}
    assert visti[0].headers["user-agent"] == "test" and visti[0].url.params["q"] == "a b"


def test_dominio_non_consentito() -> None:
    with pytest.raises(base.ContenutoNonLeggibile, match="dominio"):
        _client(lambda r: httpx.Response(200)).get_json("https://altro.org/x")


def test_redirect_verso_rete_locale_bloccato() -> None:
    def gestore(r: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:1234/v1/models"})
    with pytest.raises(base.ContenutoNonLeggibile, match="rete locale"):
        _client(gestore, domini=None).get_testo("https://api.esempio.org/x")


def test_redirect_pubblico_seguito_e_controllato() -> None:
    def gestore(r: httpx.Request) -> httpx.Response:
        if r.url.host == "api.esempio.org":
            return httpx.Response(301, headers={"location": "https://cdn.esempio.org/file"})
        return httpx.Response(200, text="contenuto")
    assert _client(gestore, domini=None).get_testo("https://api.esempio.org/x") == "contenuto"
    # con la lista dei domini, il redirect verso un dominio non consentito è rifiutato
    with pytest.raises(base.ContenutoNonLeggibile, match="dominio"):
        _client(lambda r: httpx.Response(302, headers={"location": "https://altro.org/"})).get_testo(
            "https://api.esempio.org/x")


def test_ritentativi_su_5xx_con_attesa_crescente() -> None:
    risposte = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[1])])
    client = _client(lambda r: next(risposte))
    assert client.get_json("https://api.esempio.org/x") == [1]
    assert len(client.attese) == 2 and 2 <= client.attese[0] < 3 and 4 <= client.attese[1] < 5


def test_retry_after_rispettato() -> None:
    risposte = iter([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json={})])
    client = _client(lambda r: next(risposte))
    client.get_json("https://api.esempio.org/x")
    assert client.attese == [7.0]


def test_429_persistente_diventa_limite_raggiunto() -> None:
    with pytest.raises(base.LimiteRaggiunto):
        _client(lambda r: httpx.Response(429)).get_json("https://api.esempio.org/x")


@pytest.mark.parametrize("codice,errore", [(401, base.FonteNonDisponibile), (403, base.FonteNonDisponibile),
                                           (404, base.ContenutoNonLeggibile)])
def test_nessun_ritentativo_su_401_403_404(codice: int, errore: type) -> None:
    chiamate = []

    def gestore(r: httpx.Request) -> httpx.Response:
        chiamate.append(r)
        return httpx.Response(codice)
    with pytest.raises(errore):
        _client(gestore).get_json("https://api.esempio.org/x")
    assert len(chiamate) == 1


def test_risposta_troppo_grande() -> None:
    with pytest.raises(base.ContenutoNonLeggibile, match="troppo grande"):
        _client(lambda r: httpx.Response(200, content=b"x" * 3_000_000)).get_testo("https://api.esempio.org/x",
                                                                                 max_mb=1)


class _Gestore(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"locale": true}')

    def log_message(self, *args) -> None:
        pass


def test_dns_rebinding_bloccato_alla_connessione(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il nome supera il controllo iniziale (sembra pubblico), ma al momento della connessione porta a 127.0.0.1:
    il backend sicuro rifiuta il collegamento. Con consenti_locali la stessa richiesta arriva al server locale."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gestore)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    porta = server.server_address[1]
    try:
        monkeypatch.setattr(http, "risolvi", lambda host, port: ["127.0.0.1"])
        client = http.ClientFonte("prova", user_agent="test", domini=None)
        with pytest.raises(base.ContenutoNonLeggibile, match="non consentito"):
            client.get_json(f"http://servizio.esempio.org:{porta}/")
        aperto = http.ClientFonte("prova", user_agent="test", domini=None, consenti_locali=True)
        assert aperto.get_json(f"http://servizio.esempio.org:{porta}/") == {"locale": True}
    finally:
        server.shutdown()


# --- limitatore -----------------------------------------------------------------------------------------------------

class _Orologio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def dormi(self, s: float) -> None:
        self.t += s


def test_limitatore_intervallo_e_tetto_orario() -> None:
    o = _Orologio()
    lim = Limitatore(5, max_ora=3, nome="youtube", orologio=o, dormi=o.dormi)
    lim.attendi()
    lim.attendi()
    assert o.t == 1005.0
    lim.attendi()
    with pytest.raises(base.LimiteRaggiunto, match="all'ora"):
        lim.attendi()
    o.t += 3600
    lim.attendi()  # passata un'ora, si riparte


def test_limitatore_pausa() -> None:
    o = _Orologio()
    lim = Limitatore(1, orologio=o, dormi=o.dormi)
    lim.pausa(3600)
    with pytest.raises(base.LimiteRaggiunto, match="pausa"):
        lim.attendi()
    lim.pausa(0)
    o.t += 3601
    lim.attendi()
    assert lim.in_pausa() == 0


# --- configurazione e segreti ---------------------------------------------------------------------------------------

def test_segreti_solo_con_permessi_600(cartella_temporanea: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    segreti = cartella_temporanea / "segreti.json"
    monkeypatch.setenv("RICERCA_SEGRETI", str(segreti))
    assert config.segreti() == {}
    segreti.write_text('{"stackexchange": {"chiave": "abc"}}')
    segreti.chmod(0o644)
    assert config.segreti() == {}
    segreti.chmod(0o600)
    assert config.segreti()["stackexchange"]["chiave"] == "abc"


def test_segreti_e_dati_esclusi_da_git() -> None:
    for percorso in ("config/segreti.json", "config/impostazioni.json", "indagini/x/documento.pdf", "log/a.jsonl",
                     "venv/bin/python", "browser/motori/x"):
        esito = subprocess.run(["git", "check-ignore", "-q", percorso], cwd=PROGETTO)
        assert esito.returncode == 0, percorso



# --- contatto per Wikipedia (1/11) ---------------------------------------------------------------------------------

@pytest.fixture
def contatto_prova(cartella_temporanea: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Segreti e account Claude finti: niente file veri del computer."""
    monkeypatch.setenv("RICERCA_SEGRETI", str(cartella_temporanea / "segreti.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cartella_temporanea))
    monkeypatch.setenv("HOME", str(cartella_temporanea / "casa"))
    (cartella_temporanea / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "persona@esempio.org"}}')
    return cartella_temporanea / "segreti.json"


def test_contatto_wikipedia_impostazioni_poi_account(contatto_prova: Path) -> None:
    from ricerca_catena.web.fonti import wikipedia_grafo as wg
    assert wg.contatto_wikipedia() == ("persona@esempio.org", "account")
    assert wg.user_agent() == ("ricerca-catena/0.1 (uso personale; persona@esempio.org) httpx", True)
    config.salva_segreti({"contatto_wikipedia": "dedicato@esempio.org", "stackexchange": {"chiave": "abc"}})
    assert contatto_prova.stat().st_mode & 0o777 == 0o600
    assert wg.contatto_wikipedia() == ("dedicato@esempio.org", "impostazioni")  # scritto a mano: vince
    config.salva_segreti({"contatto_wikipedia_attivo": False})
    assert wg.contatto_wikipedia() == ("", "") and wg.user_agent() == (wg.USER_AGENT, False)
    assert config.segreti()["stackexchange"] == {"chiave": "abc"}  # le altre chiavi restano
    config.salva_segreti({"contatto_wikipedia_attivo": True, "contatto_wikipedia": "non è; un contatto"})
    assert wg.contatto_wikipedia() == ("", "")  # non valido: nessun contatto, mai l'account al suo posto


def test_contatto_wikipedia_senza_account(contatto_prova: Path) -> None:
    from ricerca_catena.web.fonti import wikipedia_grafo as wg
    (contatto_prova.parent / ".claude.json").unlink()
    assert wg.contatto_wikipedia() == ("", "") and wg.user_agent()[1] is False
    assert not wg.contatto_valido("solo-testo") and wg.contatto_valido("https://esempio.org/contatti")


def test_contatto_solo_verso_wikipedia(contatto_prova: Path) -> None:
    """Il client di Wikipedia rifiuta ogni altro dominio; le altre fonti non hanno il contatto."""
    from ricerca_catena.web.fonti import wikipedia_grafo as wg
    w = wg.WikipediaGrafo(consenti_locali=False)
    assert "persona@esempio.org" in w.client._client.headers["User-Agent"]
    for url in ("https://esempio.org/x", "https://wikidata.org/w/api.php", "https://wikipedia.org.esempio.com/"):
        with pytest.raises(base.ErroreFonte, match="dominio non consentito"):
            w.client._controlla_url(url)
    w.client._controlla_url("https://it.wikipedia.org/w/api.php")
