"""Archivio del grafo delle prove di una ricerca (Fase D, Parte 8.4 del piano), in SQLite.

Tabelle: fonte, segmento, entita (+ alias), tripla, deriva (triple dedotte → triple da cui derivano). Il campo `valida`
permette la correzione di ToG: una tripla segnata come sbagliata diventa 0 insieme alle dedotte che ne dipendono.
Una vista networkx si costruisce in memoria quando serve.
"""
import sqlite3
from pathlib import Path
from typing import Any

import networkx as nx

from . import entity as ent

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (id INTEGER PRIMARY KEY, url TEXT UNIQUE, domain TEXT, css_class TEXT,
    provenance_id TEXT, access_status TEXT, method TEXT, read_on TEXT);
CREATE TABLE IF NOT EXISTS segment (id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES source(id), number INTEGER,
    text TEXT, citable_url TEXT, UNIQUE(source_id, number));
CREATE TABLE IF NOT EXISTS entity (id INTEGER PRIMARY KEY, name TEXT, name_norm TEXT UNIQUE, kind TEXT,
    wikidata_id TEXT);
CREATE TABLE IF NOT EXISTS alias (name_norm TEXT PRIMARY KEY, entity_id INTEGER REFERENCES entity(id));
CREATE TABLE IF NOT EXISTS triple (id INTEGER PRIMARY KEY, subject_id INTEGER REFERENCES entity(id),
    relation TEXT, object_id INTEGER REFERENCES entity(id), object_value TEXT, object_kind TEXT, force TEXT,
    origin TEXT CHECK (origin IN ('extracted', 'inferred', 'ambiguous', 'claude')), quote TEXT, translation TEXT,
    why TEXT,
    segment_id INTEGER REFERENCES segment(id), valid INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS derived (triple_id INTEGER REFERENCES triple(id), from_triple_id INTEGER REFERENCES triple(id),
    PRIMARY KEY (triple_id, from_triple_id));
CREATE TABLE IF NOT EXISTS mention (entity_id INTEGER REFERENCES entity(id), segment_id INTEGER REFERENCES segment(id),
    sentence TEXT, translation TEXT, PRIMARY KEY (entity_id, segment_id, sentence));
CREATE INDEX IF NOT EXISTS mention_segment ON mention(segment_id);
CREATE INDEX IF NOT EXISTS triple_subject ON triple(subject_id);
CREATE INDEX IF NOT EXISTS triple_object ON triple(object_id);
"""

SOURCE_COLUMNS = {"domain", "css_class", "provenance_id", "access_status", "method", "read_on"}


class Graph:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # La ricerca prende appunti (e triple) in più thread: le scritture passano da un blocco del chiamante.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # Grafi salvati prima delle traduzioni e dei "perché" dei legami (14/10): si aggiungono le colonne mancanti.
        for table, column in (("triple", "translation"), ("mention", "translation"), ("triple", "why")):
            columns = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    def teardown(self) -> None:
        self.db.close()

    # --- fonti e segmenti ------------------------------------------------------------------------------------

    def source(self, url: str, **fields: Any) -> int:
        row = self.db.execute("SELECT id FROM source WHERE url = ?", (url,)).fetchone()
        if row:
            return row["id"]
        foreign = set(fields) - SOURCE_COLUMNS
        if foreign:
            raise ValueError(f"campi della fonte sconosciuti: {sorted(foreign)}")
        columns = ["url", *fields]
        cur = self.db.execute(f"INSERT INTO source ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                              (url, *fields.values()))
        return int(cur.lastrowid)

    def segment(self, source_id: int, number: int, text: str, citable_url: str = "") -> int:
        row = self.db.execute("SELECT id FROM segment WHERE source_id = ? AND number = ?",
                               (source_id, number)).fetchone()
        if row:
            return row["id"]
        cur = self.db.execute("INSERT INTO segment (source_id, number, text, citable_url) VALUES (?, ?, ?, ?)",
                              (source_id, number, text, citable_url))
        return int(cur.lastrowid)

    # --- entità ----------------------------------------------------------------------------------------------

    def find(self, name: str) -> int | None:
        norm = ent.normalize(name)
        row = self.db.execute("SELECT id FROM entity WHERE name_norm = ?", (norm,)).fetchone() or \
            self.db.execute("SELECT entity_id AS id FROM alias WHERE name_norm = ?", (norm,)).fetchone()
        return row["id"] if row else None

    def entity(self, name: str, kind: str = "", id_wikidata: str | None = None) -> int:
        """Id dell'entità con questo nome (o alias), creandola se manca."""
        existing = self.find(name)
        if existing is not None:
            if id_wikidata:
                self.db.execute("UPDATE entity SET wikidata_id = COALESCE(wikidata_id, ?) WHERE id = ?",
                                (id_wikidata, existing))
            return existing
        cur = self.db.execute("INSERT INTO entity (name, name_norm, kind, wikidata_id) VALUES (?, ?, ?, ?)",
                              (str(name).strip(), ent.normalize(name), kind, id_wikidata))
        return int(cur.lastrowid)

    def alias(self, entity_id: int, alias: str) -> bool:
        """Aggiunge un nome alternativo (es. da Wikidata). False se cambierebbe i numeri o è già di un'altra entità."""
        name = self.db.execute("SELECT name FROM entity WHERE id = ?", (entity_id,)).fetchone()["name"]
        if not ent.alias_compatibile(name, alias):
            return False
        already = self.find(alias)
        if already is not None:
            return already == entity_id
        self.db.execute("INSERT INTO alias (name_norm, entity_id) VALUES (?, ?)", (ent.normalize(alias), entity_id))
        return True

    # --- triple ----------------------------------------------------------------------------------------------

    def triple(self, subject: str, relation: str, obj: str, object_kind: str, force: str,
               segment_id: int | None, quote: str = "", origin: str = "extracted",
               parent: list[int] | None = None) -> int:
        subject_id = self.entity(subject)
        object_id = self.entity(obj) if object_kind == "entity" else None
        cur = self.db.execute(
            "INSERT INTO triple (subject_id, relation, object_id, object_value, object_kind, force, origin, "
            "quote, segment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (subject_id, relation, object_id, None if object_id else obj, object_kind, force, origin,
             quote, segment_id))
        triple_id = int(cur.lastrowid)
        for another in parent or []:
            self.db.execute("INSERT OR IGNORE INTO derived (triple_id, from_triple_id) VALUES (?, ?)", (triple_id, another))
        return triple_id

    def drop(self, triple_id: int) -> list[int]:
        """Segna la tripla come sbagliata, con tutte le dedotte che ne dipendono (anche indirettamente)."""
        invalidate, from_do = [], [triple_id]
        while from_do:
            current = from_do.pop()
            if current in invalidate:
                continue
            invalidate.append(current)
            self.db.execute("UPDATE triple SET valid = 0 WHERE id = ?", (current,))
            from_do += [r["triple_id"] for r in
                        self.db.execute("SELECT triple_id FROM derived WHERE from_triple_id = ?", (current,))]
        return invalidate

    def triples(self, valid_only: bool = True, origin: str | None = None) -> list[dict]:
        conditions, parameters = [], []
        if valid_only:
            conditions.append("t.valid = 1")
        if origin:
            conditions.append("t.origin = ?")
            parameters.append(origin)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.db.execute(
            "SELECT t.id, s.name AS subject, t.relation, COALESCE(o.name, t.object_value) AS object, "
            "t.object_kind, t.force, t.origin, t.quote, t.translation, t.why, t.segment_id, t.valid, "
            "t.subject_id, t.object_id, g.citable_url "
            "FROM triple t JOIN entity s ON s.id = t.subject_id LEFT JOIN entity o ON o.id = t.object_id "
            f"LEFT JOIN segment g ON g.id = t.segment_id {where} ORDER BY t.id", parameters)
        return [dict(r) for r in rows]

    def view(self) -> nx.MultiDiGraph:
        """Le triple valide tra entità come grafo networkx (i valori restano attributi dell'entità soggetto)."""
        graph = nx.MultiDiGraph()
        for r in self.db.execute("SELECT id, name FROM entity"):
            graph.add_node(r["id"], name=r["name"], values=[])
        for t in self.db.execute("SELECT * FROM triple WHERE valid = 1"):
            if t["object_id"] is not None:
                graph.add_edge(t["subject_id"], t["object_id"], key=t["id"], relation=t["relation"],
                               force=t["force"], origin=t["origin"])
            else:
                graph.nodes[t["subject_id"]]["values"].append((t["relation"], t["object_value"]))
        return graph
