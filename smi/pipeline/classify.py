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

The phrase book (smi_coding.py) runs first; its matches are given to the
model as hints to confirm or reject, never as answers.
"""

import json
import re
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, ValidationError, field_validator

from config import (CLASSIFY_MODEL, EVIDENCE_TIERS, FAILURE_POINTS, INCIDENT_FORMS, MIN_AGE,
                    OTHER_PARTIES, RESPONSIBLE_SYSTEMS, SIGNAL_FIELDS, SUBJECT_ROLES, US_STATES)

SubjectRole = Literal[tuple(SUBJECT_ROLES)]          # type: ignore[valid-type]
OtherParty = Literal[tuple(OTHER_PARTIES)]           # type: ignore[valid-type]
IncidentForm = Literal[tuple(INCIDENT_FORMS)]        # type: ignore[valid-type]
FailurePoint = Literal[tuple(FAILURE_POINTS)]        # type: ignore[valid-type]
ResponsibleSystem = Literal[tuple(RESPONSIBLE_SYSTEMS)]  # type: ignore[valid-type]
EvidenceTier = Literal[tuple(EVIDENCE_TIERS)]        # type: ignore[valid-type]


class IncidentClassification(BaseModel):
    qualifies: bool
    reason: str
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
    mh_evidence_quote: Optional[str] = None
    diagnoses: Optional[str] = None
    signal_quotes: dict[str, str] = {}       # signal field -> verbatim sentence
    days_since_release: Optional[int] = None
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

    @field_validator("age", "days_since_release", mode="before")
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
  crisis_call: a 911 or crisis call about the subject's mental state, a welfare or wellness check, a standoff, or an "emotionally disturbed person" call.
  family_sought_help: family or friends tried to get the subject help, or were turned away.
  medication_lapse: the subject had stopped or was not taking psychiatric medication.
  treatment_refusal: the subject refused treatment or services.
  homeless: the subject was homeless or living in a shelter or on the street.
  diagnosis_named: the article names a specific disorder (schizophrenia, schizoaffective disorder, bipolar disorder, major depressive disorder, postpartum psychosis, delusional disorder). Generic language ("mental health issues") and symptoms (psychosis, delusions, hearing voices) do NOT count.
- diagnoses: the disorders the article names, separated by semicolons. Blank if none.
- days_since_release: whole days between the subject's release (hospital or custody) and the incident, ONLY when the article gives that interval. Hours count as 0.
- failure_point: the system failure the article supports, one of: {", ".join(FAILURE_POINTS)}. Use "failure not established" rather than inventing one; most articles will not support a failure point.
- responsible_system: one of: {", ".join(RESPONSIBLE_SYSTEMS)}.
- evidence_tier: T1 if a court record, official report or medical examiner is cited; T2 if a police or agency statement is attributed; T3 if family, an attorney or a witness says it; T4 if behaviour is described with no attribution.
- outcome: short phrase for where the case stands: "arrested", "charged", "convicted", "sentenced", "hospitalized", "died", "missing", "no charges".
- summary: 1-2 factual sentences: who, what happened, and what the article says systems knew beforehand. Unless the article reports a conviction or guilty plea, describe an alleged act as an allegation: "is accused of", "was charged with", "allegedly", "according to police". Never state an unproven act as fact, and do not characterise the person.
- confidence: "high" if the article is explicit, "medium" if reasonably clear, "low" if you are inferring.

The message may list phrase-book matches: automated keyword hits with the sentence each came from. They are hints, often wrong (the sentence may be about someone else or another event). Include a signal only if the article supports it for THIS subject.

Always give a one-sentence reason for your decision.

Reply with ONLY a JSON object, no prose and no code fence, matching this JSON schema exactly (use null for unknown optional fields):
{_SCHEMA_JSON}"""


def _hints(coding: dict | None) -> str:
    if not coding or not coding.get("signals"):
        return ""
    lines = [f"- {field}: {sentence}" for field, sentence in coding["signals"].items()]
    return "\n\nPhrase-book matches (hints only):\n" + "\n".join(lines)


def classify_article(client: anthropic.Anthropic, candidate: dict, article_text: str,
                     coding: dict | None = None) -> IncidentClassification:
    user_msg = (
        f"Headline: {candidate.get('title')}\n"
        f"Outlet: {candidate.get('source')}\n"
        f"Published: {candidate.get('published')}\n\n"
        f"Full article text:\n\n{article_text}{_hints(coding)}"
    )
    response = client.messages.create(
        model=CLASSIFY_MODEL,
        max_tokens=2048,
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


def check_evidence(cls: IncidentClassification, text: str) -> tuple[str | None, list[str]]:
    """(rejection reason or None, signals dropped for unverifiable quotes).
    Mutates cls: drops unverified signals and an unsupported days value.
    A subject killed by police is not a rejection: it is recorded in
    killed_by_police for the criminal-offending restrictions to decide."""
    if not quote_in_text(cls.mh_evidence_quote, text):
        return "mh_evidence_quote not found in article", []
    if cls.state and cls.state not in US_STATES:
        return f"state {cls.state!r} is not a US state", []
    if cls.age is not None and cls.age < MIN_AGE:
        return f"subject is {cls.age}, under {MIN_AGE}", []
    if cls.subject_role is None:
        return "no subject_role", []
    dropped = [f for f, q in cls.signal_quotes.items() if not quote_in_text(q, text)]
    for f in dropped:
        del cls.signal_quotes[f]
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
