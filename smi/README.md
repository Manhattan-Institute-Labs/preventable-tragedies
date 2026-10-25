# Serious-mental-illness incident tracker

A database of news-reported incidents in the United States involving people with an
indication of serious mental illness, and what public systems knew about them beforehand:
hospital discharges, releases from custody, police transports for evaluation, family pleas
for help, court-ordered treatment, competency proceedings.

It sits beside the repeat-offender database in [`../database/`](../database/) and follows
its pipeline: discovery from GDELT and Google News, headline triage, full-text classification
by Claude Haiku with verbatim evidence quotes checked against the article, and grouping with
a person key built the same way as that database's `offender_key`, so a person in both is
linked. It carries over the 274-incident reference set and the phrase book from the Known to
the System project.

Paths below are relative to `smi/`.

## Two tables, then the criminal-offending restrictions

- **`data/articles.csv`**: one row per article. Every harvested article lands here as
  `pending`. A reviewer's **accept means only that it is the right general type of story**;
  nothing needs filling in. Several articles about one incident are all kept.
- **`data/incidents.csv`**: one row per incident, built from accepted articles once Claude has
  coded them. Each incident lists its articles (`article_ids`, `source_url`,
  `additional_sources`) and takes its fields from all of them.
- The **preventable-tragedies criminal-offending restrictions** are applied to the incidents
  after that, not here. This tracker keeps incidents those rules may later exclude (people
  killed by police, incidents with no arrest), with the fields they need (`killed_by_police`,
  `outcome`, `subject_role`, `age`, `state`).

## How it works

1. **Discovery** ([pipeline/fetch.py](pipeline/fetch.py), queries in
   [pipeline/config.py](pipeline/config.py)). Google News RSS gets 52 narrow queries:
   discharge language, family-plea language, crisis-response language, and a balanced grid
   of clinical terms crossed with incident terms (each of the 10 clinical terms paired with 3
   of the 11 incident terms). GDELT gets 12 OR-grouped queries covering the same ground,
   including all 110 grid combinations, and the window is split when a query hits GDELT's
   250-record cap. Both are limited to the window being harvested.
2. **Headline and domain check** ([pipeline/prefilter.py](pipeline/prefilter.py)). Free and
   deterministic. It drops articles published outside the window, **links on non-US domains**
   (`.uk`, `.au`, `.ca` and other country codes, checked again after a Google News link is
   decoded to its real address), a short list of foreign outlets Google News reports by name,
   and headlines that name themselves as opinion, policy, research, or a police log. It never
   requires mental-health words in the headline: headlines rarely carry them.
3. **Triage** ([pipeline/triage.py](pipeline/triage.py)), with an API key only. Haiku screens
   headlines in batches of 25 and keeps anything that could be a specific incident.
4. **Fetch and phrase book** ([pipeline/smi_coding.py](pipeline/smi_coding.py)). Google
   redirects are decoded, the article text is fetched (and cached in `data/articles/`), and
   the phrase book codes the full text. An article with no mental-health language or no event
   stops here. The rest are added to `articles.csv` as `pending`.
5. **Review** ([pipeline/review.py](pipeline/review.py)). `python pipeline/run.py review`
   opens a page on this machine showing each article's source link, the mental-health quote,
   the quote behind every signal, and the phrase book's matches. **Accept** (right general
   type), **Reject** (kept for tuning, never coded) or **Flag**. Fields can be corrected, but
   none is required.
6. **Classification** ([pipeline/classify.py](pipeline/classify.py)), with an API key. Claude
   re-reads every phrase-book article not rejected in review and replaces the phrase-book
   coding with its own. Verification: the `mh_evidence_quote` must appear verbatim in the
   article; each signal needs its own verbatim quote (an unverifiable one is dropped); a
   `days_since_release` the article does not print is blanked; summaries of unproven acts are
   attributed to the outlet. An article that is outside the US, about someone under 18, or not
   a specific incident keeps its review status and gets `excluded_reason`, which keeps it out
   of incidents. A subject killed by police is recorded (`killed_by_police`), not excluded.
7. **Incidents** ([pipeline/incidents.py](pipeline/incidents.py)). Accepted, Claude-coded,
   non-excluded articles are grouped: the same person with nearby dates and the same city or
   form is one incident by rule, and Haiku decides unclear cases. Follow-up coverage (arrest,
   competency hearing, plea, a release decision years later) is the same incident. An incident
   takes each field from its articles without overwriting, and a signal any article supports
   with a quote is true. Articles keep their `incident_id`, so rebuilding keeps earlier groups.
8. **Link** ([pipeline/link.py](pipeline/link.py)). `repeat_offender_ids` lists the
   repeat-offender stories for the same person. That database is only read.

Every decision is written to a JSONL log in `data/decisions/`. Every window harvested is
recorded in `data/harvest_windows.json`.

## Inclusion rules

An article belongs if it describes a **specific incident** in the **United States** involving an
identifiable person (the subject) for whom it gives an indication of mental illness: a named
diagnosis, generic language ("mental health issues", "mentally ill"), behaviour described in
those terms, or a formal system response (psychiatric evaluation, competency proceedings,
involuntary hold, crisis call). All roles count: accused, in crisis, victim, died, killed by
police.

