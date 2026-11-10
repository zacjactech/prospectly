"""Concrete AI adapters (TECHNOLOGY_DECISIONS D7).

Two transport shapes cover every configured provider:

- ``GeminiClient`` — Google Generative Language API (native JSON response
  schema, key sent in a **header**, never in the URL per D8).
- ``OpenAICompatClient`` — the ``/chat/completions`` shape used by Groq,
  OpenRouter and a local Ollama server.

``AIGenerator`` wraps either client in the ``MessageGenerator`` protocol and
enforces the D7 contract for drafts:

- prompt input is **only** stored findings/opportunities + the user's own
  profile text (grounding, never fetched page content);
- output is JSON validated against ``DraftPayload`` (Pydantic);
- exactly **one repair retry** on a validation failure;
- every citation in ``grounding`` must be a finding id that was actually
  provided (P5 — no invented evidence);
- usage (calls + tokens) is returned in ``meta`` so the service layer can
  write ``ai_usage``; the AI layer itself never touches the database.

Failure mode: ``AIError`` carrying the usage that was already spent. The
service turns it into a user-visible error and still records the spend.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.ai.generator import GeneratedDraft, OutreachContext

MAX_DRAFT_CHARS = 2000
PROMPT_CHAR_BUDGET = 600

# Transient provider failures get exactly one extra attempt (see
# AIGenerator._call_with_retry). 429 is included: a burst-rate 429 is
# transient for a single-user tool, and the daily caps bound total spend.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
TRANSIENT_RETRY_DELAY = 1.0

# Gemini's OpenAPI-subset schema format (uppercase types).
DRAFT_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "text": {"type": "STRING"},
        "grounding": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    },
    "required": ["text", "grounding"],
}

SYSTEM_PROMPT = f"""You write short, honest first-contact WhatsApp messages for \
a small business owner doing outreach.

Hard rules:
- Use ONLY the facts in the CONTEXT block. Never invent details, prices, \
names, URLs, awards or numbers.
- Every factual statement you make must be supported by a finding id from the \
CONTEXT block; list the ids you used in "grounding".
- If the context is thin, keep the message general and ask a question instead \
of asserting facts.
- Plain text only, no emojis, no markdown, no links, at most \
{PROMPT_CHAR_BUDGET} characters.
- One short greeting, one or two sentences, one closing question.

Return ONLY a JSON object with this shape:
{{"text": "<message>", "grounding": [<finding ids>]}}
"""


class AIError(Exception):
    """A failed AI call (transport, shape, or validation after repair).

    Carries the spend so the caller can record it even though generation
    failed.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str | None,
        calls: int = 1,
        ok_calls: int = 0,
        failed_calls: int = 1,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.calls = calls
        self.ok_calls = ok_calls
        self.failed_calls = failed_calls
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class DraftPayload(BaseModel):
    """Shape the model must return (D7 output schema)."""

    text: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)
    grounding: list[int] = Field(default_factory=list)


class _TransportFailure(Exception):
    """Internal: a non-repairable transport failure plus the calls it cost."""

    def __init__(self, error: Exception, calls: int) -> None:
        super().__init__(str(error))
        self.error = error
        self.calls = calls


@dataclass
class AIResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class JSONChatClient(Protocol):
    provider_name: str
    model: str

    async def complete_json(self, *, system: str, user: str) -> AIResponse: ...


def _close(value: object) -> int:
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


