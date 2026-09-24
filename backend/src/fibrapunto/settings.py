"""Configuration. Read once, at import, from the environment and .env.

Budgets and timeouts live here rather than scattered through the code because
§16 is explicit that the integration timeout "is a product decision dressed as a
configuration value, and it should be set with the operator". Treat every value
in the Budgets block as a decision someone made, not a default someone inherited.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- model access (§10) --------------------------------------------------
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    model_agent: str = "anthropic/claude-sonnet-5"
    model_router: str = "anthropic/claude-haiku-4.5"
    model_judge: str = "google/gemini-2.5-pro"
    model_persona: str = "google/gemini-2.5-pro"

    # --- database ------------------------------------------------------------
    postgres_db: str = "fibrapunto"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    postgres_user: str = "fibrapunto_owner"
    postgres_password: SecretStr = SecretStr("change-me-owner")

    agent_db_user: str = "fibrapunto_agent"
    agent_db_password: SecretStr = SecretStr("change-me-agent")
    platform_db_user: str = "fibrapunto_platform"
    platform_db_password: SecretStr = SecretStr("change-me-platform")

    # --- billing stub (§7) ---------------------------------------------------
    billing_base_url: str = "http://localhost:8081"
    # The line that decides when to stop waiting and hand over (§16). Must stay
    # below the stub's timeout-mode delay or the timeout path is never exercised.
    billing_timeout_seconds: float = 5.0

    # --- budgets (§12.1) -----------------------------------------------------
    max_turns: int = 12
    turn_budget_without_capability: int = Field(
        default=6,
        description="Turns allowed before escalating if no capability call has succeeded",
    )
    capability_timeout_s: float = 8.0

    # --- privacy (§9) --------------------------------------------------------
    redact_inbound: bool = True
    transcript_retention_days: int = 30

    # --- credentials (§17.7) -------------------------------------------------
    fibrapunto_master_key: SecretStr = SecretStr("")

    # --- operator access (api/auth.py) ---------------------------------------
    # Gates the operator view and the admin routes. Empty means refused, not open.
    operator_password: SecretStr = SecretStr("")

    def dsn(self, role: str = "owner") -> str:
        """Connection string for one of the three identities.

        `role` is deliberately explicit at every call site. There is no default
        connection: choosing an identity is a decision, and §8 is the reason.
        """
        user, password = {
            "owner": (self.postgres_user, self.postgres_password),
            "agent": (self.agent_db_user, self.agent_db_password),
            "platform": (self.platform_db_user, self.platform_db_password),
        }[role]
        return str(
            PostgresDsn.build(
                scheme="postgresql",
                username=user,
                password=password.get_secret_value(),
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
