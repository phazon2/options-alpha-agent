"""Re-price resting orders that are not filling.

A limit order that never fills is the same as no order at all, and on the last
full session of a competition that is the expensive kind of nothing. An order
priced at mid will sit unfilled whenever the multi-leg book is thin, so after a
few minutes the agent concedes a cent at a time rather than holding out.

It concedes toward the bid - accepting less credit - and stops at a floor,
because the risk gate's credit-to-width minimum still has to hold. A trade is
worth chasing only down to the point where it stops being the trade that was
approved.

    python scripts/reprice.py            # step stale orders toward fillable
    python scripts/reprice.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.execution import AlpacaCLIExecutor, ExecutionError, Leg  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402
from agent.risk import RiskLimits  # noqa: E402

STALE_AFTER_SECONDS = 240
CONCESSION = Decimal("0.01")


def age_seconds(submitted_at: str) -> float:
    try:
        stamp = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ledger = DecisionLedger()
    executor = AlpacaCLIExecutor()
    floor = RiskLimits.from_env().min_credit_to_width

    resting = executor.open_orders()
    if not resting:
        print("no resting orders")
        return 0

    for order in resting:
        age = age_seconds(order.get("submitted_at", ""))
        limit = Decimal(str(order.get("limit_price") or 0))
        qty = int(order.get("qty") or 1)
        legs = order.get("legs") or []
        print(f"{order['id'][:8]}  {qty}x @ {limit}  age {age:,.0f}s  status {order.get('status')}")

        if age < STALE_AFTER_SECONDS:
            print(f"          fresh - leaving it to work")
            continue
        if limit >= 0 or len(legs) != 2:
            print("          not an open credit spread - skipping")
            continue

        # Widths come from the strikes in the OCC symbols.
        strikes = sorted(Decimal(l["symbol"][-8:]) / 1000 for l in legs)
        width = strikes[1] - strikes[0]
        new_limit = limit + CONCESSION  # less negative = accepting less credit
        new_credit = -new_limit
        if width > 0 and (new_credit / width) < floor:
            print(
                f"          conceding to {new_credit} would put credit/width "
                f"{new_credit / width:.3f} under the {floor} floor - cancelling instead"
            )
            if not args.dry_run:
                executor.cancel(order["id"])
                ledger.record("order_cancelled", order_id=order["id"],
                              reason="conceding further would breach the credit floor")
            continue

        print(f"          stale - conceding {limit} -> {new_limit}")
        if args.dry_run:
            continue
        try:
            executor.cancel(order["id"])
            short = next(l for l in legs if l["side"] == "sell")
            long_leg = next(l for l in legs if l["side"] == "buy")
            result = executor.submit_multileg(
                [
                    Leg(short["symbol"], "sell", "sell_to_open"),
                    Leg(long_leg["symbol"], "buy", "buy_to_open"),
                ],
                qty=qty,
                net_limit=float(new_limit),
            )
        except ExecutionError as exc:
            ledger.record("execution_error", error=str(exc), context="reprice")
            print(f"          REPRICE FAILED - {exc}", file=sys.stderr)
            continue
        ledger.record(
            "order_repriced",
            replaced=order["id"],
            order_id=result.order_id,
            was=str(limit),
            now=str(new_limit),
            age_seconds=round(age),
        )
        print(f"          resubmitted as {result.order_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
