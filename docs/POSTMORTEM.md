# Post-mortem: what went wrong, and what to check first next time

Written on 3 September 2026 with the account down $731 from $100,000, so that
none of this has to be rediscovered.

## Two failures, not one

They are unrelated, and conflating them wastes a day.

### 1. Engineering: invisible positions

`assemble()` paired a short option with a long only when their quantities were
equal. Partial fills and repeated entries left **49 short against longs of 45
and 4**, so nothing paired and the function returned zero spreads.

Everything downstream believed it:

- the risk gate saw zero open positions, so the six-position limit never bound,
  and the agent kept opening more
- the exit manager saw nothing to manage, so no profit target or stop was ever
  applied to any of it

Six nine-lot entries compounded into 49 contracts carrying **$4,229 of risk** —
past the $4,000 daily cap — while every cycle printed *"no open spreads"*.

**Caught by**: cash and equity diverging by $2,000 while the agent reported a
flat book. That divergence is the canary. Check it every cycle.

**Fixed by**: consuming longs nearest-strike-first until the short is covered;
reporting an uncovered short as naked rather than folding it into a spread; six
regression tests on the exact 49/45/4 shape.

### 2. Trading: a directional bet the edge does not support

Every put spread the agent opened was profitable. Every call spread lost.

```
SPY   761.63 (Sep 1)  →  765.13 (Sep 2)  →  772.52 (Sep 3)
```

The side was chosen by: *"neutral regime, spot marginally below its 20-day SMA,
therefore sell calls."* Spot was 766–769 against an SMA of 768.95 — a gap of two
points on a $770 underlying. That is not a signal, it is a coin flip, and it
landed on the wrong side of a 1.5% rally.

The stated edge is the **variance risk premium**, which is a claim about the
*size* of future moves, not their direction. Any rule that converts it into a
directional bet is importing risk the edge does not pay for.

## The finding that explains the whole week

The agent abstained on most cycles and it was right to. Measured against our own
Monte Carlo, at the prices our feed reports:

```
short put 763, delta 0.194, IV 10.92%, 7 realised 8.71%
width    credit   ratio    PoP    E[P&L] on 20x
    1     0.09    0.090    80%          −191
    2     0.25    0.125    81%          −195
    5     0.50    0.100    81%          −435
   10     0.79    0.079    82%          −515
   20     1.09    0.055    83%          −243
```

**No width has positive expected value.** A 0.20-delta short strike is breached
about 20% of the time, so the credit must exceed roughly 20% of the width simply
to break even, and the market is paying 6–12%.

Implied volatility does exceed realised (10.9% against 8.7%), so the premium is
real — but the structure fails to capture it. The bid-ask and the discrete
strike grid consume the 2.2 vol points before they reach the account.

**The conclusion is not "trade anyway with a looser filter."** That is precisely
what lost the money: the four gate vetoes we overrode by lowering the floor from
0.15 to 0.12 were followed by the worst session of the week.

## Check these first, next time

1. **Does cash equal equity when the book reports flat?** If not, positions exist
   that the code cannot see.
2. **Do quantities match across legs?** Partial fills are the norm, not the
   exception. Never key position logic on quantity equality.
3. **Is the structure's expected value positive at the current chain?** Compute
   it before choosing a strike, not after. Credit-to-width is a proxy and it
   lies — it picked 1-wide spreads that capture none of the vol premium.
4. **Is a directional rule smuggling itself in?** A trend filter on a
   direction-neutral edge is an unpaid bet.
5. **Does the aggregate book fit the limits?** Per-trade caps mean nothing if
   nothing tracks the sum.

## What was right

The instrumentation caught its own failure. The counterfactual scorer proved the
risk floor was miscalibrated; the equity/cash divergence exposed the pairing bug;
the Monte Carlo refused the trades that had no edge. Every one of those findings
is in the public ledger, including the ones that are unflattering.

The agent lost money because it was allowed to trade a structure with no edge —
not because it hid anything.
