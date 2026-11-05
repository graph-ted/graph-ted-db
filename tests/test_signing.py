"""REQ-078 interim signing: device keys, claims, signature lines, checks.

Every key here is a throwaway generated inside the test's temp directory.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from graph_ted_db import GraphStore, init_graph, signing
from graph_ted_db.store.jsonl import iter_json_objects
from graph_ted_db.store.records import NodeRecord

pytestmark = pytest.mark.skipif(not signing.available(), reason="needs graph-ted-db[signing]")


class Device:
    """One device: its own data dir (writer id, pins) and key file."""

    def __init__(self, base: Path, name: str, root: Path, *, key: bool = True, label: str = ""):
        self.data = base / name / "data"
        self.key_path = base / name / "keys" / "signing-key.json"
        if key:
            signing.create_key(self.key_path, label=label)
        self.store = GraphStore.open(root, data_dir=self.data, signing_key=self.key_path)

    def write(self, n: int = 2) -> None:
        for i in range(n):
            self.store.make_node(labels=["T"], props={"i": i})
        self.store.publish()

    def report(self, **kw):
        return self.store.signatures(**kw)

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "shared" / "g"
    init_graph(root)
    return root


def _bundles(root: Path, writer: str) -> list[Path]:
    return sorted((root / "nodes").glob(f"{writer}.*.jsonl"))


def test_create_key_modes_and_format(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "signing-key.json"
    key = signing.create_key(path, label="  work   laptop ")
    doc = json.loads(path.read_text())
    assert (
        doc["gtkey"] == 1 and doc["alg"] == "ed25519" and len(signing._unb64(doc["secret"])) == 32
    )
    assert key.label == "work laptop"
    assert signing.is_signer_id(key.signer)
    assert key.signer == signing.signer_id(key.public_key)
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777) == 0o600
        assert (path.parent.stat().st_mode & 0o777) == 0o700
    with pytest.raises(FileExistsError):
        signing.create_key(path)
    assert "secret" not in signing.key_info(path)


def test_label_blank_by_default_and_never_filled(tmp_path: Path, folder: Path) -> None:
    dev = Device(tmp_path, "a", folder)
    dev.write()
    claim = json.loads(
        (folder / "meta" / "identity" / f"claim.{dev.store.writer_id}.json").read_text()
    )
    assert claim["label"] == ""
    dev.close()


def test_two_writers_signed(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder, label="laptop")
    b = Device(tmp_path, "b", folder)
    a.write()
    b.write()
    rep = a.report()
    assert rep.states["signed"] == 2 and rep.states["mismatch"] == 0
    ws = rep.writers
    assert ws[a.store.writer_id].label == "laptop"
    assert ws[b.store.writer_id].signer == b.store.signing_status()["signer"]
    # The signature line is the last line; readers do not count it as skipped.
    last = _bundles(folder, a.store.writer_id)[0].read_bytes().splitlines()[-1]
    sig = json.loads(last)
    assert sig["gtsig"] == 1 and sig["file"].startswith(f"nodes/{a.store.writer_id}.")
    assert a.store.problems()["skipped_lines"] == 0
    assert len(list(a.store.iter_nodes())) == 4
    a.close()
    b.close()


def test_tampered_byte_flagged_and_records_kept(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    b = Device(tmp_path, "b", folder)
    a.write()
    a.close()
    path = _bundles(folder, a.store.writer_id)[0]
    data = path.read_bytes().replace(b'"i": 0', b'"i": 7').replace(b'"i":0', b'"i":7')
    assert data != path.read_bytes()
    path.write_bytes(data)
    rep = b.report()
    assert rep.states["mismatch"] == 1
    assert any("content differs" in m for m in rep.mismatches)
    # Q3: flagged, not hidden.
    b.store.refresh_index()
    assert sorted(n.props["i"] for n in b.store.iter_nodes()) == [1, 7]
    b.close()


def test_swapped_key_flagged(tmp_path: Path, folder: Path) -> None:
    """Another device signs a bundle under writer A's name with its own key."""
    a = Device(tmp_path, "a", folder)
    b = Device(tmp_path, "b", folder)
    a.write()
    b.report()  # b pins a's claim
    path = _bundles(folder, a.store.writer_id)[0]
    body, _ = signing.split_signature(path.read_bytes())
    other = signing.load_key(b.key_path)
    rel = path.relative_to(folder).as_posix()
    path.write_bytes(
        body + signing.signature_line(other, writer=a.store.writer_id, rel_file=rel, payload=body)
    )
    rep = b.report()
    assert rep.states["mismatch"] == 1
    assert any("signer differs" in m for m in rep.mismatches)
    a.close()
    b.close()


