#!/usr/bin/env python3
"""Preprocess fq-data-downloader data into freqtrade-compatible format.

The source data uses 'timestamp' (ms epoch) and is reverse-sorted.
Target format: 'date' as datetime, ascending order.
"""

from pathlib import Path

import pandas as pd


SRC = Path("/Users/jie/code/fq-data-downloader/data/binance")
DST = Path("/Users/jie/code/freqtrade/user_data/data/binance_fqd")

DST.mkdir(parents=True, exist_ok=True)

files = ["BTC_USDT-4h.feather", "BTC_USDT-1d.feather", "ETH_USDT-1d.feather"]
for f in files:
    src_path = SRC / f
    if not src_path.exists():
        print(f"SKIP {f} (not found)")
        continue
    df = pd.read_feather(src_path)
    if "date" not in df.columns:
        # Convert timestamp (ms) to date
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    df = df[["date", "open", "high", "low", "close", "volume"]]
    # freqtrade expects tz-naive datetime64[ns]
    df["date"] = df["date"].dt.tz_convert(None).astype("datetime64[ns]")
    out_path = DST / f
    df.to_feather(out_path)
    print(f"{f}: {len(df)} rows ({df.date.iloc[0]} -> {df.date.iloc[-1]})")
print(f"\nWritten to: {DST}")
