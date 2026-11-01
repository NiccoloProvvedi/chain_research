"""Giudice del banco di prova: estrae il valore dato come risposta e lo confronta con quello atteso.

Prima il confronto normalizzato (la risposta attesa compare nella risposta), poi Claude Haiku come giudice con due
esempi (come DeepDive). Claude è chiamato senza strumenti, come nel resto del progetto."""
import re
import unicodedata

from ricerca_catena.modello import claude_json

MODELLO = "haiku"
ESTRAI = ("Estrai dal TESTO il valore che dà come risposta alla DOMANDA: una sola riga con un solo valore (nome, data o "
          "numero), copiato dal testo. Non controllare se è giusto né se la persona o il luogo sono quelli della domanda: "
          "estrai e basta. Scrivi solo NON TROVATA se il testo dice di non avere la risposta, oppure se dà come risposta "
          "due o più valori diversi senza sceglierne uno.")
SISTEMA_GIUDICE = (
    "Sei un giudice. Ti do una domanda, la risposta attesa e la risposta di un modello. Dimmi se la risposta del "
    "modello indica la stessa cosa della risposta attesa (stessa persona, stesso luogo, stessa data al giorno, stesso "
    "numero; lingua, formato e unità di misura possono cambiare). Una risposta più vaga di quella attesa (solo l'anno "
    "invece del giorno, un intervallo) NON è corretta.\n\n"
    "Esempio 1 — attesa: \"24 luglio 1973\"; modello: \"July 24, 1973\" → {\"corretta\": true}\n"
    "Esempio 2 — attesa: \"Mitchell Baker\"; modello: \"Brendan Eich\" → {\"corretta\": false}\n\n"
    'Rispondi solo con JSON: {"corretta": true} oppure {"corretta": false}.')


def normalizza(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", str(testo).lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", " ", "".join(c for c in testo if not unicodedata.combining(c))))


def uguale_normalizzata(attesa: str, data: str) -> bool:
    attesa_n, data_n = normalizza(attesa).strip(), normalizza(data)
    return bool(attesa_n) and re.search(rf"\b{re.escape(attesa_n)}\b", data_n) is not None


def _chiedi(istruzioni: str, testo: str) -> str:
    try:
        return str(claude_json.chiedi(istruzioni, testo, None, "giudice", modello=MODELLO)[0])
    except claude_json.ErroreClaude:
        return ""


def risposta_breve(testo: str) -> str:
    """La riga «Risposta» (o la prima riga non vuota)."""
    trovata = re.search(r"\*\*Risposta:?\*\*:?\s*(.+)", testo)
    if trovata:
        return trovata.group(1).strip()[:500]
    return next((r.strip() for r in testo.splitlines() if r.strip()), "")[:500]


def estrai(domanda: str, testo: str) -> str:
    esito = _chiedi(ESTRAI, f"DOMANDA: {domanda}\n\nDOCUMENTO:\n{testo[:40000]}")
    # La prima riga con un valore: niente premesse come «Dal testo fornito, il valore … è:».
    righe = [r.strip().strip("*").strip() for r in esito.splitlines() if r.strip()]
    riga = next((r for r in righe if not r.endswith(":")), righe[0] if righe else "")
    return "" if re.match(r"(NON|NOT)\s+(TROVATA|FOUND)", riga.upper()) else riga[:300]


def giudica(domanda: str, attesa: str, data: str) -> tuple[bool, str]:
    """(corretta, chi ha deciso): "confronto", "giudice", "vuota" o "giudice senza risposta valida"."""
    if not data:
        return False, "vuota"
    if uguale_normalizzata(attesa, data):
        return True, "confronto"
    testo = f"DOMANDA: {domanda}\nRISPOSTA ATTESA: {attesa}\nRISPOSTA DEL MODELLO: {data}"
    for _ in range(2):
        trovato = re.search(r'"corretta"\s*:\s*(true|false)', _chiedi(SISTEMA_GIUDICE, testo))
        if trovato:
            return trovato.group(1) == "true", "giudice"
    return False, "giudice senza risposta valida"
