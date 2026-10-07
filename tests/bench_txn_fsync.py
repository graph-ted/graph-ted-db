"""Write-heavy fsync micro-benchmark. Not collected by pytest.

Compares the old per-record flush (one WAL replace, then fsync each record and
labels.json) with:

- one transaction of 200 single-node creates
- 200 autocommit single-statement creates
- 200 autocommit statements that each write a node and an edge
- 20 Graphiti ``add_episode``-shaped mixes (one 4-statement batch plus the
  saga autocommit saves)

The batch shape matches graphiti-core 0.30.2 ``add_nodes_and_edges_bulk_tx``
on the Neo4j provider: four ``tx.run`` calls (episodic node, entity nodes,
MENTIONS, RELATES_TO) inside one ``execute_write``. The HTTP driver sends
that as one ``post_cypher_many``, and the server runs it as one transaction.
"""

from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from graph_ted_db.store import GraphStore, init_graph
from graph_ted_db.store.jsonl import append_jsonl, replace_json_file, replace_jsonl

N = 200
ROUNDS = 5
EPISODES = 20
ENTITIES = 8
RELATES = 6


def _uid(i: int, kind: int = 1) -> str:
    return f"{(i % 256):02x}{(i // 256):06x}-0000-4000-8000-{kind:012d}"


def bench_per_record(n: int) -> float:
    root = Path(tempfile.mkdtemp())
    nodes = root / "nodes"
    nodes.mkdir()
    labels = root / "labels.json"
    wal = root / "wal.jsonl"
    line = '{"id":"x","v":1,"labels":["Entity"],"props":{"name":"n"}}'
    started = time.perf_counter()
    replace_jsonl(wal, [line] * n)
    for i in range(n):
        append_jsonl(nodes / f"{i % 256:02x}.jsonl", line)
        replace_json_file(labels, {"node_labels": ["Entity"]})
    wal.unlink()
    return time.perf_counter() - started


def _open() -> GraphStore:
    root = Path(tempfile.mkdtemp()) / "g"
    data = Path(tempfile.mkdtemp())
    init_graph(root, name="bench")
    return GraphStore.open(root, data_dir=data)


def bench_transaction(n: int) -> float:
    store = _open()
    statements = [
        (
            "CREATE (:Entity {uuid: $uuid, name: $name})",
            {"uuid": _uid(i), "name": f"n{i}"},
        )
        for i in range(n)
    ]
    started = time.perf_counter()
    store.execute_many(statements)
    elapsed = time.perf_counter() - started
    if len(list(store.iter_nodes())) != n:
        raise RuntimeError("transaction did not persist every node")
    return elapsed


def bench_autocommit(n: int) -> float:
    store = _open()
    started = time.perf_counter()
    for i in range(n):
        store.execute(
            "CREATE (:Entity {uuid: $uuid, name: $name})",
            {"uuid": _uid(i), "name": f"n{i}"},
        )
    elapsed = time.perf_counter() - started
    if len(list(store.iter_nodes())) != n:
        raise RuntimeError("autocommit did not persist every node")
    return elapsed


def bench_autocommit_node_edge(n: int) -> float:
    """Each statement writes one new node and one edge to a pre-created anchor."""
    store = _open()
    anchor = _uid(0, 9)
    store.execute(
        "CREATE (:Entity {uuid: $uuid, name: 'anchor'})",
        {"uuid": anchor},
    )
    started = time.perf_counter()
    for i in range(n):
        store.execute(
            "MATCH (anchor:Entity {uuid: $anchor}) "
            "CREATE (a:Entity {uuid: $node, name: $name}) "
            "CREATE (a)-[:RELATES_TO {uuid: $edge}]->(anchor)",
            {
                "node": _uid(i, 2),
                "name": f"n{i}",
                "edge": _uid(i, 3),
                "anchor": anchor,
            },
        )
    elapsed = time.perf_counter() - started
    nodes = len(list(store.iter_nodes()))
    edges = len(list(store.iter_edges()))
    if nodes != n + 1 or edges != n:
        raise RuntimeError(f"node+edge autocommit persisted {nodes} nodes, {edges} edges")
    return elapsed


def _episode_batch(episode: int) -> list[tuple[str, dict[str, object]]]:
    episodic = _uid(episode, 1)
    entities = [_uid(episode * ENTITIES + slot, 2) for slot in range(ENTITIES)]
    entity_cypher = "\n".join(
        f"CREATE (:Entity {{uuid: $u{slot}, name: $name{slot}}})"
        for slot in range(ENTITIES)
    )
    entity_params: dict[str, object] = {}
    for slot, uuid in enumerate(entities):
        entity_params[f"u{slot}"] = uuid
        entity_params[f"name{slot}"] = f"e{episode}n{slot}"
    mentions = "\n".join(
        "MATCH (ep:Episodic {uuid: $episode}) "
        f"MATCH (n{slot}:Entity {{uuid: $u{slot}}}) "
        f"CREATE (ep)-[:MENTIONS {{uuid: $m{slot}}}]->(n{slot})"
        for slot in range(ENTITIES)
    )
    mention_params: dict[str, object] = {"episode": episodic}
    for slot, uuid in enumerate(entities):
        mention_params[f"u{slot}"] = uuid
        mention_params[f"m{slot}"] = _uid(episode * ENTITIES + slot, 3)
    relates = "\n".join(
        f"MATCH (a{slot}:Entity {{uuid: $a{slot}}}) "
        f"MATCH (b{slot}:Entity {{uuid: $b{slot}}}) "
        f"CREATE (a{slot})-[:RELATES_TO {{uuid: $r{slot}}}]->(b{slot})"
        for slot in range(RELATES)
    )
    relate_params: dict[str, object] = {}
    for slot in range(RELATES):
        relate_params[f"a{slot}"] = entities[slot]
        relate_params[f"b{slot}"] = entities[slot + 1]
        relate_params[f"r{slot}"] = _uid(episode * RELATES + slot, 4)
    return [
        (
            "CREATE (:Episodic {uuid: $uuid, name: $name})",
            {"uuid": episodic, "name": f"ep{episode}"},
        ),
        (entity_cypher, entity_params),
        (mentions, mention_params),
        (relates, relate_params),
    ]


