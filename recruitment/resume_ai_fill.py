"""
recruitment/resume_ai_fill.py

Parses a resume into candidate-application-form field values using the
same local Ollama model as email intake (recruitment/intake.py) and AI
scoring (recruitment/ai_scoring.py). Unlike those two, nothing here is
trusted blindly: every field the LLM returns is re-validated against the
same constraints the form itself enforces (Candidate model validators,
and the exact country/state lists the form's dropdowns are built from)
before being handed back, so the frontend only ever prefills values that
would also pass a real form submission.
"""

import datetime
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator, validate_email

from recruitment import ai_client
from recruitment.resume_utils import extract_resume_text

logger = logging.getLogger(__name__)

_COUNTRY_JS_PATH = Path(settings.BASE_DIR) / "static" / "index" / "country.js"

# Scalar fields the application form has, extracted/validated one at a time
# (see recruitment/templates/candidate/application_form.html). Education,
# experience and skills are structured/repeatable and handled separately
# below (_clean_education_list, _clean_experience_list, _clean_skills).
# Deliberately NOT extracted: current_salary, expected_salary,
# available_to_join_days, reason_to_leave - none of these are things a
# resume actually contains, so guessing at them would just be noise.
FORM_FIELDS = (
    "name",
    "email",
    "mobile",
    "gender",
    "dob",
    "address",
    "country",
    "state",
    "city",
    "zip",
    "portfolio",
)


class ResumeAutofillError(Exception):
    """Raised when the resume text can't be extracted or the LLM call fails."""


@lru_cache(maxsize=1)
def _country_state_map():
    """
    Parses static/index/country.js - the same data the form's country/state
    dropdowns are populated from - into {country_name: [state_names]}, so a
    country/state extracted from a resume can be validated against exactly
    what the form's own dropdowns can represent, instead of just checked
    for shape.
    """
    try:
        text = _COUNTRY_JS_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Could not read %s for country/state validation", _COUNTRY_JS_PATH)
        return {}

    array_match = re.search(r"var\s+country_arr\s*=\s*new Array\((.*?)\);", text, re.DOTALL)
    countries = re.findall(r'"([^"]*)"', array_match.group(1)) if array_match else []

    states_by_index = {}
    for index_str, states_str in re.findall(r's_a\[(\d+)\]\s*=\s*"([^"]*)"\s*;', text):
        states_by_index[int(index_str)] = [
            s.strip() for s in states_str.split("|") if s.strip()
        ]

    # populateCountries() (static/index/country.js) puts a "Select Country"
    # placeholder at option index 0, so country_arr[i] lands on selectedIndex
    # i+1 - which is exactly the index populateStates() uses to look up s_a.
    return {country: states_by_index.get(i + 1, []) for i, country in enumerate(countries)}


def _invalid(raw=""):
    # `raw` (the LLM's un-validated guess) is kept even on failure so the
    # frontend can tell "AI found nothing" apart from "AI found something
    # that didn't pass validation" and show the latter as a hint instead of
    # silently leaving the field blank with no explanation.
    return {"value": "", "valid": False, "raw": (raw or "").strip()}


def _clean_name(raw):
    # Must match ApplicationForm.clean_name() (recruitment/forms.py) - the
    # form is the final gate before save, so a name the AI marks valid here
    # but the form then rejects would be a broken-looking autofill.
    name = (raw or "").strip()
    name = re.sub(r"\s+", " ", name)
    if not name or len(name) > 100 or not re.match(r"^[A-Za-z ]+$", name):
        return _invalid(raw)
    return {"value": name, "valid": True, "raw": name}


def _clean_email(raw):
    email = (raw or "").strip()
    if not email or len(email) > 254:
        return _invalid(raw)
    try:
        validate_email(email)
    except DjangoValidationError:
        return _invalid(raw)
    return {"value": email, "valid": True, "raw": email}


