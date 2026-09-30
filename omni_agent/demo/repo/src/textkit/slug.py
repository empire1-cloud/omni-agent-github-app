import re


def slugify(text: str) -> str:
    """Lower-case `text` and join its words with hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def truncate(text: str, limit: int) -> str:
    """Cut `text` to at most `limit` characters, marking the cut with an ellipsis."""
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"
