"""Small deterministic text normalization helpers."""


def normalize_text(value: str, limit: int) -> str:
    """Collapse HTML-derived whitespace and apply a character cap."""
    return " ".join(value.split())[:limit]
