"""AI message generation (SYSTEM_ARCHITECTURE §7, Phase 3).

``MessageGenerator`` is the abstraction; the ``template`` adapter is the
no-AI floor — it builds a draft from context without calling any model, so
outreach always works even with no AI provider configured. gemini/groq/
openrouter/ollama adapters can be added later behind the same interface
(feature-flagged by config, zero service changes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class OutreachContext:
    """Everything a draft may legitimately reference (grounding sources)."""

    prospect_name: str
    phone_e164: str | None = None
    offer: str | None = None
    target_industries: list[str] = field(default_factory=list)
    target_locations: list[str] = field(default_factory=list)
    ideal_signals: list[str] = field(default_factory=list)
    # {type, summary, rationale} — validated opportunities only.
    opportunities: list[dict] = field(default_factory=list)
    # {id, statement, source_url} — verified/high-confidence findings preferred.
    findings: list[dict] = field(default_factory=list)


@dataclass
class GeneratedDraft:
    text: str
    origin: str  # "template" | "ai"
    grounding: list[int]  # finding ids used at generation time
    meta: dict = field(default_factory=dict)


class MessageGenerator(Protocol):
    async def generate(self, ctx: OutreachContext) -> GeneratedDraft: ...


class TemplateGenerator:
    """No-AI floor: assembles a draft from context, no model, no key."""

    async def generate(self, ctx: OutreachContext) -> GeneratedDraft:
        parts: list[str] = []

        greeting = f"Hi {ctx.prospect_name},"
        parts.append(greeting)

        if ctx.offer:
            where = ctx.target_locations[0] if ctx.target_locations else ""
            parts.append(
                f"I help businesses in {where}{' ' if where else ''}"
                f"with {ctx.offer.lower() if ctx.offer else ''}.".replace("  ", " ").strip()
                or f"I help businesses with {ctx.offer.lower()}."
            )

        # Lead with the strongest validated opportunity.
        if ctx.opportunities:
            opp = ctx.opportunities[0]
            parts.append(opp.get("summary", ""))
            if opp.get("rationale"):
                parts.append(opp["rationale"])

        # Ground in one concrete finding if available.
        if ctx.findings:
            finding = ctx.findings[0]
            parts.append(f"I noticed {finding.get('statement', '').rstrip('.')}")

        parts.append("Worth a quick chat?")
        text = " ".join(p for p in parts if p)

        grounding_ids = [f.get("id") for f in ctx.findings[:1] if f.get("id") is not None]

        return GeneratedDraft(
            text=text,
            origin="template",
            grounding=[g for g in grounding_ids if g is not None],
            meta={"adapter": "template", "findings_used": len(grounding_ids)},
        )
