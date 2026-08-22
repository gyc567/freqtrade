"""
freqtrade-loop: offline backtest wrapper for TrendRider4h.

Patches ccxt's Binance load_markets to skip futures API call, then runs
freqtrade backtesting programmatically. Records results into the loop
framework (backtest-history.json + STATE.md + loop-ledger.json).
"""

import json
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


# --- Path constants ---
ROOT = Path("/Users/jie/code/freqtrade")
LOOP_DIR = ROOT / "freqtrade-loop"
DATA_SRC = Path("/Users/jie/code/fq-data-downloader/data/binance")
DATA_DST = ROOT / "user_data" / "data" / "binance"
RESULTS_DIR = ROOT / "user_data" / "backtest_results"
CONFIG_FILE = ROOT / "user_data" / "config.binance_local.json"


def prepare_data():
    """Convert raw ms-timestamp feather to freqtrade format (date col, ascending).
    Skips unreadable source files with a warning (corrupted pyarrow metadata is common
    for files produced by older downloader versions — we don't want to abort a backtest
    over one bad symbol)."""
    DATA_DST.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    for src in sorted(DATA_SRC.glob("*.feather")):
        try:
            df = pd.read_feather(src)
        except Exception as e:
            print(f"  WARN skipping {src.name}: {type(e).__name__}: {e}")
            continue
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop(columns=["timestamp"]).sort_values("date").reset_index(drop=True)
        df = df[["date", "open", "high", "low", "close", "volume"]]
        df.to_feather(DATA_DST / src.name)
        print(f"  prepared {src.name}: {len(df)} rows")


def patch_load_markets():
    """Skip live market fetch — provide minimal markets dict for spot BTC/USDT."""
    import ccxt.async_support.binance as ba

    btc_usdt_market = {
        "id": "BTCUSDT",
        "symbol": "BTC/USDT",
        "base": "BTC",
        "quote": "USDT",
        "baseId": "BTC",
        "quoteId": "USDT",
        "active": True,
        "spot": True,
        "margin": False,
        "future": False,
        "swap": False,
        "option": False,
        "contract": False,
        "type": "spot",
        "linear": None,
        "inverse": None,
        "settle": None,
        "settleId": None,
        "contractSize": None,
        "taker": 0.001,
        "maker": 0.001,
        "feeSide": "get",
        "percentage": True,
        "tierBased": True,
        "precision": {
            "amount": 8,
            "price": 2,
            "base": 8,
            "quote": 8,
            "mode": "DECIMAL",
        },
        "limits": {
            "amount": {"min": 0.0001, "max": 9000.0},
            "price": {"min": 0.01, "max": 1000000.0},
            "cost": {"min": 10.0, "max": 9000000.0},
            "leverage": {"min": 1.0, "max": 1.0},
            "market": {"min": 0.0, "max": 0.0},
        },
        "info": {},
        "subType": None,
    }

    async def stub_load_markets(self, reload=False, params=None):
        self.markets = {"BTC/USDT": btc_usdt_market}
        self.markets_by_id = {"BTCUSDT": btc_usdt_market}
        self.symbols = ["BTC/USDT"]
        self.ids = ["BTCUSDT"]
        # Fee schedule for fee lookup
        self.fees = {"trading": {"taker": 0.001, "maker": 0.001}, "funding": {}}
        # Override precisionMode to DECIMAL_PLACES (2). Default ccxt Binance is 4,
        # which causes amount_to_contract_precision to assert-fail and return 0.
        self.precisionMode = 2
        return self.markets

    # The Binance class itself is named `binance` and is the module class.
    ba.load_markets = stub_load_markets
    import ccxt.binance as bs

    bs.load_markets = stub_load_markets

    # Patch precisionMode at class level so future instances use DECIMAL_PLACES (2).
    # Binance ccxt default is 4 (TICK_SIZE-based), causes amount_to_contract_precision to return 0.
    ba.precisionMode = 2
    bs.precisionMode = 2

    # Also patch describe() to return precisionMode=2, so when an instance is constructed,
    # its describe()-driven init copies the right value into self.precisionMode.
    _orig_describe_async = ba.describe

    def _patched_describe_async(self):
        d = _orig_describe_async(self)
        d["precisionMode"] = 2
        return d

    ba.describe = _patched_describe_async
    _orig_describe_sync = bs.describe

    def _patched_describe_sync(self):
        d = _orig_describe_sync(self)
        d["precisionMode"] = 2
        return d

    bs.describe = _patched_describe_sync


