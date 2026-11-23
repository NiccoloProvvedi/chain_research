#!/usr/bin/env bash
# Corse del banco staccate dalla sessione: continuano anche se il terminale (o Claude Code) si chiude.
#
# Uso: bench/run.sh <cartella del codice> <nome>[,<nome>...] [opzioni di banco.py]
#   es. bench/run.sh ../ricerca-catena-banco-c controllo-c1,controllo-c2 --domande bench/questions_holdout.json
# Le corse vanno una dopo l'altra, dalla cartella indicata (una copia ferma del codice: `git worktree add`), con
# l'attesa del limite di sessione. Registro: risultati/bench/<nome>/run.log; processo: results/bench/runs.pid.
# Una corsa interrotta riparte da dove era arrivata: basta rilanciare lo stesso comando. Per fermare:
#   kill -- -$(cat results/bench/runs.pid)
# Con BASSA_PRIORITA=1 davanti al comando le corse non disturbano chi usa il computer.
# Consuma l'abbonamento di Claude.
set -euo pipefail
codice=$(cd "$1" && pwd); nomi=$2; shift 2
radice=$(cd "$(dirname "$0")/.." && pwd)
pid="$radice/results/bench/runs.pid"
if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
  echo "c'è già una corsa in esecuzione (processo $(cat "$pid"))" >&2; exit 1
fi
mkdir -p "$radice/risultati/banco"
# BASSA_PRIORITA=1: il computer serve ad altro (CPU e disco alla priorità più bassa); di solito con --parallelo 1.
prefisso=""
[ -n "${BASSA_PRIORITA:-}" ] && prefisso="nice -n 19 ionice -c 3 "
comando=""
for nome in ${nomi//,/ }; do
  mkdir -p "$radice/risultati/bench/$nome"
  comando+="${prefisso}venv/bin/python bench/bench.py --nome $(printf %q "$nome") $(printf '%q ' "$@") >> $(printf %q "$radice/risultati/bench/$nome/run.log") 2>&1; "
done
cd "$codice"
CHAIN_WAIT_FOR_LIMIT=1 setsid nohup bash -c "$comando" > /dev/null 2>&1 < /dev/null &
echo $! > "$pid"
echo "corse $nomi avviate da $codice (processo $!); registro in risultati/bench/<nome>/run.log"
