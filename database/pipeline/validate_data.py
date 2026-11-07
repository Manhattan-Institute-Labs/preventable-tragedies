"""Validate the whole record set before any workflow commit: both
harvesters' files, and every record that could be published.

    python pipeline/validate_data.py

1. Files. This folder's data/: JSON parses, no git conflict markers, no
   duplicate ids, daily and backfill ids in their own ranges, no id both
   live and removed. The SMI tracker's ../smi/data: its own file checks
   (../smi/pipeline/validate.py: the same integrity checks, plus codebook
   values, a quote for every true signal, rule flags that agree with its
   rules, and articles that point at real incidents), run in a separate
   process because the two folders' modules share names.
2. Records. Every record that could be published (records.candidates():
   every story, and every qualifying SMI incident not kept off the site),
   from either harvester, against one set of rules (row_problems): no
   future or pre-2000 date, a US state, no one under 18, vocabularies, a
   quote behind any release status or category, a strict flag that agrees
   with the counts, no wire or blotter primary source. A repeat-offender
   story must also show a prior record or release status and an arrest,
   charge or conviction; an SMI record qualifies by its own rule instead.

Exits non-zero, listing the problems, if anything fails. quarantine.py takes
failing records off the site; a broken file can only be fixed or discarded.
Corruption can never be committed.
"""

import csv
import json
import re
import subprocess
import sys
from datetime import date, timedelta

from classify import qualifies_strict
from config import (BACKFILL_ID_BASE, BACKFILL_STORIES_CSV, DATA_DIR, MIN_AGE, NEW_OFFENSE_TYPES,
                    OFFENDER_CATEGORIES, OFFENSE_SEVERITIES, RELEASE_STATUSES, REMOVED_CSV, SMI_DIR,
                    STORED_OUTCOME_RE, STORIES_CSV, US_STATES)
from records import SMI_CATEGORY, candidates, is_smi

MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
BAD_SOURCE = re.compile(r"prnewswire|businesswire|globenewswire|einpresswire|press_release|"
                        r"press-release|arrests\.org|mugshots\.com|bustednewspaper", re.I)
CATEGORIES = [*OFFENDER_CATEGORIES, "pending", SMI_CATEGORY]


def _int(v: str) -> int | None:
    return int(v) if v else None


def row_problems(r: dict, horizon: str | None = None) -> list[str]:
    """Every rule a single record can break, whichever harvester found it.
    Shared with quarantine.py."""
    horizon = horizon or (date.today() + timedelta(days=2)).isoformat()
    out = []
    v = r.get("incident_date") or ""
    if v and v[:10] > horizon:
        out.append(f"incident_date {v!r} is in the future")
    if v and v[:4] < "2000":
        out.append(f"incident_date {v!r} is implausibly old")
    if r.get("state") and r["state"] not in US_STATES:
        out.append(f"state {r['state']!r} is not a US state")
    if r.get("category") and r["category"] not in CATEGORIES:
        out.append(f"category {r['category']!r} outside vocabulary")
    if r.get("new_offense_type") and r["new_offense_type"] not in NEW_OFFENSE_TYPES:
        out.append(f"new_offense_type {r['new_offense_type']!r} outside vocabulary")
    if r.get("new_offense_severity") and r["new_offense_severity"] not in OFFENSE_SEVERITIES:
        out.append(f"new_offense_severity {r['new_offense_severity']!r} outside vocabulary")
    if r.get("offender_category"):
        if r["offender_category"] not in OFFENDER_CATEGORIES:
            out.append(f"offender_category {r['offender_category']!r} outside vocabulary")
        elif not r.get("category_reason"):
            out.append("offender_category set without category_reason")
    if r.get("release_status") not in RELEASE_STATUSES:
        out.append(f"release_status {r.get('release_status')!r} outside vocabulary")
    else:
        if r["release_status"] != "none stated" and not r.get("release_evidence_quote"):
            out.append("release_status set without release_evidence_quote")
        if (not is_smi(r) and not r.get("prior_evidence_quote")
                and r["release_status"] == "none stated"):
            out.append("no prior_evidence_quote and no release status")
    try:
        strict = qualifies_strict(_int(r.get("prior_count_arrests", "")),
                                  _int(r.get("prior_count_convictions", "")),
                                  _int(r.get("prior_count_felony_convictions", "")))
        if (r.get("qualifies_strict") == "yes") != strict:
            out.append(f"qualifies_strict {r.get('qualifies_strict')!r} disagrees with counts")
    except ValueError:
        out.append("non-integer prior count")
    if BAD_SOURCE.search(r.get("source_url", "")):
        out.append("wire/blotter primary source")
    if r.get("age") and r["age"].isdigit() and int(r["age"]) < MIN_AGE:
        out.append(f"subject is {r['age']}, under {MIN_AGE}")
    if not is_smi(r) and not re.search(STORED_OUTCOME_RE, r.get("outcome") or "", re.I):
        out.append(f"outcome {r.get('outcome')!r}: not arrested, charged, or convicted")
    return out


