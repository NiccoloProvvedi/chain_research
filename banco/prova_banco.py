"""Corsa del banco di prova (Fase G, Parte 11.3 del piano pensatore): le domande di banco_prova/domande.json fatte
alla ricerca vera, con la risposta confrontata con quella attesa.

- `fatto_veloce`: si confronta la riga «Risposta» del riquadro;
- `ricerca_approfondita`: Qwen estrae dal documento la risposta alla domanda (una riga), poi si confronta.
Il confronto è quello del filtro (banco_prova/filtra_domande.py): prima normalizzato, poi Qwen giudice con 2 esempi.

Registra per domanda: corretta, tempo, fonti e pagine lette, ricerche fatte, caratteri letti, costo di Claude.
Riprendibile (le domande già fatte si saltano); si ferma oltre il tetto di spesa di Claude (decisione T6: 10 $).
Risultati fuori da git: <staging>/banco/<nome>/esiti.json e riepilogo.md.

Uso: venv/bin/python test/prova_banco.py --modalita fatto_veloce [--per-tema 10] [--nome base-2026-10-14]
     (marker logici: llm, rete, claude — è lento e costa)
"""
import argparse
import json
import traceback
import re
import sys
import os
import signal
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

PROGETTO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROGETTO), str(PROGETTO / "ponte")]

import configurazione  # noqa: E402
import qwen_client  # noqa: E402
from banco_prova import filtra_domande  # noqa: E402
from navigatore.ricerca import Ricerca  # noqa: E402
from navigatore import ricerca  # noqa: E402
from orchestratore import campionamento, gpu  # noqa: E402
from orchestratore import config as config_pensatore  # noqa: E402
from orchestratore.pensatore import Pensatore, budget_per  # noqa: E402

MODALITA = ("fatto_veloce", "ricerca_approfondita")
ESTRAI = ("Estrai dal TESTO il valore che dà come risposta alla DOMANDA: una sola riga con un solo valore (nome, data o "
          "numero), copiato dal testo. Non controllare se è giusto né se la persona o il luogo sono quelli della domanda: "
          "estrai e basta. Scrivi solo NON TROVATA se il testo dice di non avere la risposta, oppure se dà come risposta "
          "due o più valori diversi senza sceglierne uno.")


def risposta_breve(testo: str) -> str:
    """La riga «Risposta» del riquadro del fatto veloce (o la prima riga non vuota)."""
    trovata = re.search(r"\*\*Risposta:?\*\*:?\s*(.+)", testo)
    if trovata:
        return trovata.group(1).strip()[:500]
    return next((r.strip() for r in testo.splitlines() if r.strip()), "")[:500]


# Chi estrae la risposta finale e fa da giudice: "qwen" (locale) o "claude" (modello di GIUDICE["modello"]).
GIUDICE = {"tipo": "qwen", "modello": "haiku"}


def chiedi_giudice(istruzioni: str, testo: str) -> str:
    """Testo libero dal giudice Claude (senza strumenti, registrato come punto "giudice")."""
    from orchestratore import claude_json
    try:
        return str(claude_json.chiedi(istruzioni, testo, None, "giudice", modello=GIUDICE["modello"])[0])
    except claude_json.ErroreClaude:
        return ""


def giudice() -> Callable[[str, str], str] | None:
    return chiedi_giudice if GIUDICE["tipo"] == "claude" else None


def estrai_risposta(domanda: str, documento: str, modello: str) -> str:
    richiesta = f"DOMANDA: {domanda}\n\nDOCUMENTO:\n{documento[:40000]}"
    if GIUDICE["tipo"] == "claude":
        esito = chiedi_giudice(ESTRAI, richiesta)
    else:
        esito = qwen_client.chiedi(ESTRAI, richiesta, None, "esecuzione", modello=modello,
                                   parametri={"temperature": 0.0, "max_tokens": 120})["risposta"]
    # La prima riga con un valore: niente premesse come «Dal testo fornito, il valore … è:» (16/10, Blender:
    # risposta giusta giudicata sbagliata perché il giudice aveva scritto la premessa su una riga a parte).
    righe = [r.strip().strip("*").strip() for r in esito.splitlines() if r.strip()]
    riga = next((r for r in righe if not r.endswith(":")), righe[0] if righe else "")
    # 18/10, Lago di Garda: il giudice ha scritto «NOT TROVATA» e «NOT TROVATA» è diventata la risposta finale.
    return "" if re.match(r"(NON|NOT)\s+(TROVATA|FOUND)", riga.upper()) else riga[:300]


