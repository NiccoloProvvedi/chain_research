"""Archivio del grafo delle prove di una ricerca (Fase D, Parte 8.4 del piano), in SQLite.

Tabelle: fonte, segmento, entita (+ alias), tripla, deriva (triple dedotte → triple da cui derivano). Il campo `valida`
permette la correzione di ToG: una tripla segnata come sbagliata diventa 0 insieme alle dedotte che ne dipendono.
Una vista networkx si costruisce in memoria quando serve.
"""
import sqlite3
from pathlib import Path
from typing import Any

import networkx as nx

from . import entita as ent

SCHEMA = """
CREATE TABLE IF NOT EXISTS fonte (id INTEGER PRIMARY KEY, url TEXT UNIQUE, dominio TEXT, classe TEXT,
    id_provenienza TEXT, stato_accesso TEXT, metodo TEXT, letto_il TEXT);
CREATE TABLE IF NOT EXISTS segmento (id INTEGER PRIMARY KEY, fonte_id INTEGER REFERENCES fonte(id), numero INTEGER,
    testo TEXT, url_citabile TEXT, UNIQUE(fonte_id, numero));
CREATE TABLE IF NOT EXISTS entita (id INTEGER PRIMARY KEY, nome TEXT, nome_norm TEXT UNIQUE, tipo TEXT,
    id_wikidata TEXT);
CREATE TABLE IF NOT EXISTS alias (nome_norm TEXT PRIMARY KEY, entita_id INTEGER REFERENCES entita(id));
CREATE TABLE IF NOT EXISTS tripla (id INTEGER PRIMARY KEY, soggetto_id INTEGER REFERENCES entita(id),
    relazione TEXT, oggetto_id INTEGER REFERENCES entita(id), oggetto_valore TEXT, tipo_oggetto TEXT, forza TEXT,
    origine TEXT CHECK (origine IN ('estratta', 'dedotta', 'ambigua', 'claude')), citazione TEXT, traduzione TEXT,
    perche TEXT,
    segmento_id INTEGER REFERENCES segmento(id), valida INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS deriva (tripla_id INTEGER REFERENCES tripla(id), da_tripla_id INTEGER REFERENCES tripla(id),
    PRIMARY KEY (tripla_id, da_tripla_id));
CREATE TABLE IF NOT EXISTS menzione (entita_id INTEGER REFERENCES entita(id), segmento_id INTEGER REFERENCES segmento(id),
    frase TEXT, traduzione TEXT, PRIMARY KEY (entita_id, segmento_id, frase));
CREATE INDEX IF NOT EXISTS menzione_segmento ON menzione(segmento_id);
CREATE INDEX IF NOT EXISTS tripla_soggetto ON tripla(soggetto_id);
CREATE INDEX IF NOT EXISTS tripla_oggetto ON tripla(oggetto_id);
"""

COLONNE_FONTE = {"dominio", "classe", "id_provenienza", "stato_accesso", "metodo", "letto_il"}


