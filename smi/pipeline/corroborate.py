"""Second-source search: find other outlets' reports of each incident.

    python pipeline/run.py corroborate [--limit N] [--max-minutes M]

Adapted from ../database/pipeline/corroborate.py, with one difference: a
report found here is read by the full classifier, not only checked for a
match, because a second outlet often adds history the first one lacked
(a diagnosis, a prior hospitalization, family pleas).

For each qualifying incident that does not yet meet the publication rule
(two outlets, or a trusted first one; sources.py) and has a name, Google
News is searched for the quoted name and the place within a window around
the incident date. Up to MAX_CANDIDATES other outlets are fetched, and
Claude classifies each with the incident as context and says whether it is
the same incident. A match is added to articles.csv already assigned to the
incident; incidents.py then keeps it as the corroboration (and as context
if it adds anything). A match needs no mental-health language of its own.
The incident is stamped corroboration_checked so the search is not
repeated. A run stops early, without stamping, when Google throttles
redirect decoding.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

import smi_coding
from classify import check_evidence, classify_article, hedge_summary
from fetch import fetch_article_text_cached, google_news_search, is_vendor_or_wire, resolve_candidate
from prefilter import is_foreign_outlet
from process import DecisionLog, _stored, canonical_url, syndication_path
from sources import host, outlets, publishable
from store import make_row, next_id

MAX_CANDIDATES = 4      # outlets tried per incident
WORKERS = 4             # incidents in flight
CHECKPOINT_EVERY = 25
THROTTLE_MIN = 20       # decodes attempted before the throttle check applies
THROTTLE_SHARE = 0.8    # ...and the share that must fail to stop the run


class Throttled(RuntimeError):
    pass


def needs_search(row: dict) -> bool:
    return (row.get("qualifies_smi") == "yes" and bool(row.get("subject_name"))
            and not row.get("corroboration_checked") and not publishable(row)
            and not row.get("site_excluded"))


def build_query(row: dict) -> str:
    """Quoted full name plus the city, or the state when no city is known."""
    name = re.sub(r"\s+", " ", row["subject_name"]).strip()
    place = (row.get("city") or "").strip() or (row.get("state") or "").strip()
    return f'"{name}" {place}'.strip()


def date_window(row: dict) -> tuple[datetime | None, datetime | None]:
    """A week before to 45 days after a full date (a month plus 45 days for a
    month); no window for a bare year."""
    v = (row.get("incident_date") or "").strip()
    try:
        if len(v) == 10:
            d = datetime.strptime(v, "%Y-%m-%d")
            return d - timedelta(days=7), d + timedelta(days=45)
        if len(v) == 7:
            d = datetime.strptime(v, "%Y-%m")
            return d - timedelta(days=7), d + timedelta(days=76)
    except ValueError:
        pass
    return None, None


def search_incident(client: anthropic.Anthropic, row: dict, have_hosts: set[str],
                    stored_urls: set[str], stored_paths: set[str], seen: set[str],
                    log: DecisionLog, stats: dict) -> list[tuple[dict, object, dict]]:
    """(candidate, verified classification, phrase-book coding) for each other
    outlet's report of this incident. Raises Throttled when Google refuses
    to decode redirects."""
    start, end = date_window(row)
    found = []
    tried = set(have_hosts)
    for cand in google_news_search(build_query(row), start, end):
        if len(tried) - len(have_hosts) >= MAX_CANDIDATES:
            break
        if "news.google.com" in cand["url"]:
            stats["decodes"] += 1
            resolve_candidate(cand)
            if "news.google.com" in cand["url"]:
                stats["undecoded"] += 1
                if stats["decodes"] >= THROTTLE_MIN and stats["undecoded"] / stats["decodes"] > THROTTLE_SHARE:
                    raise Throttled()
                continue
        url = cand["url"]
        h = host(url)
        if (h in tried or is_foreign_outlet(cand) or is_vendor_or_wire(url, cand.get("source", ""))
                or canonical_url(url) in stored_urls or (syndication_path(url) or "\0") in stored_paths
                or url in seen):
            continue
        tried.add(h)
        text = fetch_article_text_cached(url)
        if not text:
            log.write(stage="corroborate", incident=row["id"], url=url, result="no_text")
            continue
        seen.add(url)
        coding = smi_coding.code(text)
        try:
            cls = classify_article(client, cand, text, coding, incident=row)
        except Exception as e:  # noqa: BLE001
            log.write(stage="corroborate", incident=row["id"], url=url, result=f"error: {e}")
            continue
        if not cls.same_incident:
            log.write(stage="corroborate", incident=row["id"], url=url, same_incident=False,
                      reason=cls.reason)
            continue
        problem, dropped = check_evidence(cls, text, matched=True)
        log.write(stage="corroborate", incident=row["id"], url=url, same_incident=True,
                  problem=problem, dropped=dropped, reason=cls.reason)
        if not problem:
            found.append((cand, cls, coding))
    return found


def corroborate(client: anthropic.Anthropic, incidents: list[dict], articles: list[dict],
                seen: set[str], decision_log: Path | None = None, limit: int = 0,
                max_minutes: float = 0, checkpoint=None) -> dict:
    """Search for second sources on incidents that need them. Appends matched
    articles (assigned to their incident) and stamps corroboration_checked.
    Mutates incidents, articles and seen."""
    log = DecisionLog(decision_log)
    todo = [r for r in incidents if needs_search(r)]
    if limit:
        todo = todo[:limit]
    counts = {"checked": 0, "matched": 0, "articles_added": 0, "throttled": False}
    if not todo:
        return counts
    print(f"Searching for second sources on {len(todo)} of {len(incidents)} incidents")
    stored_urls, stored_paths = _stored(articles)
    by_incident: dict[str, set[str]] = {}
    for a in articles:
        if a.get("incident_id"):
            by_incident.setdefault(a["incident_id"], set()).add(host(a.get("source_url", "")))
    stats = {"decodes": 0, "undecoded": 0}
    t0 = time.monotonic()

    def work(r):
        try:
            return search_incident(client, r, outlets(r) | by_incident.get(r["id"], set()),
                                   stored_urls, stored_paths, seen, log, stats)
        except Throttled:
            return "throttled"
        except Exception as e:  # noqa: BLE001
            print(f"  error on {r['id']}: {e}")
            return None

    today = date.today().isoformat()
    row_id = next_id(articles)
    for i in range(0, len(todo), CHECKPOINT_EVERY):
        if max_minutes and (time.monotonic() - t0) / 60 > max_minutes:
            print("  time budget reached; stopping")
            break
        chunk = todo[i:i + CHECKPOINT_EVERY]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(work, chunk))
        for r, found in zip(chunk, results):
            if found is None or found == "throttled":
                continue
            counts["checked"] += 1
            r["corroboration_checked"] = today
            counts["matched"] += bool(found)
            for cand, cls, coding in found:
                cls.summary = hedge_summary(cls.summary or "", cls.outcome or "", cand.get("source") or "")
                row = make_row(row_id, cls, cand, coding)
                row_id += 1
                row["incident_id"] = r["id"]
                row["review_note"] = f"second-source search for {r['id']}: {cls.reason}"
                articles.append(row)
                counts["articles_added"] += 1
                print(f"  {r['id']} {r['subject_name']}: + {host(cand['url'])}")
        if checkpoint:
            checkpoint()
        if "throttled" in results:
            counts["throttled"] = True
            print("  Google is refusing to decode redirects; stopping (unfinished incidents stay unchecked)")
            break
    return counts
