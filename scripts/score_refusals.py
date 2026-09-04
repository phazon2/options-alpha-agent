"""Re-price every refused trade and report whether refusing was right.

    python scripts/score_refusals.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.counterfactual import extract_refusals, price_refusals, summarise  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402

OUT = Path("public/refusals.json")


def marks_for(broker: AlpacaPaper, symbols: set[str]) -> dict[str, Decimal]:
    """Current mid for each contract, fetched by expiry to keep calls few."""
    by_expiry: dict[str, set[str]] = {}
    for sym in symbols:
        # OCC: root + YYMMDD + C/P + strike
        body = sym[3:] if sym.startswith("SPY") else sym
        yy, mm, dd = body[0:2], body[2:4], body[4:6]
        by_expiry.setdefault(f"20{yy}-{mm}-{dd}", set()).add(sym)

    marks: dict[str, Decimal] = {}
    for expiry, wanted in by_expiry.items():
        for option_type in ("put", "call"):
            try:
                snaps = broker.option_chain(
                    "SPY",
                    expiration_date=expiry,
                    option_type=option_type,
                    feed="indicative",
                    limit=1000,
                )
            except Exception:
                continue
            for sym, snap in snaps.items():
                if sym not in wanted:
                    continue
                quote = snap.get("latestQuote") or {}
                bid, ask = quote.get("bp"), quote.get("ap")
                if bid is None or ask is None:
                    continue
                marks[sym] = (Decimal(str(bid)) + Decimal(str(ask))) / 2
    return marks


def main() -> int:
    entries = DecisionLedger().entries()
    refusals = extract_refusals(entries)
    if not refusals:
        print("no refused trades carry enough detail to re-price")
        return 0

    broker = AlpacaPaper()
    symbols = {r.short_symbol for r in refusals} | {r.long_symbol for r in refusals}
    marks = marks_for(broker, symbols)
    scored = price_refusals(refusals, marks)
    summary = summarise(scored)

    print(f"refused trades examined   {summary['refusals_examined']}")
    print(f"still quotable            {summary['refusals_priced']}")
    print()
    for r in scored:
        pnl = r.pnl_if_taken
        line = (
            f"  {r.at[:16]}  {r.short_symbol}/{r.long_symbol} x{r.quantity}"
            f"  credit {r.credit}"
        )
        if pnl is None:
            print(line + "  -> no longer quoted")
        else:
            print(line + f"  -> would be ${pnl:+,.2f}  [{r.verdict}]")
        print(f"      refused because: {r.reason}")

    print()
    print(f"refusals that avoided a loss   {summary['refusals_correct']}")
    print(f"refusals that cost us a gain   {summary['refusals_costly']}")
    print(f"loss avoided                   ${Decimal(summary['loss_avoided']):+,.2f}")
    print(f"profit forgone                 ${Decimal(summary['profit_forgone']):+,.2f}")
    print(f"net effect of refusing         ${Decimal(summary['net_of_refusing']):+,.2f}")
    print(f"hit rate                       {summary['hit_rate']}")
    print()
    print("by gate:")
    for gate, g in summary["by_gate"].items():
        print(f"  {gate:14s} examined {g['examined']:3d}  priced {g['priced']:3d}"
              f"  correct {g['correct']:3d}  costly {g['costly']:3d}"
              f"  net ${Decimal(g['net']):+,.2f}  hit {g['hit_rate']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "summary": summary,
                "refusals": [
                    {
                        "at": r.at,
                        "short": r.short_symbol,
                        "long": r.long_symbol,
                        "quantity": r.quantity,
                        "credit": str(r.credit),
                        "width": str(r.width),
                        "reason": r.reason,
                        "refused_by": r.refused_by,
                        "pnl_if_taken": (
                            str(r.pnl_if_taken) if r.pnl_if_taken is not None else None
                        ),
                        "verdict": r.verdict,
                    }
                    for r in scored
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
