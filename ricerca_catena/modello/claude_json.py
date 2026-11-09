"""Chiamate a Claude: `claude -p` isolato, senza strumenti, con risposta JSON validata.

Nessuno strumento (`--tools ""`), nessun server
MCP, nessun hook, nessun CLAUDE.md, nessuna memoria automatica, cartella di lavoro vuota, istruzioni di sistema
proprie, `--json-schema` per la risposta e `--max-budget-usd` come tetto della singola chiamata. Ogni chiamata,
riuscita o no, scrive una riga in <cartella_log>/pensatore.jsonl con punto, modello, token (anche di cache), costo
e durata. I campi della riga `result` sono quelli osservati il 13/10 (fixture test/fixtures/orchestratore).
"""
import json
import re
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import RADICE
from . import registro, rigioco
from . import config as config_pensatore


class ErroreClaude(RuntimeError):
    """Claude non ha potuto rispondere (non collegato, tempo scaduto, budget, risposta non valida)."""


class LimiteSessione(ErroreClaude):
    """Il limite di sessione dell'abbonamento è finito: rispondere «non trovata» sarebbe falso, l'indagine si ferma."""

    def __init__(self, secondi: float) -> None:
        super().__init__(f"Limite di sessione di Claude raggiunto: si rinnova tra circa {round(secondi / 60)} minuti.")
        self.secondi = secondi


class Interrotta(ErroreClaude):
    """L'utente ha fermato l'indagine mentre Claude stava rispondendo."""


# Le chiamate in corso in questo processo, per fermarle (`interrompi`). Dizionario senza serrature: `interrompi` gira
# dentro un gestore di segnale e una serratura presa dal thread principale lo bloccherebbe per sempre.
_in_corso: dict[int, subprocess.Popen] = {}


def interrompi() -> None:
    """Chiude le chiamate in corso: chi le aspettava riceve Interrotta."""
    for processo in list(_in_corso.values()):
        if processo.poll() is None:
            processo.interrotto = True
            processo.terminate()


@dataclass
class Consumo:
    punto: str
    modello: str
    modello_esatto: str = ""
    token_ingresso: int = 0
    token_uscita: int = 0
    token_cache_letti: int = 0
    token_cache_scritti: int = 0
    costo_usd: float = 0.0
    secondi: float = 0.0
    esito: str = "ok"


def cartella_vuota() -> Path:
    """Cartella di lavoro vuota per Claude, dentro il progetto."""
    cartella = RADICE / "browser" / "claude-vuota"
    cartella.mkdir(parents=True, exist_ok=True)
    return cartella


def comando(modello: str, istruzioni: str, schema: dict | None, max_budget_usd: float | None = None) -> list[str]:
    impostazioni = {"disableAllHooks": True, "claudeMdExcludes": ["/**/CLAUDE.md", "/**/CLAUDE.local.md"]}
    righe = [shutil.which("claude") or "claude", "-p", "--input-format", "stream-json", "--output-format",
             "stream-json", "--verbose", "--model", modello, "--tools", "", "--strict-mcp-config",
             "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence", "--settings", json.dumps(impostazioni),
             "--system-prompt", istruzioni]
    if max_budget_usd is not None:
        righe += ["--max-budget-usd", f"{max_budget_usd:.4f}"]
    return righe + (["--json-schema", json.dumps(schema)] if schema else [])


def leggi_risultato(stdout: str) -> dict[str, Any] | None:
    risultato = None
    for riga in stdout.splitlines():
        try:
            dati = json.loads(riga)
        except ValueError:
            continue
        if dati.get("type") == "assistant" and "Not logged in" in json.dumps(dati):
            raise ErroreClaude("Claude non è collegato al tuo account: esegui `claude` e fai l'accesso.")
        if dati.get("type") == "result":
            risultato = dati
    return risultato


def consumo_da(risultato: dict[str, Any], punto: str, modello: str, secondi: float) -> Consumo:
    uso = risultato.get("usage") or {}
    return Consumo(punto=punto, modello=modello, modello_esatto=", ".join(risultato.get("modelUsage") or {}),
                   token_ingresso=int(uso.get("input_tokens") or 0),
                   token_uscita=int(uso.get("output_tokens") or 0),
                   token_cache_letti=int(uso.get("cache_read_input_tokens") or 0),
                   token_cache_scritti=int(uso.get("cache_creation_input_tokens") or 0),
                   costo_usd=float(risultato.get("total_cost_usd") or 0), secondi=round(secondi, 2))


