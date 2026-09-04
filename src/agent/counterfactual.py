"""Score the trades the agent refused to take.

Every entry in this hackathon can show the trades it made. The interesting
question is the one nobody answers: when an agent declines to trade, is that
discipline or is it an excuse?

The ledger records enough to settle it. Each vetoed proposal names its short
and long legs, the credit it would have collected, and the moment it was
refused. Re-pricing those exact legs later says what the refused trade would
have been worth — so a refusal becomes a claim that can be checked rather
than a virtue that has to be taken on faith.

The scoring is deliberately symmetric. A refusal that avoided a loss is
counted, and so is a refusal that missed a profit. Reporting only the first
would be marking our own homework.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class RefusedTrade:
    """A spread the agent decided against, priced as if it had been taken."""

    at: str
    short_symbol: str
    long_symbol: str
    width: Decimal
    credit: Decimal
    quantity: int
    reason: str
    refused_by: str  # "risk gate" | "challenger" | "analyst" | "regime"

    short_now: Decimal | None = None
    long_now: Decimal | None = None

    @property
    def priced(self) -> bool:
        return self.short_now is not None and self.long_now is not None

    @property
    def cost_to_close_now(self) -> Decimal | None:
        """What buying the spread back would cost at current marks."""
        if not self.priced:
            return None
        return self.short_now - self.long_now

    @property
    def pnl_if_taken(self) -> Decimal | None:
        """Per the whole order: credit collected minus what it costs to close."""
        cost = self.cost_to_close_now
        if cost is None:
            return None
        return (self.credit - cost) * 100 * self.quantity

    @property
    def verdict(self) -> str:
        pnl = self.pnl_if_taken
        if pnl is None:
            return "unpriced"
        if pnl < 0:
            return "refusal correct"
        if pnl > 0:
            return "refusal costly"
        return "refusal neutral"


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def extract_refusals(entries: list[dict[str, Any]]) -> list[RefusedTrade]:
    """Pull every refused spread that carries enough detail to be re-priced.

    Since 3 September every gate that fires after a spread has been chosen -
    the analyst, the challenger, the Monte Carlo significance test and the
    arithmetic risk gate - writes the legs into its abstain record, so each
    gate's refusals can be graded on the same terms. Older ledgers only
    carried legs on risk-gate verdicts; those are still read, and a verdict
    that has a paired gated abstain is not counted twice.
    """
    out: list[RefusedTrade] = []
    gated_at: set[tuple[str, str, str]] = set()
    for entry in entries:
        if entry.get("kind") != "abstain" or not entry.get("gate"):
            continue
        short, long_leg = entry.get("short"), entry.get("long")
        credit, width = _dec(entry.get("credit")), _dec(entry.get("width"))
        if not (short and long_leg and credit and width):
            continue
        gate = str(entry["gate"]).replace("_", " ")
        gated_at.add((entry.get("at", ""), short, long_leg))
        out.append(
            RefusedTrade(
                at=entry.get("at", ""),
                short_symbol=short,
                long_symbol=long_leg,
                width=width,
                credit=credit,
                quantity=int(entry.get("quantity") or 1) or 1,
                reason=str(entry.get("reason") or "refused"),
                refused_by=gate,
            )
        )
    for entry in entries:
        if entry.get("kind") != "risk_verdict" or entry.get("approved"):
            continue
        short, long_leg = entry.get("short"), entry.get("long")
        credit, width = _dec(entry.get("credit")), _dec(entry.get("width"))
        if not (short and long_leg and credit and width):
            continue
        if (entry.get("at", ""), short, long_leg) in gated_at:
            continue
        out.append(
            RefusedTrade(
                at=entry.get("at", ""),
                short_symbol=short,
                long_symbol=long_leg,
                width=width,
                credit=credit,
                quantity=int(entry.get("quantity") or 1) or 1,
                reason="; ".join(entry.get("reasons") or []) or "vetoed",
                refused_by="risk gate",
            )
        )
    out.sort(key=lambda r: r.at)
    return out


def price_refusals(
    refusals: list[RefusedTrade], marks: dict[str, Decimal]
) -> list[RefusedTrade]:
    """Attach current marks. Legs that no longer quote are left unpriced."""
    priced: list[RefusedTrade] = []
    for r in refusals:
        short_now, long_now = marks.get(r.short_symbol), marks.get(r.long_symbol)
        priced.append(
            RefusedTrade(
                at=r.at,
                short_symbol=r.short_symbol,
                long_symbol=r.long_symbol,
                width=r.width,
                credit=r.credit,
                quantity=r.quantity,
                reason=r.reason,
                refused_by=r.refused_by,
                short_now=short_now,
                long_now=long_now,
            )
        )
    return priced


def summarise(refusals: list[RefusedTrade]) -> dict[str, Any]:
    priced = [r for r in refusals if r.priced]
    correct = [r for r in priced if r.verdict == "refusal correct"]
    costly = [r for r in priced if r.verdict == "refusal costly"]
    avoided = sum((r.pnl_if_taken or 0) for r in correct)
    forgone = sum((r.pnl_if_taken or 0) for r in costly)
    return {
        "refusals_examined": len(refusals),
        "refusals_priced": len(priced),
        "refusals_correct": len(correct),
        "refusals_costly": len(costly),
        "loss_avoided": str(-avoided),
        "profit_forgone": str(forgone),
        "net_of_refusing": str(-(avoided + forgone)),
        "hit_rate": (
            f"{len(correct) / len(priced):.0%}" if priced else "n/a"
        ),
        "by_gate": by_gate(refusals),
    }


def by_gate(refusals: list[RefusedTrade]) -> dict[str, dict[str, Any]]:
    """The same scorecard, one row per gate. A gate that only ever refuses
    winners is a gate that should be loosened; the aggregate hides that."""
    gates: dict[str, dict[str, Any]] = {}
    for r in refusals:
        g = gates.setdefault(
            r.refused_by,
            {"examined": 0, "priced": 0, "correct": 0, "costly": 0, "net": Decimal(0)},
        )
        g["examined"] += 1
        if not r.priced:
            continue
        g["priced"] += 1
        if r.verdict == "refusal correct":
            g["correct"] += 1
        elif r.verdict == "refusal costly":
            g["costly"] += 1
        g["net"] -= r.pnl_if_taken or 0
    for g in gates.values():
        g["net"] = str(g["net"])
        g["hit_rate"] = f"{g['correct'] / g['priced']:.0%}" if g["priced"] else "n/a"
    return gates
