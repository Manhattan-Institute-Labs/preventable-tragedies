# Frequent-offender database

The Preventable Tragedies case database, carried over from the Underincarcerated repo on
2026-10-22 (data as of its commit `c08aff9`). That repo is now a private archive: its commit
history is the day-by-day audit log up to the move. Ask the maintainer for access.

A daily-updated database of news stories about repeat offenders: people arrested, charged,
or convicted for a new crime in the United States who had a documented prior record, or who
were free at the time because of a release decision (bail, bond, probation, parole, pretrial
release, early release, dropped charges).

The backend follows [flock-crime-tracker](https://github.com/CharlesFainLehman/flock-crime-tracker).
The front end is the site in [`../site/`](../site/), whose `build.py` reads `exports/stories.json`.

**One record set, two harvesters.** This pipeline finds repeat offenders; the
serious-mental-illness tracker in [`../smi/`](../smi/) finds people with serious mental illness.
Both feed one record set ([pipeline/records.py](pipeline/records.py)), and everything after the
harvest works on that set, not on either harvester's files: one validator
(`pipeline/validate_data.py`, run through `pipeline/check_data.sh` by every data workflow), one
quarantine, one set of exports (`exports/stories.json`, `offenders.json`, `stats.json`), one
people list, and one category per record (see Offender categories). The person key and the
publication rule are in [pipeline/shared.py](pipeline/shared.py), which the SMI tracker loads
rather than copying.

Everything here lives in `database/`, and paths below are relative to it. The GitHub Actions
workflows are in the repo-root `.github/workflows/` and run with `database/` as their working
directory.

## How it works

1. **Discovery.** Each day, candidate articles are pulled from the
   [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) with six
   OR-grouped queries (splitting the window when one hits GDELT's 250-record cap) and from
   Google News RSS with ~30 narrow queries such as `"repeat offender" arrested` and
   `"out on bond" arrested` ([pipeline/fetch.py](pipeline/fetch.py),
   [pipeline/config.py](pipeline/config.py)). Every Google News query carries `after:`/`before:`
   date limits for the last two days (`DAYS_BACK`), so each day is searched by two runs.
   Until late November 2026 the daily queries had no date limit, and Google returned up to 100
   matches from the past several months in no date order: most of each day's new stories
   never appeared, and ~95% of what came back had already been seen.
2. **Triage.** Headlines are screened in batches of 25 by Claude Haiku before any article is
   fetched or any Google News redirect is decoded. Policy pieces, blotters, and non-US stories
   are dropped here; anything plausible is kept ([pipeline/triage.py](pipeline/triage.py)).
3. **Classification.** Each surviving article's text is extracted and classified by Haiku as
   qualifying or not, with structured fields: offender, new offense, prior counts, release
   status, and two verbatim evidence quotes ([pipeline/classify.py](pipeline/classify.py)).
4. **Verification.** A row is rejected unless both evidence quotes appear verbatim in the
   fetched article text. This is the guard against the model inventing or inflating a record.
5. **Deduplication.** New rows are compared against rows for the same person and rows in the
   same state within 21 days. Same incident: the URL is attached to the existing row. Same
   person, new incident: a new row sharing the offender key ([pipeline/dedupe.py](pipeline/dedupe.py)).
6. **Publishing.** The database is [data/stories.csv](data/stories.csv). `data/offenders.csv`
   and everything in `exports/` are derived from it (plus `data/backfill/stories.csv`) by
   [pipeline/build_exports.py](pipeline/build_exports.py).

An article whose text can't be fetched (paywalls, and sites that block automated readers,
about a quarter of those triage keeps) is marked seen and dropped; only its URL is logged.

The daily job is [.github/workflows/daily.yml](../.github/workflows/daily.yml), at 10:30 UTC
(6:30am Eastern in summer, 5:30am in winter), always from `main`. In order: the steps above
(`run_daily.py`), a mug-shot search on new rows (`mugshots.py`), the second-source search
(`corroborate.py`), removal of rows still single-sourced (`enforce_sources.py`), the SMI
tracker (`../smi/pipeline/run.py daily`), categorizing (`categorize.py`), then validation,
`build_exports.py` and one commit, "Daily update YYYY-MM-DD", by `preventable-tragedies-bot`.
The commit history is an audit log of what changed each day, and `data/daily_adds.csv` counts
the rows each run added. A run marked "succeeded" means no step crashed, not that anything
was found: check the run log's "Google News: N items" line.

Every triage, fetch, classify, verify, strict-threshold, dedupe, and categorize decision is
written to a JSONL log, uploaded as a workflow artifact (kept 30 days) and committed gzipped to
`data/decisions/` by `pack_decisions.py`, which deletes daily logs older than 30 days (they
stay in git history). Backfill logs (`backfill-*`) are kept.

## Inclusion criteria

A record must describe a **specific, identifiable person** arrested, charged, or convicted for
a **new offense in the United States**, and the article must state at least one of:

1. one or more prior convictions or arrests, with some specificity (a count, a named prior
   offense, a prior sentence, or a prior prison term);
2. that the person was on pretrial release, bail, bond, probation, parole, supervised release,
   or in a diversion program at the time of the new offense;
3. that the person had been released early, had charges dropped or reduced, or had been denied
   detention in a prior case that the article connects to the new offense.

Only **strict** stories are stored (since 2026-09-08): 5+ prior arrests, or 5+ prior
convictions, or 3+ prior felony convictions, as stated in the article. Counts are never
estimated: "more than a dozen" is 13, "numerous" is blank. Stories that meet the conditions
above but not the strict threshold are classified, verified and dropped (`STRICT_ONLY` in
`config.py`): the full record survives only in the decision log (the `strict` stage), so for
30 days in `data/decisions/`, and its URL is marked seen. About four in five stories that
meet the conditions fall below the threshold. The `qualifies_strict` column is therefore
always `yes`; it is kept so older exports and tools keep working.

Not stored: anyone under 18, and anyone the article does not report as arrested, charged,
indicted, convicted, or sentenced. Every stated prior count must appear in the article text.
Summaries of unproven allegations are attributed to the outlet. Rows removed after
publication go to `data/removed.csv` with a reason (`python pipeline/remove_story.py --id N
--reason "..."`); their ids are never reused and `exports/removed.json` keeps a tombstone. The corrections
policy is `content/corrections.md`.

A second-source search (`corroborate.py`) runs on new rows at the end of each daily run and
on demand for the whole database: it queries Google News for the offender's name and place
around the incident date, and adds another outlet's report to `additional_sources` when the
model confirms it covers the same person and incident. `corroboration_checked` records the
date of the last search. Rows whose primary source is on `TRUSTED_OUTLETS` in `config.py`
(national outlets and metro dailies with professional newsrooms) skip the search.

**Publication rule** (`REQUIRE_CORROBORATION`, 2026-09-08): a story is published only when two
or more distinct outlets report it, or its primary source is on `TRUSTED_OUTLETS`. New rows
stay in the data files but out of `exports/` until the second-source search has run on them;
`enforce_sources.py` then moves rows the search left single-sourced to `data/removed.csv`.

The database is a collection of stories, not a census. It holds what the news covered, what
the searches found, and what the article stated. Daily runs are capped at 150 articles
(`max_classify`), ranked by headline signals (a stated count first, then a label such as
"career criminal", then a release phrase). Articles over the cap are not marked seen, so the
next run picks them up while they are still inside its two-day window; after that, a busy
news day is sampled, not exhausted.

**Excluded by rule:**
- policy, statistics, editorial, and opinion pieces with no specific new incident
- "repeat offender" or "known to police" used as a label with no concrete prior
- priors that are only traffic infractions, or a juvenile record with no detail
- re-arrest on the same case (failure to appear, bond violation, retrial)
- crimes outside the United States
- police blotters, arrest logs, mugshot aggregators, and wire/press-release sources
- wrongful-arrest and exoneration stories

## Schema

One row per new-offense incident in `data/stories.csv`:

| Column | Notes |
|---|---|
| `id`, `date_added` | |
| `incident_date` | new offense; YYYY-MM-DD, YYYY-MM, or YYYY |
| `city`, `state` | state always set when known |
| `offender_name`, `offender_key` | name as printed; key is `last_first_ST`, links rows for one person |
| `age` | |
| `new_offense_type`, `new_offense_severity` | controlled vocabularies in `config.py` |
| `prior_count_arrests`, `prior_count_convictions`, `prior_count_felony_convictions` | only when the article states a number |
| `prior_offenses` | named priors, semicolon-separated |
| `prior_evidence_quote` | verbatim sentence from the article; verified |
| `release_status` | controlled vocabulary; `none stated` if the article is silent |
| `release_evidence_quote` | verbatim; verified; required when status is not `none stated` |
| `releasing_jurisdiction` | |
| `outcome`, `summary` | |
| `qualifies_strict` | yes/no, from the counts |
| `mugshot_url`, `mugshot_checked` | a booking photo found on the article page by `mugshots.py`, hot-linked, never copied; the date it last looked. The site keeps `photo` null until usage rights are confirmed |
| `source_name`, `source_url`, `additional_sources`, `confidence` | |
| `corroboration_checked` | date of the last second-source search |
| `offender_category`, `category_reason` | `violent`, `petty`, or `unclassified`, from the prior record (see below); blank until categorized |

`data/offenders.csv` has one row per `offender_key` across the whole record set (a
serious-mental-illness subject the incident harmed, rather than accused, is not listed): name,
state, record ids, incident count, first and last incident date, latest prior counts, strict
flag. Never edit it by hand.

## Offender categories

The project tracks frequent violent offenders, very frequent petty offenders, and seriously
mentally ill offenders. The first two are set by [pipeline/categorize.py](pipeline/categorize.py)
from the **prior record as the article states it**. The new offense is separate
(`new_offense_type`), so a petty offender who commits a homicide is `petty` with a violent new
offense.

| `offender_category` | Rule |
|---|---|
| `violent` | two or more violent priors (arrests, charges, or convictions) |
| `petty` | fewer than two violent priors, and the record is described well enough to see it is mostly low-level |
| `unclassified` | the article gives counts or labels but not what the priors were, or it's ambiguous |

Violent: homicide, assault and battery, robbery and carjacking, sexual offenses, kidnapping,
shootings and stabbings, child abuse, threatening with a weapon. Not violent on their own:
weapons possession, drug offenses, theft, burglary, trespassing, fraud, DUI, resisting arrest,
probation or parole violations. The model (`CATEGORIZE_MODEL`) re-reads the article and must
quote the sentence it relied on; a `violent` or `petty` answer whose quote isn't in the article
(or the stored `prior_evidence_quote`), or a `violent` answer with fewer than two violent priors,
is stored as `unclassified`. Every decision, with its quote, goes to the decision log. New rows
are categorized in the daily job; `Categorize (manual)` sweeps the database (`recheck` redoes
every row, e.g. after a rule change).

**Serious mental illness** is the third category, from the tracker in [`../smi/`](../smi/),
which runs in the same daily job with its own inclusion rule (see its README). Categories are
discrete: every record in `exports/` has one `category`. A story's is its `offender_category`
(or `pending`); a qualifying SMI incident's is `smi`, whatever the subject's record, and
`categorize.py` never touches SMI incidents. An SMI record's id is `SMI_ID_BASE` (2,000,000)
plus its number. When both harvesters have the same incident (a shared source article, or the
same person key within 21 days) and the SMI record is publishable, only the SMI record is
shown: the story stays in `data/stories.csv`, and `exports/removed.json` points its id to the
SMI record (`"see"`). Both are held to the same publication rule and the same validation.

## Caveats

- Entries reflect claims made in news reports, which typically rely on police and prosecutor
  statements. Inclusion is not independent verification of the record.
- Coverage is limited to English-language US outlets indexed by Google News (GDELT is off in
  the daily job).
- Classification is automated and nothing waits for a person. Anyone can open an issue with
  the forms in `.github/ISSUE_TEMPLATE/` ("Flag an error in a record", "Submit a missing
  story"); the new site does not link to them yet. Each new feedback issue gets an automated
  first review (`review_feedback.py`, run by `feedback.yml`) and a `triage:*` label, and the
  maintainer makes the final call. The review looks records up in this database's CSVs only,
  so it does not match SMI-only records (ids from 2,000,000). Corrections are made by
  editing `data/stories.csv` or `data/backfill/stories.csv` directly, or with
  `remove_story.py`; `validate_data.py` runs before every commit.

## Scripts and workflows

Everything in `pipeline/` is run by a workflow or imported by a script that is, except
`remove_story.py`, which is the hand tool for corrections.

| Script | Run by | Does |
|---|---|---|
| `run_daily.py` | Daily update | discovery, triage, classification, verification, dedupe, save (uses `fetch`, `triage`, `classify`, `process`, `dedupe`, `store`, `daily_adds`) |
| `mugshots.py` | Daily update, Backfill, Mug shot sweep | looks for a booking photo on each new row's article page |
| `corroborate.py` | Daily update, Second-source search | searches for a second outlet |
| `enforce_sources.py` | Daily update, Second-source search | moves rows still single-sourced to `removed.csv` |
| `categorize.py` | Daily update, Categorize, Backfill, SMI backfill | violent / petty / unclassified |
| `validate_data.py`, `quarantine.py` | the data workflows, before they commit | checks the data; a failing row is quarantined instead of failing the run |
| `build_exports.py` | every workflow that commits data, Rebuild exports | `offenders.csv` and `exports/`, the site's data, SMI incidents included |
| `pack_decisions.py` | Daily update, Backfill, Categorize | gzips the decision logs, deletes daily ones older than 30 days |
| `backfill.py`, `progress.py` | Backfill | the week-by-week historical search; `progress.py` pushes partial results |
| `review_feedback.py` | Feedback triage (on new issues) | first review of a feedback issue |
| `remove_story.py` | by hand | removes a published row, with a reason and a tombstone |

Workflows (repo-root `.github/workflows/`):

| Workflow | When |
|---|---|
| `daily.yml` (Daily update) | every day, 10:30 UTC |
| `exports.yml` (Rebuild exports) | a push to `main` that changes `data/`, `build_exports.py`, or the SMI incidents |
| `pages.yml` (Deploy site) | a push to `site/` or `exports/`, and after each data workflow |
| `tests.yml` (Tests) | a push or pull request touching either pipeline |
| `feedback.yml` (Feedback triage) | a new issue |
| `backfill.yml`, `categorize.yml`, `corroborate.yml`, `mugshots.yml`, `smi-backfill.yml` | by hand, from the Actions tab |

Every data workflow checks out `main` and commits to it, so code changes take effect only once
merged to `main`. They share one concurrency group and never run at the same time.

## Data files

| File | Written by | Holds |
|---|---|---|
| `data/stories.csv` | daily job | the database: one row per incident |
| `data/backfill/stories.csv`, `seen_urls.json`, `done_weeks.json` | backfill | historical rows (ids from 1,000,000), kept apart so the backfill and the daily job never edit the same file |
| `data/offenders.csv` | `build_exports.py` | one row per person; never edit |
| `data/removed.csv` | `enforce_sources.py`, `remove_story.py` | removed rows with the reason; ids never reused |
| `data/quarantine.csv` | `quarantine.py` | rows that failed validation, for hand review |
| `data/seen_urls.json` | daily job | every URL already decided, so it is never sent to Claude twice |
| `data/query_stats.json` | daily job | results per Google News query per day (90 days), for judging and pruning queries |
| `data/daily_adds.csv` | daily job | rows each daily run added (before the source rule removes any) |
| `data/decisions/` | `pack_decisions.py` | the decision logs (above) |
| `exports/` | `build_exports.py` | what the site reads: `stories.json`/`.csv`, `offenders.json`/`.csv`, `removed.json`, `stats.json` |
| `content/corrections.md` | by hand | the corrections policy |

## Running locally

```bash
cd database
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python pipeline/run_daily.py                    # one daily update
DAYS_BACK=3 MAX_CLASSIFY=200 python pipeline/run_daily.py   # wider net, bounded run
python pipeline/backfill.py --start 2025-01 --end 2025-06 --max-per-week 150   # or the Backfill workflow in Actions
python pipeline/build_exports.py               # rebuild exports/ from the CSVs only
python pipeline/categorize.py                  # categorize rows that have no offender_category
python pipeline/validate_data.py
python -m pytest -q                            # offline tests, no API key needed
```

The backfill searches Google News week by week with date operators (GDELT throttled nearly
every request from both GitHub's and a home IP; `--source gdelt` remains available). Each week
is capped at `--max-per-week` articles, best headlines first. It writes only to
`data/backfill/`, records completed weeks and resumes on rerun, and with `--push-progress`
commits that directory after every week. The "Backfill (manual)" workflow runs it on Actions
in 300-minute slices; rerun the same range until it reports no weeks left.

Until late November 2026 each week's search left out the week's last day: Google's `before:`
is exclusive, and it was given that day. Every week recorded in `data/backfill/done_weeks.json`
before then was searched that way (2025-01 through 2026-09). To search those weeks again,
remove them from that file and rerun the range. Articles already processed are skipped
without a Claude call; what goes to Claude is the missing days plus any articles an earlier
run left over its `--max-per-week` cap, which are never marked seen.

## What came over and what didn't

Carried over: `pipeline/`, `tests/`, `data/` (including removed and quarantined rows, the
seen-URL caches, and the gzipped decision logs), the corrections policy
(`content/corrections.md`), the workflows, and the issue forms.

Left behind: the old Underincarcerated front end (its `site/*.html`, `content/facts.md`, and
`build_pages.py`, which rendered Markdown into it) and its GitHub Pages deploy steps.
`exports/` replaces `site/` as the output folder.

Removed in the November 2026 cleanup (all in git history): the old design notes and to-do
list (`docs/plan.md`, `docs/TODO.md`, written for the Underincarcerated site), and two one-off
repair scripts whose jobs were done: `merge_duplicates.py` (merged rows stored twice before
2026-09-18) and `verify_counts.py` (re-checked prior counts in rows stored before the ingest
check).

## Open items

- **Corrections address.** `content/corrections.md` still says "[email address to be
  added]".
- **Discovery failures are silent.** On 2026-11-02 Google News returned nothing to every
  query and the run still succeeded; from 2026-11-05 the busiest queries returned about half
  as many results, and on 2026-11-17 to 11-22 up to 8 of 29 queries returned nothing. The
  two-day window now covers a single lost run, but nothing alerts anyone.
- **Unreadable articles** (about a quarter of those triage keeps) are dropped. Local TV
  station sites fail every time: KRQE, 11Alive, 8NewsNow, WCNC, WKRN, KING 5, ABC4, WKRG,
  FOX59, KGET, KENS 5, KHOU, WFLA, WWL.
- **Below-threshold stories** survive only in the 30-day decision logs.
- **Query mix.** Queries that name the record (`"prior arrests" charged`, `"career
  criminal"`, `"felony convictions"`) produce far more stored stories per headline than the
  release-status queries (`"released on bond"`, `"free on bond"`, `"out on bail"`).
- **The backfill's skipped days** (above) have not been searched again.
