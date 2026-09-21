#!/usr/bin/env bash
# Open a new shell with .venv on PATH (graph-ted-db, python, pytest).
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/activate ]]; then
  echo "graph-ted-db: no .venv yet. Run ./setup.sh first." >&2
  exit 1
fi

export VIRTUAL_ENV="$PWD/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
unset PYTHONHOME

echo "graph-ted-db CLI is ready. Try:  graph-ted-db --help"
echo "Leave this shell with:  exit"
echo
exec "${SHELL:-bash}" -i
