"""
recruitment/ai_description.py

Drafts a job description from just a job title, using the same local
Ollama model as the email intake and AI scoring features
(recruitment/intake.py, recruitment/ai_scoring.py). The recruiter reviews
and edits the draft in the Summernote editor before saving - this only
saves them from starting on a blank page.
"""

import json
import re

from recruitment import ai_client


class DescriptionGenerationError(Exception):
    """Raised when the LLM call fails or returns an unusable response."""


_CODE_FENCE_RE = re.compile(r"^```(?:html)?\s*|\s*```$", re.IGNORECASE)

# The 3B model reliably gets the two headings' wording right but often drops
# the <b> the prompt asks for - this deterministically re-applies bolding
# instead of depending on the model to follow that instruction every time.
_HEADING_RE = re.compile(
    r"<p>\s*(?:<b>)?\s*(Roles\s*(?:&(?:amp;)?|and)\s*Responsibilities|Requirements)"
    r"\s*(?:</b>)?\s*</p>",
    re.IGNORECASE,
)


def _strip_code_fences(text: str) -> str:
    """Small local models sometimes wrap HTML output in markdown fences
    despite being told not to - strip those if present."""
    return _CODE_FENCE_RE.sub("", text.strip()).strip()


def _bold_headings(html: str) -> str:
    def repl(match):
        text = (
            "Roles &amp; Responsibilities"
            if "roles" in match.group(1).lower()
            else "Requirements"
        )
        return f"<p><b>{text}</b></p>"

    return _HEADING_RE.sub(repl, html)


def generate_job_description(title: str) -> str:
    """
    Returns an HTML job description for the given job title, in the same
    shape recruiters already write by hand in the Summernote editor: a
    bold "Roles & Responsibilities" heading with bullet points, then a
    bold "Requirements" heading with bullet points.
    """
    prompt = (
        "Write a professional job description for the position titled "
        f'"{title}". Respond with ONLY clean HTML (no markdown, no code '
        "fences, no <html>/<head>/<body> tags, no commentary) using "
        "exactly this structure:\n"
        "<p><b>Roles &amp; Responsibilities</b></p>\n"
        "<ul><li>...</li>...</ul>\n"
        "<p><b>Requirements</b></p>\n"
        "<ul><li>...</li>...</ul>\n"
        "Write 4-6 concise bullet points under each heading. Do not "
        "repeat the job title or add a salutation/closing."
    )
    try:
        content = ai_client.chat(prompt)
    except ai_client.AIProviderError as exc:
        raise DescriptionGenerationError(str(exc)) from exc

    content = _strip_code_fences(content)
    if not content:
        raise DescriptionGenerationError("LLM returned an empty description")
    return _bold_headings(content)


def _split_compound_skill(raw: str) -> list[str]:
    """
    The 3B model sometimes ignores the "one atomic skill per string"
    instruction and groups several under a category instead (e.g.
    "Programming languages: Python, Java, C++") - this splits off any
    "Category:" prefix and then any comma-separated items, so each piece
    becomes its own skill. A no-op for already-atomic names (no ':' or
    ',' to split on).
    """
    text = raw.split(":", 1)[-1]
    return [piece.strip() for piece in text.split(",") if piece.strip()]


def generate_job_skills(title: str) -> list[str]:
    """
    Returns a short list of relevant, atomic skill names for the given
    job title (e.g. "Python", not "Programming languages: Python, Java").
    The caller is responsible for matching these against existing Skill
    rows (case-insensitively) before creating new ones, so re-generating
    for a similar title doesn't pile up near-duplicate skills.
    """
    prompt = (
        "Suggest the 5-8 most relevant technical/professional skills for "
        f'a job titled "{title}". Respond with ONLY a JSON object with a '
        'single key "skills" whose value is a flat list of short, atomic '
        "skill name strings - one specific skill per list item, e.g. "
        '{"skills": ["Python", "Django", "REST APIs", "PostgreSQL"]}. Do '
        "NOT group multiple skills into one string, do NOT use category "
        'labels like "Programming languages:" or "Frameworks:", and do '
        "NOT put commas inside a single list item. No commentary, no "
        "markdown, no code fences."
    )
    try:
        content = ai_client.chat(prompt, json_mode=True, temperature=0)
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise DescriptionGenerationError(str(exc)) from exc

    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise DescriptionGenerationError(f"LLM response missing skills list: {data}")

    skills = []
    for name in data["skills"]:
        skills.extend(_split_compound_skill(str(name)))

    # De-dupe case-insensitively (the split above can introduce repeats
    # across groups) while preserving the model's ordering.
    seen = set()
    deduped = []
    for name in skills:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped[:8]


def generate_skills_from_description(description: str) -> list[str]:
    """
    Returns a list of relevant, atomic skill names implied by a full job
    description (plain text - the caller strips HTML tags first). Grounded
    in what the description actually says, unlike generate_job_skills()
    above which only has a bare job title to go on - this is what the
    "Sync Skills" button on the recruitment form calls to keep the Skills
    field aligned with whatever the recruiter has actually written.
    """
    prompt = (
        "Read the job description below and extract the technical/"
        'professional skills it mentions or clearly implies. Respond '
        'with ONLY a JSON object with a single key "skills" whose value '
        "is a flat list of short, atomic skill name strings - one "
        'specific skill per list item, e.g. {"skills": ["Python", '
        '"Django", "REST APIs", "PostgreSQL"]}. Do NOT group multiple '
        'skills into one string, do NOT use category labels like '
        '"Programming languages:" or "Frameworks:", and do NOT put '
        "commas inside a single list item.\n\n"
        "CRITICAL - grounding rule: base this ONLY on what the "
        "description actually says. Do not invent skills it doesn't "
        "mention or clearly imply. If the description is thin or "
        'generic, return fewer skills rather than guessing. No '
        "commentary, no markdown, no code fences.\n\n"
        f"Job description:\n{description[:4000]}"
    )
    try:
        content = ai_client.chat(prompt, json_mode=True, temperature=0)
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise DescriptionGenerationError(str(exc)) from exc

    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise DescriptionGenerationError(f"LLM response missing skills list: {data}")

    skills = []
    for name in data["skills"]:
        skills.extend(_split_compound_skill(str(name)))

    seen = set()
    deduped = []
    for name in skills:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(name)
    return deduped[:15]
