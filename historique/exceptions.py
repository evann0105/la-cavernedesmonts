class HistoryError(ValueError):
    """A recoverable problem, safe to explain in the seller interface."""


class HistoryConflict(HistoryError):
    """Content changed after the seller reviewed the proposed restoration."""
