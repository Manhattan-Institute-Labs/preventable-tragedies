"""Build the front-end data files in exports/ from data/stories.csv and the
serious-mental-illness incidents in ../smi/data/incidents.csv.

Outputs:
  exports/stories.csv, exports/stories.json      one record per incident (daily + backfill + SMI)
  exports/offenders.csv, exports/offenders.json  one record per offender_key (this database only)
  exports/removed.json                           tombstones for removed record ids
  exports/stats.json                             counts for headline figures and charts

Every record has `categories`: this database's offender_category ("pending"
until categorize.py reaches it), plus "smi" when the SMI tracker has the
same incident and it meets the SMI inclusion rule. An SMI incident with no
story here is its own record, id SMI_ID_BASE + its number, with "smi" first
and a violent or petty category too when it meets this database's rules
(categorize.py). Which side of a harm an SMI subject was on is not exported.

data/offenders.csv is also written (derived; never edit by hand).
"""

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import date

from config import (CSV_COLUMNS, OFFENDER_COLUMNS, OFFENDERS_CSV, REQUIRE_CORROBORATION, EXPORTS_DIR,
                    SMI_ID_BASE, SMI_INCIDENTS_CSV, SMI_REMOVED_CSV)
from corroborate import pending, publishable
from dedupe import _parse_date
from process import canonical_url
from store import load_all_stories, load_removed

INT_FIELDS = ("id", "age", "prior_count_arrests", "prior_count_convictions",
              "prior_count_felony_convictions", "days_since_release")

SMI_CATEGORY = "smi"
# The SMI tracker's earlier-system-contact signals, in the order a card leads
# with them (its config.QUALIFYING_SIGNALS).
SMI_SIGNALS = ["prior_hospital_discharge", "prior_custody_release", "police_brought_for_eval",
               "prior_police_contact", "prior_arrests", "aot", "edp_call", "family_sought_help",
               "medication_lapse", "psych_medication", "treatment_refusal", "mh_history",
               "diagnosis_named"]
EXPORT_COLUMNS = CSV_COLUMNS + ["categories", "incident_form", "smi_signals", "diagnoses",
                                "days_since_release"]
SAME_INCIDENT_DAYS = 21   # as the SMI tracker's dedupe


