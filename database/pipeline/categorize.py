"""Offender categories: frequent violent offender, very frequent petty
offender, or unclassified.

    python pipeline/categorize.py [--limit N] [--recheck] [--max-minutes M]

The category describes the prior record, not the new offense (which is in
new_offense_type / new_offense_severity), so "petty offender commits a
homicide" stays visible as category petty plus a violent new offense:

  violent       two or more violent priors (arrests, charges, or convictions)
  petty         fewer than two violent priors, and the article describes the
                record well enough to see it is mostly low-level
  unclassified  the article gives counts or labels but not enough about what
                the priors were

For each row without a category (or every row, with --recheck), the article
is fetched again and the model reads it alongside the stored fields. It must
quote the sentence that supports its answer, and the quote must appear in
the article text or in the stored prior_evidence_quote (which was verified
against the article at ingest). An unverifiable violent or petty answer is
stored as unclassified, so a category can never rest on a claim the source
does not make.

The daily job runs this on new rows; the "Categorize (manual)" workflow
sweeps the whole database. Both also categorize the serious-mental-illness
tracker's incidents that meet this database's rules (../smi/, its
repeat_offender_rules column), so those show under both categories.
"""

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, ValidationError, field_validator

from classify import quote_in_text
from config import (BACKFILL_STORIES_CSV, CATEGORIZE_MODEL, OFFENDER_CATEGORIES, SMI_INCIDENTS_CSV,
                    STORIES_CSV)
from fetch import fetch_article_text
from process import DecisionLog, default_decision_log
from store import load_stories, save_stories

WORKERS = 4
CHECKPOINT_EVERY = 25

Category = Literal[tuple(OFFENDER_CATEGORIES)]  # type: ignore[valid-type]


class CategoryResult(BaseModel):
    category: Category
    violent_priors: Optional[int] = None  # lower bound the article supports
    evidence_quote: Optional[str] = None
    reason: str

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v):
        return v if v in OFFENDER_CATEGORIES else "unclassified"


SYSTEM_PROMPT = """You sort people in a public database of repeat offenders into categories by their PRIOR criminal record, as a news article describes it. The new crime that put them in the news does not count toward the category.

Categories:
- "violent" (frequent violent offender): the article states TWO OR MORE violent priors. Count each prior arrest, charge, or conviction for a violent offense: "two counts of assault" is 2; "prior convictions for robbery and battery" is 2; "a prior assault conviction" is 1; "violent offenses" or "violent crimes" in the plural with no number is 2.
- "petty" (very frequent petty offender): fewer than two violent priors, AND the article names or characterizes the rest of the record well enough to see it is mostly low-level or nonviolent (theft, shoplifting, trespassing, drug possession or sales, fare evasion, disorderly conduct, vandalism, burglary, fraud, DUI, probation violations, and similar).
- "unclassified": the article does not say enough to tell. Use this when it gives only counts ("arrested 30 times") or labels ("lengthy criminal history") without saying what the priors were, or when whether there were two violent priors is genuinely ambiguous. Do not guess.

Violent offenses: homicide and attempted homicide, assault and battery (including domestic and aggravated), robbery (including armed robbery and carjacking), rape and other sexual offenses, kidnapping, shootings and stabbings, child abuse, and threatening someone with a weapon. NOT violent on their own: weapons possession (including felon in possession), drug offenses, theft, burglary, trespassing, fraud, DUI, resisting arrest without an injury described, and violations of probation, parole, or court orders.

Only the prior record counts. Ignore the new offense, and ignore allegations in the current case.

Reply with only a JSON object:
{"category": "violent" | "petty" | "unclassified", "violent_priors": <integer, the number of violent priors the article supports, 0 if none>, "evidence_quote": "<one sentence copied exactly from the article that describes the prior record>", "reason": "<one sentence naming the priors that decide the category, and whether they were arrests, charges, or convictions>"}
For "unclassified", evidence_quote may be null."""


def _record_text(row: dict) -> str:
    return "\n".join(f"{label}: {row.get(field) or '(none)'}" for label, field in (
        ("Name", "offender_name"), ("New offense (ignore for the category)", "new_offense_type"),
        ("Prior arrests stated", "prior_count_arrests"),
        ("Prior convictions stated", "prior_count_convictions"),
        ("Prior felony convictions stated", "prior_count_felony_convictions"),
        ("Named prior offenses", "prior_offenses"),
        ("Prior-record sentence from the article", "prior_evidence_quote"),
        ("Summary", "summary")))


def parse_result(text: str) -> CategoryResult:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    return CategoryResult.model_validate(json.loads(text[start:end + 1]))


