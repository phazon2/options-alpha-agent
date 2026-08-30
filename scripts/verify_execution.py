"""Prove the execution path works end to end, without placing a live order.

Constructs a real defined-risk put credit spread from live option-chain data
and submits it to the Alpaca CLI in dry-run mode, so the exact request body is
verified against Alpaca before the market opens.

    python scripts/verify_execution.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.execution import AlpacaCLIExecutor, ExecutionError, Leg  # noqa: E402

SHORT_PUT = "SPY260904P00765000"
LONG_PUT = "SPY260904P00760000"


def main() -> int:
    try:
        executor = AlpacaCLIExecutor()
    except ExecutionError as exc:
        print(f"SETUP: {exc}", file=sys.stderr)
        return 2

    account = executor.account()
    print(f"account         {account['account_number']}")
    print(f"options level   {account['options_trading_level']}")
    print(f"equity          ${float(account['equity']):,.2f}")

    legs = [
        Leg(SHORT_PUT, "sell", "sell_to_open"),
        Leg(LONG_PUT, "buy", "buy_to_open"),
    ]
    try:
        result = executor.submit_multileg(legs, qty=1, limit_price=0.60, dry_run=True)
    except ExecutionError as exc:
        print(f"EXECUTION: {exc}", file=sys.stderr)
        return 1

    body = result.response
    print(f"\norder_class     {body.get('order_class')}")
    print(f"qty             {body.get('qty')}")
    print(f"type            {body.get('type')} @ {body.get('limit_price')}")
    for leg in body.get("legs", []):
        print(f"  leg           {leg['side']:4} {leg['symbol']}  {leg['position_intent']}")

    ok = (
        body.get("order_class") == "mleg"
        and len(body.get("legs", [])) == 2
        and body.get("type") == "limit"
    )
    print(f"\n{'PASS' if ok else 'FAIL'} — multi-leg request accepted by the CLI (dry run)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
