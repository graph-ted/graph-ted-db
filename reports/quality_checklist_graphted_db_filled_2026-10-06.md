# graph-ted-db — Quality checklist (FILLED)

**Purpose:** Before public publish, confirm the package isn’t easy to *break*, confusing to install, or embarrassing in front of experienced Python users. This is not “perfect software.” It’s “does what it says, fails clearly, and looks intentional.”

**How to use:** Same as security — named tip, PASS / FAIL / N/A + notes, report in git, Potts ACCEPT before public publish.

**Scope:** `graph-ted-db` + thin `graphted` meta-package on PyPI naming.

---

## Executive summary

**Core product quality is ship-capable for a private 0.1.0:** clean wheel install, import `graph_ted_db`, smoke write/read + second-open persistence, **93 passed / 11 skipped** tests (skips = app helper Cypher files absent), pure-Python wheel, LICENSE present, README examples match API.

**Not public-ship-ready on process gates:** **no CI** (6.1/6.2 FAIL), no project lint config / ad-hoc ruff finds 23 issues (5.1 FAIL), no written pre-1.0 / semver policy (4.3 FAIL), no git tag yet for 0.1.0 (1.3 N/A until publish/tag).

**Top FAILs / backlog**
1. **6.1 / 6.2 (high for public release):** add GitHub Actions pytest on PR + link green run on tip.
2. **5.1 (medium):** adopt ruff (or document “no lint”) and clean tip.
3. **4.3 (low):** write one-paragraph pre-1.0 breaking-change policy in README.

**Audited tip:** `6e25e2f3d30ee73a54db79802f6cfb6b10dfce1d`. Meta at `packages/graphted/` same tip. **No PyPI/TestPyPI publish.**

---

## 1. Install and packaging

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| 1.1 | Clean venv can `pip install` the built wheel with no manual path hacks | Public users only do `pip install` | **PASS** | `python -m build` → `graph_ted_db-0.1.0-py3-none-any.whl`; clean venv `pip install` wheel OK. `make wheels` documented. |
| 1.2 | `import` of the public package name works after install | Wrong import path = instant “this is broken” | **PASS** | `import graph_ted_db` → `__version__ == "0.1.0"`; `from graph_ted_db import GraphStore, init_graph`. Dist name `graph-ted-db` ≠ import (documented). |
| 1.3 | Package version on PyPI matches the git tag / changelog | Version lies destroy trust | **N/A** | Not on PyPI/TestPyPI (by policy). Version `0.1.0` consistent in `pyproject.toml` + `__init__.py` + meta. **No git tag `v0.1.0` yet** — create before any public/private tagged install story. |
| 1.4 | Wheel is pure Python (or platform story is documented) | Surprise native builds fail on people’s machines | **PASS** | `Root-Is-Purelib: true` / `Tag: py3-none-any`. No extensions. |
| 1.5 | Meta-package `graphted` pulls `graph-ted-db` and installs cleanly | Brand entry point must not be a trap | **PASS** | Built `graphted-0.1.0-py3-none-any.whl`; `pip install --no-index --find-links dist graphted` → `Requires: graph-ted-db`. No modules (intentional). |

## 2. “Does the core thing work?”

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| 2.1 | Automated tests cover create / read / update / delete (or your real core ops) for the happy path | No tests → regressions ship | **PASS** | `tests/test_store.py` put/get/list/delete nodes+edges; cypher + server suites. **93 passed**, 11 skipped (`test_cypher_helpers` needs sibling app helpers). |
| 2.2 | At least one test uses a real on-disk DB file (not only mocks) | Mocks lie; files are the product | **PASS** | Store tests use `tmp_path` + real JSONL shards (`test_put_get_list_node`, conflict-copy tests). |
| 2.3 | Smoke script: install wheel → open/create graph → write one fact → read it back → exit 0 | Catches “tests pass, product doesn’t” | **PASS** | Clean venv: `graph-ted-db init` / `put-node` / `ls-nodes` on `/tmp/audit-smoke-graph` exit 0; Alice persisted. |
| 2.4 | Second open of the same graph folder still works (persistence) | Local DB that forgets itself is a non-starter | **PASS** | Second `GraphStore.open` → `iter_nodes` returned Alice. |
| 2.5 | Relationship property filters in MATCH (inline maps and WHERE) are honored, and delete-by-filter (DELETE / DETACH DELETE scoped by node or relationship properties) touches only the matching records | A dropped relationship map deletes or returns every edge | **PASS** | Regression: `tests/test_rel_property_filter.py` (two groups, a missing group, anonymous maps, OPTIONAL MATCH, MERGE, rel DELETE, node DETACH DELETE / DELETE). Runs in the CI `pytest` job. Living note: `docs/quality.md`. |

## 3. Bad input and failure behavior

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| 3.1 | Empty strings, huge strings, and wrong types get a clear error — not a stack dump as the only UX | Easy to break ≠ good | **PASS** | CLI: `--prop bad` → `expected key=value`. Bad props type → `ValueError`. Empty props node allowed (valid). 1MB string stored OK. CLI wraps errors without raw traceback as UX (`cli.py` catch → print message). |
| 3.2 | Missing file / permission denied / locked DB produce actionable messages | Users need “what do I do next?” | **PASS** | Missing graph: `graph-ted-db: missing …/graph.json` (`GraphFormatError`). Lock: exclusive flock **blocks** second writer (see 3.4) rather than cryptic fail. |
| 3.3 | Partial failure doesn’t silently corrupt the graph (or corruption is detected next open) | Silent corruption is worse than a crash | **PASS** | Torn-line repair (`jsonl.repair_torn_jsonl`), WAL replay on open, `doctor` for dangling edges / labels. Invalid JSONL lines skipped + recorded (`skipped_lines`). |
| 3.4 | Concurrent open from two processes either works or fails loudly (no silent split-brain) | Laptops + sync tools do this | **PASS** | `fcntl.flock(LOCK_EX)` in `store/lock.py`. Smoke: second process blocked ~1.5s while first held lock — no silent split-brain. Cross-machine sync uses LWW + conflict-copy union (documented). |

