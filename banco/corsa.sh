#!/usr/bin/env bash
# Corse del banco staccate dalla sessione: continuano anche se il terminale (o Claude Code) si chiude.
#
# Uso: banco/corsa.sh <cartella del codice> <nome>[,<nome>...] [opzioni di banco.py]
#   es. banco/corsa.sh ../ricerca-catena-banco-c controllo-c1,controllo-c2 --domande banco/domande_holdout.json
# Le corse vanno una dopo l'altra, dalla cartella indicata (una copia ferma del codice: `git worktree add`), con
# l'attesa del limite di sessione. Registro: risultati/banco/<nome>/corsa.log; processo: risultati/banco/corse.pid.
# Una corsa interrotta riparte da dove era arrivata: basta rilanciare lo stesso comando. Per fermare:
#   kill -- -$(cat risultati/banco/corse.pid)
# Consuma l'abbonamento di Claude.
set -euo pipefail
codice=$(cd "$1" && pwd); nomi=$2; shift 2
radice=$(cd "$(dirname "$0")/.." && pwd)
pid="$radice/risultati/banco/corse.pid"
if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
  echo "c'è già una corsa in esecuzione (processo $(cat "$pid"))" >&2; exit 1
fi
mkdir -p "$radice/risultati/banco"
comando=""
for nome in ${nomi//,/ }; do
  mkdir -p "$radice/risultati/banco/$nome"
  comando+="venv/bin/python banco/banco.py --nome $(printf %q "$nome") $(printf '%q ' "$@") >> $(printf %q "$radice/risultati/banco/$nome/corsa.log") 2>&1; "
done
cd "$codice"
RICERCA_ATTENDI_LIMITE=1 setsid nohup bash -c "$comando" > /dev/null 2>&1 < /dev/null &
echo $! > "$pid"
echo "corse $nomi avviate da $codice (processo $!); registro in risultati/banco/<nome>/corsa.log"