def _rows(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def file_problems() -> list[str]:
    """This folder's data files."""
    out = []
    for p in DATA_DIR.glob("*.json"):
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            out.append(f"{p.name} is not valid JSON: {e}")
    for p in list(DATA_DIR.glob("*.csv")) + list(DATA_DIR.glob("backfill/*.csv")):
        if p.name == "quarantine.csv":
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        markers = [m for m in MARKERS if any(line.startswith(m) for line in lines)]
        if markers:
            out.append(f"{p.name} contains git conflict marker {markers[0]!r}")
            continue
        ids = [r["id"] for r in _rows(p) if r.get("id")]
        if len(ids) != len(set(ids)):
            out.append(f"{p.name} has duplicate ids: {sorted({i for i in ids if ids.count(i) > 1})[:5]}")
    if out:
        return out   # the id checks below need files that parse

    daily = _rows(STORIES_CSV) if STORIES_CSV.exists() else []
    backfill = _rows(BACKFILL_STORIES_CSV) if BACKFILL_STORIES_CSV.exists() else []
    if any(int(r["id"]) >= BACKFILL_ID_BASE for r in daily):
        out.append("stories.csv contains an id in the backfill range")
    if any(int(r["id"]) < BACKFILL_ID_BASE for r in backfill):
        out.append("backfill/stories.csv contains an id below the backfill range")
    all_ids = [r["id"] for r in daily + backfill]
    if len(all_ids) != len(set(all_ids)):
        out.append("duplicate ids across stories.csv and backfill/stories.csv")
    if REMOVED_CSV.exists():
        removed = {r["id"] for r in _rows(REMOVED_CSV)}
        if clash := sorted(removed & set(all_ids), key=int)[:5]:
            out.append(f"ids present in both removed.csv and a stories file: {clash}")
    return out


def smi_file_problems() -> list[str]:
    """The SMI tracker's data files, checked by its own validator."""
    run = SMI_DIR / "pipeline" / "run.py"
    if not run.exists():
        return []
    res = subprocess.run([sys.executable, str(run), "validate"], cwd=SMI_DIR,
                         capture_output=True, text=True)
    if res.returncode == 0:
        return []
    lines = [ln.strip() for ln in (res.stdout + res.stderr).splitlines() if ln.strip()]
    return [f"smi: {ln}" for ln in lines] or [f"smi: validator exited {res.returncode}"]


def record_problems() -> list[str]:
    return [f"id {r.get('id')}: {p}" for r in candidates() for p in row_problems(r)]


def problems() -> list[str]:
    files = file_problems()
    # Records are only checked once this folder's files parse.
    return files + smi_file_problems() + ([] if files else record_problems())


def main() -> None:
    found = problems()
    if found:
        for p in found[:30]:
            print(f"  {p}")
        sys.exit(f"DATA VALIDATION FAILED: {len(found)} problem(s)")
    print(f"data validation OK ({len(candidates())} records)")


if __name__ == "__main__":
    main()
