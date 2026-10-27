"""Build the site into _site/: the pages and assets, plus a generated
assets/js/stories-data.js made from database/exports/stories.json.

The page templates read display fields (nameAge, dateLabel, record.count,
...) so main.js stays a plain template filler. Every value comes from the
database row; nothing is estimated. Booking photos stay null until usage
rights are confirmed (see the content rules in CLAUDE.md), even where the
database has a mugshot_url.

    python3 site/build.py      # then: python3 -m http.server -d _site 8000

Standard library only, so the Pages workflow needs no installs.
"""

import json
import shutil
from datetime import date
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SITE_DIR.parent
OUT_DIR = REPO_ROOT / "_site"
STORIES_JSON = REPO_ROOT / "database" / "exports" / "stories.json"

PAGES = ["index.html", "stories.html", "facts.html", "solve.html"]

# Filter chips, keyed by the exports' `categories` vocabulary: the database's
# offender_category (set by database/pipeline/categorize.py from the prior
# record; "pending" for rows the sweep hasn't reached yet), plus "smi" for
# incidents from the serious-mental-illness tracker (smi/). A record can
# carry several: a card shows under each.
CATEGORIES = {
    "violent": "Frequent violent offender",
    "petty": "Very frequent petty offender",
    "smi": "Serious mental illness",
    "unclassified": "Record not detailed",
    "pending": "Not yet categorized",
}

# The SMI tracker's earlier-system-contact signals, in the order its cards
# lead with them when the article states no prior count, and their wording.
SMI_SIGNAL_LABELS = {
    "prior_hospital_discharge": "Prior psychiatric hospitalization",
    "prior_custody_release": "Earlier release from custody",
    "police_brought_for_eval": "Earlier taken for a psychiatric evaluation",
    "aot": "Court-ordered treatment",
    "family_sought_help": "Family sought help",
    "edp_call": "Police called for an emotionally disturbed person",
    "prior_police_contact": "Known to police",
    "prior_arrests": "Prior arrests",
    "treatment_refusal": "Refused treatment",
    "medication_lapse": "Stopped taking medication",
    "psych_medication": "Psychiatric medication",
    "diagnosis_named": "Diagnosed",
    "mh_history": "History of mental illness",
}

# release_status vocabulary -> wording on a card. "none stated" shows nothing.
STATUS_LABELS = {
    "parole": "On parole",
    "probation": "On probation",
    "bail/bond": "Out on bail or bond",
    "supervised release": "On supervised release",
    "pretrial release": "On pretrial release",
    "early release": "Released early",
    "charges dropped": "Prior charges dropped",
}

MONTHS = ["Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.",
          "Oct.", "Nov.", "Dec."]


def date_label(iso: str) -> str:
    """YYYY-MM-DD, YYYY-MM or YYYY, as precise as the source was."""
    parts = (iso or "").split("-")
    if len(parts) == 3:
        return f"{MONTHS[int(parts[1]) - 1]} {int(parts[2])}, {parts[0]}"
    if len(parts) == 2:
        return f"{MONTHS[int(parts[1]) - 1]} {parts[0]}"
    return parts[0]


def plural(n: int, word: str) -> str:
    return f"{word}s" if n != 1 else word


def headline(row: dict) -> str:
    # SMI incidents with no offense (no one was accused) lead with what happened.
    offense = row["new_offense_type"] or row.get("incident_form") or "other"
    offense = "Other offense" if offense == "other" else offense[0].upper() + offense[1:]
    outcome = (row["outcome"] or "").strip()
    # Short outcomes ("arrested", "charged") read as a label; long ones are
    # already in the summary.
    if outcome and len(outcome) <= 24:
        return f"{offense} · {outcome[0].upper()}{outcome[1:]}"
    return offense


def counts(row: dict) -> list[dict]:
    """Stated prior counts, in the order a card leads with them: arrests,
    convictions, felony convictions (the strict threshold is any one)."""
    out = []
    for field, label in (("prior_count_arrests", "prior arrest"),
                         ("prior_count_convictions", "prior conviction"),
                         ("prior_count_felony_convictions", "prior felony conviction")):
        n = row[field]
        if n is not None:
            out.append({"count": str(n), "label": plural(n, label)})
    return out


def system_contacts(row: dict) -> list[dict]:
    """For SMI incidents: the earlier system contacts the articles quote, in
    the order a card leads with them. Days since a release go first, when
    the article printed them."""
    signals = row.get("smi_signals") or []
    out = []
    # Facts a card already shows another way.
    shown = set()
    if counts(row):
        shown.add("prior_arrests")
    if STATUS_LABELS.get(row["release_status"]):
        shown.add("prior_custody_release")
    days = row.get("days_since_release")
    released = [s for s in ("prior_hospital_discharge", "prior_custody_release") if s in signals]
    if days is not None and len(released) == 1:
        place = "a psychiatric hospital" if released[0] == "prior_hospital_discharge" else "custody"
        out.append({"count": "<1" if days == 0 else str(days),
                    "label": f"{'day' if days <= 1 else 'days'} after release from {place}"})
        shown.add(released[0])
    for s, label in SMI_SIGNAL_LABELS.items():
        if s in signals and s not in shown:
            if s == "diagnosis_named" and row.get("diagnoses"):
                label = f"Diagnosed with {row['diagnoses'].split(';')[0].strip()}"
            out.append({"count": "", "label": label})
    return out


def to_story(row: dict) -> dict:
    categories = row.get("categories") or [row.get("offender_category") or "pending"]
    stated = counts(row)
    if "smi" in categories:
        stated += system_contacts(row)
    stated += [{"count": "—", "label": "no other count stated"}] * 2
    status = STATUS_LABELS.get(row["release_status"], "")
    return {
        "id": row["id"],
        "name": row["offender_name"],
        "nameAge": row["offender_name"] + (f", {row['age']}" if row["age"] is not None else ""),
        "city": row["city"],
        "state": row["state"],
        "date": row["incident_date"],
        "dateLabel": date_label(row["incident_date"]),
        "category": categories[0],
        "categories": categories,
        "headline": headline(row),
        "summary": row["summary"],
        "priorArrests": row["prior_count_arrests"],
        "record": stated[0],
        "recordAlt": stated[1],
        "status": status,
        "statusLabel": status or "Not stated",
        "source": {"outlet": row["source_name"], "url": row["source_url"]},
        "photo": None,
    }


def build() -> None:
    rows = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    stories = [to_story(r) for r in rows]
    used = {c for s in stories for c in s["categories"]}

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    for page in PAGES:
        shutil.copy(SITE_DIR / page, OUT_DIR / page)
    shutil.copytree(SITE_DIR / "assets", OUT_DIR / "assets")

    data = OUT_DIR / "assets" / "js" / "stories-data.js"
    data.write_text(
        "/* Generated by site/build.py from database/exports/stories.json on "
        f"{date.today().isoformat()}. Do not edit. */\n"
        f"window.CATEGORIES = {json.dumps({k: v for k, v in CATEGORIES.items() if k in used}, indent=1)};\n"
        f"window.STORIES = {json.dumps(stories, ensure_ascii=False)};\n",
        encoding="utf-8")
    (OUT_DIR / ".nojekyll").write_text("")
    print(f"Built {OUT_DIR.name}/: {len(PAGES)} pages, {len(stories)} stories")


if __name__ == "__main__":
    build()
