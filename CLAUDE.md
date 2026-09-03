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
