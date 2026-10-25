#!/usr/bin/env python3
"""SMI incident tracker: discover -> check -> fetch -> code -> classify -> verify -> dedupe -> review.

    python pipeline/run.py harvest --days 3            # classified run; needs ANTHROPIC_API_KEY
    python pipeline/run.py harvest --days 14 --no-llm  # free: phrase-book coding only
    python pipeline/run.py review                      # accept, correct, or reject queued rows
    python pipeline/run.py link                        # refresh links to ../database/

Every harvest appends to data/review_queue.csv as pending. Only rows accepted
in review reach data/incidents.csv. A --no-llm run never marks articles seen,
so a later classified run still reads them.
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import store
from config import INCIDENTS_CSV, REVIEW_QUEUE_CSV
from link import link_repeat_offenders
from process import code_candidates, default_decision_log, process_candidates


def _queue_summary(queue: list[dict]) -> str:
    c = Counter(r.get("review_status") for r in queue)
    return (f"{REVIEW_QUEUE_CSV.name}: {len(queue)} rows ({c['pending']} pending, "
            f"{c['flagged']} flagged, {c['accepted']} accepted, {c['rejected']} rejected)")


def cmd_harvest(args) -> None:
    import fetch
    print(f"Discovering candidates: last {args.days} days, gdelt={not args.no_gdelt}")
    candidates, window_start = fetch.discover(days_back=args.days, use_gdelt=not args.no_gdelt)
    fetch.save_query_stats()
    print(f"Found {len(candidates)} candidate URLs\n")
    queue, incidents = store.load_queue(), store.load_rows()

    if args.no_llm:
        counts = code_candidates(candidates, queue, incidents, window_start=window_start,
                                 decision_log=default_decision_log(), max_fetch=args.max_fetch)
        link_repeat_offenders(queue)
        store.save_queue(queue)
        print(f"\nDone. Queued {counts['new']} phrase-book candidates for review; no "
              f"mental-health language: {counts['no_mh']}, no specific event: {counts['rejected']}, "
              f"no text: {counts['no_text']}, headline checks: {counts['prefiltered']}, "
              f"duplicates: {counts['duplicates']}, unresolved: {counts['unresolved']}, "
              f"errors: {counts['errors']}.\n{_queue_summary(queue)}")
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Use --no-llm for a free phrase-book-only run.")
    import anthropic
    client = anthropic.Anthropic()
    seen = store.load_seen_urls()

    def checkpoint() -> None:
        store.save_queue(queue)
        store.save_seen_urls(seen)

    counts = process_candidates(client, candidates, queue, incidents, seen,
                                window_start=window_start, decision_log=default_decision_log(),
                                checkpoint=checkpoint, max_classify=args.max_fetch)
    link_repeat_offenders(queue)
    checkpoint()
    print(f"\nDone. Queued for review: {counts['new']} (of which same person: "
          f"{counts['same_person']}), same incident as another report: {counts['duplicates']}, "
          f"rejected: {counts['rejected']}, no mental-health language: {counts['no_mh']}, "
          f"headline checks: {counts['prefiltered']}, triaged out: {counts['triaged_out']}, "
          f"no text: {counts['no_text']}, unresolved: {counts['unresolved']}, "
          f"signals dropped for unverified quotes: {counts['signals_dropped']}, "
          f"errors: {counts['errors']}.\n{_queue_summary(queue)}")
    processed = counts["new"] + counts["duplicates"] + counts["rejected"] + counts["errors"]
    if processed and counts["errors"] > processed / 2:
        sys.exit("More than half of processed articles errored; failing the run.")


def cmd_review(args) -> None:
    import review
    review.serve(port=args.port, open_browser=not args.no_browser)


def cmd_link(args) -> None:
    for path, load, save in ((REVIEW_QUEUE_CSV, store.load_queue, store.save_queue),
                             (INCIDENTS_CSV, store.load_rows, store.save_rows)):
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

    h = sub.add_parser("harvest", help="discover, check, fetch, code, classify, and queue for review")
    h.add_argument("--days", type=int, default=3, help="search window in days (default 3)")
    h.add_argument("--no-gdelt", action="store_true", help="Google News only")
    h.add_argument("--no-llm", action="store_true",
                   help="phrase-book coding only; no API key needed")
    h.add_argument("--max-fetch", type=int, default=0,
                   help="cap on articles fetched this run (0 = no cap); the rest wait for the next run")
    h.set_defaults(func=cmd_harvest)

    r = sub.add_parser("review", help="review queued rows in the browser")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    r.set_defaults(func=cmd_review)

    lk = sub.add_parser("link", help="refresh links to the repeat-offender database")
    lk.set_defaults(func=cmd_link)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
