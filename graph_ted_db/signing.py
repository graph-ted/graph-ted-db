"""Interim signing (REQ-078): device keys, claims, signature lines, checks.

Each device that writes to a shared folder can hold its own Ed25519 key in
local app data (never in the graph folder). The device publishes one
self-signed *claim* (``meta/identity/claim.<writer>.json``) saying "writer
``w…`` is signer ``k…``", and appends one *signature line* to the end of every
sealed bundle it publishes. Readers check each bundle against its writer's
claim. See docs/format.md (Signatures) and docs/security.md.

Everything except creating keys, signing and verifying uses the standard
library only. Those three need the optional ``signing`` extra
(``pip install "graph-ted-db[signing]"``, which adds ``cryptography``). Without
it, a store writes unsigned bundles and reports signed ones as "can't check".
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph_ted_db.store.format import format_timestamp, is_writer_id

log = logging.getLogger("graph_ted_db")

SIGNING_KEY_ENV = "GRAPH_TED_DB_SIGNING_KEY"
KEY_VERSION = 1
SIG_VERSION = 1
CLAIM_VERSION = 1
ALG = "ed25519"
CLAIM_DIR = "identity"
CLAIM_NAME = "claim"
META_SIG_FIELD = "_gtsig"
MAX_LABEL = 64

# Per-bundle states. "unchecked": signed, but this reader has no `cryptography`.
SIGNED = "signed"
UNSIGNED = "unsigned"
MISMATCH = "mismatch"
SYNCING = "syncing"
NEWER = "newer"
UNCHECKED = "unchecked"
STATES = (SIGNED, UNSIGNED, MISMATCH, SYNCING, NEWER, UNCHECKED)

_SIGNER_RE = re.compile(r"^k[0-9a-f]{16}$")


class SigningError(RuntimeError):
    """A key file is missing, unreadable, in the wrong place, or signing is unavailable."""


def available() -> bool:
    """True when the optional ``signing`` extra (``cryptography``) is installed."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
    except Exception:
        return False
    return True


def canonical(obj: dict[str, Any]) -> bytes:
    """Sorted keys, no spaces, UTF-8. What every signature covers."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def signer_id(public_key: bytes) -> str:
    """``k`` + the first 16 hex of SHA-256 of the raw public key. Never from a name."""
    return "k" + hashlib.sha256(public_key).hexdigest()[:16]


def is_signer_id(value: object) -> bool:
    return isinstance(value, str) and bool(_SIGNER_RE.match(value))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: object) -> bytes | None:
    if not isinstance(text, str):
        return None
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception:
        return None


def default_key_path(data_dir: Path | None = None) -> Path:
    """Where this device's key lives: ``$GRAPH_TED_DB_SIGNING_KEY``, else
    ``<data dir>/keys/signing-key.json`` (local app data, never synced)."""
    env = os.environ.get(SIGNING_KEY_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    from graph_ted_db.store.lock import default_data_dir

    return (data_dir or default_data_dir()) / "keys" / "signing-key.json"


def clean_label(label: str | None) -> str:
    """A label is optional, typed by the user, never filled in by the store."""
    text = " ".join((label or "").split())
    if len(text) > MAX_LABEL:
        raise ValueError(f"label is longer than {MAX_LABEL} characters")
    return text


@dataclass(frozen=True)
class DeviceKey:
    """This device's Ed25519 key, loaded from its key file."""

    path: Path
    seed: bytes = field(repr=False)
    public_key: bytes
    signer: str
    label: str
    created_at: str

    def sign(self, message: bytes) -> str:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return _b64(Ed25519PrivateKey.from_private_bytes(self.seed).sign(message))


