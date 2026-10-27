# Preventable Tragedies

A Manhattan Institute site that documents "preventable tragedies": serious crimes committed by people who, under better policy, would already have been detained, supervised or in treatment. It covers three groups: frequent violent offenders, habitual offenders whose records escalate, and people with untreated serious mental illness. The site is a sourced repository of these cases, plus the research and policy proposals behind them.

## Repo layout

- `site/`: the static four-page site (first draft, on GitHub Pages). `site/build.py` generates its case data from `database/exports/`; other copy is placeholder. Its own `CLAUDE.md` covers structure, conventions, design rules and backlog.
- `database/`: the frequent-offender case database and the pipeline that maintains it (news discovery, Claude classification with verbatim-quote checks, dedupe, second-source search), carried over from the Underincarcerated repo. `database/README.md` covers inclusion criteria, schema and commands. Workflows live in `.github/workflows/` and run with `database/` as their working directory. **These are real, named people:** the content rules below apply to every row.
- `smi/`: the serious-mental-illness incident tracker, built on the `database/` pipeline with its own inclusion rules, codebook and reference set; `subject_key` matches `offender_key` so people in both are linked. It runs in the same daily job, and `database/pipeline/build_exports.py` merges its qualifying incidents into `exports/` as the "Serious mental illness" category. `smi/README.md` covers it. Also real, named people: the same content rules apply.

Other parts of the project (e.g. a colleague's parallel database) will get their own top-level folders.

## Content rules — important for this subject

- **Every case must be sourced** and link to its original reporting or court record. Never invent cases, names, quotes or statistics in anything that could ship.
- Keep the distinction between **arrests, charges and convictions** accurate in copy and data fields. Wording about named individuals needs editorial and legal review before launch.
- **Booking photos:** confirm usage rights and the source agency. The `photo` field is `null` until then.
- **Facts page figures** must come from the cited research (the numbered sources list). Keep the "what the data does not show" context box in the mental-illness chapter.
- Placeholders use `[Square brackets]`, so `grep -n "\[" site/*.html` finds what's left to write.
