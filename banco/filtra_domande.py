"""Filtro di difficoltà del banco di prova (Fase G, Parte 11.2 punto 7 del piano pensatore).

Una domanda candidata si scarta se Spark (2 tentativi) o Qwen (1 tentativo) la risolvono **senza strumenti**: la
risposta sarebbe a memoria e non misurerebbe la ricerca. Il giudizio: prima confronto normalizzato (la risposta
attesa compare nella risposta del modello), poi Qwen come giudice con 2 esempi (come DeepDive).

I modelli si usano a lotti per non cambiarli a ogni domanda: tutte le risposte di Spark, poi Qwen (risposte e
giudizi) con un contesto che sta tutto nella VRAM. Riprendibile: i risultati per domanda stanno in
`banco_prova/filtro.json` e le domande già filtrate si saltano.

Uso:  venv/bin/python banco_prova/filtra_domande.py [--per-tema 10]
Scrive banco_prova/filtro.json (tutti gli esiti) e banco_prova/domande.json (le approvate, al massimo --per-tema per
tema). L'approvazione finale (lettura di ogni domanda, decisione A2) si fa su domande.json.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from collections.abc import Callable
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RADICE), str(RADICE / "ponte")]

import configurazione  # noqa: E402
import qwen_client  # noqa: E402
from banco_prova.genera_domande import normalizza  # noqa: E402
from orchestratore import gpu  # noqa: E402

CARTELLA = Path(__file__).resolve().parent
SPARK = "spark-x2.5-4b"
QWEN = "qwen3.8-27b"
CONTESTO_SPARK = 8192
CONTESTO_QWEN = 32768  # sta tutto nella VRAM (13/10: 15,78 GB su 15,92, GTT +0,25 GB)
TENTATIVI_SPARK = 2

SISTEMA_RISPOSTA = (
    "Rispondi alla domanda solo con quello che sai, senza cercare nulla. Dai una sola risposta precisa (un nome, una "
    "data o un numero), nella prima riga, senza spiegazioni. Se non lo sai, prova comunque a indovinare.")
SISTEMA_GIUDICE = (
    "Sei un giudice. Ti do una domanda, la risposta attesa e la risposta di un modello. Dimmi se la risposta del "
    "modello indica la stessa cosa della risposta attesa (stessa persona, stesso luogo, stessa data al giorno, stesso "
    "numero; lingua, formato e unità di misura possono cambiare). Una risposta più vaga di quella attesa (solo l'anno "
    "invece del giorno, un intervallo) NON è corretta.\n\n"
    "Esempio 1 — attesa: \"24 luglio 1973\"; modello: \"July 24, 1973\" → {\"corretta\": true}\n"
    "Esempio 2 — attesa: \"Mitchell Baker\"; modello: \"Brendan Eich\" → {\"corretta\": false}\n\n"
    'Rispondi solo con JSON: {"corretta": true} oppure {"corretta": false}.')


def risposta_modello(modello: str, domanda: str) -> str:
    esito = qwen_client.chiedi(SISTEMA_RISPOSTA, domanda, None, "esecuzione", modello=modello,
                               parametri={"temperature": 0.7, "max_tokens": 120})
    return esito["risposta"].strip().splitlines()[0][:300] if esito["risposta"].strip() else ""


def uguale_normalizzata(attesa: str, data: str) -> bool:
    attesa_n, data_n = normalizza(attesa).strip(), normalizza(data)
    return bool(attesa_n) and re.search(rf"\b{re.escape(attesa_n)}\b", data_n) is not None


def giudica(domanda: str, attesa: str, data: str,
            chiedi: Callable[[str, str], str] | None = None) -> tuple[bool, str]:
    """(corretta, chi ha deciso): confronto normalizzato, poi il giudice (Qwen, o `chiedi(istruzioni, testo)`)."""
    if not data:
        return False, "vuota"
    if uguale_normalizzata(attesa, data):
        return True, "confronto"
    testo = f"DOMANDA: {domanda}\nRISPOSTA ATTESA: {attesa}\nRISPOSTA DEL MODELLO: {data}"
    for _ in range(2):
        esito = chiedi(SISTEMA_GIUDICE, testo) if chiedi else \
            qwen_client.chiedi(SISTEMA_GIUDICE, testo, None, "esecuzione", modello=QWEN,
                               parametri={"temperature": 0.0, "max_tokens": 40})["risposta"]
        trovato = re.search(r'"corretta"\s*:\s*(true|false)', esito)
        if trovato:
            return trovato.group(1) == "true", "giudice"
    return False, "giudice senza risposta valida"


def carica(percorso: Path) -> dict:
    return json.loads(percorso.read_text(encoding="utf-8")) if percorso.is_file() else {}


def salva(percorso: Path, dati: object) -> None:
    temporaneo = percorso.with_suffix(".tmp")
    temporaneo.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    temporaneo.replace(percorso)


def rivedi(voce: dict, decisione: dict) -> dict | None:
    """Applica la revisione a mano: None se scartata, tema corretto se spostata (la chiave resta quella originale)."""
    if "scarta" in decisione:
        return None
    if "tema" in decisione:
        return {**voce, "tema": decisione["tema"], "tema_originale": voce["tema"]}
    return voce


def chiave(voce: dict) -> str:
    return f"{voce.get('tema_originale', voce['tema'])}/{voce['seme']}"


def main() -> None:
    argomenti = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argomenti.add_argument("--candidate", default=str(CARTELLA / "domande_candidate.json"))
    argomenti.add_argument("--per-tema", type=int, default=10)
    argomenti.add_argument("--revisione", default=str(CARTELLA / "revisione.json"),
                           help="approvazione A2: domande scartate o spostate di tema dopo la lettura")
    a = argomenti.parse_args()
    revisione = carica(Path(a.revisione))
    candidate = [rivedi(v, revisione.get(chiave(v), {})) for v in json.loads(Path(a.candidate).read_text(encoding="utf-8"))
                 if not v.get("scartata") and v.get("controlli", {}).get("superati")]
    candidate = [v for v in candidate if v is not None]
    file_filtro = CARTELLA / "filtro.json"
    esiti = carica(file_filtro)
    da_fare = [v for v in candidate if chiave(v) not in esiti or "qwen" not in esiti[chiave(v)]]
    print(f"{len(candidate)} candidate valide, {len(da_fare)} da filtrare", flush=True)
    custode = gpu.custode()
    inizio = time.monotonic()

    # 1. Spark, tutte le domande (2 tentativi ciascuna)
    with custode.usa(SPARK, CONTESTO_SPARK, motivo="banco di prova: filtro, Spark"):
        for v in da_fare:
            e = esiti.setdefault(chiave(v), {})
            if "spark" in e:
                continue
            e["spark"] = [risposta_modello(SPARK, v["domanda"]) for _ in range(TENTATIVI_SPARK)]
            salva(file_filtro, esiti)
            print(f"[{time.monotonic() - inizio:5.0f} s] Spark  {chiave(v)}: {e['spark']}", flush=True)

    # 2. Qwen: la sua risposta e i giudizi su tutte le risposte (Spark comprese)
    with custode.usa(QWEN, CONTESTO_QWEN, motivo="banco di prova: filtro, Qwen"):
        for v in da_fare:
            e = esiti[chiave(v)]
            e["qwen"] = risposta_modello(QWEN, v["domanda"])
            e["giudizi"] = [dict(zip(("corretta", "da"), giudica(v["domanda"], v["risposta"], r), strict=True),
                                 modello=m, risposta=r)
                            for m, r in [*((SPARK, r) for r in e["spark"]), (QWEN, e["qwen"])]]
            e["risolta_a_memoria"] = any(g["corretta"] for g in e["giudizi"])
            salva(file_filtro, esiti)
            print(f"[{time.monotonic() - inizio:5.0f} s] Qwen   {chiave(v)}: {e['qwen']!r} → "
                  f"{'SCARTATA (risolta a memoria)' if e['risolta_a_memoria'] else 'tenuta'}", flush=True)

    # 3. Banco: le domande non risolte a memoria, al massimo --per-tema per tema, nell'ordine dei semi
    banco, per_tema = [], {}
    for v in candidate:
        e = esiti.get(chiave(v), {})
        if "risolta_a_memoria" not in e or e["risolta_a_memoria"] or per_tema.get(v["tema"], 0) >= a.per_tema:
            continue
        per_tema[v["tema"]] = per_tema.get(v["tema"], 0) + 1
        banco.append({**v, "filtro": {"spark": e["spark"], "qwen": e["qwen"], "giudizi": e["giudizi"]}})
    salva(CARTELLA / "domande.json", {"generato_il": datetime.now().astimezone().isoformat(timespec="seconds"),
                                      "modelli_del_filtro": {"spark": SPARK, "qwen": QWEN}, "domande": banco})
    scartate = sum(1 for e in esiti.values() if e.get("risolta_a_memoria"))
    print(f"Banco: {len(banco)} domande {per_tema}; scartate perché risolte a memoria: {scartate}", flush=True)


if __name__ == "__main__":
    configurazione.carica()
    main()
