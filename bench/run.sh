#!/usr/bin/env bash
# Bench runs detached from the session: they go on even if the terminal (or Claude Code) is closed.
#
# Usage: bench/run.sh <code folder> <name>[,<name>...] [bench.py options]
#   e.g. bench/run.sh ../ricerca-catena-bench-c control-c1,control-c2 --questions bench/questions_holdout.json
# The runs go one after another, from the given folder (a frozen copy of the code: `git worktree add`), waiting for
# the session limit. Log: results/bench/<name>/run.log; process: results/bench/runs.pid.
# An interrupted run resumes from where it stopped: just relaunch the same command. To stop:
#   kill -- -$(cat results/bench/runs.pid)
# With LOW_PRIORITY=1 before the command the runs do not disturb whoever is using the computer.
# Uses the Claude subscription.
set -euo pipefail
code=$(cd "$1" && pwd); names=$2; shift 2
root=$(cd "$(dirname "$0")/.." && pwd)
pid="$root/results/bench/runs.pid"
if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
  echo "a run is already in progress (process $(cat "$pid"))" >&2; exit 1
fi
mkdir -p "$root/results/bench"
# LOW_PRIORITY=1: the computer is needed for other things (CPU and disk at the lowest priority); usually with --parallel 1.
prefix=""
[ -n "${LOW_PRIORITY:-}" ] && prefix="nice -n 19 ionice -c 3 "
cmd=""
for name in ${names//,/ }; do
  mkdir -p "$root/results/bench/$name"
  cmd+="${prefix}venv/bin/python bench/bench.py --name $(printf %q "$name") $(printf '%q ' "$@") >> $(printf %q "$root/results/bench/$name/run.log") 2>&1; "
done
cd "$code"
CHAIN_WAIT_FOR_LIMIT=1 setsid nohup bash -c "$cmd" > /dev/null 2>&1 < /dev/null &
echo $! > "$pid"
echo "runs $names started from $code (process $!); log in results/bench/<name>/run.log"
