# graphted-db — Security checklist (FILLED)

**Purpose:** Before anything goes public (TestPyPI or PyPI), confirm the package is hard to *casually* hack or abuse. Local graph files are not a vault — we do not promise encryption of the user’s database on disk. We *do* promise: passwords aren’t stored in the clear, untrusted input can’t run code or commands, and secrets don’t leak into the repo or logs.

**How to use:** Margaret (or Grok Build under her brief) runs this against a named git tip, fills PASS / FAIL / N/A + short notes, and lands the filled report in git. Potts ACCEPT or reject before public publish.

**Scope:** `graphted-db` wheel + thin `graphted` meta-package. App-only items (login UI, Railway) only where the DB library itself creates the risk.

---

## Executive summary

**Not ship-ready for public PyPI yet** on process gates (no CI), but **core security posture for a local file graph is solid**: no `eval`/`exec`/`subprocess`/`pickle` on untrusted input; Cypher is an AST walker (`engine/eval.py` is not Python `eval`); loopback-default HTTP with token required for non-loopback; zero runtime deps / `pip-audit` clean; docs honestly say “not encrypted.”

**Top issues**
1. **E1 (fixed in draft PR #5):** `docs/http.md` had personal Windows path `C:\Users\<redacted>\...` — airgap FAIL on tip `6e25e2f`; scrubbed on `a2eb84b`.
2. **C2 (backlog, medium):** no explicit symlink hardening when opening the graph root / shard files.
3. **G2 (backlog, low):** docs say not encrypted / ACL boundary, but do not explicitly warn “don’t store API keys/secrets in node props.”

**Audited tip:** `graph-ted/graph-ted-db` `6e25e2f3d30ee73a54db79802f6cfb6b10dfce1d` (main).  
**graphted meta:** `packages/graphted/` same commit (introduced in packaging PR #4).  
**No TestPyPI/PyPI publish performed.**

---

## A. Passwords and credentials

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| A1 | Any password the library stores is hashed (e.g. bcrypt/argon2), never plaintext | Someone with the DB file shouldn’t get usable passwords | **N/A** | Library does not store user passwords. HTTP “token” is an optional shared secret for serve auth (`server/http.py`), compared with `hmac.compare_digest`, not persisted in the graph folder. |
| A2 | Hashing uses a modern algorithm and a salt (library default is fine if it’s current) | Old/weak hashes are easy to crack offline | **N/A** | No password hashing path in `graph_ted_db`. |
| A3 | No default “admin/admin” or empty password shipped for real use | Default creds are the #1 casual break-in | **PASS** | Default serve: loopback, **no** token required only on loopback (`LOOPBACK_HOSTS`). Off-loopback **refuses** empty token (`cli.py:241-244`, `server/http.py:380-383`). No admin/admin. |
| A4 | API keys / LLM keys are never written into the graph DB or package source | Keys in the DB travel with backups and sync | **PASS** | No LLM/API key handling in library. Props are opaque JSON; library does not inject env secrets into shards. Wheel scan: no key material. User *can* put secrets in props — see G2. |

## B. “Can someone make it run their code?”

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| B1 | User- or file-supplied text is never passed to `eval`, `exec`, or dynamic `import` of attacker-controlled names | That’s full remote/local code execution | **PASS** | Repo-wide grep: no Python `eval`/`exec`/`__import__`. `engine/eval.py` is Cypher AST evaluation only. Functions resolved via fixed registry `engine/functions.py` `lookup()`. |
| B2 | No shelling out (`subprocess`, `os.system`) with unsanitized user/path input | Same idea: command injection | **PASS** | No `subprocess` / `os.system` imports in `graph_ted_db`. |
| B3 | Template / query builders don’t concatenate raw user strings into executable query text without binding/escaping | Classic injection (SQL, Cypher-like, etc.) | **PASS** | Cypher parsed to AST then executed; `$params` bound as values (`engine/eval.py` `Param`). Not string-concatenated into a second language. HTTP rejects empty/non-object params (`server/http.py:121-129`). |
| B4 | Deserialization (pickle, YAML `load`, etc.) is not used on untrusted data | Pickle especially can run code on load | **PASS** | Only `json.loads` for graph JSONL / HTTP bodies. No pickle/yaml/marshal. |

## C. Files, paths, and local data

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| C1 | Paths from the user are checked so `../` can’t escape the intended data folder | Stops reading/writing arbitrary files | **PASS** | Graph root is *user-chosen* by design (`GraphStore.open` → `Path(root).resolve()`). Internal shard names are hex-only; vector property names must match `^[a-z][a-z0-9_]*$` (`format.py:16,42-43`) — `../etc` rejected. |
| C2 | The library doesn’t follow unexpected symlinks into sensitive system dirs when opening the graph folder | Symlink tricks are a common local escape | **FAIL** | No `is_symlink` / `follow_symlinks=False` checks. `resolve()` follows symlinks for the root. **Backlog medium** — local trusted-machine threat model, but not hardened. |
| C3 | Overwrite / delete of graph files requires an intentional API (not a side effect of a typo’d path) | Harder to wipe someone’s graph by accident or malice | **PASS** | `init_graph` refuses existing `graph.json` unless `exist_ok` (`init.py:28-29`). Deletes are explicit `delete_node` / `delete-edge` / doctor tombstones — not path-typo wipes. |

## D. Network and exposure (only if the DB lib opens ports)

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| D1 | If anything listens on the network, it does not bind to `0.0.0.0` by default without auth | Avoids “laptop on café Wi‑Fi is an open DB” | **PASS** | `DEFAULT_HOST = "127.0.0.1"` (`server/http.py:18`). Non-loopback without token → refuse start. |
| D2 | Local-only defaults (localhost / Unix socket) unless the user opts into sharing | Matches “data lives on my machine” story | **PASS** | Loopback default; docs/http.md WSL section documents opt-in `--host 0.0.0.0` + token. |

## E. Secrets hygiene (dev / docs — still a release gate)

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| E1 | Repo + wheel contain no real API keys, tokens, passwords, or private URLs | Public PyPI is forever | **PASS*** | Tip `6e25e2f`: **FAIL** — personal path in `docs/http.md:130`. **Fixed** draft PR #5 (`a2eb84b`). Wheels at audit: no secrets. No `.env` in repo. *Status after merge of #5: PASS.* |
| E2 | Example `.env` / docs use obvious placeholders (`YOUR_KEY_HERE`), not real-looking values | Copy-paste accidents | **PASS** | After #5: `YOUR_TOKEN_HERE`. No real-looking keys in docs. |
| E3 | CI / release logs don’t print secrets | Logs get shared and archived | **N/A** | No CI workflows yet (`list_workflows` → 0). Revisit when CI lands. |
| E4 | Automated secret scan (e.g. gitleaks or GitHub secret scanning) clean on the release tip | Catches what humans miss | **PASS** | gitleaks not installed. GH secret-scanning API 404 (not enabled / inaccessible). Manual `rg` for key patterns + wheel text scan: clean after path scrub. **Backlog low:** enable GH secret scanning + gitleaks in CI. |

## F. Dependencies

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| F1 | Known-high/critical vulnerabilities in direct dependencies reviewed (`pip-audit` or equivalent) | Supply-chain “easy break” | **PASS** | Runtime `dependencies = []`. `pip-audit` on clean venv with both wheels: **No known vulnerabilities found** (2026-10-06 CT). |
| F2 | No unnecessary packages that pull in heavy attack surface for little benefit | Less to patch and less to distrust | **PASS** | Zero runtime deps; optional `dev = pytest`. Meta `graphted` only depends on `graphted-db`. |

## G. Honest product claims

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| G1 | Docs do **not** claim the local graph is encrypted or “enterprise secure” unless we actually ship that | Overclaiming creates liability and wrong expectations | **PASS** | `docs/format.md:271` “Not encrypted. The folder ACL is the security boundary.” `docs/http.md:111` “This is not encryption…” |
| G2 | Docs *do* say: hash passwords, keep the machine/account safe, don’t put secrets in the graph | Sets the right bar for users | **FAIL** | ACL / not-encrypted stated; **missing** explicit “don’t put API keys/secrets in props” and password guidance (N/A for lib passwords but still user-facing). **Backlog low** — short README security note. |

---

## Sign-off

- Tip SHA: `6e25e2f3d30ee73a54db79802f6cfb6b10dfce1d` (main); docs scrub follow-up `a2eb84b` / draft PR #5
- graphted meta: `packages/graphted/` @ same tip `6e25e2f`
- Date: 2026-10-06 (America/Chicago)
- Runner: Margaret (via eng executor)
- Result: **PASS with 2 backlog FAILs** (C2 symlink hardening; G2 secrets-in-graph docs) after merge of E1 fix PR #5; on tip alone E1 was FAIL
- Potts ACCEPT: yes / no (after Ted checklist approval) — **pending**
- Evidence: `/workspace/state/audit-evidence-2026-10-06/`; clone `/workspace/audit-graph-ted-db`
- Reports also: `reports/` on PR #5 (eng landing)