**Excluded:** links on non-US domains; opinion, editorial, policy, budget and research
coverage; police logs; stories with no mental-health indication for the subject (a victim's
grief does not count); substance use alone; dementia or developmental disability as the
primary condition; anyone under 18; incidents outside the US.

Names are published, as in the repeat-offender database, so its content rules apply: every
record is sourced, allegations are worded as allegations, and copy about named people needs
editorial and legal review before launch.

## Codebook

| Column | Values |
|---|---|
| `subject_role` | `subject_accused`: accused of violence, a threat, property crime or disorder, or the person in crisis police responded to even if no crime was alleged. `subject_victim`: harm was done to the subject (violence, neglect, death by exposure, overdose or suicide, death in custody, missing). When the subject both caused and suffered harm, the role follows whoever is alleged to have started it. |
| `other_party` | Who the other person was: the subject's victim when `subject_accused` ("police" for a crisis response with no one harmed), whoever harmed the subject when `subject_victim`. `stranger`, `acquaintance` (someone the subject knew who is not family or a partner: a neighbor, friend, roommate, coworker, landlord), `police`, `ems`, `family_parent`, `family_child`, `family_other`, `partner` (spouses, girlfriends, boyfriends), `corrections`, `healthcare_worker`, `multiple` (more than one type; two strangers are `stranger`). Blank when there was none or the article does not say. |
| `fatality` | true if anyone involved died, subject or other party. Attempts and threats are not fatalities. |
| `killed_by_police` | true if the subject was killed by police or other law enforcement. Kept for the criminal-offending restrictions to decide. |
| `incident_form` | The most serious form; list in `config.INCIDENT_FORMS`. |
| Signals | `prior_hospital_discharge` (also covers any prior psychiatric hospitalization), `prior_custody_release`, `police_brought_for_eval`, `prior_police_contact`, `prior_arrests`, `competency`, `aot`, `crisis_call`, `family_sought_help`, `medication_lapse`, `treatment_refusal`, `homeless`, `diagnosis_named`. True only with a verbatim quote, kept in `signal_evidence`. False means the article did not say, not that it did not happen. |
| `diagnosis_named` / `diagnoses` | Named disorders only (schizophrenia, schizoaffective disorder, bipolar disorder, major depressive disorder, postpartum psychosis, delusional disorder). Generic language qualifies an article but is not a diagnosis; neither are symptoms (psychosis, delusions, hearing voices). |
| `days_since_release` | Days between a hospital or custody release and the incident, only when the article prints the interval. |
| `failure_point`, `responsible_system`, `evidence_tier` | As in Known to the System; `failure not established` and T4 are the defaults. |
| `subject_key` | `last_first_ST`, the same normalization as `offender_key`. |
| `repeat_offender_ids` | Ids in `../database/` for the same person. |
| Articles only | `published`, `headline`; `coded_by` (`phrasebook` or `claude`); `review_status` (`pending`, `flagged`, `accepted`, `rejected`) and `reviewed_date`; `excluded_reason`; `incident_id`. |
| Incidents only | `article_ids`, `article_count`. |

Two columns from Known to the System are gone: `prior_hospitalization` (folded into
`prior_hospital_discharge`) and `victim_smi_status`.

## Running locally

```bash
cd smi
pip install -r requirements.txt
python pipeline/run.py harvest --days 14              # the newest 14 days
python pipeline/run.py harvest --days 14 --next-back  # the 14 days before the earliest window run
python pipeline/run.py harvest --days 14 --end 2026-09-01
python pipeline/run.py review                         # accept articles of the right general type
export ANTHROPIC_API_KEY=...
python pipeline/run.py classify                       # Claude codes phrase-book articles
python pipeline/run.py incidents                      # build incidents.csv
python pipeline/run.py link                           # refresh links to ../database/
python -m pytest -q                                   # offline tests, no API key needed
```

Without `ANTHROPIC_API_KEY` a harvest is free: phrase-book coding only, no triage. With it, a
harvest also triages headlines and then codes every phrase-book article not rejected
(`--no-llm` forces the free mode). `--max-fetch N` caps the articles fetched in one run; the
rest wait for the next. `--no-gdelt` skips GDELT. `incidents` without a key groups by rule
only.

**GDELT** throttles hard. The repeat-offender database found it refused nearly every request
from GitHub's servers and from a home connection, and this project's network is refused on
most requests too. So GDELT gets few grouped calls and gives up quickly; Google News, with
date limits, is the backbone.

## Phrase book changes

[pipeline/smi_coding.py](pipeline/smi_coding.py) is the phrase book that coded the reference
set, with these changes: prior-hospitalization patterns set `prior_hospital_discharge`;
involuntary-hold patterns (Baker Act, 5150) only mark an article as mental-health related,
since they often describe the current incident; `diagnosis_named` needs a named disorder;
fatality ignores attempts and threats; `days_since_release` reads spelled-out numbers and
only in sentences about a release; "criminal history" counts as `prior_arrests`; incident
forms use the codebook's labels; `other_party` and `killed_by_police` are new. The reference
set in `reference/` was coded by the earlier version and has not been recoded.

## Caveats

Entries reflect claims in news reports, which usually rest on police statements or family
accounts; inclusion is not independent verification. Coverage is limited to English-language
US outlets indexed by GDELT and Google News, so the database understates the true count. A
phrase book cannot tell whose history a sentence describes, which is why every match keeps
its sentence and Claude treats matches as hints.
