"""Rules both harvesters share: the person key and the publication rule.

The repeat-offender pipeline (this folder) and the serious-mental-illness
tracker (../../smi/) feed one record set, so a person must get the same key
and a record must meet the same sourcing rule whichever harvester found it.
This module is the one copy. The SMI tracker loads it by path
(smi/pipeline/config.py:repeat_offender_module), so it imports nothing but
TRUSTED_OUTLETS, which the SMI config re-exports from this folder's config.
"""

import re
import unicodedata
from urllib.parse import urlsplit

from config import TRUSTED_OUTLETS

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def person_key(name: str | None, state: str | None) -> str:
    """Normalized person key: last name, first name, state. Middle names,
    initials, suffixes, punctuation, and accents are dropped so "John M.
    Smith Jr." and "John Smith" collide. Dedupe confirms the match; this
    only nominates candidates. Empty if there is no name."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z\s]", " ", s.lower())
    parts = [p for p in s.split() if p not in _SUFFIXES]
    if len(parts) < 2:
        return ""
    return f"{parts[-1]}_{parts[0]}_{(state or '').upper()}"


def host(url: str) -> str:
    return urlsplit(url or "").netloc.lower().removeprefix("www.")


def outlets(row: dict) -> set[str]:
    urls = [row.get("source_url", "")] + (row.get("additional_sources") or "").split()
    return {host(u) for u in urls if u}


def is_trusted(url: str) -> bool:
    """Primary source is an outlet on the trusted list (or a subdomain of one)."""
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in TRUSTED_OUTLETS)


def publishable(row: dict) -> bool:
    """Two or more distinct outlets, or a trusted primary source."""
    return is_trusted(row.get("source_url", "")) or len(outlets(row)) >= 2
