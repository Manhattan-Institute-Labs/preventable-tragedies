"""Validate smi/data before any workflow commit, like
../database/pipeline/validate_data.py: corruption can never be committed.

    python pipeline/run.py validate

Exits non-zero on: git conflict markers, unparseable JSON, duplicate ids,
values outside the codebook, a true signal with no quoted evidence, a prior
count or release status with no quote, a rule flag that disagrees with
rules.py, or an id in both incidents.csv and removed_incidents.csv.
"""

import csv
import json
import sys

from config import (ARTICLES_CSV, CONTRIBUTIONS, DATA_DIR, INCIDENT_FORMS, INCIDENTS_CSV,
                    NEW_OFFENSE_TYPES, OFFENSE_SEVERITIES, OTHER_PARTIES, PRIOR_COUNT_FIELDS,
                    RELEASE_STATUSES, REMOVED_INCIDENTS_CSV, REVIEW_STATUSES, SIGNAL_FIELDS,
                    SUBJECT_ROLES)
from dedupe import _split_evidence
from rules import meets_repeat_offender_rules, qualifies_smi, strict

MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

VOCAB = {"subject_role": SUBJECT_ROLES, "other_party": OTHER_PARTIES,
         "incident_form": INCIDENT_FORMS, "new_offense_type": NEW_OFFENSE_TYPES,
         "new_offense_severity": OFFENSE_SEVERITIES, "release_status": RELEASE_STATUSES}


def coded_problems(r: dict) -> list[str]:
    """Rules any coded row (article or incident) can break."""
    out = []
    for field, values in VOCAB.items():
        if r.get(field) and r[field] not in values:
            out.append(f"{field} {r[field]!r} outside the codebook")
    evidence = _split_evidence(r.get("signal_evidence", ""))
    for f in SIGNAL_FIELDS:
        if r.get(f) == "true" and f not in evidence:
            out.append(f"{f} is true with no quoted evidence")
    for f in PRIOR_COUNT_FIELDS:
        if r.get(f) and not r[f].isdigit():
            out.append(f"{f} {r[f]!r} is not a whole number")
        elif r.get(f) and not r.get("prior_evidence_quote"):
            out.append(f"{f} set without prior_evidence_quote")
    if (r.get("release_status") or "none stated") != "none stated" and not r.get("release_evidence_quote"):
        out.append("release_status set without release_evidence_quote")
    return out


def incident_problems(r: dict) -> list[str]:
    out = coded_problems(r)
    ok, _ = qualifies_smi(r)
    if (r.get("qualifies_smi") == "yes") != ok:
        out.append(f"qualifies_smi {r.get('qualifies_smi')!r} disagrees with the rule")
    if (r.get("qualifies_strict") == "yes") != strict(r):
        out.append(f"qualifies_strict {r.get('qualifies_strict')!r} disagrees with the counts")
    if (r.get("repeat_offender_rules") == "yes") != meets_repeat_offender_rules(r):
        out.append("repeat_offender_rules disagrees with the rule")
    if r.get("offender_category") and r.get("repeat_offender_rules") != "yes":
        out.append("offender_category set on an incident outside the repeat-offender rules")
    return out


def article_problems(r: dict) -> list[str]:
    out = coded_problems(r) if r.get("coded_by") == "claude" else []
    if r.get("review_status") not in REVIEW_STATUSES:
        out.append(f"review_status {r.get('review_status')!r} outside the codebook")
    if r.get("contribution") and r["contribution"] not in CONTRIBUTIONS:
        out.append(f"contribution {r['contribution']!r} outside the codebook")
    return out


def _rows(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def problems() -> list[str]:
    out = []
    for p in DATA_DIR.glob("*.json"):
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            out.append(f"{p.name} is not valid JSON: {e}")
    for p in DATA_DIR.glob("*.csv"):
        lines = p.read_text(encoding="utf-8").splitlines()
        for m in MARKERS:
            if any(line.startswith(m) for line in lines):
                out.append(f"{p.name} contains git conflict marker {m!r}")
    articles, incidents, removed = _rows(ARTICLES_CSV), _rows(INCIDENTS_CSV), _rows(REMOVED_INCIDENTS_CSV)
    for name, rows in (("articles.csv", articles), ("incidents.csv", incidents),
                       ("removed_incidents.csv", removed)):
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            out.append(f"{name} has duplicate ids: {sorted({i for i in ids if ids.count(i) > 1})[:5]}")
    if clash := sorted({r["id"] for r in incidents} & {r["id"] for r in removed})[:5]:
        out.append(f"ids in both incidents.csv and removed_incidents.csv: {clash}")
    known = {r["id"] for r in incidents}
    for r in articles:
        out += [f"{r['id']}: {p}" for p in article_problems(r)]
        if r.get("incident_id") and r["incident_id"] not in known:
            out.append(f"{r['id']}: incident_id {r['incident_id']} is not in incidents.csv")
    for r in incidents:
        out += [f"{r['id']}: {p}" for p in incident_problems(r)]
    return out


def main() -> None:
    found = problems()
    if found:
        for p in found[:20]:
            print(f"  {p}")
        sys.exit(f"SMI DATA VALIDATION FAILED: {len(found)} problem(s)")
    print("SMI data validation OK")


if __name__ == "__main__":
    main()
