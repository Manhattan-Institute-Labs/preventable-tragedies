"""The phrase book: deterministic coding of an article's full text.

Carried over from the Known to the System project, where the same rules coded
the 274-incident reference set (reference/reference_incidents.csv). It runs
before Claude: its matches, and the sentence each came from, are passed to the
classifier as hints and kept on the row for review. In a --no-llm run it is
the only coding.

Changes from the version that coded the reference set:
  - prior_hospitalization is gone. Its history patterns ("previously
    hospitalized", "in and out of psychiatric hospitals") now set
    prior_hospital_discharge. Its involuntary-hold patterns ("Baker Act",
    "5150") often describe the current incident, so they only mark an article
    as mental-health related (MH_MARKERS) and set no signal.
  - diagnosis_named needs a named disorder. Generic language ("mental health
    issues", "mentally ill") qualifies an article but is not a diagnosis, and
    neither are symptoms (psychosis, delusions, hearing voices).
  - fatality ignores attempts and threats ("attempted murder", "tried to kill").
  - days_since_release reads spelled-out numbers, and only in sentences that
    mention a release or discharge.
  - "criminal history" counts as prior_arrests.
  - incident forms use the codebook's labels (config.INCIDENT_FORMS).
  - other_party is new.

A phrase book cannot tell whose history a sentence describes: a victim's
record or an earlier case can set a field. That is why every match keeps the
sentence it came from.
"""

import re
import unicodedata

from config import INCIDENT_FORMS, SIGNAL_FIELDS


