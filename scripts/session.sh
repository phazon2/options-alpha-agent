#!/bin/bash
# Run the agent for a full trading session, unattended.
#
# Order matters. Exits first: capital freed early can be redeployed, and a
# position at its profit target should not wait behind a search for a new one.
# Then stale resting orders, because an order that never fills is the same as
# no order. Entries last.
#
# Every cycle re-reads live state; nothing is carried between iterations.
export PATH=$PATH:/root/go/bin
cd /home/user/options-alpha-agent
LOG=/tmp/session.log
: > "$LOG"

commit_ledger() {
  python3 scripts/report.py >/dev/null 2>&1
  python3 scripts/score_refusals.py >/dev/null 2>&1
  if ! git diff --quiet 2>/dev/null; then
    git add docs/ledger public/*.json >/dev/null 2>&1
    git -c user.name="options-alpha-agent" -c user.email="noreply@github.com" \
      commit -q -m "Session cycle $(date -u '+%Y-%m-%d %H:%M UTC')" >/dev/null 2>&1
    git push -q 2>/dev/null || true
  fi
}

for i in $(seq 1 70); do
  OPEN=$(alpaca clock get --jq '.is_open' 2>/dev/null)
  NOW=$(alpaca clock get --jq '.timestamp' 2>/dev/null)
  if [ "$OPEN" != "true" ]; then
    echo "=== [$i] market closed at $NOW - session over ===" | tee -a "$LOG"
    commit_ledger
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
    commit_ledger
    sleep 300
    continue
  fi
  {
    echo ""
    echo "=========== cycle $i · $NOW ==========="
    echo "aggregate open risk: \$$RISK"
    echo "--- exits ---";    python3 scripts/manage.py  2>&1 | grep -vE '^$' | tail -14
    echo "--- reprice ---";  python3 scripts/reprice.py 2>&1 | tail -6
    echo "--- entry ---";    python3 scripts/run_once.py 2>&1 | grep -E 'vol |analyst|challenger|risk officer|risk gate|budget|SUBMITTED|ABSTAIN|best ' | tail -9
    echo "--- equity ---";   alpaca account get --jq '{equity,cash}' 2>&1 | tr -d '\n '
    echo ""
  } | tee -a "$LOG"
  commit_ledger
  sleep 400
done

echo "" | tee -a "$LOG"
echo "=== SESSION SUMMARY ===" | tee -a "$LOG"
alpaca account get --jq '{equity,cash,last_equity}' 2>&1 | tee -a "$LOG"
echo "orders today:" | tee -a "$LOG"
alpaca order list --status all --limit 12 --jq '.[] | "\(.submitted_at[0:16])  \(.status)  \(.qty)x @ \(.limit_price)  filled \(.filled_avg_price // "-")"' 2>&1 | tee -a "$LOG"
alpaca position list --jq '.[] | "\(.symbol) \(.qty) @ \(.avg_entry_price) upl \(.unrealized_pl)"' 2>&1 | tee -a "$LOG"
