"""Offline tests for the SMI tracker: no network, no API key, no model calls.
Article text here is invented for the tests."""

import csv
import json
import re
from datetime import datetime

import pytest

import config
import incidents
import process
import review
import smi_coding
from classify import IncidentClassification, check_evidence, days_in_text, parse_classification
from dedupe import DedupeResult, merge_into, obvious_same_incident
from link import link_repeat_offenders
from prefilter import is_foreign_outlet, prefilter
from store import claude_fields, make_candidate_row, make_row, subject_key

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
    assert len(config.GDELT_QUERIES) == 12
    for q in config.GDELT_QUERIES:
        assert len(q) <= config.GDELT_MAX_QUERY_CHARS, (len(q), q)   # GDELT: "too long"
        assert q.count("(") == q.count(")"), q
        # GDELT: "Parentheses may only be used around OR'd statements."
        for group in re.findall(r"\(([^()]*)\)", q):
            assert " OR " in group, (group, q)
        assert q.count('"') % 2 == 0, q
        if " OR " in q:
            assert q.startswith("("), q   # GDELT needs ORs inside parentheses


def test_gdelt_grid_covers_every_combination():
    pairs = set()
    for q in config.GDELT_QUERIES[6:]:   # the grid queries follow the six block queries
        m = re.match(r"\((.*?)\) (.*)$", q)
        clinical = m.group(1).split(" OR ")
        rest = m.group(2)
        incident = rest[1:-1].split(" OR ") if rest.startswith("(") else [rest]
        pairs |= {(c, i) for c in clinical for i in incident}
    expected = {(c, i) for c in config._CLINICAL for i in config._INCIDENT}
    assert expected <= pairs


def test_codebook_shape():
    assert config.SUBJECT_ROLES == ["subject_accused", "subject_victim"]
    assert "prior_hospitalization" not in config.SIGNAL_FIELDS
    assert "victim_smi_status" not in config.ARTICLE_COLUMNS
    assert "fatal police encounter" in config.INCIDENT_FORMS   # kept, flagged
    assert "acquaintance" in config.OTHER_PARTIES
    for cols in (config.ARTICLE_COLUMNS, config.INCIDENT_COLUMNS):
        assert len(set(cols)) == len(cols)
        assert "killed_by_police" in cols
    assert {"review_status", "excluded_reason", "incident_id", "headline"} <= set(config.ARTICLE_COLUMNS)
    assert {"article_ids", "article_count"} <= set(config.INCIDENT_COLUMNS)


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


def test_police_killings_are_recorded_not_dropped():
    r = smi_coding.code("The man, who had schizophrenia, was fatally shot by police after the standoff.")
    assert r["killed_by_police"] and r["incident_form"] == "fatal police encounter"
    assert smi_coding.code("A homeless man with schizophrenia froze to death outside the shelter."
                           )["subject_role"] == "subject_victim"


def test_days_since_release_needs_a_release_sentence():
    assert smi_coding.code("Two days earlier, he bought a knife.")["days_since_release"] is None
    assert smi_coding.code("He was discharged a week before the attack.")["days_since_release"] == 7
    assert smi_coding.code("He was released within hours of arriving.")["days_since_release"] == 0


# --- headline and domain checks ---------------------------------------------

def test_prefilter():
    start, end = datetime(2026, 10, 1), datetime(2026, 10, 14)
    ok = {"title": "Mother shares story of son's arrest - WXYZ", "url": "https://wxyz.com/a",
          "source": "WXYZ", "published": "2026-10-10"}
    assert prefilter(ok, start, end) is None
    assert prefilter({**ok, "title": "Opinion: Fix the mental health system"}, start, end)
    assert prefilter({**ok, "title": "Lawmakers weigh involuntary commitment bill"}, start, end)
    for title in ("Calls to service week of 10/20/2026-10/22/2026 - Panola Watchman",
                  "Pa Senators advance legislation to create online registry - WGAL",
                  "Systematic review and meta-analysis of CBT for insomnia - Nature"):
        assert prefilter({**ok, "title": title}, start, end), title
    assert prefilter({**ok, "published": "2019-01-01"}, start, end) == "published before the search window"
    assert prefilter({**ok, "published": "2026-10-20"}, start, end) == "published after the search window"
    assert prefilter({**ok, "source": "Manchester Evening News"}, start, end) == "foreign outlet"
    assert prefilter({**ok, "url": "https://www.example.co.uk/x"}, start, end) == "foreign outlet"
    assert prefilter({**ok, "source": "Daily Mail"}, start, end) is None


