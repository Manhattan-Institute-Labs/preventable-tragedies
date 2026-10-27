#!/usr/bin/env python3
"""SMI incident tracker.

    python pipeline/run.py daily                          # the daily job: last day, every step (API key)
    python pipeline/run.py backfill --windows 4           # four 14-day windows further back (API key)
    python pipeline/run.py harvest --days 14              # newest 14 days
    python pipeline/run.py harvest --days 14 --next-back  # the 14 days before the earliest window run
    python pipeline/run.py harvest --days 14 --end 2026-10-11
    python pipeline/run.py crosslist                      # repeat-offender stories with mental-health language
    python pipeline/run.py classify                       # Claude codes phrase-book articles (API key)
    python pipeline/run.py incidents                      # group coded articles, apply the inclusion rule
    python pipeline/run.py corroborate                    # second-source search (API key)
    python pipeline/run.py link                           # refresh links to ../database/
    python pipeline/run.py review                         # spot-check articles in the browser
    python pipeline/run.py validate                       # check data/ before a commit

harvest adds articles to data/articles.csv coded by the phrase book. With
ANTHROPIC_API_KEY set (and no --no-llm), Claude also triages the headlines
and then codes every phrase-book article not rejected in review.
incidents.csv is built from the articles Claude has coded, not excluded, and
no reviewer rejected. Every window harvested is recorded in
data/harvest_windows.json.
"""

import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import store
from config import ARTICLES_CSV, INCIDENTS_CSV
from link import link_repeat_offenders
from process import classify_articles, default_decision_log, harvest_articles


def _articles_summary(articles: list[dict]) -> str:
    c = Counter(r.get("review_status") for r in articles)
    coded = Counter(r.get("coded_by") for r in articles)
    return (f"{ARTICLES_CSV.name}: {len(articles)} articles ({c['pending']} not reviewed, "
            f"{c['flagged']} flagged, {c['accepted']} accepted, {c['rejected']} rejected; "
            f"{coded['claude']} coded by Claude, {coded['phrasebook']} by the phrase book only)")


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def _need_client():
    client = _client()
    if client is None:
        sys.exit("ANTHROPIC_API_KEY is not set: this command needs Claude.")
    return client


def _window_end(args) -> datetime:
    if getattr(args, "next_back", False):
        windows = store.load_windows()
        if not windows:
            sys.exit("No harvest windows recorded yet; run a harvest without --next-back first.")
        return datetime.fromisoformat(min(w["start"] for w in windows))
    if getattr(args, "end", None):
        return datetime.fromisoformat(args.end)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def harvest(client, days: int, end: datetime, use_gdelt: bool, max_fetch: int = 0,
            classify_limit: int = 0) -> None:
    """Discover, check, fetch and phrase-book-code one window; with a client,
    triage the headlines first and code the new articles with Claude after."""
    import fetch
    print(f"Discovering candidates: {(end - timedelta(days=days)):%Y-%m-%d} to {end:%Y-%m-%d}, "
          f"gdelt={use_gdelt}")
    candidates, start, end = fetch.discover(days_back=days, use_gdelt=use_gdelt, end=end)
    fetch.save_query_stats()
    print(f"Found {len(candidates)} candidate URLs\n")

    articles, seen = store.load_articles(), store.load_seen_urls()

    def checkpoint() -> None:
        store.save_articles(articles)
        store.save_seen_urls(seen)

    log = default_decision_log()
    counts = harvest_articles(client, candidates, articles, seen, window_start=start,
                              window_end=end, decision_log=log, max_fetch=max_fetch,
                              checkpoint=checkpoint)
    checkpoint()
    windows = store.load_windows()
    windows.append({"start": start.isoformat(timespec="seconds"),
                    "end": end.isoformat(timespec="seconds"),
                    "run": datetime.now().isoformat(timespec="seconds"),
                    "gdelt": use_gdelt, "claude": client is not None,
                    "new_articles": counts["new"]})
    store.save_windows(windows)
    print(f"\nHarvest done. New articles: {counts['new']}; non-US domains: {counts['foreign']}; "
          f"headline checks: {counts['prefiltered']}; triaged out: {counts['triaged_out']}; "
          f"no mental-health language: {counts['no_mh']}; no specific event: {counts['no_event']}; "
          f"no text: {counts['no_text']}; duplicates: {counts['duplicates']}; "
          f"unresolved: {counts['unresolved']}; errors: {counts['errors']}.")
    if client is not None:
        classify(client, classify_limit)
    else:
        link_repeat_offenders(articles)
        checkpoint()
        print(_articles_summary(articles))