def ask(client: anthropic.Anthropic, row: dict, article: str | None) -> CategoryResult:
    content = f"Stored record:\n{_record_text(row)}\n\n"
    content += (f"Article text:\n{article}" if article
                else "Article text: (could not be fetched; use the stored record only)")
    resp = client.beta.messages.create(
        model=CATEGORIZE_MODEL, max_tokens=8000,  # thinking counts toward this
        output_config={"effort": "low"},
        # Re-run a safety-classifier decline on Anthropic's recommended model.
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        raise ValueError("model declined")
    return parse_result("".join(b.text for b in resp.content if b.type == "text"))


def verify(result: CategoryResult, row: dict, article: str | None) -> CategoryResult:
    """Downgrade a violent or petty answer whose quote the source doesn't
    contain, or a violent answer with fewer than two violent priors."""
    if result.category == "unclassified":
        return result
    sources = [s for s in (article, row.get("prior_evidence_quote")) if s]
    if not any(quote_in_text(result.evidence_quote, s) for s in sources):
        return CategoryResult(category="unclassified", violent_priors=result.violent_priors,
                              reason=f"quote not found in the article ({result.reason})")
    if result.category == "violent" and (result.violent_priors or 0) < 2:
        return CategoryResult(category="unclassified", violent_priors=result.violent_priors,
                              reason=f"answered violent with under two violent priors ({result.reason})")
    return result


def categorize_row(client: anthropic.Anthropic, row: dict, log: DecisionLog) -> CategoryResult:
    article = fetch_article_text(row["source_url"])
    raw = ask(client, row, article)
    result = verify(raw, row, article)
    log.write(stage="categorize", id=row["id"], article=bool(article), answer=raw.category,
              category=result.category, violent_priors=raw.violent_priors,
              quote=raw.evidence_quote, reason=result.reason)
    return result


def _sweep_rows(client: anthropic.Anthropic, todo: list[dict], save, max_minutes: float,
                log: DecisionLog, view=lambda row: row) -> dict:
    """Categorize `todo` in place, calling save() after each chunk. `view`
    presents a row in this database's field names."""
    counts = {"checked": 0, **{c: 0 for c in OFFENDER_CATEGORIES}}
    t0 = time.monotonic()

    def work(s):
        try:
            return categorize_row(client, view(s), log)
        except (anthropic.APIError, ValueError, ValidationError) as e:
            print(f"  error on id {s['id']}: {e}")
            return None

    for i in range(0, len(todo), CHECKPOINT_EVERY):
        if max_minutes and (time.monotonic() - t0) / 60 > max_minutes:
            print("  time budget reached; stopping")
            break
        chunk = todo[i:i + CHECKPOINT_EVERY]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(work, chunk))
        for s, r in zip(chunk, results):
            if r is None:
                continue
            counts["checked"] += 1
            counts[r.category] += 1
            s["offender_category"] = r.category
            s["category_reason"] = r.reason
        save()
    return counts


def sweep(client: anthropic.Anthropic, path, limit: int = 0, recheck: bool = False,
          max_minutes: float = 0, log: DecisionLog | None = None) -> dict:
    log = log or DecisionLog(default_decision_log())
    stories = load_stories(path)
    todo = [s for s in stories if recheck or not s.get("offender_category")]
    if limit:
        todo = todo[:limit]
    if todo:
        print(f"{path.name}: categorizing {len(todo)} of {len(stories)} stories")
    return _sweep_rows(client, todo, lambda: save_stories(stories, path), max_minutes, log)


def smi_view(row: dict) -> dict:
    """An SMI incident in this database's field names (../smi/ codebook)."""
    return {**row, "offender_name": row.get("subject_name", ""),
            "new_offense_type": row.get("new_offense_type") or row.get("incident_form", "")}


def sweep_smi(client: anthropic.Anthropic, path=None, limit: int = 0, recheck: bool = False,
              max_minutes: float = 0, log: DecisionLog | None = None) -> dict:
    """Categorize the SMI tracker's incidents that meet this database's rules
    (its repeat_offender_rules column), so they show under both categories.
    The file keeps its own columns."""
    path = path or SMI_INCIDENTS_CSV
    log = log or DecisionLog(default_decision_log())
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    todo = [r for r in rows if r.get("repeat_offender_rules") == "yes" and not r.get("site_excluded")
            and (recheck or not r.get("offender_category"))]
    if limit:
        todo = todo[:limit]
    if todo:
        print(f"{path.name}: categorizing {len(todo)} SMI incidents that meet this database's rules")

    def save() -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return _sweep_rows(client, todo, save, max_minutes, log, view=smi_view)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="rows per file (0 = all that need it)")
    ap.add_argument("--recheck", action="store_true", help="categorize rows that already have a category")
    ap.add_argument("--max-minutes", type=float, default=0, help="stop after this long (0 = no limit)")
    ap.add_argument("--smi-only", action="store_true", help="only the SMI tracker's incidents")
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set; refusing to run.")
    client = anthropic.Anthropic()
    total = {"checked": 0, **{c: 0 for c in OFFENDER_CATEGORIES}}
    for path in () if a.smi_only else (STORIES_CSV, BACKFILL_STORIES_CSV):
        if path.exists():
            for k, v in sweep(client, path, a.limit, a.recheck, a.max_minutes).items():
                total[k] += v
    if SMI_INCIDENTS_CSV.exists():
        for k, v in sweep_smi(client, SMI_INCIDENTS_CSV, a.limit, a.recheck, a.max_minutes).items():
            total[k] += v
    print("Categories: " + ", ".join(f"{c} {total[c]}" for c in OFFENDER_CATEGORIES)
          + f" (of {total['checked']} rows)")


if __name__ == "__main__":
    main()