def _esegui(righe: list[str], testo: str, attesa: float) -> str:
    messaggio = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": testo}]}}
    try:
        processo = subprocess.Popen(righe, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    cwd=cartella_vuota(), env={**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    except FileNotFoundError:
        raise ErroreClaude("Claude Code non è installato.") from None
    _in_corso[id(processo)] = processo
    try:
        stdout, _ = processo.communicate(json.dumps(messaggio) + "\n", timeout=attesa)
    except subprocess.TimeoutExpired:
        processo.kill()
        processo.communicate()
        raise ErroreClaude("Claude non ha risposto in tempo.") from None
    finally:
        _in_corso.pop(id(processo), None)
    if getattr(processo, "interrotto", False):
        raise Interrotta("Indagine fermata.")
    return stdout


# Secondi passati ad aspettare il rinnovo del limite di sessione (in questo processo): i limiti di tempo delle
# ricerche non li contano.
ATTESA_LIMITE = {"secondi": 0.0}


def secondi_al_rinnovo(stdout: str) -> float | None:
    """Se Claude risponde che il limite di sessione è finito ("resets 12:50am"), i secondi fino al rinnovo (più un
    minuto); altrimenti None. Senza un orario leggibile, 15 minuti. Conta solo una risposta di errore: il testo di una
    risposta normale può parlare di limiti."""
    risultato = None
    for riga in stdout.splitlines():
        try:
            dati = json.loads(riga)
        except ValueError:
            continue
        if isinstance(dati, dict) and dati.get("type") == "result":
            risultato = dati
    if risultato is not None and not risultato.get("is_error"):
        return None
    testo = stdout if risultato is None else str(risultato.get("result") or "")
    if "hit your session limit" not in testo and "usage limit" not in testo.lower():
        return None
    trovato = re.search(r"resets (\d{1,2})(?::(\d{2}))?\s*(am|pm)", testo, re.I)
    if not trovato:
        return 900.0
    ora = int(trovato.group(1)) % 12 + (12 if trovato.group(3).lower() == "pm" else 0)
    adesso = datetime.now()
    rinnovo = adesso.replace(hour=ora, minute=int(trovato.group(2) or 0), second=0, microsecond=0)
    if rinnovo <= adesso:
        rinnovo += timedelta(days=1)
    return min((rinnovo - adesso).total_seconds() + 60, 6 * 3600)


def chiedi(istruzioni: str, testo: str, schema: dict | None, punto: str, modello: str | None = None,
           max_budget_usd: float | None = None, attesa: float | None = None,
           piano: str = "") -> tuple[Any, Consumo]:
    """Una richiesta a Claude. Con `schema` restituisce il dict `structured_output`, senza il testo libero.
    `max_budget_usd` è il tetto della chiamata (predefinito: `pensatore.max_chiamata_usd`)."""
    cfg = config_pensatore.carica()
    modello = modello or cfg.modello_claude(punto)
    tetto = max_budget_usd if max_budget_usd is not None else cfg.pensatore.max_chiamata_usd
    righe = comando(modello, istruzioni, schema, tetto)
    chiave = rigioco.impronta("claude", modello, istruzioni, testo, schema)
    inizio = time.monotonic()
    consumo = Consumo(punto=punto, modello=modello)
    try:
        if rigioco.in_rigioco():
            stdout = rigioco.leggi(chiave)
        else:
            stdout = _esegui(righe, testo, attesa or cfg.pensatore.attesa_s)
            while os.environ.get("RICERCA_ATTENDI_LIMITE") and (pausa := secondi_al_rinnovo(stdout)) is not None:
                # Prove lunghe (banco): con il limite di sessione dell'abbonamento si aspetta il rinnovo invece di
                # far fallire le domande a metà (16/10, 23:45-00:50: "You've hit your session limit").
                registro.scrivi("pensatore", {"punto": punto, "modello": modello,
                                              "esito": f"limite di sessione: attendo {int(pausa)} s"})
                time.sleep(pausa)
                ATTESA_LIMITE["secondi"] += pausa
                stdout = _esegui(righe, testo, attesa or cfg.pensatore.attesa_s)
            rigioco.salva(chiave, {"punto": punto, "modello": modello, "testo": testo[:2000]}, stdout)
        if (pausa := secondi_al_rinnovo(stdout)) is not None:
            raise LimiteSessione(pausa)
        risultato = leggi_risultato(stdout)
        if risultato:
            consumo = consumo_da(risultato, punto, modello, time.monotonic() - inizio)
        if not risultato or risultato.get("is_error"):
            motivo = (risultato or {}).get("subtype") or "nessuna risposta"
            dettaglio = " ".join(str((risultato or {}).get("result") or "").split())[:200]
            raise ErroreClaude(f"Claude non ha dato una risposta valida ({motivo}"
                               + (f": {dettaglio}" if dettaglio else "") + ").")
        if schema is not None:
            dati = risultato.get("structured_output")
            if not isinstance(dati, dict):
                raise ErroreClaude("Claude non ha dato una risposta strutturata.")
        else:
            dati = str(risultato.get("result") or "").strip()
            if not dati:
                raise ErroreClaude("Claude non ha scritto il testo.")
        return dati, consumo
    except ErroreClaude as errore:
        consumo.esito = str(errore)[:200]
        errore.consumo = consumo  # anche una chiamata fallita può essere costata (per esempio oltre il tetto)
        raise
    finally:
        consumo.secondi = round(time.monotonic() - inizio, 2)
        registro.scrivi("pensatore", {"piano": piano, "caratteri_istruzioni": len(istruzioni),
                                      "caratteri_testo": len(testo), "tetto_usd": tetto, **asdict(consumo)})
