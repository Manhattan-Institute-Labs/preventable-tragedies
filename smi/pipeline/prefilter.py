"""Stage 0: a free headline check before triage, fetching, or any model call.

It only throws out what is clearly irrelevant: an article published before the
search window, a foreign local outlet, or a headline that names its genre as
opinion, policy, or research. It never requires mental-health or incident
words in the headline: headlines rarely carry them, and a headline-only rule
rejected real cases ("Man stabbed his scientist wife... as his mother
'begged' him to stop"). Those judgments are made on the full text.

Non-US links are excluded: a URL on any two-letter country-code domain
other than .us (.uk, .au, .ca, .gr and so on; a few codes US outlets use as
generic names, such as .io and .tv, are allowed), and a short list of
foreign outlets that Google News reports by name. A Google News link only shows its real domain after it is
decoded, so process.py runs is_foreign_outlet again then. The classifier
still rejects a non-US incident reported by a US outlet.
"""

import re
from datetime import datetime
from urllib.parse import urlsplit

GENRE_EXCLUDE = re.compile(
    r"\b(op-?ed|opinion|editorial|commentary|guest column|letters? to the editor|"
    r"analysis:|explainer|study (?:finds|shows|suggests)|researchers (?:say|find|found)|"
    r"survey (?:finds|shows)|report finds|systematic review|meta-analysis|"
    r"city council|town council|county commission|"
    r"board of supervisors|budget (?:hearing|proposal|cuts?)|legislature|bill would|"
    r"(?:advance|advances|pass|passes|signs|introduce|introduces) (?:a )?(?:bill|legislation)|"
    r"lawmakers|ballot measure|ribbon[- ]cutting|grand opening|job fair|webinar|"
    r"calls (?:to|for) service|police (?:blotter|log|reports? for the week)|arrest log|"
    r"jail (?:log|bookings)|booking report|"
    r"how to (?:help|cope|talk)|tips for|awareness (?:month|week|day)|"
    r"obituary|wedding|horoscope|recipe|box score)\b", re.I)

# Every two-letter country-code domain except .us is foreign, apart from the
# few that US outlets use as generic names.
US_USED_CCTLDS = {"us", "io", "tv", "co", "me", "ly", "fm", "ai", "am"}


def _foreign_domain(host: str) -> bool:
    host = host.split(":")[0].rstrip(".")
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    return len(tld) == 2 and tld.isalpha() and tld not in US_USED_CCTLDS

# Foreign local outlets as Google News names them. National outlets that cover
# US news heavily (Daily Mail, The Independent, The Guardian) are not here.
FOREIGN_OUTLETS = {
    "bbc", "bbc news", "sky news", "rte.ie", "rte", "the irish times", "irish independent",
    "cbc", "cbc news", "ctv news", "global news", "toronto star", "nz herald", "rnz",
    "abc (australia)", "abc news (australia)", "9news", "nine.com.au", "news.com.au",
    "manchester evening news", "wales online", "walesonline", "the scotsman", "the herald",
    "liverpool echo", "birmingham live", "the times of india", "hindustan times",
    "the news minute", "ndtv",
    # Foreign outlets on .com/.net/.tv domains, seen in the first harvests.
    "yorkshire live", "stv news", "news24", "the business standard", "starts at 60",
    "the nightly", "leadership newspapers", "irish mirror",
}


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower() if "://" in (url or "") else ""


def is_foreign_outlet(candidate: dict) -> bool:
    host = _host(candidate.get("url", ""))
    source = (candidate.get("source") or "").strip().lower()
    if host and "news.google.com" not in host and _foreign_domain(host):
        return True
    if " " not in source and _foreign_domain(source):   # GDELT reports the domain as the source
        return True
    return source in FOREIGN_OUTLETS


def prefilter(candidate: dict, window_start: datetime | None = None,
              window_end: datetime | None = None) -> str | None:
    """A rejection reason, or None to keep the candidate."""
    published = candidate.get("published") or ""
    if window_start and published and published < window_start.strftime("%Y-%m-%d"):
        return "published before the search window"
    if window_end and published and published > window_end.strftime("%Y-%m-%d"):
        return "published after the search window"
    if is_foreign_outlet(candidate):
        return "foreign outlet"
    title = re.sub(r"\s+-\s+[^-]+$", "", candidate.get("title") or "")  # drop " - Outlet"
    if GENRE_EXCLUDE.search(title):
        return "opinion, policy, or research headline"
    return None
