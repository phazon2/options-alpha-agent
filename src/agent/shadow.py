"""A naive agent, run in the shadow of the real one.

The real agent's claim is that its gates are worth something: the regime
filter, the challenger, the risk officer, the arithmetic gate. The only honest
way to test that claim is to run the same model on the same market with the
gates removed and see what it would have done.

So this is that model. It receives the same live chain and the same best
available spread the real agent saw, and it is asked one question: should you
sell this, and how many contracts? No regime read, no challenger, no Monte
Carlo, no floor, no position limit. Just a capable model with a brokerage API.

It never submits an order. Its would-be trades go to a separate ledger and are
re-priced later by the same counterfactual scorer that grades the real
agent's refusals — so the comparison is apples to apples, on live data,
with nothing simulated.

If the naive agent does better, that is worth knowing too.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from .analyst import BASE_URL, MODEL, TIMEOUT, _first_parsable

SYSTEM = """You are an autonomous options trading agent with access to an Alpaca \
brokerage account with $100,000. You are shown a live defined-risk credit spread \
and the current market state. Decide whether to sell it and how many contracts. \
Be decisive; you are being evaluated on P&L.

Return exactly this JSON and nothing else:
{"trade": true|false, "contracts": integer 0-100, "rationale": "one sentence"}"""


@dataclass(frozen=True)
class ShadowDecision:
    trade: bool
    contracts: int
    rationale: str
    failed: bool = False


def decide(context: dict[str, Any]) -> ShadowDecision:
    api_key = os.environ.get("FEATHERLESS_API_KEY", "").strip()
    if not api_key:
        return ShadowDecision(False, 0, "no api key", failed=True)
    try:
        r = httpx.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "Market state:\n" + json.dumps(context, indent=2, default=str) + "\n\nDecide. JSON only."},
                ],
                "max_tokens": 1500,
                "temperature": 0.2,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return ShadowDecision(False, 0, f"unreachable: {exc}", failed=True)
    if r.status_code != 200:
        return ShadowDecision(False, 0, f"http {r.status_code}", failed=True)
    parsed, _ = _first_parsable((r.json().get("choices") or [{}])[0].get("message", {}))
    if parsed is None:
        return ShadowDecision(False, 0, "unparseable", failed=True)
    try:
        n = max(0, min(100, int(parsed.get("contracts", 0))))
    except (TypeError, ValueError):
        n = 0
    return ShadowDecision(bool(parsed.get("trade", False)) and n > 0, n, str(parsed.get("rationale", ""))[:300])
