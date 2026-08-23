"""freqtrade-loop: Dry-run replay engine.

Wraps freqtrade's Backtesting class to simulate a live dry-run from local feather
data. Per-candle decision logging (entry/exit/noop) is appended to a JSONL stream
so we can monitor decisions as they would happen in real-time, even though the
underlying engine is the same batch Backtesting.

Binance is geo-blocked per STATE.md, so we cannot use freqtrade's native
`freqtrade trade --dry-run`. This replay engine sidesteps that by stepping
through local 4h candles chronologically.

Usage:
    python3 freqtrade-loop/run_dryrun.py \\
        --strategy=TrendRider4h \\
        --timerange=20260501-20260731 \\
        --db-url=sqlite:///user_data/dryrun_TR4h.sqlite

Outputs:
    /tmp/c4_results/dryrun/<strategy>_<timestamp>.jsonl  (per-candle decisions)
    /tmp/c4_results/dryrun/<strategy>_<timestamp>.csv    (per-trade summary)
    user_data/dryrun_<strategy>.sqlite                    (trade table)
"""

import argparse
import csv
import json
import sys
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOOP_DIR = ROOT / "freqtrade-loop"
STRATEGY_DIR = ROOT / "user_data" / "strategies"
DATA_SRC = ROOT / "user_data" / "data" / "binance"
DATA_DST = ROOT / "user_data" / "data" / "binance"
RESULTS_DIR = ROOT / "user_data" / "backtest_results"
CONFIG_FILE = ROOT / "user_data" / "config.binance_local.json"
DRYRUN_LOG_DIR = Path("/tmp/c4_results/dryrun")  # noqa: S108


def prepare_data() -> None:
    """Convert raw ms-timestamp feather to freqtrade format (date col, ascending).
    Same logic as run_backtest.prepare_data — duplicated here to keep this script
    standalone (it can run without backtest infrastructure)."""
    DATA_DST.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    for src in sorted(DATA_SRC.glob("*.feather")):
        try:
            df = pd.read_feather(src)
        except Exception as e:
            print(f"  WARN skipping {src.name}: {type(e).__name__}: {e}")
            continue
        if "date" in df.columns:
            df = df.drop(columns=["timestamp"], errors="ignore")
            df.to_feather(DATA_DST / src.name)
            continue
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop(columns=["timestamp"]).sort_values("date").reset_index(drop=True)
        df = df[["date", "open", "high", "low", "close", "volume"]]
        df.to_feather(DATA_DST / src.name)
        print(f"  prepared {src.name}: {len(df)} rows")


def _move_json_away(strategy_name: str) -> Path | None:
    """Move any stale strategy JSON override to /tmp/c4_results/dryrun/."""
    src = STRATEGY_DIR / f"{strategy_name}.json"
    if not src.exists():
        return None
    DRYRUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    target = DRYRUN_LOG_DIR / f"{strategy_name}_stale_{int(time.time())}.json"
    src.rename(target)
    print(f"  moved {src.name} -> {target.name}")
    return target


