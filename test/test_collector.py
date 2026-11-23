"""Collection without browser or network: excluded sites, wiki archive, limit messages."""
import pytest

from chain_research.web.collector import Collector


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=x", "https://youtu.be/x", "https://x.com/a", "https://twitter.com/a",
    "https://www.amazon.it/dp/1", "https://www.ebay.de/i/1", "https://www.google.com/search?q=a",
    "https://it.linkedin.com/in/a", "https://www.bing.com/a", "https://html.duckduckgo.com/a",
    "https://old.reddit.com/r/a", "https://www.facebook.com/a"])
def test_excluded_sites(url: str) -> None:
    assert Collector("question")._excluded(url)


@pytest.mark.parametrize("url", [
    "https://www.linux.com/a", "https://www.unix.com/a", "https://www.netflix.com/a", "https://www.dropbox.com/a",
    "https://www.fedex.com/a", "https://arduino.cc/a", "https://www.notgoogle.org/a", "https://binging.com/a"])
def test_partial_host_name_not_excluded(url: str) -> None:
    assert not Collector("question")._excluded(url)


class FakePage:
    def __init__(self) -> None:
        self.contents: list[str] = []

    @property
    def request(self):
        raise AssertionError("the archive must not go through page.request: it escapes the network filter")

    def goto(self, *_to, **_k) -> None:
        pass

    def set_content(self, html: str, **_k) -> None:
        self.contents.append(html)

    def evaluate(self, _script: str) -> dict:
        return {"title": "t", "text": "x" * 700}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url: str, params: dict | None = None) -> dict:
        self.calls.append((url, params or {}))
        return {"parse": {"title": "A</title><script>x</script>", "text": "<p>" + "t" * 600 + "</p>"}}


def test_wiki_archive_uses_safe_client_and_escapes_title() -> None:
    r = Collector("question")
    client, page = FakeClient(), FakePage()
    r._client_archive = lambda _host: client
    r.browser = type("B", (), {"page": page})()
    assert r._from_archive_wiki('https://wiki.example.org/wiki/AT&T_"x"')["text"]
    url, params = client.calls[0]
    assert url == "https://wiki.example.org/w/api.php" and params["page"] == 'AT&T_"x"' and params["action"] == "parse"
    assert "<title>A</title><script>" not in page.contents[0] and '"x""' not in page.contents[0]


def test_minute_limit_message_is_rounded() -> None:
    r = Collector("question", options={"chain_minutes": 5.003691091149818})
    r._initial_wait, r._start = 0.0, -1e9
    assert r._reached_limit() == "reached the safety limit of 5 minutes"


# --- review of 2026-11-11: the question's outcome carries the status decided by the checks ------------------------

@pytest.mark.parametrize("options,expected", [({}, 1), ({"model_checks": False}, 0)])
def test_run_does_final_review_and_reports_status(tmp_path, monkeypatch, options: dict, expected: int) -> None:
    from chain_research.web import chain as chain_module
    from chain_research.web import collector

    class FakeChain:
        facts_exams = 0

        def __init__(self, r) -> None:
            self.value, self.reason = {"value": "56 m"}, "chain complete"

        def walk(self) -> None:
            pass

        def final_review(self) -> None:
            FakeChain.facts_exams += 1

        def checks(self) -> dict:
            return {"confirmed": False, "status": "contested"}

        def write(self, _task: str) -> str:
            return "**Answer:** 56 m."

        def summary(self) -> dict:
            return {}

    class FakeBrowser:
        def __init__(self, *_to, **_k) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(chain_module, "Chain", FakeChain)
    monkeypatch.setattr(collector, "Browser", FakeBrowser)
    r = collector.Collector("question", options=options, pieces_folder=tmp_path / "pieces", profile=tmp_path / "profile")
    outcome = r.run()
    assert FakeChain.facts_exams == expected
    assert (outcome["status"], outcome["confirmed"], outcome["value"], outcome["found"]) == ("contested", False, "56 m", True)
