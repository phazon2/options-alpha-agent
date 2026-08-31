"""Thin, typed access to the Alpaca paper-trading and market-data APIs.

Read paths (account, clock, option chain, quotes) live here. Order *placement*
deliberately does not: the hackathon requires execution through Alpaca's MCP
server or CLI, so the execution adapter is separate and this module stays
side-effect free and safe to call from anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import MARKET_DATA_BASE, PAPER_TRADING_BASE, Settings

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class BrokerError(RuntimeError):
    """An Alpaca API call failed."""


@dataclass(frozen=True)
class Account:
    account_number: str
    status: str
    equity: float
    cash: float
    options_buying_power: float
    options_trading_level: int
    created_at: str
    trading_blocked: bool

    @property
    def is_competition_ready(self) -> bool:
        """The event's account gates: active, level 3, funded to $100,000."""
        return (
            self.status == "ACTIVE"
            and not self.trading_blocked
            and self.options_trading_level >= 3
            and abs(self.equity - 100_000.0) < 0.01
        )


@dataclass(frozen=True)
class MarketClock:
    is_open: bool
    next_open: str
    next_close: str
    timestamp: str


class AlpacaPaper:
    """Client for the paper environment. Never points at live trading."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._client = httpx.Client(
            headers=self.settings.auth_headers, timeout=TIMEOUT
        )

    def __enter__(self) -> "AlpacaPaper":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, **params: Any) -> dict[str, Any]:
        try:
            response = self._client.get(url, params=params or None)
        except httpx.HTTPError as exc:
            raise BrokerError(f"Request to {url} failed: {exc}") from exc
        if response.status_code != 200:
            raise BrokerError(
                f"{url} returned {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    # ---- trading API -------------------------------------------------
    def account(self) -> Account:
        data = self._get(f"{PAPER_TRADING_BASE}/account")
        return Account(
            account_number=data["account_number"],
            status=data["status"],
            equity=float(data["equity"]),
            cash=float(data["cash"]),
            options_buying_power=float(data.get("options_buying_power", 0)),
            options_trading_level=int(data.get("options_trading_level", 0)),
            created_at=data["created_at"],
            trading_blocked=bool(data["trading_blocked"]),
        )

    def clock(self) -> MarketClock:
        data = self._get(f"{PAPER_TRADING_BASE}/clock")
        return MarketClock(
            is_open=bool(data["is_open"]),
            next_open=data["next_open"],
            next_close=data["next_close"],
            timestamp=data["timestamp"],
        )

    def option_contracts(
        self,
        underlying: str,
        expiration_gte: str,
        expiration_lte: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        data = self._get(
            f"{PAPER_TRADING_BASE}/options/contracts",
            underlying_symbols=underlying,
            expiration_date_gte=expiration_gte,
            expiration_date_lte=expiration_lte,
            limit=limit,
        )
        return data.get("option_contracts", [])

    # ---- market data -------------------------------------------------
    def option_snapshots(self, underlying: str, limit: int = 100) -> dict[str, Any]:
        data = self._get(
            f"{MARKET_DATA_BASE}/v1beta1/options/snapshots/{underlying}", limit=limit
        )
        return data.get("snapshots", {})

    def option_chain(
        self,
        underlying: str,
        expiration_date: str,
        option_type: str = "put",
        feed: str = "indicative",
        limit: int = 1000,
        strike_gte: float | None = None,
        strike_lte: float | None = None,
    ) -> dict[str, Any]:
        """Chain snapshots including greeks and IV.

        The default `opra` feed requires a signed OPRA agreement and returns
        403 without one; `indicative` supplies greeks and IV on the free tier.
        """
        params: dict[str, Any] = {
            "expiration_date": expiration_date,
            "type": option_type,
            "feed": feed,
            "limit": limit,
        }
        if strike_gte is not None:
            params["strike_price_gte"] = strike_gte
        if strike_lte is not None:
            params["strike_price_lte"] = strike_lte
        data = self._get(
            f"{MARKET_DATA_BASE}/v1beta1/options/snapshots/{underlying}", **params
        )
        return data.get("snapshots", {})

    def latest_stock_bar(self, symbol: str, feed: str = "iex") -> dict[str, Any]:
        data = self._get(
            f"{MARKET_DATA_BASE}/v2/stocks/{symbol}/bars/latest", feed=feed
        )
        return data.get("bar", {})
