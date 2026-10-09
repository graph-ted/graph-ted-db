"""Paths inside a graph folder. Conflict-copy detection lives here too."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from graph_ted_db.store.format import shard_id


@dataclass(frozen=True)
class GraphPaths:
    root: Path

    @property
    def graph_json(self) -> Path:
        return self.root / "graph.json"

    @property
    def nodes_dir(self) -> Path:
        return self.root / "nodes"

    @property
    def edges_dir(self) -> Path:
        return self.root / "edges"

    @property
    def vectors_dir(self) -> Path:
        return self.root / "vectors"

    @property
    def meta_dir(self) -> Path:
        return self.root / "meta"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def deleted_jsonl(self) -> Path:
        return self.meta_dir / "deleted.jsonl"

    @property
    def labels_json(self) -> Path:
        return self.meta_dir / "labels.json"

    def node_shard(self, record_id: str) -> Path:
        return self.nodes_dir / f"{shard_id(record_id)}.jsonl"

    def edge_shard(self, record_id: str) -> Path:
        return self.edges_dir / f"{shard_id(record_id)}.jsonl"

    def vector_shard(self, property_name: str, record_id: str) -> Path:
        return self.vectors_dir / property_name / f"{shard_id(record_id)}.jsonl"

    def required_directories(self) -> tuple[Path, ...]:
        return (
            self.nodes_dir,
            self.edges_dir,
            self.vectors_dir,
            self.meta_dir,
            self.logs_dir,
        )


# rclone bisync renames both sides of a conflict by appending a suffix after the
# extension: "00.jsonl.conflict1" (default since v1.66, also with a custom
# --conflict-suffix such as "00.jsonl.laptop-conflict1") or "00.jsonl..path1"
# (earlier releases). A transfer in progress is "<name>.<hash>.partial"; that is
# not a conflict copy and stays ignored, like "*.tmp".
_SUFFIXED_CONFLICT = re.compile(r"\.jsonl\.(?:[\w-]*conflict\d*|\.path[12])$", re.IGNORECASE)


def is_record_file_name(name: str) -> bool:
    """A JSONL record file: ``*.jsonl`` or a sync client's suffixed conflict copy."""
    if name.endswith(".tmp") or name.startswith(("~$", ".")):
        return False
    return name.lower().endswith(".jsonl") or _SUFFIXED_CONFLICT.search(name) is not None


def is_canonical_shard_name(name: str) -> bool:
    """True for v1 shard filenames: two lowercase hex digits + .jsonl."""
    if not name.endswith(".jsonl") or len(name) != len("00.jsonl"):
        return False
    stem = name[:2]
    return all(c in "0123456789abcdef" for c in stem)


def is_conflict_copy(path: Path, canonical_stem: str) -> bool:
    """Whether `path` should be union-merged with `<stem>.jsonl`.

    Sync clients rename rather than overwrite. See docs/format.md.
    """
    if not is_record_file_name(path.name):
        return False
    if path.name == f"{canonical_stem}.jsonl":
        return False
    lowered = path.name.lower()
    stem = canonical_stem.lower()
    if lowered.startswith(stem):
        return True
    return stem in lowered


def shard_jsonl_files(directory: Path, canonical_stem: str) -> list[Path]:
    """Canonical shard plus any conflict-copy siblings, if they exist."""
    if not directory.is_dir():
        return []
    found: list[Path] = []
    canonical_name = f"{canonical_stem}.jsonl"
    canonical = directory / canonical_name
    if canonical.is_file():
        found.append(canonical)
    stem = canonical_stem.lower()
    conflicts: list[Path] = []
    try:
        entries = os.scandir(directory)
    except OSError:
        return found
    with entries:
        for entry in entries:
            name = entry.name
            if name == canonical_name:
                continue
            lowered = name.lower()
            if not is_record_file_name(name):
                continue
            if not (lowered.startswith(stem) or stem in lowered):
                continue
            child = directory / name
            if is_conflict_copy(child, canonical_stem):
                conflicts.append(child)
    found.extend(sorted(conflicts))
    return found


def discover_shard_stems(directory: Path) -> list[str]:
    """Shard hex stems present as canonical files or conflict copies."""
    stems: set[str] = set()
    if not directory.is_dir():
        return []
    for child in directory.iterdir():
        if not child.is_file():
            continue
        if not is_record_file_name(child.name):
            continue
        if is_canonical_shard_name(child.name):
            stems.add(child.name[:2])
            continue
        prefix = child.name[:2].lower()
        if len(prefix) == 2 and all(c in "0123456789abcdef" for c in prefix):
            stems.add(prefix)
    return sorted(stems)
