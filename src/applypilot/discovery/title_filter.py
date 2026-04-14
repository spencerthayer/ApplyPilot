"""Shared title relevance filter used by all discovery sources."""

from __future__ import annotations


def title_matches_query(title: str, query: str, *, strict: bool = False) -> bool:
    """Check if job title matches search query.

    strict=False (default): ANY query term in title = match.
    strict=True: ALL query terms must appear in title.
    """
    if not query:
        return True
    title_lower = title.lower()
    terms = [t for t in query.lower().split() if len(t) > 2]
    if not terms:
        return True
    if strict:
        return all(t in title_lower for t in terms)
    return any(t in title_lower for t in terms)