def _clean_mobile(raw):
    # Must match ApplicationForm.clean_mobile() (recruitment/forms.py),
    # which requires 7-15 digits only (no "+", spaces, or punctuation) - so
    # strip everything but digits (a "+91 98765 43210" resume phone still
    # normalizes to a value the form will accept) rather than just
    # rejecting anything with formatting.
    original = (raw or "").strip()
    mobile = re.sub(r"\D", "", original)
    # Resumes often list the number with a "+91" country code (e.g.
    # "+91 98765 43210"), which survives the digit-strip above as a leading
    # "91". Drop it so the field prefills with just the 10-digit number,
    # same as a candidate would type it - only when what's left is itself a
    # plausible Indian mobile number, so a genuine 12-digit number isn't
    # mangled.
    if len(mobile) == 12 and mobile.startswith("91") and re.match(r"^[6-9]\d{9}$", mobile[2:]):
        mobile = mobile[2:]
    if not re.match(r"^\d{7,15}$", mobile):
        return _invalid(original)
    return {"value": mobile, "valid": True, "raw": original}


_GENDER_MAP = {
    "male": "male",
    "m": "male",
    "man": "male",
    "female": "female",
    "f": "female",
    "woman": "female",
    "other": "other",
    "non-binary": "other",
    "nonbinary": "other",
    "prefer not to say": "other",
}


def _clean_gender(raw):
    value = _GENDER_MAP.get((raw or "").strip().lower())
    if not value:
        return _invalid(raw)
    return {"value": value, "valid": True, "raw": (raw or "").strip()}


# Resume dates come back from the LLM in whatever format it felt like, even
# though the prompt asks for YYYY-MM-DD - this is a small permissive parser
# (not a full dateutil-style parser) covering the formats actually seen in
# testing, used for dob and for each education/experience entry's dates.
# Full dates first, then the month/year-only formats resumes use just as
# often (strptime requires the whole string to match one format, so the
# order here doesn't affect which format a given string ends up matching -
# it's just grouped for readability).
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    # Month/year only (no day) - resumes give this far more often than a
    # full date for education and for the start/end of a job.
    "%Y-%m",
    "%Y/%m",
    "%Y.%m",
    "%m-%Y",
    "%m/%Y",
    "%m.%Y",
    "%b, %Y",
    "%B, %Y",
    "%b %Y",
    "%B %Y",
    "%Y",
)

# Ordinal day suffixes ("1st", "22nd", "3rd", "30th") - strptime has no
# directive for these, and resumes write dates this way often enough
# ("Graduated 15th June 2019") that silently failing to parse them would
# leave an otherwise-perfectly-good date blank.
_ORDINAL_SUFFIX_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)

# "Present"/"Current"/etc in a start_date or end_date field - never a real
# date, so don't burn 20 failed strptime attempts on it. (end_date="" is
# already how the rest of this module represents "ongoing".)
_ONGOING_RE = re.compile(
    r"^(present|current|currently|ongoing|now|till\s*date|to\s*date|n/?a)$",
    re.IGNORECASE,
)


def _parse_date_loose(raw):
    text = (raw or "").strip()
    if not text:
        return None
    if _ONGOING_RE.match(text):
        return None
    text = _ORDINAL_SUFFIX_RE.sub(r"\1", text)
    # Some LLM output includes a time component despite the prompt asking
    # for a bare date (e.g. "2019-06-01T00:00:00" or "2019-06-01 00:00:00")
    # - keep only the date portion.
    text = re.split(r"[T ]\d{1,2}:\d{2}", text)[0].strip()
    # Drop a trailing parenthetical note, e.g. "June 2019 (Expected)".
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.datetime.strptime(text, fmt)
            return parsed.date().isoformat()
        except ValueError:
            continue
    return None


def _clean_dob(raw):
    parsed = _parse_date_loose(raw)
    if not parsed:
        return _invalid(raw)
    dob = datetime.date.fromisoformat(parsed)
    today = datetime.date.today()
    if dob > today:
        return _invalid(raw)
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 18 or age > 100:
        return _invalid(raw)
    return {"value": parsed, "valid": True, "raw": (raw or "").strip()}


