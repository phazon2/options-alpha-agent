# Execution rules for this repo

## Environment
- Cloud container is scratch space, never storage.
  Only pushed git state survives.
- WebSearch works (server-side). WebFetch may be blocked.
- git push works. Do not report it as a blocker without trying.

## Deploy — read before claiming no public URL is possible
Git-triggered deploy does NOT require container egress.
Provider watches the repo, builds on its own infra on push.
Chain: push -> GitHub -> provider builds -> public URL.
That URL is also the webhook receiver.
Never conclude a public URL is impossible.

## Evidence discipline
- A mock passing does not license a real call.
  Mocks encode assumptions; they are scaffolding, not evidence.
- Receipts, probe outputs, CI artifacts must NEVER be
  generated from a mock.
- A mock's only job: make the day-you-get-tokens work be
  "change base URL, run probe" — not "start building."

## Credentials
- Never handle my personal credentials.
- Never drive a signup, 2FA, or SSO flow.
- If something needs an account, stop and tell me —
  I do it myself in five minutes on my phone.

## Blockers
If you hit a blocker, state the exact error and whether it is:
config-fixable / do-it-myself / genuinely human-only.
Do not collapse those three into "blocked."

## Trading agent — hard-won rules

- Cash and equity must agree when the book reports flat. A divergence means
  positions exist that the code cannot see. Check it every cycle.
- Never key position logic on leg quantities being equal. Partial fills are
  normal; 49 short against 45+4 long paired to zero spreads and let the agent
  breach its own risk cap.
- Choose strikes by expected value, not credit-to-width. The ratio is a proxy
  and it picks spreads too narrow to capture any volatility premium.
- The variance risk premium is direction-neutral. A trend filter on top of it
  is an unpaid directional bet — it cost every call spread we opened on 3 Sep.
- A gate that refuses trades is not broken. Loosening it because it refuses
  too often is how we lost $731.

See docs/POSTMORTEM.md for the full account.

## Learned the night before the deadline (4 Sep 2026)
- Check `date -u` at the start of every turn near a deadline. The summary said
  "tomorrow"; it was already 11:36 UTC on the day, 1h54m before the open.
- Verify every claim about an API against the live API before it goes into a
  README, deck or video. Two of three "undocumented" claims were documented and
  the third did not reproduce; the judges were the people who would know.
- `pgrep -f pattern` matches the shell running the pgrep. Use `ps -eo cmd | grep`
  with `grep -v grep`, or match on something not in your own command line.
- The local `main` branch goes stale. Always `git fetch origin main` and merge
  `origin/main` before pushing main; a rejected push here cost a round trip.
- The container runs `session.sh` on `main` so every cycle's push deploys. The
  Actions workflow is dispatch-only: a cron is a second runner the moment the
  secrets exist. Exactly one runner, ever.
- Film scenes: `data-t` must be the cumulative start in DOM order or `__paint`
  shows the wrong scene and two frames come out identical. Check with md5sum.
- A rehearsal must be labelled in the ledger (`rehearsal: true` on cycle_start,
  and a `note` record), never deleted. Append-only means append-only.
- Any refusal after a spread is chosen must carry the legs, or it cannot be
  graded. "Only the risk gate's refusals are re-priceable" was a ledger gap,
  not a fact about the gates.