def bench_add_episode_mix(episodes: int = EPISODES) -> tuple[float, float, float]:
    """One bulk transaction per episode, then the saga autocommit saves.

    Returns ``(total, batch, autocommit)`` seconds. The first episode creates
    the saga node and one HAS_EPISODE edge. Each later episode updates the
    saga node and writes NEXT_EPISODE plus HAS_EPISODE. That is the
    graphiti-core saga tail; the default ``add_episode`` with ``saga=None``
    is the batch alone.
    """
    store = _open()
    saga = _uid(0, 5)
    batch_s = 0.0
    auto_s = 0.0
    started = time.perf_counter()
    for episode in range(episodes):
        mark = time.perf_counter()
        store.execute_many(_episode_batch(episode))
        batch_s += time.perf_counter() - mark
        mark = time.perf_counter()
        episodic = _uid(episode, 1)
        if episode == 0:
            store.execute(
                "CREATE (:Saga {uuid: $uuid, name: 'saga'})",
                {"uuid": saga},
            )
        else:
            store.execute(
                "MATCH (s:Saga {uuid: $uuid}) SET s.last = $episode",
                {"uuid": saga, "episode": episode},
            )
            store.execute(
                "MATCH (a:Episodic {uuid: $prev}) "
                "MATCH (b:Episodic {uuid: $episode}) "
                "CREATE (a)-[:NEXT_EPISODE {uuid: $edge}]->(b)",
                {
                    "prev": _uid(episode - 1, 1),
                    "episode": episodic,
                    "edge": _uid(episode, 6),
                },
            )
        store.execute(
            "MATCH (s:Saga {uuid: $saga}) "
            "MATCH (ep:Episodic {uuid: $episode}) "
            "CREATE (s)-[:HAS_EPISODE {uuid: $edge}]->(ep)",
            {"saga": saga, "episode": episodic, "edge": _uid(episode, 7)},
        )
        auto_s += time.perf_counter() - mark
    elapsed = time.perf_counter() - started
    nodes = len(list(store.iter_nodes()))
    edges = len(list(store.iter_edges()))
    expect_nodes = episodes * (1 + ENTITIES) + 1
    expect_edges = episodes * (ENTITIES + RELATES) + episodes + (episodes - 1)
    if nodes != expect_nodes or edges != expect_edges:
        raise RuntimeError(
            f"add_episode mix persisted {nodes} nodes, {edges} edges; "
            f"expected {expect_nodes}, {expect_edges}"
        )
    return elapsed, batch_s, auto_s


def mix_record_count(episodes: int = EPISODES) -> int:
    """Shard lines the mix appends, including each saga-node rewrite."""
    bulk = episodes * (1 + ENTITIES + ENTITIES + RELATES)
    saga_nodes = episodes
    saga_edges = episodes + (episodes - 1)
    return bulk + saga_nodes + saga_edges


def _report(label: str, samples: list[float]) -> None:
    millis = [round(sample * 1000, 1) for sample in samples]
    print(
        f"{label}: median {statistics.median(samples) * 1000:.1f} ms  "
        f"samples {millis}",
        flush=True,
    )


def main() -> None:
    _report(
        f"per-record fsync x{N}",
        [bench_per_record(N) for _ in range(ROUNDS)],
    )
    _report(
        f"one transaction x{N}",
        [bench_transaction(N) for _ in range(ROUNDS)],
    )
    _report(
        f"autocommit single x{N}",
        [bench_autocommit(N) for _ in range(ROUNDS)],
    )
    multi_records = N * 2
    _report(
        f"per-record fsync x{multi_records} (node+edge equivalent)",
        [bench_per_record(multi_records) for _ in range(ROUNDS)],
    )
    _report(
        f"autocommit node+edge x{N}",
        [bench_autocommit_node_edge(N) for _ in range(ROUNDS)],
    )
    records = mix_record_count()
    _report(
        f"per-record fsync x{records} (add_episode equivalent)",
        [bench_per_record(records) for _ in range(ROUNDS)],
    )
    totals: list[float] = []
    batches: list[float] = []
    autos: list[float] = []
    for _ in range(ROUNDS):
        total, batch, auto = bench_add_episode_mix()
        totals.append(total)
        batches.append(batch)
        autos.append(auto)
    _report(f"add_episode mix x{EPISODES}", totals)
    _report(f"  of which batch x{EPISODES}", batches)
    _report(f"  of which saga autocommit x{EPISODES}", autos)


if __name__ == "__main__":
    main()