class Grafo:
    def __init__(self, percorso: Path | str = ":memory:") -> None:
        if percorso != ":memory:":
            Path(percorso).parent.mkdir(parents=True, exist_ok=True)
        # La ricerca prende appunti (e triple) in più thread: le scritture passano da un blocco del chiamante.
        self.db = sqlite3.connect(str(percorso), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # Grafi salvati prima delle traduzioni e dei "perché" dei legami (14/10): si aggiungono le colonne mancanti.
        for tabella, colonna in (("tripla", "traduzione"), ("menzione", "traduzione"), ("tripla", "perche")):
            colonne = {r["name"] for r in self.db.execute(f"PRAGMA table_info({tabella})")}
            if colonna not in colonne:
                self.db.execute(f"ALTER TABLE {tabella} ADD COLUMN {colonna} TEXT")

    def chiudi(self) -> None:
        self.db.close()

    # --- fonti e segmenti ------------------------------------------------------------------------------------

    def fonte(self, url: str, **campi: Any) -> int:
        riga = self.db.execute("SELECT id FROM fonte WHERE url = ?", (url,)).fetchone()
        if riga:
            return riga["id"]
        estranei = set(campi) - COLONNE_FONTE
        if estranei:
            raise ValueError(f"campi della fonte sconosciuti: {sorted(estranei)}")
        colonne = ["url", *campi]
        cur = self.db.execute(f"INSERT INTO fonte ({', '.join(colonne)}) VALUES ({', '.join('?' * len(colonne))})",
                              (url, *campi.values()))
        return int(cur.lastrowid)

    def segmento(self, fonte_id: int, numero: int, testo: str, url_citabile: str = "") -> int:
        riga = self.db.execute("SELECT id FROM segmento WHERE fonte_id = ? AND numero = ?",
                               (fonte_id, numero)).fetchone()
        if riga:
            return riga["id"]
        cur = self.db.execute("INSERT INTO segmento (fonte_id, numero, testo, url_citabile) VALUES (?, ?, ?, ?)",
                              (fonte_id, numero, testo, url_citabile))
        return int(cur.lastrowid)

    # --- entità ----------------------------------------------------------------------------------------------

    def trova(self, nome: str) -> int | None:
        norma = ent.normalizza(nome)
        riga = self.db.execute("SELECT id FROM entita WHERE nome_norm = ?", (norma,)).fetchone() or \
            self.db.execute("SELECT entita_id AS id FROM alias WHERE nome_norm = ?", (norma,)).fetchone()
        return riga["id"] if riga else None

    def entita(self, nome: str, tipo: str = "", id_wikidata: str | None = None) -> int:
        """Id dell'entità con questo nome (o alias), creandola se manca."""
        esistente = self.trova(nome)
        if esistente is not None:
            if id_wikidata:
                self.db.execute("UPDATE entita SET id_wikidata = COALESCE(id_wikidata, ?) WHERE id = ?",
                                (id_wikidata, esistente))
            return esistente
        cur = self.db.execute("INSERT INTO entita (nome, nome_norm, tipo, id_wikidata) VALUES (?, ?, ?, ?)",
                              (str(nome).strip(), ent.normalizza(nome), tipo, id_wikidata))
        return int(cur.lastrowid)

    def alias(self, entita_id: int, alias: str) -> bool:
        """Aggiunge un nome alternativo (es. da Wikidata). False se cambierebbe i numeri o è già di un'altra entità."""
        nome = self.db.execute("SELECT nome FROM entita WHERE id = ?", (entita_id,)).fetchone()["nome"]
        if not ent.alias_compatibile(nome, alias):
            return False
        gia = self.trova(alias)
        if gia is not None:
            return gia == entita_id
        self.db.execute("INSERT INTO alias (nome_norm, entita_id) VALUES (?, ?)", (ent.normalizza(alias), entita_id))
        return True

    # --- triple ----------------------------------------------------------------------------------------------

    def tripla(self, soggetto: str, relazione: str, oggetto: str, tipo_oggetto: str, forza: str,
               segmento_id: int | None, citazione: str = "", origine: str = "estratta",
               da: list[int] | None = None) -> int:
        soggetto_id = self.entita(soggetto)
        oggetto_id = self.entita(oggetto) if tipo_oggetto == "entita" else None
        cur = self.db.execute(
            "INSERT INTO tripla (soggetto_id, relazione, oggetto_id, oggetto_valore, tipo_oggetto, forza, origine, "
            "citazione, segmento_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (soggetto_id, relazione, oggetto_id, None if oggetto_id else oggetto, tipo_oggetto, forza, origine,
             citazione, segmento_id))
        tripla_id = int(cur.lastrowid)
        for altra in da or []:
            self.db.execute("INSERT OR IGNORE INTO deriva (tripla_id, da_tripla_id) VALUES (?, ?)", (tripla_id, altra))
        return tripla_id

    def invalida(self, tripla_id: int) -> list[int]:
        """Segna la tripla come sbagliata, con tutte le dedotte che ne dipendono (anche indirettamente)."""
        invalidate, da_fare = [], [tripla_id]
        while da_fare:
            corrente = da_fare.pop()
            if corrente in invalidate:
                continue
            invalidate.append(corrente)
            self.db.execute("UPDATE tripla SET valida = 0 WHERE id = ?", (corrente,))
            da_fare += [r["tripla_id"] for r in
                        self.db.execute("SELECT tripla_id FROM deriva WHERE da_tripla_id = ?", (corrente,))]
        return invalidate

    def triple(self, solo_valide: bool = True, origine: str | None = None) -> list[dict]:
        condizioni, parametri = [], []
        if solo_valide:
            condizioni.append("t.valida = 1")
        if origine:
            condizioni.append("t.origine = ?")
            parametri.append(origine)
        dove = f"WHERE {' AND '.join(condizioni)}" if condizioni else ""
        righe = self.db.execute(
            "SELECT t.id, s.nome AS soggetto, t.relazione, COALESCE(o.nome, t.oggetto_valore) AS oggetto, "
            "t.tipo_oggetto, t.forza, t.origine, t.citazione, t.traduzione, t.perche, t.segmento_id, t.valida, "
            "t.soggetto_id, t.oggetto_id, g.url_citabile "
            "FROM tripla t JOIN entita s ON s.id = t.soggetto_id LEFT JOIN entita o ON o.id = t.oggetto_id "
            f"LEFT JOIN segmento g ON g.id = t.segmento_id {dove} ORDER BY t.id", parametri)
        return [dict(r) for r in righe]

    def vista(self) -> nx.MultiDiGraph:
        """Le triple valide tra entità come grafo networkx (i valori restano attributi dell'entità soggetto)."""
        grafo = nx.MultiDiGraph()
        for r in self.db.execute("SELECT id, nome FROM entita"):
            grafo.add_node(r["id"], nome=r["nome"], valori=[])
        for t in self.db.execute("SELECT * FROM tripla WHERE valida = 1"):
            if t["oggetto_id"] is not None:
                grafo.add_edge(t["soggetto_id"], t["oggetto_id"], key=t["id"], relazione=t["relazione"],
                               forza=t["forza"], origine=t["origine"])
            else:
                grafo.nodes[t["soggetto_id"]]["valori"].append((t["relazione"], t["oggetto_valore"]))
        return grafo
