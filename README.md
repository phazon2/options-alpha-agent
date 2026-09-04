# Options Alpha Agent

**A self-auditing risk layer for Alpaca options.** An autonomous agent that trades
defined-risk vertical spreads, re-prices the trades it *refused*, and publishes
whether refusing was right — including when it wasn't.

Live ledger: **<https://options-alpha-agent.vercel.app/>** · Account **PA3BY0HJORNC**

## The receipts

Predicted before the trade, on a fresh $100,000 paper account. Verifiable against
the account ID above.

| | Monte Carlo, before the trade | Live Alpaca execution |
| --- | --- | --- |
| P&L on the 17-lot | **+$145** expected | **+$153** realised |
| Probability of profit | 92% | won |
| 95% VaR | −$1,428 | not breached |
| Structural max loss | −$1,428 | 0 paths exceeded it |
| Paths simulated | 20,000 | — |

<details>
<summary>Raw order records — verify the timestamps against account PA3BY0HJORNC</summary>

```json
{ "id": "cd44d308-8609-4044-8994-3137d255fc78", "status": "filled",
  "qty": "17", "limit_price": "-0.16", "filled_avg_price": "-0.16",
  "submitted_at": "2026-09-01T17:10:59.728278Z" }

{ "id": "f67cd64a-a82c-4db8-a877-52de37037761", "status": "filled",
  "qty": "17", "limit_price": "0.07",  "filled_avg_price": "0.07",
  "submitted_at": "2026-09-02T14:57:24.664744Z" }
```

Opened at a $0.16 credit, closed at $0.07 when the exit rule read 56% of the
credit captured. `(0.16 − 0.07) × 100 × 17 = $153`.

</details>


## What it found out about itself

The agent's own instrumentation produced four findings this week, all in the
public ledger:

| Finding | How it surfaced | What changed |
| --- | --- | --- |
| The risk floor was miscalibrated | Counterfactual scorer: all 4 vetoes would have profited | Floor 0.15 → 0.12, on evidence |
| A position-pairing bug let the book breach its own cap | Cash and equity diverged $2,000 while the agent reported a flat book | Pairing rewritten, 6 regression tests, aggregate-risk brake outside the agent |
| No edge existed at 7 DTE at any width or delta | Monte Carlo sweep of the live chain | Selection re-ranked by expected value; 1 DTE added to the ladder |
| The edge is conditional on realised < implied | Priced every strike under both volatilities | Both reported on every decision |
| Its own falsification conditions could not warn in time | Graded all 63 pre-registered `wrong if` conditions against daily closes: 42 on trades taken, 0 fired, 17 of 32 set at or beyond the short strike, where firing means the loss is already locked in | Each condition is now parsed at decision time and flagged when it cannot warn before max loss; `public/falsification.json` |
| Every approved entry after 18:36 UTC on 3 Sep died at the broker | Four `422 position intent mismatch` refusals in the ledger, with request ids | Book-aware pre-flight; the refusal is now made in-house, in the broker's words, and recorded with its legs |

The second one cost $731. It is not hidden; it is in `docs/POSTMORTEM.md`.

## Alpaca options field notes

Four behaviours met on the live API, each with its receipt and the code that
handles it. Two are documented and still cost a day; one is not documented
anywhere I could find; one earlier claim did not survive re-testing and is
withdrawn here rather than quietly deleted.

| Behaviour | Documented? | What happened | Handled in |
| --- | --- | --- | --- |
| Position intent is inferred from the book; a mismatch is a `422` | Not that I could find | Holding −8 SPY 768P, the agent proposed a 769/768 put spread whose long leg was that same contract. `position intent mismatch (inferred: buy_to_close, specified: buy_to_open)` four times between 18:36 and 19:58 UTC on 3 Sep, request ids in `docs/POSTMORTEM.md`. `--dry-run` passes it: the check runs against the live book at submission. Four approved entries lost | `preflight.py` removes held contracts before a candidate is scored and takes a last look before submit; 10 tests reproduce the failed order |
| Multi-leg limit sign | Yes, in the `POST /v2/orders` reference: positive is a debit, negative a credit | The CLI's `--limit-price` help does not say so. The first live order went in at `+0.15` and filled as a `0.13` credit: a credit satisfies a max-debit limit, so the wrong sign is invisible in the fill. Dry run cannot catch it | `execution.py` refuses a positive net limit on an opening order; closes must pass `allow_debit` |
| Greeks on a paper key | Yes: `feed` defaults to `opra`; `indicative` is the free feed | The default is the feed a paper key cannot read. The CLI default 403s (`OPRA agreement is not signed`); a REST snapshot with no `feed` returns `200` with null greeks (reproduced 4 Sep 2026, 11:25 UTC). No error: delta targeting just dies | `broker.option_chain()` always sends `feed=indicative` |
| Intraday portfolio history | — | An earlier version of this table claimed the series read exactly $100,000 too high. That did not reproduce on re-query and no ledger record supports it, so the claim is withdrawn. One 5-minute bar at 101,893.87 on 3 Sep does not reconcile with any ledger state. The equity curve is built from the agent's own account reads instead | `report.py` |

## One-page write-up

AI logic, risk gates and Alpaca infrastructure: **[docs/WRITEUP.md](docs/WRITEUP.md)**

## Status

Setup verified against the live Alpaca paper API — see `docs/receipts/`, which
contains only receipts generated by real calls. Nothing in this repository is
produced from a mock.

| Gate | State |
| --- | --- |
| Dedicated paper account, created for this event | ✅ `PA3BY0HJORNC` |
| Starting equity $100,000 | ✅ |
| Options level 3 (multi-leg spreads) | ✅ |
| Option chain + quotes readable | ✅ |
| Greeks + implied volatility | ✅ via `--feed indicative` |
| Multi-leg orders through the Alpaca CLI | ✅ opened and closed, live fills |
| First closed round trip | ✅ +$153 realised on a 17-lot |

## Layout

    src/agent/config.py      environment-only configuration; refuses non-paper keys
    src/agent/broker.py      read-only Alpaca access (account, clock, chain, quotes)
    src/agent/execution.py   order execution through Alpaca's official CLI
    scripts/probe.py         live readiness probe; writes a receipt
    scripts/verify_execution.py  dry-run a real multi-leg spread through the CLI
    docs/receipts/           evidence, generated only from real API calls

## Execution path

Orders go through Alpaca's official CLI, which the event requires in place of
raw REST, and which Alpaca ships for cron jobs and CI:

    go install github.com/alpacahq/cli/cmd/alpaca@latest
    alpaca doctor          # confirms paper profile + env credentials

Paper is the CLI's default; `execution.py` additionally refuses to start when
`ALPACA_LIVE_TRADE` is set, so real money is unreachable by construction.

## Market data

Greeks and implied volatility require `--feed indicative`. The default `opra`
feed returns `403 OPRA agreement is not signed`, and the plain snapshots
endpoint returns nulls — so delta targeting must read the indicative chain.

## Running the probe

    pip install -r requirements.txt
    cp .env.example .env     # fill in, never commit
    python scripts/probe.py

Exit code is non-zero if any check fails.

## Configuration

All secrets come from the environment. `ALPACA_API_KEY` must start with `PK`;
the config layer refuses to start with a live key so the agent cannot reach real
money.

## Licence

MIT — see [LICENSE](LICENSE).
