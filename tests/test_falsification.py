"""Pre-registered conditions are parsed, graded, and judged for whether they
could have warned in time."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.falsification import conditions_from_ledger, grade, parse, summarise  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main() -> int:
    c = parse("2026-09-03T14:07:00Z", "SPY closes above 777 before expiry", True,
              short_symbol="SPY260910C00777000")
    check("direction and level parse", (c.direction, c.level, c.inclusive) == ("above", 777.0, False))
    check("side and short strike come from the OCC symbol", (c.side, c.short_strike) == ("call", 777.0))
    check("a level at the short strike cannot warn before max loss", c.warns_before_max_loss is False)

    c2 = parse("2026-09-03T15:05:00Z", "SPY closes at or below 763 before 2026-09-10 expiry", True,
               short_symbol="SPY260910P00762000")
    check("'at or' makes it inclusive", c2.inclusive)
    check("an explicit deadline is captured", c2.deadline == "2026-09-10")
    check("a put level above the short strike can warn", c2.warns_before_max_loss is True)

    check("N/A is not counted as parsed",
          parse("t", "N/A — no trade opened", False).kind == "not_applicable")
    check("a vol condition is reported as unparsed, not skipped",
          parse("t", "SPY realised vol drops below 0.0944", False).kind == "unparsed")

    closes = [("2026-09-02", 765.5), ("2026-09-03", 773.9), ("2026-09-04", 778.2)]
    g = grade(c, closes, today="2026-09-04")
    check("fires on the first close beyond the level", (g.status, g.fired_on) == ("fired", "2026-09-04"))
    check("closes before the recording day are ignored",
          grade(parse("2026-09-03T10:00:00Z", "SPY closes below 770", True), closes, "2026-09-04").status
          == "holding")
    check("past the deadline without firing is expired_unfired",
          grade(parse("2026-09-02T10:00:00Z", "SPY closes below 700 before 2026-09-03", True),
                closes, "2026-09-04").status == "expired_unfired")

    ledger = [
        {"kind": "cycle_start", "expiry": "2026-09-10"},
        {"kind": "analyst_view", "at": "2026-09-03T14:07:00Z", "trade": True,
         "invalidated_if": "SPY closes above 777 before expiry"},
        {"kind": "challenge"},
        {"kind": "risk_verdict", "short": "SPY260910C00777000", "expiry": "2026-09-10"},
        {"kind": "cycle_start", "expiry": "2026-09-10"},
        {"kind": "analyst_view", "at": "2026-09-03T14:20:00Z", "trade": False,
         "invalidated_if": "N/A"},
    ]
    pairs = conditions_from_ledger(ledger)
    check("the condition is paired with the spread its cycle chose",
          pairs[0][0].short_strike == 777.0 and pairs[0][1] == "2026-09-10")
    check("a declined view is still counted", len(pairs) == 2 and pairs[1][0].kind == "not_applicable")
    s = summarise([grade(p, closes, "2026-09-04", e) for p, e in pairs])
    check("summary counts the could-not-warn pattern", s["could_not_warn"] == 1 and s["fired"] == 1)

    print(f"\n{len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