def run_backtest(timerange: str = "20230101-20240101", strategy: str = "TrendRider4h"):
    """Run freqtrade backtest via internal API."""
    # Patch ccxt BEFORE Backtesting() constructs an Exchange (Binance is
    # geo-blocked per STATE.md; without this, Backtesting fails to load markets).
    patch_load_markets()

    from freqtrade.configuration import Configuration
    from freqtrade.optimize.backtesting import Backtesting

    # Load config
    config = Configuration.from_files([str(CONFIG_FILE)])
    # Backtesting-specific config (use Path objects where freqtrade expects them)
    config["strategy"] = strategy
    config["user_data_dir"] = ROOT / "user_data"
    config["strategy_path"] = str(ROOT / "user_data" / "strategies")
    config["timerange"] = timerange
    config["dry_run_wallet"] = 1000
    config["runmode"] = "backtest"

    # Override timeframe from the strategy's class attribute (config's "timeframe"
    # would otherwise win — see Cycle 13 finding). This lets variants like
    # NostalgiaForInfinity1h use 1h data without editing the shared config.
    try:
        import importlib
        import sys

        # freqtrade sets cwd to user_data_dir via internal API; user_data is on path
        for candidate in (ROOT, ROOT / "user_data", Path.cwd()):
            p = str(candidate)
            if p not in sys.path:
                sys.path.insert(0, p)
        mod = importlib.import_module(f"user_data.strategies.{strategy}")
        cls = next(
            (
                v
                for v in vars(mod).values()
                if isinstance(v, type) and v.__module__ == mod.__name__ and hasattr(v, "timeframe")
            ),
            None,
        )
        if cls and hasattr(cls, "timeframe"):
            config["timeframe"] = cls.timeframe
            print(f"  strategy timeframe: {cls.timeframe} (overrides config)")
    except Exception as e:
        print(f"  WARN could not derive strategy timeframe: {type(e).__name__}: {e}")

    # Run backtest
    bt = Backtesting(config)
    bt.start()
    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return bt.results, config


def record_result(results, config, run_status, run_exit, strategy: str = "TrendRider4h"):
    """Append to backtest-history.json + update STATE.md + loop-ledger.json."""
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Try to get metrics from latest result zip
    record_metrics = {}
    zips = sorted(RESULTS_DIR.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime)
    if zips:
        latest = zips[-1]
        try:
            with zipfile.ZipFile(latest) as z:
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
                if json_name:
                    with z.open(json_name) as f:
                        data = json.load(f)
                    sname = strategy
                    strat_data = data.get("strategy", {}).get(sname, {})
                    comp = [c for c in data.get("strategy_comparison", []) if c.get("key") == sname]
                    s = comp[0] if comp else strat_data
                    record_metrics = {
                        "total_trades": s.get("trades", 0),
                        "win_rate": s.get("winrate"),
                        "profit_total": s.get("profit_total_abs") or s.get("profit_total"),
                        "max_drawdown": s.get("max_drawdown_abs") or s.get("max_drawdown"),
                        "profit_mean": s.get("profit_mean"),
                        "duration_avg": s.get("duration_avg"),
                        "sharpe": s.get("sharpe"),
                        "sqn": s.get("sqn"),
                        "profit_factor": s.get("profit_factor"),
                    }
        except Exception as e:
            print(f"  parse warning: {e}")

    # --- backtest-history.json ---
    history_file = LOOP_DIR / "backtest-history.json"
    history: dict[str, Any] = {"runs": []}
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text())
        except Exception:
            history = {"runs": []}

    history.setdefault("runs", []).append(
        {
            "run_at": timestamp,
            "strategy_name": strategy,
            "strategy_file": f"user_data/strategies/{strategy}.py",
            "git_commit": "(uncommitted)",
            "timeframe": "4h",
            "timerange": "20230101-20240101",
            "data_source": "binance-local",
            "data_path": str(DATA_SRC),
            "config_used": str(CONFIG_FILE),
            "status": run_status,
            "backtest_exit_code": run_exit,
            "backtest_result": record_metrics,
        }
    )
    history_file.write_text(json.dumps(history, indent=2))
    print(f"  appended to {history_file.relative_to(ROOT)}")

    # --- loop-ledger.json ---
    ledger_file = LOOP_DIR / "loop-ledger.json"
    ledger: dict[str, Any] = {
        "ledger_version": "1.0.0",
        "runs": [],
        "total_tokens_spent": 0,
        "total_runs": 0,
        "last_reset": "2026-08-20T00:00:00Z",
    }
    if ledger_file.exists():
        try:
            ledger = json.loads(ledger_file.read_text())
        except Exception as e:
            print(f"  ledger read warning: {e}")
            ledger = {
                "ledger_version": "1.0.0",
                "runs": [],
                "total_tokens_spent": 0,
                "total_runs": 0,
                "last_reset": "2026-08-20T00:00:00Z",
            }
    ledger.setdefault("runs", []).append(
        {
            "timestamp": timestamp,
            "action": "backtest",
            "strategy": strategy,
            "tokens_spent": 0,
            "status": run_status,
        }
    )
    ledger["total_runs"] = ledger.get("total_runs", 0) + 1
    ledger_file.write_text(json.dumps(ledger, indent=2))
    print(f"  appended to {ledger_file.relative_to(ROOT)}")

    return record_metrics


def _format_pf(metrics):
    pf = metrics.get("profit_factor")
    return f"{pf:.2f}" if pf not in (None, "?", "null") else "?"


