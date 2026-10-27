"""Stage 2: full-text classification and extraction with Claude Haiku.

Decides whether an article describes a specific US incident involving a
person with an indication of serious mental illness, and extracts the
structured fields. Follows ../database/pipeline/classify.py:

  - The model answers with a JSON object in plain text, validated by
    pydantic. The repeat-offender pipeline found the API's structured-output
    mode failed with "Schema is too complex" on a schema this size; plain
    JSON has no such limit. Unknown enum values are coerced, not fatal.
  - Quotes are the accuracy guard. mh_evidence_quote (the sentence showing
    the mental-health indication) must appear verbatim in the article or the
    row is rejected. Each system-contact signal is true only with its own
    verbatim quote; a signal whose quote is not in the article is dropped,
    and the row is kept.
  - The prior-record fields use the repeat-offender database's definitions
    and checks: a count the article never prints, or a prior-record or
    release quote not in the article, is dropped (that database rejects the
    row; here the record is optional, so only the claim goes).

The phrase book (smi_coding.py) runs first; its matches are given to the
model as hints to confirm or reject, never as answers.

Given a known incident (the second-source search in corroborate.py), the
model also says whether the article reports that same incident. A matching
article is kept even with no mental-health language of its own: it confirms
the incident and may add details.
"""

import json
import re
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, ValidationError, field_validator

from config import (CLASSIFY_MODEL, EVIDENCE_TIERS, FAILURE_POINTS, INCIDENT_FORMS, MIN_AGE,
                    NEW_OFFENSE_TYPES, OFFENSE_SEVERITIES, OTHER_PARTIES, RELEASE_STATUSES,
                    RESPONSIBLE_SYSTEMS, SIGNAL_FIELDS, STRICT_MIN_ARRESTS, STRICT_MIN_CONVICTIONS,
                    STRICT_MIN_FELONY_CONVICTIONS, SUBJECT_ROLES, US_STATES)

SubjectRole = Literal[tuple(SUBJECT_ROLES)]          # type: ignore[valid-type]
OtherParty = Literal[tuple(OTHER_PARTIES)]           # type: ignore[valid-type]
IncidentForm = Literal[tuple(INCIDENT_FORMS)]        # type: ignore[valid-type]
FailurePoint = Literal[tuple(FAILURE_POINTS)]        # type: ignore[valid-type]
ResponsibleSystem = Literal[tuple(RESPONSIBLE_SYSTEMS)]  # type: ignore[valid-type]
EvidenceTier = Literal[tuple(EVIDENCE_TIERS)]        # type: ignore[valid-type]
NewOffenseType = Literal[tuple(NEW_OFFENSE_TYPES)]   # type: ignore[valid-type]
Severity = Literal[tuple(OFFENSE_SEVERITIES)]        # type: ignore[valid-type]
ReleaseStatus = Literal[tuple(RELEASE_STATUSES)]     # type: ignore[valid-type]