def classify(client, limit: int = 0) -> None:
    articles = store.load_articles()
    c = classify_articles(client, articles, decision_log=default_decision_log(),
                          checkpoint=lambda: store.save_articles(articles), limit=limit)
    link_repeat_offenders(articles)
    store.save_articles(articles)
    print(f"\nClaude coded {c['coded']}, excluded {c['excluded']}, no text {c['no_text']}, "
          f"errors {c['errors']}; claims dropped for unverified quotes: {c['signals_dropped']}.")
    print(_articles_summary(articles))


def crosslist(limit: int = 0, dry_run: bool = False) -> None:
    from crosslist import crosslist as run_crosslist
    articles, checked = store.load_articles(), store.load_crosslist_checked()
    c = run_crosslist(articles, checked, limit=limit, dry_run=dry_run,
                      decision_log=default_decision_log())
    if not dry_run:
        store.save_articles(articles)
        store.save_crosslist_checked(checked)
    print(f"Repeat-offender stories read: {c['stories']}; with mental-health language: "
          f"{c['stories_with_mh']}; articles added: {c['articles_added']}; "
          f"articles with no text: {c['no_text']}" + (" (dry run: nothing saved)" if dry_run else ""))


def incidents(client) -> None:
    from incidents import build_incidents, build_subjects
    if client is None:
        print("ANTHROPIC_API_KEY is not set: grouping by rule only (same person, nearby dates).")
    articles = store.load_articles()
    removed = store.load_removed_incidents()
    rows, c, gone = build_incidents(articles, client, decision_log=default_decision_log(),
                                    previous=store.load_incidents(), removed=removed)
    store.save_incidents(rows)
    store.save_articles(articles)
    if gone:
        store.save_removed_incidents(removed + gone)
    store.save_subjects(build_subjects(rows))
    print(f"{INCIDENTS_CSV.name}: {c['incidents']} incidents ({c['qualifying']} meet the inclusion "
          f"rule) from {c['articles']} coded articles; {c['new']} new incidents, {c['grouped']} "
          f"articles grouped into an existing one, {c['redundant']} adding nothing new, "
          f"{c['removed']} incidents removed.")
    if not c["articles"]:
        print("No article has been coded by Claude yet; run classify first.")


def corroborate(client, limit: int = 0, max_minutes: float = 0) -> None:
    from corroborate import corroborate as run_corroborate
    rows, articles, seen = store.load_incidents(), store.load_articles(), store.load_seen_urls()

    def checkpoint() -> None:
        store.save_articles(articles)
        store.save_incidents(rows)
        store.save_seen_urls(seen)

    c = run_corroborate(client, rows, articles, seen, decision_log=default_decision_log(),
                        limit=limit, max_minutes=max_minutes, checkpoint=checkpoint)
    checkpoint()
    print(f"Second sources: {c['matched']} of {c['checked']} incidents matched, "
          f"{c['articles_added']} articles added.")


def cmd_harvest(args) -> None:
    client = None if args.no_llm else _client()
    if not args.no_llm and client is None:
        print("ANTHROPIC_API_KEY is not set: running the free phrase-book harvest only.\n")
    harvest(client, args.days, _window_end(args), not args.no_gdelt, args.max_fetch)


def cmd_daily(args) -> None:
    """Everything the daily workflow runs, in order."""
    client = _need_client()
    t0 = time.monotonic()
    harvest(client, args.days, _window_end(args), args.gdelt, args.max_fetch, args.max_classify)
    crosslist()
    classify(client, args.max_classify)   # the cross-listed articles, and any backlog
    incidents(client)
    left = args.max_minutes - (time.monotonic() - t0) / 60 if args.max_minutes else 0
    corroborate(client, max_minutes=max(left, 1) if args.max_minutes else 0)
    incidents(client)           # place the second sources


def cmd_backfill(args) -> None:
    """Harvest `--windows` windows further back, then the later steps once."""
    client = _need_client()
    t0 = time.monotonic()
    for i in range(args.windows):
        if args.max_minutes and (time.monotonic() - t0) / 60 > args.max_minutes:
            print("Time budget reached; stopping before the next window.")
            break
        args.next_back = bool(store.load_windows())
        harvest(client, args.days, _window_end(args), args.gdelt, args.max_fetch)
    classify(client)            # anything left uncoded, including earlier phrase-book harvests
    incidents(client)
    corroborate(client, max_minutes=args.corroborate_minutes)
    incidents(client)


