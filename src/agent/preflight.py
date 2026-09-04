"""Keep proposed legs off contracts that are already on the book.

Alpaca infers each leg's position intent from the account's live holdings
and refuses an order whose stated intent disagrees with that inference:

    422 position intent mismatch (inferred: buy_to_close, specified: buy_to_open)

The agent hit exactly that four times on 3 September 2026. It held eight short
SPY 768 puts and kept proposing a 769/768 put spread whose long leg was the
same contract, so every approved entry from 18:36 UTC to the close was refused
at the broker - after the analyst, the challenger, the Monte Carlo and the
risk gate had all passed it. ``--dry-run`` cannot catch this: the check runs
against the live book at submission time.

So the book is read first and any contract already held, long or short, is
removed before a candidate is scored. A conflict that somehow survives to
submission is refused here, with the reason written down, rather than at the
broker.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence


def held(positions: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Signed quantity per symbol currently on the book."""
    book: dict[str, int] = {}
    for position in positions:
        symbol = position.get("symbol")
        if not symbol:
            continue
        try:
            qty = int(float(position.get("qty") or 0))
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        # The CLI reports a signed qty. Some REST payloads report it positive
        # with side=short instead. Honour either.
        if position.get("side") == "short" and qty > 0:
            qty = -qty
        book[symbol] = book.get(symbol, 0) + qty
    return book


def exclude_held(
    candidates: Sequence[Any], book: dict[str, int]
) -> tuple[list[Any], list[str]]:
    """Drop every candidate contract that is already on the book.

    Removing a held contract from *both* roles is deliberately conservative.
    A held short cannot be the long leg - the broker would infer a close - and
    adding to it as the short leg would change a position the risk model has
    not sized. Neither is a trade this agent wants.
    """
    kept: list[Any] = []
    removed: list[str] = []
    for candidate in candidates:
        if candidate.symbol in book:
            removed.append(f"{candidate.symbol} held {book[candidate.symbol]:+d}")
        else:
            kept.append(candidate)
    return kept, removed


def conflicts(legs: Iterable[Any], book: dict[str, int]) -> list[str]:
    """Explain every leg the broker would refuse, in the broker's own terms."""
    problems: list[str] = []
    for leg in legs:
        qty = book.get(leg.symbol)
        if not qty:
            continue
        intent = leg.position_intent
        if intent == "buy_to_open" and qty < 0:
            problems.append(
                f"{leg.symbol} is held {qty:+d}: a buy would be inferred as "
                f"buy_to_close, not {intent}"
            )
        elif intent == "sell_to_open" and qty > 0:
            problems.append(
                f"{leg.symbol} is held {qty:+d}: a sell would be inferred as "
                f"sell_to_close, not {intent}"
            )
    return problems
