"""The risk gate is the last thing between a model and the account, so its
limits are tested as arithmetic rather than trusted as intent."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.risk import AccountState, RiskGate, RiskLimits, SpreadProposal  # noqa: E402

FUNDED = AccountState(equity=Decimal("100000"), open_positions=0)


def spread(width="5", credit="1.00", quantity=1) -> SpreadProposal:
    return SpreadProposal(
        short_symbol="SPY260904P00765000",
        long_symbol="SPY260904P00760000",
        width=Decimal(width),
        credit=Decimal(credit),
        quantity=quantity,
        underlying="SPY",
    )


def check(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    return condition


def main() -> int:
    gate = RiskGate()
    results = []

    v = gate.evaluate(spread(), FUNDED)
    results.append(check("healthy 5-wide spread at $1.00 credit is approved", v.approved))
    results.append(check("max loss is (5-1)*100 = $400", v.max_loss == Decimal("400")))

    v = gate.evaluate(spread(credit="0.40"), FUNDED)
    results.append(check("credit/width 0.08 below 0.15 minimum is vetoed", not v.approved))

    v = gate.evaluate(spread(credit="6.00"), FUNDED)
    results.append(check("credit above width is vetoed as undefined risk", not v.approved))

    v = gate.evaluate(spread(quantity=10), FUNDED)
    results.append(
        check("10 contracts sized down to fit the $500 per-trade cap", v.approved and v.approved_quantity == 1)
    )

    v = gate.evaluate(spread(), AccountState(Decimal("100000"), open_positions=4))
    results.append(check("position limit of 4 blocks a fifth", not v.approved))

    v = gate.evaluate(
        spread(), AccountState(Decimal("100000"), 0, realised_loss_today=Decimal("1500"))
    )
    results.append(check("daily loss limit reached blocks all trading", not v.approved))

    v = gate.evaluate(spread(quantity=0), FUNDED)
    results.append(check("zero quantity is rejected", not v.approved))

    tight = RiskGate(RiskLimits(max_loss_per_trade=Decimal("100")))
    v = tight.evaluate(spread(), FUNDED)
    results.append(check("$400 risk under a $100 cap cannot size one contract", not v.approved))

    v = gate.evaluate(spread(), AccountState(Decimal("100000"), 0, Decimal("1200")))
    results.append(
        check("remaining daily room of $300 blocks a $400 trade", not v.approved)
    )

    passed = sum(results)
    print(f"\n{passed}/{len(results)} risk-gate tests passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
