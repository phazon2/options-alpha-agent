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
from agent.challenger import challenge  # noqa: E402
from agent.broker import AlpacaPaper  # noqa: E402
from agent.positions import assemble  # noqa: E402
from agent.regime import read_regime  # noqa: E402
from agent.var import simulate  # noqa: E402
from agent.execution import AlpacaCLIExecutor, ExecutionError, Leg  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402
from dataclasses import replace  # noqa: E402

from agent.risk import AccountState, RiskGate, RiskLimits, SpreadProposal  # noqa: E402
from agent.strategy import (  # noqa: E402
    NoTradeFound,
    parse_chain,
    select_call_credit_spread,
    select_put_credit_spread,
)

UNDERLYING = "SPY"


# Expiries to consider, in days from today. Two days out is where theta is
# richest but gamma risk is worst — the challenger's standing objection — so
# the agent also looks a week or two ahead, where the credit is larger and an
# adverse day is survivable, and lets the credit-to-width comparison decide.
# 1 DTE included deliberately. Sweeping the chain on 3 September, no width or
# delta at 7 DTE had positive expected value, while every structure at 1 DTE
# did: at one day a sigma on SPY is about $4.40, so a strike eight points out
# sits near two sigma and the premium finally clears the breach probability.
CANDIDATE_DTE = (1, 2, 3, 7)


