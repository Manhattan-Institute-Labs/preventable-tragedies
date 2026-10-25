"""Build incidents.csv from accepted articles.

    python pipeline/run.py incidents

An article joins an incident only when a reviewer accepted it (the right
general type), Claude has coded it, and Claude did not exclude it (outside
the US, under 18, not a specific incident). Several articles about one
incident become one row: its fields are filled from all of them (a value is
never overwritten, a signal any article supports with a quote is true), its
first article is source_url, and the rest are additional_sources and
article_ids. Which article is "best" is left to the criminal-offending
restrictions applied after this.

Grouping uses the rules in dedupe.py: the same person with nearby dates and
the same city or form is the same incident without asking a model; with an
API key, Haiku decides the unclear cases. Each article keeps its incident_id,
so a rebuild keeps earlier groupings and only places new articles.
"""

from pathlib import Path

import anthropic

from config import INCIDENT_COLUMNS
from dedupe import ages_consistent, check_duplicate, merge_into, obvious_same_incident
from link import link_repeat_offenders
from process import DecisionLog
from store import INCIDENT_PREFIX, format_incident_id, next_number


def eligible(a: dict) -> bool:
    return (a.get("review_status") == "accepted" and a.get("coded_by") == "claude"
            and not a.get("excluded_reason"))


def _incident_from(a: dict, incident_id: str) -> dict:
    row = {f: a.get(f, "") for f in INCIDENT_COLUMNS}
    row.update(id=incident_id, article_ids=a["id"], article_count="1")
    return row


def _add_article(incident: dict, a: dict) -> None:
    merge_into(incident, a)
    ids = incident["article_ids"].split()
    if a["id"] not in ids:
        ids.append(a["id"])
    incident["article_ids"] = " ".join(ids)
    incident["article_count"] = str(len(ids))


def build_incidents(articles: list[dict], client: anthropic.Anthropic | None = None,
                    decision_log: Path | None = None) -> tuple[list[dict], dict]:
    """Group eligible articles into incidents. Mutates each article's
    incident_id. Returns (incidents, counts)."""
    log = DecisionLog(decision_log)
    counts = {"articles": 0, "incidents": 0, "grouped": 0, "new": 0}
    for a in articles:
        if not eligible(a):
            a["incident_id"] = ""
    chosen = sorted((a for a in articles if eligible(a)),
                    key=lambda a: (a.get("published") or a.get("incident_date") or "", a["id"]))
    counts["articles"] = len(chosen)

    incidents: dict[str, dict] = {}
    for a in chosen:   # earlier groupings first
        iid = a.get("incident_id")
        if iid:
            if iid in incidents:
                _add_article(incidents[iid], a)
            else:
                incidents[iid] = _incident_from(a, iid)
    n = next_number([a.get("incident_id") for a in articles], INCIDENT_PREFIX)

    for a in chosen:
        if a.get("incident_id"):
            continue
        rows = list(incidents.values())
        match = next((r for r in rows if obvious_same_incident(a, r)), None)
        if match is None and client and rows:
            try:
                dup = check_duplicate(client, a, rows)
            except anthropic.APIError as e:
                print(f"  grouping call failed for {a['id']} ({e}); treated as a new incident")
                dup = None
            if dup and dup.relation == "same_incident":
                match = incidents.get(dup.matching_id)
            elif dup and dup.relation == "same_person_new_incident":
                person = incidents.get(dup.matching_id)
                if person and ages_consistent(a, person) and person.get("subject_key"):
                    a["subject_key"] = person["subject_key"]
        if match is not None:
            a["incident_id"] = match["id"]
            _add_article(match, a)
            counts["grouped"] += 1
            log.write(stage="incident", article=a["id"], incident=match["id"], relation="same_incident")
        else:
            iid = format_incident_id(n)
            n += 1
            a["incident_id"] = iid
            incidents[iid] = _incident_from(a, iid)
            counts["new"] += 1
            log.write(stage="incident", article=a["id"], incident=iid, relation="new")

    rows = list(incidents.values())
    link_repeat_offenders(rows)
    counts["incidents"] = len(rows)
    return rows, counts
