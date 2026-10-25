# Serious-mental-illness incident tracker

A database of news-reported incidents in the United States involving people with an
indication of serious mental illness, and what public systems knew about them beforehand:
hospital discharges, releases from custody, police transports for evaluation, family pleas
for help, court-ordered treatment, competency proceedings.

It sits beside the repeat-offender database in [`../database/`](../database/) and follows
its pipeline: discovery from GDELT and Google News, headline triage, full-text classification
by Claude Haiku with verbatim evidence quotes checked against the article, and
deduplication with a person key built the same way as that database's `offender_key`, so a
person in both is linked. It carries over the 274-incident reference set and the phrase book
from the Known to the System project.

Paths below are relative to `smi/`.

## How it works

1. **Discovery** ([pipeline/fetch.py](pipeline/fetch.py), queries in
   [pipeline/config.py](pipeline/config.py)). Google News RSS gets 52 narrow queries:
   discharge language, family-plea language, crisis-response language, and a balanced grid
   of clinical terms crossed with incident terms (each of the 10 clinical terms paired with 3
   of the 11 incident terms). GDELT gets 8 OR-grouped queries covering the same ground,
   including all 110 grid combinations, and the window is split when a query hits GDELT's
   250-record cap. Both are limited to the last `--days` days.
2. **Headline check** ([pipeline/prefilter.py](pipeline/prefilter.py)). Free and
   deterministic. It drops only what is clearly irrelevant: published before the window, a
   foreign local outlet, or a headline that names itself as opinion, policy, or research. It
   never requires mental-health words in the headline: headlines rarely carry them.
3. **Triage** ([pipeline/triage.py](pipeline/triage.py)). Haiku screens headlines in batches
   of 25 and keeps anything that could be a specific incident.
4. **Fetch and phrase book** ([pipeline/smi_coding.py](pipeline/smi_coding.py)). Google
   redirects are decoded, the article text is fetched (and cached in `data/articles/`), and
   the phrase book codes the full text. An article with no mental-health language anywhere in
   its text stops here, before any model call.
5. **Classification** ([pipeline/classify.py](pipeline/classify.py)). Haiku reads the text
   and the phrase book's matches (as hints) and fills the fields.
6. **Verification**. A row is rejected unless its `mh_evidence_quote` appears verbatim in the
   article, the subject is 18 or over, the state is a US state, and the subject was not killed
   by police. Each system-contact signal needs its own verbatim quote; a signal whose quote is
   not in the article is dropped and the row kept. A `days_since_release` the article does not
   print is blanked. Summaries of unproven acts are attributed to the outlet.
7. **Deduplication** ([pipeline/dedupe.py](pipeline/dedupe.py)). Same incident: the URL joins
   `additional_sources` and the new report fills the row's blanks (name, age, a signal with its
   quote). Same person, new incident: a new row sharing the `subject_key`. Follow-up coverage
   (arrest, competency hearing, plea, a release decision years later) is the same incident.
8. **Link** ([pipeline/link.py](pipeline/link.py)). `repeat_offender_ids` lists the
   repeat-offender stories for the same person. That database is only read.
9. **Review** ([pipeline/review.py](pipeline/review.py)). Nothing reaches
   `data/incidents.csv` without a person accepting it. Every coded article waits in
   `data/review_queue.csv` as `pending`. `python pipeline/run.py review` opens a page on this
   machine showing each article's source link, the mental-health quote, the quote behind every
   signal, and the phrase book's matches. Fields can be corrected, then **Accept** (written to
   `incidents.csv`), **Reject** (kept in the queue for tuning) or **Flag** (a second look).
   Accepting enforces the inclusion rules: a subject role, age 18 or over, a US state, and
   codebook values. A signal the reviewer ticks is recorded as "(set by reviewer)". A new
   report of an incident already accepted is queued with `duplicate_of`; accepting it merges
   the report into that incident. Un-accepting a row removes it from `incidents.csv`.

Every decision is written to a JSONL log in `data/decisions/`.

## Inclusion rules

A record must describe a **specific incident** in the **United States** involving an
identifiable person (the subject) for whom the article gives an indication of mental illness:
a named diagnosis, generic language ("mental health issues", "mentally ill"), behaviour
described in those terms, or a formal system response (psychiatric evaluation, competency
proceedings, involuntary hold, crisis call).

