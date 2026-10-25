"""Read/write helpers for the review queue, the incidents CSV, the seen-URL
cache, and subject keys."""

import csv
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from config import CSV_COLUMNS, INCIDENTS_CSV, REVIEW_QUEUE_CSV, SEEN_URLS_JSON, SIGNAL_FIELDS

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


def load_rows(path: Path | None = None) -> list[dict]:
    path = path or INCIDENTS_CSV
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict], path: Path | None = None) -> None:
    path = path or INCIDENTS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_queue() -> list[dict]:
    return load_rows(REVIEW_QUEUE_CSV)


def save_queue(rows: list[dict]) -> None:
    save_rows(rows, REVIEW_QUEUE_CSV)


ID_PREFIX = "SMI-"


def next_id(rows: list[dict]) -> int:
    best = 0
    for r in rows:
        tail = str(r.get("id", "")).removeprefix(ID_PREFIX)
        if tail.isdigit():
            best = max(best, int(tail))
    return best + 1


def format_id(n: int) -> str:
    return f"{ID_PREFIX}{n:06d}"


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


def _b(v) -> str:
    return "true" if v else "false"


def _evidence(quotes: dict[str, str]) -> str:
    """signal_evidence column: one 'field: sentence' per true signal."""
    return " || ".join(f"{f}: {q}" for f, q in quotes.items())


def make_row(row_id: int, cls, candidate: dict, coding: dict | None = None) -> dict:
    """Build a CSV row from a verified classification and its candidate."""
    quotes = {f: cls.signal_quotes[f] for f in SIGNAL_FIELDS if f in cls.signal_quotes}
    return {
        "id": format_id(row_id),
        "date_added": date.today().isoformat(),
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
        **{f: _b(f in quotes) for f in SIGNAL_FIELDS},
        "diagnoses": cls.diagnoses or "",
        "days_since_release": "" if cls.days_since_release is None else str(cls.days_since_release),
        "mh_evidence_quote": cls.mh_evidence_quote or "",
        "signal_evidence": _evidence(quotes),
        "failure_point": cls.failure_point or "",
        "responsible_system": cls.responsible_system or "",
        "evidence_tier": cls.evidence_tier,
        "outcome": cls.outcome or "",
        "summary": cls.summary or "",
        "source_name": candidate.get("source", ""),
        "source_url": candidate["url"],
        "additional_sources": "",
        "confidence": cls.confidence,
        "trigger_phrases": " | ".join((coding or {}).get("triggers", [])),
        "repeat_offender_ids": "",
        "coded_by": "claude",
        "review_note": cls.reason or "",
        **_PENDING,
    }


_PENDING = {"review_status": "pending", "reviewed_date": "", "duplicate_of": ""}


def make_candidate_row(row_id: int, coding: dict, candidate: dict) -> dict:
    """Build a CSV row from the phrase book alone (a --no-llm run). Name, age,
    and location need the classifier or the reviewer; they are blank here."""
    quotes = {f: coding["signals"][f] for f in SIGNAL_FIELDS if f in coding["signals"]}
    notes = []
    if coding.get("killed_by_police"):
        notes.append("possible police killing: excluded if confirmed")
    return {
        "id": format_id(row_id),
        "date_added": date.today().isoformat(),
        "incident_date": candidate.get("published", ""),
        "city": "", "state": "", "subject_name": "", "subject_key": "", "age": "", "sex": "",
        "subject_role": coding["subject_role"],
        "other_party": coding["other_party"],
        "incident_form": coding["incident_form"],
        "fatality": _b(coding["fatality"]),
        "violence": _b(coding["violence"]),
        **{f: _b(f in quotes) for f in SIGNAL_FIELDS},
        "diagnoses": "; ".join(coding["diagnoses"]),
        "days_since_release": "" if coding["days_since_release"] is None
                              else str(coding["days_since_release"]),
        "mh_evidence_quote": coding.get("mh_sentence", ""),
        "signal_evidence": _evidence(quotes),
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
        "review_note": "; ".join(notes),
        **_PENDING,
    }
