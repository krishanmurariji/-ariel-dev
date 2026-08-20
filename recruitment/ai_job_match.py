"""
recruitment/ai_job_match.py

Checks whether a candidate's resume is a stronger match for a different
currently-open job in the same company than the one they applied to,
using the same local/OpenAI LLM plumbing as recruitment/ai_scoring.py.
Deliberately a separate flow from CandidateAIScore, which only ever
scores a candidate against the job they applied for - see
recruitment.models.CandidateJobSuggestion.
"""

import json
import logging

from recruitment import ai_client
from recruitment.models import Candidate, CandidateJobSuggestion, Recruitment
from recruitment.resume_utils import extract_resume_text

logger = logging.getLogger(__name__)

MIN_RESUME_WORDS = 40

# How many points a suggested job's score must beat the candidate's
# current job score by before it's worth surfacing - a small gap is
# well within the LLM's run-to-run noise, not a real "better fit".
BETTER_MATCH_MARGIN = 15

# When the candidate hasn't been AI-scored against their applied job
# yet, there's no baseline to compare against - fall back to an
# absolute bar so only a genuinely strong match gets surfaced.
NO_BASELINE_SCORE_FLOOR = 70

# Bounds how many open jobs get stuffed into one prompt - keeps latency
# and cost predictable for companies with many simultaneous openings.
MAX_JOBS_TO_COMPARE = 15


class JobMatchError(Exception):
    """Raised when the resume could not be parsed or the LLM call failed."""


def _clamp_score(raw) -> int | None:
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return None


def _other_open_jobs(candidate: Candidate):
    company_id = getattr(candidate.recruitment_id, "company_id", None)
    queryset = Recruitment.objects.filter(closed=False)
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if candidate.recruitment_id:
        queryset = queryset.exclude(pk=candidate.recruitment_id_id)
    return list(queryset[:MAX_JOBS_TO_COMPARE])


def _call_llm(resume_text: str, jobs: list) -> dict:
    job_listing = "\n\n".join(
        f"{index}: {job.title or ''}\n"
        f"Description: {(job.description or '')[:600]}\n"
        f"Skills: {json.dumps([skill.title for skill in job.skills.all()])}"
        for index, job in enumerate(jobs)
    )
    prompt = (
        "You are an ATS (Applicant Tracking System) reviewer. A candidate's "
        "resume is shown below, followed by a numbered list of other job "
        "openings at the same company. Determine which single opening (if "
        "any) this resume matches best, using standard ATS best practices: "
        "relevant skills coverage, years/relevance of experience, and "
        "keyword alignment with the job description.\n\n"
        "CRITICAL - grounding rule: base your evaluation ONLY on facts "
        "explicitly written in the resume text below. Do not infer, "
        "assume, or invent skills, job titles, employers, education, or "
        "experience that are not clearly stated.\n\n"
        "Respond with ONLY a JSON object with keys: "
        "best_index (the integer index from the list below of the single "
        "best-matching job, or null if none of them are a good match), "
        "score (integer 0-100, the ATS match score of the resume against "
        "that best-matching job only - omit or set to 0 if best_index is "
        "null), "
        "reasoning (a short paragraph explaining why that job is a "
        "stronger or comparable fit, citing specifics from the resume - "
        "omit or leave empty if best_index is null).\n\n"
        f"Resume text:\n{resume_text[:8000]}\n\n"
        f"Other open jobs:\n{job_listing}"
    )
    try:
        content = ai_client.chat(prompt, json_mode=True, temperature=0)
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise JobMatchError(str(exc)) from exc

    if "best_index" not in data:
        raise JobMatchError(f"LLM response missing required fields: {data}")
    return data


def suggest_job_match(candidate: Candidate) -> CandidateJobSuggestion:
    """
    Checks one candidate's resume against every other open job in their
    company. Always creates a CandidateJobSuggestion row (one per
    attempt, history kept) describing the outcome, whether a better
    match was found or not.
    """
    company_id = getattr(candidate.recruitment_id, "company_id", None)
    common_kwargs = {
        "candidate": candidate,
        "current_recruitment": candidate.recruitment_id,
        "current_score": candidate.latest_ai_score,
        "company_id": company_id,
    }

    def _finish(status, **kwargs) -> CandidateJobSuggestion:
        result = CandidateJobSuggestion.objects.create(
            status=status, **common_kwargs, **kwargs
        )
        # Kept in sync so the pipeline card's notification badge can read
        # it directly instead of querying job_suggestions per card.
        Candidate.objects.filter(pk=candidate.pk).update(
            has_better_job_suggestion=(status == "suggested")
        )
        return result

    if not candidate.resume:
        return _finish("no_resume")

    try:
        with candidate.resume.open("rb") as resume_file:
            resume_bytes = resume_file.read()
        resume_text = extract_resume_text(resume_bytes)
    except Exception as exc:
        logger.exception(
            "Failed to read/extract resume for candidate %s", candidate.pk
        )
        return _finish("error", error_message=str(exc))

    if len(resume_text.split()) < MIN_RESUME_WORDS:
        return _finish(
            "parse_failed",
            error_message=(
                "Extracted resume text is too short to reliably compare "
                "against other jobs - likely a scanned/garbled document."
            ),
        )

    other_jobs = _other_open_jobs(candidate)
    if not other_jobs:
        return _finish(
            "no_better_match",
            error_message="No other open positions to compare against.",
        )

    try:
        result = _call_llm(resume_text, other_jobs)
    except JobMatchError as exc:
        return _finish("error", error_message=str(exc))

    best_index = result.get("best_index")
    suggested_score = _clamp_score(result.get("score"))
    suggested_job = None
    if isinstance(best_index, int) and 0 <= best_index < len(other_jobs):
        suggested_job = other_jobs[best_index]

    current_score = candidate.latest_ai_score
    is_better_match = False
    if suggested_job is not None and suggested_score is not None:
        if current_score is not None:
            is_better_match = suggested_score - current_score >= BETTER_MATCH_MARGIN
        else:
            is_better_match = suggested_score >= NO_BASELINE_SCORE_FLOOR

    if not is_better_match:
        return _finish("no_better_match")

    return _finish(
        "suggested",
        suggested_recruitment=suggested_job,
        suggested_score=suggested_score,
        reasoning=result.get("reasoning") or "",
        model_used=ai_client.current_model_name(),
    )
