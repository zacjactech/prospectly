"""AI adapter contract tests (D7) — MockTransport only, zero network.

D7 requires: JSON-schema-validated output, exactly one repair retry on a
validation failure, citation integrity (grounding ⊆ provided finding ids),
usage accounting in ``meta``, and ``origin='ai'`` on produced drafts.

The provider factory must select the configured adapter and fall back to the
template floor (never silently claim to be AI) when a key is missing.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai import TemplateGenerator, get_generator
from app.ai.generator import OutreachContext
from app.ai.providers import (
    AIError,
    AIGenerator,
    GeminiClient,
    OpenAICompatClient,
)
from app.config import settings

CTX = OutreachContext(
    prospect_name="Acme Hotel",
    phone_e164="493012345678",
    offer="affordable websites",
    target_locations=["Berlin"],
    findings=[
        {"id": 1, "statement": "No website found in search", "source_url": None},
        {"id": 2, "statement": "Old site last updated 2014", "source_url": "https://acme.example"},
    ],
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gemini_body(text: str, *, prompt: int = 11, completion: int = 7) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": completion},
    }


def _openai_body(content: str, *, prompt: int = 5, completion: int = 9) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


VALID = json.dumps(
    {"text": "Hi Acme, I noticed you have no website. Worth a chat?", "grounding": [1]}
)


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


async def test_gemini_generates_validated_draft_and_uses_header_key() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_gemini_body(VALID))

    client = GeminiClient(api_key="secret-key", model="gemini-3.5-flash", client=_client(handler))
    draft = await AIGenerator(client).generate(CTX)

    assert draft.origin == "ai"
    assert draft.text.startswith("Hi Acme")
    assert draft.grounding == [1]
    assert draft.meta["adapter"] == "gemini"
    assert draft.meta["model"] == "gemini-3.5-flash"
    assert draft.meta["calls"] == 1
    assert draft.meta["failed_calls"] == 0
    assert draft.meta["prompt_tokens"] == 11
    assert draft.meta["completion_tokens"] == 7

    # D8: key travels in a header, never in the URL.
    assert "secret-key" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "secret-key"
    # D7: native JSON output + schema.
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" in seen["body"]["generationConfig"]
    # Grounding: only stored findings are sent to the model.
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "id=1" in prompt and "Old site last updated 2014" in prompt


async def test_repair_retry_after_invalid_json_then_success() -> None:
    replies = iter(["not json at all", VALID])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_gemini_body(next(replies)))

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    draft = await AIGenerator(client).generate(CTX)

    assert len(calls) == 2  # one attempt + exactly one repair retry
    assert draft.meta["attempts"] == 2
    assert draft.meta["calls"] == 2
    assert draft.meta["ok_calls"] == 1
    assert draft.meta["failed_calls"] == 1
    assert draft.meta["prompt_tokens"] == 22  # both calls' tokens are accounted


async def test_raises_after_repair_retry_with_spent_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_body("garbage"))

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    with pytest.raises(AIError) as excinfo:
        await AIGenerator(client).generate(CTX)

    err = excinfo.value
    assert "repair retry" in str(err)
    assert err.calls == 2
    assert err.failed_calls == 2
    assert err.ok_calls == 0
    assert err.prompt_tokens == 22


async def test_grounding_must_reference_provided_findings() -> None:
    """P5: a draft may not cite evidence that was never provided."""
    bad = json.dumps({"text": "Hi Acme", "grounding": [999]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_body(bad))

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    with pytest.raises(AIError) as excinfo:
        await AIGenerator(client).generate(CTX)
    assert "not provided" in str(excinfo.value)
    assert excinfo.value.calls == 2


async def test_grounding_required_when_findings_exist() -> None:
    ungrounded = json.dumps({"text": "Hi Acme, I invented a fact.", "grounding": []})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_body(ungrounded))

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    with pytest.raises(AIError) as excinfo:
        await AIGenerator(client).generate(CTX)
    assert "grounding is empty" in str(excinfo.value)


async def test_no_findings_allows_empty_grounding_and_says_not_to_assert_facts() -> None:
    thin = OutreachContext(prospect_name="New Cafe", offer=None, findings=[])
    body = json.dumps({"text": "Hi New Cafe, do you handle your own website?", "grounding": []})
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["prompt"] = request.content.decode()
        return httpx.Response(200, json=_gemini_body(body))

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    draft = await AIGenerator(client).generate(thin)

    assert draft.grounding == []
    assert "do not state any specific facts" in seen["prompt"]


# ---------------------------------------------------------------------------
# OpenAI-compatible (Groq / OpenRouter / Ollama)
# ---------------------------------------------------------------------------


async def test_openai_compat_generates_and_sends_bearer_auth() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_body(VALID))

    client = OpenAICompatClient(
        provider_name="groq",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key="gsk-test",
        client=_client(handler),
    )
    draft = await AIGenerator(client).generate(CTX)

    assert draft.origin == "ai"
    assert draft.meta["adapter"] == "groq"
    assert draft.meta["completion_tokens"] == 9
    assert seen["url"].endswith("/chat/completions")
    assert seen["headers"]["authorization"] == "Bearer gsk-test"
    assert seen["body"]["response_format"] == {"type": "json_object"}


async def test_http_failure_raises_with_all_attempts_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 503 is transient: one extra attempt. Both count as calls.
    monkeypatch.setattr("app.ai.providers.TRANSIENT_RETRY_DELAY", 0)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503, text="down")

    client = OpenAICompatClient(
        provider_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        api_key="k",
        client=_client(handler),
    )
    with pytest.raises(AIError) as excinfo:
        await AIGenerator(client).generate(CTX)
    err = excinfo.value
    assert len(attempts) == 2
    assert err.provider == "openrouter"
    assert err.calls == 2
    assert err.failed_calls == 2


async def test_transient_failure_recovers_on_the_single_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live-observed Gemini 503 'high demand' must not fail the draft."""
    monkeypatch.setattr("app.ai.providers.TRANSIENT_RETRY_DELAY", 0)
    replies = iter([httpx.Response(503, text="high demand"), None])
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        response = next(replies)
        return response if response is not None else httpx.Response(200, json=_gemini_body(VALID))

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    draft = await AIGenerator(client).generate(CTX)

    assert len(calls) == 2
    assert draft.origin == "ai"
    assert draft.meta["calls"] == 2
    assert draft.meta["failed_calls"] == 1


