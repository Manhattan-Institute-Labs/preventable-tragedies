"""Offline tests for the SMI tracker: no network, no API key, no model calls.
Article text here is invented for the tests."""

import csv
import json
from datetime import datetime

import pytest

import config
import process
import review
import smi_coding
from classify import IncidentClassification, check_evidence, days_in_text, parse_classification
from dedupe import DedupeResult, merge_into, obvious_same_incident
from link import link_repeat_offenders
from prefilter import prefilter
from store import make_candidate_row, make_row, subject_key

ARTICLE = (
    "CAMBRIDGE, Mass. - A Boston man accused of opening fire on passing cars had been "
    "released from a psychiatric hospital three days before the shooting, according to a "
    "state police report. Tyler Example, 46, was charged with two counts of attempted murder. "
    "He had been hospitalized several times and has a criminal history spanning two decades. "
    "Less than an hour before the shooting, he told his parole officer that people would pay. "
    "He shot two passing motorists before a trooper shot him. His family said he had "
    "mental health issues and had stopped taking his medication."
)


# --- config -----------------------------------------------------------------

def test_grid_is_balanced_across_clinical_terms():
    assert len(config.GRID_QUERIES) == 30
    for term in config._CLINICAL:
        assert sum(q.startswith(term + " ") for q in config.GRID_QUERIES) == 3, term
    assert config.GRID_QUERIES[0].startswith('"emotionally disturbed"')
    # Competency and evaluation terms: 6 of 30, down from 16 of 24.
    competency = [q for q in config.GRID_QUERIES
                  if '"competency evaluation"' in q or '"unfit to stand trial"' in q
                  or '"mental health evaluation"' in q]
    assert len(competency) == 6


def test_query_counts_and_gdelt_syntax():
    assert len(config.GOOGLE_NEWS_QUERIES) == 52
    assert len(config.GDELT_QUERIES) == 8
    for q in config.GDELT_QUERIES:
        assert q.count("(") == q.count(")"), q
        assert q.count('"') % 2 == 0, q
        if " OR " in q:
            assert q.startswith("("), q   # GDELT needs ORs inside parentheses


def test_codebook_shape():
    assert config.SUBJECT_ROLES == ["subject_accused", "subject_victim"]
    assert "prior_hospitalization" not in config.SIGNAL_FIELDS
    assert "victim_smi_status" not in config.CSV_COLUMNS
    assert "fatal police encounter" not in config.INCIDENT_FORMS
    assert "acquaintance" in config.OTHER_PARTIES
    assert {"review_status", "reviewed_date", "duplicate_of"} <= set(config.CSV_COLUMNS)
    assert len(set(config.CSV_COLUMNS)) == len(config.CSV_COLUMNS)


# --- phrase book ------------------------------------------------------------

def test_phrasebook_full_text():
    r = smi_coding.code(ARTICLE)
    assert r["has_mh"] and r["has_event"]
    assert set(r["signals"]) >= {"prior_hospital_discharge", "prior_custody_release",
                                 "prior_arrests", "medication_lapse"}
    assert "released from a psychiatric hospital" in r["signals"]["prior_hospital_discharge"]
    assert r["days_since_release"] == 3            # "three days before", spelled out
    assert r["fatality"] is False                  # attempted murder is not a death
    assert r["incident_form"] == "shooting"
    assert r["other_party"] == "stranger"          # "shot two passing motorists"
    assert r["subject_role"] == "subject_accused"
    assert r["diagnoses"] == []                    # "mental health issues" is not a diagnosis
    assert "diagnosis_named" not in r["signals"]


def test_diagnosis_needs_a_named_disorder():
    assert smi_coding.code("He was diagnosed with schizophrenia.")["diagnoses"] == ["schizophrenia"]
    for text in ("He was in a state of psychosis.", "She had delusions and heard voices.",
                 "He was mentally ill.", "He had mental health issues."):
        assert "diagnosis_named" not in smi_coding.code(text)["signals"], text


def test_prior_hospitalization_now_sets_discharge():
    r = smi_coding.code("He had been in and out of psychiatric hospitals for years.")
    assert "prior_hospital_discharge" in r["signals"]


def test_involuntary_holds_qualify_but_set_no_signal():
    r = smi_coding.code("Deputies took him into custody under the Baker Act after the standoff.")
    assert r["has_mh"]
    assert "prior_hospital_discharge" not in r["signals"]