class IncidentClassification(BaseModel):
    qualifies: bool
    reason: str
    same_incident: Optional[bool] = None     # only asked when a known incident is given
    killed_by_police: bool = False
    incident_date: Optional[str] = None      # YYYY-MM-DD, YYYY-MM, or YYYY
    city: Optional[str] = None
    state: Optional[str] = None              # two-letter US postal code
    subject_name: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[Literal["M", "F", "U"]] = None
    subject_role: Optional[SubjectRole] = None
    other_party: Optional[OtherParty] = None
    incident_form: Optional[IncidentForm] = None
    fatality: bool = False
    violence: bool = False
    new_offense_type: Optional[NewOffenseType] = None
    new_offense_severity: Optional[Severity] = None
    mh_evidence_quote: Optional[str] = None
    diagnoses: Optional[str] = None
    signal_quotes: dict[str, str] = {}       # signal field -> verbatim sentence
    days_since_release: Optional[int] = None
    prior_count_arrests: Optional[int] = None
    prior_count_convictions: Optional[int] = None
    prior_count_felony_convictions: Optional[int] = None
    prior_offenses: Optional[str] = None
    prior_evidence_quote: Optional[str] = None
    release_status: ReleaseStatus = "none stated"
    release_evidence_quote: Optional[str] = None
    releasing_jurisdiction: Optional[str] = None
    failure_point: Optional[FailurePoint] = None
    responsible_system: Optional[ResponsibleSystem] = None
    evidence_tier: EvidenceTier = "T4"
    outcome: Optional[str] = None
    summary: Optional[str] = None
    confidence: Literal["high", "medium", "low"] = "low"

    @field_validator("subject_role", mode="before")
    @classmethod
    def _coerce_role(cls, v):
        if v in SUBJECT_ROLES or v is None:
            return v
        return {"accused": "subject_accused", "victim": "subject_victim"}.get(str(v).lower())

    @field_validator("other_party", mode="before")
    @classmethod
    def _coerce_party(cls, v):
        return v if v in OTHER_PARTIES else None

    @field_validator("incident_form", mode="before")
    @classmethod
    def _coerce_form(cls, v):
        return v if v in INCIDENT_FORMS or v is None else "other"

    @field_validator("failure_point", mode="before")
    @classmethod
    def _coerce_failure(cls, v):
        return v if v in FAILURE_POINTS or v is None else "failure not established"

    @field_validator("responsible_system", mode="before")
    @classmethod
    def _coerce_system(cls, v):
        return v if v in RESPONSIBLE_SYSTEMS or v is None else "not established"

    @field_validator("evidence_tier", mode="before")
    @classmethod
    def _coerce_tier(cls, v):
        return v if v in EVIDENCE_TIERS else "T4"

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        return v if v in ("high", "medium", "low") else "low"

    @field_validator("sex", mode="before")
    @classmethod
    def _coerce_sex(cls, v):
        return v if v in ("M", "F", "U") or v is None else "U"

    @field_validator("new_offense_type", mode="before")
    @classmethod
    def _coerce_offense(cls, v):
        return v if v in NEW_OFFENSE_TYPES or v is None else "other"

    @field_validator("new_offense_severity", mode="before")
    @classmethod
    def _coerce_severity(cls, v):
        return v if v in OFFENSE_SEVERITIES or v is None else "other"

    @field_validator("release_status", mode="before")
    @classmethod
    def _coerce_release(cls, v):
        return v if v in RELEASE_STATUSES else "none stated"

    @field_validator("age", "days_since_release", "prior_count_arrests", "prior_count_convictions",
                     "prior_count_felony_convictions", mode="before")
    @classmethod
    def _coerce_int(cls, v):
        if v is None or v == "" or isinstance(v, bool):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @field_validator("state", mode="before")
    @classmethod
    def _coerce_state(cls, v):
        return v.strip().upper() if isinstance(v, str) and v.strip() else None

    @field_validator("signal_quotes", mode="before")
    @classmethod
    def _coerce_signals(cls, v):
        if not isinstance(v, dict):
            return {}
        return {k: q for k, q in v.items()
                if k in SIGNAL_FIELDS and isinstance(q, str) and q.strip()}


class ClassificationParseError(ValueError):
    """The model's reply was not a JSON object matching IncidentClassification."""


def parse_classification(text: str) -> IncidentClassification:
    """Extract the first JSON object from a model reply and validate it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ClassificationParseError(f"no JSON object in reply: {text[:200]!r}")
    try:
        return IncidentClassification.model_validate(json.loads(text[start:end + 1]))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ClassificationParseError(str(e)[:300]) from e


_SCHEMA_JSON = json.dumps(IncidentClassification.model_json_schema(), separators=(",", ":"))


SYSTEM_PROMPT = f"""You classify news articles for a public database of incidents involving people with serious mental illness in the United States, and what public systems knew about them beforehand. The database exists to hold public systems accountable. Record what the article says. Never attach a diagnosis the article does not report.

An article QUALIFIES only if ALL of the following hold:
1. It describes a SPECIFIC incident involving a specific, identifiable person (normally named): the SUBJECT.
2. The article itself gives an indication that the subject has a mental illness: a named diagnosis, generic language ("mental health issues", "mentally ill", "history of mental illness", "mental health crisis"), behaviour it describes in those terms, or a formal system response to a mental-health crisis (psychiatric evaluation, competency proceedings, involuntary hold, crisis call). Copy that sentence as mh_evidence_quote.
3. The incident happened in the United States.