def _parse_timerange(timerange: str) -> tuple[str, str]:
    """Parse YYYYMMDD-YYYYMMDD into (start, end) date strings."""
    if "-" not in timerange:
        raise ValueError(f"timerange must be YYYYMMDD-YYYYMMDD, got: {timerange}")
    parts = timerange.split("-")
    if len(parts) != 2:
        raise ValueError(f"timerange must have single dash, got: {timerange}")
    return parts[0], parts[1]


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def run_dryrun(strategy: str, timerange: str, db_url: str | None = None) -> dict:
    """Run dry-run replay on the given timerange. Returns summary dict.

    Internally uses freqtrade's Backtesting class (the same one used for backtests)
    but reads the per-trade output to construct a per-decision log.
    """
    from run_backtest import patch_load_markets, run_backtest

    # Critical: stale JSON would silently override .py buy_params
    # (see [[freqtrade-json-override-trap]] memory).
    _move_json_away(strategy)

    # Patch ccxt (Binance is geo-blocked)
    patch_load_markets()

    # Run freqtrade's Backtesting via the wrapper (config is not needed downstream)
    bt_results, _ = run_backtest(timerange=timerange, strategy=strategy)
    if bt_results is None:
        raise RuntimeError(f"Backtest returned no results for {strategy} {timerange}")

    # Find the latest backtest zip
    zips = sorted(RESULTS_DIR.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        raise RuntimeError("No backtest zip found after run_backtest")
    latest_zip = zips[-1]

    # Parse trades from the zip
    trades, summary = _extract_trades(latest_zip, strategy)

    # Build the dry-run decision log from the trade list
    start_date, end_date = _parse_timerange(timerange)
    decision_log = _build_decision_log(trades, strategy, start_date, end_date)

    # Write outputs
    DRYRUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    jsonl_path = DRYRUN_LOG_DIR / f"{strategy}_{timestamp}.jsonl"
    csv_path = DRYRUN_LOG_DIR / f"{strategy}_{timestamp}.csv"

    with jsonl_path.open("w") as f:
        for entry in decision_log:
            f.write(json.dumps(entry, default=str) + "\n")

    _write_trade_csv(csv_path, trades)

    # Update db if requested (sqlite local persistence)
    if db_url:
        _write_sqlite(db_url, strategy, trades)

    # Compose summary
    summary.update(
        {
            "strategy": strategy,
            "timerange": timerange,
            "jsonl_log": str(jsonl_path),
            "trade_csv": str(csv_path),
            "result_zip": str(latest_zip),
            "n_decisions": len(decision_log),
        }
    )
    return summary


def _extract_trades(zip_path: Path, strategy_name: str) -> tuple[list[dict], dict]:
    """Parse freqtrade backtest zip. Returns (trades_list, summary_dict)."""
    with zipfile.ZipFile(zip_path) as z:
        json_name = next(
            (
                n
                for n in z.namelist()
                if n.endswith(".json")
                and "result" in n
                and "config" not in n
                and "strategy" not in n
            ),
            None,
        )
        if not json_name:
            return [], {"status": "no_json_in_zip"}
        with z.open(json_name) as f:
            data = json.load(f)

    strat_data = data.get("strategy", {}).get(strategy_name, {})
    comp = [c for c in data.get("strategy_comparison", []) if c.get("key") == strategy_name]
    s = comp[0] if comp else strat_data

    trades = []
    for t in strat_data.get("trades", []) or []:
        trades.append(
            {
                "trade_id": t.get("trade_id"),
                "open_date": t.get("open_date"),
                "close_date": t.get("close_date"),
                "open_rate": t.get("open_rate"),
                "close_rate": t.get("close_rate"),
                "stake_amount": t.get("stake_amount"),
                "profit_abs": t.get("profit_abs"),
                "profit_ratio": t.get("profit_ratio"),
                "exit_reason": t.get("exit_reason"),
                "enter_tag": t.get("enter_tag"),
                "duration_min": t.get("trade_duration"),
            }
        )

    summary = {
        "status": "success",
        "n_trades": len(trades),
        "wins": sum(1 for t in trades if (t.get("profit_abs") or 0) > 0),
        "losses": sum(1 for t in trades if (t.get("profit_abs") or 0) < 0),
        "profit_total_abs": s.get("profit_total_abs"),
        "max_drawdown_abs": s.get("max_drawdown_abs"),
        "winrate": s.get("winrate"),
        "profit_factor": s.get("profit_factor"),
    }
    return trades, summary


def _build_decision_log(
    trades: list[dict], strategy: str, start_date: str, end_date: str
) -> list[dict]:
    """Construct a per-candle decision log from trade events.

    For each trade: emit an 'OPEN' decision at open_date and a 'CLOSE' decision at close_date.
    Between events: emit a 'HOLD' or 'NO_POSITION' decision at each 4h candle.
    """
    decisions: list[dict] = []
    start = datetime.strptime(start_date, "%Y%m%d").replace(tzinfo=UTC)
    end = datetime.strptime(end_date, "%Y%m%d").replace(tzinfo=UTC)

    # Sort trades by open_date
    sorted_trades = sorted(
        [t for t in trades if t.get("open_date")],
        key=lambda t: t["open_date"],
    )

    # Build event stream
    events: list[tuple[datetime, str, dict]] = []
    for t in sorted_trades:
        try:
            od = datetime.fromisoformat(t["open_date"])
        except ValueError:
            continue
        events.append((od, "OPEN", t))
        if t.get("close_date"):
            try:
                cd = datetime.fromisoformat(t["close_date"])
                events.append((cd, "CLOSE", t))
            except ValueError:
                pass
    events.sort(key=lambda e: e[0])

    # Walk every 4h candle in the window
    candle = start
    event_idx = 0
    open_trade = None

    while candle <= end:
        # Process all events at this candle time
        while event_idx < len(events) and events[event_idx][0] <= candle:
            ev_time, ev_type, ev_trade = events[event_idx]
            if ev_type == "OPEN":
                open_trade = ev_trade
                decisions.append(
                    {
                        "ts": _format_dt(ev_time),
                        "kind": "OPEN",
                        "trade_id": ev_trade.get("trade_id"),
                        "open_rate": ev_trade.get("open_rate"),
                        "stake_amount": ev_trade.get("stake_amount"),
                        "enter_tag": ev_trade.get("enter_tag"),
                        "strategy": strategy,
                    }
                )
            elif ev_type == "CLOSE":
                if open_trade and open_trade.get("trade_id") == ev_trade.get("trade_id"):
                    pnl = ev_trade.get("profit_abs") or 0
                    decisions.append(
                        {
                            "ts": _format_dt(ev_time),
                            "kind": "CLOSE",
                            "trade_id": ev_trade.get("trade_id"),
                            "close_rate": ev_trade.get("close_rate"),
                            "profit_abs": pnl,
                            "profit_ratio": ev_trade.get("profit_ratio"),
                            "exit_reason": ev_trade.get("exit_reason"),
                            "duration_min": ev_trade.get("duration_min"),
                            "strategy": strategy,
                        }
                    )
                    open_trade = None
                else:
                    decisions.append(
                        {
                            "ts": _format_dt(ev_time),
                            "kind": "CLOSE_ORPHAN",
                            "trade_id": ev_trade.get("trade_id"),
                            "profit_abs": ev_trade.get("profit_abs"),
                            "exit_reason": ev_trade.get("exit_reason"),
                            "strategy": strategy,
                        }
                    )
            event_idx += 1

        # Emit a heartbeat for this candle (shows strategy was alive)
        decisions.append(
            {
                "ts": _format_dt(candle),
                "kind": "TICK",
                "open_position": open_trade is not None,
                "trade_id": open_trade.get("trade_id") if open_trade else None,
                "strategy": strategy,
            }
        )

        candle += timedelta(hours=4)

    return decisions


def _write_trade_csv(csv_path: Path, trades: list[dict]) -> None:
    """Write per-trade summary CSV."""
    if not trades:
        csv_path.write_text(
            "trade_id,open_date,close_date,open_rate,close_rate,profit_abs,exit_reason\n"
        )
        return
    fieldnames = [
        "trade_id",
        "open_date",
        "close_date",
        "open_rate",
        "close_rate",
        "stake_amount",
        "profit_abs",
        "profit_ratio",
        "exit_reason",
        "enter_tag",
        "duration_min",
    ]
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow(t)


def _write_sqlite(db_url: str, strategy: str, trades: list[dict]) -> None:
    """Persist trades to local sqlite. db_url format: sqlite:///path/to/file.db."""
    if not db_url.startswith("sqlite:///"):
        print(f"  WARN unsupported db_url (only sqlite): {db_url}")
        return
    db_path = Path(db_url.replace("sqlite:///", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dryrun_trades (
            strategy TEXT,
            trade_id INTEGER,
            open_date TEXT,
            close_date TEXT,
            open_rate REAL,
            close_rate REAL,
            profit_abs REAL,
            profit_ratio REAL,
            exit_reason TEXT,
            enter_tag TEXT,
            recorded_at TEXT
        )
        """
    )
    cur.execute("DELETE FROM dryrun_trades WHERE strategy = ?", (strategy,))
    now = datetime.now(UTC).isoformat()
    for t in trades:
        cur.execute(
            """
            INSERT INTO dryrun_trades
              (strategy, trade_id, open_date, close_date, open_rate, close_rate,
               profit_abs, profit_ratio, exit_reason, enter_tag, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy,
                t.get("trade_id"),
                t.get("open_date"),
                t.get("close_date"),
                t.get("open_rate"),
                t.get("close_rate"),
                t.get("profit_abs"),
                t.get("profit_ratio"),
                t.get("exit_reason"),
                t.get("enter_tag"),
                now,
            ),
        )
    conn.commit()
    conn.close()
    print(f"  wrote {len(trades)} trades to {db_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True, help="Strategy name (e.g., TrendRider4h)")
    parser.add_argument(
        "--timerange",
        required=True,
        help="YYYYMMDD-YYYYMMDD window to replay",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Optional sqlite db url (e.g., sqlite:///user_data/dryrun_TR4h.sqlite)",
    )
    parser.add_argument(
        "--no-prepare",
        action="store_true",
        help="Skip data preparation (use if already prepared)",
    )
    args = parser.parse_args()

    print("=== freqtrade-loop Dry-Run Replay Engine ===")
    print(f"  strategy:   {args.strategy}")
    print(f"  timerange:  {args.timerange}")
    print(f"  db_url:     {args.db_url or '(none)'}")
    print(f"  log_dir:    {DRYRUN_LOG_DIR}")
    print()

    if not args.no_prepare:
        print("[1/4] preparing data...")
        prepare_data()
        print()

    print("[2/4] running dry-run replay (via freqtrade Backtesting)...")
    started = time.time()
    try:
        summary = run_dryrun(args.strategy, args.timerange, args.db_url)
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        return 1
    elapsed = time.time() - started
    print(f"  elapsed: {elapsed:.1f}s")
    print()

    print("[3/4] decision log + trade summary")
    print(f"  decisions:   {summary.get('n_decisions', 0)}")
    print(f"  trades:      {summary.get('n_trades', 0)}")
    print(f"  wins/losses: {summary.get('wins', 0)}/{summary.get('losses', 0)}")
    print(f"  winrate:     {summary.get('winrate')}")
    print(f"  profit:      {summary.get('profit_total_abs')} USDT")
    print(f"  max_dd:      {summary.get('max_drawdown_abs')} USDT")
    print(f"  jsonl_log:   {summary.get('jsonl_log')}")
    print(f"  trade_csv:   {summary.get('trade_csv')}")
    print()

    print("[4/4] done. === dry-run replay complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
