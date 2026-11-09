"""Server locale dell'app: API e pagina, solo da 127.0.0.1, con chiave segreta (cookie impostato da Electron).

Avvio: `python -m ricerca_catena.server` (lo avvia l'app). Ascolta su una porta libera e la scrive su stdout come
prima riga JSON ({"porta": 12345}); la chiave arriva da RICERCA_TOKEN. Un'indagine gira in un processo a sé
(`python -m ricerca_catena.indagine`); il server ne legge lo stato e gli eventi dai file della sua cartella.
"""
import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import RADICE, configurazione
from .indagine import FILE_BLOCCO, Indagine, processo_attivo
from .web.fonti import config as config_fonti
from .web.fonti.wikipedia_grafo import contatto_valido, contatto_wikipedia

log = logging.getLogger("ricerca_catena.server")
INTERFACCIA = RADICE / "app" / "interfaccia"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
NOME_VALIDO = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}-[a-z0-9-]{1,50}$"


class Protezione(BaseHTTPMiddleware):
    """Accetta solo richieste a 127.0.0.1 con la chiave segreta."""

    def __init__(self, app: Any, token: str, porta: Callable[[], int]) -> None:
        super().__init__(app)
        self.token = token
        self.porta = porta

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = request.headers.get("host", "")
        if host not in (f"127.0.0.1:{self.porta()}", f"localhost:{self.porta()}"):
            return JSONResponse({"errore": "Host non consentito."}, status_code=403)
        if not hmac.compare_digest(request.cookies.get("token", ""), self.token):
            return JSONResponse({"errore": "Accesso non autorizzato."}, status_code=403)
        if request.method == "POST" and request.headers.get("origin") not in (None, f"http://{host}"):
            return JSONResponse({"errore": "Origine non consentita."}, status_code=403)
        risposta = await call_next(request)
        risposta.headers["Content-Security-Policy"] = CSP
        risposta.headers["X-Content-Type-Options"] = "nosniff"
        risposta.headers["Cache-Control"] = "no-store"
        return risposta


