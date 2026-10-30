"""Offline tests for the SMI tracker: no network, no API key, no model calls.
Article text here is invented for the tests."""

import csv
import json
import re
from datetime import datetime

import pytest

import config
import corroborate
import crosslist
import incidents
import process
import review
import rules
import smi_coding
import validate
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
    rows, counts, gone = incidents.build_incidents(articles)
    assert counts == {"articles": 4, "incidents": 2, "grouped": 2, "new": 2, "qualifying": 2,
                      "redundant": 1, "removed": 0} and gone == []
    first = next(r for r in rows if "SMI-000001" in r["article_ids"])
    # 2 is the second outlet (kept as the check); 4 repeats what 1 and 2 said.
    assert first["article_ids"] == "SMI-000001 SMI-000002" and first["article_count"] == "2"
    assert first["redundant_article_ids"] == "SMI-000004"
    assert first["additional_sources"] == "https://site2.com/story"
    assert first["age"] == "46" and first["killed_by_police"] == "true"
    assert [a["contribution"] for a in articles] == ["primary", "corroboration", "primary",
                                                     "redundant", ""]
    assert articles[0]["incident_id"] == articles[1]["incident_id"] == articles[3]["incident_id"]
    assert articles[4]["incident_id"] == "" and set(rows[0]) == set(config.INCIDENT_COLUMNS)
    assert first["qualifies_smi"] == "yes" and first["qualify_note"] == "prior_hospital_discharge"


