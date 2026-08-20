"""
recruitment/ai_scoring.py

Reasoning-driven AI candidate scoring: reads a candidate's resume and
evaluates it against the job they applied for using the same local Ollama
model as the email intake feature (recruitment/intake.py), producing a
0-100 score plus specific reasons behind it. Deliberately separate from
the manual CandidateRating system - see recruitment/models.py:CandidateAIScore.
"""

import json
import logging
import re

from django.utils.translation import gettext_lazy as _

from recruitment import ai_client
from recruitment.models import Candidate, CandidateAIScore
from recruitment.resume_utils import extract_resume_text

logger = logging.getLogger(__name__)

# Below this many words, extracted text is almost always a parsing failure
# (scanned image, near-empty doc) rather than a genuinely short resume -
# scoring it would just be the LLM hallucinating from noise.
MIN_RESUME_WORDS = 40

# If two independent scoring calls for the same resume disagree by more
# than this many points, the result isn't reliable enough to trust as-is.
CONSISTENCY_THRESHOLD = 20

# Score ceiling applied when the candidate's own name can't be found
# anywhere in their resume text - a strong signal this resume may not
# actually belong to them (wrong file attached, template artifact, etc.),
# so it should never read as a confident "good" score.
NAME_MISMATCH_SCORE_CAP = 50


class ScoringError(Exception):
    """Raised when the resume could not be parsed or the LLM call failed."""


def _clamp_score(raw) -> int | None:
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return None


def _term_present_in_text(term: str, text: str) -> bool:
    """Whole-word, case-insensitive substring check."""
    term = (term or "").strip()
    if not term:
        return False
    return re.search(rf"\b{re.escape(term.lower())}\b", text.lower()) is not None


def _name_present_in_resume(candidate_name: str, resume_text: str) -> bool:
    """
    Cheap, deterministic identity check: does any meaningful part of the
    candidate's recorded name appear as a whole word in the resume text?
    Not a hard block (nicknames/formatting can legitimately miss this) -
    used as a confidence signal, not a rejection.
    """
    if not candidate_name:
        return True
    parts = [p for p in re.split(r"\s+", candidate_name.strip()) if len(p) >= 3]
    if not parts:
        return True
    return any(_term_present_in_text(part, resume_text) for part in parts)


def _verify_missing_skills(claimed_missing: list, resume_text: str) -> tuple[list, list]:
    """
    The LLM often asserts a skill is "missing" when it's actually right
    there in the text (seen live: it claimed "React" was missing from a
    resume that explicitly lists React under work experience). Rather
    than trust prose claims, the model reports missing_skills as its own
    structured field and every entry gets checked here - real ATS
    verification, not just trusting what the model says about itself.

    Returns (verified_missing, false_claims): skills confirmed absent,
    and skills the model wrongly claimed were absent (i.e. hallucinated).
    """
    verified_missing = []
    false_claims = []
    for skill in claimed_missing or []:
        if not isinstance(skill, str) or not skill.strip():
            continue
        if _term_present_in_text(skill, resume_text):
            false_claims.append(skill)
        else:
            verified_missing.append(skill)
    return verified_missing, false_claims


def _job_context(candidate: Candidate) -> dict:
    recruitment = candidate.recruitment_id
    if not recruitment:
        return {"title": "", "description": "", "skills": []}
    return {
        "title": recruitment.title or "",
        "description": (recruitment.description or "")[:800],
        "skills": [skill.title for skill in recruitment.skills.all()],
    }


def _call_llm(resume_text: str, job: dict) -> dict:
    prompt = (
        "You are an ATS (Applicant Tracking System) resume reviewer. Score "
        "how well this resume matches the job below, using standard ATS "
        "best practices: relevant skills coverage, years/relevance of "
        "experience, keyword alignment with the job description, and "
        "resume clarity/completeness.\n\n"
        "CRITICAL - grounding rule: base your evaluation ONLY on facts "
        "explicitly written in the resume text below. Do not infer, "
        "assume, or invent skills, job titles, employers, education, or "
        "experience that are not clearly stated. If the resume is thin, "
        "generic, incomplete, or missing basic sections (name, contact "
        "info, work history), say so plainly and score it low - never "
        "fill gaps with generic praise or give the benefit of the doubt.\n\n"
        "Respond with ONLY a JSON object with keys: "
        "score (integer 0-100), "
        "missing_skills (a list of specific skills/technologies from the "
        "job skills list below that do NOT appear anywhere in the resume "
        "text - only list ones you are certain are absent; this list is "
        "independently checked against the resume text word-for-word, so "
        "do not guess or list anything you merely assume is missing), "
        "key_points (a list of 3-6 short, specific strings - concrete "
        "reasons for the score such as experience relevance, seniority, "
        "clarity, standout strengths or weaknesses - do NOT make 'X is "
        "missing/not mentioned' claims here, those belong ONLY in "
        "missing_skills), "
        "reasoning (a short paragraph summarizing the evaluation, citing "
        "specifics from the resume, without repeating missing-skill "
        "claims already captured in missing_skills). "
        "The key_points and reasoning must be consistent with the score: "
        "a low score needs at least one clearly negative point, a high "
        "score needs at least one clearly positive point grounded in the "
        "resume text.\n\n"
        f"Job title: {job['title']}\n"
        f"Job description: {job['description']}\n"
        f"Job skills: {json.dumps(job['skills'])}\n\n"
        f"Resume text:\n{resume_text[:8000]}"
    )
    try:
        # Pinned to near-zero so the same resume scored twice in a row (see
        # score_candidate's consistency check) doesn't drift purely from
        # sampling noise.
        content = ai_client.chat(prompt, json_mode=True, temperature=0)
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise ScoringError(str(exc)) from exc

    if "score" not in data or "key_points" not in data:
        raise ScoringError(f"LLM response missing required fields: {data}")
    return data


