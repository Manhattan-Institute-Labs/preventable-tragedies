# preventable-tragedies
Database of preventable tragedies: crimes by frequent offenders and people with serious mental illness

## Contents

- [`site/`](site/): the site (first draft), published to GitHub Pages. The landing page and stories index show real cases from the database; other copy is placeholder.
- [`database/`](database/): the frequent-offender case database and the daily pipeline that maintains it, carried over from Underincarcerated (now a private archive).
- [`smi/`](smi/): the serious-mental-illness incident tracker, the second harvester for the same record set. Both feed `database/pipeline/records.py`, which validates, categorizes (one category per case) and exports everything.
