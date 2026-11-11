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


# Un meno davanti a un numero: non il trattino di un intervallo («00118–00199») né di una data («1973-07-24»).
_NEGATIVO = re.compile(r"(?<![\w.,])(?<!\d\s)[−–-]\s?(\d[\d.,]*)")
_SOTTO_IL_MARE = re.compile(r"sotto il livello del mare|below sea level", re.I)


def _cifre(numero: str) -> str:
    return numero.rstrip(".,")


def _negativi(testo: str) -> set[str]:
    return {_cifre(n) for n in _NEGATIVO.findall(str(testo))}


def uguale_normalizzata(attesa: str, data: str) -> bool:
    """La risposta attesa compare nella risposta data, con lo stesso segno dei numeri (11/11, domande di controllo:
    «2 m» attesa, «−2 m s.l.m.» data, contata giusta)."""
    attesa_n, data_n = normalizza(attesa).strip(), normalizza(data)
    if not attesa_n or re.search(rf"\b{re.escape(attesa_n)}\b", data_n) is None:
        return False
    sotto = _negativi(data) | ({_cifre(n) for n in re.findall(r"\d[\d.,]*", str(data))}
                               if _SOTTO_IL_MARE.search(str(data)) else set())
    return all((n in _negativi(attesa)) == (n in sotto) for n in map(_cifre, re.findall(r"\d[\d.,]*", str(attesa))))


def _chiedi(istruzioni: str, testo: str) -> str:
    """Gli errori di Claude salgono: una chiamata fallita non è una risposta sbagliata (11/11)."""
    return str(claude_json.chiedi(istruzioni, testo, None, "giudice", modello=MODELLO)[0])


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


def finale_di(esito: dict, domanda: str) -> str:
    """Il valore da giudicare: quello trovato dal programma; solo se manca, quello che Haiku estrae dalla riga
    «Risposta» (11/11: «00118–00199» e «15 ottobre 1870», giusti, contati sbagliati perché l'estrazione era vuota)."""
    valore = " ".join(str(esito.get("valore") or "").split())
    return valore or estrai(domanda, risposta_breve(esito.get("risposta") or ""))


def giudica(domanda: str, attesa: str, data: str) -> tuple[bool | None, str]:
    """(corretta, chi ha deciso): "confronto", "giudice", "vuota", "giudice senza risposta valida"; (None, "giudice
    in errore") se Claude non ha risposto: la domanda resta da giudicare. Il limite di sessione sale a chi chiama."""
    if not data:
        return False, "vuota"
    if uguale_normalizzata(attesa, data):
        return True, "confronto"
    testo = f"DOMANDA: {domanda}\nRISPOSTA ATTESA: {attesa}\nRISPOSTA DEL MODELLO: {data}"
    errori = 0
    for _ in range(2):
        try:
            risposta = _chiedi(SISTEMA_GIUDICE, testo)
        except claude_json.LimiteSessione:
            raise
        except claude_json.ErroreClaude:
            errori += 1
            continue
        trovato = re.search(r'"corretta"\s*:\s*(true|false)', risposta)
        if trovato:
            return trovato.group(1) == "true", "giudice"
    return (None, "giudice in errore") if errori == 2 else (False, "giudice senza risposta valida")
