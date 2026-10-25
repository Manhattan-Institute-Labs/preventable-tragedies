"""Shared candidate-processing loop.

Adapted from ../database/pipeline/process.py. Order per candidate:
  seen-URL check -> free headline check (prefilter.py) -> triage (batched,
  headline only, Claude) -> Google redirect resolve -> wire/blotter filter ->
  same-article URL check -> fetch (cached) -> phrase book -> classify ->
  evidence-quote check -> dedupe -> append.

Nothing goes straight into incidents.csv. Every coded article is appended to
the review queue (data/review_queue.csv) as pending, and only a reviewer's
accept moves it into incidents.csv (review.py). With no model (a --no-llm
run) the loop stops after the phrase book. That mode never touches
seen_urls.json, so a later classified run still reads every article, and a
classified row replaces a still-pending phrase-book row for the same URL.

Triage runs before URL resolution on purpose: decoding a Google News redirect
costs about a second of HTTP per URL. Network-bound work runs in thread
pools; dedupe and append are serial because each depends on the rows added
before it. Every decision goes to a JSONL log in data/decisions/.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

import smi_coding
from classify import check_evidence, classify_article, hedge_summary
from config import DECISIONS_DIR
from dedupe import ages_consistent, check_duplicate, merge_into
from fetch import fetch_article_text_cached, is_vendor_or_wire, resolve_candidate
from prefilter import prefilter
from store import make_candidate_row, make_row, next_id
from triage import triage_candidates

_VARIANT_SEGMENTS = ("/gallery/", "/newsletter/gallery/", "/newsletter/", "/amp/", "/photos/")
_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref",
                    "ito", "share", "outputType", "taid", "cmpid")


def canonical_url(url: str) -> str:
    """Normalize an article URL for duplicate comparison."""
    u = url.split("#")[0].strip().lower()
    base, _, query = u.partition("?")
    base = base.rstrip("/")
    for seg in _VARIANT_SEGMENTS:
        base = base.replace(seg, "/")
    if query:
        kept = [kv for kv in query.split("&")
                if kv and not any(kv.startswith(t) for t in _TRACKING_PARAMS)]
        if kept:
            return base + "?" + "&".join(sorted(kept))
    return base


_DATED_PATH_RE = re.compile(r"/\d{4}/\d{2}/\d{2}/[^/?]+$")


def syndication_path(url: str) -> str | None:
    """The /YYYY/MM/DD/slug tail of a canonical URL, if any. Newspaper chains
    republish one article at the same dated path on sibling hostnames."""
    m = _DATED_PATH_RE.search(canonical_url(url).partition("?")[0])
    return m.group(0) if m else None


class DecisionLog:
    def __init__(self, path: Path | None):
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **record) -> None:
        if not self.path:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def default_decision_log() -> Path:
    return DECISIONS_DIR / f"{date.today().isoformat()}.jsonl"


def _date_horizon() -> str:
    return (date.today() + timedelta(days=2)).isoformat()


def _mark_seen(seen: set[str], candidate: dict) -> None:
    seen.add(candidate["url"])
    if candidate.get("google_url"):
        seen.add(candidate["google_url"])


def _title_key(title: str) -> str:
    """Headline normalized for cross-source matching. Google News appends
    " - Outlet"; strip it, then keep letters and digits only."""
    t = (title or "").rsplit(" - ", 1)[0].lower()
    return re.sub(r"[^a-z0-9]+", "", t)


CHECKPOINT_EVERY = 25
RESOLVE_WORKERS = 4       # parallel Google News redirect decodes (8 tripped Google's throttle)
RESOLVE_RETRY_WAIT = 600  # seconds to wait when Google throttles the decoder
RESOLVE_RETRIES = 2
FETCH_WORKERS = 8         # parallel fetch (+ classify): I/O bound


class ResolutionThrottled(RuntimeError):
    """Google refused to decode most redirects even after waiting. Nothing in
    this batch was marked seen; the caller should stop and retry later."""


def _new_counts() -> dict:
    return {"new": 0, "duplicates": 0, "same_person": 0, "prefiltered": 0, "triaged_out": 0,
            "rejected": 0, "no_text": 0, "no_mh": 0, "unresolved": 0, "skipped_seen": 0,
            "errors": 0, "signals_dropped": 0}


def select_candidates(client: anthropic.Anthropic | None, candidates: list[dict],
                      seen_urls: set[str], stored_urls: set[str], stored_paths: set[str],
                      window_start: datetime | None, log: DecisionLog, counts: dict,
                      max_fetch: int = 0) -> list[dict]:
    """Everything before the article fetch. Returns the candidates to fetch,
    with Google redirects resolved. Mutates seen_urls and counts."""
    fresh = []
    for c in candidates:
        if c["url"] in seen_urls:
            counts["skipped_seen"] += 1
            continue
        reason = prefilter(c, window_start)
        if reason:
            log.write(stage="prefilter", url=c["url"], title=c.get("title"), reason=reason)
            _mark_seen(seen_urls, c)
            counts["prefiltered"] += 1
            continue
        fresh.append(c)

    if client and fresh:
        try:
            keep = triage_candidates(client, fresh)
        except Exception as e:  # noqa: BLE001
            print(f"  triage failed ({e}); fetching everything")
            keep = [True] * len(fresh)
    else:
        keep = [True] * len(fresh)
    kept = []
    for c, k in zip(fresh, keep):
        if client:
            log.write(stage="triage", url=c["url"], title=c.get("title"), query=c.get("query"), keep=k)
        if k:
            kept.append(c)
        else:
            _mark_seen(seen_urls, c)
            counts["triaged_out"] += 1
    print(f"Headline checks kept {len(kept)} of {len(candidates)} candidates")

    direct_titles = {_title_key(c["title"]) for c in kept if "news.google.com" not in c["url"]}
    google = []
    for c in kept:
        if "news.google.com" in c["url"] and _title_key(c["title"]) in direct_titles:
            _mark_seen(seen_urls, c)
            counts["duplicates"] += 1  # the same article reached via GDELT
        elif "news.google.com" in c["url"]:
            google.append(c)
    if google:
        t0 = time.monotonic()
        print(f"Resolving {len(google)} Google News redirects...")
        pending = google
        for attempt in range(RESOLVE_RETRIES + 1):
            with ThreadPoolExecutor(max_workers=RESOLVE_WORKERS) as pool:
                list(pool.map(resolve_candidate, pending))
            pending = [c for c in pending if "news.google.com" in c["url"]]
            print(f"  resolved {len(google) - len(pending)}/{len(google)} "
                  f"in {int(time.monotonic() - t0)}s")
            if len(pending) <= max(2, 0.5 * len(google)):
                break
            # Google throttles its decoder after ~1,500 decodes in an hour.
            # Wait and retry, and never mark an undecoded URL seen.
            if attempt < RESOLVE_RETRIES:
                print(f"  {len(pending)} still undecoded: Google is throttling; "
                      f"waiting {RESOLVE_RETRY_WAIT // 60} min")
                time.sleep(RESOLVE_RETRY_WAIT)
        if pending and len(pending) > max(2, 0.5 * len(google)):
            raise ResolutionThrottled(f"{len(pending)} of {len(google)} redirects undecoded "
                                      f"after {RESOLVE_RETRIES} retries")

    to_fetch = []
    for c in kept:
        url = c["url"]
        if "news.google.com" in url:
            if _title_key(c["title"]) not in direct_titles:
                counts["unresolved"] += 1  # not marked seen: retried next run
                log.write(stage="unresolved", url=url, title=c.get("title"))
            continue
        if is_vendor_or_wire(url):
            _mark_seen(seen_urls, c)
            counts["rejected"] += 1
            continue
        if canonical_url(url) in stored_urls or (syndication_path(url) or "\0") in stored_paths:
            _mark_seen(seen_urls, c)
            counts["duplicates"] += 1
            continue
        if url in seen_urls:  # resolved URL was seen under another Google id
            _mark_seen(seen_urls, c)
            counts["skipped_seen"] += 1
            continue
        to_fetch.append(c)
    if max_fetch and len(to_fetch) > max_fetch:
        print(f"Capping at {max_fetch} this run; {len(to_fetch) - max_fetch} deferred")
        to_fetch = to_fetch[:max_fetch]
    return to_fetch


def _stored(rows: list[dict]) -> tuple[set[str], set[str]]:
    urls = {canonical_url(r["source_url"]) for r in rows if r.get("source_url")}
    urls |= {canonical_url(u) for r in rows for u in (r.get("additional_sources") or "").split() if u}
    paths = {p for r in rows for u in [r.get("source_url", ""), *(r.get("additional_sources") or "").split()]
             for p in [syndication_path(u)] if p}
    return urls, paths


def _fetch_and_code(candidate: dict) -> dict:
    try:
        text = fetch_article_text_cached(candidate["url"])
        if not text:
            return {"no_text": True}
        return {"text": text, "coding": smi_coding.code(text)}
    except Exception as e:  # noqa: BLE001 - surfaced to the serial loop
        return {"error": e}


def _fetch_code_classify(client, candidate: dict) -> dict:
    res = _fetch_and_code(candidate)
    if "text" not in res or not res["coding"]["has_mh"]:
        return res   # no text, an error, or no mental-health language: no model call
    try:
        res["cls"] = classify_article(client, candidate, res["text"], res["coding"])
    except Exception as e:  # noqa: BLE001
        return {"error": e}
    return res


def _open_rows(queue: list[dict]) -> list[dict]:
    """Classified queue rows not yet decided: what a new report may merge into."""
    return [r for r in queue if r.get("coded_by") == "claude"
            and r.get("review_status") in ("pending", "flagged")]


def code_candidates(candidates: list[dict], queue: list[dict], incidents: list[dict],
                    window_start: datetime | None = None,
                    decision_log: Path | None = None, max_fetch: int = 0) -> dict:
    """The --no-llm path: phrase-book coding only, appended to the review
    queue as pending. URLs already in the queue or in incidents are skipped;
    seen_urls.json is not used."""
    counts = _new_counts()
    log = DecisionLog(decision_log)
    have = {r["source_url"] for r in queue + incidents if r.get("source_url")}
    stored_urls, stored_paths = _stored(queue + incidents)
    to_fetch = select_candidates(None, candidates, set(have), stored_urls, stored_paths,
                                 window_start, log, counts, max_fetch)
    print(f"Fetching and coding {len(to_fetch)} with {FETCH_WORKERS} workers\n")
    row_id = next_id(queue + incidents)
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(_fetch_and_code, to_fetch))
    for c, res in zip(to_fetch, results):
        if "error" in res:
            counts["errors"] += 1
            continue
        if res.get("no_text"):
            counts["no_text"] += 1
            log.write(stage="fetch", url=c["url"], result="no_text")
            continue
        coding = res["coding"]
        if not coding["has_mh"] or not coding["has_event"]:
            reason = "no mental-health language" if not coding["has_mh"] else "no specific event"
            counts["no_mh" if not coding["has_mh"] else "rejected"] += 1
            log.write(stage="phrasebook", url=c["url"], qualifies=False, reason=reason)
            continue
        queue.append(make_candidate_row(row_id, coding, c))
        log.write(stage="phrasebook", url=c["url"], qualifies=True, id=queue[-1]["id"])
        row_id += 1
        counts["new"] += 1
    return counts


def process_candidates(client: anthropic.Anthropic, candidates: list[dict],
                       queue: list[dict], incidents: list[dict], seen_urls: set[str],
                       window_start: datetime | None = None,
                       decision_log: Path | None = None, checkpoint=None,
                       max_classify: int = 0) -> dict:
    """Classify candidates and queue qualifying rows for review. Mutates queue
    and seen_urls in place; incidents is only read. Returns counts."""
    counts = _new_counts()
    log = DecisionLog(decision_log)
    # A still-pending phrase-book row is re-read: the classified row replaces it.
    phrasebook = {r["source_url"]: r for r in queue
                  if r.get("coded_by") == "phrasebook" and r.get("review_status") == "pending"}
    stored_urls, stored_paths = _stored(incidents + [r for r in queue
                                                     if r["source_url"] not in phrasebook])
    to_fetch = select_candidates(client, candidates, seen_urls, stored_urls, stored_paths,
                                 window_start, log, counts, max_classify)
    print(f"Fetching and classifying {len(to_fetch)} with {FETCH_WORKERS} workers\n")

    def retire(pb_row: dict | None, note: str) -> None:
        """Close a phrase-book row the classifier has now decided."""
        if pb_row:
            pb_row["review_status"] = "rejected"
            pb_row["review_note"] = note

    done = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for start in range(0, len(to_fetch), CHECKPOINT_EVERY):
            chunk = to_fetch[start:start + CHECKPOINT_EVERY]
            results = list(pool.map(lambda c: _fetch_code_classify(client, c), chunk))
            for c, res in zip(chunk, results):
                done += 1
                url = c["url"]
                pb_row = phrasebook.get(url)
                print(f"[{done}/{len(to_fetch)}] {c['title'][:90]}")
                if "error" in res:
                    print(f"  error, skipping (will retry next run): {res['error']}")
                    counts["errors"] += 1
                    continue   # never marked seen on errors
                _mark_seen(seen_urls, c)
                if res.get("no_text"):
                    counts["no_text"] += 1
                    log.write(stage="fetch", url=url, result="no_text")
                    print("  no article text; skipping")
                    continue
                coding = res["coding"]
                if "cls" not in res:
                    counts["no_mh"] += 1
                    log.write(stage="phrasebook", url=url, qualifies=False,
                              reason="no mental-health language in article")
                    print("  no mental-health language; skipping")
                    continue
                cls, text = res["cls"], res["text"]
                if not cls.qualifies:
                    log.write(stage="classify", url=url, qualifies=False, reason=cls.reason,
                              killed_by_police=cls.killed_by_police)
                    print(f"  rejected: {cls.reason[:100]}")
                    retire(pb_row, f"classifier rejected: {cls.reason}")
                    counts["rejected"] += 1
                    continue
                problem, dropped = check_evidence(cls, text)
                if problem:
                    log.write(stage="verify", url=url, qualifies=False, reason=problem,
                              classification=cls.model_dump())
                    print(f"  rejected on verification: {problem}")
                    retire(pb_row, f"classifier rejected on verification: {problem}")
                    counts["rejected"] += 1
                    continue
                if dropped:
                    log.write(stage="verify", url=url, dropped=dropped)
                    counts["signals_dropped"] += len(dropped)
                if cls.incident_date and cls.incident_date[:10] > _date_horizon():
                    cls.incident_date = None   # a misread date, not a reason to drop the story
                hedged = hedge_summary(cls.summary or "", cls.outcome or "", c.get("source") or "")
                if hedged != cls.summary:
                    log.write(stage="hedge", url=url, before=cls.summary, after=hedged)
                    cls.summary = hedged

                row_id = int(pb_row["id"].removeprefix("SMI-")) if pb_row else next_id(queue + incidents)
                row = make_row(row_id, cls, c, coding)
                log.write(stage="classify", url=url, qualifies=True, row=row)
                pool_rows = incidents + _open_rows(queue)
                try:
                    dup = check_duplicate(client, row, pool_rows)
                except anthropic.APIError as e:
                    print(f"  dedupe call failed ({e}); treating as new")
                    dup = None
                match = next((r for r in pool_rows if dup and r["id"] == dup.matching_id), None)
                stored_urls.add(canonical_url(url))
                if path := syndication_path(url):
                    stored_paths.add(path)

                if dup and dup.relation == "same_incident" and match is not None:
                    log.write(stage="dedupe", url=url, relation="same_incident", matching_id=match["id"])
                    counts["duplicates"] += 1
                    if not any(match is r for r in incidents):
                        # Both reports are still unreviewed: fold this one in.
                        merge_into(match, row)
                        retire(pb_row, f"merged into {match['id']} (same incident)")
                        print(f"  same incident as {match['id']} (in review); merged")
                        continue
                    # The incident is already accepted: queue this report so
                    # the reviewer decides; accepting it merges it in.
                    row["duplicate_of"] = match["id"]
                    row["review_note"] = (f"Same incident as {match['id']}, already accepted: "
                                          f"accepting merges this report into it. {row['review_note']}")
                    print(f"  same incident as accepted {match['id']}; queued to merge")

                elif dup and dup.relation == "same_person_new_incident" and match is not None:
                    if ages_consistent(row, match):
                        if match.get("subject_key"):
                            row["subject_key"] = match["subject_key"]
                        log.write(stage="dedupe", url=url, relation="same_person_new_incident",
                                  matching_id=match["id"])
                        print(f"  same person as {match['id']}, new incident")
                        counts["same_person"] += 1
                    else:
                        log.write(stage="dedupe", url=url, relation="unrelated",
                                  matching_id=match["id"], reason="ages inconsistent")

                if pb_row:
                    queue[queue.index(pb_row)] = row   # same id, now classified
                else:
                    queue.append(row)
                print(f"  QUEUED: {row['subject_name'] or '(unnamed)'} | {row['city']}, "
                      f"{row['state']} | {row['subject_role']} | {row['incident_form']}")
                counts["new"] += 1
            if checkpoint:
                checkpoint()
    if to_fetch:
        print(f"\nClassified {len(to_fetch)} in {int(time.monotonic() - t0)}s")
    return counts
