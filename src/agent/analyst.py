"""The model in the loop.

The agent's claim is that a language model proposes and deterministic code
disposes. This module is the proposing half. It is given the market state and
must return a structured judgement: trade or stand aside, with a reason.

Three properties matter more than the model's cleverness:

1. It cannot execute. It returns a judgement; the risk gate and the executor
   decide what actually happens, and either can overrule it.
2. It cannot widen risk. It may only shrink the delta band or decline. A
   model that asks for more exposure than the deterministic defaults allow is
   clamped back to them.
3. It fails closed. A timeout, a malformed reply, or an unparseable answer
   produces "do not trade", never "trade anyway".

Runs on Featherless (OpenAI-compatible), the hackathon's partner inference
provider.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = os.environ.get("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
MODEL = os.environ.get("FEATHERLESS_MODEL", "zai-org/GLM-5.2")
TIMEOUT = httpx.Timeout(90.0, connect=15.0)

# The model may tighten these but never loosen them.
FLOOR_DELTA = 0.10
CEIL_DELTA = 0.30

SYSTEM = """You are the analyst stage of an autonomous options trading agent \
running on Alpaca paper trading. You do not place orders. You return one JSON \
object and nothing else.

The agent sells defined-risk vertical credit spreads on SPY, on either side. \
A put credit spread profits if SPY holds above the short strike; a call credit \
spread profits if it stays below. The regime filter has ALREADY chosen the \
side and it is given to you as "side". Do not object that the trade is a call \
rather than a put, or the reverse - that is not your decision and there is no \
mandate to prefer either. Judge only whether selling this premium is worth it.

Its edge is the variance risk premium: implied volatility usually exceeds what \
is subsequently realised. That premium is thin, so when the short leg's \
implied volatility is at or below realised volatility there is nothing to \
harvest and standing aside is the correct answer.

Return exactly this JSON shape:
{"trade": true|false, "confidence": 0.0-1.0, "target_delta": 0.10-0.30,
 "rationale": "one or two sentences",
 "invalidated_if": "one concrete, checkable condition"}

"invalidated_if" is a commitment, recorded before the trade and checked
afterwards. It must be specific enough that a machine could evaluate it -
name a level, a move, or a close. "SPY closes below 750 before expiry" is
valid. "The market turns against us" is not.

Decline to trade when the regime is bearish, when realised volatility is \
spiking, or when the credit on offer is poor for the risk. Declining is a \
good answer; there is no penalty for standing aside and a bad fill costs real \
money. Be concise and specific about why."""


@dataclass(frozen=True)
class AnalystView:
    trade: bool
    confidence: float
    target_delta: float
    rationale: str
    invalidated_if: str = ""
    model: str = MODEL
    raw: str = ""
    failed: bool = False
    clamped: list[str] = field(default_factory=list)

    @classmethod
    def stand_aside(cls, why: str, raw: str = "") -> "AnalystView":
        return cls(False, 0.0, 0.20, why, "", MODEL, raw, failed=True)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Reasoning models wrap JSON in prose or fences; find the object."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        candidates.append(brace.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _first_parsable(message: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Pull JSON from a chat message.

    Reasoning models split their output: sometimes the answer is in `content`,
    sometimes `content` is empty and it is in `reasoning`, and sometimes
    `content` holds prose that happens not to parse. Trying only the first
    non-empty field throws away a good answer sitting in the other one, so
    both are tried in turn.
    """
    for field_name in ("content", "reasoning"):
        text = (message.get(field_name) or "").strip()
        if not text:
            continue
        parsed = _extract_json(text)
        if parsed is not None:
            return parsed, text
    return None, (message.get("content") or message.get("reasoning") or "")


def consult(context: dict[str, Any], attempts: int = 2) -> AnalystView:
    """Ask the analyst whether to trade. Never raises; fails to stand-aside.

    Reasoning models occasionally spend their budget thinking and return
    something unparseable. That is worth one retry before standing aside,
    since the alternative is skipping a session over a formatting slip.
    """
    view = AnalystView.stand_aside("not attempted")
    for _ in range(max(1, attempts)):
        view = _consult_once(context)
        if not view.failed:
            return view
    return view


def _consult_once(context: dict[str, Any]) -> AnalystView:
    api_key = os.environ.get("FEATHERLESS_API_KEY", "").strip()
    if not api_key:
        return AnalystView.stand_aside("FEATHERLESS_API_KEY is not set")

    prompt = (
        "Market state:\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        "Should the agent open the credit spread shown in best_available? "
        "Reply with the JSON object only."
    )
    try:
        response = httpx.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2000,
                "temperature": 0.2,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return AnalystView.stand_aside(f"analyst unreachable: {exc}")

    if response.status_code != 200:
        return AnalystView.stand_aside(
            f"analyst returned {response.status_code}: {response.text[:200]}"
        )

    message = (response.json().get("choices") or [{}])[0].get("message", {})
    parsed, text = _first_parsable(message)
    if parsed is None:
        return AnalystView.stand_aside("analyst reply was not valid JSON", text[:400])

    clamped: list[str] = []
    try:
        delta = float(parsed.get("target_delta", 0.20))
    except (TypeError, ValueError):
        delta, _ = 0.20, clamped.append("target_delta unparseable, used 0.20")
    if delta < FLOOR_DELTA or delta > CEIL_DELTA:
        clamped.append(f"target_delta {delta} clamped into [{FLOOR_DELTA}, {CEIL_DELTA}]")
        delta = min(max(delta, FLOOR_DELTA), CEIL_DELTA)

    try:
        confidence = min(max(float(parsed.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
        clamped.append("confidence unparseable, used 0.0")

    invalidated_if = str(parsed.get("invalidated_if", "")).strip()[:300]
    trade = bool(parsed.get("trade", False))
    if trade and not invalidated_if:
        # A thesis with no stated way to be wrong is not a thesis.
        clamped.append("no invalidated_if supplied; declining rather than trading")
        trade = False

    return AnalystView(
        trade=trade,
        confidence=confidence,
        target_delta=delta,
        rationale=str(parsed.get("rationale", ""))[:400],
        invalidated_if=invalidated_if,
        raw=text[:1000],
        clamped=clamped,
    )