The subject's role is one of two values:
- "subject_accused": the subject is accused of violence, a threat, property crime or disorder, OR is the person in crisis that police or crisis services responded to, even if no crime was alleged and no one was hurt.
- "subject_victim": harm was done to the subject: violence by another person, neglect, or death by exposure, overdose or suicide, death in custody, or going missing.
When the subject both caused and suffered harm, the role follows whoever is alleged to have STARTED the harm.

An article does NOT qualify if it is:
- An opinion piece, editorial, column or explainer, even on exactly this subject.
- Policy, legislative, budget or council coverage, or a research study or statistic with no specific incident.
- A story with no mental-health indication for the subject. A victim's grief or trauma does not count.
- Purely about substance use, or primarily about dementia or developmental disability.
- A story whose subject is under 18.
- A story about an incident outside the United States.
- A police blotter, arrest log, or list of unrelated bookings.

Field guidance:
- incident_date: date of the incident, as specific as the article allows (YYYY-MM-DD, YYYY-MM or YYYY). Use the publication date only if nothing better is stated.
- state: two-letter US postal code. Always provide it when the article names or implies any location. Never infer it from the outlet's name: station groups syndicate stories nationally.
- subject_name: ONE person, full name as printed. Blank if the article does not name the subject. age: as stated, else blank. sex: M, F or U, for the subject only.
- other_party: who the other person was. When subject_accused: the person the subject harmed or threatened, or "police" for a crisis response with no one harmed. When subject_victim: the person who harmed the subject. One of: {", ".join(OTHER_PARTIES)}. "partner" includes spouses, girlfriends and boyfriends; "family_parent" is the subject's parent, "family_child" the subject's child; "acquaintance" is someone the subject knew who is not family or a partner (a neighbor, friend, roommate, coworker, landlord); "stranger" is someone the subject did not know. Use "multiple" only for more than one TYPE of party (two strangers are "stranger"). Null when there was no other party or the article does not say.
- incident_form: the most serious form, one of: {", ".join(INCIDENT_FORMS)}.
- fatality: true if ANYONE involved died, subject or other party. Attempted murder, threats to kill, and non-fatal injuries are not fatalities. violence: true if anyone used or attempted physical violence.
- killed_by_police: true if the subject was killed by police or other law enforcement (including a deputy, trooper, bailiff or court officer). These incidents QUALIFY: record them, and set the role by who started the harm.
- signal_quotes: an object whose keys are the system-contact signals the article states, each mapped to the ONE sentence copied VERBATIM, character for character, that states it. Omit any signal the article does not state; an omitted signal means the article did not say, not that it did not happen. Signals:
  prior_hospital_discharge: a hospital or psychiatric facility released the subject before the incident, or the subject had been hospitalized for mental illness before (someone hospitalized before was discharged).
  prior_custody_release: a court, jail or prison released the subject before the incident: bail, bond, probation, parole, pretrial or supervised release, release from jail or prison. Never merge this with prior_hospital_discharge: different agencies, different decisions.
  police_brought_for_eval: police, crisis workers or a court sent the subject for a psychiatric evaluation or to a hospital. This is the system working, and it counts.
  prior_police_contact: police had dealt with the subject before (prior calls, prior encounters, "known to police").
  prior_arrests: the subject had prior arrests, charges, convictions or a criminal record.
  competency: competency to stand trial was raised, evaluated, or found lacking.
  aot: assisted outpatient treatment, Kendra's Law, or court-ordered outpatient treatment.
  edp_call: police or crisis services responded to a call about the subject described as an "emotionally disturbed person" (EDP) or a "person in emotional crisis", in this incident or before it.
  crisis_call: any other 911 or crisis call about the subject's mental state, a welfare or wellness check, or a standoff.
  family_sought_help: family or friends tried to get the subject help, or were turned away.
  medication_lapse: the subject had stopped or was not taking psychiatric medication.
  psych_medication: the article mentions psychiatric medication for the subject: prescribed, taking, stopped or refused. When medication_lapse is true, this is true too.
  treatment_refusal: the subject refused treatment or services.
  homeless: the subject was homeless or living in a shelter or on the street.
  mh_history: the article says the subject had a mental illness, mental-health problems or psychiatric treatment BEFORE this incident ("a long history of mental illness", "had struggled with schizophrenia for years", "had been diagnosed with bipolar disorder", "was being treated for"). Behaviour during the incident, or an evaluation after it, does not count.
  diagnosis_named: the article names a specific disorder (schizophrenia, schizoaffective disorder, bipolar disorder, major depressive disorder, postpartum psychosis, delusional disorder). Generic language ("mental health issues") and symptoms (psychosis, delusions, hearing voices) do NOT count.
