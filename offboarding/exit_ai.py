"""
offboarding/exit_ai.py

An AI-written plain-language summary of one exit's history (who did what, and when),
shown in the Manage exit window. It uses whichever AI provider is set under
Settings > Integrations > AI Settings - the same one the recruitment AI features use.

Only the recorded history is sent: the dates, who did each step, what changed and the
comment typed with it. The summary is kept in the shared cache and only written again
when the history changes (or when someone asks for a fresh one).
"""

import hashlib
import json
import logging

from django.core.cache import cache

from offboarding.models import ExitActivity
from recruitment import ai_client

logger = logging.getLogger(__name__)

# the most recent steps that are sent; a long exit keeps the prompt small
MAX_EVENTS = 60
CACHE_SECONDS = 24 * 60 * 60


class SummaryError(Exception):
    """The summary could not be written (the message is safe to show)."""


def _events(case):
    """The exit's history, oldest first, as (activity, one line of text)."""
    rows = list(
        ExitActivity.objects.entire()
        .filter(employee_id=case.employee_id)
        .select_related("performed_by")
        .order_by("-created_at", "-pk")[:MAX_EVENTS]
    )
    rows.reverse()
    return rows


def _line(activity):
    who = activity.performed_by.get_full_name() if activity.performed_by_id else "the system"
    text = f"{activity.created_at:%d %b %Y} - {who or 'the system'}: {activity.get_action_display()}"
    if activity.to_value:
        text += f" ({activity.from_value + ' -> ' if activity.from_value else ''}{activity.to_value})"
    if activity.comment:
        text += f' - comment: "{activity.comment.strip()[:300]}"'
    return text


def _fingerprint(rows):
    """Changes whenever a step is added, so an old summary is never shown for new history."""
    raw = "|".join(f"{a.pk}:{a.created_at.isoformat()}" for a in rows)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _cache_key(case, rows):
    return f"exit-history-summary:{case.pk}:{_fingerprint(rows)}"


def _prompt(case, rows):
    name = case.employee_id.get_full_name()
    lines = "\n".join(f"{i + 1}. {_line(a)}" for i, a in enumerate(rows))
    return (
        "You are an HR assistant. Below is the numbered, chronological history of "
        f"{name}'s exit from the company (who did what and when).\n\n"
        "Write a short summary for an HR manager who has not followed this exit: "
        "where things stand now, what has happened so far and what looks unfinished. "
        "Use plain, neutral language.\n\n"
        "CRITICAL - grounding rule: use ONLY facts in the list below. Do not invent "
        "dates, people, reasons or outcomes, and do not guess what happens next.\n\n"
        "Respond with ONLY a JSON object with two keys: "
        '"summary" (one paragraph of at most 90 words) and '
        '"highlights" (a list of at most 4 short strings, each under 18 words, '
        "for the key points such as date changes, assets, tasks or the survey).\n\n"
        f"History:\n{lines}"
    )


def _parse(content):
    try:
        data = json.loads(content)
    except (TypeError, ValueError) as error:
        raise SummaryError("The AI did not return a readable answer.") from error
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        raise SummaryError("The AI did not return a readable answer.")
    highlights = data.get("highlights")
    if not isinstance(highlights, list):
        highlights = []
    return {
        "summary": summary.strip()[:1200],
        "highlights": [str(h).strip()[:200] for h in highlights if str(h).strip()][:4],
    }


def summarize(case, *, fresh=False):
    """
    ``{"summary", "highlights", "events", "model", "cached"}`` for this exit, or ``None``
    when there is no history yet. Raises SummaryError when the AI cannot be reached.
    """
    rows = _events(case)
    if not rows:
        return None
    key = _cache_key(case, rows)
    if not fresh:
        found = cache.get(key)
        if found:
            return dict(found, cached=True)
    try:
        content = ai_client.chat(_prompt(case, rows), json_mode=True, temperature=0)
    except ai_client.AIProviderError as error:
        logger.warning("Exit history summary failed for case %s: %s", case.pk, error)
        raise SummaryError(
            "The AI service could not be reached. Check Settings > Integrations > AI Settings."
        ) from error
    result = _parse(content)
    result.update(events=len(rows), model=ai_client.current_model_name(), cached=False)
    cache.set(key, result, CACHE_SECONDS)
    return result
