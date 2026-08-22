"""
freqtrade-loop: offline hyperopt wrapper for TrendRider4h.

Same pattern as run_backtest.py — patches ccxt's Binance load_markets to skip the
live exchange API call, then drives freqtrade's Hyperopt class via the internal API.
Records best params into freqtrade-loop/hyperopt-history.json.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


# Ensure run_backtest.py is importable (sibling module)
sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path("/Users/jie/code/freqtrade")
LOOP_DIR = ROOT / "freqtrade-loop"
USER_DATA_DIR = ROOT / "user_data"
HYPEROPT_DIR = USER_DATA_DIR / "hyperopt_results"
CONFIG_FILE = ROOT / "user_data" / "config.binance_local.json"


def run_hyperopt(
    timerange: str,
    epochs: int,
    spaces: str,
    loss: str,
    random_state: int = 42,
    strategy: str = "TrendRider4h",
) -> dict:
    """Run hyperopt via the internal API. Returns best-epoch dict."""
    # Patch ccxt BEFORE freqtrade's exchange initialization.
    # (Binance is geo-blocked per STATE.md; without this, Backtesting() inside
    # Hyperopt's constructor fails to load markets.)
    from run_backtest import patch_load_markets

    patch_load_markets()

    from freqtrade.configuration import Configuration
    from freqtrade.optimize.hyperopt.hyperopt import Hyperopt

    config = Configuration.from_files([str(CONFIG_FILE)])
    config["strategy"] = strategy
    config["user_data_dir"] = USER_DATA_DIR
    config["strategy_path"] = str(USER_DATA_DIR / "strategies")
    config["timerange"] = timerange
    config["dry_run_wallet"] = 1000
    config["runmode"] = "hyperopt"
    config["epochs"] = epochs
    config["spaces"] = [s.strip() for s in spaces.split(",")]
    config["hyperopt_loss"] = loss
    config["hyperopt_min_trades"] = 1
    config["hyperopt_random_state"] = random_state
    config["hyperopt_position_adjustment"] = False
    config["print_all"] = False
    config["print_json"] = False
    config["dataformat_ohlcv"] = "feather"

    HYPEROPT_DIR.mkdir(parents=True, exist_ok=True)
    hp = Hyperopt(config)
    hp.start()

    # Load best epoch from results file. Format: .fthypt (JSONL, one epoch per line).
    # Earlier code looked for "*.fthypt.zip" which matches nothing — that's why every
    # WF cycle was reported as "hyperopt_failed" even though hyperopt itself succeeded.
    fthypts = sorted(HYPEROPT_DIR.glob("strategy_*.fthypt"), key=lambda p: p.stat().st_mtime)

    best = {}
    if fthypts:
        latest = fthypts[-1]
        epochs: list = []
        with latest.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    epochs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if epochs:
            best = min(epochs, key=lambda e: e.get("loss", 1e9))
    return best


def record_hyperopt(
    best: dict,
    timerange: str,
    epochs: int,
    spaces: str,
    loss: str,
    elapsed_s: float,
    run_status: str,
    strategy: str = "TrendRider4h",
) -> None:
    """Append result to hyperopt-history.json."""
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    history_file = LOOP_DIR / "hyperopt-history.json"
    history: dict = {"runs": []}
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
            "timerange": timerange,
            "epochs_requested": epochs,
            "spaces": spaces,
            "loss_function": loss,
            "elapsed_seconds": round(elapsed_s, 1),
            "status": run_status,
            "best_params": best.get("params", {}) if best else {},
            "best_loss": best.get("loss") if best else None,
            "best_total_profit": best.get("results_metrics", {}).get("profit_total_abs")
            if best
            else None,
        }
    )
    history_file.write_text(json.dumps(history, indent=2))
    print(f"  appended to {history_file.relative_to(ROOT)}")


def main():
    print("=== freqtrade-loop hyperopt wrapper ===")
    print(f"time: {datetime.now(UTC).isoformat()}")
    print()

    # Defaults: 100 epochs, buy space, Sharpe daily loss
    timerange = "20230101-20260101"
    epochs = 100
    spaces = "buy"
    loss = "SharpeHyperOptLossDaily"
    random_state = 42
    strategy = "TrendRider4h"
    for arg in sys.argv[1:]:
        if arg.startswith("--timerange="):
            timerange = arg.split("=", 1)[1]
        elif arg.startswith("--epochs="):
            epochs = int(arg.split("=", 1)[1])
        elif arg.startswith("--spaces="):
            spaces = arg.split("=", 1)[1]
        elif arg.startswith("--loss="):
            loss = arg.split("=", 1)[1]
        elif arg.startswith("--random-state="):
            random_state = int(arg.split("=", 1)[1])
        elif arg.startswith("--strategy="):
            strategy = arg.split("=", 1)[1]

    print(f"  strategy: {strategy}")
    print(f"  timerange: {timerange}")
    print(f"  epochs: {epochs}")
    print(f"  spaces: {spaces}")
    print(f"  loss: {loss}")
    print()

    from run_backtest import patch_load_markets, prepare_data

    print("[1/4] preparing data...")
    prepare_data()
    print()

    print("[2/4] patching ccxt load_markets (skip exchange API)...")
    patch_load_markets()
    print()

    print("[3/4] running hyperopt...")
    started = time.time()
    status = "failed_error"
    best = {}
    try:
        best = run_hyperopt(timerange, epochs, spaces, loss, random_state, strategy)
        status = "success"
    except SystemExit as e:
        status = "success" if (e.code is None or e.code == 0) else "failed_error"
    except Exception as e:
        print(f"  hyperopt raised: {type(e).__name__}: {e}")
    elapsed = time.time() - started
    print(f"  elapsed: {elapsed:.1f}s, status={status}")
    if best:
        print(f"  best loss={best.get('loss')}  params={best.get('params')}")
    print()

    print("[4/4] recording result...")
    record_hyperopt(best, timerange, epochs, spaces, loss, elapsed, status, strategy)
    print(f"=== done. status={status} ===")
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
