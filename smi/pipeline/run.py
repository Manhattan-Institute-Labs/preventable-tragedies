#!/usr/bin/env python3
"""SMI incident tracker.

    python pipeline/run.py harvest --days 14              # newest 14 days
    python pipeline/run.py harvest --days 14 --next-back  # the 14 days before the earliest window run
    python pipeline/run.py harvest --days 14 --end 2026-10-11
    python pipeline/run.py review                         # accept articles of the right general type
    python pipeline/run.py classify                       # Claude codes phrase-book articles (API key)
    python pipeline/run.py incidents                      # group accepted, coded articles into incidents
    python pipeline/run.py link                           # refresh links to ../database/

harvest adds articles to data/articles.csv as pending, coded by the phrase
book. With ANTHROPIC_API_KEY set (and no --no-llm), Claude also triages the
headlines and then codes every phrase-book article not rejected in review.
incidents.csv is built only from accepted articles Claude has coded and not
excluded. Every window harvested is recorded in data/harvest_windows.json.
"""

import argparse
import os
import sys
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
    return (f"{ARTICLES_CSV.name}: {len(articles)} articles ({c['pending']} pending, "
            f"{c['flagged']} flagged, {c['accepted']} accepted, {c['rejected']} rejected; "
            f"{coded['claude']} coded by Claude, {coded['phrasebook']} by the phrase book only)")


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def _window_end(args) -> datetime:
    if args.next_back:
        windows = store.load_windows()
        if not windows:
            sys.exit("No harvest windows recorded yet; run a harvest without --next-back first.")
        return datetime.fromisoformat(min(w["start"] for w in windows))
    if args.end:
        return datetime.fromisoformat(args.end)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cmd_harvest(args) -> None:
    import fetch
    client = None if args.no_llm else _client()
    if not args.no_llm and client is None:
        print("ANTHROPIC_API_KEY is not set: running the free phrase-book harvest only.\n")
    end = _window_end(args)
    print(f"Discovering candidates: {(end - timedelta(days=args.days)):%Y-%m-%d} to {end:%Y-%m-%d}, "
          f"gdelt={not args.no_gdelt}")
    candidates, start, end = fetch.discover(days_back=args.days, use_gdelt=not args.no_gdelt, end=end)
    fetch.save_query_stats()
    print(f"Found {len(candidates)} candidate URLs\n")

    articles, seen = store.load_articles(), store.load_seen_urls()

    def checkpoint() -> None:
        store.save_articles(articles)
        store.save_seen_urls(seen)

    log = default_decision_log()
    counts = harvest_articles(client, candidates, articles, seen, window_start=start,
                              window_end=end, decision_log=log, max_fetch=args.max_fetch,
                              checkpoint=checkpoint)
    checkpoint()
    windows = store.load_windows()
    windows.append({"start": start.isoformat(timespec="seconds"),
                    "end": end.isoformat(timespec="seconds"),
                    "run": datetime.now().isoformat(timespec="seconds"),
                    "gdelt": not args.no_gdelt, "claude": client is not None,
                    "new_articles": counts["new"]})
    store.save_windows(windows)
    print(f"\nHarvest done. New articles: {counts['new']}; non-US domains: {counts['foreign']}; "
          f"headline checks: {counts['prefiltered']}; triaged out: {counts['triaged_out']}; "
          f"no mental-health language: {counts['no_mh']}; no specific event: {counts['no_event']}; "
          f"no text: {counts['no_text']}; duplicates: {counts['duplicates']}; "
          f"unresolved: {counts['unresolved']}; errors: {counts['errors']}.")

    if client is not None:
        c = classify_articles(client, articles, decision_log=log, checkpoint=checkpoint)
        checkpoint()
        print(f"\nClaude coded {c['coded']}, excluded {c['excluded']}, no text {c['no_text']}, "
              f"errors {c['errors']}; signals dropped for unverified quotes: {c['signals_dropped']}.")
    link_repeat_offenders(articles)
    checkpoint()
    print(_articles_summary(articles))


def cmd_classify(args) -> None:
    client = _client()
    if client is None:
        sys.exit("ANTHROPIC_API_KEY is not set: classify needs Claude.")
    articles = store.load_articles()
    c = classify_articles(client, articles, decision_log=default_decision_log(),
                          checkpoint=lambda: store.save_articles(articles), limit=args.limit)
    link_repeat_offenders(articles)
    store.save_articles(articles)
    print(f"\nClaude coded {c['coded']}, excluded {c['excluded']}, no text {c['no_text']}, "
          f"errors {c['errors']}; signals dropped for unverified quotes: {c['signals_dropped']}.")
    print(_articles_summary(articles))


def cmd_incidents(args) -> None:
    from incidents import build_incidents
    client = _client()
    if client is None:
        print("ANTHROPIC_API_KEY is not set: grouping by rule only (same person, nearby dates).")
    articles = store.load_articles()
    rows, c = build_incidents(articles, client, decision_log=default_decision_log())
    store.save_incidents(rows)
    store.save_articles(articles)
    print(f"{INCIDENTS_CSV.name}: {c['incidents']} incidents from {c['articles']} accepted, "
          f"Claude-coded articles ({c['new']} new incidents, {c['grouped']} articles grouped "
          f"into an existing one).")
    if not c["articles"]:
        print("No accepted article has been coded by Claude yet; run classify first.")


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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

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

    c = sub.add_parser("classify", help="Claude codes phrase-book articles not rejected in review")
    c.add_argument("--limit", type=int, default=0, help="code at most this many (0 = all)")
    c.set_defaults(func=cmd_classify)

    i = sub.add_parser("incidents", help="group accepted, Claude-coded articles into incidents")
    i.set_defaults(func=cmd_incidents)

    r = sub.add_parser("review", help="review articles in the browser")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    r.set_defaults(func=cmd_review)

    lk = sub.add_parser("link", help="refresh links to the repeat-offender database")
    lk.set_defaults(func=cmd_link)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