class GeminiClient:
    """Google Generative Language API client (JSON response schema)."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def complete_json(self, *, system: str, user: str) -> AIResponse:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
                "responseSchema": DRAFT_SCHEMA,
            },
        }
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60.0)
        try:
            # D8: the key travels in a header, never in the URL/logs.
            response = await client.post(
                f"{self.base_url}/models/{self.model}:generateContent",
                json=payload,
                headers={"x-goog-api-key": self.api_key},
            )
            response.raise_for_status()
        finally:
            if own_client:
                await client.aclose()
        data = response.json()

        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            # Safety block, empty candidate list, or an error envelope.
            raise ValueError(f"unexpected Gemini response: {str(data)[:200]}") from exc

        usage = data.get("usageMetadata") or {}
        return AIResponse(
            text=text,
            prompt_tokens=_close(usage.get("promptTokenCount")),
            # Thinking tokens are billed as output on thinking-capable models,
            # so they belong in the completion count (honest cost figures).
            completion_tokens=_close(usage.get("candidatesTokenCount"))
            + _close(usage.get("thoughtsTokenCount")),
        )


class OpenAICompatClient:
    """``/chat/completions`` client — Groq, OpenRouter, local Ollama."""

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = client

    async def complete_json(self, *, system: str, user: str) -> AIResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60.0)
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
        finally:
            if own_client:
                await client.aclose()
        data = response.json()

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"unexpected {self.provider_name} response: {str(data)[:200]}"
            ) from exc

        usage = data.get("usage") or {}
        return AIResponse(
            text=text or "",
            prompt_tokens=_close(usage.get("prompt_tokens")),
            completion_tokens=_close(usage.get("completion_tokens")),
        )


def _context_block(ctx: OutreachContext) -> str:
    """Render the grounding material — stored records only (D7)."""
    lines = [f"Business name: {ctx.prospect_name}"]
    if ctx.offer:
        lines.append(f"Our offer: {ctx.offer}")
    if ctx.target_locations:
        lines.append(f"Our area: {', '.join(ctx.target_locations)}")
    if ctx.target_industries:
        lines.append(f"We usually help: {', '.join(ctx.target_industries)}")
    if ctx.opportunities:
        lines.append("Validated opportunities:")
        for item in ctx.opportunities[:3]:
            summary = str(item.get("summary", "")).strip()
            rationale = str(item.get("rationale", "")).strip()
            lines.append(f"- {summary}" + (f" ({rationale})" if rationale else ""))
    if ctx.findings:
        lines.append("Findings (cite ids in grounding):")
        for finding in ctx.findings:
            statement = str(finding.get("statement", "")).strip()
            lines.append(f"- id={finding.get('id')}: {statement}")
    else:
        lines.append("Findings: none — do not state any specific facts about this business.")
    return "\n".join(lines)


class AIGenerator:
    """``MessageGenerator`` backed by a JSON-capable chat model (D7)."""

    def __init__(self, client: JSONChatClient, *, task: str = "draft") -> None:
        self.client = client
        self.provider_name = client.provider_name
        self.model = client.model
        self.task = task

    async def generate(self, ctx: OutreachContext) -> GeneratedDraft:
        allowed_ids = {int(f["id"]) for f in ctx.findings if f.get("id") is not None}
        user_prompt = _build_user_prompt(ctx)

        calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        repair_note = ""
        last_error = "unknown validation failure"

        for attempt in (1, 2):  # one attempt + exactly one repair retry
            try:
                reply, transport_calls = await self._call_with_retry(
                    system=SYSTEM_PROMPT, user=user_prompt + repair_note
                )
            except _TransportFailure as failure:
                calls += failure.calls
                raise AIError(
                    f"{self.provider_name} call failed: "
                    f"{failure.error.__class__.__name__}: {failure.error}",
                    provider=self.provider_name,
                    model=self.model,
                    calls=calls,
                    failed_calls=calls,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ) from failure.error

            calls += transport_calls
            prompt_tokens += reply.prompt_tokens
            completion_tokens += reply.completion_tokens

            try:
                payload = DraftPayload.model_validate_json(reply.text)
                self._validate_grounding(payload, allowed_ids)
            except (ValidationError, ValueError) as exc:
                last_error = str(exc).splitlines()[0][:160]
                repair_note = (
                    "\n\nYour previous reply was rejected: "
                    f"{last_error}\nReturn ONLY the JSON object described above, "
                    "with grounding ids copied from the CONTEXT block."
                )
                continue

            return GeneratedDraft(
                text=payload.text.strip(),
                origin="ai",
                grounding=[gid for gid in payload.grounding if gid in allowed_ids],
                meta={
                    "adapter": self.provider_name,
                    "model": self.model,
                    "task": self.task,
                    "attempts": attempt,
                    "calls": calls,
                    "ok_calls": 1,
                    "failed_calls": calls - 1,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            )

        raise AIError(
            f"{self.provider_name} returned invalid output after one repair retry: {last_error}",
            provider=self.provider_name,
            model=self.model,
            calls=calls,
            ok_calls=0,
            failed_calls=calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def _call_with_retry(self, *, system: str, user: str) -> tuple[AIResponse, int]:
        """One extra attempt for transient provider failures (429/5xx/timeout).

        Observed live (2026-11-10): Gemini's free tier returns 503 "high
        demand" sporadically. A single retry turns most of those into a draft
        instead of a 502 — and every attempt is counted, so the spend figures
        stay honest.
        """
        calls = 0
        last_error: Exception | None = None
        for attempt in (1, 2):
            calls += 1
            try:
                return await self.client.complete_json(system=system, user=user), calls
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in TRANSIENT_STATUS:
                    raise _TransportFailure(exc, calls) from exc
                last_error = exc
            except httpx.TimeoutException as exc:
                last_error = exc
            except Exception as exc:  # response shape/decode failure: not retryable
                raise _TransportFailure(exc, calls) from exc
            if attempt == 1:
                await asyncio.sleep(TRANSIENT_RETRY_DELAY)
        assert last_error is not None
        raise _TransportFailure(last_error, calls) from last_error

    @staticmethod
    def _validate_grounding(payload: DraftPayload, allowed_ids: set[int]) -> None:
        if not payload.text.strip():
            raise ValueError("text is empty")
        cited = set(payload.grounding)
        unknown = cited - allowed_ids
        if unknown:
            raise ValueError(f"grounding cites findings that were not provided: {sorted(unknown)}")
        if allowed_ids and not cited:
            # P5: a factual draft must cite the evidence it came from.
            raise ValueError("grounding is empty but findings were provided")


def _build_user_prompt(ctx: OutreachContext) -> str:
    return (
        "Write one first-contact WhatsApp message for this business.\n\n"
        "CONTEXT\n"
        "-------\n"
        f"{_context_block(ctx)}\n"
        "-------\n"
        'Respond with JSON only: {"text": "...", "grounding": [ids]}'
    )


__all__ = [
    "AIError",
    "AIGenerator",
    "AIResponse",
    "DraftPayload",
    "GeminiClient",
    "JSONChatClient",
    "OpenAICompatClient",
]
