#!/usr/bin/env bash
# Create .venv and install graphted-db (Python 3.10+).
set -euo pipefail
cd "$(dirname "$0")"

pick_python() {
  local cmd
  for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        printf '%s\n' "$cmd"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo "graph-ted-db: need Python 3.10 or newer on PATH (python3 or python)." >&2
  echo "Install it from https://www.python.org/downloads/ then run this script again." >&2
  exit 1
fi

echo "Using $($PY -c 'import sys; print(sys.executable)') ($($PY -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
echo "Creating .venv …"
"$PY" -m venv .venv
# Always use the venv's python so we don't depend on `activate`.
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

echo
echo "Setup finished."
echo "  Start the database:     ./serve.sh"
echo "  Open a CLI prompt:      ./shell.sh"
echo "  Or in this terminal:    source .venv/bin/activate"
echo "                          graphted-db --help"
