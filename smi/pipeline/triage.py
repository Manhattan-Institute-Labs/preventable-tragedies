"""Stage 1: cheap headline/snippet triage before any article fetch.

Adapted from ../database/pipeline/triage.py with this tracker's inclusion
rules. A batched Haiku call asks, for 25 headlines at a time, which could
plausibly describe a specific incident involving a person with serious mental
illness. Headlines rarely mention mental health even when the article does,
so the triage keeps anything that could be an incident and only rejects
genres. The full classifier decides precision. Anything the model does not
return a decision for is kept (fail open).
"""

from concurrent.futures import ThreadPoolExecutor

import anthropic
from pydantic import BaseModel

from config import TRIAGE_BATCH_SIZE, TRIAGE_MODEL

TRIAGE_WORKERS = 8


class TriageDecision(BaseModel):
    index: int
    worth_fetching: bool


class TriageBatch(BaseModel):
    decisions: list[TriageDecision]


SYSTEM_PROMPT = """You screen news headlines for a database of incidents involving people with serious mental illness in the United States: a specific person accused of a crime or in a mental-health crisis, or a person with mental illness who was the victim of violence or neglect, died of exposure, overdose or suicide, died in custody, or went missing.

For each numbered headline, decide whether the full article is WORTH FETCHING: could it plausibly describe such a person and a specific incident? Most headlines do not mention mental health even when the article does, so keep any specific incident (an arrest, attack, death, standoff, police response, hospital release, court hearing) unless the headline makes the mental-health connection impossible. Only reject when the headline makes clear the article is:
- a policy, legislation, budget, statistics, research, editorial, or opinion piece with no specific incident
- about an incident outside the United States
- a police blotter, arrest log, or list of unrelated bookings
- about a juvenile (under 18) as the person in crisis or accused
- entertainment, sports, or a fictional work

Return one decision per index. Do not skip any index."""


def _triage_batch(client: anthropic.Anthropic, batch: list[dict]) -> list[bool]:
    lines = []
    for i, c in enumerate(batch):
        snippet = (c.get("snippet") or "").strip()
        line = f"{i}. [{c.get('source', '')}] {c.get('title', '')}"
        if snippet:
            line += f" -- {snippet[:200]}"
        lines.append(line)
    keep = [True] * len(batch)
    response = client.messages.parse(
        model=TRIAGE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n".join(lines)}],
        output_format=TriageBatch,
    )
    parsed = response.parsed_output
    if parsed:
        for d in parsed.decisions:
            if 0 <= d.index < len(batch):
                keep[d.index] = d.worth_fetching
    return keep


def triage_candidates(client: anthropic.Anthropic, candidates: list[dict]) -> list[bool]:
    """One bool per candidate, in order. True means fetch and classify.
    Batches run in parallel; a batch whose call fails is kept whole."""
    batches = [candidates[i:i + TRIAGE_BATCH_SIZE]
               for i in range(0, len(candidates), TRIAGE_BATCH_SIZE)]

    def run(batch):
        try:
            return _triage_batch(client, batch)
        except Exception as e:  # noqa: BLE001
            print(f"  triage batch failed ({e}); keeping all {len(batch)}")
            return [True] * len(batch)

    with ThreadPoolExecutor(max_workers=TRIAGE_WORKERS) as pool:
        results = list(pool.map(run, batches))
    return [k for batch in results for k in batch]