def scegli(domande: list[dict], per_tema: int) -> list[dict]:
    scelte, conta = [], {}
    for d in domande:
        if conta.get(d["tema"], 0) < per_tema:
            conta[d["tema"]] = conta.get(d["tema"], 0) + 1
            scelte.append(d)
    return scelte


class LimiteSettimanale(RuntimeError):
    """Il limite settimanale dell'abbonamento: niente da aspettare per ore, la corsa si ferma."""


def una_ricerca(d: dict, modalita: str, scrittore: str, opzioni: dict | None = None) -> dict:
    """Una ricerca vera sulla domanda: risposta finale estratta e misure (senza il giudizio)."""
    inizio = time.monotonic()
    for attesa in (300, 600, 0):
        eventi: list[dict] = []
        # Tetto duro per domanda (16/10: una corsa ferma 30 minuti dentro una chiamata del browser, oltre il limite
        # della ricerca): l'allarme interrompe anche un'attesa senza tempo massimo.
        def scaduto(*_: object) -> None:
            raise TimeoutError("domanda oltre il tetto duro di tempo")
        precedente = signal.signal(signal.SIGALRM, scaduto)
        signal.alarm(int(os.environ.get("BANCO_TETTO_S", "2400")))
        try:
            esito = Ricerca(d["domanda"], opzioni={"modalita": modalita, **(opzioni or {})},
                            pubblica=eventi.append).esegui()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, precedente)
        # 16/10, 18:16-18:28: Claude rispondeva vuoto a ogni chiamata (0 token) e 16 domande sono finite in 9 s
        # «catena non preparata». Una ricerca senza Claude non misura niente: si aspetta e si rifà.
        decisioni = (esito.get("pensatore") or {}).get("decisioni", [])
        # 18/10, 10:52: «You've hit your weekly limit · resets Oct 22»: 39 domande fallite una dopo l'altra in pochi
        # secondi. Il limite settimanale ferma la corsa.
        if any("weekly limit" in str(x.get("motivo")) for x in decisioni):
            raise LimiteSettimanale("limite settimanale dell'abbonamento Claude raggiunto")
        limite = [d for d in decisioni
                  if "session limit" in str(d.get("motivo")) or "usage limit" in str(d.get("motivo"))]
        if limite:  # una domanda percorsa con Claude a metà non misura niente (16/10, 23:45-00:50)
            raise RuntimeError(f"limite di sessione di Claude durante la domanda ({len(limite)} decisioni perse)")
        locale = str((opzioni or {}).get("modello_claude") or "").startswith("lm:")  # costo sempre zero
        if locale or not ((esito.get("catena") or {}).get("motivo") == "catena non preparata"
                          and not float((esito.get("pensatore") or {}).get("speso_usd") or 0)):
            break
        if not attesa:
            raise RuntimeError("Claude non disponibile (catena non preparata, nessuna spesa)")
        print(f"  Claude non risponde: riprovo tra {attesa // 60} minuti", flush=True)
        time.sleep(attesa)
    testo = esito["resoconto"]
    data = risposta_breve(testo) if modalita == "fatto_veloce" else testo
    # Una sola risposta finale (13/10: «nato l'11 marzo 1958 (… le note dicono 10 ottobre 1958)» contava come
    # corretta con il confronto sul testo intero), poi lo stesso giudizio del filtro.
    finale = estrai_risposta(d["domanda"], data, scrittore)
    corta = re.sub(r"\[\d+\]", "", data).strip(" .*\n")
    if not finale and 0 < len(corta) <= 120 and not corta.lower().startswith("non trovat"):
        finale = corta  # 17/10, Lago di Garda: «Pieve [13].» e il giudice rispondeva NON TROVATA
    lette = [e for e in eventi if e.get("tipo") == "ricerca_fonte" and e.get("stato") == "letta"]
    piani = [e for e in eventi if e.get("tipo") == "ricerca_piano"]
    pensatore = esito.get("pensatore") or {}
    return {"data": data[:500], "finale": finale, "secondi": round(time.monotonic() - inizio),
            "fonti": len(esito.get("fonti", [])),
            "indirizzi": [f.get("url") or f.get("titolo") or "" for f in esito.get("fonti", [])
                          if isinstance(f, dict)][:10],
            "pagine_lette": len(lette), "caratteri_letti": sum(int(e.get("caratteri") or 0) for e in lette),
            "ricerche": sum(len(e.get("ricerche") or []) for e in piani),
            "query": esito.get("query_eseguite") or esito.get("ricerche") or [], "ridondanza": esito.get("ridondanza"),
            "costo_claude_usd": round(float(pensatore.get("speso_usd") or 0), 4),
            "chiamate_claude": pensatore.get("chiamate"), "motivo_fine": esito.get("motivo_fine"),
            "triple": esito.get("triple"), "concetti": esito.get("concetti"), "catena": esito.get("catena"),
            "esplorazione": [e for e in eventi if e.get("tipo") == "ricerca_esplorazione"],
            "percorso": esito.get("percorso"), "ram_libera_gb": round(gpu.memoria().get("ram_libera", 0) / 2**30, 1)}


