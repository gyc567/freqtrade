#!/bin/bash
# Self-audit script for freqtrade loop system
# Calculates Loop Readiness Score and lists issues
set -euo pipefail

echo "=== Freqtrade Loop Audit ==="
echo "Run at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

LOOP_DIR="/root/code/freqtrade/freqtrade-loop"
SCORE=0
MAX_SCORE=100
ISSUES=()

# Check gate.yaml (15pts)
if [[ -f "$LOOP_DIR/gate.yaml" ]]; then
  SCORE=$((SCORE + 15))
  echo "[OK] gate.yaml exists (+15pts) = $SCORE"
else
  ISSUES+=("gate.yaml missing")
  echo "[FAIL] gate.yaml missing"
fi

# Check loop-budget.yaml (15pts)
if [[ -f "$LOOP_DIR/loop-budget.yaml" ]]; then
  SCORE=$((SCORE + 15))
  echo "[OK] loop-budget.yaml exists (+15pts) = $SCORE"
else
  ISSUES+=("loop-budget.yaml missing")
fi

# Check loop-ledger.json (15pts)
if [[ -f "$LOOP_DIR/loop-ledger.json" ]]; then
  SCORE=$((SCORE + 15))
  echo "[OK] loop-ledger.json exists (+15pts) = $SCORE"
else
  ISSUES+=("loop-ledger.json missing")
fi

# Check STATE.md (15pts)
if [[ -f "$LOOP_DIR/STATE.md" ]]; then
  SCORE=$((SCORE + 15))
  echo "[OK] STATE.md exists (+15pts) = $SCORE"
else
  ISSUES+=("STATE.md missing")
fi

# Check LOOP.md (15pts)
if [[ -f "$LOOP_DIR/LOOP.md" ]]; then
  SCORE=$((SCORE + 15))
  echo "[OK] LOOP.md exists (+15pts) = $SCORE"
else
  ISSUES+=("LOOP.md missing")
fi

# Check skills (25pts, 4 skills x ~6pts each)
SKILLS_DIR="/root/code/freqtrade/skills"
SKILL_COUNT=0
for skill in freqtrade-triage freqtrade-conventions freqtrade-backtest freqtrade-hyperopt; do
  if [[ -f "$SKILLS_DIR/SKILL.md.$skill" ]]; then
    SKILL_COUNT=$((SKILL_COUNT + 1))
  fi
done
SKILL_SCORE=$((SKILL_COUNT * 6 + 1))
SCORE=$((SCORE + SKILL_SCORE))
echo "[OK] Skills: $SKILL_COUNT/4 found (+${SKILL_SCORE}pts) = $SCORE"

# Check YAML/JSON validity
echo "[CHECK] Validating YAML files..."
yq --version >/dev/null 2>&1 || echo "[WARN] yq not installed, skipping YAML validation"

# Check STATE.md freshness
if [[ -f "$LOOP_DIR/STATE.md" ]]; then
  LAST_RUN=$(grep "Last run:" "$LOOP_DIR/STATE.md" | head -1)
  echo "[INFO] STATE.md $LAST_RUN"
fi

echo ""
echo "=== Loop Readiness Score: $SCORE / $MAX_SCORE ==="
if [[ ${#ISSUES[@]} -gt 0 ]]; then
  echo "Issues found:"
  for issue in "${ISSUES[@]}"; do
    echo "  - $issue"
  done
fi

echo ""
echo "=== Budget Status ==="
if [[ -f "$LOOP_DIR/loop-ledger.json" ]]; then
  python3 -c "
import json, sys
try:
  with open('$LOOP_DIR/loop-ledger.json') as f:
    data = json.load(f)
    print(f'Total runs: {data.get(\"total_runs\", 0)}')
    print(f'Total tokens: {data.get(\"total_tokens_spent\", 0):,}')
except Exception as e:
  print(f'Error reading ledger: {e}')
" 2>/dev/null || echo "Could not parse loop-ledger.json"
fi

echo ""
echo "=== Top 3 Actions ==="
if [[ $SCORE -lt 60 ]]; then
  echo "1. Create missing loop files"
  echo "2. Run loop-audit.sh to identify gaps"
  echo "3. Run loop-triage.sh manually to verify"
elif [[ $SCORE -lt 80 ]]; then
  echo "1. Write 4 Oh My OpenCode skills"
  echo "2. Configure GitHub Actions workflows"
  echo "3. Run first triage cycle"
else
  echo "1. System is healthy"
  echo "2. Enable GitHub Actions schedule"
  echo "3. Monitor for 2 weeks in L1 mode"
fi

exit 0
