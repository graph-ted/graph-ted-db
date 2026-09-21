"""Cypher subset errors."""


class CypherError(ValueError):
    """Unsupported or invalid Cypher in this engine."""

    def __init__(self, message: str, *, pos: int | None = None) -> None:
        if pos is not None:
            message = f"{message} (at {pos})"
        super().__init__(message)
        self.pos = pos
