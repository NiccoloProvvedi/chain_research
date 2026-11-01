"""Ricerca a catena: indagini sul web guidate da Claude.

Da un argomento, Claude prepara le domande; la catena (programma + Claude Haiku come lettore) risponde a ognuna con
prove citate; il ciclo si ripete finché Claude ritiene solida la conoscenza; alla fine un documento PDF completo.
"""
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