def _clean_address(raw):
    address = (raw or "").strip()
    if not address:
        return _invalid(raw)
    return {"value": address[:255], "valid": True, "raw": address}


# Common real-world spellings that won't exact-match static/index/country.js
# (e.g. it lists "United Kingdom", not "UK"). Deliberately small - this is a
# convenience net, not a replacement for the exact match against the form's
# own dropdown data.
_COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.a.": "United States",
    "u.s.": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "uae": "United Arab Emirates",
}


def _clean_country_state(country_raw, state_raw):
    country_map = _country_state_map()
    country = (country_raw or "").strip()
    state = (state_raw or "").strip()

    if not country or not country_map:
        return _invalid(country), _invalid(state)

    aliased = _COUNTRY_ALIASES.get(country.lower())
    lookup = aliased or country
    matched_country = next(
        (name for name in country_map if name.lower() == lookup.lower()), None
    )
    if not matched_country:
        return _invalid(country), _invalid(state)

    country_result = {"value": matched_country, "valid": True, "raw": country}
    state_result = _invalid(state)
    if state:
        matched_state = next(
            (s for s in country_map[matched_country] if s.lower() == state.lower()),
            None,
        )
        if matched_state:
            state_result = {"value": matched_state, "valid": True, "raw": state}

    return country_result, state_result


def _clean_city(raw):
    city = (raw or "").strip()
    if not city or len(city) > 30 or not re.search(r"[A-Za-z]", city):
        return _invalid(raw)
    return {"value": city, "valid": True, "raw": city}


def _clean_zip(raw):
    zip_code = (raw or "").strip()
    if not zip_code or len(zip_code) > 30 or not re.match(r"^[A-Za-z0-9][A-Za-z0-9 -]*$", zip_code):
        return _invalid(raw)
    return {"value": zip_code, "valid": True, "raw": zip_code}


_url_validator = URLValidator()


def _clean_portfolio(raw):
    original = (raw or "").strip()
    url = original
    if not url:
        return _invalid(original)
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = f"https://{url}"
    if len(url) > 200:
        return _invalid(original)
    try:
        _url_validator(url)
    except DjangoValidationError:
        return _invalid(original)
    return {"value": url, "valid": True, "raw": original}


# Cap list-shaped extractions so a malformed/hallucinated LLM response (e.g.
# one giant run-on "skills" list) can't blow up the form with hundreds of
# rows - a real resume never legitimately has more than this many.
_MAX_SKILLS = 30
_MAX_EDUCATION_ENTRIES = 10
_MAX_EXPERIENCE_ENTRIES = 15


def _clean_skills(raw_list):
    """
    Returns a deduped list of plain skill-name strings (no valid/raw
    wrapper - unlike the scalar fields, there's no single "the form
    rejected this" outcome for a skill name, so anything short and
    non-empty is accepted as-is and just capped in count).
    """
    if not isinstance(raw_list, list):
        return []
    seen = set()
    skills = []
    for item in raw_list:
        name = str(item or "").strip()
        if not name or len(name) > 50:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        skills.append(name)
        if len(skills) >= _MAX_SKILLS:
            break
    return skills


_CGPA_NUMBER_RE = re.compile(r"\d{1,3}(?:\.\d{1,2})?")


def _clean_percentage_cgpa(raw):
    """
    The CandidateEducation form only accepts a bare number (see
    CANDIDATE_CGPA_PATTERN in recruitment/forms.py, e.g. "85" or "8.5") -
    no "%" sign or "CGPA"/"Percentage" text. Even with the prompt asking
    for a bare number, small local models sometimes still echo the
    resume's own formatting (e.g. "85%", "8.5 CGPA"), so this strips
    everything down to the first plausible number found instead of
    trusting the model's formatting - a prefilled value that would fail
    the form's own validator is worse than leaving the field blank.
    """
    raw = str(raw or "")
    match = _CGPA_NUMBER_RE.search(raw)
    if not match:
        return ""
    value = match.group(0)
    if not (0 <= float(value) <= 100):
        return ""
    return value


