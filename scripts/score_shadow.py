"""Re-price what the naive agent would have done, and set it beside the real one.

    python scripts/score_shadow.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.counterfactual import RefusedTrade, price_refusals  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_refusals import marks_for  # noqa: E402

SHADOW = Path("docs/ledger/shadow.jsonl")
OUT = Path("public/shadow.json")


def main() -> int:
    entries = DecisionLedger(SHADOW).entries() if SHADOW.exists() else []
    would = [e for e in entries if e.get("kind") == "shadow_decision" and e.get("trade")]
    passed = [e for e in entries if e.get("kind") == "shadow_decision" and not e.get("trade") and not e.get("failed")]
    if not entries:
        print("no shadow decisions yet")
        return 0

    trades = [
        RefusedTrade(
            at=e["at"], short_symbol=e["short"], long_symbol=e["long"],
            width=Decimal(str(e["width"])), credit=Decimal(str(e["credit"])),
            quantity=int(e.get("contracts") or 1), reason=e.get("rationale", ""),
            refused_by="naive agent (would have traded)",
        )
        for e in would
    ]
    broker = AlpacaPaper()
    marks = marks_for(broker, {t.short_symbol for t in trades} | {t.long_symbol for t in trades})
    scored = price_refusals(trades, marks)

    priced = [t for t in scored if t.priced]
    pnl_now = sum((t.pnl_if_taken or Decimal("0")) for t in priced)
    risk = sum((t.width - t.credit) * 100 * t.quantity for t in trades)
    contracts = sum(t.quantity for t in trades)

    print(f"naive agent: {len(would)} would-be trades, {len(passed)} passes, {contracts} contracts")
    print(f"  aggregate max loss it would carry   ${risk:,.0f}")
    print(f"  mark-to-market of those trades now  ${pnl_now:+,.0f}  ({len(priced)} of {len(trades)} priced)")
    for t in scored:
        p = t.pnl_if_taken
        print(f"    {t.at[5:16]} {t.short_symbol[9:]}/{t.long_symbol[9:]} x{t.quantity} credit {t.credit} -> {'unpriced' if p is None else f'${p:+,.0f}'}")

    real = DecisionLedger().entries()
    real_orders = [e for e in real if e.get("kind") == "order_submitted" and not e.get("dry_run")]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "naive": {
            "decisions": len(would) + len(passed), "would_trade": len(would), "would_pass": len(passed),
            "contracts": contracts, "aggregate_max_loss": str(risk), "mark_to_market_now": str(pnl_now),
            "trades": [
                {"at": t.at, "short": t.short_symbol, "long": t.long_symbol, "quantity": t.quantity,
                 "credit": str(t.credit), "width": str(t.width), "rationale": t.reason,
                 "pnl_if_taken": None if t.pnl_if_taken is None else str(t.pnl_if_taken)}
                for t in scored
            ],
        },
        "gated": {"orders_submitted_live": len(real_orders)},
        "note": ("Same model, same live chain, same spreads. The naive agent has no regime filter, "
                 "no challenger, no Monte Carlo, no floor and no position limit. It never submits; "
                 "its would-be trades are re-priced at current marks by the same scorer that grades "
                 "the real agent's refusals."),
    }, indent=2) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
