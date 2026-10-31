# Serious-mental-illness incident tracker

A database of news-reported incidents in the United States involving people with an
indication of serious mental illness, and what public systems knew about them beforehand:
hospital discharges, releases from custody, police transports for evaluation, family pleas
for help, court-ordered treatment, earlier "emotionally disturbed person" calls. Its incidents
that meet the inclusion rule are published on the site as the **Serious mental illness**
category, beside the repeat-offender database's frequent violent and very frequent petty
offenders.

It is one of two harvesters for a single record set: the repeat-offender pipeline in
[`../database/`](../database/) is the other. This folder finds, codes and groups incidents;
validation, quarantine, the exports and the people list are the record set's
([`../database/pipeline/records.py`](../database/pipeline/records.py)), shared with that
pipeline. The person key and publication rule come from
[`../database/pipeline/shared.py`](../database/pipeline/shared.py), loaded, not copied.

It sits beside the repeat-offender database in [`../database/`](../database/) and follows
its pipeline: discovery from Google News (and GDELT), headline triage, full-text classification
by Claude Haiku with verbatim evidence quotes checked against the article, grouping, a
second-source search, and a person key built the same way as that database's `offender_key`,
so a person in both is linked. It runs in the same daily GitHub Actions job as that database.
It carries over the 274-incident reference set and the phrase book from the Known to the
System project.

Paths below are relative to `smi/`.

## Tables

- **`data/articles.csv`**: one row per article. Claude codes every harvested article.
  Review is a spot-check, not a gate: a reviewer's **Reject** keeps an article out of
  incidents; nothing else needs a person.
- **`data/incidents.csv`**: one row per incident, built from the coded articles, with the
  inclusion rule applied (`qualifies_smi`, and `qualify_note` saying why). Every incident is
  kept, qualifying or not, so the counts (including incidents where the subject was the one
  harmed) are all here. The site shows only the qualifying ones with two outlets (or a
  trusted one) and no `site_excluded` reason.
- **`data/subjects.csv`**: one row per person, like the repeat-offender database's
  `offenders.csv`.
- **`data/removed_incidents.csv`**: incidents that lost all their articles, with the date and
  reason. Their ids are never reused. Nothing is deleted: rejected and excluded articles stay
  in `articles.csv`.

## How it works

1. **Discovery** ([pipeline/fetch.py](pipeline/fetch.py), queries in
   [pipeline/config.py](pipeline/config.py)). Google News RSS gets 52 narrow queries:
   discharge language, family-plea language, crisis-response language, and a balanced grid
   of clinical terms crossed with incident terms. GDELT gets 12 OR-grouped queries covering
   the same ground; it is off in the daily job, because it refuses GitHub's servers.
2. **Headline and domain check** ([pipeline/prefilter.py](pipeline/prefilter.py)). Free and
   deterministic. It drops articles published outside the window, **links on non-US domains**
   (`.uk`, `.au`, `.ca` and other country codes, checked again after a Google News link is
   decoded), a short list of foreign outlets, and headlines that name themselves as opinion,
   policy, research, or a police log.
3. **Triage** ([pipeline/triage.py](pipeline/triage.py)). Haiku screens headlines in batches
   of 25 and keeps anything that could be a specific incident.
4. **Fetch and phrase book** ([pipeline/smi_coding.py](pipeline/smi_coding.py)). Google
   redirects are decoded, the article text is fetched (and cached in `data/articles/`), and
   the phrase book codes the full text. An article with no mental-health language or no event
   stops here.
5. **Cross-listing** ([pipeline/crosslist.py](pipeline/crosslist.py)). Every repeat-offender
   story is read once: its articles go through the phrase book, and one with mental-health
   language is added like a harvested article, then held to the same rules. Free.
