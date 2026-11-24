# preventable-tragedies

A Manhattan Institute database and site documenting "preventable tragedies": serious crimes by
frequent offenders and by people with serious mental illness whom public systems already knew.
Every case comes from news reporting and links to it.

## How cases get in

Every day at 10:30 UTC the **Daily update** workflow searches Google News for the last two
days of stories, has Claude screen the headlines and then read each promising article, keeps
only cases whose quoted evidence appears word for word in the article, merges duplicates, and
looks for a second outlet. A case is published only when two outlets report it (or one trusted
outlet). The same run does the same for the serious-mental-illness tracker, sorts frequent
offenders into violent and petty, rebuilds `database/exports/`, and commits the result to
`main`. No step waits for a person; errors are corrected after the fact.

Inclusion rules, schemas, scripts and commands are in [`database/README.md`](database/README.md)
and [`smi/README.md`](smi/README.md). The site is described in [`site/README.md`](site/README.md).

## Working on it

The data workflows run from `main` and the bot commits to `main` every day, so changes are
made on a branch (`dev`), tested, and merged by pull request. Merge `main` into the branch
first, so the PR carries the latest data. Tests: `python -m pytest -q` in `database/` and in
`smi/` (no API key needed). Project rules for Claude Code sessions are in
[`CLAUDE.md`](CLAUDE.md).

## Contents

- [`.github/`](.github/): the GitHub Actions workflows that run everything, and the issue forms for reporting errors and missing stories.
- [`site/`](site/): the site (first draft), published to GitHub Pages. The landing page and stories index show real cases from the database; other copy is placeholder.
- [`database/`](database/): the frequent-offender case database and the daily pipeline that maintains it, carried over from Underincarcerated (now a private archive).
- [`smi/`](smi/): the serious-mental-illness incident tracker, built on the same pipeline, with a person key that links people who appear in both.
