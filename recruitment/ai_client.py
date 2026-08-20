"""
recruitment/ai_client.py

Single point where every AI feature (email intake, resume autofill, AI
scoring, job description/skill generation, history summaries) sends a chat
prompt to whichever LLM provider is configured via Settings > Integrations
> AI Settings (recruitment.models.AISettings). Nothing outside this module
should branch on provider - callers only ever call chat() and get back the
model's raw text reply, exactly as before this module existed.
"""

import requests
from django.conf import settings

from recruitment.models import AISettings

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class AIProviderError(Exception):
    """Raised when the configured AI provider is unreachable, misconfigured,
    or returns a response that can't be read."""


def _active_settings():
    # Deliberately .first() not get_solo() - a fresh install with no one
    # having ever opened the AI Settings page yet should keep working off
    # the OLLAMA_* Django settings below, not silently create a DB row as
    # a side effect of every AI call.
    return AISettings.objects.first()


def current_model_name() -> str:
    """
    The model name currently in effect, for display/audit purposes only
    (e.g. CandidateAIScore.model_used, CandidateHistorySummary.model_used)
    - never used to decide how to call the provider, that's chat()'s job.
    """
    ai_settings = _active_settings()
    if ai_settings and ai_settings.provider == "openai":
        return ai_settings.openai_model or DEFAULT_OPENAI_MODEL
    if ai_settings and ai_settings.ollama_model:
        return ai_settings.ollama_model
    return settings.OLLAMA_INTAKE_MODEL


def _strict_json_schema(schema):
    """
    OpenAI's structured-output mode ("strict" json_schema) requires every
    object node to set additionalProperties: false. Ollama's schema-
    constrained decoding has no such requirement, so the schemas callers
    already define (e.g. resume_ai_fill._RESPONSE_SCHEMA) don't set it.
    Recursively add it here rather than making every caller's schema
    provider-aware.
    """
    if isinstance(schema, dict):
        schema = dict(schema)
        if schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
            if "properties" in schema:
                schema["properties"] = {
                    key: _strict_json_schema(value)
                    for key, value in schema["properties"].items()
                }
        if "items" in schema:
            schema["items"] = _strict_json_schema(schema["items"])
        return schema
    return schema


def chat(
    prompt: str,
    *,
    json_mode: bool = False,
    json_schema: dict | None = None,
    temperature: float | None = None,
) -> str:
    """
    Sends one prompt to the configured provider and returns the raw text of
    the reply. Callers own parsing/validating that text (usually
    json.loads + their own shape checks) - this only handles transport.

    json_schema, if given, constrains the reply to that exact JSON shape
    (used where malformed nested JSON has been observed from small local
    models - see resume_ai_fill._RESPONSE_SCHEMA). json_mode alone just
    asks for "valid JSON, any shape". temperature=None leaves whichever
    provider's own default sampling behavior in place.
    """
    ai_settings = _active_settings()
    provider = ai_settings.provider if ai_settings else "ollama"

    if provider == "openai":
        if not ai_settings or not ai_settings.openai_api_key:
            raise AIProviderError(
                "OpenAI is selected in AI Settings but no API key is configured."
            )
        return _call_openai(
            prompt,
            ai_settings,
            json_mode=json_mode,
            json_schema=json_schema,
            temperature=temperature,
        )
    return _call_ollama(
        prompt,
        ai_settings,
        json_mode=json_mode,
        json_schema=json_schema,
        temperature=temperature,
    )


def _call_ollama(prompt, ai_settings, *, json_mode, json_schema, temperature):
    base_url = (ai_settings.ollama_base_url if ai_settings else "") or settings.OLLAMA_BASE_URL
    model = (ai_settings.ollama_model if ai_settings else "") or settings.OLLAMA_INTAKE_MODEL

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if json_schema is not None:
        payload["format"] = json_schema
    elif json_mode:
        payload["format"] = "json"
    if temperature is not None:
        payload["options"] = {"temperature": temperature}

    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            timeout=settings.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise AIProviderError(str(exc)) from exc


def _call_openai(prompt, ai_settings, *, json_mode, json_schema, temperature):
    payload = {
        "model": ai_settings.openai_model or DEFAULT_OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": True,
                "schema": _strict_json_schema(json_schema),
            },
        }
    elif json_mode:
        payload["response_format"] = {"type": "json_object"}
    if temperature is not None:
        payload["temperature"] = temperature

    try:
        response = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {ai_settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        raise AIProviderError(str(exc)) from exc
