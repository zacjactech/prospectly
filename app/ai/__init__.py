"""AI adapters (SYSTEM_ARCHITECTURE §7, D7).

``get_generator()`` resolves ``AI_PROVIDER`` to a concrete ``MessageGenerator``:

- ``template`` (default) — the zero-key, zero-network floor. Always works.
- ``gemini`` — Google Generative Language API.
- ``groq`` / ``openrouter`` / ``ollama`` — the OpenAI-compatible
  ``/chat/completions`` shape.

A provider selected without its key (or with an unknown name) falls back to
the template floor **with a warning** rather than failing outreach: the draft
then carries ``origin='template'`` so the UI never mislabels it as AI.
"""

from __future__ import annotations

import logging

from app.ai.generator import GeneratedDraft, MessageGenerator, OutreachContext, TemplateGenerator
from app.ai.providers import AIError, AIGenerator, GeminiClient, OpenAICompatClient
from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODELS: dict[str, str] = {
    # Verified live 2026-11-10: gemini-2.5-flash now returns 404 ("no longer
    # available to new users") — Gemini retires model ids for new keys, so
    # this default must stay current and AI_MODEL always overrides it.
    "gemini": "gemini-3.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "ollama": "llama3.1",
}

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _model_for(provider: str) -> str:
    configured = (settings.ai_model or "").strip()
    return configured or DEFAULT_MODELS.get(provider, "")


def get_generator() -> MessageGenerator:
    """Return the configured generator; template is the always-available floor."""
    provider = (settings.ai_provider or "template").strip().lower()
    if provider in {"", "template"}:
        return TemplateGenerator()

    model = _model_for(provider)

    if provider == "gemini":
        if not settings.gemini_api_key:
            logger.warning("AI_PROVIDER=gemini but GEMINI_API_KEY is unset — using template floor")
            return TemplateGenerator()
        return AIGenerator(GeminiClient(api_key=settings.gemini_api_key, model=model))

    if provider == "groq":
        if not settings.groq_api_key:
            logger.warning("AI_PROVIDER=groq but GROQ_API_KEY is unset — using template floor")
            return TemplateGenerator()
        return AIGenerator(
            OpenAICompatClient(
                provider_name="groq",
                base_url=GROQ_BASE_URL,
                model=model,
                api_key=settings.groq_api_key,
            )
        )

    if provider == "openrouter":
        if not settings.openrouter_api_key:
            logger.warning(
                "AI_PROVIDER=openrouter but OPENROUTER_API_KEY is unset — using template floor"
            )
            return TemplateGenerator()
        return AIGenerator(
            OpenAICompatClient(
                provider_name="openrouter",
                base_url=OPENROUTER_BASE_URL,
                model=model,
                api_key=settings.openrouter_api_key,
            )
        )

    if provider == "ollama":
        base = settings.ollama_base_url.rstrip("/")
        return AIGenerator(
            OpenAICompatClient(
                provider_name="ollama",
                base_url=f"{base}/v1",
                model=model,
            )
        )

    logger.warning("Unknown AI_PROVIDER %r — using template floor", provider)
    return TemplateGenerator()


__all__ = [
    "AIError",
    "AIGenerator",
    "GeneratedDraft",
    "MessageGenerator",
    "OutreachContext",
    "TemplateGenerator",
    "get_generator",
]
