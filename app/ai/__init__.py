"""AI adapters (SYSTEM_ARCHITECTURE §7).

Only the ``template`` floor exists in MVP; other providers (gemini/groq/
openrouter/ollama) arrive as adapters behind the same ``MessageGenerator``
protocol, feature-flagged by config.
"""

from app.ai.generator import MessageGenerator, TemplateGenerator

__all__ = ["MessageGenerator", "TemplateGenerator", "get_generator"]


def get_generator() -> MessageGenerator:
    """Return the configured generator; template is the always-available floor."""
    return TemplateGenerator()
