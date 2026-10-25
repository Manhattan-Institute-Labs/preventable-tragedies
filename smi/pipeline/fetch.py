"""Discover candidate news articles about incidents involving people with
serious mental illness, and fetch their text.

Adapted from ../database/pipeline/fetch.py (itself ported from
flock-crime-tracker). Copied rather than imported, because that module reads
the repeat-offender queries from its own config at import time. Changes:
  - queries and noise domains come from this tracker's config;
  - Google News searches carry after:/before: date operators on every run,
    not only in a backfill, so --days limits Google results too;
  - fetched text is cached in data/articles/, so a --no-llm run and a later
    classified run read an article once.
"""

import base64
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import feedparser
import requests
import trafilatura
from googlenewsdecoder import gnewsdecoder
from trafilatura.settings import use_config

from config import (ARTICLE_CACHE_DIR, GDELT_QUERIES, GOOGLE_NEWS_QUERIES, QUERY_STATS_JSON,
                    SKIP_DOMAINS, SKIP_URL_FRAGMENTS)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAX_RECORDS = 250
# Plain browser UA: GDELT's front end rejects non-browser user agents.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

GDELT_STATS = {"calls": 0, "retries": 0, "gave_up": 0}

# The daily run gives up on GDELT fast; a backfill that depends on it can set
# this to wait out 429s.
GDELT_PATIENT = False

# query -> {"gdelt": n, "gdelt_capped": bool, "gnews": n} for the current run.
QUERY_HITS: dict[str, dict] = {}


def is_vendor_or_wire(url: str, domain: str = "") -> bool:
    """True for press releases, wire syndication, and blotter aggregators."""
    low = (url or "").lower()
    host = urlsplit(low).netloc if "://" in low else ""
    if any(skip in host for skip in SKIP_DOMAINS):
        return True
    if domain:
        hay = domain.lower()
        stems = [d.rsplit(".", 1)[0] for d in SKIP_DOMAINS]
        if any(skip in hay for skip in SKIP_DOMAINS) or any(st in hay for st in stems):
            return True
    return any(k in low for k in SKIP_URL_FRAGMENTS)


def _normalize(candidate: dict) -> dict:
    candidate["url"] = candidate["url"].strip()
    candidate["title"] = re.sub(r"\s+", " ", candidate.get("title", "")).strip()
    return candidate


def _hits(query: str) -> dict:
    return QUERY_HITS.setdefault(query, {"gdelt": 0, "gdelt_capped": False, "gnews": 0})


