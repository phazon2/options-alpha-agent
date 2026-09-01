"""Order execution through Alpaca's official CLI.

The hackathon requires the agent to trade through Alpaca's MCP server or CLI
rather than by calling REST directly. The CLI is the better fit here: Alpaca
ships it for "long-running agent sessions, cron jobs and CI", it emits JSON on
stdout, and it defaults to paper trading, so a scheduled run needs no browser
and no IDE.

Every invocation returns the exact argv and raw stdout alongside the parsed
response, so the decision ledger can record what was actually sent rather than
a reconstruction of it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Literal

Side = Literal["buy", "sell"]
PositionIntent = Literal[
    "buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"
]

CLI_TIMEOUT_SECONDS = 45


class ExecutionError(RuntimeError):
    """The CLI failed, or returned something that is not a usable response."""


class LiveTradingRefused(ExecutionError):
    """A live-trading configuration was detected. The agent is paper-only."""


@dataclass(frozen=True)
class Leg:
    """One leg of a multi-leg order."""

    symbol: str
    side: Side
    position_intent: PositionIntent
    ratio_qty: int = 1

    def as_payload(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "position_intent": self.position_intent,
            "ratio_qty": str(self.ratio_qty),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """What the CLI did, in enough detail to audit later."""

    argv: list[str]
    stdout: str
    response: dict[str, Any]
    dry_run: bool
    legs: list[dict[str, str]] = field(default_factory=list)

    @property
    def order_id(self) -> str | None:
        return self.response.get("id")

    @property
    def status(self) -> str | None:
        return self.response.get("status")


class AlpacaCLIExecutor:
    """Places orders by invoking the `alpaca` binary.

    Paper trading is the CLI's default. This class additionally refuses to run
    if anything in the environment asks for live trading, so a stray variable
    cannot put real money at risk.
    """

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or os.environ.get("ALPACA_CLI_BIN", "alpaca")
        resolved = shutil.which(self.binary)
        if resolved is None:
            raise ExecutionError(
                f"Alpaca CLI not found on PATH as {self.binary!r}. Install it with "
                f"`go install github.com/alpacahq/cli/cmd/alpaca@latest`, or set "
                f"ALPACA_CLI_BIN to its path."
            )
        self.binary = resolved
        self._assert_paper_only()

    @staticmethod
    def _assert_paper_only() -> None:
        if os.environ.get("ALPACA_LIVE_TRADE", "").lower() in {"1", "true", "yes"}:
            raise LiveTradingRefused(
                "ALPACA_LIVE_TRADE is set. This agent trades paper only; unset it "
                "before running."
            )

    def _run(self, args: list[str]) -> tuple[list[str], str]:
        argv = [self.binary, *args]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionError(
                f"`{' '.join(args)}` timed out after {CLI_TIMEOUT_SECONDS}s"
            ) from exc
        if proc.returncode != 0:
            raise ExecutionError(
                f"`{' '.join(args)}` exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:400]}"
            )
        return argv, proc.stdout

    def _run_json(self, args: list[str]) -> tuple[list[str], str, dict[str, Any]]:
        argv, stdout = self._run(args)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ExecutionError(
                f"`{' '.join(args)}` did not return JSON: {stdout[:300]}"
            ) from exc
        # The CLI reports API-level failures as a JSON object carrying "error".
        if isinstance(payload, dict) and payload.get("error"):
            raise ExecutionError(
                f"Alpaca rejected `{' '.join(args)}`: {payload['error']} "
                f"(status {payload.get('status')})"
            )
        return argv, stdout, payload

    # ---- read paths ---------------------------------------------------
    def account(self) -> dict[str, Any]:
        _, _, payload = self._run_json(["account", "get"])
        return payload

    def orders(self, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        _, _, payload = self._run_json(
            ["order", "list", "--status", status, "--limit", str(limit)]
        )
        return payload if isinstance(payload, list) else payload.get("orders", [])

    def positions(self) -> list[dict[str, Any]]:
        _, _, payload = self._run_json(["position", "list"])
        return payload if isinstance(payload, list) else []

    # ---- write path ---------------------------------------------------
    def submit_multileg(
        self,
        legs: list[Leg],
        qty: int,
        net_limit: float,
        *,
        time_in_force: str = "day",
        client_order_id: str | None = None,
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Submit a defined-risk multi-leg options order.

        `net_limit` is the net price for the package and its SIGN MATTERS.
        Alpaca treats a positive limit as the maximum net DEBIT you will pay
        and a negative limit as the minimum net CREDIT you will accept. This
        is not stated in the docs; it is what the fills show — an order sent
        at +0.15 filled at -0.13, which only satisfies "pay at most 0.15".

        So a credit spread must be sent with a negative limit. Sending a
        positive one silently permits paying a debit for a position that was
        supposed to pay you.
        """
        if net_limit > 0:
            raise ExecutionError(
                f"net_limit {net_limit:+.2f} is positive, which authorises "
                f"paying a debit. Credit spreads take a negative limit."
            )
        if not 2 <= len(legs) <= 4:
            raise ExecutionError(
                f"A multi-leg order takes 2 to 4 legs, got {len(legs)}."
            )
        if qty < 1:
            raise ExecutionError(f"qty must be at least 1, got {qty}.")

        payload = [leg.as_payload() for leg in legs]
        args = [
            "order", "submit",
            "--order-class", "mleg",
            "--qty", str(qty),
            "--type", "limit",
            "--limit-price", f"{net_limit:.2f}",
            "--time-in-force", time_in_force,
            "--legs", json.dumps(payload),
        ]
        if client_order_id:
            args += ["--client-order-id", client_order_id]
        if dry_run:
            args.append("--dry-run")

        argv, stdout, response = self._run_json(args)
        return ExecutionResult(
            argv=argv,
            stdout=stdout,
            response=response,
            dry_run=dry_run,
            legs=payload,
        )