def con_tentativi(d: dict, modalita: str, scrittore: str, k: int, opzioni: dict | None = None) -> dict:
    """k tentativi indipendenti (Spark a temperatura 0,7) e scelta con la regola della Parte 7.2."""
    cfg = config_pensatore.carica()
    tentativi = []
    ricerca.temperatura_tentativo["valore"] = cfg.campionamento.temperatura
    try:
        for _ in range(k):
            voce = una_ricerca(d, modalita, scrittore, opzioni)
            tentativi.append(campionamento.Tentativo(voce["finale"], voce["ricerche"] + voce["pagine_lette"],
                                                     voce["indirizzi"], voce))
    finally:
        ricerca.temperatura_tentativo["valore"] = None
    pensatore = Pensatore(f"p-banco-{d['seme'][:20]}", budget_per(modalita))

    def spareggio(lista: list) -> int | None:
        dati, da = pensatore.spareggio(d["domanda"], campionamento.per_il_pensatore(lista), lambda: {"scelta": -1})
        return dati["scelta"] if da == "claude" else None
    scelto, regola = campionamento.scegli(tentativi, spareggio)
    giusti = [filtra_domande.giudica(d["domanda"], d["risposta"], t.risposta, giudice())[0] for t in tentativi]
    voce = dict(tentativi[scelto].dati)
    voce.update(
        regola=regola, scelto=scelto, almeno_uno_giusto=any(giusti),
        tentativi=[{"finale": t.risposta, "chiamate": t.chiamate, "giusto": g} for t, g in zip(tentativi, giusti,
                                                                                               strict=True)],
        secondi=sum(t.dati["secondi"] for t in tentativi),
        costo_claude_usd=round(sum(t.dati["costo_claude_usd"] for t in tentativi) + pensatore.bilancio.speso, 4))
    return voce


def rigiudica(esiti: dict, modalita: str, modello: str) -> None:
    """Giudizio nuovo sulle risposte salvate (il vecchio resta in `corretta_prima` per il confronto)."""
    for chiave, e in esiti.items():
        if e.get("modalita") != modalita or "corretta" not in e:
            continue
        documento = Path(e.get("percorso") or "")
        testo = documento.read_text(encoding="utf-8") if modalita != "fatto_veloce" and documento.is_file() \
            else e["data"]
        finale = estrai_risposta(e["domanda"], testo, modello)
        corretta, deciso = filtra_domande.giudica(e["domanda"], e["attesa"], finale, giudice())
        e.setdefault("corretta_prima", e["corretta"])
        e.update(finale=finale, corretta=corretta, deciso_da=deciso)
        if corretta != e["corretta_prima"]:
            print(f"cambia {chiave}: {e['corretta_prima']} → {corretta} («{finale}»)", flush=True)


