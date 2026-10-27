"""Harvest articles, and code them with Claude.

Adapted from ../database/pipeline/process.py.

harvest_articles, per candidate:
  seen-URL check -> headline check (prefilter.py) -> triage (Claude, only
  with an API key) -> Google redirect resolve -> non-US domain check ->
  wire/blotter filter -> same-article URL check -> fetch (cached) -> phrase
  book -> append to articles.csv as pending.
Every article is its own row: several reports of one incident all come in,
and incidents.py groups them later.

classify_articles: Claude re-reads every phrase-book article not rejected in
review (pending, flagged, or accepted) and replaces the phrase-book coding
with its own, verified against the article text. An article Claude finds does
not qualify (outside the US, under 18, not a specific incident) keeps its
review status and gets excluded_reason, which keeps it out of incidents.

Triage runs before URL resolution on purpose: decoding a Google News redirect
costs about a second of HTTP per URL. Every decision goes to a JSONL log in
data/decisions/.
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
from fetch import fetch_article_text_cached, is_vendor_or_wire, resolve_candidate
from prefilter import is_foreign_outlet, prefilter
from store import claude_fields, make_candidate_row, next_id
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
    return {"new": 0, "duplicates": 0, "prefiltered": 0, "foreign": 0, "triaged_out": 0,
            "rejected": 0, "no_text": 0, "no_mh": 0, "no_event": 0, "unresolved": 0,
            "skipped_seen": 0, "errors": 0, "coded": 0, "excluded": 0, "signals_dropped": 0}


def select_candidates(client: anthropic.Anthropic | None, candidates: list[dict],
                      seen_urls: set[str], stored_urls: set[str], stored_paths: set[str],
                      window_start: datetime | None, window_end: datetime | None,
                      log: DecisionLog, counts: dict, max_fetch: int = 0) -> list[dict]:
    """Everything before the article fetch. Returns the candidates to fetch,
    with Google redirects resolved. Mutates seen_urls and counts."""
    fresh = []
    for c in candidates:
        if c["url"] in seen_urls:
            counts["skipped_seen"] += 1
            continue
        reason = prefilter(c, window_start, window_end)
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
        if is_foreign_outlet(c):     # the decoded link is on a non-US domain
            log.write(stage="prefilter", url=url, title=c.get("title"), reason="non-US domain")
            _mark_seen(seen_urls, c)
            counts["foreign"] += 1
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


def harvest_articles(client: anthropic.Anthropic | None, candidates: list[dict],
                     articles: list[dict], seen_urls: set[str],
                     window_start: datetime | None = None, window_end: datetime | None = None,
                     decision_log: Path | None = None, max_fetch: int = 0,
                     checkpoint=None) -> dict:
    """Fetch and phrase-book-code new candidates, appending each article that
    has mental-health language and an event to articles as pending. With a
    client, headlines are triaged by Claude first. Mutates articles and
    seen_urls. Articles with no fetchable text are not marked seen, so they
    are retried."""
    counts = _new_counts()
    log = DecisionLog(decision_log)
    stored_urls, stored_paths = _stored(articles)
    to_fetch = select_candidates(client, candidates, seen_urls, stored_urls, stored_paths,
                                 window_start, window_end, log, counts, max_fetch)
    print(f"Fetching and coding {len(to_fetch)} with {FETCH_WORKERS} workers\n")
    row_id = next_id(articles)
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for start in range(0, len(to_fetch), CHECKPOINT_EVERY):
            chunk = to_fetch[start:start + CHECKPOINT_EVERY]
            for c, res in zip(chunk, pool.map(_fetch_and_code, chunk)):
                if "error" in res:
                    counts["errors"] += 1
                    continue
                if res.get("no_text"):
                    counts["no_text"] += 1
                    log.write(stage="fetch", url=c["url"], result="no_text")
                    continue
                _mark_seen(seen_urls, c)
                coding = res["coding"]
                if not coding["has_mh"] or not coding["has_event"]:
                    key = "no_mh" if not coding["has_mh"] else "no_event"
                    counts[key] += 1
                    log.write(stage="phrasebook", url=c["url"], qualifies=False, reason=key)
                    continue
                articles.append(make_candidate_row(row_id, coding, c))
                log.write(stage="phrasebook", url=c["url"], qualifies=True, id=articles[-1]["id"])
                row_id += 1
                counts["new"] += 1
            if checkpoint:
                checkpoint()
    return counts


def _as_candidate(a: dict) -> dict:
    return {"url": a["source_url"], "title": a.get("headline") or a.get("summary", ""),
            "source": a.get("source_name", ""),
            "published": a.get("published") or a.get("incident_date", "")}


def _code_with_claude(client, a: dict) -> dict:
    c = _as_candidate(a)
    res = _fetch_and_code(c)
    if "text" not in res:
        return res
    if not res["coding"]["has_mh"]:
        return {**res, "reason": "no mental-health language in article"}
    try:
        res["cls"] = classify_article(client, c, res["text"], res["coding"])
    except Exception as e:  # noqa: BLE001
        return {"error": e}
    return res


def classify_articles(client: anthropic.Anthropic, articles: list[dict],
                      decision_log: Path | None = None, checkpoint=None, limit: int = 0) -> dict:
    """Claude re-codes every phrase-book article not rejected in review.
    Mutates articles in place; review status is never changed."""
    counts = _new_counts()
    log = DecisionLog(decision_log)
    todo = [a for a in articles
            if a.get("coded_by") == "phrasebook" and a.get("review_status") != "rejected"]
    if limit:
        todo = todo[:limit]
    print(f"Classifying {len(todo)} phrase-book articles with Claude\n")
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for start in range(0, len(todo), CHECKPOINT_EVERY):
            chunk = todo[start:start + CHECKPOINT_EVERY]
            for a, res in zip(chunk, pool.map(lambda a: _code_with_claude(client, a), chunk)):
                if "error" in res:
                    print(f"  {a['id']} error (retried next run): {res['error']}")
                    counts["errors"] += 1
                    continue
                if res.get("no_text"):
                    counts["no_text"] += 1   # left as phrase-book; retried next run
                    continue
                a["coded_by"] = "claude"
                if "cls" not in res:
                    a["excluded_reason"] = res["reason"]
                    counts["excluded"] += 1
                    continue
                cls, text = res["cls"], res["text"]
                problem = None if cls.qualifies else f"does not qualify: {cls.reason}"
                dropped = []
                if not problem:
                    problem, dropped = check_evidence(cls, text)
                if problem:
                    a["excluded_reason"] = problem
                    a["review_note"] = cls.reason or ""
                    log.write(stage="classify", id=a["id"], url=a["source_url"], qualifies=False,
                              reason=problem, classification=cls.model_dump())
                    print(f"  {a['id']} excluded: {problem[:100]}")
                    counts["excluded"] += 1
                    continue
                if dropped:
                    log.write(stage="verify", id=a["id"], dropped=dropped)
                    counts["signals_dropped"] += len(dropped)
                if cls.incident_date and cls.incident_date[:10] > _date_horizon():
                    cls.incident_date = None   # a misread date
                cls.summary = hedge_summary(cls.summary or "", cls.outcome or "", a.get("source_name") or "")
                a.update(claude_fields(cls, _as_candidate(a)))
                a["excluded_reason"] = ""
                log.write(stage="classify", id=a["id"], url=a["source_url"], qualifies=True)
                print(f"  {a['id']} coded: {a['subject_name'] or '(unnamed)'} | {a['city']}, "
                      f"{a['state']} | {a['subject_role']} | {a['incident_form']}")
                counts["coded"] += 1
            if checkpoint:
                checkpoint()
    return counts
