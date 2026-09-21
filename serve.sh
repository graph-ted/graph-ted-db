#!/usr/bin/env bash
# Start the localhost HTTP daemon (http://127.0.0.1:8099).
# Usage: ./serve.sh [graph-folder]
#   no argument → ./my-graph (created if missing)
#   with a path  → that folder must already be a graph (graph.json)
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/graph-ted-db ]]; then
  echo "graph-ted-db: no .venv yet. Run ./setup.sh first." >&2
  exit 1
fi

if [[ "${1-}" == "" ]]; then
  GRAPH="$(pwd)/my-graph"
  if [[ ! -f "$GRAPH/graph.json" ]]; then
    echo "Creating graph folder: $GRAPH"
    .venv/bin/graph-ted-db init "$GRAPH" --name demo --exist-ok
  fi
else
  GRAPH="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  if [[ ! -f "$GRAPH/graph.json" ]]; then
    echo "graph-ted-db: no graph.json in $GRAPH" >&2
    echo "Init it first, e.g.:  graph-ted-db init \"$GRAPH\" --name test" >&2
    echo "Or pass the same folder you use with ls-nodes / put-node." >&2
    exit 1
  fi
fi

echo "Serving $GRAPH  →  http://127.0.0.1:8099"
echo "Stop with Ctrl+C. In another window try:"
echo "  curl -s http://127.0.0.1:8099/health"
echo
exec .venv/bin/graph-ted-db serve "$GRAPH"
