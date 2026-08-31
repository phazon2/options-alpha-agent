"""One autonomous decision cycle: read the chain, propose, gate, execute, log.

    python scripts/run_once.py            # abstain unless everything checks out
    python scripts/run_once.py --dry-run  # build and gate, but do not submit
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.execution import AlpacaCLIExecutor, ExecutionError, Leg  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402
from agent.risk import AccountState, RiskGate, SpreadProposal  # noqa: E402
from agent.strategy import NoTradeFound, parse_chain, select_put_credit_spread  # noqa: E402

UNDERLYING = "SPY"


def pick_expiry(today: date) -> str:
    """Nearest Friday at least one day out, so the position has time to decay."""
    ahead = (4 - today.weekday()) % 7 or 7
    return (today + timedelta(days=ahead)).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expiry", default=None)
    args = parser.parse_args()

    ledger = DecisionLedger()
    executor = AlpacaCLIExecutor()
    broker = AlpacaPaper()

    clock = broker.clock()
    account_raw = executor.account()
    equity = Decimal(account_raw["equity"])
    positions = executor.positions()

    expiry = args.expiry or pick_expiry(date.today())
    ledger.record(
        "cycle_start",
        market_open=clock.is_open,
        equity=str(equity),
        open_positions=len(positions),
        expiry=expiry,
    )
    print(f"market open   {clock.is_open}")
    print(f"equity        ${equity:,.2f}")
    print(f"positions     {len(positions)}")
    print(f"expiry        {expiry}")

    if not clock.is_open:
        ledger.record("abstain", reason="market closed", next_open=clock.next_open)
        print(f"\nABSTAIN — market closed, next open {clock.next_open}")
        return 0

    spot = float(broker.latest_stock_bar(UNDERLYING).get("c") or 0)
    print(f"spot          ${spot:,.2f}")
    chain = broker.option_chain(
        UNDERLYING,
        expiration_date=expiry,
        option_type="put",
        feed="indicative",
        strike_gte=spot * 0.90,
        strike_lte=spot * 1.02,
    )
    candidates = parse_chain(chain)
    print(f"candidates    {len(candidates)} quoted puts with greeks")

    try:
        proposal, short, long_leg = select_put_credit_spread(candidates, UNDERLYING)
    except NoTradeFound as exc:
        ledger.record("abstain", reason=str(exc), candidates=len(candidates))
        print(f"\nABSTAIN — {exc}")
        return 0

    print(
        f"\nproposal      sell {short.symbol} (delta {short.delta:.3f}, bid {short.bid})"
        f"\n              buy  {long_leg.symbol} (ask {long_leg.ask})"
        f"\n              width ${proposal.width}  credit ${proposal.credit}"
    )

    gate = RiskGate()
    verdict = gate.evaluate(
        proposal, AccountState(equity=equity, open_positions=len(positions))
    )
    print(f"risk gate     {verdict.summary}")
    ledger.record(
        "risk_verdict",
        approved=verdict.approved,
        reasons=verdict.reasons,
        short=proposal.short_symbol,
        long=proposal.long_symbol,
        credit=str(proposal.credit),
        width=str(proposal.width),
        max_loss=str(verdict.max_loss),
        quantity=verdict.approved_quantity,
        short_delta=short.delta,
        short_iv=short.implied_volatility,
    )
    if not verdict.approved:
        print("\nABSTAIN — the gate vetoed this trade")
        return 0

    legs = [
        Leg(proposal.short_symbol, "sell", "sell_to_open"),
        Leg(proposal.long_symbol, "buy", "buy_to_open"),
    ]
    try:
        result = executor.submit_multileg(
            legs,
            qty=verdict.approved_quantity,
            limit_price=float(proposal.credit),
            dry_run=args.dry_run,
        )
    except ExecutionError as exc:
        ledger.record("execution_error", error=str(exc))
        print(f"\nEXECUTION FAILED — {exc}", file=sys.stderr)
        return 1

    ledger.record(
        "order_submitted",
        dry_run=args.dry_run,
        argv=result.argv[1:],
        order_id=result.order_id,
        status=result.status,
        legs=result.legs,
        max_loss=str(verdict.max_loss),
    )
    print(
        f"\n{'DRY RUN' if args.dry_run else 'SUBMITTED'}  "
        f"order {result.order_id or '(dry run)'}  status {result.status or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