def test_incidents_skip_excluded_and_phrasebook_and_keep_ids(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    pb = make_candidate_row(9, smi_coding.code(ARTICLE), CAND)
    pb["review_status"] = "accepted"
    excluded = _coded(8)
    excluded["excluded_reason"] = "state 'ON' is not a US state"
    articles = [_coded(1), pb, excluded]
    rows, _, _ = incidents.build_incidents(articles)
    assert len(rows) == 1 and rows[0]["article_ids"] == "SMI-000001"
    first_id = rows[0]["id"]
    articles.append(_coded(2, subject_name="Other Person", city="Salem"))
    rows, counts, _ = incidents.build_incidents(articles, previous=rows)
    assert counts["new"] == 1 and articles[0]["incident_id"] == first_id   # stable on rebuild


def _report(n: int, host: str, day: int, **signals) -> dict:
    """An article about the Tyler Example shooting from `host`, published on
    day `day`, stating the given signals (field -> quote)."""
    diagnoses = signals.pop("diagnoses", None)
    row = make_row(n, _cls(signal_quotes=signals, diagnoses=diagnoses, days_since_release=None),
                   {**CAND, "url": f"https://{host}/story{n}", "source": host,
                    "published": f"2026-05-{day:02d}"})
    return row


def test_later_articles_are_kept_only_for_new_information(monkeypatch):
    """The five-article example: hospitalization; a named diagnosis (second
    outlet); family pleas; a repeat of the first and third (nothing new);
    a prior arrest."""
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    hosp = "He had been hospitalized several times before the shooting, police said."
    family = "His mother had begged doctors to keep him in the hospital last spring."
    articles = [
        _report(1, "a.com", 12, prior_hospital_discharge=hosp),
        _report(2, "b.com", 13, diagnosis_named="He had been diagnosed with schizophrenia as a teen.",
                diagnoses="schizophrenia"),
        _report(3, "c.com", 14, family_sought_help=family),
        _report(4, "d.com", 15, prior_hospital_discharge=hosp, family_sought_help=family),
        _report(5, "e.com", 16, prior_arrests="He had been arrested on assault charges in 2021."),
    ]
    rows, counts, _ = incidents.build_incidents(articles)
    assert len(rows) == 1 and counts["redundant"] == 1
    r = rows[0]
    assert [a["contribution"] for a in articles] == ["primary", "corroboration", "context",
                                                     "redundant", "context"]
    assert articles[2]["added_info"] == "family_sought_help"
    assert articles[4]["added_info"] == "prior_arrests"
    assert r["article_ids"] == "SMI-000001 SMI-000002 SMI-000003 SMI-000005"
    assert r["redundant_article_ids"] == "SMI-000004"
    assert "d.com" not in r["additional_sources"]
    for f in ("prior_hospital_discharge", "diagnosis_named", "family_sought_help", "prior_arrests"):
        assert r[f] == "true", f
    assert r["diagnoses"] == "schizophrenia"
    assert "[https://e.com/story5]" in r["signal_evidence"]


def test_second_outlet_is_kept_even_with_nothing_new(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    same = "He had been hospitalized several times before the shooting, police said."
    one = [_report(1, "wxyz.com", 12, prior_hospital_discharge=same),
           _report(2, "wxyz.com", 13, prior_hospital_discharge=same),       # same outlet: no check
           _report(3, "kare11.com", 14, prior_hospital_discharge=same)]     # second outlet: the check
    incidents.build_incidents(one)
    assert [a["contribution"] for a in one] == ["primary", "redundant", "corroboration"]
    # A trusted first outlet needs no second one: a repeat adds nothing.
    trusted = [_report(1, "nytimes.com", 12, prior_hospital_discharge=same),
               _report(2, "kare11.com", 13, prior_hospital_discharge=same)]
    incidents.build_incidents(trusted)
    assert [a["contribution"] for a in trusted] == ["primary", "redundant"]


def test_removed_incident_ids_are_never_reused(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    articles = [_coded(1)]
    rows, _, _ = incidents.build_incidents(articles)
    assert rows[0]["id"] == "INC-000001"
    articles[0]["review_status"] = "rejected"
    articles.append(_coded(2, subject_name="Other Person", city="Salem"))
    rows, counts, gone = incidents.build_incidents(articles, previous=rows)
    assert [g["id"] for g in gone] == ["INC-000001"] and counts["removed"] == 1
    assert gone[0]["removed_reason"] and rows[0]["id"] == "INC-000002"
    rows, _, _ = incidents.build_incidents(articles + [_coded(3, subject_name="Third Person")],
                                           previous=rows, removed=gone)
    assert sorted(r["id"] for r in rows) == ["INC-000002", "INC-000003"]


def test_rebuild_keeps_corroboration_and_site_exclusion(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    articles = [_coded(1, prior_count_arrests=12,
                       prior_evidence_quote="He has a criminal history spanning two decades.")]
    rows, _, _ = incidents.build_incidents(articles)
    assert rows[0]["qualifies_strict"] == "yes" and "offender_category" not in rows[0]
    rows[0].update(corroboration_checked="2026-10-27", site_excluded="court-only coverage")
    rows, _, _ = incidents.build_incidents(articles, previous=rows)
    assert rows[0]["corroboration_checked"] == "2026-10-27"
    assert rows[0]["site_excluded"] == "court-only coverage"   # set by hand, kept by every rebuild


def test_subjects_table():
    a = {"subject_key": "example_tyler_MA", "subject_name": "Tyler Example", "state": "MA",
         "id": "INC-000002", "incident_date": "2026-05-11", "qualifies_smi": "yes",
         "prior_count_arrests": "12", "prior_hospital_discharge": "true", "diagnoses": "schizophrenia",
         "repeat_offender_ids": "1234"}
    b = dict(a, id="INC-000001", incident_date="2025-01-02", qualifies_smi="no",
             prior_count_arrests="9", prior_hospital_discharge="false", aot="true",
             diagnoses="bipolar disorder", subject_name="Tyler J. Example")
    rows = incidents.build_subjects([a, b, {"subject_key": "", "id": "INC-000003"}])
    assert len(rows) == 1
    s = rows[0]
    assert s["incident_ids"] == "INC-000001 INC-000002" and s["incident_count"] == "2"
    assert s["qualifying_incident_count"] == "1" and s["latest_prior_arrests"] == "12"
    assert s["subject_name"] == "Tyler J. Example" and s["signals"] == "prior_hospital_discharge aot"
    assert s["diagnoses"] == "bipolar disorder; schizophrenia" and s["repeat_offender_ids"] == "1234"
    assert set(s) == set(config.SUBJECT_COLUMNS)


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


# --- the inclusion rule -----------------------------------------------------

QUALIFYING = {"state": "MA", "age": "46", "subject_role": "subject_accused", "outcome": "charged",
              "violence": "false", "fatality": "false", "killed_by_police": "false",
              "prior_hospital_discharge": "true"}


@pytest.mark.parametrize("change,ok,note", [
    ({}, True, "prior_hospital_discharge"),
    ({"state": ""}, False, "no US state"),
    ({"state": "ON"}, False, "no US state"),
    ({"age": "17"}, False, "under 18"),
    ({"age": ""}, True, ""),
    ({"prior_hospital_discharge": "false", "edp_call": "true"}, True, "edp_call"),
    ({"prior_hospital_discharge": "false", "mh_history": "true"}, True, "mh_history"),
    ({"prior_hospital_discharge": "false", "psych_medication": "true"}, True, "psych_medication"),
    ({"prior_hospital_discharge": "false", "family_sought_help": "true"}, True, "family_sought_help"),
    ({"prior_hospital_discharge": "false", "police_brought_for_eval": "true"}, True, ""),
    ({"prior_hospital_discharge": "false", "prior_police_contact": "true"}, True, ""),
    ({"prior_hospital_discharge": "false", "diagnosis_named": "true"}, True, ""),
    ({"prior_hospital_discharge": "false", "prior_evidence_quote": "He has 12 prior arrests."}, True,
     "prior record"),
    ({"prior_hospital_discharge": "false", "release_status": "probation"}, True, "release status"),
    # Not earlier contacts on their own.
    ({"prior_hospital_discharge": "false", "crisis_call": "true"}, False, "no earlier system contact"),
    ({"prior_hospital_discharge": "false", "competency": "true"}, False, "no earlier system contact"),
    ({"prior_hospital_discharge": "false", "homeless": "true"}, False, "no earlier system contact"),
    # Harm: violence or a death without an arrest; otherwise an arrest for anything.
    ({"outcome": "hospitalized"}, False, "no violence, death or arrest"),
    ({"outcome": "hospitalized", "violence": "true"}, True, ""),
    ({"outcome": "", "fatality": "true"}, True, ""),
    ({"outcome": "arrested", "violence": "false"}, True, ""),
    # The subject harmed counts too: a police killing.
    ({"subject_role": "subject_victim", "killed_by_police": "true", "fatality": "true",
      "outcome": "died"}, True, ""),
])
def test_inclusion_rule(change, ok, note):
    got, why = rules.qualifies_smi({**QUALIFYING, **change})
    assert got is ok and note in why


def test_codebook_rule_sets():
    assert set(config.QUALIFYING_SIGNALS) < set(config.SIGNAL_FIELDS)
    assert not {"competency", "crisis_call", "homeless"} & set(config.QUALIFYING_SIGNALS)
    assert {"edp_call", "mh_history", "psych_medication"} <= set(config.QUALIFYING_SIGNALS)
    assert set(config.PRIOR_RECORD_FIELDS) <= set(config.ARTICLE_COLUMNS)
    # Read from the repeat-offender config, not copied.
    assert "probation" in config.RELEASE_STATUSES and "nytimes.com" in config.TRUSTED_OUTLETS
    assert config.STRICT_MIN_FELONY_CONVICTIONS == 3


# --- new signals and the prior record ------------------------------------------

def test_phrasebook_new_signals():
    r = smi_coding.code("Officers responded to a call about an emotionally disturbed person. "
                        "He had a long history of mental illness and was prescribed antipsychotic "
                        "medication, his sister said.")
    assert {"edp_call", "mh_history", "psych_medication"} <= set(r["signals"])
    assert "crisis_call" not in r["signals"]          # an EDP call is its own signal
    assert "psych_medication" in smi_coding.code(ARTICLE)["signals"]   # "stopped taking his medication"


RECORD = ("Tyler Example, 46, was charged with assault. Court records show he had been arrested "
          "12 times since 2010, including for robbery. He was on probation for a 2023 assault at "
          "the time, prosecutors said. His family said he had schizophrenia and had stopped taking "
          "his medication weeks earlier.")
COUNT_QUOTE = "Court records show he had been arrested 12 times since 2010, including for robbery."
RELEASE_QUOTE = "He was on probation for a 2023 assault at the time, prosecutors said."
LAPSE_QUOTE = "His family said he had schizophrenia and had stopped taking his medication weeks earlier."


def _record_cls(**kw) -> IncidentClassification:
    base = dict(mh_evidence_quote=LAPSE_QUOTE, signal_quotes={"medication_lapse": LAPSE_QUOTE},
                days_since_release=None, prior_count_arrests=12, prior_offenses="robbery",
                prior_evidence_quote=COUNT_QUOTE, release_status="probation",
                release_evidence_quote=RELEASE_QUOTE)
    base.update(kw)
    return _cls(**base)


def test_prior_record_is_kept_and_sets_its_signals():
    cls = _record_cls()
    problem, dropped = check_evidence(cls, RECORD)
    assert problem is None and dropped == []
    assert cls.prior_count_arrests == 12 and cls.release_status == "probation"
    assert cls.signal_quotes["prior_arrests"] == COUNT_QUOTE
    assert cls.signal_quotes["prior_custody_release"] == RELEASE_QUOTE
    assert "psych_medication" in cls.signal_quotes        # implied by medication_lapse
    row = make_row(1, cls, CAND)
    assert row["prior_count_arrests"] == "12" and row["release_status"] == "probation"
    assert row["prior_arrests"] == "true"


def test_unverified_prior_record_is_dropped_not_rejected():
    cls = _record_cls(prior_count_arrests=15)                    # not printed
    problem, dropped = check_evidence(cls, RECORD)
    assert problem is None and cls.prior_count_arrests is None and "prior_count_arrests=15" in dropped
    cls = _record_cls(prior_evidence_quote="He has a lengthy record of arrests for many crimes.")
    problem, dropped = check_evidence(cls, RECORD)
    assert problem is None and "prior_record" in dropped
    assert cls.prior_count_arrests is None and cls.prior_offenses is None
    assert "prior_arrests" not in cls.signal_quotes
    cls = _record_cls(release_evidence_quote=None)
    check_evidence(cls, RECORD)
    assert cls.release_status == "none stated" and "prior_custody_release" not in cls.signal_quotes


def test_count_in_text_matches_the_repeat_offender_check():
    from classify import count_in_text
    assert count_in_text(12, "arrested a dozen times")
    assert count_in_text(3, "his fourth offense")                # Nth offense: N-1 priors
    assert not count_in_text(7, "arrested 12 times")


def test_a_matched_second_source_needs_no_mental_health_quote():
    cls = _cls(mh_evidence_quote="Not a sentence from this article at all, really.", same_incident=True)
    problem, _ = check_evidence(cls, ARTICLE, matched=True)
    assert problem is None and cls.mh_evidence_quote is None
    cls = _cls(mh_evidence_quote="Not a sentence from this article at all, really.")
    assert check_evidence(cls, ARTICLE)[0] == "mh_evidence_quote not found in article"


# --- second sources, with search and model stubbed ------------------------------

def test_corroborate_adds_matching_reports(monkeypatch):
    incident = {"id": "INC-000001", "subject_name": "Tyler Example", "city": "Cambridge",
                "state": "MA", "incident_date": "2026-05-11", "qualifies_smi": "yes",
                "source_url": "https://site1.com/story", "additional_sources": "",
                "corroboration_checked": ""}
    articles = [_coded(1)]
    articles[0]["incident_id"] = "INC-000001"
    hits = [{"url": "https://site1.com/other", "title": "same outlet", "source": "Site1", "published": "2026-05-12"},
            {"url": "https://b.com/match", "title": "Man charged", "source": "B", "published": "2026-05-12"},
            {"url": "https://c.com/other", "title": "Different man", "source": "C", "published": "2026-05-12"},
            {"url": "https://www.example.co.uk/x", "title": "UK", "source": "UK", "published": "2026-05-12"}]
    monkeypatch.setattr(corroborate, "google_news_search", lambda q, s, e: [dict(h) for h in hits])
    monkeypatch.setattr(corroborate, "fetch_article_text_cached", lambda url: ARTICLE)
    asked = []
    family = "His family said he had mental health issues and had stopped taking his medication."

    def fake_classify(client, cand, text, coding, incident=None):
        asked.append(cand["url"])
        assert incident["id"] == "INC-000001"
        return _cls(same_incident=cand["url"] == "https://b.com/match", mh_evidence_quote=None,
                    signal_quotes={"family_sought_help": family})
    monkeypatch.setattr(corroborate, "classify_article", fake_classify)
    c = corroborate.corroborate(object(), [incident], articles, set())
    assert asked == ["https://b.com/match", "https://c.com/other"]   # same outlet and .co.uk skipped
    assert c == {"checked": 1, "matched": 1, "articles_added": 1, "throttled": False}
    assert incident["corroboration_checked"]
    new = articles[-1]
    assert new["incident_id"] == "INC-000001" and new["source_url"] == "https://b.com/match"
    assert new["coded_by"] == "claude" and new["family_sought_help"] == "true"
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    rows, _, _ = incidents.build_incidents(articles)
    assert new["contribution"] == "corroboration" and rows[0]["family_sought_help"] == "true"
    assert not corroborate.needs_search(incident)      # stamped: not searched again
    off_site = {**incident, "corroboration_checked": "", "site_excluded": "court-only coverage"}
    assert not corroborate.needs_search(off_site)      # kept off the site: not searched


# --- cross-listing from the repeat-offender database -----------------------------

def test_crosslist_adds_repeat_offender_articles_with_mental_health_language(monkeypatch, tmp_path):
    p = tmp_path / "stories.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "source_url", "additional_sources", "source_name",
                                          "summary", "incident_date"])
        w.writeheader()
        w.writerow({"id": "7", "source_url": "https://a.com/mh", "additional_sources": "https://b.com/plain",
                    "source_name": "A", "summary": "Man charged", "incident_date": "2026-05-11"})
        w.writerow({"id": "8", "source_url": "https://b.com/robbery", "source_name": "B",
                    "summary": "Robbery", "incident_date": "2026-05-11"})
    robbery = "Police arrested a man after a store robbery. " * 20
    texts = {"https://a.com/mh": ARTICLE, "https://b.com/plain": robbery, "https://b.com/robbery": robbery}
    monkeypatch.setattr(crosslist, "fetch_article_text_cached", texts.get)
    articles, checked = [], set()
    c = crosslist.crosslist(articles, checked, [p], dry_run=True)
    assert c["stories_with_mh"] == 1 and articles == [] and checked == set()
    c = crosslist.crosslist(articles, checked, [p])
    assert c == {"stories": 2, "stories_with_mh": 1, "articles_added": 1, "no_text": 0}
    assert articles[0]["source_url"] == "https://a.com/mh" and articles[0]["coded_by"] == "phrasebook"
    assert "repeat-offender story 7" in articles[0]["review_note"] and checked == {"7", "8"}
    assert crosslist.crosslist(articles, checked, [p])["stories"] == 0   # each story read once


# --- validation --------------------------------------------------------------------

def test_validation_catches_bad_rows():
    row = {**QUALIFYING, "signal_evidence": "prior_hospital_discharge: q"}
    rules.apply_rules(row)
    assert validate.incident_problems(row) == []
    assert any("disagrees" in p for p in validate.incident_problems(dict(row, qualifies_smi="no")))
    assert any("no quoted evidence" in p for p in validate.incident_problems(dict(row, aot="true")))
    assert any("without prior_evidence_quote" in p
               for p in validate.coded_problems({"prior_count_arrests": "5"}))
    assert any("outside the codebook" in p for p in validate.article_problems(
        {"coded_by": "phrasebook", "review_status": "maybe"}))


# --- diagnoses, court-only stories, events abroad, removal on review ----------------

import diagnoses


def test_diagnoses_are_one_spelling_and_named_disorders_only():
    assert diagnoses.clean("PTSD; post-traumatic stress disorder; psychosis; Schizophrenia") == \
        "post-traumatic stress disorder; schizophrenia"
    assert diagnoses.clean("paranoid schizophrenia; bipolar 1; acute psychosis; multiple sclerosis") == \
        "schizophrenia; bipolar disorder"
    assert diagnoses.clean("postpartum depression; depression; mania with psychosis") == "postpartum depression"
    assert diagnoses.clean("") == "" and diagnoses.clean(None) == ""


def test_tidy_drops_diagnosis_named_when_only_symptoms_were_listed():
    row = {"diagnoses": "psychosis", "diagnosis_named": "true",
           "signal_evidence": "mh_history: q1 || diagnosis_named: q2"}
    assert diagnoses.tidy(row)
    assert row["diagnoses"] == "" and row["diagnosis_named"] == "false"
    assert row["signal_evidence"] == "mh_history: q1"
    # A named disorder alongside a symptom keeps the signal.
    row = {"diagnoses": "psychosis; schizophrenia", "diagnosis_named": "true",
           "signal_evidence": "diagnosis_named: q"}
    diagnoses.tidy(row)
    assert row["diagnoses"] == "schizophrenia" and row["diagnosis_named"] == "true"
    # Nothing listed: the quoted signal stands.
    row = {"diagnoses": "", "diagnosis_named": "true", "signal_evidence": "diagnosis_named: q"}
    assert not diagnoses.tidy(row) and row["diagnosis_named"] == "true"


def test_check_evidence_cleans_diagnoses():
    quote = "His family said he had mental health issues and had stopped taking his medication."
    cls = _cls(diagnoses="psychosis", signal_quotes={"diagnosis_named": quote})
    problem, dropped = check_evidence(cls, ARTICLE)
    assert problem is None and cls.diagnoses is None and "diagnosis_named" in dropped
    cls = _cls(diagnoses="PTSD; psychosis", signal_quotes={"diagnosis_named": quote})
    check_evidence(cls, ARTICLE)
    assert cls.diagnoses == "post-traumatic stress disorder" and "diagnosis_named" in cls.signal_quotes


def test_prompt_excludes_court_only_stories_and_events_abroad():
    from classify import SYSTEM_PROMPT
    assert "court-only story" in SYSTEM_PROMPT and "execution" in SYSTEM_PROMPT
    assert "outside the United States, wherever the subject lived" in SYSTEM_PROMPT
    assert "where the incident HAPPENED" in SYSTEM_PROMPT


def test_prompt_treats_an_earlier_crime_recounted_as_background_as_prior_history():
    from classify import SYSTEM_PROMPT
    assert "recounts an earlier crime as background, the incident is the new event" in SYSTEM_PROMPT


def test_incidents_build_cleans_existing_diagnoses(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    articles = [_coded(1, diagnoses="PTSD; psychosis")]
    rows, _, _ = incidents.build_incidents(articles)
    assert articles[0]["diagnoses"] == rows[0]["diagnoses"] == "post-traumatic stress disorder"


def test_incident_removed_on_review_keeps_the_reason(monkeypatch):
    monkeypatch.setattr(incidents, "link_repeat_offenders", lambda rows: 0)
    articles = [_coded(1)]
    rows, _, _ = incidents.build_incidents(articles)
    articles[0].update(review_status="rejected", review_note="execution, not an incident")
    rows, counts, gone = incidents.build_incidents(articles, previous=rows)
    assert rows == [] and gone[0]["removed_reason"] == "removed on review: execution, not an incident"
