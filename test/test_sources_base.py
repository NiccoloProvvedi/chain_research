"""Tests of the shared source infrastructure (no network: fake transports, fake clock, short processes)."""
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from chain_research.web import network
from chain_research.web.sources import base, config, http
from chain_research.web.sources.limits import RateLimiter

PROJECT = Path(__file__).resolve().parents[1]


# --- network --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fe80::1",
                                "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_forbidden_ip(ip: str) -> None:
    assert network.forbidden_ip(ip)


def test_public_ips_and_local_names() -> None:
    assert not network.forbidden_ip("93.184.216.34") and not network.forbidden_ip("2606:4700::1111")
    assert network.local_name("printer.local") and network.local_name("localhost") and not network.local_name("example.org")


# --- HTTP client ----------------------------------------------------------------------------------------------------

def _client(handler, domains=("api.example.org",), **options) -> http.SourceClient:
    waits: list[float] = []
    client = http.SourceClient("proof", user_agent="test", domains=domains, transport=httpx.MockTransport(handler),
                              sleep=waits.append, **options)
    client.waits = waits  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _public_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """In tests the example names count as public without querying DNS."""
    true = network.forbidden_host.__wrapped__

    def fake(host: str) -> bool:
        if host.endswith(("example.org", "other.org")):
            return False
        return true(host)
    monkeypatch.setattr(network, "forbidden_host", fake)


def test_json_and_user_agent_sent() -> None:
    seen = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(200, json={"ok": True})
    assert _client(handler).get_json("https://api.example.org/x", params={"q": "a b"}) == {"ok": True}
    assert seen[0].headers["user-agent"] == "test" and seen[0].url.params["q"] == "a b"


def test_domain_not_allowed_is_refused() -> None:
    with pytest.raises(base.UnreadableContent, match="domain"):
        _client(lambda r: httpx.Response(200)).get_json("https://other.org/x")


def test_redirect_to_local_network_is_blocked() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:1234/v1/models"})
    with pytest.raises(base.UnreadableContent, match="local network"):
        _client(handler, domains=None).get_text("https://api.example.org/x")


def test_public_redirect_is_followed_and_checked() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == "api.example.org":
            return httpx.Response(301, headers={"location": "https://cdn.example.org/file"})
        return httpx.Response(200, text="content")
    assert _client(handler, domains=None).get_text("https://api.example.org/x") == "content"
    # with the domain list, a redirect to a domain not allowed is refused
    with pytest.raises(base.UnreadableContent, match="domain"):
        _client(lambda r: httpx.Response(302, headers={"location": "https://other.org/"})).get_text(
            "https://api.example.org/x")


def test_retries_on_5xx_with_increasing_wait() -> None:
    answers = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[1])])
    client = _client(lambda r: next(answers))
    assert client.get_json("https://api.example.org/x") == [1]
    assert len(client.waits) == 2 and 2 <= client.waits[0] < 3 and 4 <= client.waits[1] < 5


def test_retry_after_is_respected() -> None:
    answers = iter([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json={})])
    client = _client(lambda r: next(answers))
    client.get_json("https://api.example.org/x")
    assert client.waits == [7.0]


def test_persistent_429_becomes_reached_limit() -> None:
    with pytest.raises(base.ReachedLimit):
        _client(lambda r: httpx.Response(429)).get_json("https://api.example.org/x")


@pytest.mark.parametrize("code,error", [(401, base.SourceUnavailable), (403, base.SourceUnavailable),
                                           (404, base.UnreadableContent)])
def test_no_retry_on_401_403_404(code: int, error: type) -> None:
    calls = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(r)
        return httpx.Response(code)
    with pytest.raises(error):
        _client(handler).get_json("https://api.example.org/x")
    assert len(calls) == 1


def test_response_too_large() -> None:
    with pytest.raises(base.UnreadableContent, match="too large"):
        _client(lambda r: httpx.Response(200, content=b"x" * 3_000_000)).get_text("https://api.example.org/x",
                                                                                 max_mb=1)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"local": true}')

    def log_message(self, *args) -> None:
        pass


def test_dns_rebinding_blocked_at_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name passes the initial check (it looks public), but at connection time it leads to 127.0.0.1:
    the safe backend refuses the connection. With local_allow the same request reaches the local server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port_number = server.server_address[1]
    try:
        monkeypatch.setattr(http, "resolve", lambda host, port: ["127.0.0.1"])
        client = http.SourceClient("proof", user_agent="test", domains=None)
        with pytest.raises(base.UnreadableContent, match="not allowed"):
            client.get_json(f"http://service.example.org:{port_number}/")
        is_open = http.SourceClient("proof", user_agent="test", domains=None, local_allow=True)
        assert is_open.get_json(f"http://service.example.org:{port_number}/") == {"local": True}
    finally:
        server.shutdown()