6. **Classification** ([pipeline/classify.py](pipeline/classify.py)). Claude re-reads every
   phrase-book article not rejected in review and codes it. Verification: the
   `mh_evidence_quote` must appear verbatim in the article; each signal needs its own verbatim
   quote (an unverifiable one is dropped); the prior-record fields use the repeat-offender
   database's definitions and count checks (a count the article never prints, or a prior
   record or release quote not in the article, is dropped); a `days_since_release` the article
   does not print is blanked; summaries of unproven acts are attributed to the outlet;
   diagnoses are cleaned to one spelling each, named disorders only
   ([pipeline/diagnoses.py](pipeline/diagnoses.py)). An article about an incident outside the
   US, about someone under 18, not about a specific incident, or court-only gets
   `excluded_reason`, which keeps it out of incidents.
7. **Incidents** ([pipeline/incidents.py](pipeline/incidents.py)). Coded articles are grouped:
   the same person with nearby dates and the same city or form is one incident by rule, and
   Haiku decides unclear cases. Follow-up coverage of the criminal case (arrest, competency
   hearing, trial, plea, sentencing) is the same incident. Then, in publication order, each article's
   **contribution**:
   - `primary`: the first report.
   - `corroboration`: the first report from a second outlet, always kept, because the
     publication rule needs two outlets (unless the first is on the trusted list).
   - `context`: a later report that adds something the incident lacks: a system-contact
     signal, a diagnosis, a prior count (or a higher one), prior offenses, a release status,
     days since release, a death, violence, a police killing, or a missing name, age, sex or
     place. `added_info` says what.
   - `redundant`: a later report that adds nothing. It is not cited or merged
     (`redundant_article_ids`).

   The incident takes each field from its kept articles without overwriting (a prior count
   takes the higher stated number), a signal any of them supports with a quote is true, and
   the inclusion rule ([pipeline/rules.py](pipeline/rules.py)) is applied.
8. **Second-source search** ([pipeline/corroborate.py](pipeline/corroborate.py)). For each
   qualifying incident with one outlet, Google News is searched for the quoted name and place
   around the incident date, and up to four other outlets are read by Claude with the incident
   as context. A report of the same incident is added, needs no mental-health language of its
   own, and goes through the contribution rule like any other.
9. **Link** ([pipeline/link.py](pipeline/link.py)). `repeat_offender_ids` lists the
   repeat-offender stories for the same person.
10. **Categories and the site** (in `../database/`). `categorize.py` gives incidents that also
    meet the repeat-offender rules a violent or petty category. `build_exports.py` publishes
    qualifying incidents that meet the publication rule: one that is the same incident as a
    repeat-offender story adds "Serious mental illness" to that story's card; the rest get
    cards of their own (ids from 2,000,000).

Every decision is written to a JSONL log in `data/decisions/` (uploaded with each workflow run).
Every window harvested is recorded in `data/harvest_windows.json`.

## Inclusion rule

An incident qualifies (`qualifies_smi`) when all of these hold:

- **United States**: a US state is given.
- **18 or older** (or the age is not stated).
- **An earlier system contact, quoted**: a prior psychiatric hospitalization, a release from
  custody, police taking the subject for evaluation, prior police contact (with or without an
  arrest), a prior arrest or record, court-ordered treatment, an "emotionally disturbed person"
  call, family seeking help, psychiatric medication, refused treatment, a stated history of
  mental illness, or a named diagnosis. A prior record or release status in the
  repeat-offender fields counts too. Not on their own: a competency evaluation (usually the
  current case), another crisis call (usually the one that started the incident), or
  homelessness.
- **Harm**: violence or a death, whoever caused or suffered it; otherwise an arrest, charge or
  conviction for anything, worded as the repeat-offender database's outcome rule.

The subject may have caused the harm or suffered it (`subject_role`); both count. The site
does not label which.

Serious mental illness is a **category of its own**. A qualifying incident is shown only as
an SMI case, never also as a violent or petty offender, whatever the subject's record; when
the repeat-offender database has the same incident, the SMI record is the one shown
([`../database/pipeline/records.py`](../database/pipeline/records.py)). `qualifies_strict`
still records whether the stated counts meet that database's threshold.

