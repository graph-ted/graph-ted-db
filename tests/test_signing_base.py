"""REQ-078 parts that need no `cryptography` (the zero-dependency install)."""

from __future__ import annotations

import json
from pathlib import Path

from graph_ted_db import GraphStore, init_graph, signing
from graph_ted_db.store.jsonl import iter_json_objects


def test_no_key_means_unsigned(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    store = GraphStore.open(root, data_dir=tmp_path / "d")
    store.make_node(labels=["T"])
    store.publish()
    status = store.signing_status()
    assert status["enabled"] is False and status["problem"] == ""
    rep = store.signatures()
    assert rep.states["unsigned"] == 1
    assert "signing_key_problem" not in store.problems()
    assert not (root / "meta" / "identity").exists()
    store.close()


def test_default_key_path_is_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(signing.SIGNING_KEY_ENV, raising=False)
    assert signing.default_key_path(tmp_path) == tmp_path / "keys" / "signing-key.json"
    monkeypatch.setenv(signing.SIGNING_KEY_ENV, str(tmp_path / "k.json"))
    assert signing.default_key_path(tmp_path) == tmp_path / "k.json"


def test_signature_line_split_and_skipped(tmp_path: Path) -> None:
    body = b'{"id": "x"}\n'
    line = {"gtsig": 1, "alg": "ed25519", "signer": "k" + "0" * 16, "sig": "AA=="}
    data = body + (json.dumps(line) + "\n").encode()
    got_body, sig = signing.split_signature(data)
    assert got_body == body and sig is not None and sig["gtsig"] == 1
    assert signing.split_signature(body) == (body, None)
    p = tmp_path / "f.jsonl"
    p.write_bytes(data)
    skipped: list[str] = []
    assert [o for _, o in iter_json_objects(p, skipped=skipped)] == [{"id": "x"}]
    assert skipped == []


def test_canonical_is_stable() -> None:
    assert signing.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


def test_newer_signature_is_cant_check(tmp_path: Path) -> None:
    root = tmp_path / "g"
    init_graph(root)
    w = "w" + "1" * 16
    body = b""
    rel = f"nodes/{w}.000001.jsonl"
    sig = {"gtsig": 99, "file": rel, "writer": w}
    (root / "nodes").mkdir(exist_ok=True)
    (root / rel).write_bytes(body + (json.dumps(sig) + "\n").encode())
    rep = signing.signature_report(root, data_dir=tmp_path / "d")
    assert rep.states["newer"] == 1 and rep.states["mismatch"] == 0