def gdelt_search(query: str, start: datetime, end: datetime,
                 max_records: int = GDELT_MAX_RECORDS) -> list[dict]:
    """Full-text search of GDELT's news archive for a date window."""
    params = {
        "query": f"{query} sourcelang:english sourcecountry:US",
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "sort": "datedesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    GDELT_STATS["calls"] += 1
    articles = None
    # Two quick attempts on a daily run: from a throttled address, three
    # attempts with 45-135s backoffs cost ~5 minutes per query for nothing.
    attempts = 4 if GDELT_PATIENT else 2
    for attempt in range(attempts):
        try:
            resp = requests.get(GDELT_DOC_API, params=params,
                                headers={"User-Agent": USER_AGENT}, timeout=30)
            if resp.status_code == 429:
                GDELT_STATS["retries"] += 1
                wait = 45 * (attempt + 1) if GDELT_PATIENT else 15
                if GDELT_PATIENT:
                    print(f"    GDELT 429; waiting {wait}s (attempt {attempt + 1}/{attempts})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            try:
                articles = resp.json().get("articles", [])
            except ValueError:
                # GDELT answers invalid or too-slow queries with HTTP 200 and
                # a plain-text message. Show it: it names the problem.
                body = " ".join(resp.text.split())[:200]
                if not body:
                    articles = []  # empty body means no results for this window
                    break
                print(f"  GDELT non-JSON reply for {query[:60]!r}: {body}")
                raise
            break
        except (requests.RequestException, ValueError) as e:
            if not isinstance(e, ValueError):
                print(f"  GDELT error for {query[:60]!r}: {e}")
            time.sleep(30 if GDELT_PATIENT else 5)
    if articles is None:
        GDELT_STATS["gave_up"] += 1
        print(f"  GDELT gave up on {query[:80]!r}")
        return []

    h = _hits(query)
    h["gdelt"] += len(articles)
    if len(articles) >= max_records:
        h["gdelt_capped"] = True
    out = []
    for a in articles:
        domain = a.get("domain", "")
        url = a.get("url", "")
        if is_vendor_or_wire(url, domain):
            continue
        seendate = a.get("seendate", "")  # e.g. 20250811T120000Z
        published = f"{seendate[:4]}-{seendate[4:6]}-{seendate[6:8]}" if len(seendate) >= 8 else ""
        out.append(_normalize({"url": url, "title": a.get("title", ""), "source": domain,
                               "published": published, "snippet": "", "query": query,
                               "via": "gdelt"}))
    return out


GDELT_SLEEP = 8  # GDELT asks for ~1 request per 5s; 6 was not enough
MIN_SPLIT_HOURS = 6


def gdelt_search_split(query: str, start: datetime, end: datetime,
                       depth: int = 0) -> list[dict]:
    """gdelt_search, but when a window returns the full 250-record cap, re-run
    the query on each half of the window so nothing past the cap is lost.
    Stops splitting below MIN_SPLIT_HOURS: a window that dense is a query
    that needs narrowing, not more calls."""
    out = gdelt_search(query, start, end)
    hours = (end - start).total_seconds() / 3600
    if len(out) < GDELT_MAX_RECORDS * 0.9 or hours < MIN_SPLIT_HOURS * 2 or depth >= 4:
        return out
    time.sleep(GDELT_SLEEP)
    mid = start + (end - start) / 2
    left = gdelt_search_split(query, start, mid, depth + 1)
    time.sleep(GDELT_SLEEP)
    right = gdelt_search_split(query, mid, end, depth + 1)
    seen = {c["url"] for c in left}
    return left + [c for c in right if c["url"] not in seen]


def _decode_google_news_url(url: str) -> str | None:
    """Best-effort offline decode of a Google News redirect URL (older format only)."""
    m = re.search(r"news\.google\.com/rss/articles/([^?/]+)", url)
    if not m:
        return None
    try:
        raw = base64.urlsafe_b64decode(m.group(1) + "==")
        text = raw.decode("latin-1", errors="ignore")
        for u in re.findall(r"https?://[^\x00-\x1f\x7f-\xff\"]+", text):
            if "news.google.com" not in u:
                return u.rstrip("R")
    except Exception:
        pass
    return None


def google_news_search(query: str, start: datetime | None = None,
                       end: datetime | None = None) -> list[dict]:
    """Google News RSS search. With start/end, the query carries Google's
    after:/before: date operators. Up to 100 results per query."""
    q = query
    if start and end:
        # before: is exclusive; add a day so today's articles are included.
        q = f"{query} after:{start:%Y-%m-%d} before:{end + timedelta(days=1):%Y-%m-%d}"
    feed = feedparser.parse("https://news.google.com/rss/search?q="
                            f"{quote(q)}&hl=en-US&gl=US&ceid=US:en")
    out = []
    for entry in feed.entries:
        link = entry.get("link", "")
        decoded = _decode_google_news_url(link)
        source = entry.get("source", {}).get("title", "")
        published = ""
        if entry.get("published_parsed"):
            published = time.strftime("%Y-%m-%d", entry.published_parsed)
        snippet = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))
        if is_vendor_or_wire(decoded or "", source.replace(" ", "").lower()):
            continue
        out.append(_normalize({"url": decoded or link, "title": entry.get("title", ""),
                               "source": source, "published": published, "snippet": snippet,
                               "query": query, "via": "googlenews"}))
    _hits(query)["gnews"] += len(out)
    return out


