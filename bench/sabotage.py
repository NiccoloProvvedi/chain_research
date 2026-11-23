"""Sabotaggio: spento un controllo alla volta, almeno un test deve fallire (docs/validation.md, T1).

Copia il codice e i test in una cartella temporanea, cambia una riga e lancia i test senza rete: se passano tutti,
quel controllo non è protetto da nessun test. Non tocca il progetto e non chiama Claude.

Uso: venv/bin/python bench/sabotage.py        (esce con 1 se un sabotaggio passa inosservato)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ["test/test_validation.py", "test/test_chain.py", "test/test_bench.py", "test/test_regressions.py",
        "test/test_collector.py", "test/test_lifecycle.py", "test/test_investigation.py", "test/test_retry.py"]
# (nome, file, testo da trovare una sola volta, testo che lo sostituisce)
SABOTAGES = [
    ("G1 il valore non si confronta più con la riga", "chain_research/web/chain.py",
     "if prova is not None and not validazione.valore_nella_riga(esito.get(\"valore\", \"\"), prova.testo):",
     "if False:"),
    ("G1 i numeri non contano", "chain_research/web/validation.py",
     "            if intero not in suoi and not a_gruppi:", "            if False:"),
    ("G1 il mese non conta", "chain_research/web/validation.py",
     "return not mesi or not della_riga or bool(mesi & della_riga)", "return True"),
    ("G3 un antenato può tornare", "chain_research/web/validation.py",
     "        if mia & _identita(nodo):", "        if False:"),
    ("G3 il controllo non è collegato alla frontiera", "chain_research/web/chain.py",
     "            tornati = [a for a in figli if validazione.ripete_un_antenato(a, nodo)]", "            tornati = []"),
    ("G4 il controllo non è collegato alla verifica", "chain_research/web/chain.py",
     " and not validazione.legame_solo_da_estratti(a)]", "]"),
    ("G7 i disaccordi non si registrano", "chain_research/web/chain.py",
     "            if voce not in self.conflitti:", "            if False:"),
    ("G4 un estratto vale come una pagina letta", "chain_research/web/validation.py",
     "    if str(prova.testo).startswith(ESTRATTO):", "    if False:"),
    ("G4 confermata anche con il valore da un estratto", "chain_research/web/validation.py",
     '    if c.get("fonte_valore") == "C":', '    if False:'),
    ("G7 i disaccordi non tolgono il sigillo", "chain_research/web/validation.py",
     '    for d in c.get("disaccordi") or []:', '    for d in []:'),
    ("G7 ogni riga di Wikipedia è un disaccordo", "chain_research/web/validation.py",
     " or not _dal_riquadro(a):", ":"),
    ("G7 anche i fratelli sono disaccordi", "chain_research/web/validation.py",
     "if a.da is not None and not _legame_forte(a)}", "if a.da is not None}"),
    ("anello debole confermato lo stesso", "chain_research/web/validation.py",
     '    elif c.get("anelli_deboli"):', '    elif False:'),
    ("anello debole che passa anche senza una revisione che regge", "chain_research/web/validation.py",
     '    if c.get("anelli_deboli") and regge:', '    if c.get("anelli_deboli"):'),
    ("anello debole con revisione che regge dato per verificato", "chain_research/web/validation.py",
     '        limiti.append(f"{c[\'anelli_deboli\']} anelli con controlli non tutti provati, ma la catena intera regge alla revisione")', '        pass'),
    ("G2/G5 due valori diversi passano per uguali", "chain_research/web/validation.py",
     "        return (numeri_a == numeri_b and", "        return (True and"),
    ("G2 la seconda lettura non controlla la riga", "chain_research/web/chain.py",
     "                or not validazione.valore_nella_riga(valore, prova.testo) \\\n", ""),
    ("G6 un «no» vale anche senza la citazione nella riga", "chain_research/web/validation.py",
     "        if esito == \"no\" and valida:", "        if esito == \"no\":"),
    ("stato: le obiezioni della revisione non contano", "chain_research/web/validation.py",
     "    for o in revisione.get(\"obiezioni\") or []:", "    for o in []:"),
    ("stato: un'altra fonte con un altro valore non conta", "chain_research/web/validation.py",
     "    if riscontro.get(\"esito\") == \"discordi\":", "    if False:"),
    ("stato: una sola fonte è verificata", "chain_research/web/validation.py",
     "    if riscontro.get(\"esito\") != \"concordi\":", "    if False:"),
    ("la fiducia la scrive ancora il modello", "chain_research/web/chain.py",
     "        if not self.valore or not self.esami:\n            return testo", "        if True:\n            return testo"),
    ("gli esami non partono mai", "chain_research/web/collector.py",
     "            if self.o.get(\"controlli_modello\", True):", "            if False:"),
    ("gli esami partono anche senza budget", "chain_research/web/chain.py",
     "        if self.r.pensatore.bilancio.disponibile(\"revisione\") < self.MINIMO_PER_GLI_ESAMI:", "        if False:"),
    ("la revisione usa il modello della catena", "chain_research/web/chain.py",
     "lambda: {}, modello=modello)", "lambda: {})"),
    ("documento: le incerte arrivano a chi scrive", "chain_research/document.py",
     "    risolte = [d for d in con_testo if stato_di(d) != \"incerta\"]", "    risolte = list(con_testo)"),
    ("documento: niente riquadro dei punti aperti", "chain_research/document.py",
     "                f\"non vanno prese come fatti.</p><ul>{voci}</ul></aside>\") if voci else \"\"", "                f\"\") if False else \"\""),
    ("documento: una catena non confermata di prima vale sostenuta", "chain_research/document.py",
     "    return \"incerta\" if d.get(\"confermata\") is False else \"sostenuta\"", "    return \"sostenuta\""),
    ("regia: riceve la fiducia del modello invece dello stato", "chain_research/investigation.py",
     "                \"stato\": stato_di(d) or esito,", "                \"stato\": riga(risposta, \"Fiducia\"),"),
    ("indagine: lo stato della domanda non si salva", "chain_research/investigation.py",
     "stato_risposta=esito.get(\"stato\"),", "stato_risposta=None,"),
    ("G6 la revisione non vede le altre voci degli anelli", "chain_research/web/chain.py",
     "        righe = self._righe_della_strada() + self._righe_delle_altre_voci(strada)",
     "        righe = self._righe_della_strada()"),
    ("banco: una risposta inventata a una domanda senza risposta vale giusta", "bench/bench.py",
     "voce[\"corretta\"], voce[\"deciso_da\"] = not voce.get(\"trovata\"), \"senza risposta\"",
     "voce[\"corretta\"], voce[\"deciso_da\"] = True, \"senza risposta\""),
    ("indagine: le righe delle fonti non si salvano", "chain_research/investigation.py",
     "prove=(esito.get(\"catena\") or {}).get(\"prove\") or [],", "prove=[],"),
    ("premessa falsa anche senza le parole nella riga", "chain_research/web/validation.py",
     "        if riga is not None and citazione_presente(str(detta.get(\"citazione\") or \"\"), riga.testo):", "        if True:"),
    ("premessa smentita ma il valore si dà lo stesso", "chain_research/web/chain.py",
     "        if premessa:\n            # La domanda dà per vero", "        if False:\n            # La domanda dà per vero"),
    ("catena respinta ma il valore si dà lo stesso", "chain_research/web/chain.py",
     "        if obiezione:\n            deboli = self._anelli_deboli()", "        if False:\n            deboli = self._anelli_deboli()"),
    ("catena respinta anche con la premessa che regge", "chain_research/web/validation.py",
     " or (revisione.get(\"premessa\") or {}).get(\"esito\") != \"non provata\":", ":"),
    ("catena respinta anche senza anelli deboli", "chain_research/web/validation.py",
     "    if not anelli_deboli or not obiezioni or", "    if not obiezioni or"),
    ("documento: le sezioni non si rileggono frase per frase", "chain_research/document.py",
     "            return controlla_sezione(indagine, s, ingresso, scritta)", "            return scritta"),
    ("documento: un numero che le risposte non danno passa", "chain_research/document.py",
     "            if mancanti:\n                trovate[frase]", "            if False:\n                trovate[frase]"),
    ("documento: le frasi ancora senza appoggio restano", "chain_research/document.py",
     "        da_togliere = [f for f, (grado, _m) in ancora.items() if grado == \"non sostenuta\"]", "        da_togliere = []"),
    ("documento: le frasi segnalate da chi rilegge non contano", "chain_research/document.py",
     "            if isinstance(numero, int) and 1 <= numero <= len(frasi):", "            if False:"),
    ("una sola revisione invece di due", "chain_research/web/chain.py",
     "        letture = max(1, int(self.o.get(\"letture_revisione\", 2)))", "        letture = 1"),
    ("di due revisioni vale la più buona", "chain_research/web/validation.py",
     "    peggiore = max(fatte, key=lambda r: _SEVERITA.get(r.get(\"regge\"), 1))", "    peggiore = min(fatte, key=lambda r: _SEVERITA.get(r.get(\"regge\"), 1))"),
    ("il nome della partenza non si cerca nelle pagine lette", "chain_research/web/chain.py",
     "        if mancanti:\n            # La domanda chiama per nome", "        if False:\n            # La domanda chiama per nome"),
    ("il nome della partenza vale anche se non è nella domanda", "chain_research/web/validation.py",
     "    if not nome or nome not in \" \".join(str(domanda or \"\").split()):", "    if not nome:"),
    ("la domanda si cerca una volta sola anche se è incerta", "chain_research/question.py",
     "riprova.con_riprova(crea, richiesta.get(\"opzioni\") or {}, pubblica)", "crea(richiesta.get(\"opzioni\") or {}).esegui()"),
    ("seconda ricerca anche per le risposte non incerte", "chain_research/web/retry.py",
     " or esito.get(\"stato\") != \"incerta\" or not esito.get(\"valore\"):", " or not esito.get(\"valore\"):"),
    ("seconda ricerca con un altro valore: la risposta resta com'era", "chain_research/web/retry.py",
     "            unito = _con_stato(principale, \"contestata\", motivi)", "            unito = _con_stato(principale, principale[\"stato\"], motivi)"),
    ("seconda ricerca senza guardare il budget rimasto", "chain_research/web/retry.py",
     "    if budget < BUDGET_MINIMO_USD or minuti < MINUTI_MINIMI:", "    if False:"),
    ("due ricerche uguali ma la risposta resta incerta", "chain_research/web/retry.py",
     "else \"sostenuta\"\n            motivi = [\"due ricerche", "else principale[\"stato\"]\n            motivi = [\"due ricerche"),
    ("giudice: il segno non conta", "bench/judge.py",
     "    return all((n in _negativi(attesa)) == (n in sotto)", "    return True or all((n in _negativi(attesa)) == (n in sotto)"),
    ("giudice: l'errore vale risposta sbagliata", "bench/judge.py",
     "return (None, \"giudice in errore\") if errori == 2 else", "return"),
    ("banco: si estrae sempre con il modello", "bench/judge.py",
     "    return valore or estrai(", "    return estrai("),
    ("durata con le attese del limite", "chain_research/web/collector.py",
     "\"secondi\": round(self._trascorsi())", "\"secondi\": round(time.monotonic() - self._inizio)"),
]


def main() -> int:
    unnoticed = []
    for name, file, old, new in SABOTAGES:
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "project"
            for part in ("chain_research", "test", "bench"):
                shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "pytest.ini", copy / "pytest.ini")
            target = copy / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"? {name}: la riga da cambiare non c'è più (o non è unica): aggiorna bench/sabotage.py")
                unnoticed.append(name)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            outcome = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TEST],
                                   cwd=copy, capture_output=True, text=True)
            caught = outcome.returncode == 1
            print(f"{'✓' if caught else '✗'} {name}" + ("" if caught else "  ← nessun test se ne accorge"), flush=True)
            if not caught:
                unnoticed.append(name)
    print(f"{len(SABOTAGES) - len(unnoticed)}/{len(SABOTAGES)} sabotaggi scoperti dai test")
    return 1 if unnoticed else 0


if __name__ == "__main__":
    sys.exit(main())