def _format_wr(metrics):
    wr = metrics.get("win_rate")
    return f"{wr * 100:.1f}%" if isinstance(wr, (int, float)) else "?"


def _replace_or_insert_row(lines, prefix, new_row, fallback_prefix="|---"):
    """Replace first line starting with `prefix` in `lines` with `new_row`.
    If not found, insert after first line starting with `fallback_prefix`."""
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = new_row
            return lines
    for i, line in enumerate(lines):
        if line.startswith(fallback_prefix):
            lines.insert(i + 1, new_row)
            return lines
    return lines


def _update_strategies_table(text, strategy, run_at_iso, status_emoji, metrics):
    """Update the ## Strategies section row for {strategy}."""
    pf_str = _format_pf(metrics)
    wr_str = _format_wr(metrics)
    new_row = (
        f"|{strategy}|user_data/strategies/{strategy}.py|"
        f"{run_at_iso[:10]}|{run_at_iso[:10]}|{status_emoji}|"
        f"trades={metrics.get('total_trades', '?')}, "
        f"wr={wr_str}, "
        f"pf={pf_str}, "
        f"dd={metrics.get('max_drawdown', '?')} "
        f"(profit={metrics.get('profit_total', '?')})|"
    )
    all_lines = text.splitlines()
    all_lines = _replace_or_insert_row(all_lines, f"|{strategy}|", new_row, "|SampleStrategy")
    return "\n".join(all_lines)


def _update_recent_backtests(text, strategy, run_at_iso, status_emoji, metrics):
    """Update the ## Recent Backtests section row for {strategy}."""
    wr_r = _format_wr(metrics)
    new_row = (
        f"|{strategy}|{status_emoji}|{metrics.get('total_trades', '?')}|"
        f"{wr_r}|{metrics.get('profit_total', '?')}|"
        f"{metrics.get('max_drawdown', '?')}|binance-local|{run_at_iso}|"
    )
    before_recent, _, after_recent = text.partition("## Recent Backtests")
    recent_lines = after_recent.splitlines()
    recent_lines = _replace_or_insert_row(recent_lines, f"|{strategy}|", new_row, "|---")
    return before_recent + "## Recent Backtests" + "\n".join(recent_lines)


def update_state_md(
    metrics,
    run_status,
    run_at_iso,
    timerange: str = "20230101-20240101",
    strategy: str = "TrendRider4h",
):
    """Insert {strategy} row into STATE.md."""
    state_file = LOOP_DIR / "STATE.md"
    text = state_file.read_text()

    # Update "Last run" line
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Last run:"):
            lines[i] = f"Last run: {run_at_iso}"
            break
    text = "\n".join(lines)

    status_emoji = "success" if run_status == "success" else "failed_error"
    text = _update_strategies_table(text, strategy, run_at_iso, status_emoji, metrics)
    text = _update_recent_backtests(text, strategy, run_at_iso, status_emoji, metrics)

    state_file.write_text(text + "\n")
    print(f"  updated {state_file.relative_to(ROOT)}")


def main():
    print("=== freqtrade-loop backtest wrapper ===")
    print(f"time: {datetime.now(UTC).isoformat()}")
    print()

    # CLI: --timerange=YYYYMMDD-YYYYMMDD --strategy=NAME (default: TrendRider4h)
    timerange = "20230101-20240101"
    strategy = "TrendRider4h"
    for arg in sys.argv[1:]:
        if arg.startswith("--timerange="):
            timerange = arg.split("=", 1)[1]
        elif arg.startswith("--strategy="):
            strategy = arg.split("=", 1)[1]
    print(f"  timerange: {timerange}")
    print(f"  strategy: {strategy}")
    print()

    print("[1/4] preparing data...")
    prepare_data()
    print()

    print("[2/4] patching ccxt load_markets (skip futures API)...")
    patch_load_markets()
    print()

    print("[3/4] running backtest...")
    started = time.time()
    status = "failed_error"
    exit_code = 1
    metrics = {}
    try:
        run_backtest(timerange, strategy)
        status = "success"
        exit_code = 0
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
        status = "success" if exit_code == 0 else "failed_error"
    except Exception as e:
        print(f"  backtest raised: {type(e).__name__}: {e}")
        status = "failed_error"
    elapsed = time.time() - started
    print(f"  elapsed: {elapsed:.1f}s, status={status}, exit={exit_code}")
    print()

    print("[4/4] recording result + updating STATE.md...")
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    metrics = record_result(None, None, status, exit_code, strategy)
    # Pass timerange to state update so it gets stamped on the row
    update_state_md(metrics, status, timestamp, timerange, strategy)

    print()
    print(f"=== done. status={status} ===")
    if metrics:
        print(
            f"  trades={metrics.get('total_trades')}  win_rate={metrics.get('win_rate')}  "
            f"profit={metrics.get('profit_total')}  dd={metrics.get('max_drawdown')}"
        )
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
