"""Run the naive agent one cycle, dry-run, and log what it would have done.

    python scripts/shadow.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402
from agent.shadow import decide  # noqa: E402
from agent.strategy import NoTradeFound, parse_chain, select_put_credit_spread  # noqa: E402

SHADOW = Path("docs/ledger/shadow.jsonl")
UNDERLYING = "SPY"


def main() -> int:
    ledger = DecisionLedger(SHADOW)
    broker = AlpacaPaper()
    clock = broker.clock()
    if not clock.is_open:
        print("market closed")
        return 0
    spot = float(broker.latest_stock_bar(UNDERLYING).get("c") or 0)
    today = date.today()
    # Show it the same expiries the real agent scans, nearest first.
    seen = []
    for dte in (1, 2, 7):
        expiry = (today + timedelta(days=dte)).isoformat()
        chain = broker.option_chain(UNDERLYING, expiration_date=expiry, option_type="put",
                                    feed="indicative", strike_gte=spot * 0.9, strike_lte=spot * 1.01)
        cands = parse_chain(chain)
        if not cands:
            continue
        try:
            prop, short, long_leg = select_put_credit_spread(cands, UNDERLYING)
        except NoTradeFound:
            continue
        seen.append((expiry, dte, prop, short, long_leg))
    if not seen:
        print("no spread to show it")
        return 0

    for expiry, dte, prop, short, long_leg in seen:
        ctx = {
            "underlying": UNDERLYING, "spot": spot, "expiry": expiry, "days_to_expiry": dte,
            "structure": "put credit spread",
            "short": short.symbol, "short_delta": round(short.delta, 3), "short_iv": short.implied_volatility,
            "long": long_leg.symbol, "width": str(prop.width), "credit": str(prop.credit),
            "max_loss_per_contract": str((prop.width - prop.credit) * 100),
        }
        d = decide(ctx)
        ledger.record(
            "shadow_decision",
            underlying=UNDERLYING, expiry=expiry, dte=dte,
            short=short.symbol, long=long_leg.symbol,
            width=str(prop.width), credit=str(prop.credit),
            short_delta=round(short.delta, 4),
            trade=d.trade, contracts=d.contracts, rationale=d.rationale, failed=d.failed,
            spot=spot,
        )
        tag = "WOULD TRADE" if d.trade else "would pass"
        print(f"  {expiry} ({dte}d) {short.symbol[9:]}/{long_leg.symbol[9:]} credit {prop.credit}  -> {tag} {d.contracts}x")
        print(f"      {d.rationale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
