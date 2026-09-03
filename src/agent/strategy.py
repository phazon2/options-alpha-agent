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


def _pick_short(candidates: list[Candidate], target_delta: float | None, calls: bool):
    """The strike in the delta band closest to the requested target."""
    side = [c for c in candidates if (c.delta > 0) == calls]
    in_band = [
        c for c in side if TARGET_DELTA_LOW <= abs(c.delta) <= TARGET_DELTA_HIGH
    ]
    if not in_band:
        raise NoTradeFound(
            f"no {'call' if calls else 'put'} with delta in "
            f"[{TARGET_DELTA_LOW}, {TARGET_DELTA_HIGH}] among {len(side)} quoted"
        )
    target = target_delta if target_delta is not None else (
        TARGET_DELTA_LOW + TARGET_DELTA_HIGH
    ) / 2
    target = min(max(target, TARGET_DELTA_LOW), TARGET_DELTA_HIGH)
    return min(in_band, key=lambda c: abs(abs(c.delta) - target)), side


def select_call_credit_spread(
    candidates: list[Candidate],
    underlying: str = "SPY",
    widths: list[Decimal] | None = None,
    quantity: int = 1,
    target_delta: float | None = None,
) -> tuple[SpreadProposal, Candidate, Candidate]:
    """The bearish mirror: sell a call, buy a further-out call above it.

    Profits when the underlying stays below the short strike, so it is the
    structure for a market drifting down or sideways — the tape in which
    selling puts is the wrong side of the trend.
    """
    widths = widths or CANDIDATE_WIDTHS
    short, calls = _pick_short(candidates, target_delta, calls=True)
    by_strike = {c.strike: c for c in calls}

    best: tuple[Decimal, Decimal, Candidate] | None = None
    for width in widths:
        long_leg = by_strike.get(short.strike + width)
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
            f"call strike {short.strike} has no long leg at any width "
            f"{[str(w) for w in widths]} with a positive credit"
        )

    _, credit, long_leg = best
    proposal = SpreadProposal(
        short_symbol=short.symbol,
        long_symbol=long_leg.symbol,
        width=long_leg.strike - short.strike,
        credit=credit,
        quantity=quantity,
        underlying=underlying,
    )
    return proposal, short, long_leg



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
    target_delta: float | None = None,
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

    # The analyst may steer the short strike within the band, never outside it.
    target = target_delta if target_delta is not None else (
        TARGET_DELTA_LOW + TARGET_DELTA_HIGH
    ) / 2
    target = min(max(target, TARGET_DELTA_LOW), TARGET_DELTA_HIGH)
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


@dataclass(frozen=True)
class IronCondor:
    """Both sides at once: a put spread below and a call spread above.

    The agent's stated edge is the variance risk premium, which is a claim
    about the *size* of future moves, not their direction. Picking a side
    imports a directional bet the edge does not support - and on 3 September
    that bet cost real money: every put spread the agent opened was
    profitable and every call spread lost, because a rule that read "spot is
    marginally below its 20-day average, sell calls" put it on the wrong side
    of a 1.5% rally.

    A condor harvests the premium on both wings and profits when the
    underlying stays between the short strikes. Risk stays defined on each
    side, and because only one wing can finish in the money, the loss is
    capped at the wider wing rather than the sum of both.
    """

    short_put: Candidate
    long_put: Candidate
    short_call: Candidate
    long_call: Candidate
    credit: Decimal
    quantity: int
    underlying: str

    @property
    def put_width(self) -> Decimal:
        return self.short_put.strike - self.long_put.strike

    @property
    def call_width(self) -> Decimal:
        return self.long_call.strike - self.short_call.strike

    @property
    def width(self) -> Decimal:
        """Only one wing can expire in the money, so risk is the wider one."""
        return max(self.put_width, self.call_width)

    @property
    def max_loss(self) -> Decimal:
        return (self.width - self.credit) * 100 * self.quantity

    @property
    def credit_to_width(self) -> Decimal:
        return self.credit / self.width if self.width > 0 else Decimal("0")

    def as_proposal(self) -> SpreadProposal:
        """The risk gate reasons about width and credit; a condor fits that."""
        return SpreadProposal(
            short_symbol=self.short_put.symbol,
            long_symbol=self.long_put.symbol,
            width=self.width,
            credit=self.credit,
            quantity=self.quantity,
            underlying=self.underlying,
        )


def select_iron_condor(
    candidates: list[Candidate],
    underlying: str = "SPY",
    widths: list[Decimal] | None = None,
    quantity: int = 1,
    target_delta: float | None = None,
) -> IronCondor:
    """Build a condor from one chain containing both puts and calls."""
    put_side, short_put = None, None
    call_side, short_call = None, None

    short_put, puts = _pick_short(candidates, target_delta, calls=False)
    short_call, calls = _pick_short(candidates, target_delta, calls=True)

    widths = widths or CANDIDATE_WIDTHS
    puts_by_strike = {c.strike: c for c in puts}
    calls_by_strike = {c.strike: c for c in calls}

    def best_wing(short_leg, by_strike, downward):
        best = None
        for w in widths:
            partner = by_strike.get(
                short_leg.strike - w if downward else short_leg.strike + w
            )
            if partner is None:
                continue
            credit = (short_leg.mid - partner.mid).quantize(Decimal("0.01"))
            if credit <= 0:
                continue
            ratio = credit / w
            if best is None or ratio > best[0]:
                best = (ratio, credit, partner)
        return best

    put_wing = best_wing(short_put, puts_by_strike, downward=True)
    call_wing = best_wing(short_call, calls_by_strike, downward=False)
    if put_wing is None or call_wing is None:
        raise NoTradeFound(
            "could not build both wings: "
            f"put wing {'ok' if put_wing else 'missing'}, "
            f"call wing {'ok' if call_wing else 'missing'}"
        )

    _, put_credit, long_put = put_wing
    _, call_credit, long_call = call_wing
    if short_call.strike <= short_put.strike:
        raise NoTradeFound(
            f"short call {short_call.strike} is not above short put "
            f"{short_put.strike}; the wings would overlap"
        )

    return IronCondor(
        short_put=short_put,
        long_put=long_put,
        short_call=short_call,
        long_call=long_call,
        credit=(put_credit + call_credit).quantize(Decimal("0.01")),
        quantity=quantity,
        underlying=underlying,
    )
