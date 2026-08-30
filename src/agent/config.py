"""Configuration, loaded from the environment only.

Secrets never live in this repo. The competition account is identified by
ALPACA_ACCOUNT_ID, which is also the value submitted to lablab for judging.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

PAPER_TRADING_BASE = "https://paper-api.alpaca.markets/v2"
MARKET_DATA_BASE = "https://data.alpaca.markets"


class ConfigError(RuntimeError):
    """Raised when required configuration is absent."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example, fill it in, and export it "
            f"(or set it in the deployment's secret store)."
        )
    return value


@dataclass(frozen=True)
class Settings:
    api_key: str
    secret_key: str
    account_id: str

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            api_key=_require("ALPACA_API_KEY"),
            secret_key=_require("ALPACA_SECRET_KEY"),
            account_id=_require("ALPACA_ACCOUNT_ID"),
        )
        if not settings.api_key.startswith("PK"):
            raise ConfigError(
                "ALPACA_API_KEY does not start with 'PK'. Paper keys start with "
                "'PK'; a live key here would trade real money."
            )
        return settings
