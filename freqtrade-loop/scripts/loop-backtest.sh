#!/bin/bash
# Backtest sweeper — runs backtest on changed strategy
# Usage: loop-backtest.sh <strategy_name> <strategy_file> <git_commit>
# Exit codes: 0=success(0 trades=valid), 1=backtest failed, 2=budget exceeded, 3=dry_run not confirmed, 4=API blocked
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <strategy_name> <strategy_file> <git_commit>"
  exit 3
fi

STRATEGY_NAME="$1"
STRATEGY_FILE="$2"
GIT_COMMIT="$3"

echo "[loop-backtest] Starting backtest for $STRATEGY_NAME"
echo "[loop-backtest] File: $STRATEGY_FILE"
echo "[loop-backtest] Commit: $GIT_COMMIT"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP_DIR="$(dirname "$SCRIPT_DIR")"

# Gate check: strategy file must be in user_data/strategies/
if [[ ! "$STRATEGY_FILE" =~ ^user_data/strategies/.*\.py$ ]]; then
  echo "[loop-backtest] ERROR: Invalid strategy path: $STRATEGY_FILE"
  exit 1
fi

# dry_run check
echo "[loop-backtest] Verifying dry_run mode..."
if ! grep -q '"dry_run": true' /root/code/freqtrade/user_data/config.json 2>/dev/null; then
  echo "[loop-backtest] ERROR: dry_run=true not confirmed in config.json"
  exit 3
fi

# Ensure backtest output dir exists
mkdir -p /root/code/freqtrade/user_data/backtest_results/

# Determine which config to use
CONFIG_FILE="/root/code/freqtrade/user_data/config.json"
DATA_DIR=""
DATA_SOURCE="unknown"
DATA_FORMAT=""

# Run backtest — try primary config first
echo "[loop-backtest] Running backtest..."
cd /root/code/freqtrade

set +e
/root/code/freqtrade/.venv/bin/python -m freqtrade backtesting \
  --strategy "$STRATEGY_NAME" \
  --timeframe 4h \
  --timerange 20230101-20231231 \
  --config "$CONFIG_FILE" \
  --backtest-directory /root/code/freqtrade/user_data/backtest_results/ \
  --export trades \
  > /tmp/backtest_run.log 2>&1
BACKTEST_EXIT=$?
set -euo pipefail

# Check for Binance API block
if grep -qi "451\|GEO\|blocked\|Service unavailable from a restricted location" /tmp/backtest_run.log; then
  echo "[loop-backtest] WARNING: Binance API blocked (451), switching to Gate.io config..."
  CONFIG_FILE="/root/code/freqtrade/user_data/config.gate.json"
  DATA_DIR="/root/code/freqtrade/user_data/data/gate"
  DATA_SOURCE="gate"

  # Verify gate data exists
  if [[ ! -d "$DATA_DIR" ]] || [[ -z "$(ls -A "$DATA_DIR"/*.parquet 2>/dev/null)" ]]; then
    echo "[loop-backtest] ERROR: No Gate.io data found at $DATA_DIR"
    echo "[loop-backtest] Run: freqtrade download-data --exchange gate --pairs BTC/USDT --timeframes 4h --timerange 20230101-20231231"
    exit 4
  fi

  set +e
  /root/code/freqtrade/.venv/bin/python -m freqtrade backtesting \
    --strategy "$STRATEGY_NAME" \
    --timeframe 4h \
    --timerange 20230101-20231231 \
    --config "$CONFIG_FILE" \
    -d "$DATA_DIR" \
    --data-format-ohlcv parquet \
    --backtest-directory /root/code/freqtrade/user_data/backtest_results/ \
    --export trades \
    > /tmp/backtest_run.log 2>&1
  BACKTEST_EXIT=$?
  set -euo pipefail
fi

# Print last 20 lines of log
echo "[loop-backtest] === Backtest log (last 20 lines) ==="
tail -20 /tmp/backtest_run.log
echo "[loop-backtest] === End log ==="

# Find latest backtest result zip
RESULT_ZIP=$(ls -t /root/code/freqtrade/user_data/backtest_results/backtest-result-*.zip 2>/dev/null | head -1)
if [[ -z "$RESULT_ZIP" ]]; then
  echo "[loop-backtest] WARNING: No backtest result zip found"
fi

# Parse backtest results from zip
BACKTEST_RESULT="null"
TRADES_COUNT=0
WIN_RATE="null"
PROFIT_TOTAL="null"
MAX_DRAWDOWN="null"

