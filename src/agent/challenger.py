"""The adversarial second opinion.

The analyst is asked whether to trade and, being a helpful model, tends to
find a reason to say yes. So a second pass is given the opposite job: take the
analyst's thesis and try to destroy it. Only a thesis that survives gets sent
to the risk gate.

This is deliberately not a second vote. A vote averages two opinions and
mostly reproduces the first. A refutation is asymmetric — the challenger is
told that finding a fatal flaw is the successful outcome, and that agreeing is
the boring answer — which is what makes it catch the cases a second opinion
would wave through.

The challenger cannot approve anything. It can only veto, or decline to veto.
Like the analyst it fails closed: if it cannot be reached or cannot be parsed,
the trade does not go.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .analyst import BASE_URL, MODEL, TIMEOUT, AnalystView, _first_parsable

SYSTEM = """You are the challenger in an autonomous options trading agent. \
Another model has proposed selling a put credit spread. Your job is to REFUTE \
that proposal, not to evaluate it fairly.

Assume the proposal is wrong and look for the reason. Consider: is the trend \
actually against this position? Is implied volatility too low for the premium \
to be worth the risk? Is the credit thin relative to what can be lost? Is there \
an event or level that makes the short strike more reachable than it looks? Is \
the stated invalidation condition too loose to protect anything?

Finding a fatal flaw is the successful outcome of your work. Agreeing is the \
boring answer and you should only reach it when the thesis genuinely survives \
a hostile reading.

Return exactly this JSON and nothing else:
{"refuted": true|false, "severity": "fatal"|"serious"|"minor"|"none",
 "argument": "the strongest case against this trade, in one or two sentences"}

Keep the argument under 300 characters. A long argument gets truncated before the JSON closes, and a truncated reply is treated as a refusal to answer - which blocks the trade regardless of what you meant."""


@dataclass(frozen=True)
class Challenge:
    refuted: bool
    severity: str
    argument: str
    failed: bool = False

    @property
    def blocks_trade(self) -> bool:
        """Only a fatal objection stops the trade outright.

        An unreachable challenger is treated differently from a refuting one,
        because they are different events. A refutation is a signal; a timeout
        or a malformed reply is noise, and letting noise veto trades makes the
        agent's willingness to act hostage to a formatting slip. When the
        challenger cannot be reached the check is genuinely lost, so the
        response is to halve the size rather than either trade full or not at
        all - the same graded answer used for a serious objection.

        A challenger told that finding flaws is success will find flaws in
        almost anything, so treating every objection as a veto would mean
        never trading. A serious objection is answered by trading smaller
        instead, which is the honest response to a real but survivable
        criticism."""
        if self.failed:
            return False
        return self.refuted and self.severity == "fatal"

    @property
    def size_multiplier(self) -> float:
        """How much of the intended size survives the objection."""
        if self.failed:
            return 0.5  # the check was lost, so carry half the exposure
        if not self.refuted:
            return 1.0
        return {"serious": 0.5, "minor": 0.75}.get(self.severity, 1.0)

    @classmethod
    def unavailable(cls, why: str) -> "Challenge":
        return cls(True, "fatal", f"challenger unavailable: {why}", failed=True)


def challenge(
    view: AnalystView, context: dict[str, Any], attempts: int = 3
) -> Challenge:
    """A failure to reach the challenger blocks the trade, so retry once
    before treating the silence as a veto."""
    result = Challenge.unavailable("not attempted")
    for _ in range(max(1, attempts)):
        result = _challenge_once(view, context)
        if not result.failed:
            return result
    return result


def _challenge_once(view: AnalystView, context: dict[str, Any]) -> Challenge:
    api_key = os.environ.get("FEATHERLESS_API_KEY", "").strip()
    if not api_key:
        return Challenge.unavailable("FEATHERLESS_API_KEY is not set")

    prompt = (
        "Market state:\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        "The proposal you must attack:\n"
        f"- rationale: {view.rationale}\n"
        f"- target short delta: {view.target_delta}\n"
        f"- confidence: {view.confidence}\n"
        f"- claims it is wrong if: {view.invalidated_if}\n\n"
        "Refute it. Reply with the JSON object only."
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
                "max_tokens": 3000,
                # Higher than the analyst: a challenger that always reaches for
                # the same objection stops being a check.
                "temperature": 0.6,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return Challenge.unavailable(str(exc))

    if response.status_code != 200:
        return Challenge.unavailable(
            f"HTTP {response.status_code}: {response.text[:200]}"
        )

    message = (response.json().get("choices") or [{}])[0].get("message", {})
    parsed, _ = _first_parsable(message)
    if parsed is None:
        return Challenge.unavailable("reply was not valid JSON")

    severity = str(parsed.get("severity", "none")).lower()
    if severity not in {"fatal", "serious", "minor", "none"}:
        severity = "serious"  # an unrecognised severity is treated as blocking
    return Challenge(
        refuted=bool(parsed.get("refuted", False)),
        severity=severity,
        argument=str(parsed.get("argument", ""))[:400],
    )
