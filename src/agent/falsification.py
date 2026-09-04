"""Grade the analyst's pre-registered falsification conditions.

Before every trade the analyst has to write down one concrete condition under
which its thesis is wrong. Writing it down is cheap. This module is what makes
it cost something: each condition is parsed into a level and a direction and
checked against every daily close since it was recorded. A condition that
fired means the thesis was falsified in the market's own terms, whatever the
P&L did afterwards.

Only the closes-at-a-level form is graded automatically. Conditions phrased
in terms of realised or implied volatility are reported as unparsed rather
than quietly skipped, so the coverage number is honest.

The grade also asks a second question: could the condition have warned in
time? A put spread has lost its maximum once the underlying closes below the
short strike, so "wrong if SPY closes below the short strike" can only fire
after the loss is locked in. That is a description, not a warning. The
distance between the level and the short strike is recorded for every graded
condition so the pattern is visible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

_NOT_APPLICABLE = re.compile(r"^\s*(n/?a\b|not applicable|none\b|no trade)", re.I)
_LEVEL = re.compile(
    r"clos\w*\s+(?:(at\s+or\s+|at/)?(above|below|over|under)|(>=|<=|>|<))\s*\$?"
    r"(\d{3,4}(?:\.\d+)?)",
    re.I,
)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_OCC = re.compile(r"^[A-Z]{1,6}(\d{6})([CP])(\d{8})$")


@dataclass(frozen=True)
class Condition:
    at: str
    text: str
    trade: bool
    kind: str  # "spot_close" | "not_applicable" | "unparsed"
    direction: str | None = None  # "above" | "below"
    level: float | None = None
    inclusive: bool = False
    deadline: str | None = None
    side: str | None = None  # "put" | "call" when the chosen spread is known
    short_strike: float | None = None
    status: str = "ungraded"  # fired | holding | expired_unfired | ungraded
    fired_on: str | None = None
    fired_close: float | None = None
    closes_checked: int = 0

    @property
    def distance_from_short_strike(self) -> float | None:
        if self.level is None or self.short_strike is None:
            return None
        return round(self.level - self.short_strike, 2)

    @property
    def warns_before_max_loss(self) -> bool | None:
        """True if the level sits between spot and the short strike, so the
        condition can fire while the spread still has something to save."""
        d = self.distance_from_short_strike
        if d is None or self.side is None:
            return None
        return d > 0 if self.side == "put" else d < 0


def strike_of(symbol: str) -> float | None:
    m = _OCC.match(symbol or "")
    return int(m.group(3)) / 1000 if m else None


def side_of(symbol: str) -> str | None:
    m = _OCC.match(symbol or "")
    return {"P": "put", "C": "call"}[m.group(2)] if m else None


def parse(
    at: str,
    text: str,
    trade: bool,
    *,
    short_symbol: str | None = None,
) -> Condition:
    side = side_of(short_symbol) if short_symbol else None
    strike = strike_of(short_symbol) if short_symbol else None
    if not text or _NOT_APPLICABLE.match(text):
        return Condition(at, text, trade, "not_applicable", side=side, short_strike=strike)
    m = _LEVEL.search(text)
    if not m:
        return Condition(at, text, trade, "unparsed", side=side, short_strike=strike)
    word = (m.group(2) or "").lower()
    op = m.group(3) or ""
    inclusive = bool(m.group(1)) or op in (">=", "<=")
    direction = "above" if word in ("above", "over") or op in (">", ">=") else "below"
    deadline = _DATE.search(text)
    return Condition(
        at,
        text,
        trade,
        "spot_close",
        direction=direction,
        level=float(m.group(4)),
        inclusive=inclusive,
        deadline=deadline.group(1) if deadline else None,
        side=side,
        short_strike=strike,
    )


def grade(
    cond: Condition,
    closes: list[tuple[str, float]],
    today: str,
    default_deadline: str | None = None,
) -> Condition:
    """Check a condition against daily closes, ascending, from its own day on."""
    if cond.kind != "spot_close" or cond.level is None:
        return cond
    day = cond.at[:10]
    checked = 0
    for date_str, close in closes:
        if date_str < day:
            continue
        checked += 1
        if cond.direction == "above":
            hit = close >= cond.level if cond.inclusive else close > cond.level
        else:
            hit = close <= cond.level if cond.inclusive else close < cond.level
        if hit:
            return replace(
                cond, status="fired", fired_on=date_str, fired_close=close,
                closes_checked=checked,
            )
    deadline = cond.deadline or default_deadline
    status = "expired_unfired" if deadline and deadline < today else "holding"
    return replace(cond, status=status, closes_checked=checked)


def conditions_from_ledger(entries: list[dict[str, Any]]) -> list[tuple[Condition, str | None]]:
    """Every analyst condition, paired with the spread that cycle chose.

    Returns (condition, cycle expiry). The short symbol comes from the first
    record after the view that names one - a risk verdict or a gated abstain -
    before the next cycle starts.
    """
    out: list[tuple[Condition, str | None]] = []
    n = len(entries)
    i = 0
    expiry: str | None = None
    while i < n:
        e = entries[i]
        if e.get("kind") == "cycle_start":
            expiry = e.get("expiry")
        elif e.get("kind") == "analyst_view":
            short_symbol = None
            j = i + 1
            while j < n and entries[j].get("kind") != "cycle_start":
                if entries[j].get("short"):
                    short_symbol = entries[j]["short"]
                    if entries[j].get("expiry"):
                        expiry = entries[j]["expiry"]
                    break
                j += 1
            out.append(
                (
                    parse(
                        e.get("at", ""),
                        e.get("invalidated_if") or "",
                        bool(e.get("trade")),
                        short_symbol=short_symbol,
                    ),
                    expiry,
                )
            )
        i += 1
    return out


def summarise(conds: list[Condition]) -> dict[str, Any]:
    graded = [c for c in conds if c.kind == "spot_close"]
    on_trades = [c for c in graded if c.trade]
    placed = [c for c in on_trades if c.warns_before_max_loss is not None]
    return {
        "conditions": len(conds),
        "parsed": len(graded),
        "not_applicable": sum(1 for c in conds if c.kind == "not_applicable"),
        "unparsed": sum(1 for c in conds if c.kind == "unparsed"),
        "fired": sum(1 for c in graded if c.status == "fired"),
        "holding": sum(1 for c in graded if c.status == "holding"),
        "expired_unfired": sum(1 for c in graded if c.status == "expired_unfired"),
        "on_trades_taken": len(on_trades),
        "on_trades_fired": sum(1 for c in on_trades if c.status == "fired"),
        "placed_against_short_strike": len(placed),
        "could_warn_before_max_loss": sum(1 for c in placed if c.warns_before_max_loss),
        "could_not_warn": sum(1 for c in placed if not c.warns_before_max_loss),
    }
