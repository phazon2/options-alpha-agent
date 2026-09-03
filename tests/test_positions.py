"""Exit rules decide when real money is taken off the table, so they are
tested as arithmetic against hand-computed cases."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.positions import assemble  # noqa: E402


def legs(short_sym, short_entry, short_now, long_sym, long_entry, long_now, qty=1):
    return [
        {"symbol": short_sym, "qty": str(-qty), "avg_entry_price": str(short_entry),
         "current_price": str(short_now)},
        {"symbol": long_sym, "qty": str(qty), "avg_entry_price": str(long_entry),
         "current_price": str(long_now)},
    ]


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def main() -> int:
    r = []

    # Credit 1.00 (sold 2.00, bought 1.00). Now costs 0.50 to close -> 50% captured.
    s = assemble(legs("SPY260904P00758000", 2.00, 1.20,
                      "SPY260904P00753000", 1.00, 0.70))[0]
    r.append(check("pairs a short put with the long below it", s.width == 5))
    r.append(check("credit is short entry minus long entry", s.credit_received == 1))
    action, why = s.decide()
    r.append(check(f"takes profit at 50% captured ({why})", action == "take_profit"))

    # Only 20% captured -> hold.
    s = assemble(legs("SPY260904P00758000", 2.00, 1.80,
                      "SPY260904P00753000", 1.00, 0.90))[0]
    action, _ = s.decide()
    r.append(check("holds below the profit target", action == "hold"))

    # Cost to close 2.6x the credit -> stop.
    s = assemble(legs("SPY260904P00758000", 2.00, 4.00,
                      "SPY260904P00753000", 1.00, 1.40))[0]
    action, why = s.decide()
    r.append(check(f"stops out past the loss multiple ({why})", action == "stop_out"))

    # Quantity and P&L scale together.
    s = assemble(legs("SPY260904P00758000", 2.00, 1.20,
                      "SPY260904P00753000", 1.00, 0.70, qty=17))[0]
    r.append(check("quantity carries through", s.quantity == 17))
    r.append(check("open P&L scales with size", s.open_pnl == 850))

    # A lone short with no protective long is not assembled into a spread.
    orphan = [{"symbol": "SPY260904P00758000", "qty": "-1",
               "avg_entry_price": "2.00", "current_price": "1.20"}]
    r.append(check("an unpaired short is not treated as defined risk",
                   len(assemble(orphan)) == 0))

    # Equity positions are ignored entirely.
    r.append(check("non-option positions are skipped",
                   len(assemble([{"symbol": "SPY", "qty": "10",
                                  "avg_entry_price": "700", "current_price": "760"}])) == 0))

    # The bug that cost us: partial fills leave a short of 49 against longs of
    # 45 and 4. Requiring equal quantities paired none of it, so the book
    # reported zero open spreads while carrying $5,300 of risk.
    mismatched = [
        {"symbol": "SPY260910C00777000", "qty": "-49", "avg_entry_price": "1.06", "current_price": "2.27"},
        {"symbol": "SPY260910C00778000", "qty": "45", "avg_entry_price": "0.86", "current_price": "1.92"},
        {"symbol": "SPY260910C00779000", "qty": "4", "avg_entry_price": "0.65", "current_price": "1.59"},
    ]
    sp = assemble(mismatched)
    r.append(check(f"unequal quantities still pair ({len(sp)} spreads from 49/45/4)", len(sp) == 2))
    r.append(check("every short contract is accounted for",
                   sum(x.quantity for x in sp) == 49))
    r.append(check("no spread is left naked when longs cover the short",
                   not any(x.is_naked for x in sp)))
    r.append(check("widths reflect the strikes actually paired",
                   sorted(int(x.width) for x in sp) == [1, 2]))

    uncovered = [
        {"symbol": "SPY260910C00777000", "qty": "-10", "avg_entry_price": "1.06", "current_price": "2.27"},
        {"symbol": "SPY260910C00778000", "qty": "4", "avg_entry_price": "0.86", "current_price": "1.92"},
    ]
    sp2 = assemble(uncovered)
    naked = [x for x in sp2 if x.is_naked]
    r.append(check("a short only partly covered reports the naked remainder",
                   len(naked) == 1 and naked[0].quantity == 6))
    r.append(check("a naked short is ordered closed immediately",
                   naked[0].decide()[0] == "stop_out"))

    passed = sum(r)
    print(f"\n{passed}/{len(r)} position tests passed")
    return 0 if passed == len(r) else 1


if __name__ == "__main__":
    raise SystemExit(main())
