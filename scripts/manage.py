"""Manage open spreads: take profit at half the credit, or cut at the stop.

    python scripts/manage.py            # act on what the rules say
    python scripts/manage.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.execution import AlpacaCLIExecutor, ExecutionError, Leg  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402
from agent.positions import assemble  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ledger = DecisionLedger()
    executor = AlpacaCLIExecutor()
    clock = AlpacaPaper().clock()

    spreads = assemble(executor.positions())
    if not spreads:
        print("no open spreads")
        return 0

    print(f"market open   {clock.is_open}")
    print(f"open spreads  {len(spreads)}\n")

    acted = 0
    for spread in spreads:
        action, why = spread.decide()
        tag = {"hold": "HOLD", "take_profit": "TAKE PROFIT", "stop_out": "STOP"}[action]
        print(
            f"{tag:<12} {spread.short.symbol}/{spread.long.symbol} x{spread.quantity}"
            f"\n             credit {spread.credit_received}  now costs "
            f"{spread.cost_to_close} to close  open P&L ${spread.open_pnl:+,.2f}"
            f"\n             {why}"
        )
        review_id = ledger.record(
            "position_review",
            short=spread.short.symbol,
            long=spread.long.symbol,
            quantity=spread.quantity,
            credit=str(spread.credit_received),
            cost_to_close=str(spread.cost_to_close),
            captured=str(spread.captured_fraction.quantize(spread.credit_received or 1)),
            open_pnl=str(spread.open_pnl),
            action=action,
            reason=why,
        )
        if action == "hold":
            continue
        if not clock.is_open:
            print("             market closed — deferring the exit to the next session")
            continue

        # Closing reverses both legs: buy back the short, sell the long.
        legs = [
            Leg(spread.short.symbol, "buy", "buy_to_close"),
            Leg(spread.long.symbol, "sell", "sell_to_close"),
        ]
        # Closing costs a debit, so the limit is positive. Pay a cent over the
        # current mark on a stop, because getting out matters more than the
        # cent; hold the mark on a profit take, where there is no urgency.
        debit = float(spread.cost_to_close)
        limit = round(debit + (0.01 if action == "stop_out" else 0.0), 2)
        try:
            result = executor.submit_multileg(
                legs,
                qty=spread.quantity,
                net_limit=limit,
                allow_debit=True,
                client_order_id=f"oaa-close-{review_id}",
                dry_run=args.dry_run,
            )
        except ExecutionError as exc:
            ledger.record("execution_error", error=str(exc), context="close")
            print(f"             CLOSE FAILED — {exc}", file=sys.stderr)
            continue
        ledger.record(
            "close_submitted",
            dry_run=args.dry_run,
            order_id=result.order_id,
            client_order_id=f"oaa-close-{review_id}",
            decision=review_id,
            status=result.status,
            argv=result.argv[1:],
            action=action,
        )
        acted += 1
        print(f"             submitted {result.order_id or '(dry run)'}")

    print(f"\n{acted} exit order(s) submitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