def test_fatality_rules():
    assert smi_coding.code("The man tried to kill his neighbor.")["fatality"] is False
    assert smi_coding.code("He faces an attempted first-degree murder charge.")["fatality"] is False
    assert smi_coding.code("He made death threats against staff.")["fatality"] is False
    assert smi_coding.code("His mother was killed in the attack.")["fatality"] is True
    assert smi_coding.code("The subject died at the scene.")["fatality"] is True


@pytest.mark.parametrize("text,party", [
    ("Police said he stabbed his mother in the kitchen.", "family_parent"),
    ("A firefighter-paramedic was stabbed during the transport.", "ems"),
    ("He attacked a nurse in the emergency room.", "healthcare_worker"),
    ("He punched his girlfriend and stabbed his brother.", "multiple"),
    ("He lunged at officers with a knife and stabbed a deputy.", "police"),
    ("He attacked his neighbor with a hammer.", "acquaintance"),
    ("Prosecutors say he strangled his roommate.", "acquaintance"),
])
def test_other_party(text, party):
    assert smi_coding.code(text)["other_party"] == party


def test_shot_by_police_is_not_the_subjects_victim():
    r = smi_coding.code("He was shot by police and survived. He was charged with assault.")
    assert r["other_party"] == ""
    assert r["killed_by_police"] is False


def test_subject_victim_and_killed_by_police():
    r = smi_coding.code("A homeless man with schizophrenia froze to death outside the shelter.")
    assert r["subject_role"] == "subject_victim"
    assert smi_coding.code("The man was fatally shot by police after the standoff.")["killed_by_police"]


def test_days_since_release_needs_a_release_sentence():
    assert smi_coding.code("Two days earlier, he bought a knife.")["days_since_release"] is None
    assert smi_coding.code("He was discharged a week before the attack.")["days_since_release"] == 7
    assert smi_coding.code("He was released within hours of arriving.")["days_since_release"] == 0


# --- headline check ---------------------------------------------------------

def test_prefilter():
    start = datetime(2026, 10, 1)
    ok = {"title": "Mother shares story of son's arrest - WXYZ", "url": "https://wxyz.com/a",
          "source": "WXYZ", "published": "2026-10-10"}
    assert prefilter(ok, start) is None
    assert prefilter({**ok, "title": "Opinion: Fix the mental health system"}, start)
    assert prefilter({**ok, "title": "Lawmakers weigh involuntary commitment bill"}, start)
    # Genres seen in the first live run.
    for title in ("Calls to service week of 10/20/2026-10/22/2026 - Panola Watchman",
                  "Pa Senators advance legislation to create online registry - WGAL",
                  "Systematic review and meta-analysis of CBT for insomnia - Nature"):
        assert prefilter({**ok, "title": title}, start), title
    assert prefilter({**ok, "published": "2019-01-01"}, start) == "published before the search window"
    assert prefilter({**ok, "source": "Manchester Evening News"}, start) == "foreign outlet"
    assert prefilter({**ok, "url": "https://www.example.co.uk/x"}, start) == "foreign outlet"
    # A UK national outlet often covers US incidents: left for the classifier.
    assert prefilter({**ok, "source": "Daily Mail"}, start) is None


# --- classification parsing and verification -------------------------------

def _cls(**kw) -> IncidentClassification:
    base = dict(qualifies=True, reason="ok", state="MA", city="Cambridge",
                subject_name="Tyler Example", age=46, subject_role="subject_accused",
                incident_date="2026-05-11", incident_form="shooting",
                mh_evidence_quote="His family said he had mental health issues and had stopped taking his medication.",
                signal_quotes={"prior_hospital_discharge":
                               "A Boston man accused of opening fire on passing cars had been released "
                               "from a psychiatric hospital three days before the shooting, according to "
                               "a state police report."},
                days_since_release=3, summary="He is accused of shooting two motorists.",
                outcome="charged")
    base.update(kw)
    return IncidentClassification(**base)


def test_parse_coerces_unknown_values():
    reply = "Here you go: " + json.dumps({
        "qualifies": True, "reason": "x", "subject_role": "victim", "other_party": "neighbor",
        "incident_form": "brawl", "evidence_tier": "T9", "sex": "male", "state": " ma ",
        "signal_quotes": {"competency": "He was found unfit to stand trial last year.",
                          "made_up_field": "x", "aot": ""}})
    cls = parse_classification(reply)
    assert cls.subject_role == "subject_victim"
    assert cls.other_party is None and cls.incident_form == "other"
    assert cls.evidence_tier == "T4" and cls.sex == "U" and cls.state == "MA"
    assert list(cls.signal_quotes) == ["competency"]


