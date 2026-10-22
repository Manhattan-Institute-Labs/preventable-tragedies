"""Gzip this run's decision logs and prune old ones, before the commit step.

Workflow artifacts are not readable from every environment, and the logs are
the only record of what the model decided and why. Gzipped they are ~60 KB
per day; keep 30 days in the repo.

A second run on the same day appends to that day's .gz (a multi-member gzip,
which gzip.open reads as one stream) rather than replacing the first run's
log. Age comes from the date in the file name: a fresh checkout gives every
file the same mtime. Logs without a date name (backfill-*) are kept.
"""

import gzip
import re
import shutil
from datetime import date, timedelta

from config import DECISIONS_DIR

KEEP_DAYS = 30


def main() -> None:
    if not DECISIONS_DIR.exists():
        return
    for p in DECISIONS_DIR.glob("*.jsonl"):
        with open(p, "rb") as src, gzip.open(p.with_suffix(".jsonl.gz"), "ab") as dst:
            shutil.copyfileobj(src, dst)
        p.unlink()
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    for p in DECISIONS_DIR.glob("*.jsonl.gz"):
        m = re.match(r"\d{4}-\d{2}-\d{2}", p.name)
        if m and m.group() < cutoff:
            p.unlink()
    print(f"decision logs packed: {sorted(x.name for x in DECISIONS_DIR.glob('*.gz'))}")


if __name__ == "__main__":
    main()
