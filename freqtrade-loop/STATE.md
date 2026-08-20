# Freqtrade Loop State
Last run: 2026-08-20T12:56:48Z
Loop version: 0.1.0

## Strategies
|Strategy|File|Last Modified|Last Backtest|Backtest Status|Notes|
|---|---|---|---|---|---|
|BTCHarmonic4H|user_data/strategies/BTCHarmonic4H.py|2026-08-20|2026-08-20|zero-trades|backtest ran (gate.io), 0 trades — strategy needs signal logic tuning|
|SampleStrategy|user_data/strategies/SampleStrategy.py|2026-08-12|never|signals-all-zero|default template only, not for trading|

## Recent Backtests
|Strategy|Status|Trades|Win Rate|Profit|Drawdown|Data Source|Commit|
|---|---|---|---|---|---|---|---|
|BTCHarmonic4H|success|0|N/A|N/A|N/A|gate|2026-08-20 12:56:48Z|

## Known Issues
- **Binance API is GEO-blocked (451)** — backtest uses Gate.io data via `config.gate.json`
- Gate.io data: 2999 BTC/USDT 4H candles (2023-01-26 to 2023-12-31)
- BTCHarmonic4H produces 0 trades — harmonic pattern detector not finding valid setups on this dataset

## Human Decisions
- BTC/USDT 4H is primary pair, no other pairs until validated
- dry_run mode always on, never switch to live without explicit human approval
- Backtest data source: Gate.io (Binance blocked); config at `user_data/config.gate.json`

## Watch List
- freqtrade API at localhost:8088 — monitor for uptime
- BTCHarmonic4H signal logic — needs investigation (0 trades on valid data)

## Loop Health
- Tokens today: ~60,000 (1 backtest run)
- Runs today: 1
- Budget status: OK (800,000 daily limit)
