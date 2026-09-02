"""Market regime filter.

A put credit spread is a bullish-to-neutral position: it profits when the
underlying holds above the short strike. Selling that structure into a falling
market is the single most reliable way to lose money with it, and it is what
cost us on the first position.

So the regime is computed deterministically from daily closes and gates
direction before any model is consulted. This follows the regime-filtering
idea in Pillai et al., "Generating Alpha" (arXiv:2601.19504), which reports
that regime awareness is what keeps trend and mean-reversion strategies from
failing when conditions change. Their implementation trains on equities; the
transferable part is the filter itself, not their model.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any, Literal

Regime = Literal["bullish", "neutral", "bearish"]


@dataclass(frozen=True)
class RegimeRead:
    regime: Regime
    spot: float
    sma_short: float
    sma_long: float
    return_20d: float
    realised_vol: float
    reason: str

    @property
    def allows_put_credit_spread(self) -> bool:
        """Bullish or neutral only. A put spread sold into a downtrend is the
        trade this filter exists to prevent."""
        return self.regime in ("bullish", "neutral")

    @property
    def allows_call_credit_spread(self) -> bool:
        """The mirror: sell calls when the tape is falling or flat, never
        into a rally."""
        return self.regime in ("bearish", "neutral")

    @property
    def preferred_side(self) -> str:
        """Which side the trend argues for. In a neutral tape, lean against
        whichever way price sits relative to its longer average."""
        if self.regime == "bullish":
            return "put"
        if self.regime == "bearish":
            return "call"
        return "call" if self.spot < self.sma_long else "put"


def _sma(values: list[float], n: int) -> float:
    window = values[-n:] if len(values) >= n else values
    return mean(window) if window else 0.0


def read_regime(
    bars: list[dict[str, Any]],
    short_window: int = 5,
    long_window: int = 20,
) -> RegimeRead:
    closes = [float(b["c"]) for b in bars if b.get("c") is not None]
    if len(closes) < 3:
        return RegimeRead(
            "neutral", 0.0, 0.0, 0.0, 0.0, 0.0,
            f"only {len(closes)} daily closes available; defaulting to neutral",
        )

    spot = closes[-1]
    sma_s = _sma(closes, short_window)
    sma_l = _sma(closes, long_window)

    lookback = closes[-long_window:] if len(closes) >= long_window else closes
    ret_20 = (lookback[-1] / lookback[0] - 1.0) if lookback[0] else 0.0

    rets = [
        closes[i] / closes[i - 1] - 1.0
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    vol = pstdev(rets) * (252 ** 0.5) if len(rets) > 1 else 0.0

    if spot >= sma_l and sma_s >= sma_l:
        regime: Regime = "bullish"
        reason = f"spot {spot:.2f} above {long_window}d SMA {sma_l:.2f} and short SMA rising"
    elif spot < sma_l and sma_s < sma_l and ret_20 < -0.02:
        regime = "bearish"
        reason = (
            f"spot {spot:.2f} below {long_window}d SMA {sma_l:.2f} "
            f"with {ret_20 * 100:.1f}% {long_window}d return"
        )
    else:
        regime = "neutral"
        reason = f"spot {spot:.2f} mixed against SMAs ({sma_s:.2f}/{sma_l:.2f})"

    return RegimeRead(regime, spot, sma_s, sma_l, ret_20, vol, reason)
