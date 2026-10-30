"""Build the front-end data files in exports/ from the one record set
(records.py): the repeat-offender stories and the serious-mental-illness
incidents, each with one category.

Outputs:
  exports/stories.csv, exports/stories.json      one record per incident shown on the site
  exports/offenders.csv, exports/offenders.json  one record per person key, from both harvesters
  exports/removed.json                           tombstones for record ids no longer shown
  exports/stats.json                             counts for headline figures and charts

Every record has one `category`: violent, petty, unclassified or pending for
a repeat-offender story, smi for a serious-mental-illness incident. A story
that is the same incident as a published SMI record is superseded: its
tombstone points to the SMI record. Which side of a harm an SMI subject was
on is not exported, but a subject the incident harmed is not listed as an
offender.

data/offenders.csv is also written (derived; never edit by hand).
"""

import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import date

from config import EXPORTS_DIR, OFFENDER_COLUMNS, OFFENDERS_CSV, SMI_REMOVED_CSV
from corroborate import pending
from records import (RECORD_COLUMNS, SMI_CATEGORY, _csv_rows, candidates, is_offender, is_smi,
                     off_site_smi, site_records, smi_id)
from store import load_removed

INT_FIELDS = ("id", "age", "prior_count_arrests", "prior_count_convictions",
              "prior_count_felony_convictions", "days_since_release")
EXPORT_COLUMNS = RECORD_COLUMNS


def _max_int(values) -> str:
    ints = [int(v) for v in values if v]
    return str(max(ints)) if ints else ""


def build_offenders(records: list[dict]) -> list[dict]:
    """One row per person key, across both harvesters' records."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in records:
        if s.get("offender_key") and is_offender(s):
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
    if "smi_signals" in rec:
        rec["smi_signals"] = rec["smi_signals"].split()
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
        "by_category": by("category", stories),
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
    off_site = off_site_smi()
    if off_site:
        print(f"{len(off_site)} SMI incidents kept off the site (site_excluded)")
    stories, held, superseded = site_records(candidates())
    if held:
        # Single-source records wait for the second-source search; they are
        # in the data files but not on the site.
        print(f"{len(held)} single-source records held back from the exports"
              f" ({sum(1 for s in held if is_smi(s))} SMI;"
              f" {sum(1 for s in held if not is_smi(s) and pending(s))} stories awaiting the search)")
    if superseded:
        print(f"{len(superseded)} stories shown as the SMI record of the same incident")
    offenders = build_offenders(stories)
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
    # Tombstones: a permalink to a record no longer shown explains itself.
    removed = {r["id"]: {"date": r["removed_date"], "reason": r["removed_reason"]} for r in load_removed()}
    removed.update({str(smi_id(r["id"])): {"date": r["removed_date"], "reason": r["removed_reason"]}
                    for r in _csv_rows(SMI_REMOVED_CSV)})
    removed.update({str(smi_id(r["id"])): {"date": "", "reason": f"kept off the site: {r['site_excluded']}"}
                    for r in off_site})
    removed.update({s["id"]: {"date": "", "reason": "listed as a serious-mental-illness case",
                              "see": int(m["id"])} for s, m in superseded})
    (EXPORTS_DIR / "removed.json").write_text(json.dumps(removed, ensure_ascii=False), encoding="utf-8")
    stats = build_stats(stories, offenders)
    (EXPORTS_DIR / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"Exports built: {len(stories)} records ({stats['by_category'].get(SMI_CATEGORY, 0)} "
          f"serious mental illness), {len(offenders)} offenders")


if __name__ == "__main__":
    build_exports()