def _public_from_seed(seed: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def create_key(path: Path, *, label: str = "") -> DeviceKey:
    """Create a new key file (mode 600, in a mode 700 folder). Never replaces one.

    Raises:
        FileExistsError: a key file is already there.
        SigningError: the ``signing`` extra is not installed.
    """
    if not available():
        raise SigningError('signing needs the extra: pip install "graph-ted-db[signing]"')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(path.parent, 0o700)
    seed = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    doc = {
        "gtkey": KEY_VERSION,
        "alg": ALG,
        "secret": _b64(seed),
        "label": clean_label(label),
        "created_at": format_timestamp(),
    }
    data = (json.dumps(doc) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    key = load_key(path)
    assert key is not None
    return key


def load_key(path: Path) -> DeviceKey | None:
    """Load a key file. None when there is no file.

    Raises:
        SigningError: unreadable, a newer key version, or no ``signing`` extra.
    """
    path = Path(path).expanduser()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SigningError(f"cannot read signing key {path}: {exc}") from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise SigningError(f"signing key {path} is not valid JSON") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("gtkey"), int):
        raise SigningError(f"{path} is not a graph-ted-db signing key")
    if doc["gtkey"] > KEY_VERSION:
        raise SigningError(f"signing key {path} is a newer version; update graph-ted-db")
    if doc.get("alg") != ALG:
        raise SigningError(f"signing key {path}: unsupported alg {doc.get('alg')!r}")
    seed = _unb64(doc.get("secret"))
    if seed is None or len(seed) != 32:
        raise SigningError(f"signing key {path} has no valid secret")
    if not available():
        raise SigningError('signing needs the extra: pip install "graph-ted-db[signing]"')
    pub = _public_from_seed(seed)
    label = doc.get("label") if isinstance(doc.get("label"), str) else ""
    return DeviceKey(
        path=path,
        seed=seed,
        public_key=pub,
        signer=signer_id(pub),
        label=label,
        created_at=str(doc.get("created_at", "")),
    )


def key_info(path: Path) -> dict[str, Any]:
    """Public facts about a key file (signer id, label), for settings screens."""
    key = load_key(path)
    if key is None:
        return {"exists": False}
    return {
        "exists": True,
        "signer": key.signer,
        "label": key.label,
        "created_at": key.created_at,
        "alg": ALG,
    }


def _verify(public_key: bytes, sig_b64: object, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    sig = _unb64(sig_b64)
    if sig is None or len(sig) != 64 or len(public_key) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(sig, message)
    except (InvalidSignature, ValueError):
        return False
    return True


# --- claims -------------------------------------------------------------


def make_claim(key: DeviceKey, writer: str) -> dict[str, Any]:
    """A self-signed "writer ``writer`` is signer ``key.signer``" document."""
    if not is_writer_id(writer):
        raise ValueError(f"not a writer id: {writer!r}")
    body = {
        "gtclaim": CLAIM_VERSION,
        "writer": writer,
        "signer": key.signer,
        "alg": ALG,
        "public_key": _b64(key.public_key),
        "label": key.label,
        "created_at": format_timestamp(),
    }
    return {**body, "sig": key.sign(canonical(body))}


def check_claim(doc: dict[str, Any], *, writer: str) -> tuple[str, bytes | None, str]:
    """(state, public key, reason) for a claim document of ``writer``."""
    version = doc.get("gtclaim")
    if not isinstance(version, int):
        return MISMATCH, None, "not a claim"
    if version > CLAIM_VERSION:
        return NEWER, None, "claim is a newer version"
    pub = _unb64(doc.get("public_key"))
    if doc.get("alg") != ALG or pub is None or len(pub) != 32:
        return MISMATCH, None, "claim has no valid ed25519 public key"
    if doc.get("writer") != writer:
        return MISMATCH, None, "claim names another writer"
    if doc.get("signer") != signer_id(pub):
        return MISMATCH, None, "claim signer id does not match its key"
    if not available():
        return UNCHECKED, pub, "signing extra not installed"
    body = {k: v for k, v in doc.items() if k != "sig"}
    if not _verify(pub, doc.get("sig"), canonical(body)):
        return MISMATCH, None, "claim signature does not verify"
    return SIGNED, pub, ""


# --- signature lines (bundles) and _gtsig (meta documents) ---------------


def _sig_object(
    key: DeviceKey, *, writer: str, rel_file: str, digest: str, lines: int | None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "gtsig": SIG_VERSION,
        "alg": ALG,
        "signer": key.signer,
        "writer": writer,
        "file": rel_file,
    }
    if lines is not None:
        body["lines"] = lines
    body["sha256"] = digest
    body["signed_at"] = format_timestamp()
    return {**body, "sig": key.sign(canonical(body))}


def signature_line(key: DeviceKey, *, writer: str, rel_file: str, payload: bytes) -> bytes:
    """The line appended to a sealed bundle. ``payload`` ends with a newline."""
    if not payload.endswith(b"\n"):
        payload += b"\n"
    obj = _sig_object(
        key,
        writer=writer,
        rel_file=rel_file,
        digest=hashlib.sha256(payload).hexdigest(),
        lines=payload.count(b"\n"),
    )
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def is_signature_object(obj: object) -> bool:
    """A signature line: has ``gtsig`` and no record ``id``. Readers skip it."""
    return isinstance(obj, dict) and "gtsig" in obj and "id" not in obj


def split_signature(data: bytes) -> tuple[bytes, dict[str, Any] | None]:
    """(bytes above the signature line, the signature object or None)."""
    if not data.endswith(b"\n"):
        return data, None
    cut = data.rfind(b"\n", 0, len(data) - 1)
    last = data[cut + 1 :]
    if not last.startswith(b'{"'):
        return data, None
    try:
        obj = json.loads(last)
    except ValueError:
        return data, None
    if not is_signature_object(obj):
        return data, None
    return data[: cut + 1], obj


def sign_meta(key: DeviceKey, doc: dict[str, Any], *, writer: str, rel_file: str) -> dict:
    """``doc`` with a ``_gtsig`` field covering its canonical form without ``_gtsig``."""
    body = {k: v for k, v in doc.items() if k != META_SIG_FIELD}
    digest = hashlib.sha256(canonical(body)).hexdigest()
    return {
        **body,
        META_SIG_FIELD: _sig_object(
            key, writer=writer, rel_file=rel_file, digest=digest, lines=None
        ),
    }


def _check_sig_object(
    sig: dict[str, Any],
    *,
    writer: str,
    rel_file: str,
    digest: str,
    lines: int | None,
    claim_state: str | None,
    claim_pub: bytes | None,
    claim_signer: str | None,
) -> tuple[str, str]:
    version = sig.get("gtsig")
    if not isinstance(version, int):
        return MISMATCH, "signature has no version"
    if version > SIG_VERSION:
        return NEWER, "signed with a newer version"
    if sig.get("alg") != ALG:
        return MISMATCH, "unsupported signature algorithm"
    if sig.get("writer") != writer:
        return MISMATCH, "signature names another writer"
    if sig.get("file") != rel_file:
        return MISMATCH, "signature names another file (renamed or replayed)"
    if lines is not None and sig.get("lines") != lines:
        return MISMATCH, "line count differs from the signature"
    if sig.get("sha256") != digest:
        return MISMATCH, "content differs from the signature"
    if claim_state is None:
        return SYNCING, "no claim for this writer yet (still syncing?)"
    if claim_state == NEWER:
        return NEWER, "claim is a newer version"
    if claim_state == MISMATCH:
        return MISMATCH, "writer's claim does not match"
    if sig.get("signer") != claim_signer:
        return MISMATCH, "signer differs from the writer's claim"
    if claim_state == UNCHECKED or claim_pub is None:
        return UNCHECKED, "signing extra not installed"
    body = {k: v for k, v in sig.items() if k != "sig"}
    if not _verify(claim_pub, sig.get("sig"), canonical(body)):
        return MISMATCH, "signature does not verify"
    return SIGNED, ""


# --- report --------------------------------------------------------------


@dataclass
class WriterSignatures:
    writer: str
    signer: str = ""
    label: str = ""
    claim: str = ""  # state of the claim, "" when there is none
    claim_reason: str = ""
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATES, 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "writer": self.writer,
            "signer": self.signer,
            "label": self.label,
            "claim": self.claim,
            "claim_reason": self.claim_reason,
            "counts": dict(self.counts),
        }


@dataclass
class SignatureReport:
    """Per-writer counts of bundle states, plus signed meta documents.

    ``states``: totals across writers. ``mismatches``: one line per bundle,
    meta document or claim that does not match (path and reason).
    """

    version: int = SIG_VERSION
    signing_available: bool = False
    writers: dict[str, WriterSignatures] = field(default_factory=dict)
    meta: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATES, 0))
    mismatches: list[str] = field(default_factory=list)
    this_device: dict[str, Any] = field(default_factory=dict)

    @property
    def states(self) -> dict[str, int]:
        total = dict.fromkeys(STATES, 0)
        for w in self.writers.values():
            for k, v in w.counts.items():
                total[k] += v
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "signing_available": self.signing_available,
            "states": self.states,
            "meta": dict(self.meta),
            "writers": [w.to_dict() for w in sorted(self.writers.values(), key=lambda w: w.writer)],
            "mismatches": list(self.mismatches),
            "this_device": dict(self.this_device),
        }

    def summary(self) -> str:
        s = self.states
        line = (
            f"signatures: signed={s[SIGNED]} unsigned={s[UNSIGNED]} mismatch={s[MISMATCH]}"
            f" syncing={s[SYNCING]} newer={s[NEWER]} unchecked={s[UNCHECKED]}"
            f" meta_signed={self.meta[SIGNED]} meta_mismatch={self.meta[MISMATCH]}"
        )
        out = [line]
        if not self.signing_available and (s[UNCHECKED] or self.meta[UNCHECKED]):
            out.append('  signed bundles not checked: pip install "graph-ted-db[signing]"')
        for w in sorted(self.writers.values(), key=lambda w: w.writer):
            who = w.signer or "no claim"
            if w.label:
                who += f' "{w.label}"'
            counts = " ".join(f"{k}={v}" for k, v in w.counts.items() if v)
            out.append(f"  {w.writer} {who} {counts}".rstrip())
        for item in self.mismatches:
            out.append(f"  doesn't match: {item}")
        return "\n".join(out) + "\n"


