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

from agent.analyst import consult  # noqa: E402
from agent.broker import AlpacaPaper  # noqa: E402
from agent.regime import read_regime  # noqa: E402
from agent.execution import AlpacaCLIExecutor, ExecutionError, Leg  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402
from dataclasses import replace  # noqa: E402

from agent.risk import AccountState, RiskGate, RiskLimits, SpreadProposal  # noqa: E402
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
    # Each vertical spread holds two option legs; count spreads, not legs.
    open_spreads = len(positions) // 2

    expiry = args.expiry or pick_expiry(date.today())
    ledger.record(
        "cycle_start",
        market_open=clock.is_open,
        equity=str(equity),
        open_positions=open_spreads,
        expiry=expiry,
    )
    print(f"market open   {clock.is_open}")
    print(f"equity        ${equity:,.2f}")
    print(f"open spreads  {open_spreads} ({len(positions)} legs)")
    print(f"expiry        {expiry}")

    if not clock.is_open:
        ledger.record("abstain", reason="market closed", next_open=clock.next_open)
        print(f"\nABSTAIN — market closed, next open {clock.next_open}")
        return 0

    spot = float(broker.latest_stock_bar(UNDERLYING).get("c") or 0)
    print(f"spot          ${spot:,.2f}")
    # --- regime: deterministic, and it can veto before any model is asked ---
    bars = broker.daily_bars(UNDERLYING, lookback_days=60)
    regime = read_regime(bars)
    print(f"regime        {regime.regime} — {regime.reason}")
    ledger.record(
        "regime",
        regime=regime.regime,
        reason=regime.reason,
        spot=regime.spot,
        sma_short=regime.sma_short,
        sma_long=regime.sma_long,
        return_20d=regime.return_20d,
        realised_vol=regime.realised_vol,
    )
    if not regime.allows_put_credit_spread:
        ledger.record("abstain", reason=f"regime is {regime.regime}: {regime.reason}")
        print(f"\nABSTAIN — a put credit spread is the wrong side of a {regime.regime} regime")
        return 0

    # --- analyst: proposes only, and may decline ---
    view = consult(
        {
            "underlying": UNDERLYING,
            "spot": regime.spot,
            "regime": regime.regime,
            "regime_reason": regime.reason,
            "realised_vol_annualised": round(regime.realised_vol, 4),
            "return_20d_pct": round(regime.return_20d * 100, 2),
            "sma_5": round(regime.sma_short, 2),
            "sma_20": round(regime.sma_long, 2),
            "open_spreads": open_spreads,
            "equity": str(equity),
            "expiry": expiry,
            "structure": "put credit spread, 1-5 wide, defined risk",
            "risk_caps": {"max_loss_per_trade_usd": 1500, "min_credit_to_width": 0.15},
        }
    )
    print(f"analyst       trade={view.trade} conf={view.confidence} delta={view.target_delta}")
    print(f"              {view.rationale}")
    ledger.record(
        "analyst_view",
        model=view.model,
        trade=view.trade,
        confidence=view.confidence,
        target_delta=view.target_delta,
        rationale=view.rationale,
        clamped=view.clamped,
        failed=view.failed,
    )
    if not view.trade:
        ledger.record("abstain", reason=f"analyst declined: {view.rationale}")
        print("\nABSTAIN — the analyst declined")
        return 0

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
        proposal, short, long_leg = select_put_credit_spread(
            candidates, UNDERLYING, target_delta=view.target_delta
        )
    except NoTradeFound as exc:
        ledger.record("abstain", reason=str(exc), candidates=len(candidates))
        print(f"\nABSTAIN — {exc}")
        return 0

    print(
        f"\nproposal      sell {short.symbol} (delta {short.delta:.3f}, bid {short.bid})"
        f"\n              buy  {long_leg.symbol} (ask {long_leg.ask})"
        f"\n              width ${proposal.width}  credit ${proposal.credit}"
    )

    # Ask for the size the risk budget actually supports. The gate can only
    # cap a proposal, never enlarge it, so proposing 1 contract would leave
    # the budget unused and make P&L structurally negligible.
    limits = RiskLimits.from_env()
    per_contract_loss = (proposal.width - proposal.credit) * 100
    budget = min(limits.max_loss_per_trade, equity * limits.max_equity_fraction_per_trade)
    desired = max(1, int(budget / per_contract_loss)) if per_contract_loss > 0 else 1
    proposal = replace(proposal, quantity=desired)
    print(f"budget        ${budget:,.2f} -> want {desired}x (${per_contract_loss:,.2f}/contract)")

    gate = RiskGate(limits)
    verdict = gate.evaluate(
        proposal, AccountState(equity=equity, open_positions=open_spreads)
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
            net_limit=-float(proposal.credit),
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
