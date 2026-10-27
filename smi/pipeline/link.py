"""Link SMI incidents to the repeat-offender database.

A subject who also appears in ../database/ (same person key: last name,
first name, state) gets that database's story ids in repeat_offender_ids.
The repeat-offender files are only read. As in that database's dedupe, a
report with no state matches on the name alone, and two rows whose ages
cannot belong to one person are not linked.
"""

import csv
from pathlib import Path

from config import REPEAT_OFFENDER_STORIES
from dedupe import ages_consistent


def _load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        if p.exists():
            with open(p, newline="", encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
    return rows


def _name_part(key: str) -> str:
    return key.rsplit("_", 1)[0] if key else ""


def link_repeat_offenders(rows: list[dict], story_paths: list[Path] | None = None) -> int:
    """Fill repeat_offender_ids on each row, in place. Returns how many rows
    have at least one link."""
    stories = _load(story_paths or REPEAT_OFFENDER_STORIES)
    by_key: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for s in stories:
        key = s.get("offender_key") or ""
        if key:
            by_key.setdefault(key, []).append(s)
            by_name.setdefault(_name_part(key), []).append(s)

    linked = 0
    for r in rows:
        key = r.get("subject_key") or ""
        if not key:
            r["repeat_offender_ids"] = ""
            continue
        matches = list(by_key.get(key, []))
        stateless = not r.get("state")
        for s in by_name.get(_name_part(key), []):
            if s not in matches and (stateless or not s.get("state")):
                matches.append(s)
        # The repeat-offender rows carry age; compare it the same way dedupe does.
        matches = [s for s in matches if ages_consistent(r, s)]
        r["repeat_offender_ids"] = " ".join(dict.fromkeys(s["id"] for s in matches))
        linked += bool(matches)
    return linked