if [[ -n "$RESULT_ZIP" && -f "$RESULT_ZIP" ]]; then
  echo "[loop-backtest] Parsing result: $RESULT_ZIP"
  PARSE_OUTPUT=$(python3 << PYEOF
import zipfile, json, sys

result_zip = '$RESULT_ZIP'
try:
    with zipfile.ZipFile(result_zip) as z:
        # Find the strategy result JSON
        names = z.namelist()
        json_name = [n for n in names if n.endswith('.json') and 'result' in n and 'config' not in n and 'strategy' not in n]
        if not json_name:
            print("No result JSON found in zip")
            sys.exit(0)
        with z.open(json_name[0]) as f:
            data = json.load(f)
        
        strat_data = data.get('strategy', {}).get('$STRATEGY_NAME', {})
        strat_comp = [s for s in data.get('strategy_comparison', []) if s.get('key') == '$STRATEGY_NAME']
        
        if strat_comp:
            s = strat_comp[0]
        else:
            s = strat_data
        
        trades_count = s.get('trades', 0)
        win_rate = s.get('winrate')
        profit_total = s.get('profit_total_abs') or s.get('profit_total')
        max_drawdown = s.get('max_drawdown_abs') or s.get('max_drawdown')
        
        print(f"TRADES={trades_count}")
        print(f"WIN_RATE={win_rate}")
        print(f"PROFIT={profit_total}")
        print(f"DRAWDOWN={max_drawdown}")
        
        # Output full JSON for history recording
        record = {
            'total_trades': trades_count,
            'win_rate': win_rate,
            'profit_total': profit_total,
            'max_drawdown': max_drawdown,
            'profit_mean': s.get('profit_mean'),
            'avg_duration': s.get('duration_avg'),
            'sharpe': s.get('sharpe'),
            'sqn': s.get('sqn'),
        }
        print(f"RECORD_JSON={json.dumps(record)}")
except Exception as e:
    print(f"Parse error: {e}", file=sys.stderr)
PYEOF
)
  echo "$PARSE_OUTPUT"
  
  # Extract values from parse output
  TRADES_COUNT=$(echo "$PARSE_OUTPUT" | grep "^TRADES=" | cut -d= -f2)
  WIN_RATE=$(echo "$PARSE_OUTPUT" | grep "^WIN_RATE=" | cut -d= -f2)
  PROFIT_TOTAL=$(echo "$PARSE_OUTPUT" | grep "^PROFIT=" | cut -d= -f2)
  MAX_DRAWDOWN=$(echo "$PARSE_OUTPUT" | grep "^DRAWDOWN=" | cut -d= -f2)
  
  if [[ "$TRADES_COUNT" == "0" ]]; then
    echo "[loop-backtest] Backtest ran successfully — 0 trades (strategy produced no signals)"
  else
    echo "[loop-backtest] Backtest: $TRADES_COUNT trades, win_rate=$WIN_RATE, profit=$PROFIT_TOTAL, dd=$MAX_DRAWDOWN"
  fi
fi

echo "[loop-backtest] Done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Record to backtest-history.json
HISTORY_FILE="/root/code/freqtrade/freqtrade-loop/backtest-history.json"
RECORD_JSON=$(echo "$PARSE_OUTPUT" | grep "^RECORD_JSON=" | cut -d= -f2-)
if [[ -z "$RECORD_JSON" ]]; then
  RECORD_JSON="{}"
fi

python3 << PYEOF
import json, os, datetime

record = {
    'run_at': datetime.datetime.utcnow().isoformat() + 'Z',
    'strategy_name': '$STRATEGY_NAME',
    'strategy_file': '$STRATEGY_FILE',
    'git_commit': '$GIT_COMMIT',
    'timeframe': '4h',
    'timerange': '20230101-20231231',
    'data_source': '$DATA_SOURCE',
    'status': 'success' if $BACKTEST_EXIT == 0 else 'failed_error',
    'backtest_exit_code': $BACKTEST_EXIT,
    'backtest_result': $RECORD_JSON,
    'config_used': '$CONFIG_FILE',
    'backtest_result_zip': '$RESULT_ZIP',
}

if os.path.exists('$HISTORY_FILE'):
    with open('$HISTORY_FILE') as f:
        history = json.load(f)
else:
    history = {'runs': []}

history.setdefault('runs', []).append(record)
with open('$HISTORY_FILE', 'w') as f:
    json.dump(history, f, indent=2)
print(f'[loop-backtest] Recorded to backtest-history.json')
PYEOF

# Exit codes: 0 trades is a VALID backtest result (strategy just had no signals)
# Only exit non-zero for actual errors
if [[ "$DATA_SOURCE" == "offline-mock" ]]; then
  echo "[loop-backtest] Exit 4 (API blocked)"
  exit 4
fi
if [[ "$BACKTEST_EXIT" -ne 0 ]]; then
  echo "[loop-backtest] Exit 1 (backtest failed)"
  exit 1
fi
echo "[loop-backtest] Exit 0 (success)"
exit 0
