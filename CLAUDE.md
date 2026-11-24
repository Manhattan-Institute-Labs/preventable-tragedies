# Preventable Tragedies

A Manhattan Institute site that documents "preventable tragedies": serious crimes committed by people who, under better policy, would already have been detained, supervised or in treatment. It covers three groups: frequent violent offenders, habitual offenders whose records escalate, and people with untreated serious mental illness. The site is a sourced repository of these cases, plus the research and policy proposals behind them.

## Repo layout

- `.github/workflows/`: everything runs here; the data workflows run with `database/` (or `smi/`) as their working directory. `.github/ISSUE_TEMPLATE/` holds the public error and missing-story forms that `feedback.yml` triages.
- `site/`: the static four-page site (first draft, on GitHub Pages). `site/build.py` generates its case data from `database/exports/`; other copy is placeholder. Its own `CLAUDE.md` covers structure, conventions, design rules and backlog.
- `database/`: the frequent-offender case database and the pipeline that maintains it (news discovery, Claude classification with verbatim-quote checks, dedupe, second-source search), carried over from the Underincarcerated repo. `database/README.md` covers inclusion criteria, schema and commands. Workflows live in `.github/workflows/` and run with `database/` as their working directory. **These are real, named people:** the content rules below apply to every row.
- `smi/`: the serious-mental-illness incident tracker, built on the `database/` pipeline with its own inclusion rules, codebook and reference set; `subject_key` matches `offender_key` so people in both are linked. It runs in the same daily job, and `database/pipeline/build_exports.py` merges its qualifying incidents into `exports/` as the "Serious mental illness" category. `smi/README.md` covers it. Also real, named people: the same content rules apply.

Other parts of the project (e.g. a colleague's parallel database) will get their own top-level folders.

## How the pipeline runs

- **Daily update** (`daily.yml`, 10:30 UTC): Google News search over the last two days (`after:`/`before:` dates; Google's `before:` is exclusive), headline triage, full-text classification with verbatim evidence quotes, dedupe, mug shots, second-source search, the source rule, the SMI tracker, categorizing, validation, exports, one commit to `main` by `preventable-tragedies-bot`. No human approval step.
- **Publication rule**: two distinct outlets, or one on `TRUSTED_OUTLETS`. Repeat-offender stories must also meet the strict threshold (5+ prior arrests, 5+ prior convictions or 3+ prior felony convictions, as printed).
- **A green run is not proof the run found anything.** Discovery failures are silent (on 2026-11-02 Google returned nothing and the run succeeded). Check the log's "Google News: N items across M queries" line, `database/data/daily_adds.csv`, and `database/data/query_stats.json`.
- **Decision logs** record every model decision with its reason. The database's are committed gzipped to `database/data/decisions/`, and daily ones are pruned after 30 days (they stay in git history). The SMI tracker's are only kept as workflow artifacts, for 30 days.
- `database/README.md` lists every script, workflow and data file, and the open items.

## Working in this repo

- Every data workflow checks out and commits to `main`, and the bot commits there daily. Work on a branch (`dev`), merge `main` into it before opening a pull request, and expect code changes to take effect only once merged.
- Tests need no API key: `python -m pytest -q` in `database/` and in `smi/`, plus `python pipeline/run.py validate` in `smi/`. `tests.yml` runs them on pushes and pull requests. On Windows, `test_pack_decisions` fails on line endings; it passes on GitHub's Linux runners.
- Never hand-edit derived files: `database/data/offenders.csv`, `database/exports/`, `smi/data/subjects.csv`. Fix the source rows and rebuild (`python pipeline/build_exports.py`).
- Remove a published row with `database/pipeline/remove_story.py --id N --reason "..."` (ids are never reused); keep an SMI incident off the site with its `site_excluded` column.
- Running pipeline steps for real calls Claude (costs money) and Google News (which throttles). Ask before starting a backfill or a whole-database sweep.

## Content rules — important for this subject

- **Every case must be sourced** and link to its original reporting or court record. Never invent cases, names, quotes or statistics in anything that could ship.
- Keep the distinction between **arrests, charges and convictions** accurate in copy and data fields. Wording about named individuals needs editorial and legal review before launch.
- **Booking photos:** confirm usage rights and the source agency. The `photo` field is `null` until then.
- **Facts page figures** must come from the cited research (the numbered sources list). Keep the "what the data does not show" context box in the mental-illness chapter.
- Placeholders use `[Square brackets]`, so `grep -n "\[" site/*.html` finds what's left to write.
