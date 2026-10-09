"""Errors for the openCypher subset."""


class CypherError(ValueError):
    """Invalid query, or syntax outside this engine's openCypher subset."""

    def __init__(self, message: str, *, pos: int | None = None) -> None:
        if pos is not None:
            message = f"{message} (at {pos})"
        super().__init__(message)
        self.pos = pos
