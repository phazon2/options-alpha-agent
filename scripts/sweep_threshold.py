"""Sweep the credit-to-width floor and measure what each value would have earned.

The floor was originally a guess. This replaces the guess with a measurement:
for every spread the agent ever evaluated, re-price it now, then ask what the
portfolio would be worth under each candidate threshold. Trades a threshold
would have admitted contribute their actual outcome; trades it would have
refused contribute nothing.

The result is a frontier: too high a floor and the agent never trades, too low
and it takes premium not worth the risk. The empirical maximum sits between.

Honesty about the sample matters more than the shape of the curve. With only a
handful of evaluated spreads this is indicative, not conclusive, and the output
says so rather than presenting a jagged maximum as a settled optimum.

    python scripts/sweep_threshold.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402

OUT = Path("public/frontier.json")
STEPS = [Decimal(str(round(0.04 + 0.01 * i, 2))) for i in range(17)]  # 0.04 .. 0.20


def occ_expiry(symbol: str) -> str:
    body = symbol[3:] if symbol.startswith("SPY") else symbol
    return f"20{body[0:2]}-{body[2:4]}-{body[4:6]}"


def main() -> int:
    entries = DecisionLedger().entries()
    evaluated = [
        e
        for e in entries
        if e.get("kind") == "risk_verdict" and e.get("short") and e.get("long")
    ]
    if not evaluated:
        print("no evaluated spreads in the ledger")
        return 0

    broker = AlpacaPaper()
    symbols = {e["short"] for e in evaluated} | {e["long"] for e in evaluated}
    marks: dict[str, Decimal] = {}
    for expiry in sorted({occ_expiry(s) for s in symbols}):
        for kind in ("put", "call"):
            try:
                snaps = broker.option_chain(
                    "SPY", expiration_date=expiry, option_type=kind,
                    feed="indicative", limit=1000,
                )
            except Exception:
                continue
            for sym, snap in snaps.items():
                if sym not in symbols:
                    continue
                q = snap.get("latestQuote") or {}
                if q.get("bp") is None or q.get("ap") is None:
                    continue
                marks[sym] = (Decimal(str(q["bp"])) + Decimal(str(q["ap"]))) / 2

    # Each evaluated spread, with the outcome it would have had.
    trades = []
    for e in evaluated:
        credit, width = Decimal(str(e["credit"])), Decimal(str(e["width"]))
        if width <= 0:
            continue
        short_now, long_now = marks.get(e["short"]), marks.get(e["long"])
        if short_now is None or long_now is None:
            continue
        qty = int(e.get("quantity") or 1) or 1
        pnl = (credit - (short_now - long_now)) * 100 * qty
        trades.append(
            {
                "at": e.get("at", ""),
                "ratio": float(credit / width),
                "pnl": float(pnl),
                "was_approved": bool(e.get("approved")),
                "short": e["short"],
                "long": e["long"],
            }
        )

    if not trades:
        print("no evaluated spread could be re-priced")
        return 0

    points = []
    for theta in STEPS:
        taken = [t for t in trades if t["ratio"] >= float(theta)]
        points.append(
            {
                "threshold": float(theta),
                "trades_admitted": len(taken),
                "total_pnl": round(sum(t["pnl"] for t in taken), 2),
                "winners": sum(1 for t in taken if t["pnl"] > 0),
            }
        )

    best = max(points, key=lambda p: (p["total_pnl"], -p["threshold"]))
    ties = [p for p in points if p["total_pnl"] == best["total_pnl"]]

    # A protective filter can only be shown to pay for itself when there is
    # something to protect against. If every re-priced spread is a winner, the
    # curve is monotonically decreasing by construction and its maximum is
    # always "trade everything" - which says nothing about the threshold and
    # everything about the market having gone up. Detect that explicitly
    # rather than reporting a maximum that is an artefact.
    losers = [t for t in trades if t["pnl"] < 0]
    degenerate = not losers

    print(f"spreads evaluated and re-priced: {len(trades)}\n")
    print(f"{'theta':>7} {'admitted':>9} {'winners':>8} {'total P&L':>11}")
    for p in points:
        mark = "  <-- max" if p["total_pnl"] == best["total_pnl"] else ""
        print(
            f"{p['threshold']:>7.2f} {p['trades_admitted']:>9} {p['winners']:>8} "
            f"{p['total_pnl']:>11.2f}{mark}"
        )

    print(f"\nempirical maximum at theta = {best['threshold']:.2f}")
    if len(ties) > 1:
        lo = min(t["threshold"] for t in ties)
        hi = max(t["threshold"] for t in ties)
        print(f"  but flat across {lo:.2f}-{hi:.2f}: {len(ties)} thresholds tie")

    if degenerate:
        print()
        print("  *** THE SAMPLE CANNOT DISCRIMINATE ***")
        print(f"  All {len(trades)} re-priced spreads are winners. SPY rose across")
        print("  the whole window, so every put credit spread evaluated would have")
        print("  paid. With no losing trade in the sample, the curve can only fall")
        print("  as the threshold rises, and its maximum is 'trade everything' by")
        print("  construction - an artefact of one rally, not a property of the")
        print("  filter. A floor exists to cap the loser that has not happened yet.")
        print(f"  The shipped floor stays at 0.12; this frontier is not evidence")
        print("  for lowering it further, and would be dangerous if read that way.")
    else:
        print(
            f"  sample is {len(trades)} spreads ({len(losers)} losing) - indicative, "
            f"not conclusive."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated_at": entries[-1].get("at") if entries else None,
                "sample_size": len(trades),
                "shipped_threshold": 0.12,
                "empirical_max": best["threshold"],
                "tie_range": [
                    min(t["threshold"] for t in ties),
                    max(t["threshold"] for t in ties),
                ],
                "losers_in_sample": len(losers),
                "discriminating": not degenerate,
                "caveat": (
                    f"All {len(trades)} re-priced spreads are winners. With no "
                    f"loser in the sample the curve falls monotonically and its "
                    f"maximum is 'trade everything' by construction - an artefact "
                    f"of a rising market, not a property of the filter. Not "
                    f"evidence for lowering the floor."
                    if degenerate
                    else f"{len(trades)} spreads re-priced, {len(losers)} losing. "
                    f"Indicative of the shape, not a settled optimum."
                ),
                "points": points,
                "trades": trades,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