# --- rate limiter ---------------------------------------------------------------------------------------------------

class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_limiter_interval_and_hourly_cap() -> None:
    o = _Clock()
    lim = RateLimiter(5, max_hour=3, name="youtube", clock=o, sleep=o.sleep)
    lim.wait_for()
    lim.wait_for()
    assert o.t == 1005.0
    lim.wait_for()
    with pytest.raises(base.ReachedLimit, match="per hour"):
        lim.wait_for()
    o.t += 3600
    lim.wait_for()  # an hour later, it starts again


def test_limiter_pause() -> None:
    o = _Clock()
    lim = RateLimiter(1, clock=o, sleep=o.sleep)
    lim.pause(3600)
    with pytest.raises(base.ReachedLimit, match="paused"):
        lim.wait_for()
    lim.pause(0)
    o.t += 3601
    lim.wait_for()
    assert lim.in_pause() == 0


# --- configuration and secrets --------------------------------------------------------------------------------------

def test_secrets_only_with_permissions_600(temporary_folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = temporary_folder / "secrets.json"
    monkeypatch.setenv("CHAIN_SECRETS", str(secrets))
    assert config.secrets() == {}
    secrets.write_text('{"stackexchange": {"key": "abc"}}')
    secrets.chmod(0o644)
    assert config.secrets() == {}
    secrets.chmod(0o600)
    assert config.secrets()["stackexchange"]["key"] == "abc"


def test_secrets_and_user_data_excluded_from_git() -> None:
    for path in ("config/secrets.json", "config/settings.json", "investigations/x/document.pdf", "log/a.jsonl",
                     "venv/bin/python", "browser/engines/x"):
        outcome = subprocess.run(["git", "check-ignore", "-q", path], cwd=PROJECT)
        assert outcome.returncode == 0, path



# --- Wikipedia contact (1 Nov) ------------------------------------------------------------------------------------

@pytest.fixture
def proof_contact(temporary_folder: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake secrets and Claude account: no real files of the computer."""
    monkeypatch.setenv("CHAIN_SECRETS", str(temporary_folder / "secrets.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(temporary_folder))
    monkeypatch.setenv("HOME", str(temporary_folder / "home"))
    (temporary_folder / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "person@example.org"}}')
    return temporary_folder / "secrets.json"


def test_wikipedia_contact_from_settings_then_account(proof_contact: Path) -> None:
    from chain_research.web.sources import wikipedia_graph as wg
    assert wg.wikipedia_contact() == ("person@example.org", "account")
    assert wg.user_agent() == ("ricerca-catena/0.1 (personal use; person@example.org) httpx", True)
    config.save_secrets({"wikipedia_contact": "dedicated@example.org", "stackexchange": {"key": "abc"}})
    assert proof_contact.stat().st_mode & 0o777 == 0o600
    assert wg.wikipedia_contact() == ("dedicated@example.org", "settings")  # written by hand: wins
    config.save_secrets({"wikipedia_contact_active": False})
    assert wg.wikipedia_contact() == ("", "") and wg.user_agent() == (wg.USER_AGENT, False)
    assert config.secrets()["stackexchange"] == {"key": "abc"}  # the other keys stay
    config.save_secrets({"wikipedia_contact_active": True, "wikipedia_contact": "not; a contact"})
    assert wg.wikipedia_contact() == ("", "")  # invalid: no contact, never the account in its place


def test_wikipedia_contact_without_account(proof_contact: Path) -> None:
    from chain_research.web.sources import wikipedia_graph as wg
    (proof_contact.parent / ".claude.json").unlink()
    assert wg.wikipedia_contact() == ("", "") and wg.user_agent()[1] is False
    assert not wg.valid_contact("just-text") and wg.valid_contact("https://example.org/contacts")


def test_contact_sent_only_to_wikipedia(proof_contact: Path) -> None:
    """The Wikipedia client refuses every other domain; the other sources do not have the contact."""
    from chain_research.web.sources import wikipedia_graph as wg
    w = wg.WikipediaGraph(local_allow=False)
    assert "person@example.org" in w.client._client.headers["User-Agent"]
    for url in ("https://example.org/x", "https://wikidata.org/w/api.php", "https://wikipedia.org.example.com/"):
        with pytest.raises(base.SourceError, match="domain not allowed"):
            w.client._check_url(url)
    w.client._check_url("https://it.wikipedia.org/w/api.php")
