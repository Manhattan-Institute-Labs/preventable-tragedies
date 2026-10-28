"""One spelling per diagnosis, and only named disorders.

The diagnoses column lists disorders the article names. Claude sometimes
writes a symptom ("psychosis"), generic wording ("depression"), a physical
condition ("multiple sclerosis"), or a variant spelling ("PTSD", "bipolar 1").
clean() maps variants to one label and drops what is not a named mental
disorder. A label not listed here is kept, lowercased.

tidy() applies it to a coded row: when every label the row had was dropped,
diagnosis_named is no longer supported and is set false.
"""

import re

# Variant (lowercase) -> the label stored.
CANONICAL = {
    "schizophrenia": "schizophrenia",
    "paranoid schizophrenia": "schizophrenia",
    "schizophrenic": "schizophrenia",
    "schizophrenia spectrum disorder": "schizophrenia spectrum disorder",
    "schizophrenia-spectrum disorder": "schizophrenia spectrum disorder",
    "schizophrenia-like disorder": "schizophrenia spectrum disorder",
    "schizoaffective disorder": "schizoaffective disorder",
    "bipolar disorder": "bipolar disorder",
    "bipolar": "bipolar disorder",
    "bipolar 1": "bipolar disorder",
    "bipolar i": "bipolar disorder",
    "bipolar i disorder": "bipolar disorder",
    "bipolar 2": "bipolar disorder",
    "bipolar ii": "bipolar disorder",
    "bipolar ii disorder": "bipolar disorder",
    "bipolar disorder with depression": "bipolar disorder",
    "manic depression": "bipolar disorder",
    "major depressive disorder": "major depressive disorder",
    "major depression": "major depressive disorder",
    "postpartum depression": "postpartum depression",
    "postpartum psychosis": "postpartum psychosis",
    "delusional disorder": "delusional disorder",
    "psychotic disorder": "psychotic disorder",
    "psychotic disorders": "psychotic disorder",
    "unspecified psychotic disorder": "psychotic disorder",
    "ptsd": "post-traumatic stress disorder",
    "post-traumatic stress": "post-traumatic stress disorder",
    "post-traumatic stress disorder": "post-traumatic stress disorder",
    "posttraumatic stress disorder": "post-traumatic stress disorder",
    "adhd": "attention-deficit/hyperactivity disorder",
    "attention deficit": "attention-deficit/hyperactivity disorder",
    "attention deficit disorder": "attention-deficit/hyperactivity disorder",
    "attention deficit hyperactivity disorder": "attention-deficit/hyperactivity disorder",
}

# Not a named mental disorder: symptoms, generic wording, physical conditions.
DROPPED = {
    "psychosis", "acute psychosis", "psychotic episode", "psychotic break", "mania",
    "mania with psychosis", "delusions", "paranoia", "hallucinations", "hearing voices",
    "depression", "anxiety", "mental illness", "mental health issues", "severe mental illness",
    "multiple sclerosis", "parkinsonism", "parkinson's disease", "pseudobulbar affect",
    "dementia", "traumatic brain injury",
}


def clean(value: str | None) -> str:
    """The semicolon-separated diagnoses, one label each, named disorders only."""
    out = []
    for part in (value or "").split(";"):
        key = re.sub(r"\s+", " ", part.strip().lower())
        if not key or key in DROPPED:
            continue
        label = CANONICAL.get(key, key)
        if label not in out:
            out.append(label)
    return "; ".join(out)


def tidy(row: dict) -> bool:
    """Clean a coded row's diagnoses in place. If it listed labels and none
    is a named disorder, diagnosis_named is no longer supported. Returns
    True if anything changed."""
    before = row.get("diagnoses") or ""
    after = clean(before)
    if after == before:
        return False
    row["diagnoses"] = after
    if before.strip() and not after and row.get("diagnosis_named") == "true":
        row["diagnosis_named"] = "false"
        parts = [p for p in (row.get("signal_evidence") or "").split(" || ")
                 if p and not p.startswith("diagnosis_named: ")]
        row["signal_evidence"] = " || ".join(parts)
    return True