def cmd_classify(args) -> None:
    classify(_need_client(), args.limit)


def cmd_crosslist(args) -> None:
    crosslist(args.limit, args.dry_run)


def cmd_incidents(args) -> None:
    incidents(_client())


def cmd_corroborate(args) -> None:
    corroborate(_need_client(), args.limit, args.max_minutes)
    incidents(_client())


def cmd_review(args) -> None:
    import review
    review.serve(port=args.port, open_browser=not args.no_browser)


def cmd_link(args) -> None:
    for path, load, save in ((ARTICLES_CSV, store.load_articles, store.save_articles),
                             (INCIDENTS_CSV, store.load_incidents, store.save_incidents)):
        rows = load()
        if rows:
            n = link_repeat_offenders(rows)
            save(rows)
            print(f"{path.name}: {n} of {len(rows)} rows linked to the repeat-offender database")
        else:
            print(f"{path.name}: no rows yet")


def cmd_validate(args) -> None:
    import validate
    validate.main()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily", help="the daily job: harvest the last day and run every step")
    d.add_argument("--days", type=int, default=1, help="window length in days (default 1)")
    d.add_argument("--gdelt", action="store_true", help="also query GDELT (off: it refuses Actions' IPs)")
    d.add_argument("--max-fetch", type=int, default=150, help="cap on articles fetched (default 150)")
    d.add_argument("--max-classify", type=int, default=200,
                   help="cap on articles Claude codes per pass (default 200); the rest wait")
    d.add_argument("--max-minutes", type=float, default=0, help="time budget (0 = none)")
    d.set_defaults(func=cmd_daily)

    b = sub.add_parser("backfill", help="harvest windows further back with Claude, then group")
    b.add_argument("--windows", type=int, default=1, help="how many windows (default 1)")
    b.add_argument("--days", type=int, default=14, help="window length in days (default 14)")
    b.add_argument("--gdelt", action="store_true", help="also query GDELT")
    b.add_argument("--max-fetch", type=int, default=0, help="cap per window (0 = none)")
    b.add_argument("--max-minutes", type=float, default=0, help="stop starting windows after this")
    b.add_argument("--corroborate-minutes", type=float, default=60,
                   help="time budget for the second-source search (default 60)")
    b.set_defaults(func=cmd_backfill)

    h = sub.add_parser("harvest", help="discover, check, fetch, and code articles")
    h.add_argument("--days", type=int, default=3, help="window length in days (default 3)")
    where = h.add_mutually_exclusive_group()
    where.add_argument("--end", help="window end, YYYY-MM-DD (default now)")
    where.add_argument("--next-back", action="store_true",
                       help="end the window where the earliest recorded window starts")
    h.add_argument("--no-gdelt", action="store_true", help="Google News only")
    h.add_argument("--no-llm", action="store_true", help="never call Claude, even with a key")
    h.add_argument("--max-fetch", type=int, default=0,
                   help="cap on articles fetched this run (0 = no cap); the rest wait for the next run")
    h.set_defaults(func=cmd_harvest)

    x = sub.add_parser("crosslist", help="add repeat-offender articles with mental-health language")
    x.add_argument("--limit", type=int, default=0, help="read at most this many stories (0 = all)")
    x.add_argument("--dry-run", action="store_true", help="count only; save nothing")
    x.set_defaults(func=cmd_crosslist)

    c = sub.add_parser("classify", help="Claude codes phrase-book articles not rejected in review")
    c.add_argument("--limit", type=int, default=0, help="code at most this many (0 = all)")
    c.set_defaults(func=cmd_classify)

    i = sub.add_parser("incidents", help="group coded articles into incidents and apply the rule")
    i.set_defaults(func=cmd_incidents)

    co = sub.add_parser("corroborate", help="search for second sources on qualifying incidents")
    co.add_argument("--limit", type=int, default=0, help="incidents to search (0 = all that need it)")
    co.add_argument("--max-minutes", type=float, default=0, help="time budget (0 = none)")
    co.set_defaults(func=cmd_corroborate)

    r = sub.add_parser("review", help="spot-check articles in the browser")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    r.set_defaults(func=cmd_review)

    lk = sub.add_parser("link", help="refresh links to the repeat-offender database")
    lk.set_defaults(func=cmd_link)

    v = sub.add_parser("validate", help="check data/ before a commit")
    v.set_defaults(func=cmd_validate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