class Gestore:
    """Le indagini sul disco e quella in corso (una alla volta: i profili del browser sono condivisi)."""

    def __init__(self, cartella: Path | None = None) -> None:
        self._cartella = cartella
        self.processo: subprocess.Popen | None = None
        self.in_corso: str | None = None

    @property
    def cartella(self) -> Path:
        return self._cartella or configurazione.cartella("cartella_indagini")

    def percorso(self, ident: str) -> Path:
        if not re.match(NOME_VALIDO, ident or "") or not (self.cartella / ident / "stato.json").is_file():
            raise ValueError("Indagine inesistente.")
        return self.cartella / ident

    def stato(self, ident: str) -> dict:
        return json.loads((self.percorso(ident) / "stato.json").read_text(encoding="utf-8"))

    def elenco(self) -> list[dict]:
        voci = []
        for file in sorted(self.cartella.glob("*/stato.json"), reverse=True):
            try:
                s = json.loads(file.read_text(encoding="utf-8"))
                domande = s.get("domande") or []
                voci.append({"id": s["id"], "argomento": s["argomento"], "titolo": (s.get("piano") or {}).get("titolo"),
                             "stato": self._stato_vero(s), "creata": s["creata"], "giro": s.get("giro"),
                             "domande": len(domande), "trovate": sum(d["stato"] == "risposta" for d in domande),
                             "speso_usd": round(s.get("speso_regia_usd", 0) + sum(d.get("costo_usd") or 0
                                                                               for d in domande), 2),
                             "pdf": bool((s.get("documento") or {}).get("pdf"))})
            except (ValueError, KeyError, TypeError):  # un file rovinato non deve nascondere le altre indagini
                continue
        return voci

    def _stato_vero(self, s: dict) -> str:
        """Un'indagine "in corso" sul disco ma senza processo è stata interrotta (app chiusa, computer spento)."""
        if s["stato"] == "in_corso" and not self.in_esecuzione(s["id"]):
            return "interrotta"
        return s["stato"]

    def _mio_attivo(self) -> bool:
        return self.processo is not None and self.processo.poll() is None

    def in_esecuzione(self, ident: str) -> bool:
        """Anche un processo sopravvissuto a un server precedente: il blocco nella cartella lo dice."""
        return (self._mio_attivo() and self.in_corso == ident) or processo_attivo(self.cartella / ident) is not None

    def attiva(self) -> bool:
        return self._mio_attivo() or any(processo_attivo(f.parent) is not None
                                         for f in self.cartella.glob(f"*/{FILE_BLOCCO}"))

    def avvia(self, ident: str, solo_documento: bool = False) -> None:
        if self.attiva():
            raise ValueError("C'è già un'indagine in corso: fermala o aspetta che finisca.")
        cartella = self.percorso(ident)
        python = RADICE / "venv" / "bin" / "python"
        with (cartella / "errori.log").open("a", encoding="utf-8") as errori:
            self.processo = subprocess.Popen(
                [str(python if python.exists() else sys.executable), "-m", "ricerca_catena.indagine", str(cartella)]
                + (["--documento"] if solo_documento else []), cwd=RADICE, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=errori, start_new_session=True)
        self.in_corso = ident

    def aggiorna_limiti(self, ident: str, dati: dict) -> None:
        """Limiti nuovi per un'indagine ferma, prima di riprenderla (solo quelli indicati)."""
        if self.in_esecuzione(ident):
            raise ValueError("Questa indagine è già in corso.")
        s = self.stato(ident)
        budget = _numero(dati.get("budget_usd"), *configurazione.INTERVALLI["budget_usd"], "Budget")
        minuti = _numero(dati.get("max_minuti"), *configurazione.INTERVALLI["max_minuti"], "Tempo massimo")
        if budget is not None:
            s["limiti"]["budget_usd"] = budget
        if minuti is not None:
            s["limiti"]["max_minuti"] = minuti
        if "senza_limiti" in dati:
            s["limiti"]["senza_limiti"] = bool(dati["senza_limiti"])
        file = self.percorso(ident) / "stato.json"
        temporaneo = file.with_suffix(".tmp")
        temporaneo.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        temporaneo.replace(file)

    def pulisci(self, ident: str) -> None:
        """Toglie i pezzi di pagina letti: servono solo mentre le domande sono in corso."""
        if self.in_esecuzione(ident):
            raise ValueError("Questa indagine è in corso.")
        shutil.rmtree(self.percorso(ident) / "pezzi", ignore_errors=True)

    def ferma(self, ident: str) -> None:
        if self._mio_attivo() and self.in_corso == ident:
            self.processo.send_signal(signal.SIGTERM)
            return
        pid = processo_attivo(self.percorso(ident))
        if not pid:
            raise ValueError("Questa indagine non è in corso.")
        os.kill(pid, signal.SIGTERM)  # sopravvissuta a un server precedente

    def chiudi(self) -> None:
        if self._mio_attivo():
            self.processo.send_signal(signal.SIGTERM)
            try:
                self.processo.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.processo.pid, signal.SIGKILL)


