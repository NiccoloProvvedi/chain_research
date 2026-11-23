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

from .graph import Graph
from .triples import present_quote

MAX_CONCEPTS_PER_BLOCK = 12
MAX_TIES_PER_BLOCK = 10
MAX_FACTS_PER_BLOCK = 10
MAX_NAME = 120
_NUMBERS = re.compile(r"\d+")


def numbers(text: str) -> set[int]:
    return {int(n) for n in _NUMBERS.findall(text or "")}


def value_key(value: str) -> str:
    """Stesso valore scritto in lingue diverse ("24 luglio 1973", "24 de julho de 1973"): uguali se hanno gli stessi
    numeri; senza numeri conta il testo normalizzato."""
    digits = sorted(numbers(value))
    return " ".join(map(str, digits)) if digits else " ".join(str(value).lower().split())


_LIST = re.compile(r",|;|\s+e\s+|\s+ed\s+|\s+and\s+|\s+et\s+|\s+y\s+|\s+und\s+")
# Nome proprio: da 1 a 5 parole con l'iniziale maiuscola, con particelle (di, de, da, van, von, del, della…) in mezzo.
_PROPER_NAME = re.compile(r"[A-ZÀ-Ý][\w'’.-]+(?:\s+(?:(?:di|de|da|del|della|dei|degli|van|von|der|le|la|du|y)\s+)?"
                           r"[A-ZÀ-Ý][\w'’.-]+){0,4}")


def _surname(name: str) -> str:
    words = name.lower().split()
    return words[-1] if words and len(words[-1]) >= 3 else ""


