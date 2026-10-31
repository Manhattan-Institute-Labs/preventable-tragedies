"""The one record set: every case either harvester found, in one schema, each
with exactly one category.

Two harvesters feed it. This folder's pipeline finds repeat offenders and
writes data/stories.csv (daily) and data/backfill/stories.csv; the
serious-mental-illness tracker (../../smi/) finds people with serious mental
illness and writes ../smi/data/incidents.csv. Everything downstream of the
harvest reads this module, not those files: validation (validate_data.py),
quarantine (quarantine.py), the exports and the people list
(build_exports.py), and so the site.

Categories are discrete. A repeat-offender story's is its offender_category
(violent, petty or unclassified; "pending" until categorize.py reaches it).
An SMI incident's is "smi", whatever the subject's record. When both
harvesters have the same incident, the SMI record is the one shown and the
story is superseded: it stays in data/stories.csv, its permalink points to
the SMI record, and it is not on the site twice.

Ids: daily stories below BACKFILL_ID_BASE, backfill stories from it, SMI
incidents from SMI_ID_BASE (INC-000123 is 2000123).
"""

import csv
import re

from config import CSV_COLUMNS, REQUIRE_CORROBORATION, SMI_ID_BASE, SMI_INCIDENTS_CSV
from dedupe import _parse_date
from process import canonical_url
from shared import publishable
from store import load_all_stories

SMI_CATEGORY = "smi"
# The SMI tracker's earlier-system-contact signals, in the order a card leads
# with them (its config.QUALIFYING_SIGNALS).
SMI_SIGNALS = ["prior_hospital_discharge", "prior_custody_release", "police_brought_for_eval",
               "prior_police_contact", "prior_arrests", "aot", "edp_call", "family_sought_help",
               "medication_lapse", "psych_medication", "treatment_refusal", "mh_history",
               "diagnosis_named"]
RECORD_COLUMNS = CSV_COLUMNS + ["category", "incident_form", "smi_signals", "diagnoses",
                                "days_since_release"]
SAME_INCIDENT_DAYS = 21   # as the SMI tracker's dedupe


def _csv_rows(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def smi_id(incident_id: str) -> int:
    return SMI_ID_BASE + int(re.sub(r"\D", "", incident_id))


def is_smi(record: dict) -> bool:
    return record.get("category") == SMI_CATEGORY


def story_record(story: dict) -> dict:
    return {**story, "category": story.get("offender_category") or "pending"}


def smi_record(inc: dict) -> dict:
    """An SMI incident as a record. subject_role and incident_id are kept for
    this module's callers; the exports do not publish them."""
    return {
        **{f: inc.get(f, "") for f in CSV_COLUMNS},
        "id": str(smi_id(inc["id"])),
        "date_added": "",
        "offender_name": inc.get("subject_name", ""),
        "offender_key": inc.get("subject_key", ""),
        "release_status": inc.get("release_status") or "none stated",
        "mugshot_url": "", "mugshot_checked": "",
        "offender_category": "", "category_reason": "",
        "category": SMI_CATEGORY,
        "incident_form": inc.get("incident_form", ""),
        "smi_signals": " ".join(f for f in SMI_SIGNALS if inc.get(f) == "true"),
        "diagnoses": inc.get("diagnoses", ""),
        "days_since_release": inc.get("days_since_release", ""),
        "subject_role": inc.get("subject_role", ""),
        "incident_id": inc["id"],
    }


def smi_incidents() -> list[dict]:
    return _csv_rows(SMI_INCIDENTS_CSV)


def off_site_smi(incidents: list[dict] | None = None) -> list[dict]:
    """SMI incidents kept in the data but off the site, by hand or by
    quarantine (their site_excluded column says why)."""
    return [r for r in (smi_incidents() if incidents is None else incidents) if r.get("site_excluded")]


def candidates() -> list[dict]:
    """Every record that could be published: all stories, and the SMI
    incidents that meet its inclusion rule and are not kept off the site.
    Validation holds every one of these to the same rules."""
    smi = [smi_record(r) for r in smi_incidents()
           if r.get("qualifies_smi") == "yes" and not r.get("site_excluded")]
    return [story_record(s) for s in load_all_stories()] + smi


def _urls(row: dict) -> set[str]:
    return {canonical_url(u) for u in [row.get("source_url", "")] + (row.get("additional_sources") or "").split() if u}


def same_incident(a: dict, b: dict) -> bool:
    """Two records about the same event: they share a source article, or the
    same person key with incident dates within SAME_INCIDENT_DAYS."""
    if _urls(a) & _urls(b):
        return True
    if not a.get("offender_key") or a["offender_key"] != b.get("offender_key"):
        return False
    da, db = _parse_date(a.get("incident_date", "")), _parse_date(b.get("incident_date", ""))
    return bool(da and db and abs((da - db).days) <= SAME_INCIDENT_DAYS)


def site_records(records: list[dict]) -> tuple[list[dict], list[dict], list[tuple[dict, dict]]]:
    """(shown, held, superseded). Held records wait for a second source.
    Superseded are (story, smi record) pairs: the story is the same incident
    as a publishable SMI record, which is shown instead."""
    held = [r for r in records if REQUIRE_CORROBORATION and not publishable(r)]
    ready = [r for r in records if not (REQUIRE_CORROBORATION and not publishable(r))]
    smi = [r for r in ready if is_smi(r)]
    shown, superseded = [], []
    for r in ready:
        match = None if is_smi(r) else next((m for m in smi if same_incident(r, m)), None)
        if match is None:
            shown.append(r)
        else:
            superseded.append((r, match))
    return shown, held, superseded


def is_offender(record: dict) -> bool:
    """A person the record accuses: every story, and an SMI subject unless
    the incident harmed them rather than being something they did."""
    return not is_smi(record) or record.get("subject_role") != "subject_victim"
