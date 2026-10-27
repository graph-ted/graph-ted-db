"""Cosine over vectors of different sizes is an error, not a silent 0.0."""

from pathlib import Path

import pytest

from graph_ted_db.engine import CypherError
from graph_ted_db.engine.functions import fn_vector_cosine
from graph_ted_db.store import GraphStore, init_graph


def _open(tmp_path: Path) -> GraphStore:
    root = tmp_path / "g"
    init_graph(root, name="vec")
    return GraphStore.open(root, data_dir=tmp_path / "data")


def test_same_size_still_scores() -> None:
    assert fn_vector_cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert fn_vector_cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert fn_vector_cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert fn_vector_cosine([], []) == 0.0
    assert fn_vector_cosine(None, [1.0]) is None


@pytest.mark.parametrize(("left", "right"), [([1.0, 0.0, 0.0], [1.0, 0.0]), ([], [1.0])])
def test_different_sizes_raise_a_clear_error(left, right) -> None:
    with pytest.raises(CypherError, match=r"different dimensions \(\d+ and \d+\).*re-embed"):
        fn_vector_cosine(left, right)


def test_query_with_mixed_sizes_raises(tmp_path: Path) -> None:
    g = _open(tmp_path)
    g.execute(
        "CREATE (n:Entity {name: 'a'}) WITH n "
        "CALL db.create.setNodeVectorProperty(n, 'name_embedding', $v) RETURN n.name AS name",
        {"v": [0.1, 0.2, 0.3]},
    )
    with pytest.raises(CypherError, match="different dimensions"):
        g.execute(
            "MATCH (n:Entity) RETURN vector.similarity.cosine(n.name_embedding, $q) AS score",
            {"q": [0.1, 0.2]},
        )
    rows = g.execute(
        "MATCH (n:Entity) RETURN vector.similarity.cosine(n.name_embedding, $q) AS score",
        {"q": [0.1, 0.2, 0.3]},
    )
    assert rows[0]["score"] == pytest.approx(1.0)
