"""The crash hooks may only ever kill the process under test."""

from pathlib import Path

import graph_ted_db

ROOTS = [
    Path(graph_ted_db.__file__).parent,
    Path(__file__).parent,
    Path(__file__).parent.parent / "scripts",
]


def test_no_getppid_kill_targets() -> None:
    # A forked child that kills os.getppid() can hit a subreaper (systemd --user)
    # once its parent is gone, taking down the whole session.
    offenders = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "os.kill(" in line and "getppid" in line:
                    offenders.append(f"{path}:{n}")
    assert offenders == []