def test_check_evidence_accepts_and_drops_unverified_signals():
    cls = _cls(signal_quotes={
        "prior_hospital_discharge": _cls().signal_quotes["prior_hospital_discharge"],
        "homeless": "He had been living in a tent encampment for months before this."})
    problem, dropped = check_evidence(cls, ARTICLE)
    assert problem is None
    assert dropped == ["homeless"] and "homeless" not in cls.signal_quotes
    assert cls.days_since_release == 3


@pytest.mark.parametrize("kw,reason", [
    ({"killed_by_police": True}, "killed by police"),
    ({"age": 16}, "under 18"),
    ({"state": "ON"}, "not a US state"),
    ({"mh_evidence_quote": "He was deeply troubled, according to a neighbor who knew him."},
     "mh_evidence_quote not found"),
    ({"subject_role": None}, "no subject_role"),
])
def test_check_evidence_rejections(kw, reason):
    problem, _ = check_evidence(_cls(**kw), ARTICLE)
    assert problem and reason in problem


def test_days_in_text():
    assert days_in_text(3, "three days before")
    assert days_in_text(14, "two weeks after his release")
    assert days_in_text(0, "hours after being released")
    assert not days_in_text(5, "three days before")


def test_unverified_days_are_blanked():
    cls = _cls(days_since_release=10)
    problem, dropped = check_evidence(cls, ARTICLE)
    assert problem is None and cls.days_since_release is None
    assert "days_since_release=10" in dropped


# --- rows, keys, merge, link -----------------------------------------------

def test_subject_key_matches_repeat_offender_key():
    assert subject_key("John M. Smith Jr.", "oh") == "smith_john_OH"
    assert subject_key("José Álvarez-García", "TX") == "garcia_jose_TX"
    assert subject_key("Cher", "CA") == ""


def test_make_row_from_classification():
    row = make_row(7, _cls(), {"url": "https://a.com/x", "source": "A", "title": "t"},
                   smi_coding.code(ARTICLE))
    assert row["id"] == "SMI-000007"
    assert row["subject_key"] == "example_tyler_MA"
    assert row["prior_hospital_discharge"] == "true" and row["competency"] == "false"
    assert row["signal_evidence"].startswith("prior_hospital_discharge: ")
    assert row["coded_by"] == "claude" and row["review_status"] == "pending"
    assert set(row) == set(config.CSV_COLUMNS)


def test_make_candidate_row_from_phrasebook():
    row = make_candidate_row(1, smi_coding.code(ARTICLE),
                             {"url": "https://a.com/x", "source": "A", "title": "Headline", "published": "2026-05-12"})
    assert row["coded_by"] == "phrasebook" and row["subject_name"] == ""
    assert row["review_status"] == "pending"
    assert row["prior_custody_release"] == "true" and row["days_since_release"] == "3"
    assert set(row) == set(config.CSV_COLUMNS)


def test_merge_fills_blanks_and_ors_signals():
    keep = make_row(1, _cls(signal_quotes={}, days_since_release=None, age=None),
                    {"url": "https://a.com/1", "source": "A", "title": "t"})
    new = make_row(2, _cls(), {"url": "https://b.com/2", "source": "B", "title": "t"})
    assert obvious_same_incident(new, keep)
    merge_into(keep, new)
    assert keep["additional_sources"] == "https://b.com/2"
    assert keep["age"] == "46" and keep["days_since_release"] == "3"
    assert keep["prior_hospital_discharge"] == "true"
    assert "[https://b.com/2]" in keep["signal_evidence"]


def test_link_repeat_offenders(tmp_path):
    p = tmp_path / "stories.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "offender_key", "state", "age", "incident_date"])
        w.writeheader()
        w.writerow({"id": "1234", "offender_key": "example_tyler_MA", "state": "MA", "age": "45",
                    "incident_date": "2025-04-01"})
        w.writerow({"id": "999", "offender_key": "example_tyler_MA", "state": "MA", "age": "19",
                    "incident_date": "2025-04-01"})   # same name, impossible age
    rows = [{"subject_key": "example_tyler_MA", "state": "MA", "age": "46",
             "incident_date": "2026-05-11"}, {"subject_key": ""}]
    assert link_repeat_offenders(rows, [p]) == 1
    assert rows[0]["repeat_offender_ids"] == "1234" and rows[1]["repeat_offender_ids"] == ""