def clean(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("​", " ").replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", " - ").replace("–", "-")
    return re.sub(r"[ \t]+", " ", unicodedata.normalize("NFKC", s)).strip()


# An article must carry one of these to be about mental health at all. Broader
# than the signals: generic language and involuntary holds count here.
MH_MARKERS = re.compile(
    r"(mental health|mentally ill|mental illness|psychiatric|psychosis|psychotic|"
    r"schizophreni\w*|bipolar|schizoaffective|delusion\w*|paranoi\w*|"
    r"competenc\w*|unfit to stand trial|insanity|not guilty by reason|"
    r"emotionally disturbed|\bEDP\b|crisis intervention|welfare check|wellness check|"
    r"involuntary commitment|civil commitment|involuntar\w* (?:hold|committ\w*)|"
    r"psychiatric hold|Baker Act|Section 12|\b5150\b|"
    r"assisted outpatient|Kendra'?s Law|psych\w*\s+(?:ward|unit|eval\w*)|"
    r"off (?:his|her|their) (?:medication|meds)|mental evaluation)", re.I)

# ...and describe a specific event, not a topic.
EVENT_MARKERS = re.compile(
    r"\b(charg\w*|arrest\w*|arraign\w*|indict\w*|convict\w*|sentenc\w*|accus\w*|"
    r"stabb\w*|shot|shoot\w*|kill\w*|murder\w*|attack\w*|assault\w*|slash\w*|"
    r"standoff|barricad\w*|push\w*|shov\w*|beat\w*|struck|set fire|arson|"
    r"found dead|die[ds]|dead|body|hostage|threaten\w*|missing|"
    r"custody|suspect\w*|incident|police said|deputies|officers|released|discharged)", re.I)

# ---------------------------------------------------------------------------
# Signals. (field, signal code, regex). The signal code is kept in
# trigger_phrases for review; the field is what the CSV records.
# ---------------------------------------------------------------------------
PHRASES = [
    # --- a hospital let them go, or had held them before
    ("prior_hospital_discharge", "B.recent_discharge",
     r"released from (?:a |the )?(?:psychiatric|mental health|mental)[\w ]*(?:hospital|facility|center|unit)"),
    ("prior_hospital_discharge", "B.recent_discharge", r"released from (?:a |the )?hospital"),
    ("prior_hospital_discharge", "B.recent_discharge",
     r"discharged (?:from[\w ]{0,30})?(?:hospital|psychiatric|facility|care)"),
    ("prior_hospital_discharge", "B.recent_discharge", r"recently discharged"),
    ("prior_hospital_discharge", "B.recent_discharge",
     r"released (?:him|her|them)? ?(?:without|with no) (?:follow-?up|requiring)"),
    ("prior_hospital_discharge", "B.recent_discharge",
     r"allowed to sign himself out|signed himself out|escaped (?:the |a )?hospital"),
    ("prior_hospital_discharge", "B.recent_discharge", r"treated and streeted|streeted within hours"),
    ("prior_hospital_discharge", "B.recent_discharge", r"hospital released"),
    ("prior_hospital_discharge", "B.prior_hospitalization",
     r"(?:had been|was previously|previously been|previously|had previously been) hospitali[sz]ed"),
    ("prior_hospital_discharge", "B.prior_hospitalization",
     r"in and out of (?:psychiatric |mental )?(?:hospitals|institutions|treatment)"),
    ("prior_hospital_discharge", "B.prior_hospitalization",
     r"(?:\d+|several|multiple|numerous|prior|previous|past|repeated) (?:prior |previous )?"
     r"(?:psychiatric )?hospitali[sz]ations"),
    ("prior_hospital_discharge", "B.prior_hospitalization",
     r"shunted in and out|cycled in/?out of (?:the )?system"),

    # --- a court or a jail let them go
    ("prior_custody_release", "C.released_from_custody",
     r"released on bail|out on bail|released at a court hearing|out on bond|released on bond"),
    ("prior_custody_release", "C.released_from_custody",
     r"released from (?:jail|prison|custody|Rikers|Sing Sing)"),
    ("prior_custody_release", "C.released_from_custody",
     r"on probation|on parole|parole officer|probation officer|supervised release|pretrial release"),
    ("prior_custody_release", "C.released_from_custody", r"active warrants?"),
    ("prior_custody_release", "C.released_from_custody", r"failed to show up for court|skipped court"),

    # --- police moved them toward care
    ("police_brought_for_eval", "A.police_transport_to_care",
     r"taken (?:to|for)[\w ]{0,25}(?:evaluation|psychiatric|Bellevue|hospital)"),
    ("police_brought_for_eval", "A.police_transport_to_care",
     r"transported (?:for|to)[\w ]{0,30}(?:evaluation|treatment|hospital|clinical)"),
    ("police_brought_for_eval", "A.police_transport_to_care",
     r"brought (?:him|her|them) to the hospital"),
    ("police_brought_for_eval", "A.police_transport_to_care",
     r"police[\w ]{0,20}took (?:him|her|them)[\w ]{0,20}to the hospital"),
    ("police_brought_for_eval", "A.police_transport_to_care",
     r"sent (?:for|to)[\w ]{0,20}(?:a )?(?:mental health|psychiatric|psychological|competency) evaluation"),
    ("police_brought_for_eval", "A.police_transport_to_care", r"safely transported"),
    ("police_brought_for_eval", "A.police_transport_to_care",
     r"ordered (?:for|to undergo)[\w ]{0,25}(?:evaluation|evaluat)"),

    # --- crisis contact
    ("crisis_call", "A.crisis_call",
     r"emotionally disturbed|\bEDP\b|person experiencing an emotional crisis"),
    ("crisis_call", "A.welfare_check", r"wellness check|welfare check|well-being check"),
    ("crisis_call", "A.crisis_call", r"called 911|911 call|dialed 911"),
    ("crisis_call", "A.crisis_call", r"mental health crisis|in active crisis|acute crisis|psychiatric crisis"),
    ("crisis_call", "A.crisis_call", r"standoff|barricad"),

    # --- family tried
    ("family_sought_help", "B.family_sought_help",
     r"family (?:had )?(?:called|sought|begged|pleaded|tried|repeatedly)"),
    ("family_sought_help", "B.family_sought_help",
     r"(?:mother|father|sister|brother|aunt|grandmother|parents?) (?:had )?"
     r"(?:called|sought|begged|pleaded|tried|warned|expressed fear)"),
    ("family_sought_help", "B.family_sought_help", r"begged[\w ]{0,20}(?:hospital|to keep|for help)"),
    ("family_sought_help", "B.family_sought_help",
     r"seeking (?:medical )?(?:assistance|help)|sought help|unsuccessfully (?:trying|tried) to"),
    ("family_sought_help", "B.family_sought_help",
     r"unable to secure (?:necessary )?help|failed attempts to get care"),
    ("family_sought_help", "B.family_sought_help",
     r"turned away|being turned away|denied (?:a )?(?:bed|treatment hold)"),

    # --- medication
    ("medication_lapse", "B.medication_lapse",
     r"(?:stopped|had stopped|off (?:his|her|their)|not taking|non-?compliant with|discontinued)"
     r"[\w ]{0,15}(?:medication|meds)"),
    ("medication_lapse", "B.medication_lapse",
     r"off (?:his|her|their) meds|off medication|unmedicated|medication change|not on his meds"),
    ("medication_lapse", "B.medication_lapse", r"noncompliance|non-compliance"),

    # --- refusal / insight
    ("treatment_refusal", "B.treatment_refusal",
     r"refused (?:voluntary )?(?:treatment|services|to exit|help|medication)"),
    ("treatment_refusal", "B.treatment_refusal",
     r"did not recognize (?:his|her|the) need|prevented him from recognizing|lacked insight"),

    # --- already known to police / courts
    ("prior_police_contact", "C.known_to_police",
     r"known to (?:police|officers|law enforcement)|prior (?:police )?(?:interactions|encounters)|"
     r"similar interactions|prior (?:contacts?|run-ins) with (?:police|officers|law enforcement)|"
     r"(?:police|officers) had (?:been called|responded) to (?:the|his|her) (?:home|house|residence|apartment)"),
    ("prior_arrests", "C.prior_arrests",
     r"(?:multiple|several|prior|previous|history of|\d+) (?:prior )?arrests?"),
    ("prior_arrests", "C.prior_arrests",
     r"arrested more than \d+ times|arrested \d+ times|repeat offender|history of arrests"),
    ("prior_arrests", "C.prior_arrests",
     r"(?<!no )(?<!without a )criminal (?:record|history)|rap sheet"),

    # --- forensic
    ("competency", "C.competency",
     r"competenc(?:y|e)|not competent|unfit to stand trial|competency restoration|Bridgewater State"),
    ("competency", "C.competency", r"did not understand the proceedings|dangerousness hearing"),

    # --- court-ordered outpatient treatment
    ("aot", "B.aot",
     r"Kendra'?s Law|assisted outpatient treatment|court-ordered (?:outpatient )?treatment|mandated treatment"),

    # --- housing
    ("homeless", "D.homeless",
     r"homeless|unhoused|shelter resident|Men'?s Shelter|living on the street|encampment|sleeping on the street"),
]

# Named disorders only. (label, regex); the labels fill the diagnoses column.
DIAGNOSES = [
    ("schizophrenia", r"schizophreni(?:a|c|form)"),
    ("schizoaffective disorder", r"schizoaffective"),
    ("bipolar disorder", r"bipolar(?: 1| I| disorder| II)?"),
    ("postpartum psychosis", r"postpartum psychosis"),
    ("postpartum depression", r"postpartum depression"),
    ("major depressive disorder", r"major depressi(?:on|ve disorder)"),
    ("delusional disorder", r"delusional disorder"),
    ("psychotic disorder", r"psychotic disorder"),
]

# Incident forms, in the order config.INCIDENT_FORMS lists them (most serious
# first). Homicide and fatality read text with attempts and threats removed.
FORMS = [
    ("homicide", r"\bmurder(?:ed|ing|s)?\b|\bkilled\b|\bkilling\b|fatally (?:stabb|shot|beat|struck)|"
                 r"stabbed .{0,30}to death|beaten to death|strangled|homicide|matricide|patricide"),
    ("sexual assault", r"\brape[ds]?\b|sexual(?:ly)? assault"),
    ("shooting", r"\bshot\b|shooting|gunshot|opened fire|fired (?:shots|at)"),
    ("stabbing", r"stabb|slash|\bknife\b|machete|box cutter"),
    ("arson", r"\barson|set .{0,20}fire|molotov"),
    ("vehicular", r"vehicular (?:homicide|assault)|ran (?:him|her|them) over|drove (?:his|her|a) "
                  r"(?:car|vehicle|truck) into|carjack"),
    ("police force non-fatal", r"\btased\b|taser|pepper[- ]spray|bean ?bag|wounded by (?:police|an officer)"),
    ("assault", r"assault|attack|\bbeat(?:ing|en)?\b|punch|lunged|struck|hammer"),
    ("push or shove", r"push(?:ed|ing)? .{0,30}(?:tracks|train|subway|stairs|steps)|shove"),
    ("threat or brandishing", r"threaten|brandish|bomb threat"),
    ("suicide or self-harm", r"suicide|took (?:his|her|their) own life|self-inflicted|self-harm"),
    ("death by overdose", r"overdos"),
    ("death by exposure", r"froze to death|frozen to death|freeze to death|hypotherm|died of exposure"),
    ("found dead", r"found dead|body was found|remains were found"),
    ("missing person", r"reported missing|went missing|missing person"),
    ("property or trespass", r"trespass|vandal|burglar|broke into|criminal mischief"),
]
assert [f for f, _ in FORMS] == [f for f in INCIDENT_FORMS if f != "other"]

# Attempts, threats and negations that would otherwise read as a death.
_NOT_A_DEATH = re.compile(
    r"attempted [\w-]*\s*murder|attempted (?:homicide|killing)|"
    r"(?:tried|trying|attempted|attempting|threatened|threatening|vowed|wanted|plotted) to kill|"
    r"death threats?|non-?fatal|near-?fatal", re.I)
FATAL = re.compile(r"\b(?:killed|killing|murder(?:ed|s)?|died|dead|death|fatal(?:ly)?|homicide|"
                   r"slain|deceased|matricide|patricide)\b", re.I)
VIOLENT = re.compile(r"stabb|\bshot\b|shoot|slash|attack|assault|\bbeat|punch|strangl|murder|\bkill|"
                     r"push|shove|arson|\brape|machete|knife|hammer|lunged|struck|threw", re.I)

KILLED_BY_POLICE = re.compile(
    r"(?:shot and killed|fatally shot|killed) by (?:police|an? (?:police )?(?:officer|deputy|trooper)|"
    r"officers|deputies|troopers)|fatal police (?:shooting|encounter)|"
    r"(?:police|officers?|deputies|deputy|troopers?) (?:fatally shot|shot and killed)|"
    r"died (?:after|following) (?:being )?(?:shot|restrained|tased) by (?:police|officers?|deputies)", re.I)

# subject_role. A phrase book cannot tell who did what to whom: a story about a
# subject who was beaten usually also says someone was arrested. So it only
# calls subject_victim for harm that can only have happened to the subject,
# and leaves the rest to the classifier or the reviewer.
_ACCUSED_LANGUAGE = re.compile(
    r"\b(?:charged|arrested|accused|suspect|indicted|arraigned|convicted|pleaded|booked)\b", re.I)
_SUBJECT_HARMED_ALWAYS = re.compile(
    r"froze to death|frozen to death|freeze to death|died of exposure|"
    r"died (?:while )?(?:in custody|in jail|in prison|in a cell)", re.I)
_SUBJECT_HARMED_UNLESS_ACCUSED = re.compile(
    r"took (?:his|her|their) own life|died by suicide|died of (?:an? )?(?:apparent )?overdose|"
    r"\boverdosed\b", re.I)

# ---------------------------------------------------------------------------
# other_party: who the subject harmed (subject_accused) or who harmed the
# subject (subject_victim). Matched only next to a harm verb: "police" appears
# in nearly every story whoever the other party was.
# ---------------------------------------------------------------------------
_POSS = r"(?:his|her|their) (?:own )?"
PARTY_TERMS = {
    "corrections": r"(?:corrections?|correctional|detention) officers?|jailers?|prison guards?",
    "ems": r"firefighter-paramedics?|firefighters?|paramedics?|EMTs?|"
           r"emergency medical technicians?|first responders?",
    "police": r"(?:police |patrol |state |sheriff'?s? )?(?:officers?|deput(?:y|ies)|troopers?|"
              r"cops?|sergeants?|detectives?)|police",
    "healthcare_worker": r"nurses?|doctors?|physicians?|psychiatrists?|therapists?|"
                         r"hospital (?:workers?|employees?|staff(?:ers)?|security(?: guards?)?)",
    "family_parent": _POSS + r"(?:mother|father|mom|dad|parents?|step(?:mother|father|mom|dad))",
    "family_child": _POSS + r"(?:sons?|daughters?|child|children|kids?|baby|infant|toddler|"
                            r"step(?:sons?|daughters?))",
    "partner": _POSS + r"(?:wife|husband|girlfriend|boyfriend|partner|fianc[ée]e?|spouse|"
                       r"ex-(?:wife|husband|girlfriend|boyfriend))",
    "family_other": _POSS + r"(?:brothers?|sisters?|siblings?|grand(?:mother|father|parents?|son|daughter)|"
                            r"aunt|uncle|cousins?|nephews?|nieces?|relatives?|"
                            r"(?:mother|father|brother|sister)-in-law)",
    "acquaintance": r"neighbou?rs?|friends?|roommates?|room-mates?|co-?workers?|"
                    r"acquaintances?|landlord|landlady|tenants?|housemates?",
    "stranger": r"strangers?|passers?-?by|bystanders?|commuters?|pedestrians?|"
                r"random (?:man|woman|person|people|victims?|motorists?)|motorists?",
}
_HARM = (r"(?:killed|killing|murdered|murdering|stabbed|stabbing|shot|shooting|attacked|attacking|"
         r"assaulted|assaulting|punched|punching|beat|beating|struck|striking|strangled|strangling|"
         r"choked|choking|threatened|threatening|bit|kicked|slashed|slashing|injured|injuring|"
         r"hit|pushed|shoved|rammed|fired at)")
# Up to two words between the verb and the party ("stabbed a firefighter-
# paramedic", "attacked two passing motorists"), but never "by": "shot by
# police" says who shot, not who was shot.
_LEAD = r"(?:(?:a|an|the|two|three|four|several|one|another)\s+)?(?:(?!by\b)[\w-]+\s+){0,2}?"
_PARTY_ACCUSED = {p: re.compile(rf"\b{_HARM}\s+{_LEAD}(?:{t})\b|\b(?:{t})\s+(?:was|were|had been)\s+"
                                rf"(?:\w+\s+)?{_HARM}\b", re.I) for p, t in PARTY_TERMS.items()}
_PARTY_VICTIM = {p: re.compile(rf"\b{_HARM}\s+by\s+{_LEAD}(?:{t})\b", re.I)
                 for p, t in PARTY_TERMS.items()}
_POLICE_PRESENT = re.compile(r"\b(?:police|officers?|deput(?:y|ies)|troopers?)\b", re.I)

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
              "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
              "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30}
_INTERVAL = re.compile(rf"\b(\d{{1,3}}|{'|'.join(_NUM_WORDS)})\s+(hour|day|week|month)s?\b"
                       r"(?=[^.]{0,40}?\b(?:earlier|before|prior|after|later|since)\b)", re.I)
_RELEASE_WORD = re.compile(r"releas|discharg", re.I)
_UNIT_DAYS = {"hour": 0, "day": 1, "week": 7, "month": 30}


def sentence_at(text: str, start: int, end: int, limit: int = 400) -> str:
    """The sentence around text[start:end], trimmed to `limit` characters."""
    left = max(text.rfind(sep, 0, start) for sep in (". ", "? ", "! ", "\n"))
    left = 0 if left < 0 else left + 1
    rights = [i for i in (text.find(sep, end) for sep in (". ", "? ", "! ", "\n")) if i >= 0]
    right = min(rights) + 1 if rights else len(text)
    s = text[left:right].strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "..."


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def days_since_release(text: str) -> int | None:
    """Days between a release and the incident, from the first sentence that
    mentions a release or discharge and gives an interval."""
    fallback = None
    for s in _sentences(text):
        if not _RELEASE_WORD.search(s):
            continue
        m = _INTERVAL.search(s)
        if m:
            raw, unit = m.group(1).lower(), m.group(2).lower()
            n = int(raw) if raw.isdigit() else _NUM_WORDS[raw]
            return n * _UNIT_DAYS[unit]
        if fallback is None and re.search(r"within hours|hours (?:after|before|earlier|later)", s, re.I):
            fallback = 0
    return fallback


def other_party(text: str, role: str, violence: bool) -> str:
    patterns = _PARTY_VICTIM if role == "subject_victim" else _PARTY_ACCUSED
    found = [p for p, rx in patterns.items() if rx.search(text)]
    if not found and role == "subject_accused" and not violence and _POLICE_PRESENT.search(text):
        found = ["police"]  # a crisis response with no one hurt
    if len(found) > 1:
        return "multiple"
    return found[0] if found else ""


def code(text: str) -> dict:
    """Code one article. Returns a dict:
      mh_sentence  the sentence with the first mental-health marker
      signals      {field: sentence that set it}
      triggers     the literal phrases that matched, with their signal codes
      diagnoses    named disorders found
      incident_form, forms, fatality, violence, days_since_release,
      subject_role, other_party, killed_by_police, has_mh, has_event
    """
    t = clean(text)
    signals: dict[str, str] = {}
    triggers: list[str] = []
    for field, sig, rx in PHRASES:
        m = re.search(rx, t, re.I)
        if m:
            signals.setdefault(field, sentence_at(t, m.start(), m.end()))
            triggers.append(f"{sig}: {m.group(0).strip()}")

    diagnoses = []
    for label, rx in DIAGNOSES:
        m = re.search(rx, t, re.I)
        if m:
            diagnoses.append(label)
            signals.setdefault("diagnosis_named", sentence_at(t, m.start(), m.end()))
            triggers.append(f"B.diagnosis_named: {m.group(0).strip()}")

    no_attempts = _NOT_A_DEATH.sub(" ", t)
    forms = [name for name, rx in FORMS
             if re.search(rx, no_attempts if name == "homicide" else t, re.I)]
    fatality = bool(FATAL.search(no_attempts))
    violence = bool(VIOLENT.search(t))

    if _SUBJECT_HARMED_ALWAYS.search(t) or (_SUBJECT_HARMED_UNLESS_ACCUSED.search(t)
                                            and not _ACCUSED_LANGUAGE.search(t)):
        role = "subject_victim"
    else:
        role = "subject_accused"

    assert set(signals) <= set(SIGNAL_FIELDS)
    mh = MH_MARKERS.search(t)
    return {
        "mh_sentence": sentence_at(t, mh.start(), mh.end()) if mh else "",
        "signals": signals,
        "triggers": triggers,
        "diagnoses": diagnoses,
        "incident_form": forms[0] if forms else "",
        "forms": forms,
        "fatality": fatality,
        "violence": violence,
        "days_since_release": days_since_release(t),
        "subject_role": role,
        "other_party": other_party(t, role, violence),
        "killed_by_police": bool(KILLED_BY_POLICE.search(t)),
        "has_mh": bool(MH_MARKERS.search(t)),
        "has_event": bool(EVENT_MARKERS.search(t)),
    }