def discover(days_back: int = 3, use_gdelt: bool = True,
             end: datetime | None = None) -> tuple[list[dict], datetime, datetime]:
    """All candidates from the `days_back` days ending at `end` (default now,
    UTC), both sources, deduped by URL. Returns the candidates and the window."""
    end = end or datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=days_back)
    candidates: dict[str, dict] = {}
    t0 = time.monotonic()
    for q in GDELT_QUERIES if use_gdelt else []:
        found = gdelt_search_split(q, start, end)
        for c in found:
            candidates.setdefault(c["url"], c)
        print(f"  GDELT {len(found):4} | {q[:80]}")
        time.sleep(GDELT_SLEEP)
    for q in GOOGLE_NEWS_QUERIES:
        for c in google_news_search(q, start, end):
            candidates.setdefault(c["url"], c)
        time.sleep(1)
    print(f"  Google News: {sum(h['gnews'] for h in QUERY_HITS.values())} items "
          f"across {len(GOOGLE_NEWS_QUERIES)} queries")
    if use_gdelt:
        print(f"  GDELT calls {GDELT_STATS['calls']}, retries {GDELT_STATS['retries']}, "
              f"gave up {GDELT_STATS['gave_up']}")
    print(f"  discovery took {int(time.monotonic() - t0)}s")
    return list(candidates.values()), start, end


def save_query_stats() -> None:
    """Append this run's per-query hit counts to data/query_stats.json, keyed
    by date. Used to prune and split queries."""
    stats = {}
    if QUERY_STATS_JSON.exists():
        stats = json.loads(QUERY_STATS_JSON.read_text(encoding="utf-8"))
    stats[date.today().isoformat()] = QUERY_HITS
    for k in sorted(stats)[:-90]:   # keep the last 90 days
        del stats[k]
    QUERY_STATS_JSON.parent.mkdir(parents=True, exist_ok=True)
    QUERY_STATS_JSON.write_text(json.dumps(stats, indent=1, sort_keys=True), encoding="utf-8")


def resolve_candidate(candidate: dict) -> None:
    """Decode a Google News redirect URL to the real article URL, in place."""
    url = candidate["url"]
    if "news.google.com" not in url:
        return
    try:
        res = gnewsdecoder(url, interval=1) or {}
        # googlenewsdecoder 0.1.x reports {"status": True, ...}; 0.2.x renamed
        # the flag to "success". Trust decoded_url itself.
        decoded = res.get("decoded_url") if res.get("status", res.get("success", True)) else None
        if decoded and "news.google.com" not in decoded:
            candidate["google_url"] = url
            candidate["url"] = decoded.strip()
    except Exception:
        pass


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# A slow site must not stall a worker for trafilatura's default 30s.
FETCH_TIMEOUT = 15
_TRAFILATURA_CONFIG = use_config()
_TRAFILATURA_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(FETCH_TIMEOUT))

MIN_TEXT_CHARS = 500  # a video-page blurb is not an article
VIDEO_PATH_FRAGMENTS = ("/video/", "/videos/", "/watch/", "/clip/")


def fetch_article_text(url: str, max_chars: int = 14000) -> str | None:
    """Download and extract the main text of an article. None on failure.
    Tries trafilatura's fetch first, then a browser-UA request: many station
    sites 403 obvious non-browser agents."""
    if "news.google.com" in url:
        return None
    if any(f in url.lower() for f in VIDEO_PATH_FRAGMENTS):
        return None
    try:
        downloaded = trafilatura.fetch_url(url, config=_TRAFILATURA_CONFIG)
        if not downloaded:
            resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": BROWSER_UA})
            if not resp.ok:
                return None
            downloaded = resp.text
        text = trafilatura.extract(downloaded, include_comments=False)
        if text and len(text) >= MIN_TEXT_CHARS:
            return text[:max_chars]
    except Exception:
        pass
    return None


def _cache_path(url: str):
    return ARTICLE_CACHE_DIR / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.txt"


def fetch_article_text_cached(url: str) -> str | None:
    """fetch_article_text, reading and writing data/articles/. Only successes
    are cached: a paywall or timeout is retried on the next run."""
    path = _cache_path(url)
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = fetch_article_text(url)
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text
