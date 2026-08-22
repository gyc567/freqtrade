"""freqtrade-loop: lookahead-analysis + recursive-analysis wrapper.

Patches ccxt's Binance load_markets to skip the live API call (Binance is
geo-blocked per [[freqtrade-loop/STATE.md]]), then runs freqtrade's
lookahead-analysis or recursive-analysis subcommands. Used in Cycle 14 Phase 0
to verify no future-function bias before running Walk-Forward.

Usage:
    python3 freqtrade-loop/run_lookahead.py lookahead --strategy=NAME [--timerange=YYYYMMDD-YYYYMMDD]
    python3 freqtrade-loop/run_lookahead.py recursive --strategy=NAME [--timerange=YYYYMMDD-YYYYMMDD]
"""

import sys
from pathlib import Path


ROOT = Path("/Users/jie/code/freqtrade")
sys.path.insert(0, str(ROOT / "freqtrade-loop"))


def patch_load_markets():
    """Same patch as run_backtest.py — skip live market fetch."""
    from run_backtest import patch_load_markets

    patch_load_markets()


def main():
    if len(sys.argv) < 2:
        print("usage: run_lookahead.py {lookahead|recursive} --strategy=NAME [--timerange=...]")
        sys.exit(1)

    cmd = sys.argv[1]
    # freqtrade subcommand names: lookahead-analysis, recursive-analysis
    if cmd == "lookahead":
        subcommand = "lookahead-analysis"
    elif cmd == "recursive":
        subcommand = "recursive-analysis"
    else:
        print(f"unknown command: {cmd} (use 'lookahead' or 'recursive')")
        sys.exit(1)

    # Default args
    strategy = "TrendRider4h"
    timerange = "20230101-20260731"
    config = str(ROOT / "user_data" / "config.binance_local.json")
    for arg in sys.argv[2:]:
        if arg.startswith("--strategy="):
            strategy = arg.split("=", 1)[1]
        elif arg.startswith("--timerange="):
            timerange = arg.split("=", 1)[1]
        elif arg.startswith("--config="):
            config = arg.split("=", 1)[1]

    print(f"=== freqtrade-loop {cmd} wrapper ===")
    print(f"  strategy: {strategy}")
    print(f"  timerange: {timerange}")
    print(f"  config: {config}")
    print()

    patch_load_markets()
    print("[1/2] ccxt load_markets patched")
    print()

    # freqtrade's CLI lives in freqtrade.main module — call its entrypoint
    from freqtrade.main import main as ft_main

    ft_args = [
        subcommand,  # lookahead-analysis or recursive-analysis
        "--strategy",
        strategy,
        "--config",
        config,
        "--timerange",
        timerange,
    ]
    print(f"[2/2] running freqtrade {subcommand} {' '.join(ft_args)}")
    print()
    # freqtrade's main() reads sys.argv[1:], so inject our args
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0]] + ft_args
    try:
        ft_main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