def riepilogo(esiti: dict, modalita: str) -> dict:
    fatte = [e for e in esiti.values() if e.get("modalita") == modalita and "corretta" in e]
    if not fatte:
        return {"domande": 0}
    per_tema: dict[str, list[bool]] = {}
    for e in fatte:
        per_tema.setdefault(e["tema"], []).append(e["corretta"])
    n = len(fatte)
    return {"domande": n, "precisione": round(sum(e["corretta"] for e in fatte) / n, 3),
            "per_tema": {t: f"{sum(v)}/{len(v)}" for t, v in sorted(per_tema.items())},
            "minuti_medi": round(sum(e["secondi"] for e in fatte) / n / 60, 1),
            "pagine_medie": round(sum(e["pagine_lette"] for e in fatte) / n, 1),
            "ricerche_medie": round(sum(e["ricerche"] for e in fatte) / n, 1),
            "caratteri_letti_medi": round(sum(e["caratteri_letti"] for e in fatte) / n),
            "costo_claude_usd": round(sum(e["costo_claude_usd"] for e in fatte), 4),
            "ridondanza_media": round(sum(r) / len(r), 4) if (r := [e["ridondanza"] for e in fatte
                                                                    if e.get("ridondanza") is not None]) else None,
            "errori": sum(1 for e in esiti.values() if e.get("modalita") == modalita and "errore" in e)}