def _pins_path(data_dir: Path | None, graph_id: str) -> Path:
    from graph_ted_db.store.lock import default_data_dir

    return (data_dir or default_data_dir()) / graph_id / "signers.json"


def _load_pins(path: Path) -> dict[str, dict[str, str]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pins = doc.get("pins") if isinstance(doc, dict) else None
    return pins if isinstance(pins, dict) else {}


def _save_pins(path: Path, pins: dict[str, dict[str, str]]) -> None:
    from graph_ted_db.store.jsonl import replace_json_file

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_json_file(path, {"gtpins": 1, "pins": pins})
    except OSError as exc:
        log.warning("graph-ted-db: could not save signer pins %s: %s", path, exc)


def _bundle_files(root: Path) -> Iterable[tuple[Path, str]]:
    from graph_ted_db.store.paths import is_sealed_segment_name, writer_of

    dirs = [root / "nodes", root / "edges", root / "meta"]
    vec = root / "vectors"
    if vec.is_dir():
        dirs.extend(sorted(p for p in vec.iterdir() if p.is_dir()))
    for d in dirs:
        if not d.is_dir():
            continue
        for child in sorted(d.iterdir()):
            if not child.is_file() or not is_sealed_segment_name(child.name):
                continue
            writer = writer_of(child.name)
            if writer is None:
                continue
            if d.name == "meta" and not child.name.startswith("deleted."):
                continue
            yield child, writer


def _meta_docs(root: Path) -> Iterable[tuple[Path, str, str]]:
    """(path, writer, "<dir>/<name>") for app meta documents."""
    from graph_ted_db.store.meta import app_meta_dirs, parse_name

    meta = root / "meta"
    for directory in app_meta_dirs(meta):
        if directory.relative_to(meta).parts[0] == CLAIM_DIR:
            continue
        for child in sorted(directory.iterdir()):
            if not child.is_file():
                continue
            parsed = parse_name(child.name)
            if parsed is None:
                continue
            rel_dir = directory.relative_to(meta).as_posix()
            yield child, parsed[1], f"{rel_dir}/{parsed[0]}"


def signature_report(
    root: str | Path,
    *,
    graph_id: str | None = None,
    data_dir: Path | None = None,
    meta_patterns: Iterable[str] = (),
    pin: bool = True,
    this_device: dict[str, Any] | None = None,
    cache: dict[str, tuple[tuple[int, int], tuple[str, str]]] | None = None,
) -> SignatureReport:
    """Check every sealed bundle and signed meta document in a graph folder.

    Reads only; the one write is this device's trust-on-first-use pins
    (``<data dir>/<graph id>/signers.json``, local) when ``pin`` is true. The
    first valid claim this device sees for a writer is pinned; a later,
    different claim for the same writer makes that writer "doesn't match".

    ``meta_patterns``: regexes on ``"<dir>/<name>"`` (for example
    ``r"ontology/[^/]+/v[0-9]+"``). Matching meta documents without
    ``_gtsig`` count as unsigned; any document with ``_gtsig`` is checked.
    """
    root = Path(root).expanduser().resolve()
    if graph_id is None:
        from graph_ted_db.store.init import load_graph_meta

        graph_id = load_graph_meta(root).id
    report = SignatureReport(signing_available=available())
    report.this_device = dict(this_device or {})
    pins_file = _pins_path(data_dir, graph_id)
    pins = _load_pins(pins_file)
    pins_changed = False

    # Claims: writer -> (state, public key, signer)
    claims: dict[str, tuple[str, bytes | None, str]] = {}
    claim_dir = root / "meta" / CLAIM_DIR
    from graph_ted_db.store.meta import parse_name, read_entry

    def writer_entry(writer: str) -> WriterSignatures:
        if writer not in report.writers:
            report.writers[writer] = WriterSignatures(writer=writer)
        return report.writers[writer]

    if claim_dir.is_dir():
        for child in sorted(claim_dir.iterdir()):
            if not child.is_file():
                continue
            parsed = parse_name(child.name)
            if parsed is None:
                if child.name.endswith(".tmp"):
                    continue
                # A second copy of someone's claim (conflict copy): "two different claims".
                m = re.search(r"\.(w[0-9a-f]{16})", child.name)
                if m:
                    w = writer_entry(m.group(1))
                    w.claim, w.claim_reason = MISMATCH, "a second claim file for this writer"
                    claims[m.group(1)] = (MISMATCH, None, "")
                    report.mismatches.append(f"meta/{CLAIM_DIR}/{child.name}: second claim")
                continue
            name, writer = parsed
            if name != CLAIM_NAME:
                continue
            entry = read_entry(child)
            w = writer_entry(writer)
            if entry.data is None:
                if writer not in claims:
                    w.claim, w.claim_reason = SYNCING, entry.problem or ""
                continue
            state, pub, reason = check_claim(entry.data, writer=writer)
            signer = str(entry.data.get("signer", ""))
            if state in (SIGNED, UNCHECKED) and pub is not None:
                pinned = pins.get(writer)
                pub_b64 = _b64(pub)
                if pinned is None:
                    if pin and state == SIGNED:
                        pins[writer] = {"signer": signer, "public_key": pub_b64}
                        pins_changed = True
                elif pinned.get("public_key") != pub_b64:
                    state, reason = (
                        MISMATCH,
                        (f"claim changed: this device first saw signer {pinned.get('signer')}"),
                    )
            if writer in claims and claims[writer][0] == MISMATCH:
                continue
            claims[writer] = (state, pub, signer)
            w.claim, w.claim_reason = state, reason
            w.signer = signer
            label = entry.data.get("label")
            w.label = label if isinstance(label, str) else ""
            if state == MISMATCH:
                report.mismatches.append(f"meta/{CLAIM_DIR}/{child.name}: {reason}")
    # A writer that this device pinned but whose claim file is gone keeps its pin.
    for writer, pinned in pins.items():
        if writer not in claims and is_writer_id(writer):
            pub = _unb64(pinned.get("public_key"))
            if pub is not None:
                st = SIGNED if report.signing_available else UNCHECKED
                claims[writer] = (st, pub, str(pinned.get("signer", "")))
                w = writer_entry(writer)
                w.signer = w.signer or str(pinned.get("signer", ""))
                w.claim = w.claim or "pinned"
    if pins_changed:
        _save_pins(pins_file, pins)

    def claim_for(writer: str) -> tuple[str | None, bytes | None, str | None]:
        c = claims.get(writer)
        if c is None:
            return None, None, None
        return c[0], c[1], c[2]

    for path, writer in _bundle_files(root):
        rel = path.relative_to(root).as_posix()
        w = writer_entry(writer)
        try:
            st = path.stat()
        except OSError:
            continue
        stamp = (st.st_size, st.st_mtime_ns)
        cstate = claims.get(writer)
        ckey = f"{rel}|{cstate[0] if cstate else ''}|{cstate[2] if cstate else ''}"
        if cache is not None and ckey in cache and cache[ckey][0] == stamp:
            state, reason = cache[ckey][1]
        else:
            state, reason = _check_bundle(path, rel, writer, claim_for(writer))
            if cache is not None and state != SYNCING:
                cache[ckey] = (stamp, (state, reason))
        w.counts[state] += 1
        if state == MISMATCH:
            report.mismatches.append(f"{rel}: {reason}")

    patterns = [re.compile(p) for p in meta_patterns]
    for path, writer, name in _meta_docs(root):
        entry = read_entry(path)
        if entry.data is None:
            continue
        sig = entry.data.get(META_SIG_FIELD)
        if not isinstance(sig, dict):
            if any(p.fullmatch(name) for p in patterns):
                report.meta[UNSIGNED] += 1
            continue
        rel = path.relative_to(root).as_posix()
        body = {k: v for k, v in entry.data.items() if k != META_SIG_FIELD}
        cs, cp, csig = claim_for(writer)
        state, reason = _check_sig_object(
            sig,
            writer=writer,
            rel_file=rel,
            digest=hashlib.sha256(canonical(body)).hexdigest(),
            lines=None,
            claim_state=cs,
            claim_pub=cp,
            claim_signer=csig,
        )
        report.meta[state] += 1
        if state == MISMATCH:
            report.mismatches.append(f"{rel}: {reason}")
    return report


def _check_bundle(
    path: Path, rel: str, writer: str, claim: tuple[str | None, bytes | None, str | None]
) -> tuple[str, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return SYNCING, f"unreadable ({exc.__class__.__name__})"
    if not data or not data.endswith(b"\n"):
        return SYNCING, "unterminated (still syncing?)"
    body, sig = split_signature(data)
    if sig is None:
        return UNSIGNED, ""
    return _check_sig_object(
        sig,
        writer=writer,
        rel_file=rel,
        digest=hashlib.sha256(body).hexdigest(),
        lines=body.count(b"\n"),
        claim_state=claim[0],
        claim_pub=claim[1],
        claim_signer=claim[2],
    )
