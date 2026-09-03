"""The risk officer's numbers gate real orders, so they are tested as maths."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.var import simulate  # noqa: E402


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main() -> int:
    r = []

    far = simulate(766.0, Decimal("753"), Decimal("752"), Decimal("0.16"), 17, 3, 0.112)
    near = simulate(766.0, Decimal("764"), Decimal("763"), Decimal("0.30"), 17, 3, 0.112)

    r.append(check("no simulated path breaches the structural max loss",
                   far.worst_case >= far.max_loss - 0.01))
    r.append(check("a further strike has a higher probability of profit",
                   far.probability_of_profit > near.probability_of_profit))
    r.append(check("probability of profit is a probability",
                   0.0 <= far.probability_of_profit <= 1.0))
    r.append(check("expected shortfall is no better than VaR95",
                   far.expected_shortfall <= far.var_95 + 0.01))
    r.append(check("the near-the-money spread has negative expected value",
                   near.expected_pnl < 0))

    a = simulate(766.0, Decimal("753"), Decimal("752"), Decimal("0.16"), 17, 3, 0.112)
    r.append(check("same inputs give the same distribution",
                   a.expected_pnl == far.expected_pnl))

    calm = simulate(766.0, Decimal("753"), Decimal("752"), Decimal("0.16"), 17, 3, 0.06)
    wild = simulate(766.0, Decimal("753"), Decimal("752"), Decimal("0.16"), 17, 3, 0.35)
    r.append(check("higher volatility lowers the probability of profit",
                   wild.probability_of_profit < calm.probability_of_profit))

    call = simulate(766.0, Decimal("775"), Decimal("776"), Decimal("0.16"), 1, 3, 0.112,
                    is_put=False)
    r.append(check("a call spread above spot is mostly profitable",
                   call.probability_of_profit > 0.5))

    passed = sum(r)
    print(f"\n{passed}/{len(r)} risk-officer tests passed")
    print(f"\n  the 17-lot actually traded: {far.summary}")
    print(f"  realised outcome was +$153 against E[P&L] ${far.expected_pnl:+,.0f}")
    return 0 if passed == len(r) else 1


if __name__ == "__main__":
    raise SystemExit(main())
