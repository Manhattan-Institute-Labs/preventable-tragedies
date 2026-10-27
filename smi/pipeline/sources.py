"""Outlets and the publication rule, as in ../database/pipeline/corroborate.py:
an incident is published only when two or more distinct outlets report it,
or its primary source is on the repeat-offender database's trusted list."""

from urllib.parse import urlsplit

from config import TRUSTED_OUTLETS


def host(url: str) -> str:
    return urlsplit(url or "").netloc.lower().removeprefix("www.")


def outlets(row: dict) -> set[str]:
    urls = [row.get("source_url", "")] + (row.get("additional_sources") or "").split()
    return {host(u) for u in urls if u}


def is_trusted(url: str) -> bool:
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in TRUSTED_OUTLETS)


def publishable(row: dict) -> bool:
    """Two or more distinct outlets, or a trusted primary source."""
    return is_trusted(row.get("source_url", "")) or len(outlets(row)) >= 2