def registra(arg: argparse.Namespace, etichetta: str, sommario: dict, chiavi: set[str], esiti: dict) -> None:
    """Una riga in banco_prova/REGISTRO.md: data, cosa si prova, versione del codice, esame, risultato."""
    import subprocess
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=PROGETTO).stdout.strip() or "?"
    fatte = [e for k, e in esiti.items() if k.split(":", 1)[0] == etichetta and k.split(":", 1)[1] in chiavi
             and "corretta" in e]
    giuste = sum(e["corretta"] for e in fatte)
    esame = "rapido (12)" if arg.rapido else f"{len(chiavi)} domande"
    registro = PROGETTO / "banco_prova" / "REGISTRO.md"
    if not registro.is_file():
        registro.write_text("# Registro delle prove del banco\n\n| Data | Cosa si prova | Codice | Esame | Giuste | "
                            "Minuti medi | Claude $ | Errori |\n|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
    riga = (f"| {datetime.now():%Y-%m-%d %H:%M} | {arg.nota or etichetta} | {commit} | {esame} | "
            f"{giuste}/{len(fatte)} | {sommario.get('minuti_medi', '-')} | "
            f"{round(sum(e.get('costo_claude_usd', 0) for e in fatte), 3)} | "
            f"{sum(1 for k, e in esiti.items() if k.split(':', 1)[0] == etichetta and 'errore' in e)} |\n")
    with registro.open("a", encoding="utf-8") as f:
        f.write(riga)


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--modalita", choices=MODALITA, required=True)
    a.add_argument("--per-tema", type=int, default=10)
    a.add_argument("--budget", type=float, default=10.0, help="dollari massimi di Claude per tutta la corsa")
    a.add_argument("--nome", default=f"base-{datetime.now():%Y-%m-%d}")
    a.add_argument("--domande", default=str(PROGETTO / "banco_prova" / "domande.json"))
    a.add_argument("--tentativi", type=int, default=1,
                   help="tentativi indipendenti per domanda, scelti con la regola della Parte 7.2 (solo fatto veloce)")
    a.add_argument("--opzioni", default="{}", help='opzioni della ricerca in JSON, es. {"triple": true}')
    a.add_argument("--etichetta", default="", help="nome degli esiti (predefinito: modalità e tentativi)")
    a.add_argument("--semi", default="", help="solo queste domande: tema/seme separati da virgola")
    a.add_argument("--giudice", choices=["qwen", "claude"], default="claude",
                   help="chi estrae la risposta finale e giudica (14/10: Claude Haiku, niente modelli locali)")
    a.add_argument("--rapido", action="store_true", help="esame rapido: le 12 domande di banco_prova/rapido.json")
    a.add_argument("--nota", default="", help="cosa si prova, in una frase (va nel registro delle prove)")
    a.add_argument("--rigiudica", action="store_true",
                   help="non rifà le ricerche: ricalcola il giudizio dalle risposte salvate (estrazione + confronto)")
    arg = a.parse_args()
    GIUDICE["tipo"] = arg.giudice
    config = configurazione.carica()
    cartella = configurazione.percorso(config, "staging") / "banco" / arg.nome
    cartella.mkdir(parents=True, exist_ok=True)
    file_esiti = cartella / "esiti.json"
    esiti = json.loads(file_esiti.read_text(encoding="utf-8")) if file_esiti.is_file() else {}
    domande = scegli(json.loads(Path(arg.domande).read_text(encoding="utf-8"))["domande"], arg.per_tema)
    if arg.rapido:
        arg.semi = ",".join(json.loads((PROGETTO / "banco_prova" / "rapido.json").read_text(encoding="utf-8"))["domande"])
    if arg.semi:
        volute = {s.strip() for s in arg.semi.split(",") if s.strip()}
        domande = [d for d in domande if f"{d['tema']}/{d['seme']}" in volute]
    scrittore = config["navigatore"].get("ricerca", {}).get("modello_scrittore", "qwen3.8-27b")
    print(f"{len(domande)} domande in {arg.modalita}; risultati in {cartella}", flush=True)

    if arg.rigiudica:
        rigiudica(esiti, arg.modalita, scrittore)
        file_esiti.write_text(json.dumps(esiti, ensure_ascii=False, indent=1), encoding="utf-8")
        domande = []
    etichetta = arg.etichetta or (arg.modalita if arg.tentativi == 1 else f"{arg.modalita}_k{arg.tentativi}")
    opzioni = json.loads(arg.opzioni)
    for d in domande:
        chiave = f"{etichetta}:{d['tema']}/{d['seme']}"
        if "corretta" in esiti.get(chiave, {}):
            continue
        speso = sum(e.get("costo_claude_usd", 0) for e in esiti.values())
        if speso >= arg.budget:
            print(f"Tetto di spesa raggiunto ({speso:.2f} $ su {arg.budget} $): mi fermo.", flush=True)
            break
        try:
            if arg.tentativi == 1:
                voce = una_ricerca(d, arg.modalita, scrittore, opzioni)
            else:
                voce = con_tentativi(d, arg.modalita, scrittore, arg.tentativi, opzioni)
            corretta, deciso = filtra_domande.giudica(d["domanda"], d["risposta"], voce["finale"], giudice())
        except LimiteSettimanale as errore:
            print(f"FERMO {chiave}: {errore}", flush=True)
            break
        except Exception as errore:  # noqa: BLE001 - una domanda fallita non ferma la corsa
            esiti[chiave] = {"modalita": etichetta, "tema": d["tema"], "seme": d["seme"],
                             "errore": f"{type(errore).__name__}: {errore}"[:500]}
            file_esiti.write_text(json.dumps(esiti, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"ERRORE {chiave}: {errore!r}"[:300], flush=True)
            traceback.print_exc()
            continue
        esiti[chiave] = e = {"modalita": etichetta, "tema": d["tema"], "seme": d["seme"], "domanda": d["domanda"],
                             "attesa": d["risposta"], **voce, "corretta": corretta, "deciso_da": deciso}
        file_esiti.write_text(json.dumps(esiti, ensure_ascii=False, indent=1), encoding="utf-8")
        regola = f", regola {e['regola']}, almeno uno giusto: {e['almeno_uno_giusto']}" if "regola" in e else ""
        print(f"{'✓' if corretta else '✗'} {chiave}: attesa «{d['risposta']}», finale «{e['finale'][:80]}» "
              f"({deciso}, {e['secondi']} s, {e['pagine_lette']} pagine, {e['costo_claude_usd']} ${regola})",
              flush=True)

    sommario = riepilogo(esiti, etichetta)
    (cartella / f"riepilogo_{etichetta}.json").write_text(json.dumps(sommario, ensure_ascii=False, indent=1),
                                                            encoding="utf-8")
    print("RIEPILOGO", json.dumps(sommario, ensure_ascii=False), flush=True)
    if domande:
        registra(arg, etichetta, sommario, {f"{d['tema']}/{d['seme']}" for d in domande}, esiti)
    if any((e.get("concetti") or {}).get("file") for k, e in esiti.items() if k.split(":", 1)[0] == etichetta):
        from grafi_banco import genera  # pagine dei grafi della corsa (ricerca a grafo)
        genera(arg.nome, etichetta)


if __name__ == "__main__":
    main()
