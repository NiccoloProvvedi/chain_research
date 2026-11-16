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
TEST = ["test/test_validazione.py", "test/test_catena.py", "test/test_banco.py", "test/test_regressioni.py",
        "test/test_raccolta.py", "test/test_ciclo_vita.py", "test/test_indagine.py", "test/test_riprova.py"]
# (nome, file, testo da trovare una sola volta, testo che lo sostituisce)
SABOTAGGI = [
    ("G1 il valore non si confronta più con la riga", "ricerca_catena/web/catena.py",
     "if prova is not None and not validazione.valore_nella_riga(esito.get(\"valore\", \"\"), prova.testo):",
     "if False:"),
    ("G1 i numeri non contano", "ricerca_catena/web/validazione.py",
     "            if intero not in suoi and not a_gruppi:", "            if False:"),
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
    ("G4 confermata anche con il valore da un estratto", "ricerca_catena/web/validazione.py",
     '    if c.get("fonte_valore") == "C":', '    if False:'),
    ("G7 i disaccordi non tolgono il sigillo", "ricerca_catena/web/validazione.py",
     '    for d in c.get("disaccordi") or []:', '    for d in []:'),
    ("G7 ogni riga di Wikipedia è un disaccordo", "ricerca_catena/web/validazione.py",
     " or not _dal_riquadro(a):", ":"),
    ("G7 anche i fratelli sono disaccordi", "ricerca_catena/web/validazione.py",
     "if a.da is not None and not _legame_forte(a)}", "if a.da is not None}"),
    ("anello debole confermato lo stesso", "ricerca_catena/web/validazione.py",
     '    elif c.get("anelli_deboli"):', '    elif False:'),
    ("anello debole che passa anche senza una revisione che regge", "ricerca_catena/web/validazione.py",
     '    if c.get("anelli_deboli") and regge:', '    if c.get("anelli_deboli"):'),
    ("anello debole con revisione che regge dato per verificato", "ricerca_catena/web/validazione.py",
     '        limiti.append(f"{c[\'anelli_deboli\']} anelli con controlli non tutti provati, ma la catena intera regge alla revisione")', '        pass'),
    ("G2/G5 due valori diversi passano per uguali", "ricerca_catena/web/validazione.py",
     "        return (numeri_a == numeri_b and", "        return (True and"),
    ("G2 la seconda lettura non controlla la riga", "ricerca_catena/web/catena.py",
     "                or not validazione.valore_nella_riga(valore, prova.testo) \\\n", ""),
    ("G6 un «no» vale anche senza la citazione nella riga", "ricerca_catena/web/validazione.py",
     "        if esito == \"no\" and valida:", "        if esito == \"no\":"),
    ("stato: le obiezioni della revisione non contano", "ricerca_catena/web/validazione.py",
     "    for o in revisione.get(\"obiezioni\") or []:", "    for o in []:"),
    ("stato: un'altra fonte con un altro valore non conta", "ricerca_catena/web/validazione.py",
     "    if riscontro.get(\"esito\") == \"discordi\":", "    if False:"),
    ("stato: una sola fonte è verificata", "ricerca_catena/web/validazione.py",
     "    if riscontro.get(\"esito\") != \"concordi\":", "    if False:"),
    ("la fiducia la scrive ancora il modello", "ricerca_catena/web/catena.py",
     "        if not self.valore or not self.esami:\n            return testo", "        if True:\n            return testo"),
    ("gli esami non partono mai", "ricerca_catena/web/raccolta.py",
     "            if self.o.get(\"controlli_modello\", True):", "            if False:"),
    ("gli esami partono anche senza budget", "ricerca_catena/web/catena.py",
     "        if self.r.pensatore.bilancio.disponibile(\"revisione\") < self.MINIMO_PER_GLI_ESAMI:", "        if False:"),
    ("la revisione usa il modello della catena", "ricerca_catena/web/catena.py",
     "lambda: {}, modello=modello)", "lambda: {})"),
    ("documento: le incerte arrivano a chi scrive", "ricerca_catena/documento.py",
     "    risolte = [d for d in con_testo if stato_di(d) != \"incerta\"]", "    risolte = list(con_testo)"),
    ("documento: niente riquadro dei punti aperti", "ricerca_catena/documento.py",
     "                f\"non vanno prese come fatti.</p><ul>{voci}</ul></aside>\") if voci else \"\"", "                f\"\") if False else \"\""),
    ("documento: una catena non confermata di prima vale sostenuta", "ricerca_catena/documento.py",
     "    return \"incerta\" if d.get(\"confermata\") is False else \"sostenuta\"", "    return \"sostenuta\""),
    ("regia: riceve la fiducia del modello invece dello stato", "ricerca_catena/indagine.py",
     "                \"stato\": stato_di(d) or esito,", "                \"stato\": riga(risposta, \"Fiducia\"),"),
    ("indagine: lo stato della domanda non si salva", "ricerca_catena/indagine.py",
     "stato_risposta=esito.get(\"stato\"),", "stato_risposta=None,"),
    ("G6 la revisione non vede le altre voci degli anelli", "ricerca_catena/web/catena.py",
     "        righe = self._righe_della_strada() + self._righe_delle_altre_voci(strada)",
     "        righe = self._righe_della_strada()"),
    ("banco: una risposta inventata a una domanda senza risposta vale giusta", "banco/banco.py",
     "voce[\"corretta\"], voce[\"deciso_da\"] = not voce.get(\"trovata\"), \"senza risposta\"",
     "voce[\"corretta\"], voce[\"deciso_da\"] = True, \"senza risposta\""),
    ("indagine: le righe delle fonti non si salvano", "ricerca_catena/indagine.py",
     "prove=(esito.get(\"catena\") or {}).get(\"prove\") or [],", "prove=[],"),
    ("premessa falsa anche senza le parole nella riga", "ricerca_catena/web/validazione.py",
     "        if riga is not None and citazione_presente(str(detta.get(\"citazione\") or \"\"), riga.testo):", "        if True:"),
    ("premessa smentita ma il valore si dà lo stesso", "ricerca_catena/web/catena.py",
     "        if premessa:\n            # La domanda dà per vero", "        if False:\n            # La domanda dà per vero"),
    ("catena respinta ma il valore si dà lo stesso", "ricerca_catena/web/catena.py",
     "        if obiezione:\n            deboli = self._anelli_deboli()", "        if False:\n            deboli = self._anelli_deboli()"),
    ("catena respinta anche con la premessa che regge", "ricerca_catena/web/validazione.py",
     " or (revisione.get(\"premessa\") or {}).get(\"esito\") != \"non provata\":", ":"),
    ("catena respinta anche senza anelli deboli", "ricerca_catena/web/validazione.py",
     "    if not anelli_deboli or not obiezioni or", "    if not obiezioni or"),
    ("documento: le sezioni non si rileggono frase per frase", "ricerca_catena/documento.py",
     "            return controlla_sezione(indagine, s, ingresso, scritta)", "            return scritta"),
    ("documento: un numero che le risposte non danno passa", "ricerca_catena/documento.py",
     "            if mancanti:\n                trovate[frase]", "            if False:\n                trovate[frase]"),
    ("documento: le frasi ancora senza appoggio restano", "ricerca_catena/documento.py",
     "        da_togliere = [f for f, (grado, _m) in ancora.items() if grado == \"non sostenuta\"]", "        da_togliere = []"),
    ("documento: le frasi segnalate da chi rilegge non contano", "ricerca_catena/documento.py",
     "            if isinstance(numero, int) and 1 <= numero <= len(frasi):", "            if False:"),
    ("una sola revisione invece di due", "ricerca_catena/web/catena.py",
     "        letture = max(1, int(self.o.get(\"letture_revisione\", 2)))", "        letture = 1"),
    ("di due revisioni vale la più buona", "ricerca_catena/web/validazione.py",
     "    peggiore = max(fatte, key=lambda r: _SEVERITA.get(r.get(\"regge\"), 1))", "    peggiore = min(fatte, key=lambda r: _SEVERITA.get(r.get(\"regge\"), 1))"),
    ("il nome della partenza non si cerca nelle pagine lette", "ricerca_catena/web/catena.py",
     "        if mancanti:\n            # La domanda chiama per nome", "        if False:\n            # La domanda chiama per nome"),
    ("il nome della partenza vale anche se non è nella domanda", "ricerca_catena/web/validazione.py",
     "    if not nome or nome not in \" \".join(str(domanda or \"\").split()):", "    if not nome:"),
    ("la domanda si cerca una volta sola anche se è incerta", "ricerca_catena/domanda.py",
     "riprova.con_riprova(crea, richiesta.get(\"opzioni\") or {}, pubblica)", "crea(richiesta.get(\"opzioni\") or {}).esegui()"),
    ("seconda ricerca anche per le risposte non incerte", "ricerca_catena/web/riprova.py",
     " or esito.get(\"stato\") != \"incerta\" or not esito.get(\"valore\"):", " or not esito.get(\"valore\"):"),
    ("seconda ricerca con un altro valore: la risposta resta com'era", "ricerca_catena/web/riprova.py",
     "            unito = _con_stato(principale, \"contestata\", motivi)", "            unito = _con_stato(principale, principale[\"stato\"], motivi)"),
    ("seconda ricerca senza guardare il budget rimasto", "ricerca_catena/web/riprova.py",
     "    if budget < BUDGET_MINIMO_USD or minuti < MINUTI_MINIMI:", "    if False:"),
    ("due ricerche uguali ma la risposta resta incerta", "ricerca_catena/web/riprova.py",
     "else \"sostenuta\"\n            motivi = [\"due ricerche", "else principale[\"stato\"]\n            motivi = [\"due ricerche"),
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
