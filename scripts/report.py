"""Generate the dashboard's data file from live account state and the ledger.

Runs after every trading cycle. Everything it writes comes from a live Alpaca
read or from the append-only ledger — no figure on the dashboard is computed
from a mock or carried over from a previous run.

    python scripts/report.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.execution import AlpacaCLIExecutor  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402

OUT = Path("public/data.json")
STARTING_EQUITY = Decimal("100000")


def main() -> int:
    executor = AlpacaCLIExecutor()
    account = executor.account()
    positions = executor.positions()
    orders = executor.orders(status="all", limit=200)
    entries = DecisionLedger().entries()

    equity = Decimal(account["equity"])
    pnl = equity - STARTING_EQUITY

    verdicts = [e for e in entries if e.get("kind") == "risk_verdict"]
    vetoes = [e for e in verdicts if not e.get("approved")]
    abstentions = [e for e in entries if e.get("kind") == "abstain"]
    submitted = [e for e in entries if e.get("kind") == "order_submitted"]
    filled = [o for o in orders if o.get("status") == "filled"]

    # Group option legs into the spreads they belong to.
    legs = [
        {
            "symbol": p["symbol"],
            "qty": p["qty"],
            "entry": p["avg_entry_price"],
            "current": p.get("current_price"),
            "unrealized": p.get("unrealized_pl"),
        }
        for p in positions
    ]
    unrealized = sum(Decimal(str(p.get("unrealized_pl") or 0)) for p in positions)

    # Equity over time, taken from the agent's own live reads in the ledger.
    # Alpaca's intraday portfolio-history series is unusable in paper: it
    # reports exactly $100,000 too high (200,151.93 against a true 100,151.93),
    # while the daily series is correct. Our own cycle reads avoid the bug and
    # are higher resolution than daily.
    curve = []
    for e in entries:
        if e.get("kind") == "cycle_start" and e.get("equity"):
            try:
                curve.append({"at": e["at"], "equity": float(e["equity"])})
            except (TypeError, ValueError):
                continue
    curve.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "equity": float(equity)})
    # Collapse repeated reads at the same minute.
    deduped = []
    for point in curve:
        if deduped and deduped[-1]["at"][:16] == point["at"][:16]:
            deduped[-1] = point
        else:
            deduped.append(point)

    frontier = {}
    refusals = {}
    shadow = {}
    scan = {}
    for name, target in (("frontier.json", "frontier"), ("refusals.json", "refusals"),
                         ("shadow.json", "shadow"), ("scan.json", "scan"),
                         ("falsification.json", "falsification"),
                         ("reconcile.json", "reconcile")):
        path = Path("public") / name
        if path.exists():
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if target == "frontier":
                frontier = payload
            elif target == "refusals":
                refusals = payload
            elif target == "shadow":
                shadow = payload
            else:
                scan = payload

    data = {
        "equity_curve": deduped,
        "frontier": frontier,
        "refusals": refusals,
        "shadow": shadow,
        "scan": scan,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": {
            "number": account["account_number"],
            "equity": str(equity),
            "cash": account["cash"],
            "starting_equity": str(STARTING_EQUITY),
            "pnl": str(pnl),
            "pnl_pct": str((pnl / STARTING_EQUITY * 100).quantize(Decimal("0.001"))),
            "options_level": account.get("options_trading_level"),
            "unrealized": str(unrealized),
        },
        "counters": {
            "decisions": len(verdicts),
            "approved": len(verdicts) - len(vetoes),
            "vetoed": len(vetoes),
            "abstained": len(abstentions),
            "orders_submitted": len(submitted),
            "orders_filled": len(filled),
            "open_legs": len(positions),
        },
        "positions": legs,
        "orders": [
            {
                "id": o.get("id"),
                "status": o.get("status"),
                "qty": o.get("qty"),
                "limit_price": o.get("limit_price"),
                "filled_avg_price": o.get("filled_avg_price"),
                "submitted_at": o.get("submitted_at"),
                "legs": [
                    {
                        "symbol": leg.get("symbol"),
                        "side": leg.get("side"),
                        "filled_avg_price": leg.get("filled_avg_price"),
                    }
                    for leg in (o.get("legs") or [])
                ],
            }
            for o in orders[:25]
        ],
        "ledger": entries[-60:],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, default=str) + "\n")
    print(f"equity      ${equity:,.2f}  (P&L ${pnl:+,.2f})")
    print(f"decisions   {len(verdicts)}  vetoed {len(vetoes)}  abstained {len(abstentions)}")
    print(f"orders      {len(submitted)} submitted, {len(filled)} filled")
    print(f"wrote       {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