def _clean_education_list(raw_list):
    """
    Returns a list of education entries matching CandidateEducation's
    fields (recruitment/models.py). Only degree is required for an entry
    to be kept - everything else is best-effort. Dates that don't parse
    are left blank rather than dropping the whole entry.
    """
    if not isinstance(raw_list, list):
        return []
    entries = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        degree = str(item.get("degree") or "").strip()
        if not degree or len(degree) > 100:
            continue
        entries.append(
            {
                "degree": degree,
                "specialization": str(item.get("specialization") or "").strip()[:100],
                "percentage_cgpa": _clean_percentage_cgpa(item.get("percentage_cgpa")),
                "university": str(item.get("university") or "").strip()[:150],
                "location": str(item.get("location") or "").strip()[:100],
                "start_date": _parse_date_loose(item.get("start_date")) or "",
                "end_date": _parse_date_loose(item.get("end_date")) or "",
            }
        )
        if len(entries) >= _MAX_EDUCATION_ENTRIES:
            break
    return entries


def _clean_experience_list(raw_list):
    """
    Returns a list of experience entries matching CandidateExperience's
    fields (recruitment/models.py). An entry is kept if it has a
    designation or a company name - everything else is best-effort.
    """
    if not isinstance(raw_list, list):
        return []
    entries = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        designation = str(item.get("designation") or "").strip()[:100]
        company_name = str(item.get("company_name") or "").strip()[:150]
        if not designation and not company_name:
            continue
        is_current = bool(item.get("is_current")) if isinstance(
            item.get("is_current"), bool
        ) else str(item.get("is_current") or "").strip().lower() in (
            "true",
            "yes",
            "present",
            "current",
        )
        entries.append(
            {
                "designation": designation,
                "company_name": company_name,
                "location": str(item.get("location") or "").strip()[:100],
                "start_date": _parse_date_loose(item.get("start_date")) or "",
                "end_date": "" if is_current else (_parse_date_loose(item.get("end_date")) or ""),
                "is_current_job": is_current,
            }
        )
        if len(entries) >= _MAX_EXPERIENCE_ENTRIES:
            break
    return entries


def _estimate_total_experience(experience_entries):
    """
    Total experience isn't something to ask the LLM for directly (models
    this size are unreliable at date arithmetic) - instead it's summed
    here from the already-parsed, already-validated start/end dates of
    each experience entry. Overlapping jobs are double-counted (a
    simplifying assumption; resumes rarely list concurrent jobs), and any
    entry missing a start date is skipped since there's nothing to measure
    from. Returns (years, months) as ints, or (None, None) if nothing
    measurable was found.
    """
    total_months = 0
    today = datetime.date.today()
    for entry in experience_entries:
        start_raw = entry.get("start_date")
        if not start_raw:
            continue
        start = datetime.date.fromisoformat(start_raw)
        end_raw = entry.get("end_date")
        end = datetime.date.fromisoformat(end_raw) if end_raw else today
        if end <= start:
            continue
        months = (end.year - start.year) * 12 + (end.month - start.month)
        # Standard whole-months-of-service rounding: a job doesn't count a
        # given month until you've worked through the day-of-month you
        # started on (e.g. Jan 15 - Feb 10 is 0 complete months, not 1).
        # Dates parsed from a month/year-only resume entry (the common
        # case) both default to day 1, so this never under-counts those.
        if end.day < start.day:
            months -= 1
        total_months += max(0, months)
    if total_months <= 0:
        return None, None
    return divmod(total_months, 12)


