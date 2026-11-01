"""Grafo dei concetti di una ricerca (paradigma del 14/10, piano "ricerca a grafo").

Chi legge (Claude) dà per ogni blocco di pagina i concetti principali come tag, ognuno con la frase del blocco
da cui viene. Qui:
- ogni blocco letto diventa un **segmento salvato** (testo + indirizzo citabile): sono le "pagine salvate" da cui
  Claude scriverà;
- ogni concetto la cui frase compare davvero nel blocco diventa un **nodo** (o si aggiunge a quello con lo stesso nome,
  regola dei numeri compresa), con una **menzione** (concetto, segmento, frase): così ogni nodo sa dove è spiegato;
- i concetti nuovi si contano, per dare a Claude i legami da creare a gruppi.
- i **fatti** (concetto, attributo, valore: date, numeri, luoghi) con la frase che li prova: sono triple con
  `tipo_oggetto = 'valore'`, accettate solo se la frase è nel blocco e contiene i numeri del valore (diagnosi del 14/10:
  senza fatti la mappa mostrava "Alexandre Oliva" ma non la sua data di nascita);
- ogni legame ha un **perché** breve in italiano; quelli di Claude senza perché non entrano.
I legami tra concetti li propongono chi legge (dal testo) e Claude (origine `claude`). Più thread leggono insieme: ogni
scrittura passa da un blocco.
"""
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .grafo import Grafo
from .triple import citazione_presente

MAX_CONCETTI_PER_BLOCCO = 12
MAX_LEGAMI_PER_BLOCCO = 10
MAX_FATTI_PER_BLOCCO = 10
MAX_NOME = 120
_NUMERI = re.compile(r"\d+")


def numeri(testo: str) -> set[int]:
    return {int(n) for n in _NUMERI.findall(testo or "")}


def chiave_valore(valore: str) -> str:
    """Stesso valore scritto in lingue diverse ("24 luglio 1973", "24 de julho de 1973"): uguali se hanno gli stessi
    numeri; senza numeri conta il testo normalizzato."""
    cifre = sorted(numeri(valore))
    return " ".join(map(str, cifre)) if cifre else " ".join(str(valore).lower().split())


_ELENCO = re.compile(r",|;|\s+e\s+|\s+ed\s+|\s+and\s+|\s+et\s+|\s+y\s+|\s+und\s+")
# Nome proprio: da 1 a 5 parole con l'iniziale maiuscola, con particelle (di, de, da, van, von, del, della…) in mezzo.
_NOME_PROPRIO = re.compile(r"[A-ZÀ-Ý][\w'’.-]+(?:\s+(?:(?:di|de|da|del|della|dei|degli|van|von|der|le|la|du|y)\s+)?"
                           r"[A-ZÀ-Ý][\w'’.-]+){0,4}")


def _cognome(nome: str) -> str:
    parole = nome.lower().split()
    return parole[-1] if parole and len(parole[-1]) >= 3 else ""


def _pulisci(testo: object, n: int) -> str:
    return " ".join(str(testo or "").split())[:n]


