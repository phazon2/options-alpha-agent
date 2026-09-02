"""Prove the competition account is real, fresh, and ready to trade options.

Runs only live calls against Alpaca's paper environment and writes a receipt to
docs/receipts/. Nothing here is mocked: if the network or the credentials are
wrong, this fails loudly rather than emitting a green receipt.

    python scripts/probe.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper, BrokerError  # noqa: E402
from agent.config import ConfigError  # noqa: E402

RECEIPTS = Path(__file__).resolve().parents[1] / "docs" / "receipts"
UNDERLYING = "SPY"


def _redact(account_number: str) -> str:
    return account_number  # public identifier, submitted to judges by design


def main() -> int:
    started = datetime.now(timezone.utc)
    try:
        broker = AlpacaPaper()
    except ConfigError as exc:
        print(f"CONFIG: {exc}", file=sys.stderr)
        return 2

    checks: list[tuple[str, bool, str]] = []
    try:
        with broker:
            account = broker.account()
            checks.append(
                ("account active", account.status == "ACTIVE", account.status)
            )
            checks.append(
                (
                    "starting equity is $100,000",
                    abs(account.equity - 100_000.0) < 0.01,
                    f"${account.equity:,.2f}",
                )
            )
            checks.append(
                (
                    "options level 3 (multi-leg)",
                    account.options_trading_level >= 3,
                    f"level {account.options_trading_level}",
                )
            )
            checks.append(
                (
                    "account is dedicated to this event",
                    account.created_at >= "2026-08-28",
                    f"created {account.created_at}",
                )
            )

            clock = broker.clock()
            checks.append(
                ("market clock reachable", True, f"next open {clock.next_open}")
            )

            today = started.date()
            contracts = broker.option_contracts(
                UNDERLYING,
                expiration_gte=today.isoformat(),
                expiration_lte=(today + timedelta(days=14)).isoformat(),
                limit=100,
            )
            checks.append(
                (
                    f"{UNDERLYING} option chain readable",
                    len(contracts) > 0,
                    f"{len(contracts)} contracts",
                )
            )

            snapshots = broker.option_snapshots(UNDERLYING, limit=50)
            quoted = sum(
                1 for s in snapshots.values() if s.get("latestQuote", {}).get("ap")
            )
            checks.append(
                ("option quotes readable", quoted > 0, f"{quoted} quoted contracts")
            )
            with_greeks = sum(1 for s in snapshots.values() if s.get("greeks"))
            checks.append(
                (
                    "greeks supplied by feed",
                    with_greeks > 0,
                    f"{with_greeks}/{len(snapshots)} — compute locally if 0",
                )
            )

            bar = broker.latest_stock_bar(UNDERLYING)
            checks.append(
                (
                    f"{UNDERLYING} equity data readable",
                    bool(bar),
                    f"last close ${bar.get('c')}",
                )
            )
    except BrokerError as exc:
        print(f"BROKER: {exc}", file=sys.stderr)
        return 1

    passed = sum(1 for _, ok, _ in checks if ok)
    width = max(len(name) for name, _, _ in checks)
    print(f"\nAlpaca paper probe — account {_redact(account.account_number)}")
    print(f"{'':<{width}}   run at {started.isoformat(timespec='seconds')}\n")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print(f"\n{passed}/{len(checks)} checks passed")

    RECEIPTS.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    receipt = {
        "generated_at": started.isoformat(),
        "source": "live Alpaca paper API — not mocked",
        "account_number": account.account_number,
        "equity": account.equity,
        "options_trading_level": account.options_trading_level,
        "account_created_at": account.created_at,
        "market_next_open": clock.next_open,
        "checks": [
            {"name": n, "passed": ok, "detail": d} for n, ok, d in checks
        ],
        "passed": passed,
        "total": len(checks),
    }
    path = RECEIPTS / f"probe-{stamp}.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"receipt: {path.relative_to(Path.cwd())}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