def score_candidate(candidate: Candidate) -> CandidateAIScore:
    """
    Scores one candidate's resume against the job they applied for.
    Always creates a CandidateAIScore row (one per attempt, history kept)
    describing the outcome, whether it succeeded or not.
    """
    company_id = getattr(candidate.recruitment_id, "company_id", None)

    if not candidate.resume:
        return CandidateAIScore.objects.create(
            candidate=candidate,
            scored_against=candidate.recruitment_id,
            status="no_resume",
            company_id=company_id,
        )

    try:
        with candidate.resume.open("rb") as resume_file:
            resume_bytes = resume_file.read()
        resume_text = extract_resume_text(resume_bytes)
    except Exception as exc:
        logger.exception("Failed to read/extract resume for candidate %s", candidate.pk)
        return CandidateAIScore.objects.create(
            candidate=candidate,
            scored_against=candidate.recruitment_id,
            status="error",
            error_message=str(exc),
            company_id=company_id,
        )

    if not resume_text.strip():
        return CandidateAIScore.objects.create(
            candidate=candidate,
            scored_against=candidate.recruitment_id,
            status="parse_failed",
            error_message=_(
                "No extractable text in the resume (likely a scanned image)."
            ),
            company_id=company_id,
        )

    word_count = len(resume_text.split())
    if word_count < MIN_RESUME_WORDS:
        return CandidateAIScore.objects.create(
            candidate=candidate,
            scored_against=candidate.recruitment_id,
            status="parse_failed",
            error_message=_(
                "Extracted resume text is too short to reliably score "
                "(%(count)d words) - likely a scanned/garbled document."
            )
            % {"count": word_count},
            company_id=company_id,
        )

    job = _job_context(candidate)
    try:
        result_a = _call_llm(resume_text, job)
    except ScoringError as exc:
        return CandidateAIScore.objects.create(
            candidate=candidate,
            scored_against=candidate.recruitment_id,
            status="error",
            error_message=str(exc),
            company_id=company_id,
        )

    # Second independent pass purely to sanity-check consistency - a
    # resume the model can't reliably grade will produce two divergent
    # scores even at temperature=0 (small local models still vary run to
    # run). A transient failure here shouldn't discard a good first result.
    score_b = None
    try:
        result_b = _call_llm(resume_text, job)
        score_b = _clamp_score(result_b.get("score"))
    except ScoringError:
        logger.warning(
            "Second consistency-check scoring pass failed for candidate %s; "
            "keeping the first result without a consistency comparison.",
            candidate.pk,
        )

    score_a = _clamp_score(result_a.get("score"))
    scores = [s for s in (score_a, score_b) if s is not None]
    score = round(sum(scores) / len(scores)) if scores else None

    flags = []
    confidence = "high"
    if score_a is not None and score_b is not None:
        if abs(score_a - score_b) > CONSISTENCY_THRESHOLD:
            confidence = "low"
            flags.append("score_inconsistent")

    key_points = list(result_a.get("key_points") or [])

    verified_missing, false_missing_claims = _verify_missing_skills(
        result_a.get("missing_skills"), resume_text
    )
    if false_missing_claims:
        # The model asserted a skill was absent when it's actually in the
        # resume text - a confirmed hallucination, not just low confidence.
        confidence = "low"
        flags.append("hallucinated_missing_skill")
        logger.warning(
            "AI scoring for candidate %s claimed missing skills that are "
            "actually present in the resume text: %s",
            candidate.pk,
            false_missing_claims,
        )
    if verified_missing:
        key_points.append(
            str(
                _("Missing skills relevant to this job: %(skills)s")
                % {"skills": ", ".join(verified_missing)}
            )
        )

    if not _name_present_in_resume(candidate.name, resume_text):
        confidence = "low"
        flags.append("candidate_name_not_found")
        if score is not None:
            score = min(score, NAME_MISMATCH_SCORE_CAP)
        key_points.insert(
            0,
            str(
                _(
                    "Automated check could not find the candidate's name "
                    "anywhere in the resume text - verify this resume "
                    "actually belongs to this candidate before trusting "
                    "this score."
                )
            ),
        )

    score_obj = CandidateAIScore.objects.create(
        candidate=candidate,
        scored_against=candidate.recruitment_id,
        status="scored",
        score=score,
        key_points=key_points,
        reasoning=result_a.get("reasoning") or "",
        model_used=ai_client.current_model_name(),
        company_id=company_id,
        confidence=confidence,
        flags=flags,
    )
    # Kept in sync so the Candidates grid/Pipeline card/sort can read it
    # directly instead of querying the ai_scores relation every time.
    Candidate.objects.filter(pk=candidate.pk).update(latest_ai_score=score)
    return score_obj
