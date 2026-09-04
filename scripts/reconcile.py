"""Prove that every order at the broker traces to a decision in the ledger.

    python scripts/reconcile.py     # -> public/reconcile.json

Two directions. Every order Alpaca holds for the account must match a ledger
record that submitted it, or it is an order this agent cannot account for.
Every live submission in the ledger must exist at the broker, or the ledger
is claiming something the broker never saw. Both counts are published.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.execution import AlpacaCLIExecutor  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402

OUT = Path("public/reconcile.json")
SUBMITTING = {"order_submitted", "close_submitted", "risk_reduction", "order_repriced"}


def main() -> int:
    entries = DecisionLedger().entries()
    known: dict[str, dict] = {}
    for e in entries:
        if e.get("kind") not in SUBMITTING or e.get("dry_run"):
            continue
        for key in ("order_id", "replaced"):
            oid = e.get(key)
            if oid:
                known.setdefault(oid, {"kind": e["kind"], "decision": e["id"], "at": e["at"]})
    by_client = {
        e["client_order_id"]: e for e in entries if e.get("client_order_id")
    }

    orders = AlpacaCLIExecutor().orders(status="all", limit=500)
    rows = []
    untraced = 0
    for o in sorted(orders, key=lambda x: x.get("submitted_at", "")):
        oid = o.get("id", "")
        coid = o.get("client_order_id") or ""
        link = known.get(oid)
        via = "order_id"
        if link is None and coid in by_client:
            link = {"kind": by_client[coid]["kind"], "decision": by_client[coid]["id"],
                    "at": by_client[coid]["at"]}
            via = "client_order_id"
        if link is None:
            untraced += 1
            via = "UNTRACED"
        rows.append(
            {
                "order_id": oid,
                "client_order_id": coid,
                "submitted_at": o.get("submitted_at"),
                "status": o.get("status"),
                "qty": o.get("qty"),
                "limit_price": o.get("limit_price"),
                "filled_avg_price": o.get("filled_avg_price"),
                "legs": [
                    {"symbol": leg.get("symbol"), "side": leg.get("side")}
                    for leg in (o.get("legs") or [])
                ],
                "ledger_kind": link["kind"] if link else None,
                "decision_id": link["decision"] if link else None,
                "traced_via": via,
            }
        )

    broker_ids = {o.get("id") for o in orders}
    orphaned = sorted(oid for oid in known if oid not in broker_ids)
    summary = {
        "broker_orders": len(rows),
        "traced": len(rows) - untraced,
        "untraced": untraced,
        "traced_via_client_order_id": sum(1 for r in rows if r["traced_via"] == "client_order_id"),
        "ledger_submissions": len(known),
        "ledger_orders_missing_at_broker": len(orphaned),
        "filled": sum(1 for r in rows if r["status"] == "filled"),
        "canceled": sum(1 for r in rows if r["status"] == "canceled"),
        "clean": untraced == 0 and not orphaned,
    }
    print(f"broker orders {summary['broker_orders']}  traced {summary['traced']}  "
          f"untraced {summary['untraced']}  ledger-only {summary['ledger_orders_missing_at_broker']}")
    for r in rows:
        print(f"  {str(r['submitted_at'])[:16]}  {r['status']:9s} {str(r['qty']):>3s}x @ {r['limit_price']}"
              f"  {r['traced_via']:16s} {r['ledger_kind'] or '-'}")
    if orphaned:
        print("ledger order ids not at broker:", orphaned)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "reconciled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "summary": summary,
                "orders": rows,
                "ledger_orders_missing_at_broker": orphaned,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
