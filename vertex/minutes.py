"""
vertex/minutes.py

Built-in minutes generator, ported from ArielVertex MinutesGenerator.BuildStructured:
structures raw notes into Discussion / Decisions / Action Items and applies a
deterministic grammar and formatting clean-up. No AI service is called.
"""

import re

DECISION = re.compile(r"\b(decid|agreed|approv|resolv|conclud|finaliz|sign(ed)? off)", re.I)
ACTION = re.compile(
    r"\b(action|will |to-?do|assign|follow[- ]?up|responsible|owner|due|"
    r"by (mon|tue|wed|thu|fri|sat|sun|next|eod|tomorrow))\b|@",
    re.I,
)
_BULLETS = "-*•·\t "


def polish(text, force_stop=True):
    """Grammar / formatting clean-up applied automatically to every line."""
    s = text.strip()
    if not s:
        return s
    s = re.sub(r"\s+", " ", s)  # collapse runs of whitespace
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)  # no space before punctuation
    s = re.sub(r"([,;:])(?=\S)", r"\1 ", s)  # one space after a comma / semicolon
    s = re.sub(r"\bi\b", "I", s)  # standalone "i" -> "I"
    s = s[0].upper() + s[1:]  # capitalise the first letter
    if force_stop and not re.search(r"[.!?:]$", s):
        s += "."
    return s


def split_emails(raw):
    """Attendee emails from a comma / semicolon / line separated string, de-duplicated."""
    seen, result = set(), []
    for part in re.split(r"[,;\n\r]+", raw or ""):
        email = part.strip().lower()
        if email and email not in seen:
            seen.add(email)
            result.append(email)
    return result


def generate_minutes(title, meeting_date, location, attendees, raw_notes):
    decisions, actions, discussion = [], [], []
    for raw in raw_notes.splitlines():
        line = raw.strip().lstrip(_BULLETS)
        if not line:
            continue
        clean = polish(line)
        if DECISION.search(line):
            decisions.append(clean)
        elif ACTION.search(line):
            actions.append(clean)
        else:
            discussion.append(clean)

    def section(heading, items, empty):
        return [f"## {heading}"] + ([f"- {i}" for i in items] or [f"- {empty}"]) + [""]

    lines = [
        f"# Minutes of Meeting — {polish(title, force_stop=False)}",
        "",
        f"**Date:** {meeting_date:%A, %d %b %Y, %H:%M}",
    ]
    if location and location.strip():
        lines.append(f"**Location:** {location.strip()}")
    lines.append(f"**Attendees:** {', '.join(attendees) if attendees else chr(0x2014)}")
    lines.append("")
    lines += section("Discussion", discussion, "No discussion points were recorded.")
    lines += section("Decisions", decisions, "No decisions were recorded.")
    lines += section("Action Items", actions, "No action items were recorded.")
    return "\n".join(lines).rstrip()
