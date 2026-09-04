#!/bin/bash
# Run the agent for a full trading session, unattended.
#
# Order matters. Exits first: capital freed early can be redeployed, and a
# position at its profit target should not wait behind a search for a new one.
# Then stale resting orders, because an order that never fills is the same as
# no order. Entries last. The gate-less shadow runs alongside, dry-run only.
#
# Every cycle re-reads live state; nothing is carried between iterations.
#
# Exactly one runner may be live. Two runners would each see a flat book and
# each open a position. The lock stops a second copy in this container; the
# GitHub Actions workflow is dispatch-only for the same reason.
export PATH=$PATH:/root/go/bin
cd /home/user/options-alpha-agent
exec 9>/tmp/oaa-session.lock
if ! flock -n 9; then
  echo "another session.sh already holds /tmp/oaa-session.lock - refusing to start a second runner"
  exit 1
fi
LOG=/tmp/session.log
PUSHLOG=/tmp/session-push.log
: > "$LOG"
echo "runner: session.sh on branch $(git rev-parse --abbrev-ref HEAD) at $(git rev-parse --short HEAD)" | tee -a "$LOG"

publish() {
  # Every receipt is rebuilt from the ledger and the live account, then
  # pushed. A push that fails is logged and reported, never swallowed.
  python3 scripts/report.py            >/dev/null 2>&1
  python3 scripts/score_refusals.py    >/dev/null 2>&1
  python3 scripts/score_shadow.py      >/dev/null 2>&1
  python3 scripts/grade_invalidations.py >/dev/null 2>&1
  python3 scripts/reconcile.py         >/dev/null 2>&1
  python3 scripts/report.py            >/dev/null 2>&1
  if ! git diff --quiet 2>/dev/null; then
    git add docs/ledger public/*.json >/dev/null 2>&1
    git -c user.name="options-alpha-agent" -c user.email="noreply@github.com" \
      commit -q -m "Session cycle $(date -u '+%Y-%m-%d %H:%M UTC')" >/dev/null 2>&1
    if git push -q origin HEAD >>"$PUSHLOG" 2>&1; then
      echo "published $(git rev-parse --short HEAD) to $(git rev-parse --abbrev-ref HEAD)" | tee -a "$LOG"
    else
      echo "PUSH FAILED at $(date -u '+%H:%M UTC') - see $PUSHLOG" | tee -a "$LOG"
    fi
  fi
}

for i in $(seq 1 70); do
  OPEN=$(alpaca clock get --jq '.is_open' 2>/dev/null)
  NOW=$(alpaca clock get --jq '.timestamp' 2>/dev/null)
  if [ "$OPEN" != "true" ]; then
    echo "=== [$i] market closed at $NOW - session over ===" | tee -a "$LOG"
    python3 scripts/scan_universe.py >/dev/null 2>&1
    publish
    break
  fi
  # Hard brake. The pairing bug let six entries compound into $4,229 of risk
  # while every cycle reported a flat book. The per-trade cap cannot catch
  # that on its own, so the aggregate is checked from outside the agent.
  RISK=$(python3 -c "
import sys; sys.path.insert(0,'src')
from agent.positions import assemble
from agent.execution import AlpacaCLIExecutor
try:
    sp=assemble(AlpacaCLIExecutor().positions())
    print(int(sum(float((s.width-s.credit_received)*100*s.quantity) for s in sp if not s.is_naked)))
except Exception:
    print(999999)
" 2>/dev/null)
  if [ "${RISK:-999999}" -gt 3000 ]; then
    echo "=== [$i] HALT: aggregate open risk \$$RISK exceeds \$3,000 - not opening more ===" | tee -a "$LOG"
    python3 scripts/manage.py 2>&1 | tail -10 | tee -a "$LOG"
    publish
    sleep 300
    continue
  fi
  # The shadow decides on the same chain at the same moment, never submits.
  python3 scripts/shadow.py >>/tmp/shadow.log 2>&1 &
  SHADOW_PID=$!
  {
    echo ""
    echo "=========== cycle $i · $NOW ==========="
    echo "aggregate open risk: \$$RISK"
    echo "--- exits ---";    python3 scripts/manage.py  2>&1 | grep -vE '^$' | tail -14
    echo "--- reprice ---";  python3 scripts/reprice.py 2>&1 | tail -6
    echo "--- entry ---";    python3 scripts/run_once.py 2>&1 | grep -E 'vol |analyst|challenger|risk officer|risk gate|budget|SUBMITTED|ABSTAIN|best |chose|left out|wrong if|that level' | tail -12
    echo "--- equity ---";   alpaca account get --jq '{equity,cash}' 2>&1 | tr -d '\n '
    echo ""
  } | tee -a "$LOG"
  wait "$SHADOW_PID" 2>/dev/null
  publish
  sleep 400
done

echo "" | tee -a "$LOG"
echo "=== SESSION SUMMARY ===" | tee -a "$LOG"
alpaca account get --jq '{equity,cash,last_equity}' 2>&1 | tee -a "$LOG"
echo "orders today:" | tee -a "$LOG"
alpaca order list --status all --limit 12 --jq '.[] | "\(.submitted_at[0:16])  \(.status)  \(.qty)x @ \(.limit_price)  filled \(.filled_avg_price // "-")  \(.client_order_id)"' 2>&1 | tee -a "$LOG"
alpaca position list --jq '.[] | "\(.symbol) \(.qty) @ \(.avg_entry_price) upl \(.unrealized_pl)"' 2>&1 | tee -a "$LOG"
