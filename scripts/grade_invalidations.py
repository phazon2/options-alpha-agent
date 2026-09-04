"""Grade every 'wrong if' the analyst pre-registered against what SPY did.

    python scripts/grade_invalidations.py     # -> public/falsification.json
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.broker import AlpacaPaper  # noqa: E402
from agent.falsification import conditions_from_ledger, grade, summarise  # noqa: E402
from agent.ledger import DecisionLedger  # noqa: E402

OUT = Path("public/falsification.json")


def main() -> int:
    entries = DecisionLedger().entries()
    pairs = conditions_from_ledger(entries)
    if not pairs:
        print("no analyst conditions recorded yet")
        return 0

    bars = AlpacaPaper().daily_bars("SPY", lookback_days=30)
    closes = [(b["t"][:10], float(b["c"])) for b in bars if b.get("c")]
    today = date.today().isoformat()

    graded = [grade(c, closes, today, default_deadline=exp) for c, exp in pairs]
    summary = summarise(graded)

    print(f"conditions {summary['conditions']}  parsed {summary['parsed']}  "
          f"fired {summary['fired']}  holding {summary['holding']}  "
          f"unparsed {summary['unparsed']}  n/a {summary['not_applicable']}")
    print()
    for c in graded:
        if c.kind != "spot_close":
            print(f"  {c.at[:16]}  {'trade' if c.trade else 'pass '}  [{c.kind}]  {c.text[:70]}")
            continue
        warn = c.warns_before_max_loss
        tag = "" if warn is None else ("  can warn" if warn else "  fires only after max loss")
        print(f"  {c.at[:16]}  {'trade' if c.trade else 'pass '}  {c.direction} {c.level}"
              f"  -> {c.status}{' on ' + c.fired_on if c.fired_on else ''}"
              f"  short {c.short_strike}{tag}")
    print()
    print(f"on trades taken: {summary['on_trades_taken']} conditions, "
          f"{summary['on_trades_fired']} fired, "
          f"{summary['could_not_warn']} of {summary['placed_against_short_strike']} "
          f"set at or beyond the short strike")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "closes_used": closes[-10:],
                "summary": summary,
                "conditions": [
                    {
                        "at": c.at,
                        "trade": c.trade,
                        "text": c.text,
                        "kind": c.kind,
                        "direction": c.direction,
                        "level": c.level,
                        "inclusive": c.inclusive,
                        "deadline": c.deadline,
                        "side": c.side,
                        "short_strike": c.short_strike,
                        "distance_from_short_strike": c.distance_from_short_strike,
                        "warns_before_max_loss": c.warns_before_max_loss,
                        "status": c.status,
                        "fired_on": c.fired_on,
                        "fired_close": c.fired_close,
                        "closes_checked": c.closes_checked,
                    }
                    for c in graded
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
