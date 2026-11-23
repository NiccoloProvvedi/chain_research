"""Research sources besides web pages: Wikipedia as a graph (wikipedia_graph) and the safe HTTP client."""
import re
from urllib.parse import parse_qs, urlsplit

STACKEXCHANGE_SITES = {"stackoverflow.com": "stackoverflow", "superuser.com": "superuser", "askubuntu.com": "askubuntu",
                      "unix.stackexchange.com": "unix", "serverfault.com": "serverfault"}
ID_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")


def canonical_key(url: str) -> str | None:
    """Key of the platforms the page reader does not read well (Stack Exchange questions, Wikidata, Hacker
    News, YouTube videos): the research skips these results, as it did on the test bench."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in STACKEXCHANGE_SITES:
        match = re.match(r"/questions/(\d+)", parts.path)
        return f"stackexchange:{STACKEXCHANGE_SITES[host]}:{match.group(1)}" if match else None
    if host == "wikidata.org":
        match = re.fullmatch(r"/wiki/(Q\d+)", parts.path)
        return f"wikidata:{match.group(1)}" if match else None
    if host == "news.ycombinator.com":
        number = parse_qs(parts.query).get("id", [""])[0]
        return f"hackernews:{number}" if number.isdigit() else None
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        if host == "youtu.be":
            candidate = parts.path.strip("/")
        elif parts.path.startswith(("/shorts/", "/live/", "/embed/")):
            candidate = parts.path.split("/")[2]
        else:
            candidate = parse_qs(parts.query).get("v", [""])[0]
        return f"youtube:{candidate}" if ID_VIDEO.fullmatch(candidate) else None
    return None