- diagnoses: the disorders the article names, separated by semicolons. Blank if none.
- days_since_release: whole days between the subject's release (hospital or custody) and the incident, ONLY when the article gives that interval. Hours count as 0.
- new_offense_type: the most serious offense the subject was arrested for, charged with or accused of in this incident, one of: {", ".join(NEW_OFFENSE_TYPES)}. new_offense_severity: one of {", ".join(OFFENSE_SEVERITIES)}. Both null when the subject is not accused of a crime (the subject was the one harmed, or a crisis with no crime alleged).
- The subject's PRIOR criminal record and release status, as the article states them:
  prior_count_arrests, prior_count_convictions, prior_count_felony_convictions: ONLY if the article states a number. "More than a dozen arrests" is 13; "nearly two dozen" is 24; "half a dozen" is 6. "Dozens" or "numerous" is null. "N prior charges" counts as N arrests. "N-time convicted felon" means prior_count_felony_convictions = N. A charge styled "Nth offense" documents N-1 prior convictions. Never estimate beyond what the words say, and never count the charges in THIS incident.
  prior_offenses: the prior offenses the article names, separated by semicolons. Blank if none.
  prior_evidence_quote: copy VERBATIM the one sentence that best documents the prior record. Required whenever a count or prior_offenses is given.
  release_status: the subject's supervision or release status AT THE TIME OF THIS INCIDENT, from an EARLIER case, one of: {", ".join(RELEASE_STATUSES)}. Bail or bond set AFTER this arrest is the current case, not a release status: use "none stated".
  release_evidence_quote: copy VERBATIM the sentence documenting that release status. Required whenever release_status is not "none stated".
  releasing_jurisdiction: the county, city, state or court that released the subject, if named.
  When you give a prior record, also give the prior_arrests signal; when you give a release status, also give prior_custody_release.
- failure_point: the system failure the article supports, one of: {", ".join(FAILURE_POINTS)}. Use "failure not established" rather than inventing one; most articles will not support a failure point.
- responsible_system: one of: {", ".join(RESPONSIBLE_SYSTEMS)}.
- evidence_tier: T1 if a court record, official report or medical examiner is cited; T2 if a police or agency statement is attributed; T3 if family, an attorney or a witness says it; T4 if behaviour is described with no attribution.
- outcome: short phrase for where the case stands: "arrested", "charged", "convicted", "sentenced", "hospitalized", "died", "missing", "no charges".
- summary: 1-2 factual sentences: who, what happened, and what the article says systems knew beforehand. Unless the article reports a conviction or guilty plea, describe an alleged act as an allegation: "is accused of", "was charged with", "allegedly", "according to police". Never state an unproven act as fact, and do not characterise the person.
- confidence: "high" if the article is explicit, "medium" if reasonably clear, "low" if you are inferring.

The message may list phrase-book matches: automated keyword hits with the sentence each came from. They are hints, often wrong (the sentence may be about someone else or another event). Include a signal only if the article supports it for THIS subject.

The message may end with a DATABASE RECORD of an incident already in the database. Then set same_incident: true only if the article reports the same person AND the same incident, at any stage (arrest, charging, arraignment, plea, sentencing, a later hearing); false for a different incident or a different person with the same name. Be strict: when the article does not clearly match, say false. When same_incident is true, the article qualifies even if it gives no mental-health indication of its own (leave mh_evidence_quote null then); extract everything it says about the subject.

Always give a one-sentence reason for your decision.

