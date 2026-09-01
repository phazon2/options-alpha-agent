"""Exercise the analyst against the live model and against failure."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.analyst import AnalystView, consult  # noqa: E402
from agent.broker import AlpacaPaper  # noqa: E402
from agent.regime import read_regime  # noqa: E402


def main() -> int:
    broker = AlpacaPaper()
    bars = broker.daily_bars("SPY", lookback_days=60)
    regime = read_regime(bars)
    print(f"regime        {regime.regime} — {regime.reason}")
    print(f"realised vol  {regime.realised_vol:.3f}   20d return {regime.return_20d*100:+.2f}%")

    context = {
        "underlying": "SPY",
        "spot": regime.spot,
        "regime": regime.regime,
        "regime_reason": regime.reason,
        "realised_vol_annualised": round(regime.realised_vol, 4),
        "return_20d_pct": round(regime.return_20d * 100, 2),
        "sma_5": round(regime.sma_short, 2),
        "sma_20": round(regime.sma_long, 2),
        "structure": "put credit spread, 1-5 wide, defined risk",
        "risk_caps": {"max_loss_per_trade_usd": 1500, "min_credit_to_width": 0.15},
    }

    view = consult(context)
    print(f"\ntrade         {view.trade}")
    print(f"confidence    {view.confidence}")
    print(f"target delta  {view.target_delta}")
    print(f"rationale     {view.rationale}")
    if view.clamped:
        print(f"clamped       {'; '.join(view.clamped)}")
    if view.failed:
        print(f"FAILED CLOSED (stood aside): {view.rationale}")

    checks = []
    checks.append(("returned a view", isinstance(view, AnalystView)))
    checks.append(("delta within hard band", 0.10 <= view.target_delta <= 0.30))
    checks.append(("confidence in [0,1]", 0.0 <= view.confidence <= 1.0))

    saved = os.environ.pop("FEATHERLESS_API_KEY", None)
    blind = consult(context)
    checks.append(("fails closed without a key", blind.trade is False and blind.failed))
    if saved:
        os.environ["FEATHERLESS_API_KEY"] = saved

    print()
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed = sum(ok for _, ok in checks)
    print(f"\n{passed}/{len(checks)} analyst checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