def candidate_expiries(today: date, broker, underlying: str) -> list[str]:
    """Expiries that actually exist, nearest first."""
    wanted = {(today + timedelta(days=d)).isoformat() for d in CANDIDATE_DTE}
    contracts = broker.option_contracts(
        underlying,
        expiration_gte=(today + timedelta(days=1)).isoformat(),
        expiration_lte=(today + timedelta(days=max(CANDIDATE_DTE))).isoformat(),
        limit=2000,
    )
    listed = {c["expiration_date"] for c in contracts}
    return sorted(wanted & listed)


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
    # Count spreads the same way the exit manager does. Halving the leg count
    # is not the same thing: it counts stock positions and orphaned legs as
    # half a spread each, so the risk gate and the manager could disagree
    # about how much is open.
    open_spreads = len(assemble(positions))

    expiries = (
        [args.expiry]
        if args.expiry
        else candidate_expiries(date.today(), broker, UNDERLYING)
    )
    expiry = expiries[0] if expiries else pick_expiry(date.today())
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
    side = regime.preferred_side
    allowed = (
        regime.allows_put_credit_spread
        if side == "put"
        else regime.allows_call_credit_spread
    )
    if not allowed:
        ledger.record(
            "abstain", reason=f"regime is {regime.regime}: {regime.reason}", side=side
        )
        print(f"\nABSTAIN — neither side is supportable in a {regime.regime} regime")
        return 0
    print(f"side          {side} credit spread (trend argues this way)")

    # --- scan every expiry and keep the best EXPECTED VALUE, not the best ratio ---
    # Credit-to-width is a proxy and it lies. On 3 September it picked a 7-day
    # spread at 0.18 over a 1-day spread at 0.15, and the 7-day structure had
    # negative expected value at every width and delta while every 1-day one
    # was positive. Rank by what the risk officer computes, which is the thing
    # actually being maximised.
    selector = select_put_credit_spread if side == "put" else select_call_credit_spread
    best = None
    for candidate_expiry in expiries:
        cand_dte = max((date.fromisoformat(candidate_expiry) - date.today()).days, 1)
        cand_vol = regime.realised_vol_over(cand_dte)
        chain = broker.option_chain(
            UNDERLYING,
            expiration_date=candidate_expiry,
            option_type=side,
            feed="indicative",
            strike_gte=spot * 0.88,
            strike_lte=spot * 1.12,
        )
        candidates = parse_chain(chain)
        if not candidates:
            continue
        try:
            found = selector(candidates, UNDERLYING)
        except NoTradeFound:
            continue
        prop, sh, lg = found
        probe = simulate(
            spot=spot,
            short_strike=sh.strike,
            long_strike=lg.strike,
            credit=prop.credit,
            quantity=20,
            days_to_expiry=cand_dte,
            annual_vol=cand_vol,
            is_put=(side == "put"),
        )
        print(
            f"  {candidate_expiry} ({cand_dte}d)  ${prop.credit} on ${prop.width} wide"
            f"  PoP {probe.probability_of_profit:.0%}  E[P&L] {probe.expected_pnl:+,.0f}"
            f"  {'edge' if probe.edge_is_significant else 'noise'}"
        )
        if not probe.edge_is_significant:
            continue
        if best is None or probe.expected_pnl > best[0]:
            best = (probe.expected_pnl, candidate_expiry, found)

    if best is None:
        ledger.record(
            "abstain",
            reason="no expiry offered a structure with a significant edge",
            expiries=expiries,
        )
        print("\nABSTAIN — no expiry offered a structure with a significant edge")
        return 0

    _, expiry, (proposal, short, long_leg) = best
    print(
        f"chose         {expiry}: sell {short.symbol} (delta {short.delta:.3f}) "
        f"/ buy {long_leg.symbol}"
        f"\n              width ${proposal.width}  credit ${proposal.credit}"
    )

    dte = max((date.fromisoformat(expiry) - date.today()).days, 1)
    matched_vol = regime.realised_vol_over(dte)
    if short.implied_volatility is not None:
        vrp = (short.implied_volatility - matched_vol) * 100
        print(
            f"vol           IV {short.implied_volatility*100:.2f}% vs {dte}d realised "
            f"{matched_vol*100:.2f}% -> VRP {vrp:+.2f} vol pts "
            f"(long window {regime.realised_vol*100:.2f}%)"
        )
    analyst_context = {
        "underlying": UNDERLYING,
        "spot": regime.spot,
        "regime": regime.regime,
        "regime_reason": regime.reason,
        "implied_vol_short_leg": short.implied_volatility,
        "realised_vol_matched_to_expiry": round(matched_vol, 4),
        "realised_vol_long_window": round(regime.realised_vol, 4),
        "variance_risk_premium_vol_points": (
            round((short.implied_volatility - matched_vol) * 100, 2)
            if short.implied_volatility is not None
            else None
        ),
        "return_20d_pct": round(regime.return_20d * 100, 2),
        "sma_5": round(regime.sma_short, 2),
        "sma_20": round(regime.sma_long, 2),
        "available_expiries_dte": [
            (date.fromisoformat(e) - date.today()).days for e in expiries
        ],
        "open_spreads": open_spreads,
        "equity": str(equity),
        "expiry": expiry,
        "side": side,
        "best_available": {
            "expiry": expiry,
            "days_to_expiry": (date.fromisoformat(expiry) - date.today()).days,
            "short_symbol": short.symbol,
            "short_delta": round(short.delta, 4),
            "short_iv": short.implied_volatility,
            "width": str(proposal.width),
            "credit": str(proposal.credit),
            "credit_to_width": round(float(proposal.credit / proposal.width), 4),
        },
        "structure": (
            f"{side} credit spread, 1-5 wide, defined risk. A put spread "
            f"profits if SPY holds above the short strike; a call spread "
            f"profits if it stays below."
        ),
        "note": (
            "Compare implied against realised_vol_matched_to_expiry, NOT the "
            "long window: a 7-day option's premium is not judged against two "
            "months of realised volatility. variance_risk_premium_vol_points "
            "is already the correct difference. "
            "best_available is the actual spread the agent will place, already "
            "chosen as the best credit-per-width across every listed expiry. "
            "These are live quotes, not estimates - judge the spread shown "
            "rather than speculating about what the credit might be. The risk "
            "gate separately enforces a 0.15 credit-to-width minimum."
        ),
        # Read from the live limits. Hardcoding these meant the analyst was
        # rejecting trades against a floor the gate had already moved off.
        "risk_caps": {
            "max_loss_per_trade_usd": float(RiskLimits.from_env().max_loss_per_trade),
            "min_credit_to_width": float(RiskLimits.from_env().min_credit_to_width),
        },
    }
    view = consult(analyst_context)
    print(f"analyst       trade={view.trade} conf={view.confidence} delta={view.target_delta}")
    print(f"              {view.rationale}")
    if view.invalidated_if:
        print(f"  wrong if    {view.invalidated_if}")
    ledger.record(
        "analyst_view",
        model=view.model,
        trade=view.trade,
        confidence=view.confidence,
        target_delta=view.target_delta,
        rationale=view.rationale,
        invalidated_if=view.invalidated_if,
        clamped=view.clamped,
        failed=view.failed,
    )
    if not view.trade:
        ledger.record("abstain", reason=f"analyst declined: {view.rationale}")
        print("\nABSTAIN — the analyst declined")
        return 0

    # --- challenger: tries to destroy the thesis; only survivors proceed ---
    objection = challenge(view, analyst_context)
    print(
        f"challenger    refuted={objection.refuted} severity={objection.severity}"
        f"\n              {objection.argument}"
    )
    ledger.record(
        "challenge",
        refuted=objection.refuted,
        severity=objection.severity,
        argument=objection.argument,
        failed=objection.failed,
        blocks=objection.blocks_trade,
        size_multiplier=objection.size_multiplier,
    )
    if objection.blocks_trade:
        ledger.record(
            "abstain", reason=f"challenger ({objection.severity}): {objection.argument}"
        )
        print("\nABSTAIN — the thesis did not survive the challenger")
        return 0

    # Ask for the size the risk budget actually supports. The gate can only
    # cap a proposal, never enlarge it, so proposing 1 contract would leave
    # the budget unused and make P&L structurally negligible.
    limits = RiskLimits.from_env()
    per_contract_loss = (proposal.width - proposal.credit) * 100
    budget = min(limits.max_loss_per_trade, equity * limits.max_equity_fraction_per_trade)
    desired = max(1, int(budget / per_contract_loss)) if per_contract_loss > 0 else 1
    if objection.size_multiplier < 1.0:
        reduced = max(1, int(desired * objection.size_multiplier))
        why = (
            "challenger unreachable, check lost"
            if objection.failed
            else f"challenger objection ({objection.severity})"
        )
        print(f"              {why} cuts size {desired} -> {reduced}")
        desired = reduced
    proposal = replace(proposal, quantity=desired)
    print(f"budget        ${budget:,.2f} -> want {desired}x (${per_contract_loss:,.2f}/contract)")

    # --- risk officer: simulate the trade's distribution before allowing it ---
    # Max loss says what can be lost; it says nothing about how likely that is.
    # A trade whose expected value is negative is refused however much premium
    # it appears to pay.
    var_report = simulate(
        spot=spot,
        short_strike=short.strike,
        long_strike=long_leg.strike,
        credit=proposal.credit,
        quantity=desired,
        days_to_expiry=max((date.fromisoformat(expiry) - date.today()).days, 1),
        annual_vol=matched_vol,
        is_put=(side == "put"),
    )
    print(f"risk officer  {var_report.summary}")
    ledger.record(
        "var_report",
        paths=var_report.paths,
        probability_of_profit=round(var_report.probability_of_profit, 4),
        expected_pnl=round(var_report.expected_pnl, 2),
        var_95=round(var_report.var_95, 2),
        expected_shortfall=round(var_report.expected_shortfall, 2),
        std_error=round(var_report.std_error, 2),
        edge_significant=var_report.edge_is_significant,
        max_loss=round(var_report.max_loss, 2),
    )
    if not var_report.edge_is_significant:
        ledger.record(
            "abstain",
            reason=(
                f"expected value not distinguishable from zero: E[P&L] "
                f"${var_report.expected_pnl:,.0f} against a standard error of "
                f"${var_report.std_error:,.0f} over {var_report.paths:,} paths"
            ),
            expected_pnl=round(var_report.expected_pnl, 2),
            std_error=round(var_report.std_error, 2),
        )
        print("\nABSTAIN — the edge is not distinguishable from simulation noise")
        return 0

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
        side=side,
        expiry=expiry,
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