Reply with ONLY a JSON object, no prose and no code fence, matching this JSON schema exactly (use null for unknown optional fields):
{_SCHEMA_JSON}"""


def _hints(coding: dict | None) -> str:
    if not coding or not coding.get("signals"):
        return ""
    lines = [f"- {field}: {sentence}" for field, sentence in coding["signals"].items()]
    return "\n\nPhrase-book matches (hints only):\n" + "\n".join(lines)


def _record(incident: dict | None) -> str:
    if not incident:
        return ""
    return ("\n\nDATABASE RECORD (set same_incident):\n"
            f"name: {incident.get('subject_name') or '?'} | age: {incident.get('age') or '?'} | "
            f"place: {incident.get('city')}, {incident.get('state')} | "
            f"incident date: {incident.get('incident_date') or '?'} | "
            f"form: {incident.get('incident_form')} | summary: {incident.get('summary')}")


def classify_article(client: anthropic.Anthropic, candidate: dict, article_text: str,
                     coding: dict | None = None,
                     incident: dict | None = None) -> IncidentClassification:
    """Classify one article. With `incident`, also decide same_incident."""
    user_msg = (
        f"Headline: {candidate.get('title')}\n"
        f"Outlet: {candidate.get('source')}\n"
        f"Published: {candidate.get('published')}\n\n"
        f"Full article text:\n\n{article_text}{_hints(coding)}{_record(incident)}"
    )
    response = client.messages.create(
        model=CLASSIFY_MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    cls = parse_classification(text)
    if cls.subject_name and ";" in cls.subject_name:
        cls.subject_name = cls.subject_name.split(";")[0].strip()  # one person per row
    return cls


# --- verification, copied from ../database/pipeline/classify.py -------------

_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def normalize_for_match(text: str) -> str:
    """Lowercase, straight quotes, collapsed whitespace. Enough to survive
    trafilatura's cleanup and a model that straightens curly quotes; not
    enough to let a paraphrase through."""
    text = (text or "").translate(_QUOTE_MAP).lower()
    text = re.sub(r"[^\w\s'\"$%.,;:!?()/-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str | None, text: str) -> bool:
    """True if the quote appears verbatim (after normalization) in the text.
    A quote under 20 characters is too short to be evidence of anything."""
    q = normalize_for_match(quote or "").strip("\"' ")
    if len(q) < 20:
        return False
    return q in normalize_for_match(text)


_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
          8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
          14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
          19: "nineteen", 20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
          60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety", 100: "hundred"}


def _number_forms(n: int) -> list[str]:
    out = [str(n)]
    if n in _WORDS:
        out.append(_WORDS[n])
    elif 20 < n < 100 and n % 10 in _WORDS:
        out.append(f"{_WORDS[n // 10 * 10]}-{_WORDS[n % 10]}")
        out.append(f"{_WORDS[n // 10 * 10]} {_WORDS[n % 10]}")
    if n == 1:
        out += ["a", "an"]
    return out


def number_in_text(n: int, unit: str, text: str) -> bool:
    """True if the article prints "<n> <unit>" in any common form."""
    low = text.lower()
    for form in _number_forms(n):
        if re.search(rf"(?<![\w.]){re.escape(form)}[\s-]+{unit}s?\b", low):
            return True
    return False


def days_in_text(days: int, text: str) -> bool:
    """A days_since_release value the article supports: hours for 0, or the
    number of days, weeks or months it converts from."""
    if days == 0:
        return bool(re.search(r"\bhours?\b", text, re.I))
    if number_in_text(days, "day", text):
        return True
    if days % 7 == 0 and number_in_text(days // 7, "week", text):
        return True
    return days % 30 == 0 and number_in_text(days // 30, "month", text)


_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
             7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth"}


def _count_forms(n: int) -> list[str]:
    """Ways an article might print a prior count: digits, a number word, an
    ordinal, a dozen. As in ../database/pipeline/classify.py."""
    out = [str(n), f"{n}th", f"{n}st", f"{n}nd", f"{n}rd"]
    if n in _WORDS:
        out.append(_WORDS[n])
    elif 20 < n < 100 and n % 10 in _WORDS:
        out.append(f"{_WORDS[n // 10 * 10]}-{_WORDS[n % 10]}")
        out.append(f"{_WORDS[n // 10 * 10]} {_WORDS[n % 10]}")
    if n in _ORDINALS:
        out.append(_ORDINALS[n])
    if n in (12, 13):
        out.append("dozen")
    if n == 24:
        out.append("two dozen")
    if n == 6:
        out.append("half a dozen")
    return out


def count_in_text(n: int, text: str) -> bool:
    """True if the article states n, or n+1 (an "Nth offense" charge documents
    N-1 priors), in any common form."""
    low = text.lower()
    for m in (n, n + 1):
        for form in _count_forms(m):
            if re.search(rf"(?<![\w.])({re.escape(form)})(?![\w])", low):
                return True
    return False


def qualifies_strict(arrests: int | None, convictions: int | None,
                     felony_convictions: int | None) -> bool:
    """The repeat-offender database's strict threshold: 5+ prior arrests, or
    5+ prior convictions, or 3+ prior felony convictions."""
    return ((arrests or 0) >= STRICT_MIN_ARRESTS
            or (convictions or 0) >= STRICT_MIN_CONVICTIONS
            or (felony_convictions or 0) >= STRICT_MIN_FELONY_CONVICTIONS)


def _check_prior_record(cls: IncidentClassification, text: str) -> list[str]:
    """Drop prior-record claims the article does not support. Returns what
    was dropped. A verified record also sets the matching signal, quoted."""
    dropped = []
    counts = ("prior_count_arrests", "prior_count_convictions", "prior_count_felony_convictions")
    claims_record = any(getattr(cls, f) for f in counts) or bool(cls.prior_offenses)
    if (claims_record or cls.prior_evidence_quote) and not quote_in_text(cls.prior_evidence_quote, text):
        dropped.append("prior_record")
        for f in counts:
            setattr(cls, f, None)
        cls.prior_offenses = cls.prior_evidence_quote = None
    for f in counts:
        n = getattr(cls, f)
        if n and not count_in_text(n, text):
            dropped.append(f"{f}={n}")
            setattr(cls, f, None)
    if cls.release_status != "none stated" and not quote_in_text(cls.release_evidence_quote, text):
        dropped.append(f"release_status={cls.release_status}")
        cls.release_status, cls.release_evidence_quote = "none stated", None
    if cls.release_status == "none stated":
        cls.release_evidence_quote = cls.releasing_jurisdiction = None
    if cls.prior_evidence_quote:
        cls.signal_quotes.setdefault("prior_arrests", cls.prior_evidence_quote)
    if cls.release_evidence_quote:
        cls.signal_quotes.setdefault("prior_custody_release", cls.release_evidence_quote)
    return dropped


def check_evidence(cls: IncidentClassification, text: str,
                   matched: bool = False) -> tuple[str | None, list[str]]:
    """(rejection reason or None, claims dropped for unverifiable quotes).
    Mutates cls: drops unverified signals, prior-record claims and an
    unsupported days value. A subject killed by police is not a rejection.
    `matched`: the article was confirmed as another report of a known
    incident, so it needs no mental-health quote of its own."""
    if not quote_in_text(cls.mh_evidence_quote, text):
        if not matched:
            return "mh_evidence_quote not found in article", []
        cls.mh_evidence_quote = None
    if cls.state and cls.state not in US_STATES:
        return f"state {cls.state!r} is not a US state", []
    if cls.age is not None and cls.age < MIN_AGE:
        return f"subject is {cls.age}, under {MIN_AGE}", []
    if cls.subject_role is None:
        return "no subject_role", []
    dropped = [f for f, q in cls.signal_quotes.items() if not quote_in_text(q, text)]
    for f in dropped:
        del cls.signal_quotes[f]
    dropped += _check_prior_record(cls, text)
    if "medication_lapse" in cls.signal_quotes:
        cls.signal_quotes.setdefault("psych_medication", cls.signal_quotes["medication_lapse"])
    if cls.days_since_release is not None and not days_in_text(cls.days_since_release, text):
        dropped.append(f"days_since_release={cls.days_since_release}")
        cls.days_since_release = None
    return None, dropped


CONVICTED_RE = re.compile(r"convict|sentenc|plead|guilty|found guilty", re.I)
HEDGE_RE = re.compile(r"alleged|accus|charged|arrested|indicted|suspect|according to|"
                      r"police sa|prosecutors|authorities|reportedly|booked|arraigned", re.I)


def hedge_summary(summary: str, outcome: str, outlet: str) -> str:
    """Attribute an unproven allegation. If the case is not a conviction and
    the summary has no allegation language, prefix an attribution to the
    outlet: "According to WXYZ, ..." is true whatever the facts turn out to be."""
    if not summary or CONVICTED_RE.search(outcome or "") or HEDGE_RE.search(summary):
        return summary
    return f"According to {outlet or 'the cited report'}, {summary}"