# --- the processing loop, with the network and the model stubbed ----------

def _candidates():
    return [
        {"url": "https://news.example.com/2026/10/10/shooting", "title": "Man charged in shooting",
         "source": "Example News", "published": "2026-10-10"},
        {"url": "https://news.example.com/2026/10/10/council", "title": "Opinion: council must act",
         "source": "Example News", "published": "2026-10-10"},
        {"url": "https://news.example.com/2026/10/11/robbery", "title": "Man arrested in robbery",
         "source": "Example News", "published": "2026-10-11"},
    ]


TEXTS = {
    "https://news.example.com/2026/10/10/shooting": ARTICLE,
    "https://news.example.com/2026/10/11/robbery": "Police arrested a man after a store robbery. " * 20,
}
WINDOW = datetime(2026, 10, 1)


def _stub_model(monkeypatch, cls=None, dup=None):
    monkeypatch.setattr(process, "fetch_article_text_cached", TEXTS.get)
    monkeypatch.setattr(process, "triage_candidates", lambda client, cs: [True] * len(cs))
    monkeypatch.setattr(process, "classify_article", lambda client, c, text, coding: cls or _cls())
    monkeypatch.setattr(process, "check_duplicate",
                        dup or (lambda client, row, rows: DedupeResult(relation="unrelated")))


def test_code_candidates_no_llm_queues_for_review(monkeypatch):
    monkeypatch.setattr(process, "fetch_article_text_cached", TEXTS.get)
    queue, incidents = [], []
    counts = process.code_candidates(_candidates(), queue, incidents, window_start=WINDOW)
    assert counts["prefiltered"] == 1      # the opinion headline
    assert counts["no_mh"] == 1            # the robbery has no mental-health language
    assert counts["new"] == 1 and queue[0]["coded_by"] == "phrasebook"
    assert queue[0]["review_status"] == "pending" and incidents == []
    # A second run skips what the queue already has.
    again = process.code_candidates(_candidates(), queue, incidents, window_start=WINDOW)
    assert again["new"] == 0 and len(queue) == 1


def test_classified_run_queues_and_never_writes_incidents(monkeypatch):
    _stub_model(monkeypatch)
    queue, incidents, seen = [], [], set()
    counts = process.process_candidates(object(), _candidates(), queue, incidents, seen,
                                        window_start=WINDOW)
    assert counts["new"] == 1 and counts["no_mh"] == 1 and counts["prefiltered"] == 1
    assert queue[0]["subject_name"] == "Tyler Example" and queue[0]["review_status"] == "pending"
    assert incidents == []
    assert "https://news.example.com/2026/10/10/shooting" in seen


def test_classified_row_replaces_pending_phrasebook_row(monkeypatch):
    monkeypatch.setattr(process, "fetch_article_text_cached", TEXTS.get)
    queue, incidents = [], []
    process.code_candidates(_candidates(), queue, incidents, window_start=WINDOW)
    pb_id = queue[0]["id"]
    _stub_model(monkeypatch)
    process.process_candidates(object(), _candidates(), queue, incidents, set(), window_start=WINDOW)
    assert len(queue) == 1
    assert queue[0]["id"] == pb_id and queue[0]["coded_by"] == "claude"


def test_report_of_a_queued_incident_merges_into_it(monkeypatch):
    _stub_model(monkeypatch)
    queue, incidents = [], []
    process.process_candidates(object(), _candidates(), queue, incidents, set(), window_start=WINDOW)
    _stub_model(monkeypatch, dup=lambda client, row, rows: DedupeResult(
        relation="same_incident", matching_id=rows[0]["id"]))
    monkeypatch.setattr(process, "fetch_article_text_cached", lambda url: ARTICLE)
    second = [{"url": "https://other.example.org/story/5", "title": "Gunman was just released",
               "source": "Other", "published": "2026-10-12"}]
    counts = process.process_candidates(object(), second, queue, incidents, set(), window_start=WINDOW)
    assert counts["duplicates"] == 1 and len(queue) == 1
    assert queue[0]["additional_sources"] == "https://other.example.org/story/5"


