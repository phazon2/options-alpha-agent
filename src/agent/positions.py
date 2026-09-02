"""Reconstruct spreads from individual option legs, and decide when to exit.

Alpaca reports positions leg by leg. A vertical spread is two legs sharing an
expiry, one short and one long, and the pair has to be reassembled before any
exit decision can be made about it.

Exits follow the managed-winner rule that tastytrade's study of several
thousand SPY put credit spreads found beats holding to expiry: take profit at
half the credit rather than waiting for the last few cents, because the
remaining premium is not worth the gamma risk carried to collect it. The stop
is the mirror image: cut when the position has lost a multiple of the credit,
long before max loss.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

OCC = re.compile(r"^(?P<root>[A-Z]+)(?P<exp>\d{6})(?P<kind>[CP])(?P<strike>\d{8})$")

# Take profit once half the credit has been captured.
PROFIT_TARGET_FRACTION = Decimal("0.50")
# Cut when the cost to close reaches this multiple of the credit received.
STOP_MULTIPLE = Decimal("2.5")

Action = Literal["hold", "take_profit", "stop_out"]


@dataclass(frozen=True)
class OptionLeg:
    symbol: str
    qty: int
    entry: Decimal
    current: Decimal
    root: str
    expiry: str
    kind: str
    strike: Decimal


@dataclass(frozen=True)
class Spread:
    short: OptionLeg
    long: OptionLeg

    @property
    def quantity(self) -> int:
        return abs(self.short.qty)

    @property
    def width(self) -> Decimal:
        return abs(self.short.strike - self.long.strike)

    @property
    def credit_received(self) -> Decimal:
        """Per contract, from the entry prices actually filled."""
        return self.short.entry - self.long.entry

    @property
    def cost_to_close(self) -> Decimal:
        """Per contract, at current marks: buy back the short, sell the long."""
        return self.short.current - self.long.current

    @property
    def captured_fraction(self) -> Decimal:
        """How much of the credit is banked if closed now."""
        if self.credit_received <= 0:
            return Decimal("0")
        return (self.credit_received - self.cost_to_close) / self.credit_received

    @property
    def open_pnl(self) -> Decimal:
        return (self.credit_received - self.cost_to_close) * 100 * self.quantity

    def decide(self) -> tuple[Action, str]:
        if self.credit_received <= 0:
            return "hold", "entry credit not positive; nothing to manage against"
        captured = self.captured_fraction
        if captured >= PROFIT_TARGET_FRACTION:
            return (
                "take_profit",
                f"captured {captured:.0%} of the {self.credit_received} credit, "
                f"at or past the {PROFIT_TARGET_FRACTION:.0%} target",
            )
        if self.cost_to_close >= self.credit_received * STOP_MULTIPLE:
            return (
                "stop_out",
                f"cost to close {self.cost_to_close} is {STOP_MULTIPLE}x the "
                f"{self.credit_received} credit received",
            )
        return (
            "hold",
            f"captured {captured:.0%}, below the {PROFIT_TARGET_FRACTION:.0%} target",
        )


def _parse(position: dict[str, Any]) -> OptionLeg | None:
    match = OCC.match(position.get("symbol", ""))
    if not match:
        return None
    try:
        return OptionLeg(
            symbol=position["symbol"],
            qty=int(position["qty"]),
            entry=Decimal(str(position["avg_entry_price"])),
            current=Decimal(str(position.get("current_price") or 0)),
            root=match["root"],
            expiry=match["exp"],
            kind=match["kind"],
            strike=Decimal(match["strike"]) / 1000,
        )
    except Exception:
        return None


def assemble(positions: list[dict[str, Any]]) -> list[Spread]:
    """Pair each short option with the long that defines its risk.

    Reconstruction from legs is inherently ambiguous. If two spreads share an
    expiry and their strikes interleave - shorts at 758 and 757 against longs
    at 756 and 755 - then 758/756 with 757/755 and 758/755 with 757/756 are
    both consistent with the same four legs, and the widths differ. Nearest
    strike is chosen because it is the conservative reading: it reports the
    narrower spread, so the risk gate never sees less exposure than is really
    there.

    The agent avoids the ambiguity in practice by holding few spreads at
    distinct strikes. Removing it entirely means recording spread identity at
    entry and reconciling against the ledger rather than inferring from legs.
    """
    legs = [leg for leg in (_parse(p) for p in positions) if leg is not None]
    shorts = [leg for leg in legs if leg.qty < 0]
    longs = [leg for leg in legs if leg.qty > 0]

    spreads: list[Spread] = []
    for short in shorts:
        # The defining long shares root, expiry and type, matches size, and
        # sits further out of the money.
        candidates = [
            leg
            for leg in longs
            if leg.root == short.root
            and leg.expiry == short.expiry
            and leg.kind == short.kind
            and abs(leg.qty) == abs(short.qty)
            and (
                leg.strike < short.strike
                if short.kind == "P"
                else leg.strike > short.strike
            )
        ]
        if not candidates:
            continue
        # Nearest strike is the one that was bought against this short.
        partner = min(candidates, key=lambda leg: abs(leg.strike - short.strike))
        longs.remove(partner)
        spreads.append(Spread(short=short, long=partner))
    return spreads
