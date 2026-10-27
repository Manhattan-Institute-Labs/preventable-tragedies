"""Build incidents.csv (and subjects.csv) from coded articles.

    python pipeline/run.py incidents

An article joins an incident when Claude has coded it, Claude did not
exclude it (outside the US, under 18, not a specific incident), and no
reviewer rejected it. Review is a spot-check, not a gate.

Grouping uses the rules in dedupe.py: the same person with nearby dates and
the same city or form is the same incident without asking a model; with an
API key, Haiku decides the unclear cases. Each article keeps its incident_id,
so a rebuild keeps earlier groupings and only places new articles.

Which articles an incident keeps, in publication order:
  primary        the first report.
  corroboration  the first report from a second outlet, always kept: the
                 publication rule needs two outlets (or a trusted first one).
  context        a later report that adds something the incident lacks: a
                 system-contact signal, a diagnosis, a prior count, a release
                 status, days since release, a death, or a missing name, age
                 or place (dedupe.new_info). added_info says what.
  redundant      a later report that adds nothing. It keeps its incident_id
                 but is not cited or merged (redundant_article_ids).
The incident's fields come from the kept articles (a value is never
overwritten, a signal any of them supports with a quote is true), and the
inclusion rule in rules.py is applied to the result.

An incident that loses all its articles moves to removed_incidents.csv, and
its id is never reused.
"""

from datetime import date
from pathlib import Path

import anthropic

from config import INCIDENT_COLUMNS, SIGNAL_FIELDS
from dedupe import ages_consistent, check_duplicate, merge_into, new_info, obvious_same_incident
from link import link_repeat_offenders
from process import DecisionLog
from rules import apply_rules
from sources import host, outlets, publishable
from store import INCIDENT_PREFIX, format_incident_id, next_number

# Set by other steps (corroborate.py, ../database categorize.py), so a
# rebuild carries them over.
CARRIED = ("corroboration_checked", "offender_category", "category_reason")


def eligible(a: dict) -> bool:
    return (a.get("coded_by") == "claude" and a.get("review_status") != "rejected"
            and not a.get("excluded_reason"))


def _order(a: dict):
    return (a.get("published") or a.get("incident_date") or "", a["id"])


def _incident_from(a: dict, incident_id: str) -> dict:
    row = {f: a.get(f, "") for f in INCIDENT_COLUMNS}
    row.update(id=incident_id, article_ids=a["id"], article_count="1", redundant_article_ids="",
               additional_sources="", qualifies_smi="", qualify_note="", qualifies_strict="",
               repeat_offender_rules="", corroboration_checked="", offender_category="",
               category_reason="")
    return row


def _add_article(incident: dict, a: dict) -> None:
    merge_into(incident, a)
    ids = incident["article_ids"].split()
    if a["id"] not in ids:
        ids.append(a["id"])
    incident["article_ids"] = " ".join(ids)
    incident["article_count"] = str(len(ids))


def assemble(incident_id: str, members: list[dict], previous: dict | None = None) -> dict:
    """One incident from its articles, applying the contribution rule.
    Mutates each article's contribution and added_info."""
    members = sorted(members, key=_order)
    first = members[0]
    row = _incident_from(first, incident_id)
    first["contribution"], first["added_info"] = "primary", ""
    redundant = []
    for a in members[1:]:
        info = new_info(row, a)
        if not publishable(row) and host(a.get("source_url", "")) not in outlets(row):
            a["contribution"] = "corroboration"
        elif info:
            a["contribution"] = "context"
        else:
            a["contribution"] = "redundant"
        a["added_info"] = "; ".join(info)
        if a["contribution"] == "redundant":
            redundant.append(a["id"])
        else:
            _add_article(row, a)
    row["redundant_article_ids"] = " ".join(redundant)
    for f in CARRIED:
        row[f] = (previous or {}).get(f, "")
    apply_rules(row)
    return row


