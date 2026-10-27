"""Read/write helpers for the articles, incidents and subjects CSVs, the
seen-URL cache, the harvest windows, and subject keys."""

import csv
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from config import (ARTICLE_COLUMNS, ARTICLES_CSV, CROSSLIST_CHECKED_JSON, HARVEST_WINDOWS_JSON,
                    INCIDENT_COLUMNS, INCIDENTS_CSV, REMOVED_INCIDENTS_CSV, SEEN_URLS_JSON,
                    SIGNAL_FIELDS, SUBJECT_COLUMNS, SUBJECTS_CSV)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def subject_key(name: str | None, state: str | None) -> str:
    """Normalized person key: last name, first name, state. Built exactly like
    ../database/pipeline/store.py:offender_key, so the same person gets the
    same key in both databases. Dedupe confirms a match; this only nominates
    candidates. Empty if there is no name."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z\s]", " ", s.lower())
    parts = [p for p in s.split() if p not in _SUFFIXES]
    if len(parts) < 2:
        return ""
    return f"{parts[-1]}_{parts[0]}_{(state or '').upper()}"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict], path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_articles() -> list[dict]:
    return load_rows(ARTICLES_CSV)


def save_articles(rows: list[dict]) -> None:
    save_rows(rows, ARTICLES_CSV, ARTICLE_COLUMNS)


def load_incidents() -> list[dict]:
    return load_rows(INCIDENTS_CSV)


def save_incidents(rows: list[dict]) -> None:
    save_rows(rows, INCIDENTS_CSV, INCIDENT_COLUMNS)


def save_subjects(rows: list[dict]) -> None:
    save_rows(rows, SUBJECTS_CSV, SUBJECT_COLUMNS)


REMOVED_COLUMNS = INCIDENT_COLUMNS + ["removed_date", "removed_reason"]


def load_removed_incidents() -> list[dict]:
    return load_rows(REMOVED_INCIDENTS_CSV)


def save_removed_incidents(rows: list[dict]) -> None:
    save_rows(rows, REMOVED_INCIDENTS_CSV, REMOVED_COLUMNS)


def load_crosslist_checked() -> set[str]:
    if not CROSSLIST_CHECKED_JSON.exists():
        return set()
    return set(json.loads(CROSSLIST_CHECKED_JSON.read_text(encoding="utf-8")))


def save_crosslist_checked(ids: set[str]) -> None:
    CROSSLIST_CHECKED_JSON.parent.mkdir(parents=True, exist_ok=True)
    CROSSLIST_CHECKED_JSON.write_text(json.dumps(sorted(ids, key=int), indent=0), encoding="utf-8")


ARTICLE_PREFIX = "SMI-"
INCIDENT_PREFIX = "INC-"


def next_number(ids, prefix: str) -> int:
    best = 0
    for i in ids:
        tail = str(i or "").removeprefix(prefix)
        if tail.isdigit():
            best = max(best, int(tail))
    return best + 1


def next_id(rows: list[dict]) -> int:
    """Next article number."""
    return next_number((r.get("id") for r in rows), ARTICLE_PREFIX)


def format_id(n: int) -> str:
    return f"{ARTICLE_PREFIX}{n:06d}"


def format_incident_id(n: int) -> str:
    return f"{INCIDENT_PREFIX}{n:06d}"


def load_seen_urls(path: Path | None = None) -> set[str]:
    path = path or SEEN_URLS_JSON
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return set(json.load(f))


def save_seen_urls(urls: set[str], path: Path | None = None) -> None:
    path = path or SEEN_URLS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, indent=0)


def load_windows() -> list[dict]:
    if not HARVEST_WINDOWS_JSON.exists():
        return []
    return json.loads(HARVEST_WINDOWS_JSON.read_text(encoding="utf-8"))


def save_windows(windows: list[dict]) -> None:
    HARVEST_WINDOWS_JSON.parent.mkdir(parents=True, exist_ok=True)
    HARVEST_WINDOWS_JSON.write_text(json.dumps(windows, indent=1), encoding="utf-8")


def _b(v) -> str:
    return "true" if v else "false"


def _evidence(quotes: dict[str, str]) -> str:
    """signal_evidence column: one 'field: sentence' per true signal."""
    return " || ".join(f"{f}: {q}" for f, q in quotes.items())


_UNREVIEWED = {"review_status": "pending", "reviewed_date": "", "excluded_reason": "",
               "incident_id": "", "contribution": "", "added_info": ""}


def _int_str(v) -> str:
    return "" if v is None else str(int(v))


_NO_RECORD = {"prior_count_arrests": "", "prior_count_convictions": "",
              "prior_count_felony_convictions": "", "prior_offenses": "", "prior_evidence_quote": "",
              "release_status": "none stated", "release_evidence_quote": "",
              "releasing_jurisdiction": ""}


def claude_fields(cls, candidate: dict) -> dict:
    """The coded fields from a verified classification."""
    quotes = {f: cls.signal_quotes[f] for f in SIGNAL_FIELDS if f in cls.signal_quotes}
    return {
        "incident_date": cls.incident_date or candidate.get("published", ""),
        "city": cls.city or "",
        "state": cls.state or "",
        "subject_name": cls.subject_name or "",
        "subject_key": subject_key(cls.subject_name, cls.state),
        "age": "" if cls.age is None else str(cls.age),
        "sex": cls.sex or "",
        "subject_role": cls.subject_role or "",
        "other_party": cls.other_party or "",
        "incident_form": cls.incident_form or "",
        "fatality": _b(cls.fatality),
        "violence": _b(cls.violence),
        "killed_by_police": _b(cls.killed_by_police),
        "new_offense_type": cls.new_offense_type or "",
        "new_offense_severity": cls.new_offense_severity or "",
        **{f: _b(f in quotes) for f in SIGNAL_FIELDS},
        "diagnoses": cls.diagnoses or "",
        "days_since_release": "" if cls.days_since_release is None else str(cls.days_since_release),
        "mh_evidence_quote": cls.mh_evidence_quote or "",
        "signal_evidence": _evidence(quotes),
        "prior_count_arrests": _int_str(cls.prior_count_arrests),
        "prior_count_convictions": _int_str(cls.prior_count_convictions),
        "prior_count_felony_convictions": _int_str(cls.prior_count_felony_convictions),
        "prior_offenses": cls.prior_offenses or "",
        "prior_evidence_quote": cls.prior_evidence_quote or "",
        "release_status": cls.release_status,
        "release_evidence_quote": cls.release_evidence_quote or "",
        "releasing_jurisdiction": cls.releasing_jurisdiction or "",
        "failure_point": cls.failure_point or "",
        "responsible_system": cls.responsible_system or "",
        "evidence_tier": cls.evidence_tier,
        "outcome": cls.outcome or "",
        "summary": cls.summary or "",
        "confidence": cls.confidence,
        "coded_by": "claude",
        "review_note": cls.reason or "",
    }


def make_row(row_id: int, cls, candidate: dict, coding: dict | None = None) -> dict:
    """An article row from a verified classification and its candidate."""
    return {
        "id": format_id(row_id),
        "date_added": date.today().isoformat(),
        "published": candidate.get("published", ""),
        "headline": candidate.get("title", ""),
        **claude_fields(cls, candidate),
        "source_name": candidate.get("source", ""),
        "source_url": candidate["url"],
        "additional_sources": "",
        "trigger_phrases": " | ".join((coding or {}).get("triggers", [])),
        "repeat_offender_ids": "",
        **_UNREVIEWED,
    }


def make_candidate_row(row_id: int, coding: dict, candidate: dict) -> dict:
    """An article row from the phrase book alone. Name, age, and location need
    Claude; they are blank here."""
    quotes = {f: coding["signals"][f] for f in SIGNAL_FIELDS if f in coding["signals"]}
    return {
        "id": format_id(row_id),
        "date_added": date.today().isoformat(),
        "published": candidate.get("published", ""),
        "headline": candidate.get("title", ""),
        "incident_date": candidate.get("published", ""),
        "city": "", "state": "", "subject_name": "", "subject_key": "", "age": "", "sex": "",
        "subject_role": coding["subject_role"],
        "other_party": coding["other_party"],
        "incident_form": coding["incident_form"],
        "fatality": _b(coding["fatality"]),
        "violence": _b(coding["violence"]),
        "killed_by_police": _b(coding.get("killed_by_police")),
        "new_offense_type": "", "new_offense_severity": "",
        **{f: _b(f in quotes) for f in SIGNAL_FIELDS},
        "diagnoses": "; ".join(coding["diagnoses"]),
        "days_since_release": "" if coding["days_since_release"] is None
                              else str(coding["days_since_release"]),
        "mh_evidence_quote": coding.get("mh_sentence", ""),
        "signal_evidence": _evidence(quotes),
        **_NO_RECORD,   # counts and release status need Claude and the count checks
        "failure_point": "", "responsible_system": "", "evidence_tier": "",
        "outcome": "",
        "summary": candidate.get("title", ""),
        "source_name": candidate.get("source", ""),
        "source_url": candidate["url"],
        "additional_sources": "",
        "confidence": "low",
        "trigger_phrases": " | ".join(coding["triggers"]),
        "repeat_offender_ids": "",
        "coded_by": "phrasebook",
        "review_note": "",
        **_UNREVIEWED,
    }
