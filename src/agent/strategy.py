"""Strategy: delta-targeted defined-risk put credit spreads on SPY.

The thesis is narrow and falsifiable. Short-dated index options carry a
variance risk premium: implied volatility tends to price a wider move than
realises. The agent sells that premium in a strictly defined-risk structure
and never in size, so the edge, if it exists, compounds slowly and a single
adverse move cannot end the account.

Strike selection reads delta from the live chain rather than guessing. The
short leg targets a delta band; the long leg sits a fixed width below it,
capping loss at (width - credit) x 100 per contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .risk import SpreadProposal

# Short-leg delta band. Around 0.20 keeps the short strike meaningfully out of
# the money while still paying a credit worth the risk.
TARGET_DELTA_LOW = 0.12
TARGET_DELTA_HIGH = 0.28
# Widths to consider. A wider spread collects more absolute credit but the
# long leg is cheaper, so credit-to-width usually falls as width grows; the
# selector tries each and keeps the best ratio rather than assuming one.
CANDIDATE_WIDTHS = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("5")]
SPREAD_WIDTH = Decimal("5")
MIN_OPEN_INTEREST_QUOTE = 0.05  # ignore contracts with no real bid


@dataclass(frozen=True)
class Candidate:
    symbol: str
    strike: Decimal
    delta: float
    bid: float
    ask: float
    implied_volatility: float | None

    @property
    def mid(self) -> Decimal:
        return (Decimal(str(self.bid)) + Decimal(str(self.ask))) / 2


class NoTradeFound(RuntimeError):
    """No contract in the chain met the entry conditions. Abstaining is fine."""


def parse_chain(snapshots: dict[str, Any]) -> list[Candidate]:
    """Turn a raw option-chain response into usable put candidates."""
    out: list[Candidate] = []
    for symbol, snap in snapshots.items():
        greeks = snap.get("greeks") or {}
        quote = snap.get("latestQuote") or {}
        delta = greeks.get("delta")
        bid, ask = quote.get("bp"), quote.get("ap")
        if delta is None or bid is None or ask is None:
            continue
        if bid < MIN_OPEN_INTEREST_QUOTE:
            continue
        # OCC symbol: root + YYMMDD + C/P + strike in thousandths.
        try:
            strike = Decimal(symbol[-8:]) / 1000
        except Exception:
            continue
        out.append(
            Candidate(
                symbol=symbol,
                strike=strike,
                delta=float(delta),
                bid=float(bid),
                ask=float(ask),
                implied_volatility=snap.get("impliedVolatility"),
            )
        )
    return out


def select_put_credit_spread(
    candidates: list[Candidate],
    underlying: str = "SPY",
    widths: list[Decimal] | None = None,
    quantity: int = 1,
) -> tuple[SpreadProposal, Candidate, Candidate]:
    """Pick a short put in the delta band, then the width paying best.

    The credit is priced at the mid of each leg, which is where vertical
    spreads actually fill. Pricing at bid/ask instead would model crossing
    both spreads on entry — a cost real orders do not usually pay, and one
    that would make every trade look unprofitable.
    """
    widths = widths or CANDIDATE_WIDTHS
    puts = [c for c in candidates if c.delta < 0]
    by_strike = {c.strike: c for c in puts}

    in_band = [
        c for c in puts if TARGET_DELTA_LOW <= abs(c.delta) <= TARGET_DELTA_HIGH
    ]
    if not in_band:
        raise NoTradeFound(
            f"no put with delta in [{TARGET_DELTA_LOW}, {TARGET_DELTA_HIGH}] "
            f"among {len(puts)} quoted puts"
        )

    target = (TARGET_DELTA_LOW + TARGET_DELTA_HIGH) / 2
    short = min(in_band, key=lambda c: abs(abs(c.delta) - target))

    best: tuple[Decimal, Decimal, Candidate] | None = None
    for width in widths:
        long_leg = by_strike.get(short.strike - width)
        if long_leg is None:
            continue
        credit = (short.mid - long_leg.mid).quantize(Decimal("0.01"))
        if credit <= 0:
            continue
        ratio = credit / width
        if best is None or ratio > best[0]:
            best = (ratio, credit, long_leg)

    if best is None:
        raise NoTradeFound(
            f"short strike {short.strike} has no long leg at any width "
            f"{[str(w) for w in widths]} with a positive credit"
        )

    ratio, credit, long_leg = best
    width = short.strike - long_leg.strike
    proposal = SpreadProposal(
        short_symbol=short.symbol,
        long_symbol=long_leg.symbol,
        width=width,
        credit=credit,
        quantity=quantity,
        underlying=underlying,
    )
    return proposal, short, long_leg