def _csv_rows(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def smi_id(incident_id: str) -> int:
    return SMI_ID_BASE + int(re.sub(r"\D", "", incident_id))


def _urls(row: dict) -> set[str]:
    return {canonical_url(u) for u in [row.get("source_url", "")] + (row.get("additional_sources") or "").split() if u}


def same_incident(story: dict, inc: dict) -> bool:
    """A story here and an SMI incident about the same event: they share a
    source article, or the same person key with incident dates within
    SAME_INCIDENT_DAYS of each other."""
    if _urls(story) & _urls(inc):
        return True
    if not story.get("offender_key") or story["offender_key"] != inc.get("subject_key"):
        return False
    a, b = _parse_date(story.get("incident_date", "")), _parse_date(inc.get("incident_date", ""))
    return bool(a and b and abs((a - b).days) <= SAME_INCIDENT_DAYS)


def _smi_fields(inc: dict) -> dict:
    return {"smi_signals": " ".join(f for f in SMI_SIGNALS if inc.get(f) == "true"),
            "diagnoses": inc.get("diagnoses", ""), "days_since_release": inc.get("days_since_release", "")}


def smi_story(inc: dict) -> dict:
    """An SMI incident as a record with this database's columns."""
    category = inc.get("offender_category") or ""
    return {
        **{f: inc.get(f, "") for f in CSV_COLUMNS},
        "id": str(smi_id(inc["id"])),
        "date_added": "",
        "offender_name": inc.get("subject_name", ""),
        "offender_key": inc.get("subject_key", ""),
        "release_status": inc.get("release_status") or "none stated",
        "mugshot_url": "", "mugshot_checked": "",
        "categories": " ".join([SMI_CATEGORY] + ([category] if category else [])),
        "incident_form": inc.get("incident_form", ""),
        **_smi_fields(inc),
    }


def merge_smi(stories: list[dict], incidents: list[dict]) -> list[dict]:
    """Give every story its categories, mark the ones the SMI tracker shares,
    and return the SMI incidents that need records of their own."""
    for s in stories:
        s["categories"] = s.get("offender_category") or "pending"
    own = []
    for inc in incidents:
        match = next((s for s in stories if same_incident(s, inc)), None)
        if match is None:
            own.append(smi_story(inc))
        elif SMI_CATEGORY not in match["categories"].split():
            match["categories"] += " " + SMI_CATEGORY
            match.update(_smi_fields(inc))
    return own


def _max_int(values) -> str:
    ints = [int(v) for v in values if v]
    return str(max(ints)) if ints else ""


def build_offenders(stories: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in stories:
        if s.get("offender_key"):
            groups[s["offender_key"]].append(s)
    out = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: r.get("incident_date") or "")
        dates = [r["incident_date"] for r in rows if r.get("incident_date")]
        # Longest printed name is usually the fullest.
        name = max((r["offender_name"] for r in rows), key=len)
        out.append({
            "offender_key": key,
            "offender_name": name,
            "state": rows[0]["state"],
            "story_ids": " ".join(r["id"] for r in rows),
            "incident_count": str(len(rows)),
            "first_incident_date": dates[0] if dates else "",
            "last_incident_date": dates[-1] if dates else "",
            # Counts as stated in the most recent report, not a maximum across
            # linked incidents: a wrong link must not inflate anyone's record.
            "latest_prior_arrests": rows[-1]["prior_count_arrests"],
            "latest_prior_convictions": rows[-1]["prior_count_convictions"],
            "latest_prior_felony_convictions": rows[-1]["prior_count_felony_convictions"],
            "qualifies_strict": "yes" if any(r["qualifies_strict"] == "yes" for r in rows) else "no",
        })
    out.sort(key=lambda o: (-int(o["incident_count"]), o["offender_key"]))
    return out


def _to_json_record(row: dict) -> dict:
    rec = dict(row)
    for f in INT_FIELDS:
        if f in rec:
            rec[f] = int(rec[f]) if rec[f] else None
    if "additional_sources" in rec:
        rec["additional_sources"] = rec["additional_sources"].split()
    if "story_ids" in rec:
        rec["story_ids"] = [int(i) for i in rec["story_ids"].split()]
    if "incident_count" in rec:
        rec["incident_count"] = int(rec["incident_count"])
    for f in ("categories", "smi_signals"):
        if f in rec:
            rec[f] = rec[f].split()
    for f in ("qualifies_strict",):
        rec[f] = rec.get(f) == "yes"
    return rec


def build_stats(stories: list[dict], offenders: list[dict]) -> dict:
    strict = [s for s in stories if s["qualifies_strict"] == "yes"]
    def by(field, rows):
        return dict(Counter(r[field] for r in rows if r.get(field)).most_common())
    return {
        "updated": date.today().isoformat(),
        "stories": len(stories),
        "stories_strict": len(strict),
        "by_category": dict(Counter(c for s in stories for c in s.get("categories", "").split()).most_common()),
        "offenders": len(offenders),
        "offenders_multiple_incidents": sum(1 for o in offenders if int(o["incident_count"]) > 1),
        "by_state": by("state", stories),
        "by_state_strict": by("state", strict),
        "by_release_status": by("release_status", stories),
        "by_offense_type": by("new_offense_type", stories),
        "by_severity": by("new_offense_severity", stories),
        "by_month": dict(sorted(Counter(s["incident_date"][:7] for s in stories
                                        if len(s.get("incident_date", "")) >= 7).items())),
    }


def build_exports() -> None:
    stories = load_all_stories()
    smi = [r for r in _csv_rows(SMI_INCIDENTS_CSV) if r.get("qualifies_smi") == "yes"]
    if REQUIRE_CORROBORATION:
        # Single-source rows wait for the second-source search; they are in
        # the data files but not on the site.
        held = [s for s in stories if not publishable(s)]
        stories = [s for s in stories if publishable(s)]
        if held:
            print(f"{len(held)} single-source rows held back from the exports"
                  f" ({sum(1 for s in held if pending(s))} awaiting the second-source search)")
        held_smi = sum(1 for r in smi if not publishable(r))
        smi = [r for r in smi if publishable(r)]
        if held_smi:
            print(f"{held_smi} single-source SMI incidents held back from the exports")
    offenders = build_offenders(stories)   # this database's people only
    stories += merge_smi(stories, smi)
    stories.sort(key=lambda r: (r.get("incident_date") or "", int(r["id"])))
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    OFFENDERS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OFFENDERS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OFFENDER_COLUMNS)
        w.writeheader()
        w.writerows(offenders)

    with open(EXPORTS_DIR / "stories.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(stories)
    shutil.copy(OFFENDERS_CSV, EXPORTS_DIR / "offenders.csv")
    (EXPORTS_DIR / "stories.json").write_text(
        json.dumps([_to_json_record({f: s.get(f, "") for f in EXPORT_COLUMNS}) for s in stories],
                   ensure_ascii=False), encoding="utf-8")
    (EXPORTS_DIR / "offenders.json").write_text(
        json.dumps([_to_json_record(o) for o in offenders], ensure_ascii=False), encoding="utf-8")
    # Tombstones: a permalink to a removed record explains itself.
    removed = {r["id"]: {"date": r["removed_date"], "reason": r["removed_reason"]} for r in load_removed()}
    removed.update({str(smi_id(r["id"])): {"date": r["removed_date"], "reason": r["removed_reason"]}
                    for r in _csv_rows(SMI_REMOVED_CSV)})
    (EXPORTS_DIR / "removed.json").write_text(json.dumps(removed, ensure_ascii=False), encoding="utf-8")
    stats = build_stats(stories, offenders)
    (EXPORTS_DIR / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"Exports built: {len(stories)} stories ({stats['by_category'].get(SMI_CATEGORY, 0)} "
          f"serious mental illness), {len(offenders)} offenders")


if __name__ == "__main__":
    build_exports()
