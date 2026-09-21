"""Optional Graphiti GraphDriver. Import GraphTedDbDriver from .graphiti."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "GraphTedDbDriver":
        from graph_ted_db.driver.graphiti import GraphTedDbDriver

        return GraphTedDbDriver
    raise AttributeError(name)


__all__ = ["GraphTedDbDriver"]