def test_report_of_an_accepted_incident_is_queued_as_duplicate(monkeypatch):
    accepted = make_row(1, _cls(), {"url": "https://a.com/1", "source": "A", "title": "t"})
    accepted["review_status"] = "accepted"
    queue, incidents = [dict(accepted)], [dict(accepted)]
    _stub_model(monkeypatch, dup=lambda client, row, rows: DedupeResult(
        relation="same_incident", matching_id="SMI-000001"))
    process.process_candidates(object(), _candidates()[:1], queue, incidents, set(), window_start=WINDOW)
    assert len(queue) == 2 and queue[1]["duplicate_of"] == "SMI-000001"
    assert len(incidents) == 1 and incidents[0]["additional_sources"] == ""   # unchanged until review


def test_killed_by_police_is_never_queued(monkeypatch):
    _stub_model(monkeypatch, cls=_cls(killed_by_police=True))
    queue = []
    counts = process.process_candidates(object(), _candidates(), queue, [], set(), window_start=WINDOW)
    assert queue == [] and counts["rejected"] == 1


# --- review -----------------------------------------------------------------

def _queued(**kw) -> dict:
    row = make_row(1, _cls(), {"url": "https://a.com/1", "source": "A", "title": "t"})
    row.update(kw)
    return row


def test_accept_promotes_with_edits(monkeypatch):
    monkeypatch.setattr(review, "link_repeat_offenders", lambda rows: 0)
    queue, incidents = [_queued()], []
    ok, msg = review.apply_review("SMI-000001", "accepted",
                                  {"other_party": "stranger", "homeless": True, "age": "47"},
                                  queue, incidents)
    assert ok, msg
    assert len(incidents) == 1 and incidents[0]["other_party"] == "stranger"
    assert incidents[0]["homeless"] == "true" and "homeless: (set by reviewer)" in incidents[0]["signal_evidence"]
    assert queue[0]["review_status"] == "accepted" and queue[0]["reviewed_date"]


@pytest.mark.parametrize("edits,reason", [
    ({"age": "16"}, "under 18"),
    ({"state": "ON"}, "not a US state"),
    ({"subject_role": ""}, "subject role"),
    ({"other_party": "neighbor"}, "not in the codebook"),
])
def test_accept_enforces_inclusion_rules(monkeypatch, edits, reason):
    monkeypatch.setattr(review, "link_repeat_offenders", lambda rows: 0)
    queue, incidents = [_queued()], []
    ok, msg = review.apply_review("SMI-000001", "accepted", edits, queue, incidents)
    assert not ok and reason in msg
    assert incidents == [] and queue[0]["review_status"] == "pending"


def test_rejecting_an_accepted_row_removes_it(monkeypatch):
    monkeypatch.setattr(review, "link_repeat_offenders", lambda rows: 0)
    queue, incidents = [_queued()], []
    review.apply_review("SMI-000001", "accepted", {}, queue, incidents)
    ok, _ = review.apply_review("SMI-000001", "rejected", {}, queue, incidents)
    assert ok and incidents == [] and queue[0]["review_status"] == "rejected"


def test_accepting_a_duplicate_merges_into_the_incident(monkeypatch):
    monkeypatch.setattr(review, "link_repeat_offenders", lambda rows: 0)
    incident = _queued(review_status="accepted", age="")
    dup = make_row(2, _cls(), {"url": "https://b.com/2", "source": "B", "title": "t"})
    dup["duplicate_of"] = "SMI-000001"
    queue, incidents = [dict(incident), dup], [dict(incident)]
    ok, msg = review.apply_review("SMI-000002", "accepted", {}, queue, incidents)
    assert ok and "Merged into SMI-000001" in msg
    assert len(incidents) == 1 and incidents[0]["additional_sources"] == "https://b.com/2"
    assert incidents[0]["age"] == "46"   # a blank filled from the second report


def test_save_without_deciding(monkeypatch):
    monkeypatch.setattr(review, "link_repeat_offenders", lambda rows: 0)
    queue, incidents = [_queued()], []
    ok, _ = review.apply_review("SMI-000001", None, {"city": "Boston", "competency": False}, queue, incidents)
    assert ok and queue[0]["city"] == "Boston" and queue[0]["review_status"] == "pending"
    assert incidents == []


def test_review_page_renders():
    page = review._page()
    assert "SMI review queue" in page and "acquaintance" in page and "__CODEBOOK__" not in page


def test_reference_set_is_present():
    with open(config.REFERENCE_CSV, newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 274
