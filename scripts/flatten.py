"""Close every open spread, so nothing is left running unattended.

    python scripts/flatten.py [--dry-run]

The exit manager holds a position until it hits its profit target or its stop.
That is the right rule while someone is running the agent every few minutes,
and the wrong one on the last day: a short strike that nobody is watching over
a weekend is unmanaged risk, not a position.

So this is a deliberate human override, and it is recorded as one. Each spread
is bought back at a penny through the current mark - getting out matters more
than the penny - and every close names the position review that produced it.
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
    parser.add_argument(
        "--reason",
        default="end of session: flattening so no position is held unattended",
    )
    args = parser.parse_args()

    ledger = DecisionLedger()
    executor = AlpacaCLIExecutor()
    clock = AlpacaPaper().clock()

    spreads = assemble(executor.positions())
    print(f"market open   {clock.is_open}")
    print(f"open spreads  {len(spreads)}")
    if not spreads:
        print("\nnothing to flatten")
        return 0
    if not clock.is_open and not args.dry_run:
        print("\nmarket closed — cannot flatten now", file=sys.stderr)
        return 1

    closed = 0
    for spread in spreads:
        if spread.is_naked:
            # A lone short leg has no partner to sell; closing it is a single-leg
            # buy, which this executor does not send. Say so rather than skip it.
            print(f"NAKED  {spread.short.symbol} x{abs(spread.short.qty)} — close by hand")
            ledger.record(
                "flatten_skipped",
                short=spread.short.symbol,
                quantity=abs(spread.short.qty),
                reason="naked short leg: no paired long to close against",
            )
            continue

        debit = float(spread.cost_to_close)
        limit = round(debit + 0.01, 2)
        print(
            f"CLOSE  {spread.short.symbol}/{spread.long.symbol} x{spread.quantity}"
            f"\n       credit {spread.credit_received} · costs {spread.cost_to_close}"
            f" to close · open P&L ${spread.open_pnl:+,.2f} · limit {limit}"
        )
        review_id = ledger.record(
            "position_review",
            short=spread.short.symbol,
            long=spread.long.symbol,
            quantity=spread.quantity,
            credit=str(spread.credit_received),
            cost_to_close=str(spread.cost_to_close),
            open_pnl=str(spread.open_pnl),
            action="flatten",
            reason=args.reason,
        )
        legs = [
            Leg(spread.short.symbol, "buy", "buy_to_close"),
            Leg(spread.long.symbol, "sell", "sell_to_close"),
        ]
        try:
            result = executor.submit_multileg(
                legs,
                qty=spread.quantity,
                net_limit=limit,
                allow_debit=True,
                client_order_id=f"oaa-flat-{review_id}",
                dry_run=args.dry_run,
            )
        except ExecutionError as exc:
            ledger.record("execution_error", error=str(exc), context="flatten")
            print(f"       FLATTEN FAILED — {exc}", file=sys.stderr)
            continue
        ledger.record(
            "close_submitted",
            dry_run=args.dry_run,
            order_id=result.order_id,
            client_order_id=f"oaa-flat-{review_id}",
            decision=review_id,
            status=result.status,
            argv=result.argv[1:],
            action="flatten",
        )
        print(f"       {'DRY RUN' if args.dry_run else 'SUBMITTED'} {result.order_id or ''}")
        closed += 1

    print(f"\n{closed} close order(s) {'simulated' if args.dry_run else 'submitted'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
