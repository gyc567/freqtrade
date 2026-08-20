#!/bin/bash
# Daily triage entry point
# Exit codes: 0=success, 1=noop, 2=budget exceeded, 3=error
set -euo pipefail

echo "[loop-triage] Starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP_DIR="$(dirname "$SCRIPT_DIR")"
cd "$LOOP_DIR"

echo "[loop-triage] Reading skill..."
# In Oh My OpenCode context, skill://freqtrade-triage would be loaded
# For standalone script: run triage commands directly
echo "[loop-triage] Checking strategy files..."
ls -la /root/code/freqtrade/user_data/strategies/

echo "[loop-triage] Checking recent backtests..."
find /root/code/freqtrade/user_data/backtest_results/ -name '*.json' -mtime -7 2>/dev/null | head -5 || echo "No recent backtests"

echo "[loop-triage] Checking logs for errors..."
tail -50 /root/code/freqtrade/user_data/logs/freqtrade.log 2>/dev/null | grep -i "ERROR\|451\|blocked\|GEO" || echo "No critical errors"

echo "[loop-triage] Checking git log..."
git -C /root/code/freqtrade log --oneline -10 2>/dev/null || echo "Not a git repo"

echo "[loop-triage] Checking dry_run mode..."
grep '"dry_run":' /root/code/freqtrade/user_data/config.json 2>/dev/null || echo "Config not found"

echo "[loop-triage] Updating STATE.md timestamp..."
sed -i "s/Last run:.*/Last run: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" /root/code/freqtrade/freqtrade-loop/STATE.md 2>/dev/null || true

echo "[loop-triage] Done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit 0
