"""Take records that fail validation off the site, keeping them for review.

Run by the workflows when validate_data.py fails, so one bad record (a model
misreading a date as next week) can never throw away a 45-minute run.

A repeat-offender story is moved into data/quarantine.csv with the reason;
it can be fixed and moved back. A serious-mental-illness record cannot be
moved out of ../smi/data/incidents.csv (the next incidents build would bring
it back), so its site_excluded column gets "quarantined DATE: reason"
instead, which every rebuild keeps and the exports respect; clear it once
the incident is fixed.
"""

import csv
from datetime import date

import records
from config import BACKFILL_STORIES_CSV, CSV_COLUMNS, DATA_DIR, STORIES_CSV
from store import load_stories, save_stories
from validate_data import row_problems

QUARANTINE_CSV = DATA_DIR / "quarantine.csv"


def quarantine_smi() -> int:
    """Keep failing SMI records off the site. Returns how many."""
    bad = {}
    for r in records.candidates():
        if records.is_smi(r) and (problems := row_problems(r)):
            bad[r["incident_id"]] = "; ".join(problems)
    if not bad:
        return 0
    path = records.SMI_INCIDENTS_CSV
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        if row["id"] in bad:
            row["site_excluded"] = f"quarantined {date.today().isoformat()}: {bad[row['id']]}"
            print(f"quarantined SMI incident {row['id']}: {bad[row['id']]}")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return len(bad)


def main() -> None:
    moved = quarantine_smi()
    for path in (STORIES_CSV, BACKFILL_STORIES_CSV):
        rows = load_stories(path)
        keep, bad = [], []
        for r in rows:
            problems = row_problems(r)
            if problems:
                bad.append(dict(r, quarantined=date.today().isoformat(),
                                reason="; ".join(problems)))
            else:
                keep.append(r)
        if not bad:
            continue
        save_stories(keep, path)
        exists = QUARANTINE_CSV.exists()
        with open(QUARANTINE_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS + ["quarantined", "reason"],
                               extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerows(bad)
        for r in bad:
            print(f"quarantined id {r['id']}: {r['reason']}")
        moved += len(bad)
    print(f"quarantined {moved} records")


if __name__ == "__main__":
    main()
