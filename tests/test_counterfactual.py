"""Every gate's refusal is re-priceable, not just the arithmetic gate's."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.counterfactual import extract_refusals, price_refusals, summarise  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main() -> int:
    legs = {"short": "SPY260904P00768000", "long": "SPY260904P00767000",
            "credit": "0.15", "width": "1"}
    entries = [
        {"kind": "abstain", "at": "t1", "reason": "analyst declined: no VRP", "gate": "analyst",
         "quantity": 8, **legs},
        {"kind": "abstain", "at": "t2", "reason": "challenger (fatal): too close", "gate": "challenger",
         "quantity": 8, **legs},
        {"kind": "abstain", "at": "t3", "reason": "E[P&L] within noise", "gate": "significance",
         "quantity": 8, **legs},
        {"kind": "risk_verdict", "at": "t4", "approved": False, "reasons": ["credit/width 0.10 < 0.12"],
         "quantity": 0, **legs},
        {"kind": "abstain", "at": "t4", "reason": "credit/width 0.10 < 0.12", "gate": "risk_gate",
         "quantity": 8, **legs},
        {"kind": "abstain", "at": "t5", "reason": "market closed"},
        {"kind": "risk_verdict", "at": "t6", "approved": True, **legs, "quantity": 8},
        {"kind": "risk_verdict", "at": "t7", "approved": False, "reasons": ["old-style veto"],
         "quantity": 0, **legs},
    ]
    refusals = extract_refusals(entries)
    check("one refusal per gate, none double counted, legless abstains skipped",
          [r.refused_by for r in refusals] == ["analyst", "challenger", "significance",
                                                "risk gate", "risk gate"])
    check("a risk_verdict without a paired abstain is still read (older ledgers)",
          refusals[-1].reason == "old-style veto")
    marks = {"SPY260904P00768000": Decimal("0.05"), "SPY260904P00767000": Decimal("0.02")}
    scored = price_refusals(refusals, marks)
    check("all five price", all(r.priced for r in scored))
    s = summarise(scored)
    check("the by-gate breakdown exists", set(s["by_gate"]) == {"analyst", "challenger", "significance",
                                                                 "risk gate"})
    check("each gate carries its own verdict counts",
          s["by_gate"]["analyst"]["costly"] == 1 and s["by_gate"]["risk gate"]["examined"] == 2)

    print(f"\n{len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
