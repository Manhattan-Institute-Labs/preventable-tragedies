"""Shared configuration for the serious-mental-illness (SMI) incident tracker.

The tracker sits beside the repeat-offender database in ../database/ and
follows its pipeline: discovery, headline triage, full-text classification
with verbatim evidence quotes, deduplication, and a person key built the same
way as that database's offender_key, so a person in both can be linked.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # the smi/ folder
DATA_DIR = REPO_ROOT / "data"
REFERENCE_DIR = REPO_ROOT / "reference"

# Every coded article waits in the review queue; only rows a reviewer accepts
# reach incidents.csv. Rejected rows stay in the queue for tuning.
REVIEW_QUEUE_CSV = DATA_DIR / "review_queue.csv"
INCIDENTS_CSV = DATA_DIR / "incidents.csv"
SEEN_URLS_JSON = DATA_DIR / "seen_urls.json"
QUERY_STATS_JSON = DATA_DIR / "query_stats.json"
DECISIONS_DIR = DATA_DIR / "decisions"   # per-run audit log (gitignored)
ARTICLE_CACHE_DIR = DATA_DIR / "articles"  # fetched article text (gitignored)
REFERENCE_CSV = REFERENCE_DIR / "reference_incidents.csv"

# The repeat-offender database, read (never written) to link people in both.
REPEAT_OFFENDER_STORIES = [
    REPO_ROOT.parent / "database" / "data" / "stories.csv",
    REPO_ROOT.parent / "database" / "data" / "backfill" / "stories.csv",
]

# Same models as the repeat-offender pipeline: bulk extraction, not judgment.
TRIAGE_MODEL = "claude-haiku-4-5"
CLASSIFY_MODEL = "claude-haiku-4-5"
DEDUPE_MODEL = "claude-haiku-4-5"

TRIAGE_BATCH_SIZE = 25

# No one under 18 is stored, whatever the article gives.
MIN_AGE = 18

# ---------------------------------------------------------------------------
# Discovery queries.
#
# Google News RSS is cheap (about a second a query, up to 100 results each),
# so it gets the narrow list. GDELT throttles hard (see ../database/config.py
# and this tracker's README), so it gets a few OR-grouped queries covering the
# same ground, and fetch.py splits the window when one hits the 250-record cap.
# ---------------------------------------------------------------------------

# Block A: clinical terms crossed with incident terms. The competency and
# evaluation phrases are incident terms, not clinical ones, so they no longer
# take up most of the grid.
_CLINICAL = [
    '"emotionally disturbed"',
    '"history of mental illness"',
    'schizophrenia',
    'schizophrenic',
    '"bipolar disorder"',
    '"psychotic episode"',
    'delusional',
    '"off his medication"',
    '"off her medication"',
    '"mental health issues"',
]
_INCIDENT = [
    'stabbing',
    'stabbed',
    '"shot by police"',
    'arraigned',
    'standoff',
    '"charged with murder"',
    'attack',
    '"wellness check"',
    '"mental health evaluation"',
    '"competency evaluation"',
    '"unfit to stand trial"',
]
GRID_PER_CLINICAL = 3


def balanced_grid(clinical: list[str], incident: list[str], per_term: int) -> list[str]:
    """Each clinical term paired with `per_term` incident terms, rotating
    through the incident list so every incident term is used about equally.
    Taking the first N of the full cross product instead covers only the
    first two or three clinical terms."""
    n = len(incident)
    return [f"{c} {incident[(per_term * i + j) % n]}"
            for i, c in enumerate(clinical) for j in range(per_term)]


GRID_QUERIES = balanced_grid(_CLINICAL, _INCIDENT, GRID_PER_CLINICAL)

# Block B: discharge and release language on its own. No crime word at all.
DISCHARGE_QUERIES = [
    '"released from a psychiatric hospital"',
    '"released from the hospital" "mental health"',
    '"discharged from a psychiatric"',
    '"treated and released" psychiatric',
    '"denied a bed" psychiatric',
    '"turned away" "emergency room" "mental health"',
    '"released without follow-up"',
    '"hours after being released" hospital',
]

# Block C: family-plea language on its own.
FAMILY_QUERIES = [
    '"family begged" "mental illness"',
    '"mother begged" hospital "mental"',
    '"tried to get him committed"',
    '"repeatedly called for help" "mental health"',
    '"pleaded with" doctors "mental illness"',
    '"we tried to get him help"',
]

# Block D: crisis-response and civil-commitment language.
CRISIS_QUERIES = [
    '"emotionally disturbed person"',
    '"person experiencing an emotional crisis"',
    '"crisis intervention team" shooting',
    '"involuntary commitment"',
    '"Baker Act" arrested',
    '"Section 12" psychiatric',
    '"assisted outpatient treatment" OR "Kendra\'s Law"',
    '"competency restoration"',
]

GOOGLE_NEWS_QUERIES = DISCHARGE_QUERIES + FAMILY_QUERIES + CRISIS_QUERIES + GRID_QUERIES


def _any(terms: list[str]) -> str:
    """GDELT OR group. GDELT rejects an OR that is not inside parentheses."""
    return "(" + " OR ".join(terms) + ")"


_DIAGNOSIS_TERMS = ['schizophrenia', 'schizophrenic', '"bipolar disorder"',
                    '"psychotic episode"', 'delusional']
_OTHER_CLINICAL = [t for t in _CLINICAL if t not in _DIAGNOSIS_TERMS]

# Eight GDELT calls in place of the 46 single queries. Grouping slightly
# broadens three of them: "turned away" no longer needs "emergency room",
# "pleaded with" becomes "pleaded with doctors", and "hours after being
# released" needs "mental health" or "psychiatric" rather than "hospital".
# The last two cover all 110 clinical x incident combinations.
GDELT_QUERIES = [
    _any(['"released from a psychiatric hospital"', '"discharged from a psychiatric"',
          '"released without follow-up"']),
    _any(['"released from the hospital"', '"treated and released"', '"denied a bed"',
          '"hours after being released"', '"turned away"'])
    + " " + _any(['"mental health"', 'psychiatric']),
    _any(['"family begged"', '"mother begged"', '"repeatedly called for help"',
          '"pleaded with doctors"']) + " " + _any(['"mental illness"', '"mental health"']),
    _any(['"tried to get him committed"', '"we tried to get him help"']),
    _any(['"emotionally disturbed person"', '"person experiencing an emotional crisis"',
          '"involuntary commitment"', '"competency restoration"',
          '"assisted outpatient treatment"', '"Kendra\'s Law"']),
    _any(['"Baker Act"', '"Section 12"', '"crisis intervention team"'])
    + " " + _any(['arrested', 'psychiatric', 'shooting']),
    _any(_DIAGNOSIS_TERMS) + " " + _any(_INCIDENT),
    _any(_OTHER_CLINICAL) + " " + _any(_INCIDENT),
]

# Domains and URL fragments that produce noise rather than incident reporting.
SKIP_DOMAINS = (
    "prnewswire.com", "businesswire.com", "globenewswire.com", "accesswire.com",
    "einpresswire.com", "newswire.com", "streetinsider.com", "finance.yahoo.com",
    "seekingalpha.com", "clinicaltrials.gov", "pubmed.ncbi.nlm.nih.gov", "psychiatry.org",
    "arrests.org", "mugshots.com", "bustednewspaper.com", "jailbase.com",
)
SKIP_URL_FRAGMENTS = ("newswire", "press_release", "press-release", "/sponsored/",
                      "/obituaries/", "/obituary/", "/horoscope", "/mugshots/",
                      "/arrest-log", "/police-blotter", "/blotter/")

# ---------------------------------------------------------------------------
# Codebook. One vocabulary for the phrase book, the classifier and the CSV.
# ---------------------------------------------------------------------------

# The subject is the person with the mental-health indication. When the
# subject both caused and suffered harm, the role follows whoever is alleged
# to have started it. A person in crisis with no crime alleged is
# subject_accused: the subject is the subject whether or not anyone was hurt.
SUBJECT_ROLES = ["subject_accused", "subject_victim"]

# Who the other party was. Direction follows subject_role: the subject's
# victim when subject_accused, whoever harmed the subject when subject_victim.
# "acquaintance" is someone the subject knew who is not family or a partner:
# a neighbor, friend, roommate, coworker, landlord. "multiple" means more than
# one type of party; two strangers are "stranger". Blank when there was no
# other party or the article does not say.
OTHER_PARTIES = [
    "stranger", "acquaintance", "police", "ems", "family_parent", "family_child",
    "family_other", "partner", "corrections", "healthcare_worker", "multiple",
]

# In rough order of seriousness: the phrase book picks the first that matches.
# People killed by police are excluded from the database, so there is no
# "fatal police encounter" form.
INCIDENT_FORMS = [
    "homicide", "sexual assault", "shooting", "stabbing", "arson", "vehicular",
    "police force non-fatal", "assault", "push or shove", "threat or brandishing",
    "suicide or self-harm", "death by overdose", "death by exposure", "found dead",
    "missing person", "property or trespass", "other",
]

# System-contact signals. Each is true only when the article says so, with a
# verbatim quote. prior_hospital_discharge covers any prior psychiatric
# hospitalization: someone hospitalized before the incident was discharged.
SIGNAL_FIELDS = [
    "prior_hospital_discharge", "prior_custody_release", "police_brought_for_eval",
    "prior_police_contact", "prior_arrests", "competency", "aot", "crisis_call",
    "family_sought_help", "medication_lapse", "treatment_refusal", "homeless",
    "diagnosis_named",
]

FAILURE_POINTS = [
    "premature discharge", "no discharge plan", "bed unavailable",
    "commitment denied", "AOT not sought", "AOT lapsed", "competency delay",
    "released untreated from custody", "dropped from caseload",
    "crisis call with no clinical response", "shelter denied",
    "records not shared", "failure not established",
]

RESPONSIBLE_SYSTEMS = ["hospital", "jail or prison", "court", "police",
                       "community provider", "homeless services", "state",
                       "cross-agency", "not established"]

EVIDENCE_TIERS = ["T1", "T2", "T3", "T4"]

CSV_COLUMNS = [
    "id", "date_added", "incident_date", "city", "state",
    "subject_name", "subject_key", "age", "sex",
    "subject_role", "other_party", "incident_form", "fatality", "violence",
    *SIGNAL_FIELDS,
    "diagnoses", "days_since_release",
    "mh_evidence_quote", "signal_evidence",
    "failure_point", "responsible_system", "evidence_tier",
    "outcome", "summary",
    "source_name", "source_url", "additional_sources",
    "confidence", "trigger_phrases", "repeat_offender_ids",
    "coded_by", "review_note",
    # Review. duplicate_of names an accepted incident this report describes;
    # accepting the row merges it into that incident instead of adding one.
    "review_status", "reviewed_date", "duplicate_of",
]

REVIEW_STATUSES = ["pending", "flagged", "accepted", "rejected"]

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "AS", "MP",
}
