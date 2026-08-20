"""
recruitment/ai_history_summary.py

AI-generated plain-language narrative of a candidate's tracked history
(stage moves, field edits, who made them, and when), using the same
local Ollama model as AI scoring/email intake (recruitment/ai_scoring.py).
Unlike a compressed summary, this walks through every recorded event as
its own formal, easy-to-read sentence - a step-by-step "what happened"
account a layperson can follow, rather than a handful of highlights.
"""

import json
import logging

from horilla_audit.methods import get_diff
from recruitment import ai_client
from recruitment.models import Candidate, CandidateHistorySummary

logger = logging.getLogger(__name__)

# Caps prompt size for candidates with unusually long change histories.
MAX_EVENTS = 60


class SummaryError(Exception):
    """Raised when the history could not be analyzed."""


def _who(entry: dict) -> str:
    updated_by = entry.get("updated_by")
    get_full_name = getattr(updated_by, "get_full_name", None)
    if callable(get_full_name):
        name = get_full_name()
        if name:
            return name
    return str(updated_by)


def _resolve_fk_value(pair_record, change: dict):
    """
    Mirrors the `fk_history` template filter: resolves a foreign-key
    field on a historical record to its current display value, falling
    back to the raw stored id if the related row no longer exists.
    """
    try:
        return getattr(pair_record, change["field_name"])
    except Exception:
        raw_id = pair_record.__dict__.get(f"{change['field_name']}_id")
        return f"{raw_id} (deleted)"


def _event_records(candidate: Candidate) -> list[dict]:
    """
    Structured, chronological (oldest-first) list of every tracked event
    for this candidate - each a dict describing when it happened, who
    did it, and what changed.
    """
    tracking = get_diff(candidate)
    records = []
    # `tracking` is newest-first; reverse for a chronological narrative.
    for entry in reversed(tracking):
        newer, older = entry["pair"]
        when = newer.history_date.strftime("%b %d, %Y")
        who = _who(entry)
        if entry["type"] != "Changes":
            records.append(
                {"when": when, "who": who, "kind": "created", "text": entry["type"]}
            )
            continue
        for change in entry.get("changes", []):
            if change.get("is_fk"):
                old = _resolve_fk_value(older, change)
                new = _resolve_fk_value(newer, change)
            else:
                old, new = change.get("old"), change.get("new")
            records.append(
                {
                    "when": when,
                    "who": who,
                    "kind": "change",
                    "field": change["field"],
                    "old": old,
                    "new": new,
                }
            )
    return records[-MAX_EVENTS:]


def _prompt_line(record: dict) -> str:
    if record["kind"] == "created":
        return f"{record['when']} - {record['who']}: {record['text']}"
    return (
        f"{record['when']} - {record['who']}: {record['field']} changed "
        f"from '{record['old']}' to '{record['new']}'"
    )


def _fallback_sentence(record: dict) -> str:
    """
    Deterministic, template-based formal sentence for one event - used
    when the LLM doesn't return exactly one sentence per event, so
    "every step" is still guaranteed to be covered even if the model
    under-delivers.
    """
    if record["kind"] == "created":
        return f"On {record['when']}, {record['who']} created this candidate record."
    return (
        f"On {record['when']}, {record['who']} changed the "
        f"{record['field'].lower()} from '{record['old']}' to '{record['new']}'."
    )


def _call_llm(records: list[dict], candidate_name: str) -> list[str]:
    numbered = "\n".join(
        f"{i + 1}. {_prompt_line(record)}" for i, record in enumerate(records)
    )
    prompt = (
        "You are an HR assistant explaining a job candidate's record "
        f"history to someone with no HR background. Below is a "
        f"numbered, chronological list of every recorded change to "
        f"{candidate_name}'s candidate record (stage moves, field "
        "edits, who made them, and when).\n\n"
        "For EVERY numbered event, write exactly one clear, formal, "
        "plain-language sentence describing what happened - phrased so "
        "a layperson with no HR background can understand it "
        "immediately (e.g. \"On Aug 10, 2026, Amit Chawla moved the "
        "candidate from the Applied stage to the Screening stage.\"). Do "
        "not skip, merge, or summarize multiple events into a single "
        "sentence - there must be exactly as many sentences as there "
        "are numbered events below, in the same order.\n\n"
        "CRITICAL - grounding rule: base every sentence ONLY on the "
        "event it corresponds to. Do not invent stages, dates, or "
        "people not present in that event.\n\n"
        "Respond with ONLY a JSON object with one key: "
        "steps (a list of strings, exactly one per numbered event "
        "below, in the same order).\n\n"
        f"Events:\n{numbered}"
    )
    try:
        content = ai_client.chat(prompt, json_mode=True, temperature=0)
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise SummaryError(str(exc)) from exc

    steps = data.get("steps")
    if not isinstance(steps, list):
        raise SummaryError(f"LLM response missing required fields: {data}")
    return [str(s).strip() for s in steps if str(s).strip()]


def summarize_candidate_history(candidate: Candidate) -> CandidateHistorySummary:
    """
    Generates a step-by-step, plain-language narrative of one
    candidate's tracked history - one formal sentence per recorded
    event, in chronological order. Always creates a
    CandidateHistorySummary row (one per attempt, history kept),
    mirroring CandidateAIScore's audit-trail shape.
    """
    company_id = getattr(candidate.recruitment_id, "company_id", None)

    records = _event_records(candidate)
    if not records:
        return CandidateHistorySummary.objects.create(
            candidate=candidate,
            status="no_history",
            company_id=company_id,
        )

    try:
        steps = _call_llm(records, candidate.name)
    except SummaryError as exc:
        logger.warning(
            "AI history analysis failed for candidate %s: %s", candidate.pk, exc
        )
        return CandidateHistorySummary.objects.create(
            candidate=candidate,
            status="error",
            error_message=str(exc),
            company_id=company_id,
        )

    if len(steps) != len(records):
        # The model skipped, merged, or added events - fall back to a
        # deterministic sentence per event so every step is still
        # guaranteed to be covered, just less naturally phrased.
        logger.warning(
            "AI history analysis for candidate %s returned %d steps for "
            "%d events; falling back to templated sentences.",
            candidate.pk,
            len(steps),
            len(records),
        )
        steps = [_fallback_sentence(record) for record in records]

    return CandidateHistorySummary.objects.create(
        candidate=candidate,
        status="summarized",
        milestones=steps,
        model_used=ai_client.current_model_name(),
        company_id=company_id,
    )