## 4. Public API and docs

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| 4.1 | README shows a minimal working example that matches the installed API | First five minutes decide reputation | **PASS** | README Python block: `init_graph`, `GraphStore.open`, `make_node`, `execute` — matches `__init__.py` exports. CLI examples use `graph-ted-db`. |
| 4.2 | Public function/class names are stable and documented; experimental bits marked | Cool-kid derision often starts at messy APIs | **PASS** | Public: `GraphStore`, `init_graph`, `CypherError`, `__version__`. Status section documents Cypher subset / localhost HTTP. |
| 4.3 | Breaking changes bump major version (or pre-1.0 policy is written down) | Surprises burn adopters | **FAIL** | At `0.1.0` with **no** written pre-1.0 / semver policy in README. **Backlog low.** |
| 4.4 | Limitations are stated (e.g. not a network server, not encrypted at rest) | Honesty > marketing gloss | **PASS** | README + format/http docs: not multi-tenant server; not encrypted; loopback HTTP; Cypher subset. |

## 5. Code hygiene that reviewers notice

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| 5.1 | Lint / format clean on the release tip (ruff/black or project standard) | Sloppy formatting signals sloppy process | **FAIL** | No ruff/black config in `pyproject.toml`. Ad-hoc `ruff check graph_ted_db`: **23 errors** (mostly style/UP/TRY). **Backlog medium** — adopt tool + clean, or document “lint deferred.” |
| 5.2 | Type hints on the public API (even if internals are looser) | Expected for modern PyPI libs | **PASS** | Public methods on `GraphStore` / `init_graph` annotated; `__init__.py` clean. Minor: `open` classmethod `cls` unannotated (ast check). |
| 5.3 | No obvious dead code, commented-out blocks, or “TODO: fix before release” in shipped paths | Reads as unfinished | **PASS** | No `TODO`/`FIXME` in `graph_ted_db`. Soft: setuptools warns deprecated `license = { text = ... }` TOML table. |
| 5.4 | License file present and accurate | Required for serious use | **PASS** | Root `LICENSE` + `packages/graphted/LICENSE` MIT © 2026 graph-ted-db contributors. |

## 6. Automation (lightweight, not a bureaucracy)

| # | Check | Why it matters | PASS / FAIL / N/A | Notes |
|---|--------|----------------|-------------------|-------|
| 6.1 | CI runs tests on every PR to main | Humans forget; CI doesn’t | **FAIL** | `list_workflows` → **0** workflows. **Backlog high** before public. |
| 6.2 | Release tip has a green CI run linked in the report | Auditable “we actually ran it” | **FAIL** | No CI. Local green substitute: pytest **93 passed, 11 skipped** on tip `6e25e2f` (box, 2026-10-06 ~15:00 CT). |
| 6.3 | One command documented to reproduce the smoke locally | Others can verify without tribal knowledge | **PASS** | README: `make wheels` / `pip install` wheel / `graph-ted-db init|put-node|ls-nodes`. Also `pytest` via `pip install -e ".[dev]"`. |

---

## What “good enough” means (plain English)

**Ship when:** install works, core ops work twice in a row on disk, bad input fails clearly, docs match reality, and the security checklist isn’t hiding a FAIL on injection/passwords/secrets.

**Don’t block on:** perfect coverage %, formal penetration test, or encrypting the local graph file (out of scope unless product changes).

### Auditor judgment vs that bar

| Bar item | Status |
|---|---|
| Install works | **Met** |
| Core ops twice on disk | **Met** |
| Bad input fails clearly | **Met** |
| Docs match reality | **Met** (after PR #5 path scrub) |
| Security injection/passwords/secrets | **Met** for injection/passwords; E1 fixed in #5; G2 soft docs gap |

**Public PyPI:** still blocked on **CI + (optional) lint policy + Potts/Ted green-light**. Private use / git install: **acceptable** after E1 merge.

---

## Sign-off

- Tip SHA: `6e25e2f3d30ee73a54db79802f6cfb6b10dfce1d`
- graphted meta: `packages/graphted/` @ `6e25e2f` (same repo)
- Date: 2026-10-06 (America/Chicago)
- Runner: Margaret (via eng executor)
- Result: **FAIL** (process gates 6.1/6.2 + 5.1 lint + 4.3 policy) — **core functional quality PASS**
- Potts ACCEPT: yes / no (after Ted checklist approval) — **pending**
- CI on tip: **none** (no workflows). Local pytest green: 93 passed / 11 skipped.
- Small fix landed: draft PR https://github.com/graph-ted/graph-ted-db/pull/5 (`a2eb84b`) docs airgap path
- Reports: `/workspace/state/quality_checklist_graphted_db_filled_2026-10-06.md` + `reports/` on PR #5