**Publication** (as in the repeat-offender database): two or more distinct outlets, or a first
source on that database's trusted list. Qualifying incidents with one outlet stay in the data
and off the site.

Articles are excluded from incidents for: links on non-US domains; opinion, editorial, policy,
budget and research coverage; police logs; no mental-health indication for the subject (a
victim's grief does not count; a confirmed second source is exempt); substance use alone;
dementia or developmental disability as the primary condition; anyone under 18; incidents
that happened outside the US, wherever the subject lived (a US resident who dies abroad after
deportation is out); **court-only stories**, whose news is a later proceeding on an earlier,
decided case (an execution, a clemency or pardon decision, an appeal, a parole or release
hearing, a civil lawsuit or settlement). Coverage of an incident's criminal case from arrest
through sentencing is about the incident and can qualify.

To remove an incident by hand, reject its articles in review with a note; the next incidents
build moves it to `removed_incidents.csv` with "removed on review: " and the note. To keep an
incident in `incidents.csv` but off the site, put the reason in its `site_excluded` column:
every rebuild keeps it, the exports skip the incident (a permalink to it shows "kept off the
site: " and the reason), and the second-source search and categorizing skip it.

When a story reports a new event and recounts an earlier crime as background, the incident is
the new event and the earlier crime is prior history. An article coded the other way can be
corrected in review and its `incident_id` set to the right incident; the next build merges it
there.

Names are published, as in the repeat-offender database, so its content rules apply: every
record is sourced, allegations are worded as allegations, and copy about named people needs
editorial and legal review before launch.

## Codebook

| Column | Values |
|---|---|
| `subject_role` | `subject_accused`: accused of violence, a threat, property crime or disorder, or the person in crisis police responded to even if no crime was alleged. `subject_victim`: harm was done to the subject (violence, neglect, death by exposure, overdose or suicide, death in custody, missing). When the subject both caused and suffered harm, the role follows whoever is alleged to have started it. Not exported to the site. |
| `other_party` | Who the other person was: the subject's victim when `subject_accused` ("police" for a crisis response with no one harmed), whoever harmed the subject when `subject_victim`. `stranger`, `acquaintance`, `police`, `ems`, `family_parent`, `family_child`, `family_other`, `partner`, `corrections`, `healthcare_worker`, `multiple`. Blank when there was none or the article does not say. |
| `fatality` | true if anyone involved died, subject or other party. Attempts and threats are not fatalities. |
| `killed_by_police` | true if the subject was killed by police or other law enforcement. |
| `incident_form` | The most serious form; list in `config.INCIDENT_FORMS`. |
| `new_offense_type`, `new_offense_severity` | The offense the subject was arrested for or accused of, in the repeat-offender vocabulary. Blank when none. |
| Signals | `prior_hospital_discharge` (any prior psychiatric hospitalization), `prior_custody_release`, `police_brought_for_eval`, `prior_police_contact`, `prior_arrests`, `competency`, `aot`, `edp_call` (an "emotionally disturbed person" call), `crisis_call` (any other crisis call, welfare check or standoff), `family_sought_help`, `medication_lapse`, `psych_medication` (any psychiatric medication), `treatment_refusal`, `homeless`, `mh_history` (a stated history of mental illness before the incident), `diagnosis_named`. True only with a verbatim quote, kept in `signal_evidence`. False means the article did not say, not that it did not happen. |
| `diagnosis_named` / `diagnoses` | Named mental disorders only (schizophrenia, schizoaffective disorder, bipolar disorder, major depressive disorder, postpartum psychosis, delusional disorder; post-traumatic stress disorder and postpartum depression are kept for now). Generic language ("mental illness", "depression") qualifies an article but is not a diagnosis; neither are symptoms (psychosis, mania, delusions, hearing voices) or physical conditions. One spelling per disorder ("PTSD" is stored as post-traumatic stress disorder, "paranoid schizophrenia" as schizophrenia); the list is in [pipeline/diagnoses.py](pipeline/diagnoses.py), and every incidents build applies it to rows already coded. When every label an article listed is dropped, `diagnosis_named` is set false. |
| `days_since_release` | Days between a hospital or custody release and the incident, only when the article prints the interval. |
| Prior record | `prior_count_arrests`, `prior_count_convictions`, `prior_count_felony_convictions` (only numbers the article prints), `prior_offenses`, `prior_evidence_quote`, `release_status` (at the time of the incident, from an earlier case), `release_evidence_quote`, `releasing_jurisdiction`: the repeat-offender database's fields and definitions. A verified record also sets `prior_arrests`; a verified release status sets `prior_custody_release`. |
| `failure_point`, `responsible_system`, `evidence_tier` | As in Known to the System; `failure not established` and T4 are the defaults. |
| `subject_key` | `last_first_ST`, the same normalization as `offender_key`. |
| `repeat_offender_ids` | Ids in `../database/` for the same person. |
| Articles only | `published`, `headline`; `coded_by` (`phrasebook` or `claude`); `review_status` (`pending` = not looked at, `flagged`, `accepted`, `rejected`) and `reviewed_date`; `excluded_reason`; `incident_id`; `contribution` (`primary`, `corroboration`, `context`, `redundant`); `added_info`. |
| Incidents only | `article_ids`, `article_count`, `redundant_article_ids`; `qualifies_smi`, `qualify_note`; `qualifies_strict` (the repeat-offender count threshold); `corroboration_checked`; `site_excluded` (why the incident is kept off the site: set by hand, or "quarantined DATE: reason" by `../database/pipeline/quarantine.py`). |

## Running

In GitHub Actions, with the repository's `ANTHROPIC_API_KEY` secret:

- **Daily update** (`.github/workflows/daily.yml`, 6:30am ET): after the repeat-offender
  pipeline, `python pipeline/run.py daily` harvests the last day, cross-lists new
  repeat-offender stories, codes, groups, and searches for second sources. The record set is
  then validated as a whole (`../database/pipeline/check_data.sh`): a failing SMI record is
  quarantined (its `site_excluded` set), and if this folder's files are broken (the
  `run.py validate` checks below), its changes that day are discarded, not committed.
- **SMI backfill (manual)** (`.github/workflows/smi-backfill.yml`): harvests further 14-day
  windows backwards, codes everything still uncoded, groups, searches for second sources,
  validates the record set and rebuilds the exports.

Locally:

```bash
cd smi
pip install -r requirements.txt
python pipeline/run.py harvest --days 14 --next-back  # free without a key: phrase book only
python pipeline/run.py crosslist                      # free
python pipeline/run.py incidents                      # without a key, groups by rule only
python pipeline/run.py review                         # spot-check articles
python pipeline/run.py validate                       # this folder's files only
python ../database/pipeline/validate_data.py          # the whole record set (from ../database)
export ANTHROPIC_API_KEY=...
python pipeline/run.py daily                          # what the daily job runs
python pipeline/run.py backfill --windows 4           # what the backfill workflow runs
python pipeline/run.py classify                       # single steps
python pipeline/run.py corroborate
python -m pytest -q                                   # offline tests, no API key needed
```

`harvest` with a key also triages headlines and codes the new articles (`--no-llm` forces the
free mode). `--max-fetch N` caps the articles fetched in one run; the rest wait for the next.
The review page holds the articles in memory: stop it before any other command.

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
forms use the codebook's labels; `other_party` and `killed_by_police` are new; "emotionally
disturbed person" calls are `edp_call`, out of `crisis_call`; `mh_history` and
`psych_medication` are new. The reference set in `reference/` was coded by the earlier version
and has not been recoded.

## Caveats

Entries reflect claims in news reports, which usually rest on police statements or family
accounts; inclusion is not independent verification. Coverage is limited to English-language
US outlets indexed by Google News (and GDELT), so the database understates the true count. A
phrase book cannot tell whose history a sentence describes, which is why every match keeps
its sentence and Claude treats matches as hints.