async def test_non_transient_error_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad request")

    client = GeminiClient(api_key="k", model="m", client=_client(handler))
    with pytest.raises(AIError) as excinfo:
        await AIGenerator(client).generate(CTX)
    assert len(calls) == 1
    assert excinfo.value.calls == 1


async def test_unexpected_response_shape_raises_ai_error() -> None:
    client = GeminiClient(
        api_key="k",
        model="m",
        client=_client(
            lambda request: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        ),
    )
    with pytest.raises(AIError):
        await AIGenerator(client).generate(CTX)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_selects_gemini_with_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    generator = get_generator()
    assert isinstance(generator, AIGenerator)
    assert generator.provider_name == "gemini"
    assert generator.model == "gemini-3.5-flash"


def test_factory_respects_ai_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    monkeypatch.setattr(settings, "ai_model", "gemini-custom")
    assert get_generator().model == "gemini-custom"


def test_factory_selects_groq_and_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "gsk")
    assert get_generator().provider_name == "groq"

    monkeypatch.setattr(settings, "ai_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "or")
    assert get_generator().provider_name == "openrouter"


def test_factory_ollama_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "ollama")
    generator = get_generator()
    assert isinstance(generator, AIGenerator)
    assert generator.provider_name == "ollama"


def test_factory_falls_back_to_template_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", None)
    generator = get_generator()
    assert isinstance(generator, TemplateGenerator)
    assert generator.provider_name == "template"


def test_factory_unknown_provider_falls_back_to_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_provider", "not-a-provider")
    assert isinstance(get_generator(), TemplateGenerator)
