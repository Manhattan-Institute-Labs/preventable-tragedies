"""Incident- and person-level matching, used by incidents.py to group articles.

Adapted from ../database/pipeline/dedupe.py. Several outlets cover one
incident, and one person recurs across months (an incident, then a
competency hearing, then a sentencing). An article is
compared against existing incidents for the same subject key (any date) and
the same state within a date window, and is one of three things:

  same_incident            -> joins that incident: its URL is added and it
                              fills the incident's blanks (merge_into)
  same_person_new_incident -> a new incident that shares the subject_key
  unrelated                -> a new incident

Follow-up coverage of one incident's criminal case (arrest, competency
hearing, plea, sentencing) is the same incident. Later proceedings on a
decided case (an execution, an appeal, a release hearing, a lawsuit) are
court-only stories, which the classifier excludes. The clear cases are
decided by rule; Haiku decides the rest.
"""

from datetime import date, datetime
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel

from config import DEDUPE_MODEL, PRIOR_COUNT_FIELDS, SIGNAL_FIELDS
from store import subject_key

DATE_WINDOW_DAYS = 21
MAX_CANDIDATES = 20


class DedupeResult(BaseModel):
    relation: Literal["same_incident", "same_person_new_incident", "unrelated"]
    matching_id: Optional[str] = None


