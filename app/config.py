"""Application settings.

All secrets arrive via environment / `.env` (never committed, never logged, D8).
Provider keys are optional: presence of a key is what "configured" means (D6/D7).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of the `app/` package.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed configuration (see `.env.example` for the documented keys)."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "Prospectly"
    app_version: str = "0.1.0a1"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    app_access_token: str | None = None

    # --- Database ---
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'prospectly.db').as_posix()}"

    # --- Discovery / search providers (keys optional) ---
    osm_enabled: bool = True
    tavily_api_key: str | None = None
    serper_api_key: str | None = None
    brave_api_key: str | None = None

    # --- AI (D7) ---
    ai_provider: str = "template"  # template | gemini | groq | openrouter | ollama
    ai_model: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"

    # --- Usage caps (cost control, COST_ANALYSIS) ---
    tavily_daily_cap: int = 1000
    serper_daily_cap: int = 500
    ai_daily_cap: int = 200

    # --- Safety limits (SECURITY_AND_PRIVACY §5) ---
    http_timeout_seconds: int = 30
    http_connect_timeout_seconds: int = 10
    http_max_redirects: int = 5
    http_max_bytes: int = 2 * 1024 * 1024
    http_max_concurrency: int = 5
    csv_max_bytes: int = 2 * 1024 * 1024
    csv_max_rows: int = 5000

    # --- Paths ---
    data_dir: Path = BASE_DIR / "data"
    logs_dir: Path = BASE_DIR / "logs"
    backups_dir: Path = BASE_DIR / "backups"
    uploads_dir: Path = BASE_DIR / "uploads"

    def ensure_dirs(self) -> None:
        """Create runtime directories (idempotent)."""
        for path in (self.data_dir, self.logs_dir, self.backups_dir, self.uploads_dir):
            path.mkdir(parents=True, exist_ok=True)

    def provider_configured(self, provider: str) -> bool:
        """True when the provider can be used with current configuration."""
        checks: dict[str, bool] = {
            "osm": self.osm_enabled,
            "tavily": bool(self.tavily_api_key),
            "serper": bool(self.serper_api_key),
            "brave": bool(self.brave_api_key),
            "gemini": bool(self.gemini_api_key),
            "groq": bool(self.groq_api_key),
            "openrouter": bool(self.openrouter_api_key),
            "ollama": True,  # local service; reachability checked at call time
            "template": True,  # zero-key floor (D7)
        }
        return checks.get(provider, False)

    @property
    def is_loopback(self) -> bool:
        return self.bind_host in {"127.0.0.1", "localhost", "::1"}


settings = Settings()


def get_settings() -> Settings:
    return settings