# Passed as Ollama's `format` (structured outputs, supported since ~0.5) -
# this constrains token sampling to only produce JSON matching this exact
# shape, rather than just "syntactically valid JSON". Needed because on
# nested array-of-objects fields specifically (education/experience), the
# 3B model would otherwise sometimes emit an array of raw value lists
# (e.g. ["degree", "B.Tech", ...]) instead of an array of {"degree": ...}
# objects - technically valid JSON, but not a shape _clean_education_list/
# _clean_experience_list can read, so those fields silently came back
# empty. A schema-constrained decode can't produce that malformed shape.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "mobile": {"type": "string"},
        "gender": {"type": "string"},
        "dob": {"type": "string"},
        "address": {"type": "string"},
        "country": {"type": "string"},
        "state": {"type": "string"},
        "city": {"type": "string"},
        "zip": {"type": "string"},
        "portfolio": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}},
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "degree": {"type": "string"},
                    "specialization": {"type": "string"},
                    "percentage_cgpa": {"type": "string"},
                    "university": {"type": "string"},
                    "location": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                },
                "required": [
                    "degree",
                    "specialization",
                    "percentage_cgpa",
                    "university",
                    "location",
                    "start_date",
                    "end_date",
                ],
            },
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "designation": {"type": "string"},
                    "company_name": {"type": "string"},
                    "location": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "is_current": {"type": "boolean"},
                },
                "required": [
                    "designation",
                    "company_name",
                    "location",
                    "start_date",
                    "end_date",
                    "is_current",
                ],
            },
        },
    },
    "required": [
        "name",
        "email",
        "mobile",
        "gender",
        "dob",
        "address",
        "country",
        "state",
        "city",
        "zip",
        "portfolio",
        "skills",
        "education",
        "experience",
    ],
}


def _call_llm(resume_text: str) -> dict:
    # Deliberately doesn't dump the ~250-country dropdown list into the
    # prompt to constrain the "country" answer - on the local 3B model that
    # blew the prompt up enough to blow past OLLAMA_TIMEOUT. The country
    # name the model returns in plain English is instead matched against
    # that list afterwards, in _clean_country_state() - same end result,
    # without the prompt bloat.
    prompt = (
        "You are extracting job-application form data from a resume. "
        "Respond with ONLY a JSON object with these exact keys: "
        "name, email, mobile, gender, dob, address, country, state, city, zip, "
        "portfolio, skills, education, experience.\n\n"
        "Rules:\n"
        '- Use an empty string "" for any scalar value not clearly present in the resume. '
        "Never guess - except gender, see below.\n"
        "- gender must be one of male/female/other. If not explicitly stated, make your best "
        "guess from the candidate's first name instead of leaving it blank - this is only a "
        "prefill the candidate can change themselves, not a final answer. Only use \"\" if the "
        "name gives no reasonable indication either way.\n"
        '- dob (date of birth) in YYYY-MM-DD format if present, else "".\n'
        "- country/state/city should be the resume's home address location, in plain English "
        '(e.g. "India", "Punjab"), else "".\n'
        "- portfolio is a personal website/GitHub/LinkedIn/portfolio URL, else \"\".\n"
        "- skills is a JSON array of short skill name strings (e.g. [\"Python\", \"Django\"]), "
        "else an empty array.\n"
        "- education is a JSON array of objects, one per degree, each with keys: "
        "degree (the qualification itself, e.g. \"B.Tech\", \"B.Sc\", \"MBA\", \"MCA\", "
        "\"High School\" - NOT the subject/major), specialization (the subject/major/branch, "
        "e.g. \"Computer Science\", \"Mechanical Engineering\" - NOT the degree name), "
        "percentage_cgpa (the bare number ONLY, e.g. \"85\" or \"8.5\" - never include a "
        "% sign, the word CGPA/percentage, or any other unit/text), "
        "university, location, start_date, end_date. Always give start_date "
        "and end_date as a bare date, never a range or a word: use YYYY-MM-DD when the resume "
        "states a day, YYYY-MM when it only states a month and year (e.g. \"Jun 2019\" -> "
        "\"2019-06\"), or just YYYY when only a year is given. Never write \"Present\"/"
        "\"Current\"/\"Ongoing\" as a date - use \"\" for end_date instead if the entry is still "
        "ongoing. Use \"\" for any key not present in that entry. One object per distinct degree "
        "only - do not add empty/placeholder entries. Empty array if no education info found.\n"
        "- experience is a JSON array of objects, one per job, each with keys: "
        "designation (the candidate's job title/role at that company, e.g. \"Software "
        "Developer\", \"Product Designer\" - NOT the company name), company_name (the employer "
        "- NOT the job title), location, start_date, end_date, is_current (true if this is the "
        "candidate's current/present job, else false). Same date rules as education. One "
        "object per distinct job only - do not add empty/placeholder entries. Empty array if "
        "no work experience found.\n\n"
        f"Resume text:\n{resume_text[:8000]}"
    )
    try:
        # This is extraction, not creative writing - the answer should be
        # "what's actually in the text", not a sampled variation of it. The
        # default temperature on small local models was producing visibly
        # inconsistent results run-to-run on the same resume (e.g.
        # education present on one call, empty on the next) - a low
        # temperature makes the model consistently pick its
        # highest-confidence reading instead.
        content = ai_client.chat(
            prompt, json_schema=_RESPONSE_SCHEMA, temperature=0.1
        )
        data = json.loads(content)
    except (ai_client.AIProviderError, ValueError) as exc:
        raise ResumeAutofillError(str(exc)) from exc

    if not isinstance(data, dict) or "name" not in data:
        raise ResumeAutofillError(f"LLM response missing required fields: {data}")
    return data


