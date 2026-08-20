# Freqtrade Loop Engineering

## Purpose
Automated strategy triage, backtest tracking, and hyperopt management for freqtrade.

## Active Patterns
|Pattern|Trigger|Cadence|Level|Notes|
|---|---|---|---|---|
|strategy-daily-triage|cron|1d 08:00 UTC|L1|Report only, no auto-modification|
|backtest-sweeper|git push user_data/strategies/*.py|on-change|L2|Verifies strategy changes via backtest|
|strategy-hyperopt-loop|manual + human gate|weekly|L2+human|Requires explicit human approval|

## Non-Goals
- Does NOT trade live (freqtrade trade mode requires human decision)
- Does NOT modify user_data/config.json
- Does NOT touch core freqtrade/ source code
- Does NOT run without dry_run=true confirmed
- Does NOT auto-merge hyperopt results

## Human Gates (必须 human approve)
1. hyperopt results writing to config.json
2. Switching dry_run to false
3. Adding new trading pairs
4. Modifying stoploss by more than 2%

## Emergency Stop
systemctl stop freqtrade-loop.timer
Or: set freqtrade-loop/loop-budget.yaml emergency_stop: true

## Pattern Promotion Criteria
L1 -> L2 promotion requires ALL of:
- 14 consecutive successful triage runs
- 0 false positive findings in last 14 runs
- Human has reviewed and approved in STATE.md
