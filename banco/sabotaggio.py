"""Sabotaggio: spento un controllo alla volta, almeno un test deve fallire (docs/solidita-e-validazione.md, T1).

Copia il codice e i test in una cartella temporanea, cambia una riga e lancia i test senza rete: se passano tutti,
quel controllo non è protetto da nessun test. Non tocca il progetto e non chiama Claude.

Uso: venv/bin/python banco/sabotaggio.py        (esce con 1 se un sabotaggio passa inosservato)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
TEST = ["test/test_validazione.py", "test/test_catena.py", "test/test_banco.py", "test/test_regressioni.py"]
# (nome, file, testo da trovare una sola volta, testo che lo sostituisce)
SABOTAGGI = [
    ("G1 il valore non si confronta più con la riga", "ricerca_catena/web/catena.py",
     "if prova is not None and not validazione.valore_nella_riga(esito.get(\"valore\", \"\"), prova.testo):",
     "if False:"),
    ("G1 i numeri non contano", "ricerca_catena/web/validazione.py",
     "            if intero not in suoi and not all(", "            if False and not all("),
    ("G1 il mese non conta", "ricerca_catena/web/validazione.py",
     "return not mesi or not della_riga or bool(mesi & della_riga)", "return True"),
    ("G3 un antenato può tornare", "ricerca_catena/web/validazione.py",
     "        if mia & _identita(nodo):", "        if False:"),
    ("G3 il controllo non è collegato alla frontiera", "ricerca_catena/web/catena.py",
     "            tornati = [a for a in figli if validazione.ripete_un_antenato(a, nodo)]", "            tornati = []"),
    ("G4 il controllo non è collegato alla verifica", "ricerca_catena/web/catena.py",
     " and not validazione.legame_solo_da_estratti(a)]", "]"),
    ("G7 i disaccordi non si registrano", "ricerca_catena/web/catena.py",
     "            if voce not in self.conflitti:", "            if False:"),
    ("G4 un estratto vale come una pagina letta", "ricerca_catena/web/validazione.py",
     "    if str(prova.testo).startswith(ESTRATTO):", "    if False:"),
    ("G4 confermata anche con il valore da un estratto", "ricerca_catena/web/catena.py",
     "deboli == 0 and fonte != \"C\" and not contrari", "deboli == 0 and not contrari"),
    ("G7 i disaccordi non tolgono il sigillo", "ricerca_catena/web/catena.py",
     "deboli == 0 and fonte != \"C\" and not contrari", "deboli == 0 and fonte != \"C\""),
    ("G7 ogni riga di Wikipedia è un disaccordo", "ricerca_catena/web/validazione.py",
     " or not _dal_riquadro(a):", ":"),
    ("G7 anche i fratelli sono disaccordi", "ricerca_catena/web/validazione.py",
     "if a.da is not None and not _legame_forte(a)}", "if a.da is not None}"),
    ("anello debole confermato lo stesso", "ricerca_catena/web/catena.py",
     "deboli == 0 and fonte != \"C\" and not contrari", "fonte != \"C\" and not contrari"),
    ("giudice: il segno non conta", "banco/giudice.py",
     "    return all((n in _negativi(attesa)) == (n in sotto)", "    return True or all((n in _negativi(attesa)) == (n in sotto)"),
    ("giudice: l'errore vale risposta sbagliata", "banco/giudice.py",
     "return (None, \"giudice in errore\") if errori == 2 else", "return"),
    ("banco: si estrae sempre con il modello", "banco/giudice.py",
     "    return valore or estrai(", "    return estrai("),
    ("durata con le attese del limite", "ricerca_catena/web/raccolta.py",
     "\"secondi\": round(self._trascorsi())", "\"secondi\": round(time.monotonic() - self._inizio)"),
]


def main() -> int:
    inosservati = []
    for nome, file, vecchio, nuovo in SABOTAGGI:
        with tempfile.TemporaryDirectory() as cartella:
            copia = Path(cartella) / "progetto"
            for parte in ("ricerca_catena", "test", "banco"):
                shutil.copytree(RADICE / parte, copia / parte, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(RADICE / "pytest.ini", copia / "pytest.ini")
            bersaglio = copia / file
            testo = bersaglio.read_text(encoding="utf-8")
            if testo.count(vecchio) != 1:
                print(f"? {nome}: la riga da cambiare non c'è più (o non è unica): aggiorna banco/sabotaggio.py")
                inosservati.append(nome)
                continue
            bersaglio.write_text(testo.replace(vecchio, nuovo), encoding="utf-8")
            esito = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TEST],
                                   cwd=copia, capture_output=True, text=True)
            scoperto = esito.returncode == 1
            print(f"{'✓' if scoperto else '✗'} {nome}" + ("" if scoperto else "  ← nessun test se ne accorge"), flush=True)
            if not scoperto:
                inosservati.append(nome)
    print(f"{len(SABOTAGGI) - len(inosservati)}/{len(SABOTAGGI)} sabotaggi scoperti dai test")
    return 1 if inosservati else 0


if __name__ == "__main__":
    sys.exit(main())