def parse_resume_for_form(resume_bytes: bytes) -> dict:
    """
    Extracts and validates candidate-application-form fields from a resume
    PDF's bytes.

    Returns:
    {
        "fields": {field_name: {"value": str, "valid": bool, "raw": str}},
        "skills": [str, ...],
        "education": [{"degree": str, "specialization": str, "percentage_cgpa": str,
                        "university": str, "location": str, "start_date": str, "end_date": str}, ...],
        "experience": [{"designation": str, "company_name": str, "location": str,
                         "start_date": str, "end_date": str, "is_current_job": bool}, ...],
        "experience_years": int or None,
        "experience_months": int or None,
    }

    A scalar field with valid=False always has value="" - the frontend
    should leave that input blank for the candidate to fill in themselves
    rather than trusting an unverified AI guess. "raw" carries what the LLM
    actually returned (even when invalid) so the frontend can show it as a
    hint instead of just silently skipping the field. skills/education/
    experience have no such wrapper - each entry has already been filtered
    down to ones worth prefilling (see _clean_skills/_clean_education_list/
    _clean_experience_list), so the frontend can add them directly.

    Raises ResumeAutofillError if the resume has no extractable text or the
    LLM call fails.
    """
    resume_text = extract_resume_text(resume_bytes)
    if not resume_text.strip():
        raise ResumeAutofillError(
            "No extractable text in the resume (likely a scanned image)."
        )

    raw = _call_llm(resume_text)

    country_field, state_field = _clean_country_state(raw.get("country"), raw.get("state"))
    experience = _clean_experience_list(raw.get("experience"))
    experience_years, experience_months = _estimate_total_experience(experience)

    return {
        "fields": {
            "name": _clean_name(raw.get("name")),
            "email": _clean_email(raw.get("email")),
            "mobile": _clean_mobile(raw.get("mobile")),
            "gender": _clean_gender(raw.get("gender")),
            "dob": _clean_dob(raw.get("dob")),
            "address": _clean_address(raw.get("address")),
            "country": country_field,
            "state": state_field,
            "city": _clean_city(raw.get("city")),
            "zip": _clean_zip(raw.get("zip")),
            "portfolio": _clean_portfolio(raw.get("portfolio")),
        },
        "skills": _clean_skills(raw.get("skills")),
        "education": _clean_education_list(raw.get("education")),
        "experience": experience,
        "experience_years": experience_years,
        "experience_months": experience_months,
    }