def crea_app(token: str, porta: Callable[[], int], gestore: Gestore | None = None) -> Starlette:
    gestore = gestore or Gestore()

    async def corpo(request: Request) -> dict[str, Any]:
        try:
            dati = await request.json()
        except ValueError:
            dati = {}
        return dati if isinstance(dati, dict) else {}

    async def protetto(funzione: Callable[[], Any]) -> JSONResponse:
        try:
            esito = funzione()
            if asyncio.iscoroutine(esito):
                esito = await esito
            return JSONResponse(esito)
        except ValueError as e:
            return JSONResponse({"errore": str(e)}, status_code=400)
        except Exception as e:  # noqa: BLE001
            log.exception("Errore nel server")
            return JSONResponse({"errore": f"Errore: {e}"}, status_code=500)

    async def home(_request: Request) -> Response:
        return FileResponse(INTERFACCIA / "index.html")

    async def impostazioni(request: Request) -> JSONResponse:
        if request.method == "POST":
            dati = await corpo(request)
            return await protetto(lambda: salva_impostazioni(dati))
        return await protetto(leggi_impostazioni)

    async def indagini(request: Request) -> JSONResponse:
        if request.method == "POST":
            dati = await corpo(request)

            def nuova() -> dict:
                if gestore.attiva():
                    raise ValueError("C'è già un'indagine in corso: fermala o aspetta che finisca.")
                ind = Indagine.crea(str(dati.get("argomento") or ""), modello_regia=dati.get("modello_regia"),
                                    budget_usd=_numero(dati.get("budget_usd"), 0.5, 500, "Budget"),
                                    max_minuti=_numero(dati.get("max_minuti"), 10, 24 * 60, "Tempo massimo"),
                                    senza_limiti=bool(dati.get("senza_limiti")), cartella_base=gestore.cartella)
                gestore.avvia(ind.cartella.name)
                return {"id": ind.cartella.name}
            return await protetto(nuova)
        return await protetto(gestore.elenco)

    async def indagine(request: Request) -> JSONResponse:
        ident = request.path_params["ident"]

        def leggi() -> dict:
            s = gestore.stato(ident)
            return {**s, "stato": gestore._stato_vero(s)}
        return await protetto(leggi)

    async def azione(request: Request) -> JSONResponse:
        ident, nome = request.path_params["ident"], request.path_params["azione"]

        dati = await corpo(request)

        def esegui() -> dict:
            if nome == "ferma":
                gestore.ferma(ident)
            elif nome == "riprendi":
                if gestore.attiva():
                    raise ValueError("C'è già un'indagine in corso: fermala o aspetta che finisca.")
                gestore.aggiorna_limiti(ident, dati)
                gestore.avvia(ident)
            elif nome == "pulisci":
                gestore.pulisci(ident)
            elif nome == "documento":
                gestore.avvia(ident, solo_documento=True)
            elif nome == "apri":
                pdf = gestore.percorso(ident) / "documento.pdf"
                if not pdf.is_file():
                    raise ValueError("Il PDF non c'è ancora.")
                subprocess.Popen(["xdg-open", str(pdf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif nome == "cartella":
                subprocess.Popen(["xdg-open", str(gestore.percorso(ident))], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                raise ValueError("Azione sconosciuta.")
            return {"ok": True}
        return await protetto(esegui)

    async def eventi(request: Request) -> Response:
        """Gli eventi dell'indagine (SSE), dal file eventi.jsonl: prima quelli già scritti, poi i nuovi."""
        ident = request.path_params["ident"]
        try:
            file = gestore.percorso(ident) / "eventi.jsonl"
        except ValueError as e:
            return JSONResponse({"errore": str(e)}, status_code=400)
        try:
            ultimo = max(0, int(request.headers.get("last-event-id") or request.query_params.get("da") or 0))
        except ValueError:
            ultimo = 0

        async def flusso():
            inviati, giri = ultimo, 0
            while True:
                righe = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
                for numero in range(inviati, len(righe)):
                    yield f"id: {numero + 1}\ndata: {righe[numero]}\n\n"
                inviati = max(inviati, len(righe))
                # Indagine conclusa e nessun processo al lavoro: il flusso finisce (l'app lo riapre se riprende).
                if righe and '"tipo": "fine"' in righe[-1] and not gestore.in_esecuzione(ident):
                    yield "event: chiuso\ndata: {}\n\n"  # solo questo chiude il flusso: un «fine» può essere di una corsa passata
                    break
                giri += 1
                if giri % 30 == 0:
                    yield ": vivo\n\n"
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.5)
        return StreamingResponse(flusso(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    rotte = [
        Route("/", home),
        Route("/api/impostazioni", impostazioni, methods=["GET", "POST"]),
        Route("/api/indagini", indagini, methods=["GET", "POST"]),
        Route("/api/indagini/{ident}", indagine),
        Route("/api/indagini/{ident}/eventi", eventi),
        Route("/api/indagini/{ident}/{azione}", azione, methods=["POST"]),
        Mount("/interfaccia", StaticFiles(directory=INTERFACCIA)),
    ]
    @asynccontextmanager
    async def ciclo_vita(_app: Starlette):
        yield
        gestore.chiudi()  # l'app si chiude: l'indagine in corso si ferma (si potrà riprendere)

    app = Starlette(routes=rotte, lifespan=ciclo_vita)
    app.add_middleware(Protezione, token=token, porta=porta)
    app.state.gestore = gestore
    return app


def _numero(valore: Any, minimo: float, massimo: float, nome: str) -> float | None:
    if valore in (None, ""):
        return None
    try:
        numero = float(valore)
    except (TypeError, ValueError):
        raise ValueError(f"{nome}: deve essere un numero.") from None
    if not minimo <= numero <= massimo:
        raise ValueError(f"{nome}: deve essere tra {minimo:g} e {massimo:g}.")
    return numero


def leggi_impostazioni() -> dict:
    imp = configurazione.carica()
    segreti = config_fonti.segreti()
    contatto, origine = contatto_wikipedia()
    return {"modello_regia": imp["modello_regia"], "modelli": list(configurazione.MODELLI),
            "modello_catena": imp["modello_catena"], "budget_domanda_usd": imp["budget_domanda_usd"],
            "pagine_domanda": imp["pagine_domanda"], "minuti_domanda": imp["minuti_domanda"],
            "budget_usd": imp["budget_usd"], "max_minuti": imp["max_minuti"], "senza_limiti": imp["senza_limiti"],
            "domande_in_parallelo": imp["domande_in_parallelo"],
            "wikipedia": {"attivo": segreti.get("contatto_wikipedia_attivo") is not False,
                          "contatto": str(segreti.get("contatto_wikipedia") or ""),
                          "in_uso": contatto, "origine": origine}}


def salva_impostazioni(dati: dict) -> dict:
    modifiche: dict[str, Any] = {}
    for chiave in ("modello_regia", "modello_catena"):
        if chiave in dati:
            if dati[chiave] not in configurazione.MODELLI:
                raise ValueError("Modello non valido.")
            modifiche[chiave] = dati[chiave]
    for chiave, nome in (("budget_usd", "Budget"), ("max_minuti", "Tempo"), ("domande_in_parallelo", "Domande in parallelo"),
                         ("budget_domanda_usd", "Budget per domanda"), ("pagine_domanda", "Pagine per domanda"),
                         ("minuti_domanda", "Minuti per domanda")):
        if chiave in dati:
            valore = _numero(dati[chiave], *configurazione.INTERVALLI[chiave], nome)
            if valore is not None:
                modifiche[chiave] = int(valore) if chiave in ("domande_in_parallelo", "pagine_domanda") else valore
    if "senza_limiti" in dati:
        modifiche["senza_limiti"] = bool(dati["senza_limiti"])
    wiki = dati.get("wikipedia")
    if isinstance(wiki, dict):
        contatto = " ".join(str(wiki.get("contatto") or "").split())
        if contatto and not contatto_valido(contatto):
            raise ValueError("Contatto per Wikipedia: un'email o un indirizzo web (http:// o https://), senza spazi.")
        config_fonti.salva_segreti({"contatto_wikipedia_attivo": bool(wiki.get("attivo", True)),
                                    "contatto_wikipedia": contatto})
    if modifiche:
        configurazione.salva(modifiche)
    return leggi_impostazioni()


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    token = os.environ.get("RICERCA_TOKEN", "")
    if len(token) < 32:
        sys.exit("RICERCA_TOKEN mancante o troppo corto: il server va avviato dall'app.")
    presa = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    presa.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    presa.bind(("127.0.0.1", 0))
    porta = presa.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(crea_app(token, lambda: porta), log_level="warning", access_log=False))
    print(json.dumps({"porta": porta}), flush=True)
    server.run(sockets=[presa])


if __name__ == "__main__":
    main()
