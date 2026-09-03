"""Monte Carlo value-at-risk for a defined-risk vertical spread.

The gate already knows the worst case: a vertical's maximum loss is
(width - credit) x 100 per contract and it cannot be exceeded. What it does
not know is how *likely* the bad outcomes are, and "max loss $1,428" says
nothing about whether that is a one-in-three event or a one-in-fifty one.

This simulates the underlying to expiry under geometric Brownian motion with
realised volatility, prices the spread at expiry along each path, and reports
the distribution: probability of profit, 95% value at risk, and expected
shortfall in the tail beyond it.

Two honest limits, stated because they change how the number should be read:

- GBM has thin tails. Real equity returns do not, so the true tail is worse
  than this reports. Since a vertical's loss is capped by construction, the
  understatement is bounded - it cannot be worse than max loss - which is
  exactly why defined-risk structures are used here.
- Realised volatility is a backward-looking estimate of a forward-looking
  quantity. When implied sits below realised, as it has all week, this
  simulation is if anything pessimistic about the premium.

No model is consulted. Same inputs, same distribution.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from decimal import Decimal

CONTRACT_MULTIPLIER = 100
DEFAULT_PATHS = 20_000
TRADING_DAYS = 252


@dataclass(frozen=True)
class VarReport:
    paths: int
    probability_of_profit: float
    expected_pnl: float
    var_95: float           # loss at the 5th percentile, negative
    expected_shortfall: float  # mean of the worst 5%, negative
    worst_case: float
    max_loss: float

    @property
    def summary(self) -> str:
        return (
            f"PoP {self.probability_of_profit:.0%} · "
            f"E[P&L] ${self.expected_pnl:+,.0f} · "
            f"VaR95 ${self.var_95:,.0f} · "
            f"ES ${self.expected_shortfall:,.0f}"
        )


def _spread_value_at_expiry(
    spot: float, short_strike: float, long_strike: float, is_put: bool
) -> float:
    """What buying the spread back costs at expiry, per share."""
    if is_put:
        short_leg = max(short_strike - spot, 0.0)
        long_leg = max(long_strike - spot, 0.0)
    else:
        short_leg = max(spot - short_strike, 0.0)
        long_leg = max(spot - long_strike, 0.0)
    return short_leg - long_leg


def simulate(
    spot: float,
    short_strike: Decimal,
    long_strike: Decimal,
    credit: Decimal,
    quantity: int,
    days_to_expiry: int,
    annual_vol: float,
    is_put: bool = True,
    paths: int = DEFAULT_PATHS,
    seed: int = 7,
) -> VarReport:
    """Distribution of the trade's P&L at expiry."""
    rng = random.Random(seed)  # fixed so the same trade reports the same risk
    t = max(days_to_expiry, 1) / TRADING_DAYS
    sigma = max(annual_vol, 1e-6)
    drift = -0.5 * sigma * sigma * t
    diffusion = sigma * math.sqrt(t)

    credit_f = float(credit)
    short_f, long_f = float(short_strike), float(long_strike)
    scale = CONTRACT_MULTIPLIER * quantity

    outcomes = []
    for _ in range(paths):
        terminal = spot * math.exp(drift + diffusion * rng.gauss(0.0, 1.0))
        cost = _spread_value_at_expiry(terminal, short_f, long_f, is_put)
        outcomes.append((credit_f - cost) * scale)

    outcomes.sort()
    cut = max(1, int(0.05 * len(outcomes)))
    tail = outcomes[:cut]
    wins = sum(1 for o in outcomes if o > 0)
    width = abs(short_f - long_f)

    return VarReport(
        paths=paths,
        probability_of_profit=wins / len(outcomes),
        expected_pnl=sum(outcomes) / len(outcomes),
        var_95=outcomes[cut - 1],
        expected_shortfall=sum(tail) / len(tail),
        worst_case=outcomes[0],
        max_loss=-(width - credit_f) * scale,
    )
