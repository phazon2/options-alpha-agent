# One-page write-up

**Options Alpha Agent** — autonomous options trading on Alpaca paper trading.
Competition account: **PA3BY0HJORNC**, started at $100,000.
Live ledger: <https://options-alpha-agent.vercel.app/>

---

## AI logic

The agent sells defined-risk vertical credit spreads on SPY. Its edge is the
variance risk premium — implied volatility usually exceeds what is subsequently
realised — and that premium is thin, so most of the design is about not trading
when it isn't there.

A language model is in the loop, but it is deliberately the weakest component.
Three stages run before anything reaches the broker:

1. **Regime filter (deterministic).** Daily closes give a trend read, which
   *chooses the side*: put credit spreads in a bullish or neutral tape, call
   credit spreads in a bearish or neutral one. The model never picks direction.

2. **Analyst (LLM, on Featherless).** Receives live market state and the actual
   best quote across every listed expiry — not a description of one, the real
   spread the agent would place. It answers whether to sell that premium, and
   must **pre-register a falsification condition**: one concrete, checkable
   statement of what would prove it wrong, recorded before the trade. A thesis
   with no stated way to be wrong is rejected as not a thesis.

3. **Challenger (LLM, adversarial).** A second pass whose only job is to
   *refute* the first. This is asymmetric on purpose — a second opinion averages
   toward the first, while a challenger told that finding a fatal flaw is the
   successful outcome catches what agreement waves through. Its response is
   graded: fatal blocks the trade, serious halves the size, minor trims it.

The model can only ever *narrow* the trade. It may tighten the delta band, never
widen it; values outside the hard band are clamped. Every stage **fails closed** —
a timeout, an HTTP error or an unparseable reply produces "do not trade", never
"trade anyway".

*It works.* The challenger's first live run rejected a proposal because the
analyst had set its invalidation level at its own short strike: *"by the time it
triggers the spread is already at max loss — the stop protects nothing."*

## Risk gates

Nothing in the risk layer consults a model. The limits are arithmetic, so the
same proposal always draws the same verdict and a persuasive model output cannot
argue past a cap.

| Gate | Limit |
| --- | --- |
| Max loss per trade | $1,500 |
| Max loss per day | $4,000 |
| Concurrent spreads | 6 |
| Equity at risk per trade | 1.5% |
| Minimum credit-to-width | 0.15 |

Risk is defined by construction: every position is a vertical spread, so the
worst case is `(width − credit) × 100 × quantity` and is known before entry. The
gate sizes down to the tightest binding cap rather than rejecting outright, and
records that it did. Exits follow the managed-winner rule — take profit at half
the credit, stop at 2.5× the credit received — rather than holding to expiry for
the last few cents of premium.

**A risk officer prices the distribution before the gate rules.** Max loss says
what can be lost; it says nothing about how likely that is. A 20,000-path Monte
Carlo simulates the underlying to expiry at realised volatility and reports
probability of profit, 95% VaR and expected shortfall. A trade with negative
expected value is refused however much premium it appears to pay. On the 17-lot
the agent actually traded it computed **E[P&L] +$145 at 92% probability of
profit**; the realised outcome was **+$153**. No simulated path breaches the
structural max loss, because a vertical cannot exceed it.

Two limits worth stating: geometric Brownian motion has thinner tails than real
equity returns, so the true tail is worse — but a vertical caps the loss by
construction, which bounds the understatement. And realised volatility is a
backward-looking estimate; with implied below realised all week, the simulation
is if anything pessimistic.

**The gate refuses trades, and the refusals are published.** Of 9 decisions,
4 were vetoed on credit-to-width. It was never loosened to let one through.

## Alpaca infrastructure

Execution runs through **Alpaca's official CLI** (`alpacahq/cli`), not raw REST.
Alpaca ships it for cron jobs and CI, it emits JSON, and it defaults to paper —
so a scheduled run needs no browser and no IDE, which is what "autonomous" has
to mean. Orders are multi-leg: `--order-class mleg` with `--legs`, correct
`position_intent` per leg.

- **Trading API** — account, clock, positions, orders, option contracts
- **Market data** — option chain snapshots on `--feed indicative`, which supplies
  delta, gamma, theta, vega and implied volatility used for strike selection
- **Scheduling** — GitHub Actions every 30 minutes through the US session,
  managing open positions before looking for new entries

Two findings worth recording, both discovered by running against the live API
rather than reading the docs:

- **The multi-leg limit-price sign is undocumented and inverted from intuition.**
  Alpaca reads a positive net limit as the maximum *debit* it will pay. An order
  sent at `+0.15` filled at `−0.13` — a fill that only satisfies "pay at most
  0.15". A credit spread sent with a positive limit therefore authorises paying
  to enter a position meant to pay you. Opening now requires a negative limit and
  the executor refuses a positive one; closing passes `allow_debit` explicitly.
- **Greeks require `--feed indicative`.** The default `opra` feed returns
  `403 OPRA agreement is not signed`, and the plain snapshots endpoint returns
  nulls, which silently disables delta targeting.

Every decision — proposal, model reasoning, challenger verdict, gate ruling, the
exact argv sent to Alpaca, and the fill — is appended to a decision ledger that
is never rewritten, and published live.

---

*Paper trading only. Results are hypothetical and are not investment advice.*