def build_incidents(articles: list[dict], client: anthropic.Anthropic | None = None,
                    decision_log: Path | None = None, previous: list[dict] | None = None,
                    removed: list[dict] | None = None) -> tuple[list[dict], dict, list[dict]]:
    """Group eligible articles into incidents. Mutates each article's
    incident_id, contribution and added_info. `previous` is the incidents.csv
    being replaced and `removed` the removed_incidents.csv rows (for ids).
    Returns (incidents, counts, newly removed incidents)."""
    log = DecisionLog(decision_log)
    previous = previous or []
    counts = {"articles": 0, "incidents": 0, "grouped": 0, "new": 0, "qualifying": 0,
              "redundant": 0, "removed": 0}
    for a in articles:
        if not eligible(a):
            a["incident_id"] = a["contribution"] = a["added_info"] = ""
    chosen = sorted((a for a in articles if eligible(a)), key=_order)
    counts["articles"] = len(chosen)

    # Grouping. `working` merges every article, to match new ones against.
    working: dict[str, dict] = {}
    members: dict[str, list[dict]] = {}
    for a in chosen:   # earlier groupings first
        iid = a.get("incident_id")
        if iid:
            if iid in working:
                _add_article(working[iid], a)
            else:
                working[iid] = _incident_from(a, iid)
            members.setdefault(iid, []).append(a)
    n = next_number([a.get("incident_id") for a in articles]
                    + [r.get("id") for r in previous] + [r.get("id") for r in removed or []],
                    INCIDENT_PREFIX)

    for a in chosen:
        if a.get("incident_id"):
            continue
        rows = list(working.values())
        match = next((r for r in rows if obvious_same_incident(a, r)), None)
        if match is None and client and rows:
            try:
                dup = check_duplicate(client, a, rows)
            except anthropic.APIError as e:
                print(f"  grouping call failed for {a['id']} ({e}); treated as a new incident")
                dup = None
            if dup and dup.relation == "same_incident":
                match = working.get(dup.matching_id)
            elif dup and dup.relation == "same_person_new_incident":
                person = working.get(dup.matching_id)
                if person and ages_consistent(a, person) and person.get("subject_key"):
                    a["subject_key"] = person["subject_key"]
        if match is not None:
            a["incident_id"] = match["id"]
            _add_article(match, a)
            members[match["id"]].append(a)
            counts["grouped"] += 1
            log.write(stage="incident", article=a["id"], incident=match["id"], relation="same_incident")
        else:
            iid = format_incident_id(n)
            n += 1
            a["incident_id"] = iid
            working[iid] = _incident_from(a, iid)
            members[iid] = [a]
            counts["new"] += 1
            log.write(stage="incident", article=a["id"], incident=iid, relation="new")

    before = {r["id"]: r for r in previous}
    rows = [assemble(iid, ms, before.get(iid)) for iid, ms in members.items()]
    rows.sort(key=lambda r: r["id"])
    link_repeat_offenders(rows)
    for a in chosen:
        if a["contribution"] == "redundant":
            log.write(stage="incident", article=a["id"], incident=a["incident_id"],
                      relation="redundant")

    today = date.today().isoformat()
    kept = {r["id"] for r in rows}
    gone = [dict(r, removed_date=today, removed_reason="no eligible articles left")
            for r in previous if r["id"] not in kept]
    counts.update(incidents=len(rows), qualifying=sum(r["qualifies_smi"] == "yes" for r in rows),
                  redundant=sum(a["contribution"] == "redundant" for a in chosen),
                  removed=len(gone))
    return rows, counts, gone


def build_subjects(incidents: list[dict]) -> list[dict]:
    """One row per person (subject_key), like the repeat-offender database's
    offenders.csv. Counts are as stated in the most recent incident."""
    groups: dict[str, list[dict]] = {}
    for r in incidents:
        if r.get("subject_key"):
            groups.setdefault(r["subject_key"], []).append(r)
    out = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: r.get("incident_date") or "")
        dates = [r["incident_date"] for r in rows if r.get("incident_date")]
        diagnoses = []
        for r in rows:
            for d in (r.get("diagnoses") or "").split(";"):
                if d.strip() and d.strip() not in diagnoses:
                    diagnoses.append(d.strip())
        out.append({
            "subject_key": key,
            "subject_name": max((r["subject_name"] for r in rows), key=len),
            "state": rows[0].get("state", ""),
            "incident_ids": " ".join(r["id"] for r in rows),
            "incident_count": str(len(rows)),
            "qualifying_incident_count": str(sum(r.get("qualifies_smi") == "yes" for r in rows)),
            "first_incident_date": dates[0] if dates else "",
            "last_incident_date": dates[-1] if dates else "",
            "latest_prior_arrests": rows[-1].get("prior_count_arrests", ""),
            "latest_prior_convictions": rows[-1].get("prior_count_convictions", ""),
            "latest_prior_felony_convictions": rows[-1].get("prior_count_felony_convictions", ""),
            "signals": " ".join(f for f in SIGNAL_FIELDS if any(r.get(f) == "true" for r in rows)),
            "diagnoses": "; ".join(diagnoses),
            "repeat_offender_ids": " ".join(dict.fromkeys(
                i for r in rows for i in (r.get("repeat_offender_ids") or "").split())),
        })
    out.sort(key=lambda o: (-int(o["incident_count"]), o["subject_key"]))
    return out
