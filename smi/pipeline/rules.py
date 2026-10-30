"""The inclusion rule for the "Serious mental illness" category.

An incident qualifies (qualifies_smi = yes) when:
  - it happened in the United States (a US state is given);
  - the subject is 18 or older (or the age is not stated);
  - the articles quote at least one EARLIER system contact: a prior
    psychiatric hospitalization, a release from custody, police taking the
    subject for evaluation, prior police contact, a prior arrest or record,
    court-ordered treatment, an "emotionally disturbed person" call, family
    seeking help, psychiatric medication, refused treatment, a stated history
    of mental illness, or a named diagnosis (config.QUALIFYING_SIGNALS), or a
    prior record or release status in the repeat-offender fields;
  - there was harm: violence or a death, whoever caused or suffered it, or
    else an arrest, charge or conviction for anything, worded as the
    repeat-offender database's outcome rule (STORED_OUTCOME_RE).
The subject may have caused the harm or suffered it (subject_role); both
count. Corroboration (two outlets or a trusted one) decides publication, not
qualification: see corroborate.py.

Serious mental illness is a category of its own in the one record set
(../database/pipeline/records.py): a qualifying incident is never also
violent or petty, whatever the subject's record. qualifies_strict records
whether the stated counts meet the repeat-offender threshold, for the data,
not for a second category.
"""

import re

from classify import qualifies_strict
from config import MIN_AGE, QUALIFYING_SIGNALS, STORED_OUTCOME_RE, US_STATES


def _int(v) -> int | None:
    v = str(v or "").strip()
    return int(v) if v.isdigit() else None


def earlier_contacts(row: dict) -> list[str]:
    """The earlier system contacts an incident's articles quote."""
    out = [f for f in QUALIFYING_SIGNALS if row.get(f) == "true"]
    if row.get("prior_evidence_quote") and "prior_arrests" not in out:
        out.append("prior record")
    if row.get("release_status") not in ("", None, "none stated") and "prior_custody_release" not in out:
        out.append("release status")
    return out


def arrested(row: dict) -> bool:
    return bool(re.search(STORED_OUTCOME_RE, row.get("outcome") or "", re.I))


def harm(row: dict) -> bool:
    return row.get("violence") == "true" or row.get("fatality") == "true" or arrested(row)


def qualifies_smi(row: dict) -> tuple[bool, str]:
    """(qualifies, note): the contacts that qualify it, or why it does not."""
    if (row.get("state") or "") not in US_STATES:
        return False, "no US state"
    age = _int(row.get("age"))
    if age is not None and age < MIN_AGE:
        return False, f"subject is {age}, under {MIN_AGE}"
    contacts = earlier_contacts(row)
    if not contacts:
        return False, "no earlier system contact quoted"
    if not harm(row):
        return False, "no violence, death or arrest"
    return True, "; ".join(contacts)


def strict(row: dict) -> bool:
    return qualifies_strict(_int(row.get("prior_count_arrests")),
                            _int(row.get("prior_count_convictions")),
                            _int(row.get("prior_count_felony_convictions")))


def apply_rules(row: dict) -> None:
    """Set the rule columns on an incident row, in place."""
    ok, note = qualifies_smi(row)
    row["qualifies_smi"] = "yes" if ok else "no"
    row["qualify_note"] = note
    row["qualifies_strict"] = "yes" if strict(row) else "no"
