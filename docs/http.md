# HTTP daemon

Optional. Everyday open / put / get is the in-process Python API in [Getting started](getting-started.md) (`GraphStore.open`, `make_node`, `get_node`). This daemon is localhost JSON over HTTP so another process can run openCypher queries without importing `graph_ted_db`. One process owns the graph folder and the process lock. It is plain JSON over HTTP, not a hosted multi-tenant server.

Default bind: `127.0.0.1:8099` (not 7474/7687, so it can run next to another graph database on the same machine).

First-time clone: run `setup.bat` (Windows) or `./setup.sh` (macOS/Linux), then `serve.bat` / `./serve.sh`. Or:

```bash
graph-ted-db init ./my-graph --name demo
graph-ted-db put-node ./my-graph --label Entity --prop name=Alice --prop group_id=default
graph-ted-db serve ./my-graph
```

A client stores `http://127.0.0.1:8099` as its connection URL.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` or `/` | Liveness + graph id/name. Use it (or `RETURN 1 AS ok`) as a connection test. |
| `GET` | `/info` | `graph.json` fields plus live node/edge counts |
| `POST` | `/cypher` | One query (`query`) or an atomic batch (`statements`). Writes allowed. |
| `GET` | `/cypher?query=…` | Read convenience for curl. Mutating queries return `405`. `params` is a JSON object string |

### `POST /cypher`

Request:

```json
{
  "query": "MATCH (n:Entity {group_id: $group_id}) RETURN n.name AS name",
  "parameters": {"group_id": "default"},
  "updated_by": "alice"
}
```

`params` is accepted as an alias of `parameters`. Optional `updated_by` is stored on records written by this request (default `"cypher"`).

`GET /cypher` is read-only. Queries whose text contains (case-insensitive, word-boundary) `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, or `FOREACH` are rejected with `405`. Use `POST /cypher` for writes.

Response:

```json
{
  "records": [{"name": "Alice"}],
  "truncated": false
}
```

Results are truncated after 500 rows (`--max-records`). Send `"max_records": 0` to raise the cap (hard max 100000). Query errors are `400` with `{"error": "…", "type": "cypher"}`.

### Atomic batch (`statements`)

One lock, one transaction. All statements commit or none do. Use it when several writes must land together rather than as a sequence of auto-commit POSTs.

```json
{
  "statements": [
    {"query": "MERGE (n:Entity {uuid: $uuid}) SET n.name = $name", "parameters": {"uuid": "…", "name": "Alice"}},
    {"query": "RETURN 1 AS ok"}
  ]
}
```

Response:

```json
{
  "results": [
    {"records": [], "truncated": false},
    {"records": [{"ok": 1}], "truncated": false}
  ],
  "truncated": false
}
```

### Connection check

```bash
curl -s http://127.0.0.1:8099/health
curl -s http://127.0.0.1:8099/cypher \
  -H 'Content-Type: application/json' \
  -d '{"query":"RETURN 1 AS ok"}'
```

## What you can test from a shell

No client application is needed. Against a folder:

1. **CLI CRUD** — `put-node` / `ls-nodes` / `put-edge` / `delete-node` / `doctor`
2. **CLI queries** — `graph-ted-db cypher ./my-graph 'MATCH (n) RETURN n.name AS name'`
3. **This daemon** — `serve` + the curls above

Writes through openCypher queries include `CREATE` / `MERGE` / `SET` / `DELETE` / `DETACH DELETE` (POST only). The optional Graphiti driver (`GraphTedDbDriver`) accepts an `http://` URL for this daemon. Opening `GraphStore` in-process remains available for library and CLI use.

`put-node` / `put-edge` from another terminal (a second process) are visible on the **next** `/cypher` request. The daemon reloads its catalog when shard files change; you do not need to restart `serve`.

## Auth

Loopback binds (`127.0.0.1`, `localhost`, `::1`) may run with no token.

Binding any other host requires a token: `--token` or env `GRAPH_TED_DB_TOKEN`. Serve refuses to start (exit 1) if the host is off-loopback and the token is empty. Default bind remains `127.0.0.1`.

When a token is configured, every route except `GET /health` requires one of:

- `Authorization: Bearer <token>`
- `X-Graph-Ted-Token: <token>`

Missing or wrong token is `401`. The token protects the *port*, not the files on disk — see [Security](security.md). This is not at-rest encryption and not a hosted multi-tenant server.

If `GET /health` shows a different `name`/`id` than the serve banner, another `graph-ted-db serve` is still bound to 8099 (common on Windows). Stop it, then start once:

```bat
netstat -ano | findstr :8099
taskkill /PID <pid> /F
```

Only `serve.bat` / `serve.sh` / `graph-ted-db serve` start the daemon. `setup.*` and `shell.*` do not.

## WSL curl → Windows serve

WSL2 `127.0.0.1` is the Linux VM, not Windows. A Windows `serve` bound to `127.0.0.1` is invisible from WSL. For dev:

1. Do **not** run `serve` in WSL (nothing else on 8099 there).
2. On **Windows**, bind all interfaces and pass a token (`--host 0.0.0.0` without `--token` or `GRAPH_TED_DB_TOKEN` is refused):

```bat
graph-ted-db serve --host 0.0.0.0 --token YOUR_TOKEN_HERE D:\graphs\test-graph
```

3. In **WSL**, curl the Windows host, not localhost. `GET /health` stays open; other routes need the token:

```bash
WIN=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
curl -s "http://$WIN:8099/health"
curl -s "http://$WIN:8099/cypher" -H "Authorization: Bearer $GRAPH_TED_DB_TOKEN" \
  -H "Content-Type: application/json" -d '{"query":"RETURN 1 AS ok"}'
```

If that times out, allow TCP 8099 in Windows Firewall (WSL uses a virtual adapter, so loopback-only rules are not enough):

```bat
netsh advfirewall firewall add rule name="graph-ted-db 8099" dir=in action=allow protocol=TCP localport=8099
```

Optional: Windows 11 WSL mirrored networking (`%UserProfile%\.wslconfig`) makes `localhost` the same on both sides — then keep a **single** serve, still on Windows:

```
[wsl2]
networkingMode=mirrored
```

Restart WSL (`wsl --shutdown`) after changing that. If a WSL `serve` is also running, curl will hit WSL again.
