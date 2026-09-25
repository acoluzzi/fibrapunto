from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECOMMENDATIONS_")

    # What the operator is prepared to give away in discounts, in euros, before
    # this system stops offering discounted deals. Spent when an offer is
    # accepted, not when it is shown.
    budget_eur: float = 120.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
