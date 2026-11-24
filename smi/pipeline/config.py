"""Shared configuration for the serious-mental-illness (SMI) incident tracker.

The tracker sits beside the repeat-offender database in ../database/ and
follows its pipeline: discovery, headline triage, full-text classification
with verbatim evidence quotes, deduplication, second-source search, and a
person key built the same way as that database's offender_key, so a person
in both can be linked. Its incidents that meet the inclusion rule are
published on the site as the "Serious mental illness" category, by
../database/pipeline/build_exports.py.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # the smi/ folder
DATA_DIR = REPO_ROOT / "data"
REFERENCE_DIR = REPO_ROOT / "reference"

# Three tables. articles.csv has one row per article; Claude codes each one,
# and a reviewer can reject one at any time (review is a spot-check, not a
# gate). incidents.csv is built from the coded articles: one row per
# incident, with the inclusion rule applied (qualifies_smi). subjects.csv has
# one row per person, like the repeat-offender database's offenders.csv.
ARTICLES_CSV = DATA_DIR / "articles.csv"
INCIDENTS_CSV = DATA_DIR / "incidents.csv"
SUBJECTS_CSV = DATA_DIR / "subjects.csv"
# Incidents that lost all their articles (rejected in review, or excluded on
# a re-code). Their ids are never reused: a published id must not come back
# as a different person.
REMOVED_INCIDENTS_CSV = DATA_DIR / "removed_incidents.csv"
# The date windows already harvested, so the next run can continue backwards.
HARVEST_WINDOWS_JSON = DATA_DIR / "harvest_windows.json"
SEEN_URLS_JSON = DATA_DIR / "seen_urls.json"
QUERY_STATS_JSON = DATA_DIR / "query_stats.json"
# Repeat-offender story ids whose articles have been read for mental-health
# language (crosslist.py), so each is read once.
CROSSLIST_CHECKED_JSON = DATA_DIR / "crosslist_checked.json"
DECISIONS_DIR = DATA_DIR / "decisions"   # per-run audit log (gitignored)
ARTICLE_CACHE_DIR = DATA_DIR / "articles"  # fetched article text (gitignored)
REFERENCE_CSV = REFERENCE_DIR / "reference_incidents.csv"

# The repeat-offender database. Its stories are read (never written) to link
# people in both and to cross-list its people who show mental illness.
REPEAT_OFFENDER_DIR = REPO_ROOT.parent / "database"
REPEAT_OFFENDER_STORIES = [
    REPEAT_OFFENDER_DIR / "data" / "stories.csv",
    REPEAT_OFFENDER_DIR / "data" / "backfill" / "stories.csv",
]


def repeat_offender_module(name: str):
    """A module from ../database/pipeline, loaded once under its own name:
    both folders have a config.py (and a store.py, and more), and the rules
    the two harvesters share must be read from one place, not copied. Its
    config gives the outcome wording, strict threshold, trusted outlets and
    vocabularies; its shared.py the person key and the publication rule."""
    key = f"repeat_offender_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, REPEAT_OFFENDER_DIR / "pipeline" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


_RO = repeat_offender_module("config")
STORED_OUTCOME_RE = _RO.STORED_OUTCOME_RE        # arrested, charged, convicted...
STRICT_MIN_ARRESTS = _RO.STRICT_MIN_ARRESTS
STRICT_MIN_CONVICTIONS = _RO.STRICT_MIN_CONVICTIONS
STRICT_MIN_FELONY_CONVICTIONS = _RO.STRICT_MIN_FELONY_CONVICTIONS
NEW_OFFENSE_TYPES = _RO.NEW_OFFENSE_TYPES
OFFENSE_SEVERITIES = _RO.OFFENSE_SEVERITIES
RELEASE_STATUSES = _RO.RELEASE_STATUSES
TRUSTED_OUTLETS = _RO.TRUSTED_OUTLETS
OFFENDER_CATEGORIES = _RO.OFFENDER_CATEGORIES

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
    """GDELT OR group. GDELT rejects an OR that is not inside parentheses, and
    parentheses around anything but an OR ("Parentheses may only be used
    around OR'd statements"), so a single term is left bare."""
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


_DIAGNOSIS_TERMS = ['schizophrenia', 'schizophrenic', '"bipolar disorder"',
                    '"psychotic episode"', 'delusional']
_OTHER_CLINICAL = [t for t in _CLINICAL if t not in _DIAGNOSIS_TERMS]

# A dozen or so GDELT calls in place of the 52 single queries. Grouping
# slightly broadens three of them: "turned away" no longer needs "emergency
# room", "pleaded with" becomes "pleaded with doctors", and "hours after
# being released" needs "mental health" or "psychiatric" rather than
# "hospital". The grid queries at the end cover all 110 clinical x incident
# combinations. GDELT rejects a query of about 300 characters as "too long"
# (203 characters was tested and accepted), so incident terms are packed
# into groups that keep every query under GDELT_MAX_QUERY_CHARS.
GDELT_MAX_QUERY_CHARS = 205


def _pack(clinical: list[str], terms: list[str]) -> list[str]:
    """clinical x terms as the fewest grouped queries under the length limit."""
    queries, group = [], []
    for t in terms:
        if group and len(_any(clinical) + " " + _any(group + [t])) > GDELT_MAX_QUERY_CHARS:
            queries.append(_any(clinical) + " " + _any(group))
            group = []
        group.append(t)
    return queries + [_any(clinical) + " " + _any(group)]
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
    *_pack(_DIAGNOSIS_TERMS, _INCIDENT),
    *_pack(_OTHER_CLINICAL, _INCIDENT),
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

# In rough order of seriousness: the phrase book picks the first that matches,
# so a police killing is checked before the homicide wording it also uses.
INCIDENT_FORMS = [
    "fatal police encounter", "homicide", "sexual assault", "shooting", "stabbing", "arson",
    "vehicular", "police force non-fatal", "assault", "push or shove", "threat or brandishing",
    "suicide or self-harm", "death by overdose", "death by exposure", "found dead",
    "missing person", "property or trespass", "other",
]

# System-contact signals. Each is true only when the article says so, with a
# verbatim quote. prior_hospital_discharge covers any prior psychiatric
# hospitalization: someone hospitalized before the incident was discharged.
# edp_call is a police response to an "emotionally disturbed person" call;
# crisis_call is any other crisis call, welfare check or standoff.
# mh_history is a stated history of mental illness before the incident;
# psych_medication is any mention of psychiatric medication (medication_lapse
# is the narrower "had stopped taking it").
SIGNAL_FIELDS = [
    "prior_hospital_discharge", "prior_custody_release", "police_brought_for_eval",
    "prior_police_contact", "prior_arrests", "competency", "aot", "edp_call", "crisis_call",
    "family_sought_help", "medication_lapse", "psych_medication", "treatment_refusal",
    "homeless", "mh_history", "diagnosis_named",
]

# The inclusion rule's "earlier system contact": any one of these, quoted.
# Not on their own: competency (usually the current case), crisis_call (usually
# the call that started this incident) and homeless. A stated prior record
# (counts or named priors) or release status from an earlier case also counts.
QUALIFYING_SIGNALS = [
    "prior_hospital_discharge", "prior_custody_release", "police_brought_for_eval",
    "prior_police_contact", "prior_arrests", "aot", "edp_call", "family_sought_help",
    "medication_lapse", "psych_medication", "treatment_refusal", "mh_history",
    "diagnosis_named",
]

# The repeat-offender database's prior-record fields, with its definitions and
# count checks, so a card can show "12 prior arrests" where the article says so.
PRIOR_RECORD_FIELDS = [
    "prior_count_arrests", "prior_count_convictions", "prior_count_felony_convictions",
    "prior_offenses", "prior_evidence_quote",
    "release_status", "release_evidence_quote", "releasing_jurisdiction",
]
PRIOR_COUNT_FIELDS = PRIOR_RECORD_FIELDS[:3]

# How each article in an incident was used (incidents.py). The first report
# is primary; the first report from a second outlet is always kept as the
# check; any later one is kept only if it adds something the incident lacks.
CONTRIBUTIONS = ["primary", "corroboration", "context", "redundant"]

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

# The coded fields an article and an incident share.
_CODED = [
    "incident_date", "city", "state",
    "subject_name", "subject_key", "age", "sex",
    "subject_role", "other_party", "incident_form", "fatality", "violence",
    "killed_by_police",
    # The offense the subject was arrested for or accused of, in the
    # repeat-offender vocabulary; blank when none (subject harmed, crisis only).
    "new_offense_type", "new_offense_severity",
    *SIGNAL_FIELDS,
    "diagnoses", "days_since_release",
    "mh_evidence_quote", "signal_evidence",
    *PRIOR_RECORD_FIELDS,
    "failure_point", "responsible_system", "evidence_tier",
    "outcome", "summary",
]

ARTICLE_COLUMNS = [
    "id", "date_added", "published", "headline", *_CODED,
    "source_name", "source_url", "additional_sources",
    "confidence", "trigger_phrases", "repeat_offender_ids",
    "coded_by", "review_note",
    # Review, a spot-check: rejected keeps the article out of incidents.
    "review_status", "reviewed_date",
    # Set when Claude finds an article does not qualify (outside the US,
    # under 18, not a specific incident): it stays out of incidents.
    "excluded_reason",
    "incident_id",     # the incident this article was grouped into
    "contribution",    # CONTRIBUTIONS: how the incident used it
    "added_info",      # what it added that the incident lacked
]

INCIDENT_COLUMNS = [
    "id", *_CODED,
    "source_name", "source_url", "additional_sources",
    "article_ids", "article_count",
    "redundant_article_ids",   # same incident, nothing new: not merged or cited
    "confidence", "repeat_offender_ids",
    # The inclusion rule (rules.py), and why.
    "qualifies_smi", "qualify_note",
    # Whether the stated counts meet the repeat-offender strict threshold.
    "qualifies_strict",
    "corroboration_checked",   # date the second-source search last ran
    "site_excluded",   # set by hand: why the incident stays in this file but off the site
]

SUBJECT_COLUMNS = [
    "subject_key", "subject_name", "state", "incident_ids", "incident_count",
    "qualifying_incident_count", "first_incident_date", "last_incident_date",
    "latest_prior_arrests", "latest_prior_convictions", "latest_prior_felony_convictions",
    "signals", "diagnoses", "repeat_offender_ids",
]

REVIEW_STATUSES = ["pending", "flagged", "accepted", "rejected"]

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "AS", "MP",
}