@pytest.mark.parametrize("url,foreign", [
    ("https://www.examinerlive.co.uk/news/tiger-attack", True),
    ("https://thenightly.com.au/news/attack", True),
    ("https://leadership.ng/kano-court", True),
    ("https://www.cbc.ca/news/canada/x", True),
    ("https://www.ekathimerini.gr/news/x", True),
    ("https://www.example.com:443/x", False),
    ("https://www.wbur.org/news/2026/05/12/x", False),
    ("https://nypost.com/2026/10/24/us-news/x", False),
    ("https://www.ci.example.ny.us/x", False),
    ("https://www.example.tv/x", False),
])
def test_non_us_domains(url, foreign):
    assert is_foreign_outlet({"url": url, "source": ""}) is foreign


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


def test_killed_by_police_is_kept():
    problem, _ = check_evidence(_cls(killed_by_police=True), ARTICLE)
    assert problem is None


@pytest.mark.parametrize("kw,reason", [
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


CAND = {"url": "https://a.com/x", "source": "A", "title": "Headline", "published": "2026-05-12"}


def test_make_row_from_classification():
    row = make_row(7, _cls(killed_by_police=True), CAND, smi_coding.code(ARTICLE))
    assert row["id"] == "SMI-000007" and row["headline"] == "Headline"
    assert row["subject_key"] == "example_tyler_MA" and row["killed_by_police"] == "true"
    assert row["prior_hospital_discharge"] == "true" and row["competency"] == "false"
    assert row["signal_evidence"].startswith("prior_hospital_discharge: ")
    assert row["coded_by"] == "claude" and row["review_status"] == "pending"
    assert set(row) == set(config.ARTICLE_COLUMNS)


def test_make_candidate_row_from_phrasebook():
    row = make_candidate_row(1, smi_coding.code(ARTICLE), CAND)
    assert row["coded_by"] == "phrasebook" and row["subject_name"] == ""
    assert row["review_status"] == "pending" and row["published"] == "2026-05-12"
    assert row["prior_custody_release"] == "true" and row["days_since_release"] == "3"
    assert set(row) == set(config.ARTICLE_COLUMNS)


def test_merge_fills_blanks_and_ors_signals():
    keep = make_row(1, _cls(signal_quotes={}, days_since_release=None, age=None),
                    {"url": "https://a.com/1", "source": "A", "title": "t"})
    new = make_row(2, _cls(killed_by_police=True), {"url": "https://b.com/2", "source": "B", "title": "t"})
    assert obvious_same_incident(new, keep)
    merge_into(keep, new)
    assert keep["additional_sources"] == "https://b.com/2"
    assert keep["age"] == "46" and keep["days_since_release"] == "3"
    assert keep["prior_hospital_discharge"] == "true" and keep["killed_by_police"] == "true"
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


# --- harvest and classify, with the network and the model stubbed ----------

def _candidates():
    return [
        {"url": "https://news.example.com/2026/10/10/shooting", "title": "Man charged in shooting",
         "source": "Example News", "published": "2026-10-10"},
        {"url": "https://news.example.com/2026/10/10/council", "title": "Opinion: council must act",
         "source": "Example News", "published": "2026-10-10"},
        {"url": "https://news.example.com/2026/10/11/robbery", "title": "Man arrested in robbery",
         "source": "Example News", "published": "2026-10-11"},
        {"url": "https://www.example.co.uk/2026/10/11/tiger", "title": "Man killed by tiger",
         "source": "Example UK", "published": "2026-10-11"},
    ]


TEXTS = {
    "https://news.example.com/2026/10/10/shooting": ARTICLE,
    "https://news.example.com/2026/10/11/robbery": "Police arrested a man after a store robbery. " * 20,
}
START, END = datetime(2026, 10, 1), datetime(2026, 10, 14)


def test_harvest_without_claude(monkeypatch):
    monkeypatch.setattr(process, "fetch_article_text_cached", TEXTS.get)
    articles, seen = [], set()
    counts = process.harvest_articles(None, _candidates(), articles, seen, START, END)
    assert counts["prefiltered"] == 2      # the opinion headline and the .co.uk link
    assert counts["no_mh"] == 1            # the robbery has no mental-health language
    assert counts["new"] == 1 and articles[0]["coded_by"] == "phrasebook"
    assert articles[0]["review_status"] == "pending"
    again = process.harvest_articles(None, _candidates(), articles, seen, START, END)
    assert again["new"] == 0 and len(articles) == 1


def test_decoded_google_link_on_a_foreign_domain_is_dropped(monkeypatch):
    monkeypatch.setattr(process, "fetch_article_text_cached", lambda url: ARTICLE)
    monkeypatch.setattr(process, "resolve_candidate",
                        lambda c: c.update(url="https://www.examinerlive.co.uk/news/x", google_url=c["url"]))
    google = [{"url": "https://news.google.com/rss/articles/abc", "title": "Tiger attack",
               "source": "Examiner Live", "published": "2026-10-10"}]
    articles = []
    counts = process.harvest_articles(None, google, articles, set(), START, END)
    assert counts["foreign"] == 1 and articles == []


def test_claude_codes_every_unrejected_phrasebook_article(monkeypatch):
    monkeypatch.setattr(process, "fetch_article_text_cached", lambda url: ARTICLE)
    articles = [make_candidate_row(i, smi_coding.code(ARTICLE),
                                   {**CAND, "url": f"https://a.com/{i}"}) for i in (1, 2, 3)]
    articles[0]["review_status"] = "accepted"
    articles[2]["review_status"] = "rejected"
    calls = []
    monkeypatch.setattr(process, "classify_article",
                        lambda client, c, text, coding: calls.append(c["url"]) or _cls())
    counts = process.classify_articles(object(), articles)
    assert counts["coded"] == 2 and sorted(calls) == ["https://a.com/1", "https://a.com/2"]
    assert articles[0]["subject_name"] == "Tyler Example" and articles[0]["coded_by"] == "claude"
    assert articles[0]["review_status"] == "accepted"          # review is never changed
    assert articles[2]["coded_by"] == "phrasebook"              # rejected: never coded


def test_claude_exclusion_keeps_review_status(monkeypatch):
    monkeypatch.setattr(process, "fetch_article_text_cached", lambda url: ARTICLE)
    articles = [make_candidate_row(1, smi_coding.code(ARTICLE), CAND)]
    articles[0]["review_status"] = "accepted"
    monkeypatch.setattr(process, "classify_article",
                        lambda client, c, text, coding: _cls(state="ON"))
    counts = process.classify_articles(object(), articles)
    assert counts["excluded"] == 1
    assert articles[0]["review_status"] == "accepted" and "not a US state" in articles[0]["excluded_reason"]


# --- incidents ---------------------------------------------------------------

def _coded(n: int, status="accepted", **kw) -> dict:
    row = make_row(n, _cls(**kw), {**CAND, "url": f"https://site{n}.com/story"})
    row["review_status"] = status
    return row


def test_incidents_group_reports_of_one_incident(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    articles = [_coded(1, age=None), _coded(2), _coded(3, subject_name="Other Person", city="Salem"),
                _coded(4, status="pending"), _coded(5, status="rejected")]
    articles[1]["killed_by_police"] = "true"
    rows, counts = incidents.build_incidents(articles)
    assert counts == {"articles": 3, "incidents": 2, "grouped": 1, "new": 2}
    first = next(r for r in rows if "SMI-000001" in r["article_ids"])
    assert first["article_ids"] == "SMI-000001 SMI-000002" and first["article_count"] == "2"
    assert first["additional_sources"] == "https://site2.com/story"
    assert first["age"] == "46" and first["killed_by_police"] == "true"
    assert articles[0]["incident_id"] == articles[1]["incident_id"] == first["id"]
    assert articles[3]["incident_id"] == "" and set(rows[0]) == set(config.INCIDENT_COLUMNS)


def test_incidents_skip_excluded_and_phrasebook_and_keep_ids(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    pb = make_candidate_row(9, smi_coding.code(ARTICLE), CAND)
    pb["review_status"] = "accepted"
    excluded = _coded(8)
    excluded["excluded_reason"] = "state 'ON' is not a US state"
    articles = [_coded(1), pb, excluded]
    rows, _ = incidents.build_incidents(articles)
    assert len(rows) == 1 and rows[0]["article_ids"] == "SMI-000001"
    first_id = rows[0]["id"]
    articles.append(_coded(2, subject_name="Other Person", city="Salem"))
    rows, counts = incidents.build_incidents(articles)
    assert counts["new"] == 1 and articles[0]["incident_id"] == first_id   # stable on rebuild


# --- review -----------------------------------------------------------------

def test_accept_needs_nothing_filled_in():
    articles = [make_candidate_row(1, smi_coding.code(ARTICLE), CAND)]
    ok, msg = review.apply_review("SMI-000001", "accepted", {}, articles)
    assert ok and articles[0]["review_status"] == "accepted" and articles[0]["reviewed_date"]


def test_review_edits_are_checked_but_optional():
    articles = [make_candidate_row(1, smi_coding.code(ARTICLE), CAND)]
    ok, _ = review.apply_review("SMI-000001", None, {"other_party": "acquaintance", "homeless": True},
                                articles)
    assert ok and articles[0]["other_party"] == "acquaintance"
    assert "homeless: (set by reviewer)" in articles[0]["signal_evidence"]
    ok, msg = review.apply_review("SMI-000001", "accepted", {"age": "16"}, articles)
    assert not ok and "under 18" in msg and articles[0]["review_status"] == "pending"


def test_review_page_renders():
    page = review._page()
    assert "SMI article review" in page and "acquaintance" in page and "__CODEBOOK__" not in page


def test_reference_set_is_present():
    with open(config.REFERENCE_CSV, newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 274
