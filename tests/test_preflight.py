"""The book pre-flight: the 3 September 422, made impossible."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.execution import Leg  # noqa: E402
from agent.preflight import conflicts, exclude_held, held  # noqa: E402


@dataclass(frozen=True)
class C:
    symbol: str


FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main() -> int:
    # The book on 3 September at 18:36 UTC: eight short 768 puts, eight long 767s.
    book = held([
        {"symbol": "SPY260904P00768000", "qty": "-8"},
        {"symbol": "SPY260904P00767000", "qty": "8"},
        {"symbol": "SPY", "qty": "0"},
    ])
    check("signed quantities are read from the CLI payload", book == {
        "SPY260904P00768000": -8, "SPY260904P00767000": 8})
    check("side=short with a positive qty is honoured too",
          held([{"symbol": "X", "qty": "3", "side": "short"}]) == {"X": -3})

    chain = [C("SPY260904P00769000"), C("SPY260904P00768000"),
             C("SPY260904P00767000"), C("SPY260904P00766000")]
    kept, removed = exclude_held(chain, book)
    check("held contracts leave the candidate set before scoring",
          [c.symbol for c in kept] == ["SPY260904P00769000", "SPY260904P00766000"])
    check("what was removed is explained", removed == [
        "SPY260904P00768000 held -8", "SPY260904P00767000 held +8"])
    check("an empty book removes nothing", exclude_held(chain, {})[0] == chain)

    # The exact order the broker refused four times.
    legs = [Leg("SPY260904P00769000", "sell", "sell_to_open"),
            Leg("SPY260904P00768000", "buy", "buy_to_open")]
    problems = conflicts(legs, book)
    check("the 769/768 spread is refused before it reaches the broker", len(problems) == 1)
    check("in the broker's own words", "buy_to_close" in problems[0])
    check("selling a contract that is held long is caught the same way",
          conflicts([Leg("SPY260904P00767000", "sell", "sell_to_open")], book))
    check("a clean spread passes",
          conflicts([Leg("SPY260904P00770000", "sell", "sell_to_open"),
                     Leg("SPY260904P00769000", "buy", "buy_to_open")], book) == [])
    check("closing legs are never flagged",
          conflicts([Leg("SPY260904P00768000", "buy", "buy_to_close")], book) == [])

    print(f"\n{len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
