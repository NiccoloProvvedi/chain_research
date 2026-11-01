"""Fonti della ricerca oltre alle pagine web: Wikipedia come grafo (wikipedia_grafo) e il client HTTP sicuro."""
import re
from urllib.parse import parse_qs, urlsplit

SITI_STACKEXCHANGE = {"stackoverflow.com": "stackoverflow", "superuser.com": "superuser", "askubuntu.com": "askubuntu",
                      "unix.stackexchange.com": "unix", "serverfault.com": "serverfault"}
ID_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")


def chiave_canonica(url: str) -> str | None:
    """Chiave delle piattaforme che il lettore di pagine non legge bene (domande di Stack Exchange, Wikidata, Hacker
    News, video di YouTube): la ricerca salta questi risultati, come faceva sul banco di prova."""
    try:
        parti = urlsplit(url)
    except ValueError:
        return None
    host = (parti.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in SITI_STACKEXCHANGE:
        trovato = re.match(r"/questions/(\d+)", parti.path)
        return f"stackexchange:{SITI_STACKEXCHANGE[host]}:{trovato.group(1)}" if trovato else None
    if host == "wikidata.org":
        trovato = re.fullmatch(r"/wiki/(Q\d+)", parti.path)
        return f"wikidata:{trovato.group(1)}" if trovato else None
    if host == "news.ycombinator.com":
        numero = parse_qs(parti.query).get("id", [""])[0]
        return f"hackernews:{numero}" if numero.isdigit() else None
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        if host == "youtu.be":
            candidato = parti.path.strip("/")
        elif parti.path.startswith(("/shorts/", "/live/", "/embed/")):
            candidato = parti.path.split("/")[2]
        else:
            candidato = parse_qs(parti.query).get("v", [""])[0]
        return f"youtube:{candidato}" if ID_VIDEO.fullmatch(candidato) else None
    return None
