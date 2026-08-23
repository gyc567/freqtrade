"""freqtrade-loop: OHLCV downloader for Binance pairs.

Fetches candles from Binance public API (no auth needed) and saves them
in the same feather format used by the existing 4h/1h/1d data files
(timestamp ms int + OHLCV float). freqtrade's existing data prep step
(in run_backtest.py) auto-converts to (date, OHLCV) format.

Uses Binance's REST API directly (httpx) instead of ccxt because ccxt's
load_markets() call times out from this network (Binance geo-fences
exchangeInfo differently from klines).

Usage:
    python3 freqtrade-loop/download_5m.py                       # default 5m all 4 pairs
    python3 freqtrade-loop/download_5m.py --interval=15m         # 15m (for NFIX7)
    python3 freqtrade-loop/download_5m.py --interval=5m --pair=BTC/USDT
    python3 freqtrade-loop/download_5m.py --start=20230101
    python3 freqtrade-loop/download_5m.py --end=20260731

Output:
    /Users/jie/code/fq-data-downloader/data/binance/BTC_USDT-{interval}.feather
    ... (one file per pair)
"""

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd


DATA_DST = Path("/Users/jie/code/fq-data-downloader/data/binance")
PAIRS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]
BINANCE_LIMIT = 1000  # API max candles per call
RATE_SLEEP = 0.12  # ~8 calls/sec, under Binance 1200/min limit
BASE_URL = "https://api.binance.com"
VALID_INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d")


def _ts_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _symbol_to_binance(symbol: str) -> str:
    """Convert 'BTC/USDT' to 'BTCUSDT' (Binance's symbol format)."""
    return symbol.replace("/", "")


def _fetch(symbol: str, start_ms: int, end_ms: int, interval: str) -> pd.DataFrame:
    """Fetch all OHLCV candles for `symbol` at `interval` between start_ms and end_ms."""
    binance_sym = _symbol_to_binance(symbol)
    all_rows: list[list] = []
    since = start_ms
    call = 0
    timeout = httpx.Timeout(15.0, connect=10.0)
    # Use SOCKS5 proxy (Binance geo-fences api.binance.com in this region; proxy is required).
    # trust_env=True so HTTPS_PROXY env var is picked up.
    proxy_url = "socks5://127.0.0.1:7897"
    with httpx.Client(timeout=timeout, proxy=proxy_url) as client:
        while since < end_ms:
            try:
                resp = client.get(
                    f"{BASE_URL}/api/v3/klines",
                    params={
                        "symbol": binance_sym,
                        "interval": interval,
                        "startTime": since,
                        "limit": BINANCE_LIMIT,
                    },
                )
                resp.raise_for_status()
                batch = resp.json()
            except Exception as e:
                print(f"  WARN fetch failed: {type(e).__name__}: {e}", flush=True)
                print("  retrying in 5s...", flush=True)
                time.sleep(5)
                continue
            if not batch:
                break
            all_rows.extend(batch)
            # Advance: last candle's close time + 1 ms (avoid duplicate first candle)
            last_close_ms = batch[-1][6]  # kline: [openTime, o, h, l, c, v, closeTime, ...]
            since = last_close_ms + 1
            call += 1
            if call % 20 == 0:
                last_ts = datetime.fromtimestamp(last_close_ms / 1000, tz=UTC)
                print(
                    f"  {symbol} {interval}: {len(all_rows)} candles fetched, last_close={last_ts}",
                    flush=True,
                )
            time.sleep(RATE_SLEEP)
    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    # Binance kline format: [openTime, o, h, l, c, v, closeTime, ...]
    df = pd.DataFrame(
        [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in all_rows],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def download_pair(symbol: str, start_ms: int, end_ms: int, interval: str) -> dict:
    """Download data for one symbol at given interval. Returns stats dict."""
    feather_name = symbol.replace("/", "_") + f"-{interval}.feather"
    out_path = DATA_DST / feather_name

    print(f"\n=== {symbol} {interval} ===", flush=True)
    print(
        f"  range: {datetime.fromtimestamp(start_ms / 1000, tz=UTC)} "
        f"-> {datetime.fromtimestamp(end_ms / 1000, tz=UTC)}",
        flush=True,
    )
    print(f"  output: {out_path}", flush=True)

    started = time.time()
    df = _fetch(symbol, start_ms, end_ms, interval)
    elapsed = time.time() - started

    if df.empty:
        print("  FAILED: no data fetched", flush=True)
        return {"symbol": symbol, "status": "failed", "elapsed_s": round(elapsed, 1)}

    df.to_feather(out_path)
    print(f"  wrote {len(df)} candles to {out_path.name}", flush=True)
    print(f"  elapsed: {elapsed:.1f}s", flush=True)
    return {
        "symbol": symbol,
        "status": "success",
        "candles": len(df),
        "first_ts": datetime.fromtimestamp(df["timestamp"].iloc[0] / 1000, tz=UTC).isoformat(),
        "last_ts": datetime.fromtimestamp(df["timestamp"].iloc[-1] / 1000, tz=UTC).isoformat(),
        "elapsed_s": round(elapsed, 1),
        "output": str(out_path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", help="Single pair (e.g. BTC/USDT). Default: all 4")
    parser.add_argument(
        "--interval",
        default="5m",
        help=f"Binance kline interval (default 5m). Options: {', '.join(VALID_INTERVALS)}",
    )
    parser.add_argument("--start", default="20230101", help="YYYYMMDD (default 20230101)")
    parser.add_argument("--end", default="20260731", help="YYYYMMDD (default 20260731)")
    args = parser.parse_args()

    if args.interval not in VALID_INTERVALS:
        print(f"ERROR: --interval must be one of {VALID_INTERVALS}", file=sys.stderr)
        return 2

    pairs = [args.pair] if args.pair else PAIRS
    start_ms = _ts_to_ms(args.start)
    end_ms = _ts_to_ms(args.end) + 24 * 3600 * 1000  # include end date fully
    DATA_DST.mkdir(parents=True, exist_ok=True)

    print("=== freqtrade-loop OHLCV downloader ===", flush=True)
    print(f"  pairs:    {pairs}", flush=True)
    print(f"  interval: {args.interval}", flush=True)
    print(f"  range:    {args.start} -> {args.end}", flush=True)
    print(f"  dest:     {DATA_DST}", flush=True)
    print(f"  rate:     {1 / RATE_SLEEP:.1f} calls/sec", flush=True)

    results = [download_pair(p, start_ms, end_ms, args.interval) for p in pairs]

    print("\n=== summary ===", flush=True)
    for r in results:
        print(
            f"  {r['symbol']:12s}: {r.get('status', '?')} "
            f"candles={r.get('candles', '-')} elapsed={r.get('elapsed_s', '-')}s",
            flush=True,
        )
    ok = sum(1 for r in results if r.get("status") == "success")
    print(f"\n  {ok}/{len(results)} succeeded", flush=True)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