def _parse_date(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value or "", fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _name_part(key: str) -> str:
    """subject_key without its state suffix, so a report that omits the state
    still matches the same person."""
    return key.rsplit("_", 1)[0] if key else ""


def same_person(a: dict, b: dict) -> bool:
    ka, kb = a.get("subject_key", ""), b.get("subject_key", "")
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    return _name_part(ka) == _name_part(kb) and (not a.get("state") or not b.get("state"))


def obvious_same_incident(new_row: dict, s: dict) -> bool:
    """Same person, incident dates within the window (or one missing), and the
    same city or the same incident form: merged without asking the model."""
    if not same_person(new_row, s):
        return False
    a, b = _parse_date(new_row.get("incident_date", "")), _parse_date(s.get("incident_date", ""))
    if a and b and abs((a - b).days) > DATE_WINDOW_DAYS:
        return False
    same_city = bool(new_row.get("city")) and \
        (new_row.get("city") or "").strip().lower() == (s.get("city") or "").strip().lower()
    same_form = bool(new_row.get("incident_form")) and new_row.get("incident_form") == s.get("incident_form")
    return same_city or same_form


def ages_consistent(a: dict, b: dict) -> bool:
    """False only when both rows give an age and the ages cannot belong to one
    person given the gap between incidents (a year of slack for birthdays and
    rounding). A wrong link gives someone another person's record."""
    try:
        age_a, age_b = int(a.get("age") or 0), int(b.get("age") or 0)
    except ValueError:
        return True
    if not age_a or not age_b:
        return True
    da, db = _parse_date(a.get("incident_date", "")), _parse_date(b.get("incident_date", ""))
    if not da or not db:
        return abs(age_a - age_b) <= 2
    years = abs((da - db).days) / 365.25
    return abs(abs(age_a - age_b) - years) <= 1.5


def find_candidates(new_row: dict, rows: list[dict]) -> list[dict]:
    new_date = _parse_date(new_row.get("incident_date", ""))
    out = []
    for s in rows:
        if same_person(new_row, s):
            out.append(s)
            continue
        if not new_row.get("state") or s.get("state") != new_row.get("state"):
            continue
        old_date = _parse_date(s.get("incident_date", ""))
        if new_date and old_date and abs((new_date - old_date).days) > DATE_WINDOW_DAYS:
            continue
        out.append(s)

    def _rank(s: dict):
        same_person_rank = 0 if same_person(new_row, s) else 1
        added = _parse_date(s.get("date_added", ""))
        recency = -(added.toordinal() if added else 0)
        old_date = _parse_date(s.get("incident_date", ""))
        if not new_date or not old_date:
            return (same_person_rank, 1, 0, recency)
        return (same_person_rank, 0, abs((new_date - old_date).days), recency)
    out.sort(key=_rank)
    return out[:MAX_CANDIDATES]


def _describe(s: dict) -> str:
    return (f"{s.get('incident_date', '')} | {s.get('city', '')}, {s.get('state', '')} | "
            f"{s.get('subject_name') or '(unnamed)'} | {s.get('subject_role', '')} | "
            f"{s.get('incident_form', '')} | {s.get('summary', '')}")


def check_duplicate(client: anthropic.Anthropic, new_row: dict,
                    rows: list[dict]) -> DedupeResult:
    candidates = find_candidates(new_row, rows)
    if not candidates:
        return DedupeResult(relation="unrelated")
    for s in candidates:
        if obvious_same_incident(new_row, s):
            return DedupeResult(relation="same_incident", matching_id=s["id"])

    existing = "\n".join(f"- id {s['id']}: {_describe(s)}" for s in candidates)
    response = client.messages.parse(
        model=DEDUPE_MODEL,
        max_tokens=256,
        system=("You deduplicate a database of news-reported incidents involving people "
                "with serious mental illness. Decide how the new entry relates to the "
                "existing entries.\n"
                "- same_incident: the same person and the same incident. Follow-up "
                "coverage of one incident's criminal case (arrest, competency hearing, "
                "plea, sentencing) is the same incident. Different "
                "outlets often report slightly different dates and details for one event.\n"
                "- same_person_new_incident: clearly the same person, but a different "
                "incident on a different occasion.\n"
                "- unrelated: a different person, or you cannot tell.\n"
                "Same name in the same state is strong evidence of the same person. "
                "If unsure between same_incident and unrelated, choose unrelated."),
        messages=[{"role": "user",
                   "content": f"Existing entries:\n{existing}\n\nNew entry:\n{_describe(new_row)}"}],
        output_format=DedupeResult,
    )
    result = response.parsed_output
    known = {s["id"] for s in candidates}
    if result.relation != "unrelated" and result.matching_id not in known:
        return DedupeResult(relation="unrelated")
    return result


def _split_evidence(value: str) -> dict[str, str]:
    out = {}
    for part in (value or "").split(" || "):
        field, sep, quote = part.partition(": ")
        if sep and field in SIGNAL_FIELDS:
            out[field] = quote
    return out


def _diagnoses(row: dict) -> list[str]:
    return [d.strip() for d in (row.get("diagnoses") or "").split(";") if d.strip()]


def _count(row: dict, f: str) -> int:
    v = str(row.get(f) or "")
    return int(v) if v.isdigit() else 0


def _no_release(row: dict) -> bool:
    return row.get("release_status") in ("", None, "none stated")


# The facts an incident can be missing. A later report is kept only if it
# supplies one of these (incidents.py); otherwise it adds nothing.
IDENTITY_FIELDS = ("subject_name", "age", "sex", "city", "state")
OUTCOME_FLAGS = ("fatality", "violence", "killed_by_police")


def new_info(keep: dict, new_row: dict) -> list[str]:
    """What new_row reports that the incident does not have yet: a system-
    contact signal, a named diagnosis, a prior count (or a higher one), prior
    offenses, a release status, days since release, a death, violence or a
    police killing, or a missing name, age, sex or place."""
    out = [f for f in SIGNAL_FIELDS if new_row.get(f) == "true" and keep.get(f) != "true"]
    have = {d.lower() for d in _diagnoses(keep)}
    out += [f"diagnosis: {d}" for d in _diagnoses(new_row) if d.lower() not in have]
    out += [f for f in PRIOR_COUNT_FIELDS if _count(new_row, f) > _count(keep, f)]
    if new_row.get("prior_offenses") and not keep.get("prior_offenses"):
        out.append("prior_offenses")
    if not _no_release(new_row) and _no_release(keep):
        out.append("release_status")
    if new_row.get("days_since_release") and not keep.get("days_since_release"):
        out.append("days_since_release")
    out += [f for f in OUTCOME_FLAGS if new_row.get(f) == "true" and keep.get(f) != "true"]
    out += [f for f in IDENTITY_FIELDS if new_row.get(f) and not keep.get(f)]
    return out


def merge_into(keep: dict, new_row: dict) -> None:
    """Fold a second report of the same incident into the stored row: its URL
    joins additional_sources, and it fills what the first report lacked. A
    signal one report supports is true for the incident, with its quote. A
    value the stored row already has is never overwritten, except a prior
    count, which takes the higher stated number (as the repeat-offender
    dedupe does) together with the quote that states it."""
    url = new_row.get("source_url", "")
    urls = [u for u in (keep.get("additional_sources") or "").split() if u]
    for u in [url] + (new_row.get("additional_sources") or "").split():
        if u and u != keep.get("source_url") and u not in urls:
            urls.append(u)
    keep["additional_sources"] = " ".join(urls)

    for f in ("subject_name", "age", "sex", "city", "state", "other_party", "incident_form",
              "new_offense_type", "new_offense_severity", "days_since_release",
              "failure_point", "responsible_system", "outcome", "mh_evidence_quote"):
        if not keep.get(f) and new_row.get(f):
            keep[f] = new_row[f]
    if keep.get("subject_name"):   # a name or state may have just been filled
        keep["subject_key"] = subject_key(keep["subject_name"], keep.get("state"))
    if len(new_row.get("incident_date") or "") > len(keep.get("incident_date") or ""):
        keep["incident_date"] = new_row["incident_date"]   # the more precise date
    for f in OUTCOME_FLAGS:
        if new_row.get(f) == "true":
            keep[f] = "true"

    higher = [f for f in PRIOR_COUNT_FIELDS if _count(new_row, f) > _count(keep, f)]
    for f in higher:
        keep[f] = new_row[f]
    if new_row.get("prior_evidence_quote") and (higher or not keep.get("prior_evidence_quote")):
        keep["prior_evidence_quote"] = new_row["prior_evidence_quote"]
    if new_row.get("prior_offenses") and not keep.get("prior_offenses"):
        keep["prior_offenses"] = new_row["prior_offenses"]
    if _no_release(keep) and not _no_release(new_row):
        for f in ("release_status", "release_evidence_quote", "releasing_jurisdiction"):
            keep[f] = new_row.get(f, "")

    evidence = _split_evidence(keep.get("signal_evidence", ""))
    for f, quote in _split_evidence(new_row.get("signal_evidence", "")).items():
        if keep.get(f) != "true":
            keep[f] = "true"
            evidence[f] = f"{quote} [{url}]"
    keep["signal_evidence"] = " || ".join(f"{f}: {q}" for f, q in evidence.items())

    diagnoses = _diagnoses(keep)
    for d in _diagnoses(new_row):
        if d.lower() not in {x.lower() for x in diagnoses}:
            diagnoses.append(d)
    keep["diagnoses"] = "; ".join(diagnoses)
