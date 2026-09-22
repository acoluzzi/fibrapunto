"""Stub configuration.

The three latencies are the interesting values here, and they are related by an
invariant the tests enforce:

    slow_mode_delay_s  <  client timeout  <  timeout_mode_delay_s

Get that ordering wrong and `slow` starts failing or `timeout` starts
succeeding, and the two failure modes stop testing different things. §16 calls
the client-side value "a product decision dressed as a configuration value";
these are the stub's half of that conversation.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BILLING_", extra="ignore")

    # Every successful response is slow, because the real thing is.
    base_latency_ms: float = 800.0
    latency_jitter_ms: float = 300.0

    # Succeeds, below the client timeout (default 5.0s).
    slow_mode_delay_s: float = 3.5

    # Exceeds the client timeout, so the client gives up before the 504 lands.
    timeout_mode_delay_s: float = 8.0

    # §17.7: the stub requires a token. A credential store that nothing ever
    # validates is theatre — the rotation demo has to actually be able to fail.
    api_token: str = "billing-dev-token"

    max_control_ttl_s: float = 600.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