**Excluded:** opinion, editorial, policy, budget and research coverage; stories with no
mental-health indication for the subject (a victim's grief does not count); substance use
alone; dementia or developmental disability as the primary condition; anyone under 18;
subjects **killed by police** (a subject shot or injured by police who survived can qualify);
incidents outside the US; police blotters.

Names are published, as in the repeat-offender database, so its content rules apply: every
record is sourced, allegations are worded as allegations, and copy about named people needs
editorial and legal review before launch.

## Codebook

| Column | Values |
|---|---|
| `subject_role` | `subject_accused`: accused of violence, a threat, property crime or disorder, or the person in crisis police responded to even if no crime was alleged. `subject_victim`: harm was done to the subject (violence, neglect, death by exposure, overdose or suicide, death in custody, missing). When the subject both caused and suffered harm, the role follows whoever is alleged to have started it. |
| `other_party` | Who the other person was: the subject's victim when `subject_accused` ("police" for a crisis response with no one harmed), whoever harmed the subject when `subject_victim`. `stranger`, `acquaintance` (someone the subject knew who is not family or a partner: a neighbor, friend, roommate, coworker, landlord), `police`, `ems`, `family_parent`, `family_child`, `family_other`, `partner` (spouses, girlfriends, boyfriends), `corrections`, `healthcare_worker`, `multiple` (more than one type; two strangers are `stranger`). Blank when there was none or the article does not say. |
| `fatality` | true if anyone involved died, subject or other party. Attempts and threats are not fatalities. |
| `incident_form` | The most serious form; list in `config.INCIDENT_FORMS`. |
| Signals | `prior_hospital_discharge` (also covers any prior psychiatric hospitalization), `prior_custody_release`, `police_brought_for_eval`, `prior_police_contact`, `prior_arrests`, `competency`, `aot`, `crisis_call`, `family_sought_help`, `medication_lapse`, `treatment_refusal`, `homeless`, `diagnosis_named`. True only with a verbatim quote, kept in `signal_evidence`. False means the article did not say, not that it did not happen. |
| `diagnosis_named` / `diagnoses` | Named disorders only (schizophrenia, schizoaffective disorder, bipolar disorder, major depressive disorder, postpartum psychosis, delusional disorder). Generic language qualifies an article but is not a diagnosis; neither are symptoms (psychosis, delusions, hearing voices). |
| `days_since_release` | Days between a hospital or custody release and the incident, only when the article prints the interval. |
| `failure_point`, `responsible_system`, `evidence_tier` | As in Known to the System; `failure not established` and T4 are the defaults. |
| `subject_key` | `last_first_ST`, the same normalization as `offender_key`. |
| `repeat_offender_ids` | Ids in `../database/` for the same person. |
| `coded_by` | `claude` for classified rows, `phrasebook` for `--no-llm` candidates. |
| `review_status`, `reviewed_date`, `duplicate_of` | `pending`, `flagged`, `accepted` or `rejected`; the date of the decision; the accepted incident this report describes, if any. |

Two columns from Known to the System are gone: `prior_hospitalization` (folded into
`prior_hospital_discharge`) and `victim_smi_status`.

## Running locally

```bash
cd smi
pip install -r requirements.txt
python pipeline/run.py harvest --days 14 --no-llm    # free: phrase-book coding only
export ANTHROPIC_API_KEY=...
python pipeline/run.py harvest --days 3              # classified run
python pipeline/run.py review                        # accept, correct, or reject queued rows
python pipeline/run.py link                          # refresh links to ../database/
python -m pytest -q                                  # offline tests, no API key needed
```

Every harvest appends to `data/review_queue.csv`. A classified run records what it read in
`data/seen_urls.json`. A `--no-llm` run queues phrase-book candidates with name, age and
location blank (the classifier or the reviewer fills them), and never marks articles seen,
so a later classified run still reads them; a classified row then replaces a still-pending
phrase-book row for the same article. `--max-fetch N` caps the articles fetched in one run;
the rest wait for the next. `--no-gdelt` skips GDELT.

**GDELT** throttles hard. The repeat-offender database found it refused nearly every request
from GitHub's servers and from a home connection, and a test from this project's network was
refused on its first request. So GDELT gets few grouped calls and gives up quickly; Google
News, with date limits, is the backbone.

## Phrase book changes

[pipeline/smi_coding.py](pipeline/smi_coding.py) is the phrase book that coded the reference
set, with these changes: prior-hospitalization patterns set `prior_hospital_discharge`;
involuntary-hold patterns (Baker Act, 5150) only mark an article as mental-health related,
since they often describe the current incident; `diagnosis_named` needs a named disorder;
fatality ignores attempts and threats; `days_since_release` reads spelled-out numbers and
only in sentences about a release; "criminal history" counts as `prior_arrests`; incident
forms use the codebook's labels; `other_party` is new. The reference set in `reference/` was
coded by the earlier version and has not been recoded.

## Caveats

Entries reflect claims in news reports, which usually rest on police statements or family
accounts; inclusion is not independent verification. Coverage is limited to English-language
US-indexed outlets, so the database understates the true count. A phrase book cannot tell
whose history a sentence describes, which is why every match keeps its sentence and the
classifier treats matches as hints.
