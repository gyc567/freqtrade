"""Pre-backtest integration check: count enter_long=1 signals.

Per Audit #6: verify signal generation matches expectation before running backtest.
Expected: 5+ enter_long=1 signals for 2023 BTC/USDT 4h.
"""

import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, "/Users/jie/code/freqtrade")

from user_data.strategies.TrendRider4h import TrendRider4h


sys.path.insert(0, "/Users/jie/code/freqtrade")

# Auto-detect data source
for cand in [
    Path("/Users/jie/code/freqtrade/user_data/data/binance_fqd"),
    Path("/Users/jie/code/freqtrade/user_data/data/binance"),
    Path("/Users/jie/code/fq-data-downloader/data/binance"),
]:
    if (cand / "BTC_USDT-4h.feather").exists():
        DATADIR = cand
        break
else:
    print("ERROR: No BTC_USDT-4h.feather found")
    sys.exit(1)
print(f"Using data: {DATADIR}")

feather_4h = DATADIR / "BTC_USDT-4h.feather"
feather_1d = DATADIR / "BTC_USDT-1d.feather"
df_4h = pd.read_feather(feather_4h)
df_1d = pd.read_feather(feather_1d)

# Normalize date column
for df in (df_4h, df_1d):
    if "date" not in df.columns:
        if "timestamp" in df.columns:
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(None)
        else:
            raise ValueError(f"No date/timestamp column: {df.columns}")
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_convert(None)
    df["date"] = pd.to_datetime(df["date"])

# Trim 4h to 2023
mask = (df_4h["date"] >= "2023-01-01") & (df_4h["date"] < "2024-01-01")
df = df_4h[mask].reset_index(drop=True).copy()
df1d = df_1d.reset_index(drop=True).copy()

print(f"4h: {len(df)} rows ({df.date.iloc[0]} -> {df.date.iloc[-1]})")
print(f"1d: {len(df1d)} rows ({df1d.date.iloc[0]} -> {df1d.date.iloc[-1]})")


class FakeDP:
    def get_pair_dataframe(self, pair, timeframe):
        return df1d.copy() if timeframe == "1d" else df.copy()


class FakeEx:
    def get_pair_quote_currency(self, p):
        return "USDT"

    def get_pair_base_currency(self, p):
        return "BTC"


strat = TrendRider4h({"minimal_roi": {}, "stoploss": -0.08, "trailing_stop": False})
strat._exchange = FakeEx()
strat.dp = FakeDP()
strat.timeframe = "4h"

df = strat.populate_indicators(df, {"pair": "BTC/USDT"})
df = strat.populate_entry_trend(df, {"pair": "BTC/USDT"})

entries = df[df["enter_long"] == 1].copy()
print(f"\n=== enter_long=1 signals: {len(entries)} ===")
for _idx, row in entries.iterrows():
    rsi2 = row.get("rsi_2", float("nan"))
    rsi14 = row.get("rsi_14", float("nan"))
    adx = row.get("adx", float("nan"))
    vol = row.get("volume", float("nan"))
    vol_sma = row.get("vol_sma_20", float("nan"))
    vol_ratio = (vol / vol_sma) if vol_sma and vol_sma > 0 else float("nan")
    tag = row.get("enter_tag", "N/A")
    print(
        f"  {row['date']} | close={row['close']:.0f} | tag={tag} | rsi2={rsi2:.1f} | "
        f"rsi14={rsi14:.1f} | adx={adx:.1f} | vol_ratio={vol_ratio:.2f}"
    )

# Verification
if len(entries) >= 5:
    print(f"\n✓ PASS: {len(entries)} signals >= 5 threshold. Backtest integration OK.")
elif len(entries) >= 1:
    n = len(entries)
    print(f"\n⚠ WARN: only {n} signals. May indicate over-filtered conditions.")
else:
    print("\n✗ FAIL: 0 signals. Do NOT run backtest — debug signal generation first.")
