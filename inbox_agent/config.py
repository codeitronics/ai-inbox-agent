"""Settings from environment variables (or a .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["recorded", "anthropic", "openai", "gemini", "deepseek"]

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
    "deepseek": "deepseek-chat",
    "recorded": "recorded-demo",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Demo mode: a fictional mailbox, a mock "Sent" box, and recorded AI unless a provider is set.
    demo_mode: bool = Field(default=True)
    demo_reset_minutes: int = Field(default=0, description="Wipe and reseed the demo every N minutes (0 = never).")

    # AI provider. "auto" picks the first provider with a key, falling back to recorded answers.
    ai_provider: Literal["auto", "recorded", "anthropic", "openai", "gemini", "deepseek"] = "auto"
    ai_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    deepseek_api_key: str = ""

    # Storage
    database_path: str = "./data/inbox.db"

    # Gmail (live mode)
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"
    max_emails_per_run: int = 25
    poll_interval_seconds: int = 300

    # Optional Google Sheets activity log
    google_sheets_id: str = ""

    # Web UI. Required outside demo mode: the UI shows a real inbox.
    web_password: str = ""
    owner_name: str = "Dana"
    owner_signature: str = "Dana Ruiz\nOperations Lead, Northwind Logistics"

    # Time-saved estimate shown on the stats page (minutes per email, by what the agent did).
    minutes_saved_triage: float = 1.5
    minutes_saved_reply: float = 6.0

    def resolved_provider(self) -> Provider:
        if self.ai_provider != "auto":
            return self.ai_provider
        for name in ("anthropic", "openai", "gemini", "deepseek"):
            if getattr(self, f"{name}_api_key"):
                return name  # type: ignore[return-value]
        return "recorded"

    def resolved_model(self) -> str:
        return self.ai_model or DEFAULT_MODELS[self.resolved_provider()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
