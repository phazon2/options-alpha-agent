"""The deterministic risk gate.

Every order the agent wants to place passes through here first. Nothing in this
module consults a model: the limits are arithmetic, so the same proposal always
gets the same verdict, and a language model cannot talk the gate out of a limit
by arguing well.

The gate can veto, and it records why. Refusing to trade is a valid outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class RiskLimits:
    """Hard caps. Chosen once, then enforced without exception."""

    max_loss_per_trade: Decimal = Decimal("1500")
    max_loss_per_day: Decimal = Decimal("4000")
    max_open_positions: int = 6
    max_contracts_per_order: int = 20
    # A credit spread must pay at least this fraction of the width, otherwise
    # the reward does not justify the capital at risk.
    min_credit_to_width: Decimal = Decimal("0.15")
    # Never risk more than this share of starting equity on one trade.
    max_equity_fraction_per_trade: Decimal = Decimal("0.015")

    @classmethod
    def from_env(cls) -> "RiskLimits":
        """Limits are tunable without a code change, but never silently."""
        import os

        def dec(name: str, default: Decimal) -> Decimal:
            raw = os.environ.get(name)
            return Decimal(raw) if raw else default

        return cls(
            max_loss_per_trade=dec("RISK_MAX_LOSS_PER_TRADE", cls.max_loss_per_trade),
            max_loss_per_day=dec("RISK_MAX_LOSS_PER_DAY", cls.max_loss_per_day),
            max_open_positions=int(
                os.environ.get("RISK_MAX_OPEN_POSITIONS", cls.max_open_positions)
            ),
            max_contracts_per_order=int(
                os.environ.get("RISK_MAX_CONTRACTS", cls.max_contracts_per_order)
            ),
            min_credit_to_width=dec(
                "RISK_MIN_CREDIT_TO_WIDTH", cls.min_credit_to_width
            ),
            max_equity_fraction_per_trade=dec(
                "RISK_MAX_EQUITY_FRACTION", cls.max_equity_fraction_per_trade
            ),
        )


@dataclass(frozen=True)
class SpreadProposal:
    """A defined-risk vertical spread the strategy would like to place."""

    short_symbol: str
    long_symbol: str
    width: Decimal
    credit: Decimal
    quantity: int
    underlying: str

    @property
    def max_loss(self) -> Decimal:
        """Worst case for a vertical credit spread, per the whole order."""
        per_contract = (self.width - self.credit) * CONTRACT_MULTIPLIER
        return per_contract * self.quantity

    @property
    def max_profit(self) -> Decimal:
        return self.credit * CONTRACT_MULTIPLIER * self.quantity

    @property
    def credit_to_width(self) -> Decimal:
        if self.width <= 0:
            return Decimal("0")
        return self.credit / self.width


@dataclass(frozen=True)
class AccountState:
    """What the gate needs to know about the account right now."""

    equity: Decimal
    open_positions: int
    realised_loss_today: Decimal = Decimal("0")


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    max_loss: Decimal = Decimal("0")
    approved_quantity: int = 0

    @property
    def summary(self) -> str:
        if self.approved:
            return (
                f"approved {self.approved_quantity}x, "
                f"max loss ${self.max_loss:,.2f}"
            )
        return "vetoed: " + "; ".join(self.reasons)


class RiskGate:
    """Applies `RiskLimits` to a proposal. Deterministic, no model involved."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def evaluate(
        self, proposal: SpreadProposal, account: AccountState
    ) -> RiskVerdict:
        limits = self.limits
        reasons: list[str] = []

        if proposal.quantity < 1:
            reasons.append(f"quantity {proposal.quantity} is not tradeable")
        if proposal.width <= 0:
            reasons.append(f"width {proposal.width} must be positive")
        if proposal.credit <= 0:
            reasons.append(
                f"credit {proposal.credit} must be positive for a credit spread"
            )
        if proposal.credit >= proposal.width:
            reasons.append(
                f"credit {proposal.credit} >= width {proposal.width}: not a "
                f"defined-risk credit spread"
            )
        if reasons:
            return RiskVerdict(False, reasons)

        if proposal.credit_to_width < limits.min_credit_to_width:
            reasons.append(
                f"credit/width {proposal.credit_to_width:.3f} below minimum "
                f"{limits.min_credit_to_width}"
            )
        if account.open_positions >= limits.max_open_positions:
            reasons.append(
                f"{account.open_positions} open positions at the limit of "
                f"{limits.max_open_positions}"
            )

        daily_room = limits.max_loss_per_day - account.realised_loss_today
        if daily_room <= 0:
            reasons.append(
                f"daily loss limit reached (${account.realised_loss_today:,.2f} "
                f"of ${limits.max_loss_per_day:,.2f})"
            )

        equity_cap = account.equity * limits.max_equity_fraction_per_trade
        per_contract_loss = (
            proposal.width - proposal.credit
        ) * CONTRACT_MULTIPLIER

        # Size down to the tightest binding cap rather than rejecting outright.
        caps = [
            limits.max_loss_per_trade,
            equity_cap,
            max(daily_room, Decimal("0")),
        ]
        affordable = min(int(cap / per_contract_loss) for cap in caps)
        quantity = min(proposal.quantity, affordable, limits.max_contracts_per_order)

        if quantity < 1:
            reasons.append(
                f"cannot size even one contract: ${per_contract_loss:,.2f} risk "
                f"exceeds the binding cap of ${min(caps):,.2f}"
            )

        if reasons:
            return RiskVerdict(False, reasons, proposal.max_loss)

        sized = replace(proposal, quantity=quantity)
        notes: list[str] = []
        if quantity < proposal.quantity:
            notes.append(
                f"sized down from {proposal.quantity} to {quantity} contracts"
            )
        return RiskVerdict(
            approved=True,
            reasons=notes,
            max_loss=sized.max_loss,
            approved_quantity=quantity,
        )
