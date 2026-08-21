"""
recruitment/ai_linkedin_post.py

Drafts the text of a LinkedIn job post from a Recruitment record, using the
same AI provider as the rest of the recruitment AI features (see
recruitment/ai_client.py - Ollama or OpenAI, whichever is configured under
Settings > Integrations > AI Settings).

The apply link itself is never put in this text - post_recruitment_in_linkedin
(recruitment/views/linkedin.py) attaches it separately as the post's link
card, so candidates get a clickable "Apply" preview card instead of a raw
pasted URL sitting in the post body. If the AI call fails for any reason,
the caller falls back to a plain deterministic template (see
_fallback_commentary in linkedin.py) so publishing a job is never blocked on
the AI being unavailable.
"""

from recruitment import ai_client


class LinkedInPostGenerationError(Exception):
    """Raised when the LLM call fails or returns an unusable response."""


def generate_linkedin_post_text(recruitment, description_text, skill_titles):
    """
    Returns a ready-to-post LinkedIn commentary string: a short attention-
    grabbing opening, a bulleted list of what the role needs, and a closing
    line inviting the reader to apply - written in plain, friendly language
    rather than corporate/technical phrasing, matching how a recruiter
    would actually talk to candidates on LinkedIn.
    """
    skills_line = ", ".join(skill_titles) if skill_titles else ""
    prompt = (
        "Write the text of a LinkedIn job post for a recruiter to publish "
        f'as-is. Job title: "{recruitment.title}".\n'
        f"Company: {recruitment.company_id}.\n"
        f"Number of openings: {recruitment.vacancy}.\n"
        + (f"Key skills: {skills_line}.\n" if skills_line else "")
        + "Job description (context only, don't just copy it in):\n"
        f"{description_text[:3000]}\n\n"
        "Write in plain, warm, everyday language a hiring manager would "
        "actually use when talking to people - not stiff corporate or "
        "technical wording. A non-technical reader should be able to "
        "understand every sentence.\n\n"
        "Structure the post exactly like this:\n"
        "1. One or two short opening sentences that make someone want to "
        "keep reading (an emoji like 🚀 or 👋 is fine here).\n"
        "2. A short bulleted list (4-6 bullets, each on its own line "
        "starting with '🔹') of what the role needs - key skills, "
        "experience level, and anything that makes a candidate stand out. "
        "Keep each bullet under 12 words.\n"
        "3. Exactly one closing line inviting the reader to apply. This "
        "line must NOT contain a URL, a website address, an '@' email "
        "address, or any instruction to email/DM someone - just something "
        "like 'Apply using the link below' or 'Tap Apply to get started', "
        "because the real apply link is attached separately as a "
        "clickable card under this post, not typed into the text.\n"
        "4. On a new final line, 4-6 relevant hashtags separated by "
        "spaces (always include #Hiring and #NowHiring, plus 2-4 more "
        "specific to the role).\n\n"
        "Respond with ONLY the finished post text - no markdown, no code "
        "fences, no quotation marks around the whole thing, no commentary "
        "about what you wrote."
    )
    try:
        content = ai_client.chat(prompt, temperature=0.4)
    except ai_client.AIProviderError as exc:
        raise LinkedInPostGenerationError(str(exc)) from exc

    content = content.strip().strip("`").strip()
    if not content:
        raise LinkedInPostGenerationError("LLM returned empty post text")
    return content