class Concetti:
    def __init__(self, percorso: Path | str = ":memory:") -> None:
        self.grafo = Grafo(percorso)
        self.grafo.db.execute("CREATE TABLE IF NOT EXISTS irrilevante (entita_id INTEGER PRIMARY KEY)")
        self.grafo.db.execute("CREATE TABLE IF NOT EXISTS stato (chiave TEXT PRIMARY KEY, valore TEXT)")
        self._serratura = threading.Lock()
        self.nuovi: list[int] = []  # concetti creati dall'ultimo consolidamento di Claude
        self.validi = 0
        self.scartati = 0
        self.legami_dal_testo = 0
        self.legami_scartati = 0
        self.fatti_validi = 0
        self.fatti_scartati = 0
        self.legami_da_elenchi = 0

    def chiudi(self) -> None:
        self.grafo.chiudi()

    def registra(self, url: str, titolo: str, numero_blocco: int, testo: str, concetti: list[dict],
                 legami: list[dict] | None = None, fatti: list[dict] | None = None) -> int:
        """Salva il blocco, i suoi concetti verificati, i legami e i fatti dal testo (anche questi con la frase
        verificata). Restituisce quanti concetti sono stati accettati."""
        accettati = 0
        with self._serratura:
            fonte = self.grafo.fonte(url, dominio=urlsplit(url).hostname or "")
            segmento = self.grafo.segmento(fonte, numero_blocco, testo, url)
            for c in concetti[:MAX_CONCETTI_PER_BLOCCO]:
                nome = str(c.get("nome") or "").strip()[:MAX_NOME]
                frase = str(c.get("frase") or "").strip()
                if not nome or not citazione_presente(frase, testo):
                    self.scartati += 1
                    continue
                prima = self.grafo.trova(nome)
                entita_id = self.grafo.entita(nome)
                if prima is None:
                    self.nuovi.append(entita_id)
                traduzione = " ".join(str(c.get("traduzione") or "").split())[:400] or None
                self.grafo.db.execute("INSERT OR IGNORE INTO menzione (entita_id, segmento_id, frase, traduzione) "
                                      "VALUES (?, ?, ?, ?)", (entita_id, segmento, frase[:300], traduzione))
                accettati += 1
            self.validi += accettati
            accettati_legami = [x for x in (self._legame_dal_testo(legame, testo, segmento)
                                            for legame in (legami or [])[:MAX_LEGAMI_PER_BLOCCO]) if x]
            self._completa_elenchi(accettati_legami, segmento)
            for fatto in (fatti or [])[:MAX_FATTI_PER_BLOCCO]:
                self._fatto(fatto, testo, segmento)
            self.grafo.db.commit()
        return accettati

    def _legame_dal_testo(self, legame: dict, testo: str, segmento: int) -> tuple | None:
        """Restituisce (da, relazione, a, frase, nome di da, nome di a) se il legame è entrato (o c'era già)."""
        da, a = self.grafo.trova(str(legame.get("da") or "")), self.grafo.trova(str(legame.get("a") or ""))
        relazione = " ".join(str(legame.get("relazione") or "").split())[:80]
        frase = str(legame.get("frase") or "").strip()
        if da is None or a is None or da == a or not relazione or not citazione_presente(frase, testo):
            self.legami_scartati += 1
            return None
        risultato = (da, relazione, a, frase, str(legame.get("da")), str(legame.get("a")))
        gia = self.grafo.db.execute(
            "SELECT 1 FROM tripla WHERE soggetto_id = ? AND oggetto_id = ? AND relazione = ? AND segmento_id = ?",
            (da, a, relazione, segmento)).fetchone()
        if gia:
            return risultato
        traduzione = " ".join(str(legame.get("traduzione") or "").split())[:400] or None
        self.grafo.db.execute(
            "INSERT INTO tripla (soggetto_id, relazione, oggetto_id, tipo_oggetto, forza, origine, citazione, "
            "traduzione, perche, segmento_id) VALUES (?, ?, ?, 'entita', 'fatto', 'estratta', ?, ?, ?, ?)",
            (da, relazione, a, frase[:300], traduzione, _pulisci(legame.get("perche"), 200) or None, segmento))
        self.legami_dal_testo += 1
        return risultato

    def _completa_elenchi(self, legami: list[tuple], segmento: int) -> None:
        """Rete di sicurezza per gli elenchi (diagnosi del 15/10, Galileo: "tra i suoi discepoli figurano Riccioli,
        Bettini, Zucchi e Cabeo" → legami solo per Riccioli e Cabeo). Se due o più legami con la stessa relazione e
        la stessa frase puntano allo stesso concetto, i nomi propri dell'elenco che stanno TRA quelli già legati
        diventano concetti con lo stesso legame (origine "estratta", la frase è la stessa già verificata)."""
        gruppi: dict[tuple, list[tuple[str, int]]] = {}
        for da, relazione, a, frase, nome_da, nome_a in legami:
            gruppi.setdefault((relazione, frase, "a", a), []).append((nome_da, da))
            gruppi.setdefault((relazione, frase, "da", da), []).append((nome_a, a))
        for (relazione, frase, lato, fisso), noti in gruppi.items():
            if len(noti) < 2:
                continue
            pezzi = [x.strip(" .;:()\"'«»") for x in _ELENCO.split(frase)]
            indici = [i for i, pezzo in enumerate(pezzi)
                      if any(_cognome(nome) and _cognome(nome) in pezzo.lower() for nome, _ in noti)]
            if len(indici) < 2:
                continue
            for pezzo in pezzi[min(indici) + 1:max(indici)]:
                if pezzi.index(pezzo) in indici or not _NOME_PROPRIO.fullmatch(pezzo):
                    continue
                nuovo = self.grafo.trova(pezzo)
                if nuovo is None:
                    nuovo = self.grafo.entita(pezzo)
                    self.nuovi.append(nuovo)
                if nuovo == fisso:
                    continue
                self.grafo.db.execute("INSERT OR IGNORE INTO menzione (entita_id, segmento_id, frase) VALUES (?, ?, ?)",
                                      (nuovo, segmento, frase[:300]))
                da, a = (nuovo, fisso) if lato == "a" else (fisso, nuovo)
                gia = self.grafo.db.execute(
                    "SELECT 1 FROM tripla WHERE soggetto_id = ? AND oggetto_id = ? AND relazione = ? AND "
                    "segmento_id = ?", (da, a, relazione, segmento)).fetchone()
                if gia:
                    continue
                self.grafo.db.execute(
                    "INSERT INTO tripla (soggetto_id, relazione, oggetto_id, tipo_oggetto, forza, origine, citazione, "
                    "perche, segmento_id) VALUES (?, ?, ?, 'entita', 'fatto', 'estratta', ?, ?, ?)",
                    (da, relazione, a, frase[:300], "nello stesso elenco della frase (completato dal programma)",
                     segmento))
                self.legami_dal_testo += 1
                self.legami_da_elenchi += 1

    def _fatto(self, fatto: dict, testo: str, segmento: int) -> None:
        """Un fatto vale se la frase è nel blocco e contiene tutti i numeri del valore (niente date inventate). Se il
        concetto non è ancora nella mappa, nasce qui con la frase del fatto come menzione."""
        nome = _pulisci(fatto.get("concetto"), MAX_NOME)
        attributo = _pulisci(fatto.get("attributo"), 60)
        valore = _pulisci(fatto.get("valore"), 150)
        frase = str(fatto.get("frase") or "").strip()
        if not nome or not attributo or not valore or not citazione_presente(frase, testo) \
                or not numeri(valore) <= numeri(frase):
            self.fatti_scartati += 1
            return
        traduzione = _pulisci(fatto.get("traduzione"), 400) or None
        entita_id = self.grafo.trova(nome)
        if entita_id is None:
            entita_id = self.grafo.entita(nome)
            self.nuovi.append(entita_id)
            self.grafo.db.execute("INSERT OR IGNORE INTO menzione (entita_id, segmento_id, frase, traduzione) "
                                  "VALUES (?, ?, ?, ?)", (entita_id, segmento, frase[:300], traduzione))
        gia = self.grafo.db.execute(
            "SELECT 1 FROM tripla WHERE soggetto_id = ? AND relazione = ? AND oggetto_valore = ? AND segmento_id = ?",
            (entita_id, attributo, valore, segmento)).fetchone()
        if gia:
            return
        self.grafo.db.execute(
            "INSERT INTO tripla (soggetto_id, relazione, oggetto_valore, tipo_oggetto, forza, origine, citazione, "
            "traduzione, segmento_id) VALUES (?, ?, ?, 'valore', 'fatto', 'estratta', ?, ?, ?)",
            (entita_id, attributo, valore, frase[:300], traduzione, segmento))
        self.fatti_validi += 1

    def prendi_nuovi(self) -> list[int]:
        """I concetti nuovi da dare a Claude, e si riparte da zero."""
        with self._serratura:
            nuovi, self.nuovi = self.nuovi, []
        return nuovi

    def elenco(self, ids: list[int] | None = None, massimo: int = 60) -> list[dict]:
        """Concetti con il numero di menzioni e di fonti e una frase d'esempio (i più citati prima)."""
        filtro = f"WHERE e.id IN ({','.join('?' * len(ids))})" if ids else ""
        righe = self.grafo.db.execute(
            "SELECT e.id, e.nome, COUNT(m.segmento_id) AS menzioni, COUNT(DISTINCT s.fonte_id) AS fonti, "
            "MIN(m.frase) AS frase, MAX(m.traduzione) AS traduzione FROM entita e JOIN menzione m ON m.entita_id = e.id "
            f"JOIN segmento s ON s.id = m.segmento_id {filtro} GROUP BY e.id ORDER BY menzioni DESC, e.id LIMIT ?",
            [*(ids or []), massimo])
        return [dict(r) for r in righe]

    def dove(self, entita_id: int) -> list[dict]:
        """Dove compare un concetto: pagina, blocco, frase."""
        righe = self.grafo.db.execute(
            "SELECT f.url, s.numero, s.id AS segmento, m.frase, m.traduzione FROM menzione m "
            "JOIN segmento s ON s.id = m.segmento_id "
            "JOIN fonte f ON f.id = s.fonte_id WHERE m.entita_id = ? ORDER BY s.id", (entita_id,))
        return [dict(r) for r in righe]

    def segmenti(self, entita_ids: list[int]) -> list[dict]:
        """I blocchi salvati che spiegano questi concetti (per la scrittura), senza doppioni."""
        if not entita_ids:
            return []
        righe = self.grafo.db.execute(
            "SELECT DISTINCT s.id, f.url, s.numero, s.testo FROM menzione m JOIN segmento s ON s.id = m.segmento_id "
            f"JOIN fonte f ON f.id = s.fonte_id WHERE m.entita_id IN ({','.join('?' * len(entita_ids))}) ORDER BY s.id",
            entita_ids)
        return [dict(r) for r in righe]

    def legami(self) -> list[dict]:
        """Tutti i legami tra concetti: dal testo (origine "estratta", con frase verificata) e dedotti da Claude."""
        return [t for t in self.grafo.triple() if t["origine"] in ("claude", "estratta") and t["tipo_oggetto"] == "entita"]

    def fatti(self) -> list[dict]:
        """I fatti (concetto, attributo, valore) con frase, traduzione, blocco e pagina."""
        righe = self.grafo.db.execute(
            "SELECT t.id, t.soggetto_id AS concetto_id, e.nome AS concetto, t.relazione AS attributo, "
            "t.oggetto_valore AS valore, t.citazione AS frase, t.traduzione, t.segmento_id, s.fonte_id, f.url "
            "FROM tripla t JOIN entita e ON e.id = t.soggetto_id JOIN segmento s ON s.id = t.segmento_id "
            "JOIN fonte f ON f.id = s.fonte_id WHERE t.valida = 1 AND t.tipo_oggetto = 'valore' ORDER BY t.id")
        return [dict(r) for r in righe]

    def fatti_raggruppati(self) -> list[dict]:
        """Lo stesso fatto letto in più pagine (anche in lingue diverse) conta una volta, con il numero di fonti."""
        gruppi: dict[tuple, dict] = {}
        for f in self.fatti():
            chiave = (f["concetto_id"], f["attributo"].lower(), chiave_valore(f["valore"]))
            g = gruppi.setdefault(chiave, {**f, "fonti": set(), "prove": []})
            g["fonti"].add(f["fonte_id"])
            g["prove"].append(f)
        return sorted(({**g, "fonti": len(g["fonti"])} for g in gruppi.values()),
                      key=lambda g: (-g["fonti"], g["id"]))

    def salva_stato(self, chiave: str, valore: object) -> None:
        """Vincoli della domanda e risposte candidate, salvati con il grafo (per la pagina del grafo)."""
        with self._serratura:
            self.grafo.db.execute("INSERT OR REPLACE INTO stato (chiave, valore) VALUES (?, ?)",
                                  (chiave, json.dumps(valore, ensure_ascii=False)))
            self.grafo.db.commit()

    def stato(self, chiave: str, predefinito: object = None) -> object:
        riga = self.grafo.db.execute("SELECT valore FROM stato WHERE chiave = ?", (chiave,)).fetchone()
        return json.loads(riga[0]) if riga else predefinito

    # --- consolidamento di Claude ----------------------------------------------------------------------------

    def isolati(self) -> list[int]:
        """Concetti senza legami e non giudicati inutili da Claude."""
        return [r["id"] for r in self.grafo.db.execute(
            "SELECT e.id FROM entita e WHERE e.id IN (SELECT entita_id FROM menzione) "
            "AND e.id NOT IN (SELECT entita_id FROM irrilevante) AND NOT EXISTS (SELECT 1 FROM tripla t WHERE "
            "t.valida = 1 AND t.origine IN ('claude', 'estratta') AND t.tipo_oggetto = 'entita' "
            "AND (t.soggetto_id = e.id OR t.oggetto_id = e.id)) "
            "ORDER BY e.id")]

    def irrilevanti(self) -> list[int]:
        return [r[0] for r in self.grafo.db.execute("SELECT entita_id FROM irrilevante")]

    def doppioni_possibili(self, massimo: int = 25) -> list[tuple[int, int]]:
        """Coppie di concetti forse uguali, per Claude: un nome contenuto nell'altro ("Riccioli" e "Giovanni Battista
        Riccioli") o quasi uguali ("Ricciolo" e "Riccioli"); mai con numeri diversi."""
        from difflib import SequenceMatcher

        from .entita import cifre, normalizza
        nomi = [(c["id"], c["nome"], normalizza(c["nome"])) for c in self.elenco(massimo=400)]
        coppie = []
        for i, (id1, nome1, n1) in enumerate(nomi):
            for id2, nome2, n2 in nomi[i + 1:]:
                if cifre(nome1) != cifre(nome2):
                    continue
                corto, lungo = sorted((n1, n2), key=len)
                contenuto = len(corto) >= 4 and set(corto.split()) <= set(lungo.split())
                simile = SequenceMatcher(None, n1, n2).ratio()
                if contenuto or simile >= 0.85:
                    coppie.append((max(simile, 0.9 if contenuto else 0), id1, id2))
        return [(a, b) for _, a, b in sorted(coppie, reverse=True)[:massimo]]

    def per_claude(self, domanda: str, sotto_domande: list[str], ricerche: list[str], nuovi: list[int],
                   vincoli: list[str] | None = None, candidate: list[dict] | None = None) -> dict:
        """Quello che Claude vede: TUTTA la mappa in forma compatta (numero e nome, fino a 400 concetti), i fatti
        trovati (con quante fonti), i concetti nuovi e quelli isolati con una frase (già ripulite dalla lettura), i
        legami con il loro perché, i doppioni possibili, i vincoli della domanda e le risposte candidate."""
        da_campo = lambda testo, n: " ".join(str(testo).split())[:n]  # noqa: E731
        nomi = {c["id"]: c["nome"] for c in self.elenco(massimo=100000)}
        con_frase = lambda ids: [{"n": c["id"], "nome": da_campo(c["nome"], 120), "frase": da_campo(c["frase"], 200),  # noqa: E731
                                  "fonti": c["fonti"]} for c in self.elenco(ids, massimo=80)] if ids else []
        legami = [f"[{t['soggetto_id']}] {da_campo(t['soggetto'], 60)} → {da_campo(t['relazione'], 60)} → "
                  f"[{t['oggetto_id']}] {da_campo(t['oggetto'], 60)}"
                  + (" (dal testo)" if t["origine"] == "estratta" else "")
                  + (f" — perché: {da_campo(t['perche'], 100)}" if t.get("perche") else "")
                  for t in self.legami()[-200:]]
        fatti = [f"[{g['concetto_id']}] {da_campo(g['concetto'], 60)} — {da_campo(g['attributo'], 40)}: "
                 f"{da_campo(g['valore'], 80)} ({g['fonti']} {'fonte' if g['fonti'] == 1 else 'fonti'})"
                 for g in self.fatti_raggruppati()[:150]]
        return {
            "domanda": da_campo(domanda, 600), "sotto_domande": [da_campo(s, 200) for s in sotto_domande[:6]],
            "mappa": [f"[{c['id']}] {da_campo(c['nome'], 80)} ({c['menzioni']})" for c in self.elenco(massimo=400)],
            "concetti_nuovi": con_frase(nuovi),
            "isolati": con_frase([i for i in self.isolati() if i not in nuovi][:80]),
            "doppioni_possibili": [f"[{a}] {da_campo(nomi.get(a, ''), 80)} = [{b}] {da_campo(nomi.get(b, ''), 80)} ?"
                                   for a, b in self.doppioni_possibili()],
            "legami": legami,
            "fatti": fatti,
            "vincoli": [da_campo(v, 150) for v in (vincoli or [])[:6]],
            "risposte_candidate": [f"{da_campo(c.get('valore'), 150)} (concetti {c.get('concetti', [])}; "
                                   f"{c.get('stato', '')}"
                                   + (f": {da_campo(c.get('vincolo_in_dubbio'), 150)}" if c.get("vincolo_in_dubbio")
                                      else "") + ")"
                                   for c in (candidate or [])[:5]],
            "ricerche_fatte": [da_campo(r, 120) for r in ricerche[-15:]],
        }

    def applica(self, decisione: dict) -> dict:
        """Applica legami e unioni decisi da Claude; i numeri che non esistono si ignorano e si contano."""
        conteggi = {"legami": 0, "unioni": 0, "ignorati": 0, "irrilevanti": 0}
        with self._serratura:
            esistenti = {r["id"]: r["nome"] for r in self.grafo.db.execute("SELECT id, nome FROM entita")}
            for u in decisione.get("unisci", []):
                tieni, togli = u.get("tieni"), u.get("togli")
                if tieni == togli or tieni not in esistenti or togli not in esistenti or \
                        not self._unisci(tieni, togli, esistenti):
                    conteggi["ignorati"] += 1
                    continue
                conteggi["unioni"] += 1
            for legame in decisione.get("legami", []):
                da, a = legame.get("da"), legame.get("a")
                perche = _pulisci(legame.get("perche"), 200)
                if da == a or da not in esistenti or a not in esistenti or len(perche) < 5:
                    conteggi["ignorati"] += 1  # un legame di Claude senza perché non entra (diagnosi del 14/10)
                    continue
                self.grafo.db.execute(
                    "INSERT INTO tripla (soggetto_id, relazione, oggetto_id, tipo_oggetto, forza, origine, perche) "
                    "VALUES (?, ?, ?, 'entita', 'interpretazione', 'claude', ?)",
                    (da, " ".join(str(legame.get("relazione", "")).split())[:80], a, perche))
                conteggi["legami"] += 1
            for entita_id in decisione.get("irrilevanti", []):
                if entita_id in esistenti:
                    self.grafo.db.execute("INSERT OR IGNORE INTO irrilevante (entita_id) VALUES (?)", (entita_id,))
                    conteggi["irrilevanti"] += 1
            self.grafo.db.commit()
        return conteggi

    def _unisci(self, tieni: int, togli: int, esistenti: dict[int, str]) -> bool:
        """Sposta menzioni e legami di `togli` su `tieni`; il nome tolto resta come alias. Mai nomi con numeri
        diversi (regola dei numeri)."""
        from .entita import cifre
        if cifre(esistenti[tieni]) != cifre(esistenti[togli]):
            return False
        db = self.grafo.db
        db.execute("UPDATE OR IGNORE menzione SET entita_id = ? WHERE entita_id = ?", (tieni, togli))
        db.execute("DELETE FROM menzione WHERE entita_id = ?", (togli,))
        db.execute("UPDATE tripla SET soggetto_id = ? WHERE soggetto_id = ?", (tieni, togli))
        db.execute("UPDATE tripla SET oggetto_id = ? WHERE oggetto_id = ?", (tieni, togli))
        db.execute("UPDATE alias SET entita_id = ? WHERE entita_id = ?", (tieni, togli))
        db.execute("DELETE FROM irrilevante WHERE entita_id = ?", (togli,))
        nome_norm = db.execute("SELECT nome_norm FROM entita WHERE id = ?", (togli,)).fetchone()["nome_norm"]
        db.execute("DELETE FROM entita WHERE id = ?", (togli,))
        db.execute("INSERT OR REPLACE INTO alias (nome_norm, entita_id) VALUES (?, ?)", (nome_norm, tieni))
        esistenti.pop(togli, None)
        self.nuovi = [tieni if n == togli else n for n in self.nuovi]
        return True