def test_renamed_bundle_flagged(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    a.close()
    path = _bundles(folder, a.store.writer_id)[0]
    path.rename(path.with_name(f"{a.store.writer_id}.000099.jsonl"))
    b = Device(tmp_path, "b", folder)
    rep = b.report()
    assert rep.states["mismatch"] == 1
    assert any("another file" in m for m in rep.mismatches)
    b.close()


def test_unsigned_writer_is_neutral(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder, key=False)
    a.write()
    rep = a.report()
    assert rep.states["unsigned"] == 1 and rep.states["mismatch"] == 0
    assert not (folder / "meta" / "identity").exists()
    a.close()


def test_older_reader_skips_the_signature_line(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    a.close()
    path = _bundles(folder, a.store.writer_id)[0]
    objs = [json.loads(x) for x in path.read_bytes().splitlines()]
    with pytest.raises(ValueError):
        NodeRecord.from_dict(objs[-1])  # an older reader skips (and counts) this one line
    assert all(NodeRecord.from_dict(o) for o in objs[:-1])
    skipped: list[str] = []
    assert len(list(iter_json_objects(path, skipped=skipped))) == len(objs) - 1
    assert skipped == []


def test_claim_changed_after_pin(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    b = Device(tmp_path, "b", folder, key=False)
    a.write()
    assert b.report().states["signed"] == 1  # trust on first use
    # Someone replaces a's claim with one for another key, and re-signs the bundle.
    forger = signing.create_key(tmp_path / "forger" / "k.json")
    claim = folder / "meta" / "identity" / f"claim.{a.store.writer_id}.json"
    claim.write_text(json.dumps(signing.make_claim(forger, a.store.writer_id)) + "\n")
    path = _bundles(folder, a.store.writer_id)[0]
    body, _ = signing.split_signature(path.read_bytes())
    rel = path.relative_to(folder).as_posix()
    path.write_bytes(
        body + signing.signature_line(forger, writer=a.store.writer_id, rel_file=rel, payload=body)
    )
    rep = b.report()
    assert rep.writers[a.store.writer_id].claim == "mismatch"
    assert rep.states["mismatch"] == 1
    # A device that never saw the first claim trusts the new one (Q8 limit).
    c = Device(tmp_path, "c", folder, key=False)
    assert c.report().states["signed"] == 1
    for d in (a, b, c):
        d.close()


def test_switch_off_and_on(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    off = a.key_path.with_name(a.key_path.name + ".off")
    a.key_path.rename(off)
    a.write()
    assert a.store.signing_status()["enabled"] is False
    off.rename(a.key_path)
    a.write()
    rep = a.report()
    assert rep.states == {**dict.fromkeys(signing.STATES, 0), "signed": 2, "unsigned": 1}
    a.close()


def test_compact_resigns_and_doctor_clean(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    a.write()
    a.store.compact()
    rep = a.report()
    assert rep.states["signed"] == 1 and rep.states["mismatch"] == 0
    report = a.store.doctor()
    assert report.own_files_changed == [] and report.skipped_lines == []
    assert "signatures: signed=1" in report.summary()
    a.close()
    # Reopen: nothing is republished or flagged as changed.
    b = GraphStore.open(folder, data_dir=a.data, signing_key=a.key_path)
    assert b.problems().get("own_files_changed", 0) == 0
    b.close()


def test_meta_documents_signed(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    path = a.store.meta_put("ontology/g1", "v1", {"version": 1, "types": ["A"]}, sign=True)
    a.store.meta_put("ontology/g1/drafts", "d1", {"draft": True})
    doc = json.loads(path.read_text())
    assert doc["_gtsig"]["file"] == path.relative_to(folder).as_posix()
    rep = a.report(meta_patterns=[r"ontology/[^/]+/v[0-9]+"])
    assert rep.meta["signed"] == 1 and rep.meta["unsigned"] == 0
    doc["types"] = ["B"]
    path.write_text(json.dumps(doc) + "\n")
    rep = a.report()
    assert rep.meta["mismatch"] == 1
    a.close()


def test_key_inside_graph_folder_refused(tmp_path: Path, folder: Path) -> None:
    key_path = folder / "meta" / "k.json"
    signing.create_key(key_path)
    store = GraphStore.open(folder, data_dir=tmp_path / "d", signing_key=key_path)
    store.make_node(labels=["T"])
    store.publish()
    assert store.problems()["signing_key_problem"] == 1
    assert store.signatures().states["unsigned"] == 1
    store.close()


def test_new_key_for_same_writer_is_not_used(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    a.key_path.unlink()
    signing.create_key(a.key_path)
    a.write()
    status = a.store.signing_status()
    assert status["enabled"] is False and "new writer id" in status["problem"]
    assert a.report().states["mismatch"] == 0
    a.close()


def test_without_extra_reports_unchecked(tmp_path: Path, folder: Path, monkeypatch) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    a.close()
    monkeypatch.setattr(signing, "available", lambda: False)
    rep = signing.signature_report(folder, data_dir=tmp_path / "nokey")
    assert rep.states["unchecked"] == 1 and rep.states["mismatch"] == 0
    with pytest.raises(signing.SigningError):
        signing.load_key(a.key_path)


def test_nr_files_leave_v1_untouched(tmp_path: Path, folder: Path) -> None:
    """Design acceptance: NR membership and revocation are new files next to v1 data."""
    a = Device(tmp_path, "a", folder)
    a.write()
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    rep_before = a.report().to_dict()
    sig = json.loads(_bundles(folder, a.store.writer_id)[0].read_bytes().splitlines()[-1])
    w = a.store.writer_id
    a.store.meta_put(
        "membership/g1",
        "m1",
        {"gtmember": 1, "group": "g1", "signer": sig["signer"], "role": "admin", "epoch": 1},
    )
    seq = int(sig["file"].rsplit(".", 2)[1])
    a.store.meta_put(
        "revocations", "r1", {"gtrevoke": 1, "signer": sig["signer"], "writer": w, "after_seq": seq}
    )
    for p, data in before.items():
        assert p.read_bytes() == data
    rep_after = a.report().to_dict()
    assert rep_after["states"] == rep_before["states"]
    a.close()


def test_report_never_contains_key_material(tmp_path: Path, folder: Path) -> None:
    a = Device(tmp_path, "a", folder)
    a.write()
    secret = json.loads(a.key_path.read_text())["secret"]
    out = json.dumps(a.report().to_dict()) + a.report().summary()
    out += "".join(p.read_text(errors="replace") for p in folder.rglob("*") if p.is_file())
    assert secret not in out
    a.close()


def test_http_signatures_route(tmp_path: Path, folder: Path) -> None:
    import threading
    import urllib.request

    from graph_ted_db.server.http import make_server

    a = Device(tmp_path, "a", folder)
    a.write()
    server = make_server(a.store, "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/signatures") as resp:
            body = json.loads(resp.read())
        assert body["states"]["signed"] == 1
        assert body["this_device"]["enabled"] is True
        assert "secret" not in json.dumps(body)
    finally:
        server.shutdown()
        server.server_close()
        a.close()


def test_two_device_copies_via_sync(tmp_path: Path) -> None:
    """Two devices on two replicas of one folder (a sync client copies files)."""
    a_root = tmp_path / "a" / "g"
    init_graph(a_root)
    b_root = tmp_path / "b" / "g"
    shutil.copytree(a_root, b_root)
    a = Device(tmp_path, "da", a_root)
    b = Device(tmp_path, "db", b_root)
    a.write()
    b.write()
    for src, dst in ((a_root, b_root), (b_root, a_root)):
        for p in src.rglob("*"):
            if p.is_file():
                t = dst / p.relative_to(src)
                if not t.exists():
                    t.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, t)
    for d in (a, b):
        rep = d.report()
        assert rep.states["signed"] == 2, rep.summary()
        d.close()
