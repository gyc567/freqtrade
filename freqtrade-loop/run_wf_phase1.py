"""freqtrade-loop: Walk-Forward Phase 1 orchestrator.

Runs 5 WF windows x 2 strategies = 10 hyperopt + 10 backtest cycles.
Each cycle:
  1. Move any stale strategy JSON override away
  2. Run hyperopt on train period (auto-dumps <strategy>.json)
  3. Save hyperopt JSON to wf{N}_{strategy}_hyperopt.json for reference
  4. Run backtest on test period (picks up the JSON just dumped)
  5. Parse metrics from backtest zip
  6. Append to /tmp/c4_results/wf_results.csv
  7. Move JSON aside so next window starts clean

Usage:
    python3 freqtrade-loop/run_wf_phase1.py             # run all 5 windows x 2 strategies
    python3 freqtrade-loop/run_wf_phase1.py --window=1 # run only WF1
    python3 freqtrade-loop/run_wf_phase1.py --window=1 --strategy=TrendRider4h
"""

import argparse
import csv
import json
import shutil
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path("/Users/jie/code/freqtrade")
LOOP_DIR = ROOT / "freqtrade-loop"
STRATEGY_DIR = ROOT / "user_data" / "strategies"
DATA_SRC = Path("/Users/jie/code/fq-data-downloader/data/binance")
RESULTS_DIR = ROOT / "user_data" / "backtest_results"
CONFIG_FILE = ROOT / "user_data" / "config.binance_local.json"
WF_RESULTS_CSV = Path("/tmp/c4_results/wf_results.csv")  # noqa: S108
WF_FROZEN_CSV = Path("/tmp/c4_results/wf_results_frozen.csv")  # noqa: S108
WF_LOGS_DIR = Path("/tmp/c4_results/wf_logs")  # noqa: S108


# 5 WF windows + 1 BLIND (Phase 3). Each has train + test + embargo.
WINDOWS = [
    {"id": "WF1", "train": "20230101-20230930", "test": "20231007-20240207", "embargo_days": 7},
    {"id": "WF2", "train": "20230601-20240229", "test": "20240307-20240707", "embargo_days": 7},
    {"id": "WF3", "train": "20231101-20240731", "test": "20240807-20241207", "embargo_days": 7},
    {"id": "WF4", "train": "20240401-20241231", "test": "20250107-20250507", "embargo_days": 7},
    {"id": "WF5", "train": "20240901-20250531", "test": "20250607-20251007", "embargo_days": 7},
    {"id": "BLIND", "train": None, "test": "20260101-20260731", "embargo_days": None},
]

STRATEGIES = [
    # TrendRider4h has a 1-param roi_space (roi_t1 only); freqtrade's
    # generate_roi_table needs 6 params. Use minimal_roi from .py unchanged
    # and tune buy + stoploss.
    {"name": "TrendRider4h", "epochs": 500, "spaces": "buy,stoploss"},
    # NFI has the full 6-param roi_space (roi_p1..p3 + roi_t1..t3) and an
    # explicit rsi_exit sell param. Tune buy + sell; ROI + stoploss are stable.
    {"name": "NostalgiaForInfinity", "epochs": 300, "spaces": "buy,sell"},
]


def _move_json_away(strategy_name: str, target_path: Path) -> None:
    """Move any existing strategy JSON override to a safe location."""
    src = STRATEGY_DIR / f"{strategy_name}.json"
    if src.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target_path))
        print(f"  moved {src.name} -> {target_path}")