def _clean(text: object, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


class Concepts:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.graph = Graph(path)
        self.graph.db.execute("CREATE TABLE IF NOT EXISTS irrelevant (entity_id INTEGER PRIMARY KEY)")
        self.graph.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        self._mutex = threading.Lock()
        self.new_items: list[int] = []  # concetti creati dall'ultimo consolidamento di Claude
        self.valid = 0
        self.discarded = 0
        self.ties_from_text = 0
        self.discarded_ties = 0
        self.valid_facts = 0
        self.discarded_facts = 0
        self.ties_from_lists = 0

    def teardown(self) -> None:
        self.graph.teardown()

    def record(self, url: str, title: str, block_number: int, text: str, concepts: list[dict],
                 ties: list[dict] | None = None, facts: list[dict] | None = None) -> int:
        """Salva il blocco, i suoi concetti verificati, i legami e i fatti dal testo (anche questi con la frase
        verificata). Restituisce quanti concetti sono stati accettati."""
        accepted = 0
        with self._mutex:
            source = self.graph.source(url, domain=urlsplit(url).hostname or "")
            segment = self.graph.segment(source, block_number, text, url)
            for c in concepts[:MAX_CONCEPTS_PER_BLOCK]:
                name = str(c.get("name") or "").strip()[:MAX_NAME]
                sentence = str(c.get("sentence") or "").strip()
                if not name or not present_quote(sentence, text):
                    self.discarded += 1
                    continue
                first = self.graph.find(name)
                entity_id = self.graph.entity(name)
                if first is None:
                    self.new_items.append(entity_id)
                translation = " ".join(str(c.get("translation") or "").split())[:400] or None
                self.graph.db.execute("INSERT OR IGNORE INTO mention (entity_id, segment_id, sentence, translation) "
                                      "VALUES (?, ?, ?, ?)", (entity_id, segment, sentence[:300], translation))
                accepted += 1
            self.valid += accepted
            accepted_ties = [x for x in (self._tie_from_text(tie, text, segment)
                                            for tie in (ties or [])[:MAX_TIES_PER_BLOCK]) if x]
            self._complete_lists(accepted_ties, segment)
            for fact in (facts or [])[:MAX_FACTS_PER_BLOCK]:
                self._fact(fact, text, segment)
            self.graph.db.commit()
        return accepted

    def _tie_from_text(self, tie: dict, text: str, segment: int) -> tuple | None:
        """Restituisce (da, relazione, a, frase, nome di da, nome di a) se il legame è entrato (o c'era già)."""
        parent, a = self.graph.find(str(tie.get("parent") or "")), self.graph.find(str(tie.get("a") or ""))
        relation = " ".join(str(tie.get("relation") or "").split())[:80]
        sentence = str(tie.get("sentence") or "").strip()
        if parent is None or a is None or parent == a or not relation or not present_quote(sentence, text):
            self.discarded_ties += 1
            return None
        result = (parent, relation, a, sentence, str(tie.get("parent")), str(tie.get("a")))
        already = self.graph.db.execute(
            "SELECT 1 FROM triple WHERE subject_id = ? AND object_id = ? AND relation = ? AND segment_id = ?",
            (parent, a, relation, segment)).fetchone()
        if already:
            return result
        translation = " ".join(str(tie.get("translation") or "").split())[:400] or None
        self.graph.db.execute(
            "INSERT INTO triple (subject_id, relation, object_id, object_kind, force, origin, quote, "
            "translation, why, segment_id) VALUES (?, ?, ?, 'entity', 'fact', 'extracted', ?, ?, ?, ?)",
            (parent, relation, a, sentence[:300], translation, _clean(tie.get("why"), 200) or None, segment))
        self.ties_from_text += 1
        return result

    def _complete_lists(self, ties: list[tuple], segment: int) -> None:
        """Rete di sicurezza per gli elenchi (diagnosi del 15/10, Galileo: "tra i suoi discepoli figurano Riccioli,
        Bettini, Zucchi e Cabeo" → legami solo per Riccioli e Cabeo). Se due o più legami con la stessa relazione e
        la stessa frase puntano allo stesso concetto, i nomi propri dell'elenco che stanno TRA quelli già legati
        diventano concetti con lo stesso legame (origine "estratta", la frase è la stessa già verificata)."""
        groups: dict[tuple, list[tuple[str, int]]] = {}
        for parent, relation, a, sentence, name_from, name_to in ties:
            groups.setdefault((relation, sentence, "a", a), []).append((name_from, parent))
            groups.setdefault((relation, sentence, "parent", parent), []).append((name_to, a))
        for (relation, sentence, side, fixed), known in groups.items():
            if len(known) < 2:
                continue
            pieces = [x.strip(" .;:()\"'«»") for x in _LIST.split(sentence)]
            indices = [i for i, piece in enumerate(pieces)
                      if any(_surname(name) and _surname(name) in piece.lower() for name, _ in known)]
            if len(indices) < 2:
                continue
            for piece in pieces[min(indices) + 1:max(indices)]:
                if pieces.index(piece) in indices or not _PROPER_NAME.fullmatch(piece):
                    continue
                new = self.graph.find(piece)
                if new is None:
                    new = self.graph.entity(piece)
                    self.new_items.append(new)
                if new == fixed:
                    continue
                self.graph.db.execute("INSERT OR IGNORE INTO mention (entity_id, segment_id, sentence) VALUES (?, ?, ?)",
                                      (new, segment, sentence[:300]))
                parent, a = (new, fixed) if side == "a" else (fixed, new)
                already = self.graph.db.execute(
                    "SELECT 1 FROM triple WHERE subject_id = ? AND object_id = ? AND relation = ? AND "
                    "segment_id = ?", (parent, a, relation, segment)).fetchone()
                if already:
                    continue
                self.graph.db.execute(
                    "INSERT INTO triple (subject_id, relation, object_id, object_kind, force, origin, quote, "
                    "why, segment_id) VALUES (?, ?, ?, 'entity', 'fact', 'extracted', ?, ?, ?)",
                    (parent, relation, a, sentence[:300], "nello stesso elenco della frase (completato dal programma)",
                     segment))
                self.ties_from_text += 1
                self.ties_from_lists += 1

    def _fact(self, fact: dict, text: str, segment: int) -> None:
        """Un fatto vale se la frase è nel blocco e contiene tutti i numeri del valore (niente date inventate). Se il
        concetto non è ancora nella mappa, nasce qui con la frase del fatto come menzione."""
        name = _clean(fact.get("concept"), MAX_NAME)
        attribute = _clean(fact.get("attribute"), 60)
        value = _clean(fact.get("value"), 150)
        sentence = str(fact.get("sentence") or "").strip()
        if not name or not attribute or not value or not present_quote(sentence, text) \
                or not numbers(value) <= numbers(sentence):
            self.discarded_facts += 1
            return
        translation = _clean(fact.get("translation"), 400) or None
        entity_id = self.graph.find(name)
        if entity_id is None:
            entity_id = self.graph.entity(name)
            self.new_items.append(entity_id)
            self.graph.db.execute("INSERT OR IGNORE INTO mention (entity_id, segment_id, sentence, translation) "
                                  "VALUES (?, ?, ?, ?)", (entity_id, segment, sentence[:300], translation))
        already = self.graph.db.execute(
            "SELECT 1 FROM triple WHERE subject_id = ? AND relation = ? AND object_value = ? AND segment_id = ?",
            (entity_id, attribute, value, segment)).fetchone()
        if already:
            return
        self.graph.db.execute(
            "INSERT INTO triple (subject_id, relation, object_value, object_kind, force, origin, quote, "
            "translation, segment_id) VALUES (?, ?, ?, 'value', 'fact', 'extracted', ?, ?, ?)",
            (entity_id, attribute, value, sentence[:300], translation, segment))
        self.valid_facts += 1

    def new_take(self) -> list[int]:
        """I concetti nuovi da dare a Claude, e si riparte da zero."""
        with self._mutex:
            new_items, self.new_items = self.new_items, []
        return new_items

    def listing(self, ids: list[int] | None = None, maximum: int = 60) -> list[dict]:
        """Concetti con il numero di menzioni e di fonti e una frase d'esempio (i più citati prima)."""
        filter_spec = f"WHERE e.id IN ({','.join('?' * len(ids))})" if ids else ""
        rows = self.graph.db.execute(
            "SELECT e.id, e.name, COUNT(m.segment_id) AS mentions, COUNT(DISTINCT s.source_id) AS sources, "
            "MIN(m.sentence) AS sentence, MAX(m.translation) AS translation FROM entity e JOIN mention m ON m.entity_id = e.id "
            f"JOIN segment s ON s.id = m.segment_id {filter_spec} GROUP BY e.id ORDER BY mentions DESC, e.id LIMIT ?",
            [*(ids or []), maximum])
        return [dict(r) for r in rows]

    def where(self, entity_id: int) -> list[dict]:
        """Dove compare un concetto: pagina, blocco, frase."""
        rows = self.graph.db.execute(
            "SELECT f.url, s.number, s.id AS segment, m.sentence, m.translation FROM mention m "
            "JOIN segment s ON s.id = m.segment_id "
            "JOIN source f ON f.id = s.source_id WHERE m.entity_id = ? ORDER BY s.id", (entity_id,))
        return [dict(r) for r in rows]

    def segments(self, entity_ids: list[int]) -> list[dict]:
        """I blocchi salvati che spiegano questi concetti (per la scrittura), senza doppioni."""
        if not entity_ids:
            return []
        rows = self.graph.db.execute(
            "SELECT DISTINCT s.id, f.url, s.number, s.text FROM mention m JOIN segment s ON s.id = m.segment_id "
            f"JOIN source f ON f.id = s.source_id WHERE m.entity_id IN ({','.join('?' * len(entity_ids))}) ORDER BY s.id",
            entity_ids)
        return [dict(r) for r in rows]

    def ties(self) -> list[dict]:
        """Tutti i legami tra concetti: dal testo (origine "estratta", con frase verificata) e dedotti da Claude."""
        return [t for t in self.graph.triples() if t["origin"] in ("claude", "extracted") and t["object_kind"] == "entity"]

    def facts(self) -> list[dict]:
        """I fatti (concetto, attributo, valore) con frase, traduzione, blocco e pagina."""
        rows = self.graph.db.execute(
            "SELECT t.id, t.subject_id AS concept_id, e.name AS concept, t.relation AS attribute, "
            "t.object_value AS value, t.quote AS sentence, t.translation, t.segment_id, s.source_id, f.url "
            "FROM triple t JOIN entity e ON e.id = t.subject_id JOIN segment s ON s.id = t.segment_id "
            "JOIN source f ON f.id = s.source_id WHERE t.valid = 1 AND t.object_kind = 'value' ORDER BY t.id")
        return [dict(r) for r in rows]

    def grouped_facts(self) -> list[dict]:
        """Lo stesso fatto letto in più pagine (anche in lingue diverse) conta una volta, con il numero di fonti."""
        groups: dict[tuple, dict] = {}
        for f in self.facts():
            key = (f["concept_id"], f["attribute"].lower(), value_key(f["value"]))
            g = groups.setdefault(key, {**f, "sources": set(), "proofs": []})
            g["sources"].add(f["source_id"])
            g["proofs"].append(f)
        return sorted(({**g, "sources": len(g["sources"])} for g in groups.values()),
                      key=lambda g: (-g["sources"], g["id"]))

    def save_status(self, key: str, value: object) -> None:
        """Vincoli della domanda e risposte candidate, salvati con il grafo (per la pagina del grafo)."""
        with self._mutex:
            self.graph.db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
                                  (key, json.dumps(value, ensure_ascii=False)))
            self.graph.db.commit()

    def status(self, key: str, default: object = None) -> object:
        row = self.graph.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # --- consolidamento di Claude ----------------------------------------------------------------------------

    def isolated(self) -> list[int]:
        """Concetti senza legami e non giudicati inutili da Claude."""
        return [r["id"] for r in self.graph.db.execute(
            "SELECT e.id FROM entity e WHERE e.id IN (SELECT entity_id FROM mention) "
            "AND e.id NOT IN (SELECT entity_id FROM irrelevant) AND NOT EXISTS (SELECT 1 FROM triple t WHERE "
            "t.valid = 1 AND t.origin IN ('claude', 'extracted') AND t.object_kind = 'entity' "
            "AND (t.subject_id = e.id OR t.object_id = e.id)) "
            "ORDER BY e.id")]

    def irrelevant(self) -> list[int]:
        return [r[0] for r in self.graph.db.execute("SELECT entity_id FROM irrelevant")]

    def possible_duplicates(self, maximum: int = 25) -> list[tuple[int, int]]:
        """Coppie di concetti forse uguali, per Claude: un nome contenuto nell'altro ("Riccioli" e "Giovanni Battista
        Riccioli") o quasi uguali ("Ricciolo" e "Riccioli"); mai con numeri diversi."""
        from difflib import SequenceMatcher

        from .entity import digits, normalize
        names = [(c["id"], c["name"], normalize(c["name"])) for c in self.listing(maximum=400)]
        pairs = []
        for i, (id1, nome1, n1) in enumerate(names):
            for id2, nome2, n2 in names[i + 1:]:
                if digits(nome1) != digits(nome2):
                    continue
                short, long = sorted((n1, n2), key=len)
                content = len(short) >= 4 and set(short.split()) <= set(long.split())
                similar = SequenceMatcher(None, n1, n2).ratio()
                if content or similar >= 0.85:
                    pairs.append((max(similar, 0.9 if content else 0), id1, id2))
        return [(a, b) for _, a, b in sorted(pairs, reverse=True)[:maximum]]

    def per_claude(self, question: str, sub_questions: list[str], searches: list[str], new_items: list[int],
                   constraints: list[str] | None = None, candidate: list[dict] | None = None) -> dict:
        """Quello che Claude vede: TUTTA la mappa in forma compatta (numero e nome, fino a 400 concetti), i fatti
        trovati (con quante fonti), i concetti nuovi e quelli isolati con una frase (già ripulite dalla lettura), i
        legami con il loro perché, i doppioni possibili, i vincoli della domanda e le risposte candidate."""
        from_field = lambda text, n: " ".join(str(text).split())[:n]  # noqa: E731
        names = {c["id"]: c["name"] for c in self.listing(maximum=100000)}
        with_sentence = lambda ids: [{"n": c["id"], "name": from_field(c["name"], 120), "sentence": from_field(c["sentence"], 200),  # noqa: E731
                                  "sources": c["sources"]} for c in self.listing(ids, maximum=80)] if ids else []
        ties = [f"[{t['subject_id']}] {from_field(t['subject'], 60)} → {from_field(t['relation'], 60)} → "
                  f"[{t['object_id']}] {from_field(t['object'], 60)}"
                  + (" (dal testo)" if t["origin"] == "extracted" else "")
                  + (f" — perché: {from_field(t['why'], 100)}" if t.get("why") else "")
                  for t in self.ties()[-200:]]
        facts = [f"[{g['concept_id']}] {from_field(g['concept'], 60)} — {from_field(g['attribute'], 40)}: "
                 f"{from_field(g['value'], 80)} ({g['sources']} {'fonte' if g['sources'] == 1 else 'sources'})"
                 for g in self.grouped_facts()[:150]]
        return {
            "question": from_field(question, 600), "sub_questions": [from_field(s, 200) for s in sub_questions[:6]],
            "mapping": [f"[{c['id']}] {from_field(c['name'], 80)} ({c['mentions']})" for c in self.listing(maximum=400)],
            "new_concepts": with_sentence(new_items),
            "isolated": with_sentence([i for i in self.isolated() if i not in new_items][:80]),
            "possible_duplicates": [f"[{a}] {from_field(names.get(a, ''), 80)} = [{b}] {from_field(names.get(b, ''), 80)} ?"
                                   for a, b in self.possible_duplicates()],
            "ties": ties,
            "facts": facts,
            "constraints": [from_field(v, 150) for v in (constraints or [])[:6]],
            "candidate_answers": [f"{from_field(c.get('value'), 150)} (concetti {c.get('concepts', [])}; "
                                   f"{c.get('status', '')}"
                                   + (f": {from_field(c.get('constraint_in_doubt'), 150)}" if c.get("constraint_in_doubt")
                                      else "") + ")"
                                   for c in (candidate or [])[:5]],
            "searches_done": [from_field(r, 120) for r in searches[-15:]],
        }

    def apply(self, decision: dict) -> dict:
        """Applica legami e unioni decisi da Claude; i numeri che non esistono si ignorano e si contano."""
        counts = {"ties": 0, "unions": 0, "ignored": 0, "irrelevant": 0}
        with self._mutex:
            existing = {r["id"]: r["name"] for r in self.graph.db.execute("SELECT id, name FROM entity")}
            for u in decision.get("merge", []):
                keep, remove = u.get("keep"), u.get("remove")
                if keep == remove or keep not in existing or remove not in existing or \
                        not self._merge(keep, remove, existing):
                    counts["ignored"] += 1
                    continue
                counts["unions"] += 1
            for tie in decision.get("ties", []):
                parent, a = tie.get("parent"), tie.get("a")
                why = _clean(tie.get("why"), 200)
                if parent == a or parent not in existing or a not in existing or len(why) < 5:
                    counts["ignored"] += 1  # un legame di Claude senza perché non entra (diagnosi del 14/10)
                    continue
                self.graph.db.execute(
                    "INSERT INTO triple (subject_id, relation, object_id, object_kind, force, origin, why) "
                    "VALUES (?, ?, ?, 'entity', 'interpretation', 'claude', ?)",
                    (parent, " ".join(str(tie.get("relation", "")).split())[:80], a, why))
                counts["ties"] += 1
            for entity_id in decision.get("irrelevant", []):
                if entity_id in existing:
                    self.graph.db.execute("INSERT OR IGNORE INTO irrelevant (entity_id) VALUES (?)", (entity_id,))
                    counts["irrelevant"] += 1
            self.graph.db.commit()
        return counts

    def _merge(self, keep: int, remove: int, existing: dict[int, str]) -> bool:
        """Sposta menzioni e legami di `togli` su `tieni`; il nome tolto resta come alias. Mai nomi con numeri
        diversi (regola dei numeri)."""
        from .entity import digits
        if digits(existing[keep]) != digits(existing[remove]):
            return False
        db = self.graph.db
        db.execute("UPDATE OR IGNORE mention SET entity_id = ? WHERE entity_id = ?", (keep, remove))
        db.execute("DELETE FROM mention WHERE entity_id = ?", (remove,))
        db.execute("UPDATE triple SET subject_id = ? WHERE subject_id = ?", (keep, remove))
        db.execute("UPDATE triple SET object_id = ? WHERE object_id = ?", (keep, remove))
        db.execute("UPDATE alias SET entity_id = ? WHERE entity_id = ?", (keep, remove))
        db.execute("DELETE FROM irrelevant WHERE entity_id = ?", (remove,))
        name_norm = db.execute("SELECT name_norm FROM entity WHERE id = ?", (remove,)).fetchone()["name_norm"]
        db.execute("DELETE FROM entity WHERE id = ?", (remove,))
        db.execute("INSERT OR REPLACE INTO alias (name_norm, entity_id) VALUES (?, ?)", (name_norm, keep))
        existing.pop(remove, None)
        self.new_items = [keep if n == remove else n for n in self.new_items]
        return True
