"""PEP 561: the wheel ships py.typed."""

from importlib.resources import files


def test_py_typed_marker_present() -> None:
    assert files("graph_ted_db").joinpath("py.typed").is_file()