def _run_hyperopt(
    strategy_name: str, timerange: str, epochs: int, spaces: str, log_path: Path
) -> bool:
    """Run hyperopt via the wrapper. Returns True on success."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from run_hyperopt import run_hyperopt

    print(f"  [hyperopt] {strategy_name} {timerange} epochs={epochs} spaces={spaces}")
    started = time.time()
    try:
        best = run_hyperopt(
            timerange=timerange,
            epochs=epochs,
            spaces=spaces,
            loss="SharpeHyperOptLossDaily",
            strategy=strategy_name,
        )
        elapsed = time.time() - started
        print(
            f"  [hyperopt] elapsed {elapsed:.1f}s, best_loss={best.get('loss') if best else 'N/A'}"
        )
        log_path.write_text(
            json.dumps(
                {
                    "strategy": strategy_name,
                    "timerange": timerange,
                    "best": best,
                    "elapsed": elapsed,
                },
                indent=2,
                default=str,
            )
        )
        return best is not None and best != {}
    except Exception as e:
        elapsed = time.time() - started
        print(f"  [hyperopt] FAILED {elapsed:.1f}s: {type(e).__name__}: {e}")
        log_path.write_text(f"FAILED: {type(e).__name__}: {e}")
        return False


def _run_backtest(strategy_name: str, timerange: str) -> dict:
    """Run backtest via the wrapper. Returns parsed metrics dict."""
    from run_backtest import run_backtest

    print(f"  [backtest] {strategy_name} {timerange}")
    started = time.time()
    try:
        run_backtest(timerange=timerange, strategy=strategy_name)
        elapsed = time.time() - started
    except SystemExit as e:
        elapsed = time.time() - started
        print(f"  [backtest] SystemExit {elapsed:.1f}s, code={e.code}")
        return {"status": "failed", "elapsed_s": round(elapsed, 1)}

    # Find the latest backtest result zip
    zips = sorted(RESULTS_DIR.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        print("  [backtest] no result zip found")
        return {"status": "failed", "reason": "no_result_zip"}

    latest = zips[-1]
    metrics = _parse_backtest_zip(latest, strategy_name)
    metrics["elapsed_s"] = round(elapsed, 1)
    metrics["result_zip"] = str(latest)
    return metrics


def _parse_backtest_zip(zip_path: Path, strategy_name: str) -> dict:
    """Parse freqtrade backtest result zip and extract metrics."""
    try:
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
                return {"status": "failed", "reason": "no_result_json_in_zip"}
            with z.open(json_name) as f:
                data = json.load(f)
    except Exception as e:
        return {"status": "failed", "reason": f"zip_parse: {e}"}

    sname = strategy_name
    comp = [c for c in data.get("strategy_comparison", []) if c.get("key") == sname]
    strat_data = data.get("strategy", {}).get(sname, {})
    s = comp[0] if comp else strat_data

    trades = s.get("trades", 0) or 0
    wins = 0
    losses = 0
    avg_winner = 0.0
    avg_loser = 0.0
    for t in strat_data.get("trades", []) or []:
        p = t.get("profit_abs", 0) or 0
        if p > 0:
            wins += 1
            avg_winner += p
        elif p < 0:
            losses += 1
            avg_loser += abs(p)
    if wins:
        avg_winner /= wins
    if losses:
        avg_loser /= losses
    rr = (avg_winner / avg_loser) if avg_loser > 0 else float("inf") if wins > 0 else 0.0

    return {
        "status": "success",
        "trades": trades,
        "win_rate": s.get("winrate"),
        "profit_total_abs": s.get("profit_total_abs") or s.get("profit_total"),
        "profit_total_pct": s.get("profit_total"),
        "max_drawdown_abs": s.get("max_drawdown_abs") or s.get("max_drawdown"),
        "max_drawdown_pct": s.get("max_drawdown"),
        "profit_factor": s.get("profit_factor"),
        "sharpe": s.get("sharpe"),
        "calmar": s.get("calmar"),
        "avg_profit": s.get("profit_mean"),
        "duration_avg": s.get("duration_avg"),
        "avg_winner_usdt": round(avg_winner, 4),
        "avg_loser_usdt": round(avg_loser, 4),
        "rr_ratio": round(rr, 4) if rr != float("inf") else "inf",
        "wins": wins,
        "losses": losses,
    }


def _append_to_csv(row: dict, frozen: bool = False) -> None:
    """Append a single WF row to wf_results.csv (or frozen variant)."""
    WF_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "window_id",
        "strategy",
        "train_range",
        "test_range",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_total_abs",
        "profit_total_pct",
        "max_drawdown_abs",
        "max_drawdown_pct",
        "profit_factor",
        "avg_winner_usdt",
        "avg_loser_usdt",
        "rr_ratio",
        "sharpe",
        "calmar",
        "avg_profit",
        "duration_avg",
        "result_zip",
        "elapsed_s",
        "status",
        "run_at",
    ]
    target = WF_FROZEN_CSV if frozen else WF_RESULTS_CSV
    file_exists = target.exists()
    with target.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def run_window_strategy(
    window: dict, strategy: dict, do_hyperopt: bool = True, frozen: bool = False
) -> dict:
    """Execute one (window, strategy) cycle: hyperopt + backtest + record."""
    wid = window["id"]
    sname = strategy["name"]
    test_range = window["test"]
    print(f"\n=== {wid} x {sname} (test={test_range}) ===")

    metrics = {
        "window_id": wid,
        "strategy": sname,
        "train_range": window["train"] or "n/a",
        "test_range": test_range,
        "status": "failed",
        "run_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }

    # 1. Move any stale JSON away (from previous window or default state)
    _move_json_away(sname, WF_LOGS_DIR / f"{wid}_{sname}_prev.json")

    # 2. Run hyperopt on train period (only if window has a train range)
    if do_hyperopt and window["train"]:
        hp_log = WF_LOGS_DIR / f"{wid}_{sname}_hyperopt.json"
        hp_ok = _run_hyperopt(
            sname,
            window["train"],
            strategy["epochs"],
            strategy["spaces"],
            hp_log,
        )
        if not hp_ok:
            print(f"  [skip backtest] hyperopt failed for {wid} x {sname}")
            metrics["status"] = "hyperopt_failed"
            _append_to_csv(metrics, frozen=frozen)
            return metrics

    # 3. JSON should be at user_data/strategies/<sname>.json (auto-dumped by hyperopt)
    json_path = STRATEGY_DIR / f"{sname}.json"
    if json_path.exists():
        safe_copy = WF_LOGS_DIR / f"{wid}_{sname}_hyperopt_used.json"
        safe_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(json_path), str(safe_copy))
        print(f"  saved {json_path.name} -> {safe_copy}")
    else:
        print(f"  WARN: no {sname}.json found after hyperopt (using buy_params from .py)")

    # 4. Run backtest on test period
    bt_metrics = _run_backtest(sname, test_range)
    metrics.update(bt_metrics)

    # 5. Move JSON aside so next window starts clean
    if json_path.exists():
        used = WF_LOGS_DIR / f"{wid}_{sname}_used.json"
        _move_json_away(sname, used)

    _append_to_csv(metrics, frozen=frozen)
    print(f"  [{wid} x {sname}] done. status={metrics.get('status')}")
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", help="Run only this window ID (e.g., WF1)")
    parser.add_argument("--strategy", help="Run only this strategy name")
    parser.add_argument(
        "--no-hyperopt",
        action="store_true",
        help="Skip hyperopt (use existing buy_params from .py)",
    )
    args = parser.parse_args()

    windows_to_run = [w for w in WINDOWS if args.window is None or w["id"] == args.window]
    strategies_to_run = [
        s for s in STRATEGIES if args.strategy is None or s["name"] == args.strategy
    ]

    print("=== freqtrade-loop WF Phase 1 orchestrator ===")
    print(f"  windows: {[w['id'] for w in windows_to_run]}")
    print(f"  strategies: {[s['name'] for s in strategies_to_run]}")
    print(f"  hyperopt: {not args.no_hyperopt}")
    print(f"  results CSV: {WF_RESULTS_CSV}")
    print()

    started = time.time()
    results = []
    for window in windows_to_run:
        for strategy in strategies_to_run:
            metrics = run_window_strategy(
                window, strategy, do_hyperopt=not args.no_hyperopt, frozen=args.no_hyperopt
            )
            results.append(metrics)

    elapsed = time.time() - started
    print(f"\n=== done. elapsed {elapsed:.1f}s, {len(results)} runs ===")
    success = sum(1 for r in results if r.get("status") == "success")
    print(f"  success: {success}/{len(results)}")
    return 0 if success == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
