"""Cross-list people from the repeat-offender database who show mental illness.

    python pipeline/run.py crosslist [--limit N] [--dry-run]

Every story in ../database/ is read once (its id goes in
data/crosslist_checked.json). Each of its source articles is fetched and run
through the phrase book; an article with mental-health language and an
event is added to articles.csv like any harvested article, and from there
it is coded by Claude, grouped and held to the same inclusion rule as
everything else. The repeat-offender files are only read.

Free (no model calls), so it can run without an API key; Claude codes the
added articles on the next classify.
"""

import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import smi_coding
from config import REPEAT_OFFENDER_STORIES
from fetch import fetch_article_text_cached
from process import DecisionLog, _stored, canonical_url
from store import make_candidate_row, next_id

WORKERS = 8


def load_stories(paths: list[Path] | None = None) -> list[dict]:
    rows = []
    for p in paths or REPEAT_OFFENDER_STORIES:
        if p.exists():
            with open(p, newline="", encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
    return rows


def _urls(story: dict) -> list[str]:
    return [u for u in [story.get("source_url", "")] + (story.get("additional_sources") or "").split() if u]


def _code(url: str) -> dict | None:
    try:
        text = fetch_article_text_cached(url)
    except Exception:  # noqa: BLE001
        return None
    return smi_coding.code(text) if text else None


def crosslist(articles: list[dict], checked: set[str], story_paths: list[Path] | None = None,
              limit: int = 0, dry_run: bool = False, decision_log: Path | None = None) -> dict:
    """Add repeat-offender articles with mental-health language to articles.
    Mutates articles and checked (unless dry_run)."""
    log = DecisionLog(decision_log)
    stories = [s for s in load_stories(story_paths) if s.get("id") and s["id"] not in checked]
    if limit:
        stories = stories[:limit]
    counts = {"stories": len(stories), "stories_with_mh": 0, "articles_added": 0, "no_text": 0}
    stored, _ = _stored(articles)
    jobs = [(s, u) for s in stories for u in _urls(s) if canonical_url(u) not in stored]
    print(f"Reading {len(jobs)} articles from {len(stories)} repeat-offender stories")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        codings = list(pool.map(lambda job: _code(job[1]), jobs))
    row_id = next_id(articles)
    with_mh = set()
    for (story, url), coding in zip(jobs, codings):
        if coding is None:
            counts["no_text"] += 1
            continue
        if not (coding["has_mh"] and coding["has_event"]):
            continue
        with_mh.add(story["id"])
        log.write(stage="crosslist", story=story["id"], url=url, mh=coding["mh_sentence"])
        if dry_run:
            continue
        row = make_candidate_row(row_id, coding, {
            "url": url, "source": story.get("source_name", ""), "title": story.get("summary", ""),
            "published": story.get("incident_date", "")})
        row["review_note"] = f"from repeat-offender story {story['id']}"
        articles.append(row)
        row_id += 1
        counts["articles_added"] += 1
    counts["stories_with_mh"] = len(with_mh)
    if not dry_run:
        # A story none of whose articles could be fetched is retried next time.
        tried = {s["id"] for s, _ in jobs}
        read = {s["id"] for (s, _), c in zip(jobs, codings) if c is not None}
        checked |= {s["id"] for s in stories if s["id"] in read or s["id"] not in tried}
    return counts
